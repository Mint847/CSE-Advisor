"""Stock-selection features used by the factor study and the SCR ranking.

`features` turns a daily OHLCV frame (date index) into one row per day of the measures the 5 Nov 2026 factor study
tested: returns over 20 / 60 / 120 sessions, distance from the 52-week high, trend (close > EMA50 > EMA200),
volume trend, median turnover. Forward returns (`f<H>`) are added only when `fee` is given, for the study.

`qualifies` is the one combination that held up in that study (uptrend, within 10% of the 52-week high, top 40% by
120-day return among the stocks ranked). It is a tested starting point, not a prediction.
"""
from __future__ import annotations

import pandas as pd

HORIZONS = (20, 40, 60, 90)
NEAR_HIGH = -0.10          # within 10% of the 52-week high
RS_TOP = 0.60              # at or above the 60th percentile of 120-day return (top 40%)


def features(d: pd.DataFrame, aspi: pd.Series, fee: float | None = None) -> tuple[pd.DataFrame, pd.Series, pd.Series, pd.Series]:
    """Returns (features, ATR14, EMA50, EMA200)."""
    c, o, h, l, v = d["close"], d["open"], d["high"], d["low"], d["volume"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    def ema(n: int) -> pd.Series:
        return c.ewm(span=n, adjust=False, min_periods=n).mean()
    e20, e50, e200 = ema(20), ema(50), ema(200)
    a = aspi.reindex(d.index).ffill()
    F = pd.DataFrame(index=d.index)
    for n in (20, 60, 120):
        F[f"ret{n}"] = c / c.shift(n) - 1
    F["mom12_1"] = c.shift(20) / c.shift(250) - 1
    F["rs120"] = F["ret120"] - (a / a.shift(120) - 1)
    F["dist52"] = c / h.rolling(252).max() - 1
    F["volr"] = v.rolling(20).mean() / v.rolling(120).mean()
    F["turn"] = (c * v).rolling(60).median()
    F["atrp"] = atr / c
    F["ext20"], F["ext50"] = c / e20 - 1, c / e50 - 1
    F["above50"] = c > e50
    F["trendup"] = (c > e50) & (e50 > e200)
    if fee is not None:
        nxt = o.shift(-1)
        for H in HORIZONS:
            F[f"f{H}"] = c.shift(-H) * (1 - fee) / (nxt * (1 + fee)) - 1
        F["brk55"] = (c > h.shift(1).rolling(55).max()) & (c > e200)
        F["pb"] = F["trendup"] & (l <= e20) & (c > e20) & (c > pc)
    return F, atr, e50, e200


def qualifies(trendup: bool, dist52: float, rs_rank: float) -> bool:
    return bool(trendup) and dist52 > NEAR_HIGH and rs_rank >= RS_TOP
