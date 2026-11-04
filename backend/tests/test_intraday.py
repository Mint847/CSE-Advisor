"""Intraday bars: tick -> 1m -> timeframe buckets, source merge, adjustment, time labels, engines on
intraday frames, market status fallback and the market-wide notice matcher."""
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd

from cse_advisor import intraday as it
from cse_advisor.analysis.model import tfuture, tkey
from cse_advisor.cse_client import TZ
from cse_advisor.ingest import match_company
from cse_advisor.live import clock_open, market_open, parse_status, _status


def _ms(y, m, d, hh, mm, ss=0):
    return int(datetime(y, m, d, hh, mm, ss, tzinfo=TZ).timestamp() * 1000)


def test_buckets_anchor_on_session_open():
    ts = pd.DatetimeIndex(["2026-11-03 09:30", "2026-11-03 10:29", "2026-11-03 10:30", "2026-11-03 13:29",
                           "2026-11-03 13:30", "2026-11-03 14:29", "2026-11-03 14:45", "2026-11-03 09:10"])
    assert [str(x)[11:16] for x in it.bucket(ts, "1h")] == ["09:30", "09:30", "10:30", "12:30", "13:30", "13:30", "13:30", "09:30"]
    assert [str(x)[11:16] for x in it.bucket(ts, "4h")] == ["09:30", "09:30", "09:30", "09:30", "13:30", "13:30", "13:30", "09:30"]
    assert [str(x)[11:16] for x in it.bucket(ts, "15m")][:3] == ["09:30", "10:15", "10:30"]


def test_ticks_to_bars():
    trades = [{"t": _ms(2026, 11, 3, 9, 30, 5), "p": 10.0, "q": 100}, {"t": _ms(2026, 11, 3, 9, 30, 40), "p": 10.2, "q": 50},
              {"t": _ms(2026, 11, 3, 9, 34, 0), "p": 9.9, "q": 10}, {"t": _ms(2026, 11, 3, 14, 31, 0), "p": 10.1, "q": 5}]
    m1 = it.ticks_to_1m(trades)
    assert list(m1.index.strftime("%H:%M")) == ["09:30", "09:34", "14:29"]   # the 14:31 print folds into the last minute
    assert m1.iloc[0].to_dict() == {"open": 10.0, "high": 10.2, "low": 10.0, "close": 10.2, "volume": 150.0, "trades": 2}
    b5 = it.resample(m1, "5m")
    assert list(b5.index.strftime("%H:%M")) == ["09:30", "14:25"]
    assert b5.iloc[0][["open", "high", "low", "close", "volume"]].tolist() == [10.0, 10.2, 9.9, 9.9, 160.0]


def _bars(day, times, base=10.0, vol=100.0):
    idx = pd.DatetimeIndex([f"{day} {t}" for t in times])
    return pd.DataFrame({"open": base, "high": base + 0.1, "low": base - 0.1, "close": base, "volume": vol}, index=idx)


def test_merge_prefers_complete_tradingview_day():
    tv = pd.concat([_bars("2026-11-02", ["09:30", "13:30"], 10), _bars("2026-11-03", ["09:30"], 11)])
    cse = pd.concat([_bars("2026-11-02", ["09:30", "13:30"], 20, vol=300), _bars("2026-11-03", ["09:30", "13:30"], 21)])
    df, src = it.merge_sources(cse, tv)
    # 2 Nov: TradingView complete (same last bar) -> TradingView (crossings excluded, like the daily chart)
    # 3 Nov: TradingView stops earlier than cse.lk (fetched mid-session) -> cse.lk
    assert src.tolist() == ["tv", "tv", "cse", "cse"]
    assert df["close"].tolist() == [10, 10, 21, 21]
    df2, src2 = it.merge_sources(cse.iloc[:0], tv)
    assert src2.tolist() == ["tv"] * 3


def test_adjustment_per_day():
    df = pd.concat([_bars("2026-11-02", ["09:30"], 20), _bars("2026-11-03", ["09:30"], 10)])
    src = pd.Series(["cse", "cse"], index=df.index)
    daily = pd.DataFrame({"close": [10.0, 10.0], "raw_close": [20.0, 10.0], "volume": [200.0, 100.0], "raw_volume": [100.0, 100.0]},
                         index=pd.DatetimeIndex(["2026-11-02", "2026-11-03"]))   # a 1:1 bonus on 3 Nov halves earlier prices
    adj = it.adjust_intraday(df, src, daily, adjusted=True)
    assert adj["close"].tolist() == [10.0, 10.0]
    assert adj["volume"].tolist() == [200.0, 100.0]
    raw = it.adjust_intraday(df, src, daily, adjusted=False)
    assert raw["close"].tolist() == [20.0, 10.0]
    # a TradingView day already adjusted by TradingView (10 vs raw 20) is put back on the raw basis first
    tv_src = pd.Series(["tv", "cse"], index=df.index)
    df_tv = df.copy()
    df_tv.loc[df_tv.index[0], ["open", "high", "low", "close"]] = [10.0, 10.1, 9.9, 10.0]
    assert it.adjust_intraday(df_tv, tv_src, daily, adjusted=False)["close"].tolist() == [20.0, 10.0]


