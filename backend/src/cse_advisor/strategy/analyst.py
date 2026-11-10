"""The Levels engine (id: analyst): level-based analysis modelled on the KZOO / JKH / RIL example posts.

It runs next to (not inside) the Classic engine in recommend.py. Inputs are the confluence
buy zones, Fibonacci, trendlines, MA events, the extension / volatility / volume / drawdown
signals and the corporate-action phase. Output has the same shape as the Classic engine
(verdict, score, plan, checklist, explanation) plus the Entry 1 / Entry 2 / Zone 2 table."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..config import settings
from .recommend import GRADE_ORDER, _fmt, _src, build_plan, plan_explain
from .ticks import round_tick
from ..analysis.model import tkey



def analyst_setups(df: pd.DataFrame, zones: list[dict], trend: dict, ctx: dict) -> list[dict[str, Any]]:
    """Level-based setups in the style of the SLEG analysis: buy zones from confluence,
    retest of a flipped level, break of resistance / trendline, base retest after a big fall."""
    last = df.iloc[-1]
    close, atr = float(last["close"]), float(last["atr"])
    prev = float(df["close"].iloc[-2])
    conf = ctx.get("confluence") or {}
    sig = ctx.get("signals") or {}
    mom = sig.get("momentum") or {}
    fib = ctx.get("fib")
    out: list[dict[str, Any]] = []
    z1, z2 = conf.get("zone1"), conf.get("zone2")
    leg_up = bool(fib and fib["direction"] == "up")
    confirm = bool(mom.get("above_rsi_ma") or close > prev or mom.get("cross_above_50"))

    if z1:
        inside = z1["low"] - 0.3 * atr <= close <= z1["high"] + 0.5 * atr
        ok_context = trend["direction"] >= 0 or leg_up or mom.get("oversold_turn")
        out.append({
            "kind": "fib_pullback", "entry_label": "Entry 1", "triggered": bool(inside and confirm and ok_context),
            "pattern_id": None, "pattern_name": "Buy zone 1", "zone_low": z1["low"], "zone_high": z1["high"],
            "stop_base": z1["low"], "stop_reason": f"the bottom of Buy zone 1 ({_fmt(z1['low'])}: {_src(z1)})",
            "pattern_target": None, "quality": 2.6 + z1["score"] / 10 if ok_context else 1.2, "confluence": z1,
            "why": (f"Buy zone 1 {_fmt(z1['low'])}–{_fmt(z1['high'])} where {_src(z1)} line up (confluence score {z1['score']:.1f})"
                    + ("; price is in the zone now" if inside else f"; price is {(close / z1['high'] - 1) * 100:.1f}% above it, so this is a limit-order level")),
        })
    if z2:
        inside = z2["low"] - 0.3 * atr <= close <= z2["high"] + 0.5 * atr
        out.append({
            "kind": "zone2", "entry_label": "Zone 2", "triggered": bool(inside and confirm),
            "pattern_id": None, "pattern_name": "Buy zone 2 (if Zone 1 breaks)", "zone_low": z2["low"], "zone_high": z2["high"],
            "stop_base": z2["low"], "stop_reason": f"the bottom of Buy zone 2 ({_fmt(z2['low'])}: {_src(z2)})",
            "pattern_target": None, "quality": 1.4 + z2["score"] / 10, "confluence": z2,
            "why": f"deeper Buy zone 2 {_fmt(z2['low'])}–{_fmt(z2['high'])} ({_src(z2)}); a separate entry if Zone 1 fails, not an add-on to a losing position",
        })

    # Entry 2: retest of a level that flipped from resistance to support
    flipped = [z for z in zones if (z.get("flip") or {}).get("to") == "support"]
    flipped.sort(key=lambda z: z["flip"]["date"], reverse=True)
    retest_done = False
    for z in flipped[:1]:
        days = int((df.index[-1] - pd.Timestamp(z["flip"]["date"])).days)
        if days <= 30 and z["low"] - 0.2 * atr <= close <= z["high"] + 0.8 * atr:
            out.append({
                "kind": "breakout_retest", "entry_label": "Entry 2", "triggered": True, "pattern_id": None,
                "pattern_name": "Retest of flipped level", "zone_low": z["low"], "zone_high": z["high"] + 0.3 * atr,
                "stop_base": z["low"] - 0.5 * atr, "stop_reason": f"below the flipped level {_fmt(z['low'])}–{_fmt(z['high'])}",
                "pattern_target": None, "quality": 3.1,
                "why": f"{z['flip']['text']}; buying the retest of a broken resistance", "flip": z["flip"],
            })
            retest_done = True
    cur = conf.get("current")
    if not retest_done and cur and cur["center"] <= close:
        out.append({
            "kind": "support_hold", "entry_label": "Entry 2", "triggered": bool(confirm), "pattern_id": None,
            "pattern_name": "Hold of the current support", "zone_low": cur["low"], "zone_high": min(cur["high"], close + 0.2 * atr),
            "stop_base": cur["low"] - 0.3 * atr, "stop_reason": f"below the current support cluster {_fmt(cur['low'])} ({_src(cur)})",
            "pattern_target": None, "quality": 2.4 + cur["score"] / 20, "confluence": cur,
            "why": f"price is holding the {_fmt(cur['low'])}–{_fmt(cur['high'])} support cluster ({_src(cur)})"
                   + ("" if confirm else "; wait for an up close / RSI back above its average before buying"),
        })
        retest_done = True
    if not retest_done:
        res = sorted([z for z in zones if z["low"] > close and z["low"] - close <= 3 * atr], key=lambda z: z["low"])
        if res:
            r = res[0]
            trig = r["high"] + 0.2 * atr
            sup_below = max([z["high"] for z in zones if z["high"] < close] or [r["low"] - 1.5 * atr])
            stop_b = max(sup_below - 0.3 * atr, r["low"] - 2.0 * atr)
            out.append({
                "kind": "resistance_break", "entry_label": "Entry 2", "triggered": False, "pattern_id": None,
                "pattern_name": "Break & retest of resistance", "zone_low": r["high"], "zone_high": r["high"] + 0.4 * atr,
                "trigger": trig, "stop_base": stop_b, "stop_reason": f"below the support under the resistance ({_fmt(stop_b)})",
                "pattern_target": None, "quality": 1.6,
                "why": f"resistance {_fmt(r['low'])}–{_fmt(r['high'])} ({r['touches']} touches) just above; a close above {_fmt(trig)} turns it into support",
            })

    # falling-trendline break
    for p in ctx.get("trendlines", []):
        if p["type"] == "trendline_resistance" and p["status"] == "confirmed" and p["context"].get("broken_index") is not None \
                and len(df) - 1 - p["context"]["broken_index"] <= 3:
            lv = p["levels"]["level"]
            out.append({
                "kind": "trendline_break", "entry_label": "Entry 2", "triggered": True, "pattern_id": p.get("id"),
                "pattern_name": "Falling-trendline breakout", "zone_low": min(close, lv + 0.1 * atr), "zone_high": max(close, lv + 0.5 * atr),
                "stop_base": lv - 1.0 * atr, "stop_reason": f"1 ATR under the broken trendline ({_fmt(lv)})",
                "pattern_target": None, "quality": 2.7, "why": p["explanation"].split(". ")[0],
            })

    # base retest after a deep fall (KZOO 29 Oct style): WATCH with a reclaim trigger
    dd = sig.get("drawdown") or {}
    if (dd.get("pct") or 0) <= -35 and mom.get("oversold_turn"):
        sup = [z for z in zones if z["low"] - 0.2 * atr <= close <= z["high"] + 1.2 * atr]
        if sup:
            b = max(sup, key=lambda z: z["strength"])
            e21 = float(last.get("ema21", np.nan))
            tl = [p["levels"]["level"] for p in ctx.get("trendlines", []) if p["type"] == "trendline_resistance" and p["status"] == "forming"]
            trig_base = min([x for x in [e21] + tl if np.isfinite(x) and x > close] or [close + 0.5 * atr])
            out.append({
                "kind": "base_retest", "entry_label": "Base retest", "triggered": False, "pattern_id": None,
                "pattern_name": "Base retest (early reversal)", "zone_low": b["low"], "zone_high": max(b["high"], close),
                "trigger": trig_base + 0.1 * atr, "stop_base": b["low"], "stop_reason": f"the base support low {_fmt(b['low'])}",
                "pattern_target": None, "quality": 1.9,
                "why": (f"after a {dd['pct']:.0f}% fall from {dd['peak']:.2f} price is back on support {_fmt(b['low'])}–{_fmt(b['high'])} "
                        f"with RSI turning up from {mom.get('rsi_min10', 30):.0f}; a close above {_fmt(trig_base)} (21-day EMA / falling trendline) confirms"),
            })
    return out



STANDING_NOTE = ("Entry levels are where buyers can become strong; take-profit levels are where sellers can become strong "
                 "and can act as resistance. Price can fall below an entry level, overshoot a resistance, or never reach a "
                 "take-profit, so always use the stop and manage risk.")


def _explain_levels(expl: dict, verdict: str, plan: dict | None, entries: list[dict], ctx: dict, close: float,
                    atr: float, fundamentals: dict | None, sig: dict, corp: dict) -> None:
    """Analyst-style additions: level ladder with if-then scenarios, entry table, signal notes,
    corporate-action notes, Fibonacci position and value checks."""
    pf, pa = expl["reasons_for"], expl["reasons_against"]
    for t in sig.get("notes_for", []):
        pf.append({"text": t, "factor": "trend_pattern", "strength": 2})
    for t in sig.get("notes_against", []):
        pa.append({"text": t, "factor": "trend_pattern", "strength": 2})
    for t in corp.get("notes", []):
        pa.append({"text": t, "factor": "news", "strength": 3 if corp.get("phase") else 1})
    fib = ctx.get("fib")
    if fib:
        lv = sorted(fib["levels"], key=lambda x: x["price"])
        below = [x for x in lv if x["price"] <= close]
        above = [x for x in lv if x["price"] > close]
        txt = (f"Fibonacci ({fib['scale']}) of the {fib['move_pct']:.0f}% {'rally' if fib['direction'] == 'up' else 'decline'} "
               f"{fib['low']['price']:.2f} ↔ {fib['high']['price']:.2f}: price sits at the {fib['retracement_now']:.2f} level")
        if below:
            txt += f", nearest Fib support {below[-1]['ratio']:g} ({below[-1]['price']:.2f})"
        if above:
            txt += f", nearest Fib resistance {above[0]['ratio']:g} ({above[0]['price']:.2f})"
        (pf if fib["direction"] == "up" and fib["retracement_now"] <= 0.66 else pa if fib["direction"] == "down" and fib["retracement_now"] < 0.382 else pf).append(
            {"text": txt + ".", "factor": "trend_pattern", "strength": 2})
    for p in ctx.get("trendlines", []):
        lvl = p["levels"].get("level")
        if p["type"] == "trendline_resistance" and p["status"] == "forming" and lvl and 0 < lvl - close <= 2 * atr:
            pa.append({"text": f"Falling trendline resistance near {lvl:.2f}, only {(lvl / close - 1) * 100:.1f}% above, so selling pressure can build there.",
                       "factor": "trend_pattern", "strength": 2, "pattern_id": p.get("id")})
        if p["type"] == "trendline_support" and p["status"] == "forming" and lvl and 0 < close - lvl <= 1.5 * atr:
            pf.append({"text": f"Rising trendline support just below at {lvl:.2f}.", "factor": "trend_pattern", "strength": 1, "pattern_id": p.get("id")})
    ma = ctx.get("ma") or {}
    if ma.get("rejection_200"):
        pa.append({"text": f"Price was rejected at the {ma.get('ma200_name', '200-day average')} ({ma['rejection_200']['price']:.2f}) on {ma['rejection_200']['date']}: long-term sellers defend it.", "factor": "trend_pattern", "strength": 2})
    if ma.get("reclaim_200"):
        pf.append({"text": f"Price reclaimed the {ma.get('ma200_name', '200-day average')} on {ma['reclaim_200']['date']}.", "factor": "trend_pattern", "strength": 2})
    if ma.get("golden_cross"):
        pf.append({"text": f"Golden cross: the 50-day EMA crossed above the 200-day EMA on {ma['golden_cross']}.", "factor": "trend_pattern", "strength": 1})
    if ma.get("death_cross"):
        pa.append({"text": f"Death cross: the 50-day EMA crossed below the 200-day EMA on {ma['death_cross']}.", "factor": "trend_pattern", "strength": 1})
    if ma.get("below_all"):
        pa.append({"text": "Price is below its 21, 50 and 200-day averages (all overhead resistance).", "factor": "trend_pattern", "strength": 1})
    for z in ctx.get("zones", []):
        if z.get("flip") and abs(z["price"] - close) <= 3 * atr:
            (pf if z["flip"]["to"] == "support" else pa).append({"text": z["flip"]["text"] + ".", "factor": "trend_pattern", "strength": 2})
    if fundamentals and fundamentals.get("has_data"):
        r = fundamentals.get("ratios") or {}
        rel = fundamentals.get("relative") or {}
        bits = []
        if r.get("nav_per_share"):
            disc = r.get("price_to_nav_discount_pct")          # can be missing when the NAV exists but the discount could not be worked out (ACME, 10 Nov 2026)
            bits.append(f"NAV per share {r['nav_per_share']:.2f} vs price {close:.2f}" + (f" ({disc:+.0f}%)" if disc is not None else ""))
        if rel.get("pe_vs_market_pct") is not None:
            bits.append(f"P/E {rel['pe_vs_market_pct']:+.0f}% vs the market's {rel['market_pe']:g}")
        if rel.get("pbv_vs_market_pct") is not None:
            bits.append(f"P/BV {rel['pbv_vs_market_pct']:+.0f}% vs the market's {rel['market_pbv']:g}")
        vb = fundamentals.get("valuation_band")
        if vb:
            where = "below" if close < vb["low"] else "above" if close > vb["high"] else "inside"
            bits.append(f"value range {vb['low']:.2f} / {vb['mid']:.2f} / {vb['high']:.2f} (price {where} it)")
        if bits:
            cheap = (r.get("price_to_nav_discount_pct") or 0) < -20 or (rel.get("pe_vs_market_pct") or 0) < -25
            (pf if cheap else pa if (rel.get("pe_vs_market_pct") or 0) > 25 else pf).append(
                {"text": "Value check: " + "; ".join(bits) + ".", "factor": "fundamentals", "strength": 1})
    pf.sort(key=lambda x: -x["strength"])
    pa.sort(key=lambda x: -x["strength"])

    # ---- level ladder with if-then scenarios
    clusters = (ctx.get("confluence") or {}).get("all") or []
    sup = sorted([c for c in clusters if c["high"] < close or c["side"] == "at"], key=lambda c: -c["center"])
    res = sorted([c for c in clusters if c["low"] > close], key=lambda c: c["center"])
    sup = [c for c in sup if c["score"] >= 2][:5]
    res = [c for c in res if c["score"] >= 1.5][:5]
    ladder = []
    for i, c in enumerate(res):
        nxt = res[i + 1] if i + 1 < len(res) else None
        ladder.append({"side": "resistance", "low": c["low"], "high": c["high"], "score": c["score"], "sources": c["sources"],
                       "scenario": (f"Sellers can become strong here. A daily close above {c['high']:.2f} opens the way to "
                                    + (f"{nxt['low']:.2f}–{nxt['high']:.2f}." if nxt else "the next leg (no higher level mapped)."))})
    for i, c in enumerate(sup):
        nxt = sup[i + 1] if i + 1 < len(sup) else None
        ladder.append({"side": "support", "low": c["low"], "high": c["high"], "score": c["score"], "sources": c["sources"],
                       "scenario": (f"Buyers can become strong here. If it breaks (close below {c['low']:.2f}), the next support is "
                                    + (f"{nxt['low']:.2f}–{nxt['high']:.2f}." if nxt else "not mapped nearby; step aside."))})
    ladder.sort(key=lambda x: -x["high"])
    expl["ladder"] = ladder
    expl["standing_note"] = STANDING_NOTE

    # ---- entry table rows
    rows = []
    for e in entries:
        rows.append({"label": e.get("entry_label") or e["setup_name"], "setup": e["setup_name"],
                     "zone": f"{_fmt(e['buy_zone']['low'])} – {_fmt(e['buy_zone']['high'])}", "limit": e["ideal"],
                     "trigger": e.get("trigger"), "stop": e["stop"], "targets": [t["price"] for t in e["targets"]],
                     "rr": e["reward_risk"], "actionable": e["actionable"], "triggered": e["triggered"],
                     "why": e.get("why"), "sources": e.get("zone_sources")})
    expl["entries"] = rows
    if rows:
        w = expl["watch_next"]
        for r in rows:
            line = f"{r['label']}: {r['zone']}" + (f" after a close above {_fmt(r['trigger'])}" if r.get("trigger") else "")
            line += f", stop {_fmt(r['stop'])}" + (f", TP {' / '.join(_fmt(t) for t in r['targets'][:2])}" if r["targets"] else "")
            if r["label"] == "Zone 2":
                line += " (only if Zone 1 support fails; a new entry, not averaging down)"
            w.append(line + ".")


# ============================================================================ Levels engine (id: analyst)
ORDER = {"Entry 1": 0, "Entry 2": 1, "Zone 2": 2, "Base retest": 3}
HARD = {"red_flag", "suspension"}


def _cfg() -> dict[str, Any]:
    return {"buy_score": 55, **(settings().get("analyst") or {})}


def _recent(patterns: list[dict], types: tuple[str, ...], age: int = 10) -> list[dict]:
    return [p for p in patterns if p["type"] in types and p["context"].get("age_bars", 99) <= age and p["status"] != "failed"]


def level_score(df: pd.DataFrame, trend: dict, patterns: list[dict], setups: list[dict], entries: list[dict],
                ctx: dict, fundamentals: dict | None) -> tuple[float, list[dict[str, Any]]]:
    """0-100 score for the Levels engine: starts at 50 and every adjustment is listed."""
    last = df.iloc[-1]
    close = float(last["close"])
    sig, corp, ma, fib = ctx.get("signals") or {}, ctx.get("corporate") or {}, ctx.get("ma") or {}, ctx.get("fib")
    mom, vol, ext = sig.get("momentum") or {}, sig.get("volume") or {}, sig.get("extension") or {}
    pts: list[dict[str, Any]] = []

    def add(p: int, why: str) -> None:
        pts.append({"points": p, "reason": why})

    trig = {s["kind"] for s in setups if s.get("triggered")}
    kinds = {s["kind"] for s in setups}
    if "fib_pullback" in trig:
        add(10, "price is inside Buy zone 1 with confirmation")
    if trig & {"breakout_retest", "support_hold"}:
        add(8, "holding a flipped / current support level")
    if "trendline_break" in trig:
        add(5, "fresh falling-trendline breakout")
    if "base_retest" in kinds:
        add(5, "base retest after a deep fall (early reversal)")
    if ma.get("below_all") and trend["direction"] < 0:
        add(-10, "below the 21/50/200-day averages in a downtrend")
    if fib and fib["direction"] == "up" and fib["retracement_now"] > 0.786:
        add(-5, "retraced past Fib 0.786 (the rally structure is failing)")
    if mom.get("cross_above_50") or mom.get("above_rsi_ma"):
        add(5, "RSI back above 50 / above its average")
    if mom.get("oversold_turn"):
        add(5, "RSI turning up from oversold")
    if mom.get("cross_below_50"):
        add(-5, "RSI fell below 50")
    if _recent(patterns, ("bearish_rsi_divergence", "hidden_bearish_divergence")):
        add(-5, "recent bearish (or hidden bearish) RSI divergence")
    if _recent(patterns, ("bullish_rsi_divergence", "hidden_bullish_divergence")):
        add(5, "recent bullish (or hidden bullish) RSI divergence")
    streak = vol.get("high_volume_streak") or 0
    if streak >= 3 and close >= float(df["close"].iloc[-1 - streak]):
        add(5, f"{streak} straight high-volume sessions holding up (accumulation)")
    if vol.get("down_volume_rising"):
        add(-5, "selling volume rising (distribution)")
    if ext.get("flag"):
        add(-10, "over-extended from the 21-day EMA")
    if corp.get("phase") == "pre_xr":
        add(-5, "rights-issue XR gap ahead")
    elif corp.get("phase") == "overhang":
        add(-10, "rights-issue supply overhang after XR")
    vb = (fundamentals or {}).get("valuation_band")
    if vb:
        if close < vb["low"]:
            add(5, f"below the value range ({vb['low']:.2f}–{vb['high']:.2f})")
        elif close > vb["high"]:
            add(-5, f"above the value range ({vb['low']:.2f}–{vb['high']:.2f})")
    if any(e["reward_risk"] >= settings()["signals"]["min_reward_risk"] for e in entries):
        add(5, "at least one entry has reward:risk ≥ 2 after fees")
    score = float(np.clip(50 + sum(p["points"] for p in pts), 0, 100))
    return round(score, 1), pts


def _pick_plan(entries: list[dict]) -> dict | None:
    for test in (lambda e: e["triggered"] and e["actionable"], lambda e: e["triggered"], lambda e: e["actionable"], lambda e: True):
        hit = next((e for e in entries if test(e)), None)
        if hit:
            return hit
    return None


def _position_review_analyst(pos: dict, df: pd.DataFrame, patterns: list[dict], liquidity: dict, ctx: dict,
                             hard_flags: list, plan: dict | None) -> dict[str, Any]:
    last = df.iloc[-1]
    close, atr = float(last["close"]), float(last["atr"])
    entry = pos["entry_price"]
    sig, corp, conf = ctx.get("signals") or {}, ctx.get("corporate") or {}, ctx.get("confluence") or {}
    ext = sig.get("extension") or {}
    sup = [c for c in [conf.get("current"), conf.get("zone1")] if c and c["low"] < close]
    zone_stop = round_tick(max(c["low"] for c in sup) - 0.5 * atr, "down") if sup else None
    chand = float(df["high"].tail(22).max()) - 3 * atr
    stop = max([x for x in (pos.get("stop"), zone_stop, chand if close > entry else None) if x] or [0]) or None
    stop = round_tick(stop, "down") if stop else None
    sell, trim = [], []
    if stop and float(last["low"]) <= stop:
        sell.append(f"the session low {_fmt(float(last['low']))} broke the support-based stop {_fmt(stop)}")
    if (sig.get("volatility") or {}).get("guard") and close < entry:
        sell.append("extreme volatility while the position is under water")
    for f in hard_flags:
        sell.append(f"red-flag announcement: {f['title']}")
    if ext.get("extreme"):
        trim.append("price is extremely extended (RSI ≥ 90 or > 40% above EMA21); lock in part of the gain")
    elif ext.get("flag") and _recent(patterns, ("bearish_rsi_divergence", "hidden_bearish_divergence"), 5):
        trim.append("over-extended with a fresh bearish divergence")
    if corp.get("phase") == "overhang":
        trim.append("rights-issue supply overhang: new shares are reaching CDS accounts")
    sz = (conf.get("sell_zones") or [None])[0]
    if sz and float(last["high"]) >= sz["low"]:
        trim.append(f"price reached the {_fmt(sz['low'])}–{_fmt(sz['high'])} sell zone ({_src(sz)})")
    if (sig.get("volume") or {}).get("down_volume_rising") and close < float(last.get("ema21", np.inf)):
        trim.append("selling volume is rising and price lost the 21-day EMA")
    action = "SELL" if sell else "TRIM" if trim else "HOLD"
    return {"action": action, "reasons": sell or trim or ["price is holding its support levels"],
            "entry": entry, "quantity": pos["quantity"], "close": close, "pl_lkr": round((close - entry) * pos["quantity"], 0),
            "pl_pct": round((close / entry - 1) * 100, 2), "suggested_stop": stop, "chandelier": round(chand, 2),
            "target": sz["low"] if sz else None}


def recommend_analyst(symbol: str, name: str, df: pd.DataFrame, trend: dict, patterns: list[dict], zones: list[dict],
                      profile: dict, pools: list[dict], liquidity: dict, fundamentals: dict | None, news: dict | None,
                      position: dict | None, previous: dict | None, ctx: dict) -> dict[str, Any]:
    """The Levels engine (modelled on the KZOO / JKH / RIL posts): confluence buy zones, flip retests,
    trendline breaks and base retests; extension / volatility / rights-issue checks; level score."""
    cfg = settings()["signals"]
    acfg = _cfg()
    last = df.iloc[-1]
    close, atr = float(last["close"]), float(last["atr"])
    sig, corp = ctx.get("signals") or {}, ctx.get("corporate") or {}
    mom = sig.get("momentum") or {}
    sell_zones = (ctx.get("confluence") or {}).get("sell_zones")

    setups = analyst_setups(df, zones, trend, ctx)
    setups.sort(key=lambda s: (s["triggered"], s["quality"]), reverse=True)
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for st in setups:
        lab = st.get("entry_label")
        if not lab or lab in seen:
            continue
        seen.add(lab)
        entries.append(build_plan(st, df, zones, profile, pools, liquidity, fundamentals, sell_zones))
    entries.sort(key=lambda e: ORDER.get(e.get("entry_label") or "", 9))
    plan = _pick_plan(entries)
    score, pts = level_score(df, trend, patterns, setups, entries, ctx, fundamentals)

    hard_flags = [f for f in (news or {}).get("red_flags") or [] if f["category"] in HARD]
    grade_ok = GRADE_ORDER.index(liquidity["grade"]) <= GRADE_ORDER.index(cfg["min_liquidity_grade"])
    vola = sig.get("volatility") or {}
    ext = sig.get("extension") or {}
    has_base = any(s["kind"] == "base_retest" for s in setups)
    falling_knife = (trend["direction"] < 0 and (ctx.get("ma") or {}).get("below_all") and not any(e["triggered"] for e in entries)
                     and not has_base and not mom.get("oversold_turn"))
    checklist = [
        {"label": "A level entry has triggered", "passed": bool(plan and plan["triggered"]),
         "value": (f"{plan.get('entry_label')}: {plan['setup_name']}" + (" (triggered)" if plan["triggered"] else " (waiting)")) if plan else "no entry level"},
        {"label": f"Reward:risk ≥ {cfg['min_reward_risk']} after fees", "passed": bool(plan and plan["reward_risk"] >= cfg["min_reward_risk"]),
         "value": f"{plan['reward_risk']:.2f}" if plan else "n/a"},
        {"label": "Price not above max-chase", "passed": bool(plan and close <= plan["max_chase"]),
         "value": f"close {_fmt(close)} vs max {_fmt(plan['max_chase'])}" if plan else "n/a"},
        {"label": f"Liquidity grade ≥ {cfg['min_liquidity_grade']}", "passed": grade_ok, "value": liquidity["grade"]},
        {"label": "Not over-extended (RSI < 80, < 3 ATR / 25% above EMA21)", "passed": not ext.get("flag"),
         "value": f"{ext.get('pct_above_ema21') or 0:+.0f}% vs EMA21, RSI {ext.get('rsi') or 0:.0f}"},
        {"label": "No extreme volatility", "passed": not vola.get("guard"), "value": (vola.get("reasons") or ["normal"])[0]},
        {"label": "No rights-issue XR gap / supply overhang", "passed": corp.get("phase") is None, "value": corp.get("phase") or "none"},
        {"label": "No going-concern / suspension flag", "passed": not hard_flags, "value": ", ".join(f["category"] for f in hard_flags) or "none"},
        {"label": f"Level score ≥ {acfg['buy_score']}", "passed": score >= acfg["buy_score"], "value": f"{score:.0f}"},
    ]
    review = None
    if position:
        review = _position_review_analyst(position, df, patterns, liquidity, ctx, hard_flags, plan)
        verdict = review["action"]
    elif vola.get("guard") or hard_flags or liquidity["grade"] == "E" or falling_knife:
        verdict = "AVOID"
    elif all(c["passed"] for c in checklist):
        verdict = "BUY"
    else:
        verdict = "WATCH"

    # ---- explanation
    expl: dict[str, Any] = {"reasons_for": [], "reasons_against": [], "watch_next": []}
    for p in patterns:
        if p.get("engine") == "analyst" and p.get("category") != "level" and p["context"].get("age_bars", 99) <= 15:
            item = {"text": f"{p['name']} ({p['status']}): {p['explanation']}", "factor": "trend_pattern", "strength": 1, "pattern_id": p["id"]}
            (expl["reasons_for"] if p["direction"] == "bullish" else expl["reasons_against"]).append(item)
    _explain_levels(expl, verdict, plan, entries, ctx, close, atr, fundamentals, sig, corp)
    px = plan_explain(plan, setups, patterns, liquidity, atr)
    expl.update({k: px[k] for k in ("buy_prices", "sell_prices", "sizing", "scenarios", "invalidation")})
    expl["watch_next"] = px["watch_next"] + expl["watch_next"]
    expl["score_breakdown"] = [{"label": "Starting level score", "score": None, "weight": 0, "contribution": 50, "available": True}] + [
        {"label": p["reason"], "score": None, "weight": 0, "contribution": p["points"], "available": True} for p in pts]
    changes = []
    if previous and previous.get("verdict"):
        if previous["verdict"] != verdict:
            changes.append(f"Verdict changed {previous['verdict']} → {verdict}.")
        d = score - (previous.get("score") or score)
        if abs(d) >= 0.5:
            changes.append(f"Level score {'+' if d > 0 else ''}{d:.1f} (from {previous['score']:.0f} to {score:.0f}).")
    else:
        changes.append("First Levels-engine report for this stock.")
    expl["changes"] = changes

    s = f"{verdict}: {name} ({symbol}) has a level score of {score:.0f}/100. "
    if review:
        s += f"Your position is {review['pl_pct']:+.1f}%. {review['action']} because " + "; ".join(review["reasons"]) + ". "
        if review.get("suggested_stop"):
            s += f"Support-based stop {_fmt(review['suggested_stop'])}. "
    elif verdict == "AVOID":
        why = ([vola["reasons"][0]] if vola.get("guard") and vola.get("reasons") else []) + [f["title"] for f in hard_flags]
        if liquidity["grade"] == "E":
            why.append("illiquid (grade E)")
        if falling_knife and not why:
            why.append("price is below all its averages in a downtrend with no support entry or reversal signal")
        s += "Avoid for now: " + "; ".join(why or ["no clear direction"]) + ". Wait until the direction is clear."
    elif plan:
        s += (f"{plan.get('entry_label') or 'Entry'}: {plan['setup_name'].lower()} {_fmt(plan['buy_zone']['low'])}–{_fmt(plan['buy_zone']['high'])}"
              + (f" after a close above {_fmt(plan['trigger'])}" if plan.get("trigger") else "")
              + f", stop {_fmt(plan['stop'])}, TP " + " / ".join(_fmt(t["price"]) for t in plan["targets"][:2])
              + f", reward:risk {plan['reward_risk']:.2f} after fees.")
        if verdict == "WATCH":
            failed = [c["label"] for c in checklist if not c["passed"]]
            if failed:
                s += " Not a buy yet: " + "; ".join(failed) + "."
    else:
        s += "No support level is mapped close enough below price for a planned entry."
    for n in corp.get("notes", [])[:1]:
        s += " " + n
    expl["summary"] = s
    return {"engine": "analyst", "label": "Levels", "verdict": verdict, "score": score, "adjustments": pts,
            "plan": plan, "entries": entries, "setups": setups[:6], "checklist": checklist, "red_flags": hard_flags,
            "position_review": review, "explanation": expl, "close": close, "date": tkey(df.index[-1]),
            "signals": sig, "corporate": corp}
