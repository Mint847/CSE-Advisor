"""Swing-point (pivot) detection with an ATR-scaled ZigZag."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Pivot:
    idx: int
    price: float
    kind: str          # "H" or "L"
    confirmed: bool = True


def zigzag(df: pd.DataFrame, atr: pd.Series, k: float = 1.5, min_pct: float = 0.02) -> list[Pivot]:
    """Alternating swing highs/lows. A swing is confirmed once price reverses by
    max(k * ATR, min_pct * price) from the running extreme. The final running
    extreme is returned unconfirmed."""
    hi, lo = df["high"].to_numpy(float), df["low"].to_numpy(float)
    a = atr.bfill().fillna((df["high"] - df["low"]).mean()).to_numpy(float)
    n = len(df)
    if n < 3:
        return []
    pivots: list[Pivot] = []
    trend = 0                       # 1 = looking for a high, -1 = looking for a low
    ext_i, ext_hi_i, ext_lo_i = 0, 0, 0
    for i in range(1, n):
        thr = lambda j: max(k * a[j], min_pct * hi[j])  # noqa: E731
        if trend == 0:
            if hi[i] > hi[ext_hi_i]:
                ext_hi_i = i
            if lo[i] < lo[ext_lo_i]:
                ext_lo_i = i
            if hi[ext_hi_i] - lo[i] >= thr(i) and ext_hi_i < i:
                pivots.append(Pivot(ext_hi_i, hi[ext_hi_i], "H"))
                trend, ext_i = -1, i
            elif hi[i] - lo[ext_lo_i] >= thr(i) and ext_lo_i < i:
                pivots.append(Pivot(ext_lo_i, lo[ext_lo_i], "L"))
                trend, ext_i = 1, i
        elif trend == 1:            # rising: track highest high
            if hi[i] >= hi[ext_i]:
                ext_i = i
            elif hi[ext_i] - lo[i] >= thr(i):
                pivots.append(Pivot(ext_i, hi[ext_i], "H"))
                trend, ext_i = -1, i
        else:                       # falling: track lowest low
            if lo[i] <= lo[ext_i]:
                ext_i = i
            elif hi[i] - lo[ext_i] >= thr(i):
                pivots.append(Pivot(ext_i, lo[ext_i], "L"))
                trend, ext_i = 1, i
    if trend == 1:
        pivots.append(Pivot(ext_i, hi[ext_i], "H", confirmed=False))
    elif trend == -1:
        pivots.append(Pivot(ext_i, lo[ext_i], "L", confirmed=False))
    return pivots


def fit_line(points: list[Pivot]) -> tuple[float, float, float]:
    """Least-squares line through pivots -> (slope, intercept, max abs residual)."""
    x = np.array([p.idx for p in points], float)
    y = np.array([p.price for p in points], float)
    if len(points) < 2:
        return 0.0, float(y[0]), 0.0
    m, b = np.polyfit(x, y, 1)
    resid = np.abs(y - (m * x + b)).max()
    return float(m), float(b), float(resid)