def test_time_labels():
    d = pd.DatetimeIndex(["2026-10-29", "2026-10-30"])
    assert tkey(d[-1]) == "2026-10-30" and tfuture(d, 1) == "2026-11-02"          # daily unchanged (Fri -> Mon)
    h = pd.DatetimeIndex(["2026-10-30 12:30", "2026-10-30 13:30"])
    assert tkey(h[-1]) == "2026-10-30 13:30"
    assert tfuture(h, 1) == "2026-11-02 09:30" and tfuture(h, 2) == "2026-11-02 10:30"   # skips the night and weekend
    f = pd.DatetimeIndex(["2026-11-03 09:30", "2026-11-03 13:30"])
    assert tfuture(f, 1) == "2026-11-04 09:30"                                    # 4H: 13:30 -> next morning


def test_completed_drops_forming_bar():
    df = _bars("2026-11-03", ["09:30", "09:45", "10:00"])
    assert len(it.completed(df, "15m", now=datetime(2026, 11, 3, 10, 5))) == 2
    assert len(it.completed(df, "15m", now=datetime(2026, 11, 3, 10, 16))) == 3
    assert len(it.completed(df, "15m", now=datetime(2026, 11, 3, 15, 0))) == 3


def _synthetic(tf: str, n: int, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    step = it.MINUTES[tf]
    times, d = [], pd.Timestamp("2026-06-01")
    while len(times) < n:
        if d.weekday() < 5:
            t = d + pd.Timedelta(minutes=570)
            while t < d + pd.Timedelta(minutes=870) and len(times) < n:
                times.append(t)
                t += pd.Timedelta(minutes=step)
        d += pd.Timedelta(days=1)
    c = 20 + np.cumsum(rng.normal(0, 0.08, n)) + 2 * np.sin(np.arange(n) / 40)
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) + rng.uniform(0, 0.08, n)
    l = np.minimum(o, c) - rng.uniform(0, 0.08, n)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": rng.integers(1_000, 50_000, n).astype(float),
                         "turnover": c * 10_000, "trades": 10, "open_estimated": False, "source": "tv"}, index=pd.DatetimeIndex(times))


def test_engines_run_on_intraday_frame():
    from cse_advisor.analysis import indicators
    from cse_advisor.analysis.liquidity import analyze_liquidity
    from cse_advisor.engine import analyze_frame, _jsonable
    df = _synthetic("15m", 900)
    htf = it.resample(df, "1h")
    daily = it.resample(df, "4h").groupby(lambda t: t.normalize()).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    daily["turnover"], daily["trades"], daily["open_estimated"] = daily["close"] * daily["volume"], 100, False
    liq = analyze_liquidity(indicators.add_all(daily), None)
    liq.pop("_patterns")
    sec = SimpleNamespace(symbol="TEST.N0000", name="Test PLC", info={}, shares_issued=1e8)
    shared = {"liquidity": _jsonable(liq), "fundamentals": None, "market": {"score": 50, "regime": "neutral", "detail": "", "factors": []}}
    r = analyze_frame(None, sec, df, [], tf="15m", shared=shared, htf_df=htf, anns=[], md=pd.DataFrame())
    rec = r["recommendation"]
    assert {"original", "analyst", "smc"} <= set(rec) and r["tf"] == "15m"
    assert all(rec[k]["verdict"] in ("BUY", "WATCH", "AVOID") for k in ("original", "analyst", "smc"))
    assert rec["smc"]["checklist"][0]["label"].startswith("1H")
    assert rec["smc"]["smc"]["labels"] == {"ltf": "15m", "htf": "1H", "htf_short": "1H"}
    assert all(len(p["start"]) == 16 for p in r["patterns"] if p["category"] != "candlestick")   # 'YYYY-MM-DD HH:MM'
    assert "weekly" not in rec["smc"]["explanation"]["summary"].lower()


def test_market_status_fallback():
    _status.update(at=0.0, open=None, open_seen=None)
    assert parse_status("Market Closed") is False and parse_status("Market Open") is True and parse_status("?") is None

    def boom():
        raise RuntimeError("down")
    # the CSE call failing falls back to the clock rule (whatever the time is now)
    from cse_advisor.cse_client import now_local
    assert market_open(fetch=boom) == clock_open(now_local())
    _status.update(at=0.0)
    assert market_open(fetch=lambda: "Market Open") is True
    assert clock_open(datetime(2026, 11, 3, 10, 0)) and not clock_open(datetime(2026, 11, 1, 10, 0))   # Sunday
    _status.update(at=0.0, open=None, open_seen=None)


def test_company_matcher_is_exact():
    secs = [("JKH.N0000", "JOHN KEELLS HOLDINGS PLC"), ("KHL.N0000", "John Keells Hotels PLC"), ("IND.N0000", "Industrial Asphalts (Ceylon) PLC")]
    assert match_company("JOHN KEELLS PLC", secs) is None
    assert match_company("John Keells Holdings PLC", secs) == "JKH.N0000"
    assert match_company("INDUSTRIAL ASPHALTS (CEYLON) PLC", secs) == "IND.N0000"
    assert match_company("", secs) is None


def test_modified_audit_opinion_is_a_red_flag():
    from cse_advisor.analysis.news import classify_announcement
    assert classify_announcement("Independent Auditor'S Report Containing Modified Audit Opinion", None)[0] == "red_flag"
    assert classify_announcement("Non-Compliance With Minimum Public Holding", None)[0] == "non_compliance"
