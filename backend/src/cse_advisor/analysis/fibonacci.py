"""Fibonacci retracements of the dominant price leg, on a LOG scale.

The analyst's KZOO, JKH and RIL levels are all reproduced by log-scale Fibonacci
(TradingView's behaviour on a log chart). For example, RIL's 0.236 / 0.5 / 0.786 = 25.2 /
15.5 / 9.2 come from a 6.2 -> 38.8 leg: exp(ln38.8 - f*(ln38.8 - ln6.2)).

Leg choice: the history's highest high and the lowest low before it (an up leg), or
that high and the lowest low after it (a down leg). The bigger move wins, unless the
newer leg is at least 60% as big, because price is then trading off the newer leg.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from ..config import settings
from .model import Pattern, round_price, tkey, tfuture

RETRACE = [0.236, 0.382, 0.5, 0.618, 0.66, 0.786]
EXTEND = [1.272, 1.618]
MIN_MOVE = 0.20          # the leg must be at least 20% (log ~0.18) to be worth drawing


def _scale() -> str:
    return str(settings().get("fibonacci", {}).get("scale", "log"))


def level_price(lo: float, hi: float, f: float, direction: str, scale: str = "log") -> float:
    """Price at ratio f. Up leg: retrace down from the high. Down leg: retrace up from the low."""
    if scale == "log":
        a, b = math.log(lo), math.log(hi)
        return math.exp(b - f * (b - a)) if direction == "up" else math.exp(a + f * (b - a))
    return hi - f * (hi - lo) if direction == "up" else lo + f * (hi - lo)


def robust_extremes(df: pd.DataFrame, wick_atr: float = 3.0, cap_atr: float = 1.5) -> tuple[np.ndarray, np.ndarray]:
    """Highs/lows with freak wicks capped. A single-bar wick of > 3 ATR beyond the candle body
    (e.g. KZOO's XR-day spike to 20.6 that triggered the price breakers) is not where the
    analyst anchors; such wicks are capped at body + 1.5 ATR."""
    h, l = df["high"].to_numpy(float).copy(), df["low"].to_numpy(float).copy()
    o, c = df["open"].to_numpy(float), df["close"].to_numpy(float)
    a = df["atr"].shift().bfill().to_numpy(float) if "atr" in df else np.full(len(df), np.nan)
    top, bot = np.maximum(o, c), np.minimum(o, c)
    for i in range(len(df)):
        if np.isfinite(a[i]) and a[i] > 0:
            if h[i] - top[i] > wick_atr * a[i]:
                h[i] = top[i] + cap_atr * a[i]
            if bot[i] - l[i] > wick_atr * a[i]:
                l[i] = bot[i] - cap_atr * a[i]
    return h, l


def dominant_leg(df: pd.DataFrame) -> dict[str, Any] | None:
    h, l = robust_extremes(df)
    n = len(df)
    if n < 30:
        return None
    hi_i = int(np.nanargmax(h))
    up_lo_i = int(np.nanargmin(l[: hi_i + 1])) if hi_i > 0 else None
    dn_lo_i = hi_i + int(np.nanargmin(l[hi_i:])) if hi_i < n - 1 else None
    legs = []
    if up_lo_i is not None and up_lo_i < hi_i:
        legs.append(("up", up_lo_i, hi_i, math.log(h[hi_i] / l[up_lo_i])))
    if dn_lo_i is not None and dn_lo_i > hi_i:
        legs.append(("down", hi_i, dn_lo_i, math.log(h[hi_i] / l[dn_lo_i])))
    legs = [x for x in legs if x[3] >= math.log(1 + MIN_MOVE)]
    if not legs:
        return None
    big = max(legs, key=lambda x: x[3])
    recent = max(legs, key=lambda x: x[2])
    leg = recent if recent[3] >= 0.6 * big[3] else big
    direction, i0, i1, mv = leg
    lo_i, hi_ix = (i0, i1) if direction == "up" else (i1, i0)
    return {"direction": direction, "start_index": i0, "end_index": i1,
            "low": {"index": lo_i, "time": tkey(df.index[lo_i]), "price": float(l[lo_i])},
            "high": {"index": hi_ix, "time": tkey(df.index[hi_ix]), "price": float(h[hi_ix])},
            "move_pct": round((math.exp(mv) - 1) * 100 if direction == "up" else (1 - math.exp(-mv)) * 100, 1)}


def fibonacci(df: pd.DataFrame) -> dict[str, Any] | None:
    leg = dominant_leg(df)
    if not leg:
        return None
    scale = _scale()
    lo, hi, d = leg["low"]["price"], leg["high"]["price"], leg["direction"]
    levels = [{"ratio": f, "price": round(level_price(lo, hi, f, d, scale), 3)} for f in RETRACE]
    ext = []
    for e in EXTEND:
        if d == "up":
            p = math.exp(math.log(lo) + e * (math.log(hi) - math.log(lo))) if scale == "log" else lo + e * (hi - lo)
        else:
            p = math.exp(math.log(hi) - e * (math.log(hi) - math.log(lo))) if scale == "log" else hi - e * (hi - lo)
        ext.append({"ratio": e, "price": round(p, 3)})
    close = float(df["close"].iloc[-1])
    if scale == "log":
        span = math.log(hi) - math.log(lo)
        pos = (math.log(hi) - math.log(close)) / span if d == "up" else (math.log(close) - math.log(lo)) / span
    else:
        pos = (hi - close) / (hi - lo) if d == "up" else (close - lo) / (hi - lo)
    return {**leg, "scale": scale, "levels": levels, "extensions": ext, "retracement_now": round(float(pos), 3)}


def fib_pattern(df: pd.DataFrame, fib: dict[str, Any]) -> Pattern:
    """Drawable pattern: one labelled line per level from the leg start to the right edge."""
    n = len(df)
    dates = [tkey(x) for x in df.index]
    t0 = dates[fib["start_index"]]
    t_end = tfuture(df.index, 8)
    lines = []
    for lv in fib["levels"]:
        lines.append({"from": {"time": t0, "price": round_price(lv["price"])}, "to": {"time": t_end, "price": round_price(lv["price"])},
                      "role": "fib", "style": "solid" if lv["ratio"] in (0.382, 0.5, 0.618) else "dashed",
                      "label": f"{lv['ratio']:g} ({lv['price']:.2f})"})
    gp = [lv["price"] for lv in fib["levels"] if lv["ratio"] in (0.618, 0.66)]
    zones = [{"t1": t0, "t2": t_end, "low": round_price(min(gp)), "high": round_price(max(gp)), "role": "golden_pocket"}] if len(gp) == 2 else []
    d = fib["direction"]
    leg_txt = (f"rally from {fib['low']['price']:.2f} ({fib['low']['time']}) to {fib['high']['price']:.2f} ({fib['high']['time']})" if d == "up"
               else f"decline from {fib['high']['price']:.2f} ({fib['high']['time']}) to {fib['low']['price']:.2f} ({fib['low']['time']})")
    lv = {f"fib_{x['ratio']:g}": x["price"] for x in fib["levels"]}
    expl = (f"Fibonacci retracement ({fib['scale']} scale) of the {fib['move_pct']:.0f}% {leg_txt}. "
            + ", ".join(f"{x['ratio']:g} = {x['price']:.2f}" for x in fib["levels"])
            + f". Price is now at the {fib['retracement_now']:.2f} retracement. "
            + ("In a pullback these levels act as support (buy zones); the 0.618–0.66 band is the 'golden pocket'."
               if d == "up" else "On a rebound these levels act as resistance (take-profit levels); a close above 0.5 shows real recovery."))
    return Pattern(type="fibonacci", direction="neutral", status="confirmed", start=t0, end=dates[-1], end_index=n - 1,
                   anchors=[{"time": fib["low"]["time"], "price": round_price(fib["low"]["price"]), "label": "Fib 1" if d == "up" else "Fib 0"},
                            {"time": fib["high"]["time"], "price": round_price(fib["high"]["price"]), "label": "Fib 0" if d == "up" else "Fib 1"}],
                   lines=lines, zones=zones, levels=lv, confidence=0.7, explanation=expl,
                   context={"direction": d, "retracement_now": fib["retracement_now"]})
