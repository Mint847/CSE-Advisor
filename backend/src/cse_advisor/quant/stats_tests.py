"""Significance tests shared by the research studies and the quant pages (moved here from factor_study / macro_study).

- nw_t: t-statistic of a mean with Newey-West (Bartlett) standard errors, for overlapping or autocorrelated returns.
- clustered_t: t-statistic of a mean with errors clustered by group (e.g. by month), for many stocks on the same dates.
- corr / r_t: Pearson correlation with its sample size, and the t-statistic of a correlation.
A |t| above about 2 is the usual bar for "probably not chance"; below it, treat a result as noise.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def nw_t(x: Any, lag: int) -> float:
    """t-statistic of the mean with Newey-West (Bartlett) standard errors."""
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 12:
        return float("nan")
    e = x - x.mean()
    s = float(e @ e) / n
    for k in range(1, min(lag, n - 1) + 1):
        s += 2 * (1 - k / (lag + 1)) * float(e[k:] @ e[:-k]) / n
    return float(x.mean() / math.sqrt(s / n)) if s > 0 else float("nan")


def clustered_t(e: pd.Series, groups: pd.Series) -> float:
    e = e.dropna()
    if len(e) < 30:
        return float("nan")
    m = e.mean()
    se = float(np.sqrt(((e - m).groupby(groups.loc[e.index]).sum() ** 2).sum())) / len(e)
    return float(m / se) if se > 0 else float("nan")


def r_t(r: float, n: int) -> float:
    if n < 4 or not np.isfinite(r) or abs(r) >= 1:
        return float("nan")
    return float(r * math.sqrt((n - 2) / (1 - r * r)))


def corr(x: pd.Series, y: pd.Series) -> tuple[float, int]:
    d = pd.concat([x, y], axis=1).dropna()
    return (float(d.iloc[:, 0].corr(d.iloc[:, 1])) if len(d) >= 8 else float("nan")), len(d)
