"""Data-quality check for the Data health strip: single-session price spikes in the stored daily prices.

A spike is a close that is more than THRESHOLD above (or below) BOTH neighbouring closes: a typing slip or a bad tick, not a
move the market made (a real jump stays after it). Found with gs-quant's smooth_spikes, which replaces such a point with the
average of its neighbours; the points it changes are reported. Prices are the adjusted ones the engines use."""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

import gs_quant.timeseries as ts

THRESHOLD = 0.25
RECENT = 500                         # sessions checked
_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}


def spikes(close: pd.Series, threshold: float = THRESHOLD) -> list[dict[str, Any]]:
    x = close.dropna().tail(RECENT)
    if len(x) < 10:
        return []
    sm = ts.smooth_spikes(x, threshold)                  # the first and last points are dropped by gs-quant
    cur = x.reindex(sm.index)
    bad = cur[(cur - sm).abs() > 1e-9]
    out = []
    for t, v in bad.items():
        i = x.index.get_loc(t)
        out.append({"date": str(pd.Timestamp(t).date()), "close": round(float(v), 4), "neighbours": [round(float(x.iloc[i - 1]), 4), round(float(x.iloc[i + 1]), 4)]})
    return out


def price_spikes(s: Session, symbols: list[str]) -> list[dict[str, Any]]:
    from ..engine import load_adjusted
    items: list[dict[str, Any]] = []
    for sym in symbols:
        adj, _ = load_adjusted(s, sym)
        if adj.empty:
            continue
        key = (sym, str(adj.index[-1].date()))
        if key not in _cache:
            _cache[key] = spikes(adj["close"])
        items += [{"symbol": sym, **x} for x in _cache[key]]
    return sorted(items, key=lambda x: x["date"], reverse=True)
