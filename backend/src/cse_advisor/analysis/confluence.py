"""Confluence zones: where several independent levels agree.

The analyst's buy zones are clusters. For example, KZOO Buy zone 1 = Fib 0.5 (9.3) + the 9.8
support; Buy zone 2 = Fib 0.786 (6.9) + the 6.3 support. Take-profit levels are the same
idea above price (Fib 0.236 at 12.1, resistance at 13.1). Each candidate level gets a
weight; nearby candidates are clustered; each cluster's score is the sum of its weights.
"""
from __future__ import annotations

from typing import Any

FIB_W = {0.236: 1.5, 0.382: 2.0, 0.5: 2.0, 0.618: 2.0, 0.66: 1.5, 0.786: 2.0}


def candidates(close: float, atr: float, zones: list[dict], fib: dict | None, anchored: dict | None,
               profile: dict | None, ma: dict | None, trend_patterns: list[dict]) -> list[dict[str, Any]]:
    c: list[dict[str, Any]] = []
    for z in zones:
        w = 1.0 + min(z["touches"], 5) * 0.4 + (1.0 if z.get("flip") else 0) + min(z.get("volume_share", 0) * 10, 1.0)
        role = z["kind"] if not z.get("flip") else z["flip"]["to"]
        txt = f"{'support' if z['price'] < close else 'resistance'} {z['low']:.2f}–{z['high']:.2f} ×{z['touches']}"
        if z.get("flip"):
            txt += f" (flipped {z['flip']['from']}→{z['flip']['to']})"
        c.append({"price": z["price"], "low": z["low"], "high": z["high"], "weight": round(w, 2), "kind": "sr", "text": txt, "role": role})
    if fib:
        for lv in fib["levels"]:
            c.append({"price": lv["price"], "weight": FIB_W.get(lv["ratio"], 1.0), "kind": "fib",
                      "text": f"Fib {lv['ratio']:g} ({lv['price']:.2f})"})
        for e in fib.get("extensions", []):
            if e["price"] > close:
                c.append({"price": e["price"], "weight": 1.0, "kind": "fib_ext", "text": f"Fib extension {e['ratio']:g} ({e['price']:.2f})"})
    if anchored and anchored.get("poc"):
        c.append({"price": anchored["poc"], "weight": 2.0, "kind": "poc",
                  "text": f"volume POC since {anchored.get('anchor')} ({anchored['poc']:.2f})"})
        for k, name in (("va_low", "value-area low"), ("va_high", "value-area high")):
            if anchored.get(k):
                c.append({"price": anchored[k], "weight": 1.5, "kind": k, "text": f"{name} ({anchored[k]:.2f})"})
    if profile and profile.get("poc") and (not anchored or abs(profile["poc"] - (anchored.get("poc") or 0)) > 0.5 * atr):
        c.append({"price": profile["poc"], "weight": 1.0, "kind": "poc120", "text": f"120-day volume POC ({profile['poc']:.2f})"})
    if ma:
        for label, v in (ma.get("position") or {}).items():
            if label in ("EMA200", "SMA200"):
                c.append({"price": v["value"], "weight": 1.5, "kind": "ma200", "text": f"{label} ({v['value']:.2f})"})
            elif label == "EMA50":
                c.append({"price": v["value"], "weight": 1.0, "kind": "ma50", "text": f"EMA50 ({v['value']:.2f})"})
    for p in trend_patterns:
        if p["type"] in ("trendline_resistance", "trendline_support") and p["status"] == "forming" and p["levels"].get("level"):
            c.append({"price": p["levels"]["level"], "weight": 1.0, "kind": "trendline",
                      "text": f"{'falling' if p['type'] == 'trendline_resistance' else 'rising'} trendline ({p['levels']['level']:.2f})"})
    return [x for x in c if x["price"] and x["price"] > 0]


def cluster(cands: list[dict[str, Any]], close: float, atr: float) -> list[dict[str, Any]]:
    tol = max(0.5 * atr, 0.012 * close)
    pts = sorted(cands, key=lambda x: x["price"])
    groups: list[list[dict]] = []
    for p in pts:
        if groups and p["price"] - groups[-1][0]["price"] <= 2 * tol:
            groups[-1].append(p)
        else:
            groups.append([p])
    out = []
    for g in groups:
        # same-kind duplicates count once (two S/R bands in one cluster are still one idea)
        best: dict[str, float] = {}
        for x in g:
            best[x["kind"]] = max(best.get(x["kind"], 0), x["weight"])
        score = sum(best.values())
        lo = min(x.get("low", x["price"]) for x in g)
        hi = max(x.get("high", x["price"]) for x in g)
        if hi - lo < 0.5 * atr:
            mid = (hi + lo) / 2
            lo, hi = mid - 0.25 * atr, mid + 0.25 * atr
        center = sum(x["price"] * x["weight"] for x in g) / sum(x["weight"] for x in g)
        out.append({"low": round(lo, 3), "high": round(hi, 3), "center": round(center, 3), "score": round(score, 2),
                    "sources": [x["text"] for x in sorted(g, key=lambda x: -x["weight"])],
                    "kinds": sorted(best), "side": "below" if hi < close else "above" if lo > close else "at"})
    return out


def zones_for_plan(clusters: list[dict[str, Any]], close: float, atr: float) -> dict[str, Any]:
    """Buy zone 1 (nearest strong cluster at/below price), Buy zone 2 (next major one below),
    and sell zones (clusters above price, nearest first)."""
    # the cluster price is sitting on is the 'current' level (retest / Entry 2 territory);
    # Buy zone 1 is the next strong cluster fully below it, Zone 2 the next major one below that.
    current = next((c for c in clusters if c["low"] - 0.25 * atr <= close <= c["high"] + 0.25 * atr), None)
    below = sorted([c for c in clusters if c["high"] < close - 0.25 * atr and c is not current and c["low"] >= close * 0.5],
                   key=lambda c: -c["high"])
    zone1 = next((c for c in below if c["score"] >= 3.0 and c["high"] >= close * 0.75), None)
    zone2 = None
    if zone1:
        zone2 = next((c for c in below if c is not zone1 and c["high"] < zone1["low"] - 1.0 * atr and c["score"] >= 3.5
                      and c["low"] >= close * 0.5), None)
    above = sorted([c for c in clusters if c["side"] == "above" and c["score"] >= 1.5], key=lambda c: c["low"])
    return {"zone1": zone1, "zone2": zone2, "current": current if current and current["score"] >= 2.5 else None,
            "sell_zones": above[:5], "all": clusters}
