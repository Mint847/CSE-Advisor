"""Live check of the stored analysis against the live price.

The daily analysis (patterns, plans) is computed once, after the close. During the session the chart shows a live
candle, but nothing told you when that candle had already overtaken a signal: DOCK's bear flag still read "target
77" while the live price was +10%, above the flag's own invalidation. This module compares the live price with
every level the stored analysis names and says what has changed, in words. It never changes the stored analysis
or any verdict; a pattern is only really invalidated (or confirmed) on a close.
"""
from __future__ import annotations

from typing import Any

FRESH_BARS = 15        # patterns older than this are history, not signals


def _fmt(x: float | None) -> str:
    return "–" if x is None else f"{x:,.2f}"


def pattern_status(p: dict[str, Any], last: float, high: float, low: float) -> dict[str, Any] | None:
    """Status of one pattern against the live price, or None when it is old, neutral or has no levels."""
    if p.get("category") in ("level", "smc") or p.get("direction") not in ("bullish", "bearish"):
        return None
    lv = p.get("levels") or {}
    age = (p.get("context") or {}).get("age_bars", 99)
    if p["status"] not in ("forming", "confirmed") or (p["status"] == "confirmed" and age > FRESH_BARS):
        return None
    bull = p["direction"] == "bullish"
    inv, tgt, brk = lv.get("invalidation"), lv.get("target"), lv.get("breakout")
    name = p["name"]
    if inv is not None and ((bull and last < inv) or ((not bull) and last > inv)):
        return {"state": "invalidated", "level": inv,
                "text": f"{name}: price {_fmt(last)} is {'below' if bull else 'above'} its invalidation level {_fmt(inv)}. "
                        f"It fails if the session closes here."}
    if tgt is not None and p["status"] == "confirmed" and ((bull and high >= tgt) or ((not bull) and low <= tgt)):
        return {"state": "target", "level": tgt,
                "text": f"{name}: target {_fmt(tgt)} reached today (high {_fmt(high)})" if bull else f"{name}: target {_fmt(tgt)} reached today (low {_fmt(low)})"}
    if p["status"] == "forming" and brk is not None and ((bull and last > brk) or ((not bull) and last < brk)):
        return {"state": "breaking", "level": brk,
                "text": f"{name}: price {_fmt(last)} is {'above' if bull else 'below'} the {_fmt(brk)} breakout level; it confirms only on a close beyond it."}
    return {"state": "intact", "level": None, "text": ""}


def plan_status(plan: dict[str, Any], last: float, high: float, low: float) -> dict[str, Any]:
    """Where the live price sits against one engine's trade plan."""
    zl, zh = plan["buy_zone"]["low"], plan["buy_zone"]["high"]
    stop, mc = plan["stop"], plan.get("max_chase")
    t1 = plan["targets"][0]["price"] if plan.get("targets") else None
    trig = plan.get("trigger")
    if low <= stop:
        return {"state": "stop_level_hit", "text": f"Price traded down to {_fmt(low)}, through the stop {_fmt(stop)}: this setup is broken."}
    if t1 is not None and high >= t1:
        return {"state": "t1_reached", "text": f"T1 {_fmt(t1)} was reached today (high {_fmt(high)}). The entry window has passed."}
    if mc is not None and last > mc:
        return {"state": "above_max_chase", "text": f"Price {_fmt(last)} is above the max-chase {_fmt(mc)}: the reward:risk no longer works from here. Wait for a pullback."}
    if trig is not None and last > trig:
        return {"state": "trigger_crossed", "text": f"Price {_fmt(last)} is above the trigger {_fmt(trig)}. It counts only if the session closes above it."}
    if trig is not None:
        return {"state": "waiting_trigger", "text": f"Waiting for a close above the trigger {_fmt(trig)} (price {_fmt(last)})."}
    if zl <= last <= zh:
        return {"state": "in_zone", "text": f"Price {_fmt(last)} is inside the buy zone {_fmt(zl)}–{_fmt(zh)} (limit {_fmt(plan.get('ideal'))})."}
    if last > zh:
        return {"state": "above_zone", "text": f"Price {_fmt(last)} is {last / zh * 100 - 100:.1f}% above the buy zone ({_fmt(zl)}–{_fmt(zh)}); the limit order waits for a pullback."}
    return {"state": "below_zone", "text": f"Price {_fmt(last)} is below the buy zone {_fmt(zl)}–{_fmt(zh)} but above the stop {_fmt(stop)}."}


def live_overlay(analysis: dict[str, Any], quote: dict[str, Any] | None) -> dict[str, Any] | None:
    """-> {price, time, patterns: {id: status}, plans: {engine: status}, summary: [str]} or None when the quote is
    missing or is not from the session after the stored analysis."""
    if not quote or not quote.get("close"):
        return None
    if str(quote.get("time", "")) <= str(analysis.get("date", "")):
        return None                       # the quote is from the analysed session itself (or older): nothing new
    last = float(quote["close"])
    high = float(quote.get("high") or last)
    low = float(quote.get("low") or last)
    rec = analysis.get("recommendation") or {}
    out: dict[str, Any] = {"price": last, "time": quote.get("last_trade"), "date": quote.get("time"), "open": quote.get("open"),
                           "high": high, "low": low, "patterns": {}, "plans": {}, "summary": []}
    for eng in ("original", "analyst", "smc"):
        plan = (rec.get(eng) or {}).get("plan")
        if plan:
            st = plan_status(plan, last, high, low)
            out["plans"][eng] = st
            if st["state"] in ("stop_level_hit", "t1_reached", "above_max_chase", "trigger_crossed", "in_zone"):
                out["summary"].append(f"[{eng[:4].upper()}] {st['text']}")
    prev = float(rec.get("close") or 0)
    if prev:
        out["change_pct"] = (last / prev - 1) * 100
    for p in analysis.get("patterns") or []:
        st = pattern_status(p, last, high, low)
        if st and st["state"] != "intact":
            out["patterns"][p["id"]] = st
            if st["state"] in ("invalidated", "target"):
                out["summary"].append(st["text"])
    return out
