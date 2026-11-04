"""CSE tick-size rounding (bands from settings.yaml)."""
from __future__ import annotations

import math

from ..config import settings


def tick_size(price: float) -> float:
    for band in settings()["market"]["tick_sizes"]:
        if price < float(band["below"]):
            return float(band["tick"])
    return 0.25


def round_tick(price: float, mode: str = "nearest") -> float:
    if price is None or not math.isfinite(price) or price <= 0:
        return price
    t = tick_size(price)
    q = price / t
    q = math.floor(q + 1e-9) if mode == "down" else math.ceil(q - 1e-9) if mode == "up" else round(q)
    out = round(q * t, 2)
    # crossing a band boundary can change the tick; re-check once
    t2 = tick_size(out)
    if t2 != t:
        q = out / t2
        q = math.floor(q + 1e-9) if mode == "down" else math.ceil(q - 1e-9) if mode == "up" else round(q)
        out = round(q * t2, 2)
    return out
