"""Trend state by voting across several independent methods."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .indicators import ema
from .pivots import Pivot

STATES = ["strong_down", "down", "range", "up", "strong_up"]


def _regression(close: pd.Series, n: int) -> tuple[float, float]:
    y = close.tail(n).to_numpy(float)
    if len(y) < max(10, n // 2):
        return 0.0, 0.0
    x = np.arange(len(y), dtype=float)
    m, b = np.polyfit(x, y, 1)
    fit = m * x + b
    ss_res = ((y - fit) ** 2).sum()
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return float(m / y.mean() * 100), float(r2)   # slope in % per bar


def trend_state(df: pd.DataFrame, pivots: list[Pivot]) -> dict[str, Any]:
    last = df.iloc[-1]
    votes: list[dict[str, Any]] = []

    # 1. EMA alignment
    c, e20, e50, e200 = last["close"], last.get("ema20"), last.get("ema50"), last.get("ema200")
    if pd.notna(e20) and pd.notna(e50):
        if pd.notna(e200):
            if c > e20 > e50 > e200:
                v = 2
            elif c < e20 < e50 < e200:
                v = -2
            else:
                v = int(np.sign(c - e50)) + (1 if e50 > e200 else -1)
                v = int(np.clip(v, -1, 1))
        else:
            v = 1 if c > e20 > e50 else -1 if c < e20 < e50 else 0
        votes.append({"method": "EMA alignment", "vote": v,
                      "detail": f"close {c:.2f}, EMA20 {e20:.2f}, EMA50 {e50:.2f}" + (f", EMA200 {e200:.2f}" if pd.notna(e200) else " (EMA200 needs 200 bars)")})

    # 2. Swing structure (last two confirmed highs and lows)
    hs = [p for p in pivots if p.kind == "H" and p.confirmed][-2:]
    ls = [p for p in pivots if p.kind == "L" and p.confirmed][-2:]
    if len(hs) == 2 and len(ls) == 2:
        hh, hl = hs[1].price > hs[0].price, ls[1].price > ls[0].price
        v = 2 if hh and hl else -2 if (not hh and not hl) else 0
        label = {2: "higher highs & higher lows", -2: "lower highs & lower lows", 0: "mixed swings"}[v]
        votes.append({"method": "Swing structure", "vote": v // 1, "detail": label})

    # 3. ADX strength / direction
    if pd.notna(last.get("adx")):
        a, pdi, mdi = last["adx"], last["plus_di"], last["minus_di"]
        if a >= 25:
            v = 2 if pdi > mdi else -2
        elif a >= 20:
            v = 1 if pdi > mdi else -1
        else:
            v = 0
        votes.append({"method": "ADX/DMI", "vote": v, "detail": f"ADX {a:.0f} (+DI {pdi:.0f} / -DI {mdi:.0f})"})

    # 4. Regression channel slope & fit
    slope_pct, r2 = _regression(df["close"], 50)
    v = 0
    if r2 >= 0.5 and abs(slope_pct) >= 0.08:
        v = 2 if slope_pct > 0 else -2
    elif abs(slope_pct) >= 0.05:
        v = 1 if slope_pct > 0 else -1
    votes.append({"method": "50-bar regression", "vote": v, "detail": f"slope {slope_pct:+.2f}%/bar, R² {r2:.2f}"})

    # 5. Weekly trend (higher-timeframe filter)
    wk = df["close"].resample("W-FRI").last().dropna()
    weekly = 0
    if len(wk) >= 12:
        w10, w20 = ema(wk, 10).iloc[-1], ema(wk, 20).iloc[-1] if len(wk) >= 20 else np.nan
        if pd.notna(w20):
            weekly = 1 if wk.iloc[-1] > w10 > w20 else -1 if wk.iloc[-1] < w10 < w20 else 0
        else:
            weekly = 1 if wk.iloc[-1] > w10 else -1
        votes.append({"method": "Weekly trend", "vote": weekly * 2 if weekly else 0,
                      "detail": {1: "weekly closes above rising weekly EMAs", -1: "weekly closes below falling weekly EMAs", 0: "weekly trend flat"}[weekly]})

    total = sum(v["vote"] for v in votes)
    maxabs = 2 * len(votes) or 1
    norm = total / maxabs                     # -1..1
    idx = 4 if norm >= 0.6 else 3 if norm >= 0.2 else 2 if norm > -0.2 else 1 if norm > -0.6 else 0
    agree = sum(1 for v in votes if np.sign(v["vote"]) == np.sign(total) and v["vote"] != 0) / max(1, len(votes))
    return {
        "state": STATES[idx],
        "direction": 1 if idx >= 3 else -1 if idx <= 1 else 0,
        "score": round(norm, 3),
        "quality": round(abs(norm) * 0.6 + agree * 0.4, 3),
        "weekly": weekly,
        "regression": {"slope_pct_per_bar": round(slope_pct, 3), "r2": round(r2, 3)},
        "votes": votes,
    }
