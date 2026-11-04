"""Multi-year daily history: TradingView's older (already adjusted) bars merged under cse.lk's year."""
from datetime import date, datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from cse_advisor import intraday as it
from cse_advisor import tv_client
from cse_advisor.analysis.corporate_actions import adjust_prices
from cse_advisor.db import Base, PriceDaily, Security
from cse_advisor.engine import stitch_deep
from cse_advisor.ingest import backfill_deep_history


def _daily(rows):
    df = pd.DataFrame(rows, columns=["date", "close", "source", "tv_close"]).set_index("date")
    df.index = pd.DatetimeIndex(df.index)
    for c in ("open", "high", "low"):
        df[c] = df["close"]
    df["volume"], df["turnover"], df["trades"], df["open_estimated"] = 1000.0, None, None, False
    return df


def test_stitch_scales_older_bars_to_the_app_basis():
    # KZOO-like: cse.lk days are raw 20 before a 2-for-1 rights XR at Rs 7, which the app adjusts by TERP;
    # TradingView's own factor (0.556) differs a little, so its older bars sit on a slightly different basis
    raw = _daily([("2025-10-30", 11.12, "tv_adj", None), ("2025-10-31", 11.12, "tv_adj", None),
                  ("2025-11-03", 20.0, "history", 11.12), ("2025-11-04", 20.0, "history", 11.12),
                  ("2026-03-04", 11.0, "eod", 11.0)])
    actions = [{"id": 1, "kind": "rights", "ex_date": date(2026, 3, 4), "ratio_new": 2.0, "ratio_old": 1.0, "price": 7.0}]
    adj, _ = adjust_prices(raw, actions)
    out = stitch_deep(adj)
    factor = (20.0 + 2 * 7.0) / 3 / 20.0          # TERP / cum-rights price = 0.5667 at a 20.00 cum price
    k = 20.0 * factor / 11.12
    assert out.attrs["stitch_k"] == pytest.approx(k, rel=1e-3)
    # the older bars land on the same level as the adjusted cse.lk bars right after the seam: no jump
    assert out.loc["2025-10-31", "close"] == pytest.approx(out.loc["2025-11-03", "close"], rel=2e-3)
    assert out["deep"].tolist() == [True, True, False, False, False]
    assert out.loc["2025-10-31", "raw_close"] == out.loc["2025-10-31", "close"]       # no as-traded price exists
    assert out.loc["2025-11-03", "raw_close"] == 20.0


def test_stitch_without_older_bars_is_a_no_op():
    raw = _daily([("2025-11-03", 20.0, "history", None), ("2025-11-04", 21.0, "eod", None)])
    adj, _ = adjust_prices(raw, [])
    out = stitch_deep(adj)
    assert out["close"].tolist() == [20.0, 21.0] and not out["deep"].any()


def test_piecewise_constant_factor():
    r = pd.Series([0.556, 0.558, 0.553, 1.002, 0.998, 1.004], index=pd.date_range("2025-01-01", periods=6))
    out = it.piecewise_constant(r)
    assert out.iloc[:3].nunique() == 1 and out.iloc[3:].nunique() == 1
    assert out.iloc[0] == pytest.approx(0.556) and out.iloc[-1] == pytest.approx(1.002)


def test_intraday_days_in_the_older_era_follow_the_adjusted_daily_close():
    # 2 raw intraday days before cse.lk's history; the daily bars there are TradingView-adjusted (x0.5588)
    idx = pd.DatetimeIndex(["2025-10-30 09:30", "2025-10-30 13:30", "2025-10-31 09:30", "2025-10-31 13:30"])
    bars = pd.DataFrame({"open": 20.0, "high": 20.2, "low": 19.8, "close": [20.0, 20.1, 19.9, 20.0], "volume": 100.0}, index=idx)
    src = pd.Series("tv", index=idx)
    daily = pd.DataFrame({"close": [11.23, 11.18], "raw_close": [11.23, 11.18], "volume": [1.0, 1.0], "raw_volume": [1.0, 1.0],
                          "deep": [True, True]}, index=pd.DatetimeIndex(["2025-10-30", "2025-10-31"]))
    out = it.adjust_intraday(bars, src, daily, adjusted=True)
    f = out["close"] / bars["close"]
    assert f.nunique() == 1 and f.iloc[0] == pytest.approx(0.5588, rel=3e-3)   # one factor, no day-to-day jitter
    assert out["volume"].iloc[0] == pytest.approx(100 / f.iloc[0])


@pytest.fixture()
def mem_session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(Security(symbol="TEST.N0000", cse_id=1, name="Test PLC"))
        # cse.lk year: an estimated-open history bar with crossings in its volume, and a real eod bar
        s.add(PriceDaily(symbol="TEST.N0000", date=date(2025, 11, 3), open=9.5, high=10.4, low=9.4, close=10.0,
                         volume=5000.0, open_estimated=True, source="history"))
        s.add(PriceDaily(symbol="TEST.N0000", date=date(2025, 11, 4), open=10.1, high=10.6, low=10.0, close=10.5,
                         volume=900.0, open_estimated=False, source="eod"))
        s.commit()
        yield s


def test_backfill_merges_under_the_cse_year(mem_session, monkeypatch):
    tv = [{"ts": datetime(2025, 10, 30), "open": 9.0, "high": 9.6, "low": 8.9, "close": 9.5, "volume": 700.0},
          {"ts": datetime(2025, 10, 31), "open": 9.5, "high": 9.9, "low": 9.4, "close": 9.8, "volume": 800.0},
          {"ts": datetime(2025, 11, 3), "open": 9.9, "high": 10.4, "low": 9.4, "close": 10.0, "volume": 3000.0},
          {"ts": datetime(2025, 11, 4), "open": 10.1, "high": 10.6, "low": 10.0, "close": 10.5, "volume": 900.0}]
    monkeypatch.setattr(tv_client, "fetch_bars", lambda *a, **k: tv)
    s = mem_session
    got = backfill_deep_history(s, s.get(Security, "TEST.N0000"))
    s.flush()
    assert got["added"] == 2 and got["opens_fixed"] == 1 and got["volumes_fixed"] == 1
    rows = {r.date: r for r in s.scalars(select(PriceDaily).where(PriceDaily.symbol == "TEST.N0000"))}
    assert rows[date(2025, 10, 30)].source == "tv_adj" and not rows[date(2025, 10, 30)].open_estimated
    h = rows[date(2025, 11, 3)]
    assert h.open == 9.9 and not h.open_estimated                       # TradingView's real open
    assert h.volume == 3000.0 and h.crossing_volume == 2000.0            # crossing-free volume, crossings kept
    assert h.tv_close == 10.0
    e = rows[date(2025, 11, 4)]
    assert e.open == 10.1 and e.volume == 900.0 and e.source == "eod"    # real end-of-day bars are never changed


def test_backfill_puts_adjusted_opens_back_on_the_raw_basis(mem_session, monkeypatch):
    # TradingView adjusted this day (close 5.0 vs cse.lk's raw 10.0): its open is scaled back before use
    tv = [{"ts": datetime(2025, 11, 3), "open": 4.9, "high": 5.2, "low": 4.7, "close": 5.0, "volume": 6000.0}]
    monkeypatch.setattr(tv_client, "fetch_bars", lambda *a, **k: tv)
    s = mem_session
    backfill_deep_history(s, s.get(Security, "TEST.N0000"))
    h = s.scalars(select(PriceDaily).where(PriceDaily.date == date(2025, 11, 3))).one()
    assert h.open == pytest.approx(9.8) and h.volume == pytest.approx(3000.0)
