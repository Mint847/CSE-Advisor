"""SMC detectors on a hand-built series with a known order block, FVG, BOS and sweep."""
from __future__ import annotations

import numpy as np
import pandas as pd

from cse_advisor.analysis.smc import (_atr, analyze_smc, candle_dirs, dealing_range, fair_value_gaps, order_blocks,
                                      structure, swings)


def frame(path: list[tuple[float, float]], wick: float = 0.3, low_override: dict[int, float] | None = None) -> pd.DataFrame:
    """path = [(open, close)]; highs/lows sit `wick` beyond the body."""
    o = np.array([p[0] for p in path], float)
    c = np.array([p[1] for p in path], float)
    h = np.maximum(o, c) + wick
    lo = np.minimum(o, c) - wick
    for i, v in (low_override or {}).items():
        lo[i] = v
    idx = pd.bdate_range("2026-01-05", periods=len(path))
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": 1000.0, "open_estimated": False}, index=idx)


def steps(*closes: float, start: float) -> list[tuple[float, float]]:
    out, prev = [], start
    for x in closes:
        out.append((prev, x))
        prev = x
    return out


# rally to a 110.3 high (bar 9), five down candles to 100 (bars 10-14), a fast impulse that breaks
# the high (bar 17), a pullback into the last down candle (bar 24), then a second break (bar 29).
PATH = steps(*[100 + i for i in range(1, 10)], start=100) + steps(110, start=109) + \
    steps(108, 106, 104, 102, 100, start=110) + steps(103, 107, 111, 113, start=100) + \
    steps(114, 115, 112, 108, 104, 102.5, 105, 108, 110, 112, 116, start=113)


def parts(df):
    o, c, h, l = (df[x].to_numpy(float) for x in ("open", "close", "high", "low"))
    atr = _atr(df)
    dirs = candle_dirs(df)
    sw = swings(h, l, 3)
    st = structure(c, h, l, sw)
    fv = fair_value_gaps(c, h, l, dirs, atr)
    obs = order_blocks(o, c, h, l, dirs, atr, st["events"], st["sweeps"], fv, "D")
    return o, c, h, l, atr, sw, st, fv, obs


def test_break_of_structure_and_order_block():
    df = frame(PATH)
    o, c, h, l, atr, sw, st, fv, obs = parts(df)
    first = st["events"][0]
    assert first["dir"] == "up" and first["idx"] == 17 and first["level"] == 110.3      # close 111 above the 110.3 swing high
    assert st["events"][1]["idx"] == 29 and st["events"][1]["kind"] == "bos"
    bull = [x for x in obs if x["dir"] == "up"]
    ob = bull[0]
    assert ob["m"] == 14 and ob["b"] == 10                     # the down candles before the impulse
    assert ob["bottom"] == 99.7                                # lowest wick of the down move
    assert ob["refined"] and ob["top"] == 102.3                # wide block refined to the last down candle
    assert ob["status"] == "tested" and ob["touches"] == 1     # price came back to it (bar 24) and held
    assert bull[1]["status"] == "fresh" and bull[1]["bottom"] == 102.2


def test_fair_value_gap_and_fill():
    df = frame(PATH)
    *_, fv, _ = parts(df)
    g = next(f for f in fv if f["dir"] == "up" and f["idx"] == 16)
    assert g["bottom"] == 103.3 and g["top"] == 106.7          # candle 15's high to candle 17's low
    assert g["status"] == "filled"                              # the pullback to 102.2 traded through it
    assert next(f for f in fv if f["idx"] == 26)["status"] == "open"


def test_premium_discount_range():
    df = frame(PATH)
    _, c, h, l, *_ = parts(df)
    st = structure(c, h, l, swings(h, l, 3))
    rng = dealing_range(c, h, l, st["events"])
    assert rng["dir"] == "up" and rng["low"] == 102.2 and rng["high"] == h.max()   # the leg behind the latest break
    assert abs(rng["eq"] - (rng["low"] + rng["high"]) / 2) < 1e-9
    assert rng["zone"] == "premium"                            # last close 116 is near the top of the leg


def test_wick_through_a_low_is_a_sweep_not_a_break():
    path = steps(*[110 - i for i in range(1, 8)], start=110) + steps(105, 106, 107, 106, 105, 104.5, start=103) + steps(105, 106, start=104.5)
    df = frame(path, low_override={13: 102.0})                 # bar 13 wicks under the 102.7 swing low but closes at 104.5
    _, c, h, l, *_ = parts(df)
    st = structure(c, h, l, swings(h, l, 3))
    assert any(s["side"] == "sell" and s["idx"] == 13 for s in st["sweeps"])
    assert not any(e["dir"] == "down" and e["idx"] == 13 for e in st["events"])


def test_analyze_smc_drawings():
    df = frame(PATH + steps(*[116 + i for i in range(1, 30)], start=116))
    df["atr"] = _atr(df)
    smc = analyze_smc(df)
    types = {p.type for p in smc["patterns"]}
    assert {"smc_structure", "premium_discount", "bullish_order_block"} <= types
    ob = next(p for p in smc["patterns"] if p.type == "bullish_order_block")
    assert ob.zones[0]["role"] == "order_block" and ob.levels["proximal"] > ob.levels["distal"]
    assert smc["summary"]["bias"] == "bullish"
