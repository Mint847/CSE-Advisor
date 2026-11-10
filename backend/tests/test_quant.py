"""Quant foundations (Q0): figures checked by hand and against gs-quant itself, the CSE accuracy rules (total return,
annualisation, thin trading), the CBSL T-bill reader on CBSL's real files, FRED, and the gs-quant coverage map."""
import importlib
import math
import pkgutil
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import gs_quant
import gs_quant.timeseries as ts
from gs_quant.timeseries.econometrics import CurveType

from cse_advisor.db import Announcement, Base, MacroDaily, Preference
from cse_advisor.quant import coverage, metrics as M, rates as R, series as S

FIX = Path(__file__).parent / "fixtures"
NO_RATE = pd.Series(dtype=float)


def days(n, start="2024-01-01"):
    return pd.bdate_range(start, periods=n)


def walk(n=400, seed=3, vol=0.015, drift=0.0004, start=100.0):
    rng = np.random.default_rng(seed)
    return pd.Series(start * np.cumprod(1 + rng.normal(drift, vol, n)), index=days(n))


@pytest.fixture()
def S_(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Sess = sessionmaker(bind=eng, expire_on_commit=False)
    monkeypatch.setattr(R, "SessionLocal", Sess)
    return Sess


# ---------------------------------------------------------------- figures worked out by hand
def test_hand_computed_figures(monkeypatch):
    # prices 100 -> 110 -> 99 -> 108.9: daily returns +10%, -10%, +10%
    x = pd.Series([100.0, 110.0, 99.0, 108.9], index=days(4))
    assert M.daily_returns(x).round(10).tolist() == [0.1, -0.1, 0.1]
    # sample sd of (0.1, -0.1, 0.1): mean 1/30, deviations (2/30, -4/30, 2/30), var = (4+16+4)/900/2 = 0.013333, sd = 0.115470
    monkeypatch.setattr(M, "MIN_VOL", 2)
    assert M.volatility(x, af=240) == pytest.approx(0.1154700538 * math.sqrt(240), rel=1e-6)
    # drawdown: peak 110, trough 99 -> -10%, not yet recovered (108.9 < 110)
    dd = M.max_drawdown(x)
    assert dd["max_drawdown"] == pytest.approx(-0.10) and (dd["peak"], dd["trough"], dd["recovered"]) == ("2024-01-02", "2024-01-03", None)
    assert dd["fall_sessions"] == 1 and dd["current_drawdown"] == pytest.approx(108.9 / 110 - 1)
    # total return and annualised return: 8.9% over 3 calendar days
    assert M.single(x, NO_RATE, 240)["total_return"] == pytest.approx(0.089)
    assert M.ann_return(x) == pytest.approx(1.089 ** (365.25 / 3) - 1)


def test_recovery_and_streaks():
    x = pd.Series([10, 12, 9, 10, 11, 12.5, 12, 11, 10.5, 10.4], index=days(10), dtype=float)
    dd = M.max_drawdown(x)
    assert (dd["peak"], dd["trough"], dd["recovered"]) == ("2024-01-02", "2024-01-03", "2024-01-08")
    assert dd["recovery_sessions"] == 3
    st = M.streaks(x)
    assert st["longest_up"] == 3 and st["longest_down"] == 4 and st["current"] == -4


# ---------------------------------------------------------------- parity with gs-quant itself
def test_sharpe_equals_gs_quant_with_252_days():
    x = walk(600)
    rates = pd.Series({pd.Timestamp("2023-01-01"): 9.0, pd.Timestamp("2024-06-01"): 7.5})
    er = M.excess_index(x, rates)
    gs = float(ts.sharpe_ratio(er, curve_type=CurveType.EXCESS_RETURNS).iloc[-1])
    assert M.sharpe(x, rates, af=252) == pytest.approx(gs, rel=1e-6)
    # with the CSE's ~239 sessions the ratio is larger by sqrt(252/239): the only difference is the annualisation
    assert M.sharpe(x, rates, af=239) == pytest.approx(gs * math.sqrt(252 / 239), rel=1e-6)


def test_volatility_and_ewma_match_gs_quant():
    x = walk(300)
    assert M.volatility(x, af=252) == pytest.approx(float(ts.volatility(x, ts.Window(300, 0)).iloc[-1]) / 100, rel=1e-8)
    assert M.ewma_volatility(x, af=252, beta=0.75) == pytest.approx(float(ts.exponential_volatility(x, 0.75).iloc[-1]) / 100, rel=1e-8)


def test_beta_and_correlation_on_a_known_relationship():
    b = walk(500, seed=8)
    rx = 1.5 * ts.returns(b)                                  # x moves exactly 1.5 times as much as b every day
    x = (1 + rx.fillna(0)).cumprod() * 50
    rel = M.relative(x, b, NO_RATE, 239)
    assert rel["beta"] == pytest.approx(1.5, rel=1e-9) and rel["correlation"] == pytest.approx(1.0, abs=1e-9)
    assert rel["r_squared"] == pytest.approx(1.0, abs=1e-9)


def test_short_history_says_why_instead_of_a_number():
    x = walk(40)
    out = M.single(x, NO_RATE, 239)
    assert out["volatility"] is None and "60" in out["missing"]["volatility"]
    assert out["sharpe"] is None and out["cagr"] is None
    assert "beta" in M.relative(x, walk(40, seed=9), NO_RATE, 239)["missing"]


def test_downside_deviation_ignores_gains():
    x = pd.Series(100.0 * np.cumprod([1] + [1.01] * 80), index=days(81))     # only up days
    assert M.downside_deviation(x, NO_RATE, 239) == 0.0


# ---------------------------------------------------------------- CSE rules: total return, annualisation, thin trading
def test_total_return_adds_the_dividend_back_on_the_xd_date():
    p = pd.Series([10.0, 10.0, 9.5, 9.5], index=days(4))      # the price drops by the 0.50 dividend on the XD day
    tr = S.total_return_index(p, [(p.index[2], 0.5)])
    assert tr.tolist() == pytest.approx([10.0, 10.0, 10.0, 10.0])
    q = pd.Series([10.0, 10.0, 9.5, 9.5], index=pd.DatetimeIndex(["2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"]))
    sat = S.total_return_index(q, [(pd.Timestamp("2024-01-06"), 0.5)])          # XD on a Saturday counts on Monday
    assert sat.tolist() == pytest.approx([10.0, 10.0, 10.0, 10.0])
    assert S.total_return_index(p, [(p.index[0], 0.5)]).tolist() == pytest.approx(p.tolist())   # nothing to pay against


def test_recorded_dividends_are_counted_once_and_scaled_like_the_price(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Sess = sessionmaker(bind=eng, expire_on_commit=False)
    idx = days(6)
    with Sess.begin() as s:
        for i, ev in enumerate([{"kind": "cash_dividend", "dps": 2.0, "xd": str(idx[3].date())},
                                {"kind": "cash_dividend", "dps": 2.0, "xd": str(idx[3].date()), "payment": "2024-02-01"},   # same dividend, re-announced
                                {"kind": "scrip_dividend", "proportion": "1 for 20", "xd": str(idx[2].date())},           # not cash
                                {"kind": "cash_dividend", "dps": None, "xd": None}]):
            s.add(Announcement(id=i + 1, symbol="ABC.N0000", title="Dividend", event=ev, date=datetime(2024, 1, 1)))
    # a 1:1 bonus later on halved every earlier adjusted price: adjusted = raw x 0.5
    frame = pd.DataFrame({"close": [5.0, 5.0, 5.0, 4.0, 4.0, 4.0], "raw_close": [10.0, 10.0, 10.0, 8.0, 8.0, 8.0],
                          "volume": [1.0] * 6}, index=idx)
    monkeypatch.setattr("cse_advisor.engine.load_adjusted", lambda s, sym: (frame, []))
    with Sess() as s:
        b = S.cse_stock(s, "ABC.N0000")
    assert len(b.dividends) == 1 and b.dividends[0]["dps"] == 2.0 and b.dividends[0]["dps_adj"] == 1.0
    assert b.dividends_from == str(idx[3].date())
    assert b.total.iloc[-1] == pytest.approx(5.0)              # the 1.00 adjusted dividend exactly offsets the 5 -> 4 drop


def test_sessions_per_year_uses_complete_years():
    idx = pd.DatetimeIndex([d for y in (2021, 2022, 2023) for d in pd.bdate_range(f"{y}-01-01", f"{y}-12-31")[:239]]
                           + list(pd.bdate_range("2024-01-01", periods=50)))
    assert S.sessions_per_year(idx) == 239.0
    assert S.sessions_per_year(pd.DatetimeIndex(pd.bdate_range("2024-03-01", periods=30))) == S.SESSIONS_FALLBACK


def test_thin_trading_flag():
    v = pd.Series([0, 0, 0, 100, 200] * 20, dtype=float)
    assert S.thin_trading(v, 100) == {"share_no_trade": 0.6, "thin": True, "limit": 0.2}
    assert not S.thin_trading(pd.Series([10.0] * 50), 50)["thin"]


def test_window_slice_and_alignment():
    x = walk(800)
    assert S.window_slice(x, "1y").index[0] > x.index[-1] - pd.DateOffset(years=1)
    a = pd.Series([1.0, 2.0, 3.0], index=days(3))
    b = pd.Series([4.0, 5.0], index=days(2)[::1])
    assert len(S.aligned(a, b)) == 2


# ---------------------------------------------------------------- the CBSL T-bill rate (real CBSL files as fixtures)
def test_cbsl_weekly_and_monthly_tables_parse():
    w = R.parse_weekly((FIX / "cbsl_tbill_weekly.xlsx").read_bytes())
    assert w.index[0] == pd.Timestamp("1996-01-04") and w.iloc[0] == pytest.approx(19.27)
    assert w.index[-1] == pd.Timestamp("2025-11-28") and w.iloc[-1] == pytest.approx(7.52)
    m = R.parse_monthly((FIX / "cbsl_rates_monthly.xlsx").read_bytes())
    assert m.index[0] == pd.Timestamp("2003-01-31") and m.iloc[0] == pytest.approx(8.74)
    assert m.index[-1] == pd.Timestamp("2026-08-31") and m.iloc[-1] == pytest.approx(9.06)
    # the two CBSL tables agree where they overlap (month-end yield = the month's last auction), most months exactly
    ov = m[(m.index >= "2024-02-01") & (m.index <= w.index[-1])]
    diff = (ov - w.reindex(ov.index, method="ffill")).abs()
    assert (diff < 0.05).mean() > 0.8 and diff.max() < 0.35
    c = R.combine(w, m)
    assert c.index.is_monotonic_increasing and c.index[-1] == pd.Timestamp("2026-08-31")
    assert (c.index > w.index[-1]).sum() == (m.index > w.index[-1]).sum()


def test_cbsl_links_found_by_title_not_file_name():
    html = ('<a href="/sites/x/Treasury_Bill_Auctions_20991231_e.xlsx">Treasury Bill Auctions - Weekly (1996 to Latest)</a>'
            '<a href="https://www.cbsl.gov.lk/s/table4.04_20991012.xlsx"> Interest Rates  -  Monthly (2003 to Latest)</a>'
            '<a href="/s/table4.03.xlsx">Interest Rates - Annual</a>')
    assert R.find_link(html, "weekly") == "https://www.cbsl.gov.lk/sites/x/Treasury_Bill_Auctions_20991231_e.xlsx"
    assert R.find_link(html, "monthly") == "https://www.cbsl.gov.lk/s/table4.04_20991012.xlsx"
    assert R.find_link("<p>moved</p>", "weekly") is None


def fake_cbsl(url, binary=False):
    if url.endswith(R.PAGES["weekly"]):
        return '<a href="/w.xlsx">Treasury Bill Auctions - Weekly (1996 to Latest)</a>'
    if url.endswith(R.PAGES["monthly"]):
        return '<a href="/m.xlsx">Interest Rates - Monthly (2003 to Latest)</a>'
    return (FIX / ("cbsl_tbill_weekly.xlsx" if url.endswith("/w.xlsx") else "cbsl_rates_monthly.xlsx")).read_bytes()


def test_top_up_stores_once_a_week_and_survives_a_failure(S_, monkeypatch):
    monkeypatch.setattr(R, "fred_key", lambda: None)
    out = R.top_up(get=fake_cbsl)
    assert out["tbill_new"] > 1500 and out["tbill_latest"] == ["2026-08-31", 9.06]
    assert "tbill_skipped" in R.top_up(get=lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not fetch")))
    with S_.begin() as s:
        s.get(Preference, "rates_fetched_at").value = (datetime.utcnow() - timedelta(days=8)).isoformat()
    broken = R.top_up(get=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("CBSL down")))
    assert "CBSL down" in broken["tbill_error"] and broken["tbill_latest"] == ["2026-08-31", 9.06]   # the old value stays
    with S_() as s:
        info = R.rate_info(s)
        assert (info["rate_pct"], info["as_of"]) == (9.06, "2026-08-31") and "CBSL" in info["source"]


def test_rate_fallbacks(S_, monkeypatch):
    with S_() as s:
        assert R.rate_info(s)["rate_pct"] == 0.0 and R.rate_info(s)["stale"]          # nothing yet: 0%, said plainly
        monkeypatch.setattr(R, "cfg", lambda: {"risk_free_manual_pct": 8.5})
        assert R.rate_info(s)["rate_pct"] == 8.5 and "manual" in R.rate_info(s)["source"]
        assert R.tbill_series(s).iloc[0] == 8.5


def test_cash_index_accrues_actual_365_at_the_rate_known_the_day_before():
    idx = pd.DatetimeIndex(["2024-01-01", "2024-01-02", "2024-01-05", "2024-01-08"])
    rates = pd.Series({pd.Timestamp("2023-12-01"): 10.0, pd.Timestamp("2024-01-05"): 20.0})
    c = R.cash_index(idx, rates)
    assert c.iloc[1] == pytest.approx(1 + 0.10 / 365)
    assert c.iloc[2] == pytest.approx(c.iloc[1] * (1 + 0.10 * 3 / 365))            # 3 days at 10% (the 20% starts that day)
    assert c.iloc[3] == pytest.approx(c.iloc[2] * (1 + 0.20 * 3 / 365))


# ---------------------------------------------------------------- FRED (gs-quant's FredDataApi)
class FakeFred:
    def build_query(self, start=None, **k):
        return start

    def query_data(self, q, sid):
        return pd.Series([4.1, 4.2], index=pd.to_datetime(["2026-11-05", "2026-11-06"]), name=sid)


def test_fred_series_are_stored_under_short_codes(S_):
    out = R.fred_top_up(api=FakeFred())
    assert set(out) == set(R.FRED_SERIES) and all(v == 2 for v in out.values())
    with S_() as s:
        codes = {c for (c,) in s.query(MacroDaily.code).distinct()}
    assert codes == {c for c, _ in R.FRED_SERIES.values()} and all(len(c) <= 12 for c in codes)


# ---------------------------------------------------------------- nothing in gs-quant is left out
def all_modules():
    names = [m.name for m in pkgutil.iter_modules(gs_quant.__path__)]
    for pkg in ("timeseries", "backtests", "markets", "models", "datetime", "api"):
        mod = importlib.import_module(f"gs_quant.{pkg}")
        names += [f"{pkg}.{m.name}" for m in pkgutil.iter_modules(mod.__path__)]
    return names


def test_every_gs_quant_module_has_a_coverage_entry():
    assert gs_quant.__version__.startswith(coverage.GS_QUANT_VERSION)
    keys = {e["module"] for e in coverage.table()}
    sub = {"timeseries", "backtests", "markets", "models", "datetime", "api"}
    missing = [m for m in all_modules() if m not in keys and m not in sub]
    assert missing == [], f"gs-quant modules with no coverage entry: {missing}"
    for e in coverage.table():
        assert e["status"] in {"used", "recreated", "not_possible", "not_applicable", "plumbing"}
        if e["status"] in ("not_possible", "not_applicable"):
            assert e["why"], f"{e['module']} needs a reason"
        if e["status"] in ("used", "recreated"):
            assert e["where"] and e["stage"], f"{e['module']} needs a place in the app"


def test_beta_ratios_withheld_when_the_link_is_weak():
    a, b = walk(400, seed=1), walk(400, seed=2)                 # two unrelated random walks
    rel = M.relative(a, b, NO_RATE, 239)
    assert abs(rel["correlation"]) < M.MIN_LINK and rel["weak_link"]
    assert rel["treynor"] is None and "not meaningful" in rel["missing"]["treynor"]
    assert rel["beta"] is not None                               # beta itself is still shown (near zero, which is the point)


def test_every_used_gs_quant_entry_names_calls_that_exist_in_the_source():
    """The coverage map must not claim more than the app does: each 'used' module lists the exact calls it makes, and each must appear in the code."""
    import pathlib
    from cse_advisor.quant import coverage
    root = pathlib.Path(coverage.__file__).resolve().parents[1]
    code = "\n".join(p.read_text(encoding="utf-8") for p in root.rglob("*.py") if p.name != "coverage.py")
    for e in coverage.table():
        if e["status"] == "used":
            assert e.get("calls"), f"{e['module']} is marked used but names no call"
            for c in e["calls"]:
                assert c in code, f"{e['module']}: '{c}' is not called anywhere in the app"
        else:
            assert not e.get("calls"), f"{e['module']} is not 'used' but lists calls"
