"""Support/resistance zones, volume profile and liquidity pools."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .pivots import Pivot
from .model import tkey


def volume_profile(df: pd.DataFrame, lookback: int = 120, bins: int = 40) -> dict[str, Any]:
    d = df.tail(lookback)
    lo, hi = float(d["low"].min()), float(d["high"].max())
    if not np.isfinite(lo) or hi <= lo:
        return {"bins": [], "poc": None, "va_low": None, "va_high": None, "hvn": [], "lvn": [], "lookback": lookback}
    edges = np.linspace(lo, hi, bins + 1)
    vol = np.zeros(bins)
    # spread each bar's volume uniformly across its high-low range
    for h, l, v in zip(d["high"].to_numpy(float), d["low"].to_numpy(float), d["volume"].to_numpy(float)):
        if v <= 0:
            continue
        i0 = max(0, int(np.searchsorted(edges, l, side="right")) - 1)
        i1 = min(bins - 1, int(np.searchsorted(edges, h, side="left")) - 1)
        i1 = max(i0, i1)
        vol[i0:i1 + 1] += v / (i1 - i0 + 1)
    total = vol.sum()
    poc_i = int(vol.argmax())
    # value area: expand from POC until 70% of volume
    in_va = {poc_i}
    acc = vol[poc_i]
    left, right = poc_i - 1, poc_i + 1
    while total > 0 and acc / total < 0.7 and (left >= 0 or right < bins):
        lv = vol[left] if left >= 0 else -1
        rv = vol[right] if right < bins else -1
        if rv >= lv:
            in_va.add(right); acc += rv; right += 1
        else:
            in_va.add(left); acc += lv; left -= 1
    mids = (edges[:-1] + edges[1:]) / 2
    mean = vol.mean() if bins else 0
    hvn = [float(mids[i]) for i in range(1, bins - 1) if vol[i] > 1.3 * mean and vol[i] >= vol[i - 1] and vol[i] >= vol[i + 1]]
    lvn = [float(mids[i]) for i in range(1, bins - 1) if vol[i] < 0.5 * mean and vol[i] <= vol[i - 1] and vol[i] <= vol[i + 1]]
    return {
        "bins": [{"low": float(edges[i]), "high": float(edges[i + 1]), "volume": float(vol[i]),
                  "share": float(vol[i] / total) if total else 0.0, "in_value_area": i in in_va} for i in range(bins)],
        "poc": float(mids[poc_i]),
        "va_low": float(edges[min(in_va)]), "va_high": float(edges[max(in_va) + 1]),
        "hvn": hvn, "lvn": lvn, "lookback": lookback,
    }


def volume_share_near(profile: dict[str, Any], price: float, width: float) -> float:
    return float(sum(b["share"] for b in profile.get("bins", [])
                     if b["high"] >= price - width and b["low"] <= price + width))


def sr_zones(df: pd.DataFrame, pivots: list[Pivot], atr_now: float, profile: dict[str, Any],
             max_zones: int = 9) -> list[dict[str, Any]]:
    """Cluster pivot prices into support/resistance zones scored by touches, recency and volume."""
    if not pivots or atr_now <= 0:
        return []
    n = len(df)
    close = float(df["close"].iloc[-1])
    tol = max(0.6 * atr_now, 0.012 * close)
    pts = sorted(pivots, key=lambda p: p.price)
    clusters: list[list[Pivot]] = []
    for p in pts:
        # compare with the cluster's lowest member so clusters can't chain into very wide bands
        if clusters and p.price - clusters[-1][0].price <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    dates = df.index
    zones = []
    for c in clusters:
        prices = [p.price for p in c]
        center = float(np.mean(prices))
        touches = len(c)
        last_idx = max(p.idx for p in c)
        recency = 0.5 ** ((n - 1 - last_idx) / 60)
        vshare = volume_share_near(profile, center, tol / 2)
        strength = touches * 1.0 + 2.0 * recency + 15 * vshare
        half = min(max(tol / 2, (max(prices) - min(prices)) / 2), 0.6 * atr_now)
        zones.append({
            "price": round(center, 2), "low": round(center - half, 2), "high": round(center + half, 2),
            "touches": touches, "last_touch": tkey(dates[last_idx]),
            "first_touch": tkey(dates[min(p.idx for p in c)]),
            "volume_share": round(vshare, 3), "strength": round(strength, 2),
            "kind": "support" if center < close else "resistance",
            "pivot_kinds": "".join(sorted({p.kind for p in c})),
        })
    zones = [z for z in zones if z["touches"] >= 2 or z["strength"] >= 3.0]
    zones.sort(key=lambda z: z["strength"], reverse=True)
    zones = zones[:max_zones]
    zones.sort(key=lambda z: z["price"])
    # merge zones whose bands overlap so the chart never shows stacked, overlapping bands
    merged: list[dict[str, Any]] = []
    for z in zones:
        if merged and z["low"] <= merged[-1]["high"]:
            m = merged[-1]
            t = m["touches"] + z["touches"]
            m["price"] = round((m["price"] * m["touches"] + z["price"] * z["touches"]) / t, 2)
            m["low"], m["high"] = min(m["low"], z["low"]), max(m["high"], z["high"])
            m["touches"], m["strength"] = t, round(m["strength"] + z["strength"], 2)
            m["volume_share"] = round(max(m["volume_share"], z["volume_share"]), 3)
            m["last_touch"], m["first_touch"] = max(m["last_touch"], z["last_touch"]), min(m["first_touch"], z["first_touch"])
            m["kind"] = "support" if m["price"] < close else "resistance"
        else:
            merged.append(dict(z))
    for z in merged:
        z["flip"] = _flip(df, z)
    return merged


def _flip(df: pd.DataFrame, z: dict[str, Any], lookback: int = 40) -> dict[str, Any] | None:
    """Role reversal: a resistance that price has CLOSED above (now support), or a support
    it has closed below (now resistance), within the last `lookback` bars."""
    c = df["close"].to_numpy(float)
    n = len(c)
    start = max(1, n - lookback)
    for j in range(n - 1, start - 1, -1):
        if z["kind"] == "support" and c[j] > z["high"] and c[j - 1] <= z["high"]:
            prior = c[max(0, j - 60):j]
            if (prior < z["low"]).any() and "H" in z.get("pivot_kinds", ""):
                return {"from": "resistance", "to": "support", "date": tkey(df.index[j]),
                        "text": f"Former resistance {z['low']:.2f}–{z['high']:.2f}, now support after the {tkey(df.index[j])} close above it"}
            return None
        if z["kind"] == "resistance" and c[j] < z["low"] and c[j - 1] >= z["low"]:
            prior = c[max(0, j - 60):j]
            if (prior > z["high"]).any() and "L" in z.get("pivot_kinds", ""):
                return {"from": "support", "to": "resistance", "date": tkey(df.index[j]),
                        "text": f"Former support {z['low']:.2f}–{z['high']:.2f}, now resistance after the {tkey(df.index[j])} close below it"}
            return None
    return None


def anchored_profile(df: pd.DataFrame, start_index: int, bins: int = 48) -> dict[str, Any]:
    """Volume profile from a chosen anchor (start of the dominant rally, or the peak of a decline)
    to today: POC = 'volume support/resistance', VAH/VAL = value-area edges."""
    start_index = max(0, min(start_index, len(df) - 5))
    p = volume_profile(df.iloc[start_index:], lookback=len(df) - start_index, bins=bins)
    p["anchor"] = tkey(df.index[start_index])
    return p


def liquidity_pools(df: pd.DataFrame, pivots: list[Pivot], lookback: int = 120) -> list[dict[str, Any]]:
    """Untaken swing highs/lows (where stop orders cluster) plus nearby round numbers."""
    n = len(df)
    hi, lo = df["high"].to_numpy(float), df["low"].to_numpy(float)
    close = float(df["close"].iloc[-1])
    pools = []
    for p in pivots:
        if not p.confirmed or p.idx < n - lookback:
            continue
        after = slice(p.idx + 1, n)
        taken = (hi[after].max(initial=-np.inf) > p.price) if p.kind == "H" else (lo[after].min(initial=np.inf) < p.price)
        if not taken:
            pools.append({"price": round(p.price, 2), "kind": "buy_stops_above" if p.kind == "H" else "sell_stops_below",
                          "time": tkey(df.index[p.idx]), "source": "swing"})
    step = 10 if close < 100 else 25 if close < 500 else 50 if close < 1000 else 100
    if close < 20:
        step = 1 if close < 10 else 5
    base = np.floor(close / step) * step
    for r in (base - step, base, base + step, base + 2 * step):
        if r > 0 and abs(r - close) / close < 0.15:
            pools.append({"price": float(r), "kind": "round_number", "time": None, "source": "round"})
    return pools
