"""Tests on synthetic price series with planted patterns, plus trade-plan invariants."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cse_advisor.analysis import indicators
from cse_advisor.analysis.candlesticks import detect_candlesticks
from cse_advisor.analysis.chart_patterns import detect_chart_patterns
from cse_advisor.analysis.levels import liquidity_pools, sr_zones, volume_profile
from cse_advisor.analysis.liquidity import analyze_liquidity
from cse_advisor.analysis.model import score_patterns
from cse_advisor.analysis.pivots import zigzag
from cse_advisor.analysis.trend import trend_state
from cse_advisor.strategy.recommend import recommend_original
from cse_advisor.strategy.ticks import round_tick, tick_size


def make_series(points: list[tuple[int, float]], lead: int = 60, lead_from: float | None = None,
                breakout_vol_at: int | None = None, noise: float = 0.3, seed: int = 1) -> pd.DataFrame:
    """Piecewise-linear close path through `points`, preceded by `lead` bars of gentle drift."""
    rng = np.random.default_rng(seed)
    xs, ys = zip(*points)
    path = np.interp(np.arange(xs[-1] + 1), xs, ys)
    start = lead_from if lead_from is not None else ys[0] * 0.9
    pre = np.linspace(start, ys[0], lead, endpoint=False)
    close = np.concatenate([pre, path]) + rng.normal(0, noise * 0.1, lead + len(path))
    n = len(close)
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + noise
    low = np.minimum(open_, close) - noise
    vol = np.full(n, 100_000.0)
    if breakout_vol_at is not None:
        vol[lead + breakout_vol_at:] = 400_000.0
    idx = pd.bdate_range("2025-01-01", periods=n)
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol,
                       "turnover": close * vol, "trades": 50, "open_estimated": False}, index=idx)
    return indicators.add_all(df)


def types(df: pd.DataFrame) -> dict[str, list]:
    piv = zigzag(df, df["atr"], k=1.5)
    out: dict[str, list] = {}
    for p in detect_chart_patterns(df, piv):
        out.setdefault(p.type, []).append(p)
    return out


# ---------------------------------------------------------------- ticks
def test_tick_sizes():
    assert tick_size(50) == 0.10 and tick_size(150) == 0.25
    assert round_tick(150.1, "down") == 150.0
    assert round_tick(150.1, "up") == 150.25
    assert round_tick(17.73, "nearest") == 17.7
    assert round_tick(99.97, "nearest") == 100.0


# ---------------------------------------------------------------- indicators
def test_rsi_bounds_and_atr():
    df = make_series([(0, 100), (40, 140)])
    assert df["rsi"].dropna().between(0, 100).all()
    assert df["rsi"].iloc[-1] > 70
    assert (df["atr"].dropna() > 0).all()


# ---------------------------------------------------------------- chart patterns
def test_ascending_triangle_breakout():
    pts = [(0, 90), (10, 100), (20, 92), (30, 100), (40, 95), (50, 100), (55, 97.5), (62, 108)]
    df = make_series(pts, breakout_vol_at=58)
    found = types(df)
    assert "ascending_triangle" in found, found.keys()
    tri = found["ascending_triangle"][0]
    assert tri.direction == "bullish" and tri.status == "confirmed"
    assert 99 <= tri.levels["breakout"] <= 102
    assert tri.levels["target"] > tri.levels["breakout"] > tri.levels["invalidation"]
    assert tri.context["volume_confirmed"]


def test_double_bottom():
    pts = [(0, 112), (15, 95), (30, 106), (45, 95.5), (62, 112)]
    found = types(make_series(pts, lead_from=125))
    assert "double_bottom" in found
    db = found["double_bottom"][0]
    assert db.direction == "bullish" and db.status == "confirmed"
    assert db.levels["target"] > db.levels["breakout"]


def test_head_and_shoulders():
    pts = [(0, 88), (10, 100), (20, 94), (30, 108), (40, 94.5), (50, 100.5), (62, 86)]
    found = types(make_series(pts, lead_from=80))
    assert "head_and_shoulders" in found
    hs = found["head_and_shoulders"][0]
    assert hs.direction == "bearish" and hs.status == "confirmed"
    assert hs.levels["target"] < hs.levels["breakout"]


def test_bull_flag():
    pts = [(0, 100), (8, 118), (20, 114), (26, 124)]
    found = types(make_series(pts, lead_from=96, breakout_vol_at=21))
    assert "bull_flag" in found
    assert found["bull_flag"][0].direction == "bullish"


def test_no_triangle_in_trend():
    found = types(make_series([(0, 100), (80, 160)]))
    assert not any(k.endswith("triangle") for k in found)


# ---------------------------------------------------------------- candlesticks
def test_bullish_engulfing():
    df = make_series([(0, 120), (30, 100)])
    i = len(df) - 1
    df.iloc[i - 1, df.columns.get_loc("open")] = df["close"].iloc[i - 2]
    df.iloc[i - 1, df.columns.get_loc("close")] = df["close"].iloc[i - 2] - 1.0
    df.iloc[i, df.columns.get_loc("open")] = df["close"].iloc[i - 1] - 0.1
    df.iloc[i, df.columns.get_loc("close")] = df["open"].iloc[i - 1] + 1.0
    df.iloc[i, df.columns.get_loc("high")] = df["close"].iloc[i] + 0.2
    df.iloc[i, df.columns.get_loc("low")] = df["open"].iloc[i] - 0.2
    found = [p.type for p in detect_candlesticks(df) if p.end_index == i]
    assert "bullish_engulfing" in found


# ---------------------------------------------------------------- full pipeline / plan invariants
def _full(df: pd.DataFrame) -> dict:
    piv = zigzag(df, df["atr"], k=1.5)
    trend = trend_state(df, piv)
    atr_now = float(df["atr"].iloc[-1])
    prof = volume_profile(df)
    zones = sr_zones(df, piv, atr_now, prof)
    pools = liquidity_pools(df, piv)
    liq = analyze_liquidity(df, None)
    raw = detect_chart_patterns(df, piv) + detect_candlesticks(df) + liq.pop("_patterns")
    pats = [p.to_dict() for p in score_patterns(raw, len(df), trend["direction"], [z["price"] for z in zones], atr_now, liq["grade"], set())]
    return recommend_original("TEST.N0000", "Test PLC", df, trend, pats, zones, prof, pools, liq, None, None, None, None, None)


def test_plan_invariants_on_breakout():
    pts = [(0, 90), (10, 100), (20, 92), (30, 100), (40, 95), (50, 100), (55, 97.5), (60, 103)]
    rec = _full(make_series(pts, breakout_vol_at=58, lead_from=70))
    plan = rec["plan"]
    assert plan is not None
    assert plan["stop"] < plan["buy_zone"]["low"] <= plan["ideal"] <= plan["buy_zone"]["high"]
    prices = [t["price"] for t in plan["targets"]]
    assert prices == sorted(prices) and prices[0] > plan["ideal"]
    for p in [plan["stop"], plan["ideal"], plan["max_chase"], *prices]:
        assert round_tick(p) == pytest.approx(p), p
    assert plan["size"]["qty"] >= 0
    if plan["size"]["days_to_exit"] is not None:
        assert plan["size"]["days_to_exit"] <= 3.0001
    ex = rec["explanation"]
    assert ex["summary"] and ex["buy_prices"] and ex["sell_prices"]
    assert rec["verdict"] in {"BUY", "WATCH", "AVOID"}


def test_explanation_numbers_come_from_plan():
    pts = [(0, 90), (10, 100), (20, 92), (30, 100), (40, 95), (50, 100), (55, 97.5), (60, 103)]
    rec = _full(make_series(pts, breakout_vol_at=58, lead_from=70))
    plan, ex = rec["plan"], rec["explanation"]
    stop_row = next(r for r in ex["sell_prices"] if r["label"] == "Stop-loss")
    assert stop_row["price"] == f"{plan['stop']:,.2f}"
    for t in plan["targets"]:
        assert any(r["label"] == t["label"] and r["price"] == f"{t['price']:,.2f}" for r in ex["sell_prices"])
