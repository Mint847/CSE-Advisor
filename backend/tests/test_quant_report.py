"""The QNT tab report: charts that add up, the regression against gs-quant and statsmodels, the plain-English rules,
the route, and a whole report on a small database."""
from datetime import datetime

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from gs_quant.timeseries.statistics import LinearRegression

from cse_advisor import quant_api
from cse_advisor.auth import User
from cse_advisor.db import Base, MacroDaily, MarketDaily, PriceDaily, Security
from cse_advisor.quant import metrics as M, report

ME = User(uid="local", admin=True)


def bdays(n, start="2022-01-03"):
    return pd.bdate_range(start, periods=n)


def walk(n, seed, vol=0.012, drift=0.0003, start=100.0):
    rng = np.random.default_rng(seed)
    return pd.Series(start * np.cumprod(1 + rng.normal(drift, vol, n)), index=bdays(n))


# ---------------------------------------------------------------- chart pieces
def test_histogram_counts_every_day_and_marks_var():
    r = pd.Series(np.random.default_rng(1).normal(0, 0.01, 500))
    h = report.histogram(r)
    assert sum(b["count"] for b in h["bins"]) == 500 and len(h["bins"]) == report.HIST_BINS
    assert h["var95"] == pytest.approx(np.percentile(r, 5), abs=1e-6)
    assert report.histogram(r.head(10)) == {"bins": []}


def test_monthly_table_years_and_seasonal_average():
    idx = pd.DatetimeIndex(["2023-12-29", "2024-01-31", "2024-02-29", "2024-03-29", "2024-12-31", "2025-01-31"])
    x = pd.Series([100.0, 110.0, 99.0, 99.0, 120.0, 132.0], index=idx)
    t = report.monthly_table(x)
    y24 = next(y for y in t["years"] if y["year"] == 2024)
    assert y24["months"]["1"] == pytest.approx(0.10) and y24["months"]["2"] == pytest.approx(-0.10)
    assert y24["year_return"] == pytest.approx(0.20)                      # 100 at the end of 2023 -> 120 at the end of 2024
    jan = t["seasonal"][0]
    assert jan["years"] == 2 and jan["average"] == pytest.approx(0.10) and jan["positive"] == 1.0


def test_decomposition_needs_two_years():
    assert report.decomposition(walk(300, 2))["available"] is False
    d = report.decomposition(walk(700, 2))
    assert d["available"] and len(d["trend"]) > 50 and len(d["adjusted"]) > 50


# ---------------------------------------------------------------- regression: gs-quant coefficients, Newey-West t
@pytest.fixture()
def S(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Sess = sessionmaker(bind=eng, expire_on_commit=False)
    monkeypatch.setattr(quant_api, "SessionLocal", Sess)
    report._cache.clear()
    return Sess


def seed_market(S, n=600):
    aspi = walk(n, 11)
    usd = walk(n, 12, vol=0.003, start=300)
    spx = walk(n, 13)
    with S.begin() as s:
        for t in aspi.index:
            s.add(MarketDaily(date=t.date(), aspi=float(aspi[t])))
            s.add(MacroDaily(code="usdlkr", date=t.date(), close=float(usd[t])))
            s.add(MacroDaily(code="spx", date=t.date(), close=float(spx[t])))
    return aspi, usd, spx


def test_regression_coefficients_match_gs_quant(S):
    aspi, usd, spx = seed_market(S)
    ra = aspi.pct_change()
    rng = np.random.default_rng(5)
    rx = 1.3 * ra + rng.normal(0, 0.005, len(ra))                         # the stock is 1.3 x the market plus noise
    x = (1 + rx.fillna(0)).cumprod() * 50
    with S() as s:
        out = report.regression(s, x, thin=False)
    assert out["available"] and out["frequency"] == "daily"
    rows = {r["factor"]: r for r in out["rows"]}
    assert rows["ASPI"]["coefficient"] == pytest.approx(1.3, abs=0.05) and rows["ASPI"]["t"] > 20
    assert abs(rows["USD/LKR"]["t"]) < 3                                  # unrelated by construction
    # the same numbers straight from gs-quant's LinearRegression
    d = pd.DataFrame({"x": x, "a": aspi, "u": usd, "s": spx}).pct_change()
    d["s"] = d["s"].shift(1)
    d = d.dropna()
    lr = LinearRegression([d["a"], d["u"], d["s"]], d["x"])
    assert rows["ASPI"]["coefficient"] == pytest.approx(round(lr.coefficient(1), 4))
    assert out["r_squared"] == pytest.approx(round(lr.r_squared(), 4))


def test_thin_stock_regression_uses_weekly_returns(S):
    aspi, _, _ = seed_market(S, 400)
    with S() as s:
        out = report.regression(s, aspi * 0.5, thin=True)
    assert out["available"] and out["frequency"] == "weekly"


# ---------------------------------------------------------------- plain English
def test_summary_lines_follow_the_numbers():
    rate = {"rate_pct": 9.0}
    f = {"volatility": 0.5, "max_drawdown": -0.4, "peak": "2024-01-02", "trough": "2024-06-03", "recovered": None, "sharpe": -0.2}
    rel = {"beta": 1.6, "up_capture": 1.1, "down_capture": 1.4}
    lines = report.summary(f, rel, "ASPI", False, rate)
    assert "very volatile" in lines[0] and "1.6%" in lines[1] and "40%" in lines[2] and "not yet recovered" in lines[2]
    assert "did not beat the T-bill rate (9.00%)" in lines[3] and "fell 140% as much" in lines[4]
    assert lines[-1].startswith("These describe the past")
    thin = report.summary({"volatility": 0.1}, {"beta": 0.5, "beta_weekly": 0.9}, "ASPI", True, rate)
    assert "fairly steady" in thin[0] and "beta 0.90" in thin[1] and any("Thinly traded" in x for x in thin)


# ---------------------------------------------------------------- the whole report and the route
def test_full_report_on_a_small_database(S, monkeypatch):
    aspi, _, _ = seed_market(S, 600)
    px = aspi * 0.2
    with S.begin() as s:
        s.add(Security(symbol="ABC.N0000", cse_id=1, name="ABC PLC"))
        for t in px.index:
            s.add(PriceDaily(symbol="ABC.N0000", date=t.date(), open=float(px[t]), high=float(px[t]), low=float(px[t]), close=float(px[t]),
                             volume=1000.0))
        s.add(MacroDaily(code="lk_tbill_3m", date=datetime(2021, 12, 31).date(), close=9.0))
    r = quant_api.stock_quant("ABC.N0000", "1y", "total", None, ME)
    m, f, rel, c = r["meta"], r["figures"], r["relative"], r["charts"]
    assert m["bench"]["id"] == "^ASPI" and m["rate"]["rate_pct"] == 9.0 and m["dividends"]["applies"]
    assert rel["beta"] == pytest.approx(1.0, abs=1e-6) and rel["correlation"] == pytest.approx(1.0, abs=1e-6)
    assert f["volatility"] is not None and f["sharpe"] is not None and len(c["drawdown"]) == f["sessions"]
    assert c["histogram"]["bins"] and c["monthly"]["years"] and c["rolling_vol"] and c["rolling_beta"]
    assert any("ASPI" in line for line in r["summary"])
    assert len(report._cache) == 1                                         # kept for the next view
    with pytest.raises(HTTPException) as e:
        quant_api.stock_quant("NOPE.N0000", "1y", "total", None, ME)
    assert e.value.status_code == 404
