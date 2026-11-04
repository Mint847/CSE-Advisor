"""The SMC engine: smart-money concepts, independent of the Original and Analyst engines.

Model (long only, like retail trading on the CSE): the weekly chart sets the bias; on the daily
chart wait for a bullish break of structure (BOS / CHoCH), then buy the pullback into discount
(below the 0.5 equilibrium of the impulse leg) at an unmitigated bullish order block or fair value
gap, or the retest of the broken level. Stop under the block, targets at the buy-side liquidity
above (untaken swing highs, equal highs) and at bearish blocks / gaps overhead."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..config import settings
from .recommend import GRADE_ORDER, _fmt, build_plan, plan_explain
from .ticks import round_tick
from ..analysis.model import tkey

ORDER = {"OB entry": 0, "FVG entry": 1, "Break & retest": 2, "Weekly OB": 3}
HARD = {"red_flag", "suspension"}
KILL_ZONE_NOTE = ("Kill zones (the London / New York session windows) are an intraday timing tool. This engine works on "
                  "end-of-day CSE bars, so they are not used; the closest CSE equivalent is the first hour after the "
                  "09:30 open and the run into the 14:30 close.")


def _cfg() -> dict[str, Any]:
    return {"buy_score": 55, "swing_len": 3, **(settings().get("smc") or {})}


def _pid(patterns: list[dict], key: str) -> str | None:
    return next((p["id"] for p in patterns if p.get("context", {}).get("key") == key), None)


def _overlap(a_low: float, a_high: float, b_low: float, b_high: float) -> bool:
    return a_low <= b_high and b_low <= a_high


def smc_setups(df: pd.DataFrame, smc: dict, patterns: list[dict]) -> list[dict[str, Any]]:
    last = df.iloc[-1]
    close, atr = float(last["close"]), float(last["atr"])
    low, high = float(last["low"]), float(last["high"])
    prev = float(df["close"].iloc[-2])
    est = bool(last.get("open_estimated")) if pd.notna(last.get("open_estimated")) else False
    react = close > prev or (not est and close > float(last["open"])) or close >= (high + low) / 2
    rng = smc["range"]
    up_leg = bool(rng and rng["dir"] == "up")
    eq = rng["eq"] if up_leg else None
    n = len(df)
    bull_flow = smc["trend"] == 1
    fresh_ssl = any(s["side"] == "sell" and s["idx"] >= n - 3 for s in smc["sweeps"])
    can_trigger = bull_flow or fresh_ssl
    target = rng["high"] if up_leg and rng["high"] > close + atr else None
    t_note = f"the dealing-range high {_fmt(target)} (buy-side liquidity above the leg)" if target else None
    w_obs = [w for w in smc["htf"].get("obs", []) if w["dir"] == "up" and w["status"] != "broken"]
    out: list[dict[str, Any]] = []

    def where(lo: float, hi: float) -> str:
        if eq is None:
            return "no bullish dealing range yet (latest break was down)"
        return "in discount (below equilibrium " + _fmt(eq) + ")" if hi <= eq + 0.1 * atr else "above equilibrium " + _fmt(eq) + " (premium)"

    fvgs = [f for f in smc["fvgs"] if f["dir"] == "up" and f["status"] != "filled"]
    obs = [x for x in smc["obs"] if x["dir"] == "up" and x["status"] != "broken" and x["bottom"] <= close + 0.2 * atr and x["top"] >= close - 6 * atr]
    obs.sort(key=lambda x: -x["top"])
    for ob in obs[:1]:
        inside = low <= ob["top"] + 0.1 * atr and close >= ob["bottom"]
        f_ov = next((f for f in fvgs if _overlap(f["bottom"], f["top"], ob["bottom"], ob["top"])), None)
        w_in = next((w for w in w_obs if _overlap(w["bottom"], w["top"], ob["bottom"], ob["top"])), None)
        q = 3.0 + (0.5 if ob["status"] == "fresh" else 0) + (0.4 if ob["swept"] else 0) + (0.3 if f_ov else 0) + (0.4 if w_in else 0)
        out.append({
            "kind": "order_block", "entry_label": "OB entry", "triggered": bool(inside and react and can_trigger),
            "pattern_id": _pid(patterns, f"ob|D|{ob['m']}"), "pattern_name": "Bullish order block",
            "zone_low": ob["bottom"], "zone_high": ob["top"], "ideal_price": min(ob["top"], close),
            "ideal_note": ("top of the order block (the proximal line): price often only taps the top before moving" if ob["top"] <= close
                           else "today's close: price is already inside the order block, below its top"),
            "stop_base": ob["bottom"], "stop_reason": f"the order-block low, its distal line ({_fmt(ob['bottom'])})",
            "pattern_target": target, "target_note": t_note, "quality": q, "ob": ob, "fvg_overlap": bool(f_ov), "htf_poi": bool(w_in),
            "why": (f"bullish order block {_fmt(ob['bottom'])}–{_fmt(ob['top'])} behind the {'CHoCH' if ob['bos_kind'] == 'choch' else 'BOS'} "
                    f"through {_fmt(ob['bos_level'])}; {where(ob['bottom'], ob['top'])}; "
                    + ("first return to it" if ob["status"] == "fresh" else f"tested {ob['touches']}× already")
                    + ("; it swept sell-side liquidity first" if ob["swept"] else "")
                    + ("; overlaps a fair value gap" if f_ov else "")
                    + ("; sits inside a weekly bullish order block (higher-timeframe point of interest)" if w_in else "")
                    + ("; price is in it now" if inside else f"; price is {(close / ob['top'] - 1) * 100:.1f}% above it, so this is a limit-order level")),
        })

    fv = [f for f in fvgs if f["bottom"] <= close + 0.2 * atr and f["top"] >= close - 6 * atr]
    fv.sort(key=lambda f: -f["top"])
    for f in fv[:1]:
        inside = low <= f["top"] and close >= f["bottom"]
        out.append({
            "kind": "fvg", "entry_label": "FVG entry", "triggered": bool(inside and react and can_trigger),
            "pattern_id": _pid(patterns, f"fvg|{f['idx']}"), "pattern_name": "Bullish fair value gap",
            "zone_low": f["bottom"], "zone_high": f["top"], "ideal_price": min(f["ce"], close),
            "ideal_note": "middle of the fair value gap (consequent encroachment)" if f["ce"] <= close else "today's close, already inside the gap",
            "stop_base": f["bottom"], "stop_reason": f"the bottom of the fair value gap ({_fmt(f['bottom'])})",
            "pattern_target": target, "target_note": t_note, "quality": 2.4 + (0.3 if f["status"] == "open" else 0),
            "why": (f"unfilled bullish fair value gap {_fmt(f['bottom'])}–{_fmt(f['top'])} from {smc['dates'][f['idx']]}; "
                    f"{where(f['bottom'], f['top'])}" + ("; price is in it now" if inside else "; a limit-order level")),
        })

    ups = [e for e in smc["events"] if e["dir"] == "up" and e["idx"] >= n - 30]
    if ups and bull_flow:
        ev = ups[-1]
        lv = ev["level"]
        after = df.iloc[ev["idx"] + 1:]
        retested = len(after) > 0 and float(after["low"].tail(3).min()) <= lv + 0.3 * atr and close >= lv - 0.1 * atr
        if retested or 0 < close - lv <= 3 * atr:
            out.append({
                "kind": "bos_retest", "entry_label": "Break & retest", "triggered": bool(retested and react),
                "pattern_id": _pid(patterns, "structure"), "pattern_name": "Retest of the broken structure level",
                "zone_low": lv - 0.2 * atr, "zone_high": lv + 0.3 * atr,
                "stop_base": min(float(df["low"].tail(3).min()), lv - 0.5 * atr) if retested else lv - 0.8 * atr,
                "stop_reason": "below the retest low" if retested else f"0.8 ATR under the broken level {_fmt(lv)}",
                "pattern_target": target, "target_note": t_note, "quality": 2.6 if retested else 1.5,
                "why": (f"{'CHoCH' if ev['kind'] == 'choch' else 'BOS'} up through {_fmt(lv)} on {smc['dates'][ev['idx']]}; "
                        + ("price came back to the broken level and held it (old resistance acting as support)" if retested
                           else f"wait for a pullback to {_fmt(lv)} (break and retest)")),
            })

    daily_in_w = any(s.get("htf_poi") for s in out)
    for w in sorted([w for w in w_obs if w["top"] < close + 0.2 * atr and w["top"] >= close * 0.75], key=lambda w: -w["top"])[:1]:
        if daily_in_w:
            break
        zl = max(w["bottom"], w["top"] - 2.0 * atr)
        inside = low <= w["top"] and close >= zl
        out.append({
            "kind": "weekly_ob", "entry_label": "Weekly OB", "triggered": bool(inside and react and can_trigger),
            "pattern_id": _pid(patterns, f"ob|W|{w['m']}"), "pattern_name": "Weekly bullish order block (refined)",
            "zone_low": zl, "zone_high": w["top"], "ideal_price": min(w["top"], close), "ideal_note": "top of the weekly order block (proximal line)",
            "stop_base": zl, "stop_reason": f"the refined bottom of the weekly block ({_fmt(zl)})",
            "pattern_target": target, "target_note": t_note, "quality": 1.8,
            "why": (f"weekly bullish order block {_fmt(w['bottom'])}–{_fmt(w['top'])}; no daily block inside it, so the entry is refined "
                    f"to its top 2 ATR ({_fmt(zl)}–{_fmt(w['top'])}), as you would refine on a lower timeframe"),
        })
    return out


def smc_score(df: pd.DataFrame, smc: dict, setups: list[dict], entries: list[dict]) -> tuple[float, list[dict[str, Any]]]:
    """0-100 SMC score: starts at 50 and every adjustment is listed."""
    close, atr = float(df["close"].iloc[-1]), float(df["atr"].iloc[-1])
    n = len(df)
    pts: list[dict[str, Any]] = []

    def add(p: int, why: str) -> None:
        pts.append({"points": p, "reason": why})

    htf = smc["htf"]
    if htf["bias"] == "bullish":
        add(10, "weekly structure is bullish (the last weekly break was up)")
    elif htf["bias"] == "bearish":
        add(-10, "weekly structure is bearish (the last weekly break was down)")
    ev = smc["events"][-1] if smc["events"] else None
    if ev:
        if ev["dir"] == "up":
            add(5, "daily CHoCH up: structure has turned bullish" if ev["kind"] == "choch" else "daily BOS up: bullish order flow")
        else:
            add(-5 if ev["kind"] == "choch" else -10, "daily CHoCH down: structure turned bearish" if ev["kind"] == "choch" else "daily BOS down: bearish order flow")
    rng = smc["range"]
    if rng and rng["dir"] == "up":
        if rng["pos"] < 0.5:
            add(5, f"price is in discount ({rng['pos'] * 100:.0f}% of the up leg, below equilibrium {rng['eq']:.2f})")
        if rng["in_ote"]:
            add(3, "inside the optimal-trade-entry band (0.62–0.79 retracement)")
        if rng["pos"] > 0.75:
            add(-5, f"price is deep in premium ({rng['pos'] * 100:.0f}% of the leg): late to buy")
    elif rng and rng["dir"] == "down" and rng["pos"] > 0.5:
        add(-5, "price is in the premium half of a down leg, where sellers step in")
    trig = {s["kind"] for s in setups if s["triggered"]}
    ob_set = next((s for s in setups if s["kind"] == "order_block"), None)
    if "order_block" in trig:
        add(10, "tapping an unmitigated bullish order block with a bullish reaction")
        if ob_set and ob_set["ob"]["status"] == "fresh":
            add(3, "first return to the order block (fresh orders)")
    if "fvg" in trig:
        add(5, "reacting inside an unfilled bullish fair value gap")
    if ob_set and ob_set.get("fvg_overlap"):
        add(3, "the order block overlaps a fair value gap")
    if "bos_retest" in trig:
        add(5, "the broken structure level held on the retest")
    if ob_set and ob_set.get("htf_poi"):
        add(5, "the daily block sits inside a weekly bullish order block")
    if "weekly_ob" in trig:
        add(5, "reacting at a weekly bullish order block")
    if any(s["side"] == "sell" and s["idx"] >= n - 5 for s in smc["sweeps"]):
        add(5, "sell-side liquidity was just swept (stops below a swing low taken, price closed back above)")
    if any(s["side"] == "buy" and s["idx"] >= n - 5 for s in smc["sweeps"]):
        add(-5, "buy-side liquidity was just swept (a possible short-term top)")
    sup = [x for x in smc["obs"] if x["dir"] == "down" and x["status"] != "broken" and 0 <= x["bottom"] - close <= 1.5 * atr] + \
          [f for f in smc["fvgs"] if f["dir"] == "down" and f["status"] != "filled" and 0 <= f["bottom"] - close <= 1.5 * atr]
    if sup:
        add(-5, f"supply just overhead: a bearish order block / fair value gap from {_fmt(min(x['bottom'] for x in sup))}")
    if any(e["reward_risk"] >= settings()["signals"]["min_reward_risk"] for e in entries):
        add(5, "at least one entry has reward:risk ≥ 2 after fees")
    return round(float(np.clip(50 + sum(p["points"] for p in pts), 0, 100)), 1), pts


def _pick_plan(entries: list[dict]) -> dict | None:
    for test in (lambda e: e["triggered"] and e["actionable"], lambda e: e["triggered"], lambda e: e["actionable"], lambda e: True):
        hit = next((e for e in entries if test(e)), None)
        if hit:
            return hit
    return None


def _position_review_smc(pos: dict, df: pd.DataFrame, smc: dict, hard_flags: list) -> dict[str, Any]:
    last = df.iloc[-1]
    close, atr = float(last["close"]), float(last["atr"])
    entry = pos["entry_price"]
    n = len(df)
    ups = [e for e in smc["events"] if e["dir"] == "up"]
    strong_low = None
    if ups:
        ev = ups[-1]
        strong_low = float(df["low"].iloc[ev["swing_idx"]:ev["idx"] + 1].min())
    stop_c = [x for x in (pos.get("stop"), round_tick(strong_low - 0.3 * atr, "down") if strong_low and strong_low < close else None) if x]
    stop = max(stop_c) if stop_c else None
    sell, trim = [], []
    ev = smc["events"][-1] if smc["events"] else None
    if stop and close < stop:
        sell.append(f"closed below the protective low / stop {_fmt(stop)}")
    if ev and ev["dir"] == "down" and ev["idx"] >= n - 3:
        sell.append(f"daily {'CHoCH' if ev['kind'] == 'choch' else 'BOS'} down through {_fmt(ev['level'])}: structure turned bearish")
    for f in hard_flags:
        sell.append(f"red-flag announcement: {f['title']}")
    if any(s["side"] == "buy" and s["idx"] >= n - 3 for s in smc["sweeps"]) and close > entry:
        trim.append("buy-side liquidity above a swing high was just swept: a common place for smart money to sell")
    sup = [x for x in smc["obs"] if x["dir"] == "down" and x["status"] != "broken" and x["bottom"] <= float(last["high"]) <= x["top"] + 0.1 * atr]
    if sup:
        trim.append(f"price reached a bearish order block {_fmt(sup[0]['bottom'])}–{_fmt(sup[0]['top'])} (supply)")
    rng = smc["range"]
    if rng and rng["dir"] == "up" and rng["pos"] >= 0.95 and close > entry:
        trim.append("price is at the top of the dealing range (premium)")
    action = "SELL" if sell else "TRIM" if trim else "HOLD"
    tgt = min([p["price"] for p in smc["pools"] if p["kind"] == "buy_stops_above" and p["price"] > close], default=None)
    return {"action": action, "reasons": sell or trim or ["bullish structure intact; the protective low is holding"],
            "entry": entry, "quantity": pos["quantity"], "close": close, "pl_lkr": round((close - entry) * pos["quantity"], 0),
            "pl_pct": round((close / entry - 1) * 100, 2), "suggested_stop": stop,
            "chandelier": round(float(df["high"].tail(22).max()) - 3 * atr, 2), "target": tgt}


def recommend_smc(symbol: str, name: str, df: pd.DataFrame, smc: dict, patterns: list[dict], liquidity: dict,
                  news: dict | None, position: dict | None, previous: dict | None) -> dict[str, Any]:
    cfg = settings()["signals"]
    scfg = _cfg()
    last = df.iloc[-1]
    close, atr = float(last["close"]), float(last["atr"])
    n = len(df)
    setups = smc_setups(df, smc, patterns)
    setups.sort(key=lambda s: (s["triggered"], s["quality"]), reverse=True)
    sell_zones = sorted(
        [{"low": x["bottom"], "high": x["top"], "sources": [f"bearish OB from {smc['dates'][x['b']]}"]}
         for x in smc["obs"] if x["dir"] == "down" and x["status"] != "broken" and x["bottom"] > close]
        + [{"low": f["bottom"], "high": f["top"], "sources": [f"bearish FVG from {smc['dates'][f['idx']]}"]}
           for f in smc["fvgs"] if f["dir"] == "down" and f["status"] != "filled" and f["bottom"] > close],
        key=lambda z: z["low"])
    entries = [build_plan(st, df, [], {}, smc["pools"], liquidity, None, sell_zones) for st in setups]
    entries.sort(key=lambda e: ORDER.get(e.get("entry_label") or "", 9))
    plan = _pick_plan(entries)
    score, pts = smc_score(df, smc, setups, entries)

    hard_flags = [f for f in (news or {}).get("red_flags") or [] if f["category"] in HARD]
    grade_ok = GRADE_ORDER.index(liquidity["grade"]) <= GRADE_ORDER.index(cfg["min_liquidity_grade"])
    rng, htf = smc["range"], smc["htf"]
    ev = smc["events"][-1] if smc["events"] else None
    fresh_ssl = any(s["side"] == "sell" and s["idx"] >= n - 5 for s in smc["sweeps"])
    bearish_flow = bool(ev and ev["dir"] == "down" and htf["bias"] != "bullish" and not any(e["triggered"] for e in entries) and not fresh_ssl)
    in_disc = bool(rng and rng["dir"] == "up" and (rng["pos"] < 0.5 or (plan and plan["buy_zone"]["high"] <= rng["eq"])))
    smc_kinds = ("order_block", "fvg", "bos_retest", "weekly_ob")
    checklist = [
        {"label": "Weekly (higher-timeframe) bias is bullish", "passed": htf["bias"] == "bullish",
         "value": htf["bias"] + (f" · {htf['last']['kind'].upper()} {'↑' if htf['last']['dir'] == 'up' else '↓'}" if htf.get("last") else "")},
        {"label": "Daily structure is bullish (last break up)", "passed": bool(ev and ev["dir"] == "up"),
         "value": f"{ev['kind'].upper()} {'↑' if ev['dir'] == 'up' else '↓'} {_fmt(ev['level'])} on {smc['dates'][ev['idx']]}" if ev else "no break yet"},
        {"label": "Entry in discount (below equilibrium)", "passed": in_disc,
         "value": ((f"entry zone top {_fmt(plan['buy_zone']['high'])} vs " if plan else "") + f"EQ {_fmt(rng['eq'])} · price at {rng['pos'] * 100:.0f}% of the "
                   + ("up" if rng["dir"] == "up" else "down") + " leg") if rng else "no range"},
        {"label": "An order block / FVG / retest entry has triggered", "passed": bool(plan and plan["triggered"] and plan["setup"] in smc_kinds),
         "value": (f"{plan.get('entry_label')}: " + ("triggered" if plan["triggered"] else "waiting")) if plan else "no entry level"},
        {"label": f"Reward:risk ≥ {cfg['min_reward_risk']} after fees", "passed": bool(plan and plan["reward_risk"] >= cfg["min_reward_risk"]),
         "value": f"{plan['reward_risk']:.2f}" if plan else "n/a"},
        {"label": "Price not above max-chase", "passed": bool(plan and close <= plan["max_chase"]),
         "value": f"close {_fmt(close)} vs max {_fmt(plan['max_chase'])}" if plan else "n/a"},
        {"label": f"Liquidity grade ≥ {cfg['min_liquidity_grade']}", "passed": grade_ok, "value": liquidity["grade"]},
        {"label": "No going-concern / suspension flag", "passed": not hard_flags, "value": ", ".join(f["category"] for f in hard_flags) or "none"},
        {"label": f"SMC score ≥ {scfg['buy_score']}", "passed": score >= scfg["buy_score"], "value": f"{score:.0f}"},
    ]
    review = None
    if position:
        review = _position_review_smc(position, df, smc, hard_flags)
        verdict = review["action"]
    elif hard_flags or liquidity["grade"] == "E" or bearish_flow:
        verdict = "AVOID"
    elif all(c["passed"] for c in checklist):
        verdict = "BUY"
    else:
        verdict = "WATCH"

    # ---- explanation
    pf: list[dict] = []
    pa: list[dict] = []
    for p in patterns:
        if p.get("engine") != "smc" or p["type"] in ("smc_structure", "premium_discount"):
            continue
        if p["type"] in ("bullish_order_block", "bearish_order_block", "bullish_fvg", "bearish_fvg"):
            near = p["zones"] and abs((p["zones"][0]["low"] + p["zones"][0]["high"]) / 2 - close) <= 4 * atr
            if not near:
                continue
        elif p["context"].get("age_bars", 99) > 10:
            continue
        (pf if p["direction"] == "bullish" else pa).append({"text": f"{p['name']}: {p['explanation']}", "factor": "trend_pattern", "strength": 2, "pattern_id": p["id"]})
    for p in pts:
        (pf if p["points"] > 0 else pa).append({"text": p["reason"][0].upper() + p["reason"][1:] + ".", "factor": "trend_pattern",
                                                 "strength": 3 if abs(p["points"]) >= 10 else 2})
    pf.sort(key=lambda x: -x["strength"])
    pa.sort(key=lambda x: -x["strength"])
    expl: dict[str, Any] = {"reasons_for": pf, "reasons_against": pa}
    px = plan_explain(plan, setups, patterns, liquidity, atr)
    expl.update({k: px[k] for k in ("buy_prices", "sell_prices", "sizing", "scenarios", "invalidation")})
    watch = list(px["watch_next"])
    if ev and ev["dir"] == "down":
        hs = [p for p in smc["pools"] if p["kind"] == "buy_stops_above" and p["price"] > close]
        if hs:
            lvl = min(p["price"] for p in hs)
            watch.append(f"Structure is bearish: a daily close above {_fmt(lvl)} (the last swing high) would be a bullish CHoCH; then buy the pullback into the new order block.")
    if fresh_ssl:
        swp = [s for s in smc["sweeps"] if s["side"] == "sell"][-1]
        watch.append(f"Sell-side liquidity under {_fmt(swp['level'])} was swept on {smc['dates'][swp['idx']]}: if a bullish CHoCH follows, the sweep low is the stop.")
    if rng and rng["dir"] == "up" and rng["pos"] >= 0.5:
        watch.append(f"Price is in premium: wait for a pullback below equilibrium {_fmt(rng['eq'])} into an order block or fair value gap.")
    expl["watch_next"] = watch
    expl["invalidation"] = [f"A daily close below the stop {_fmt(plan['stop'])} (the order block failed)."] if plan else []
    expl["invalidation"] += ["A daily CHoCH down (close below the last swing low that started the up leg).",
                             "A red-flag announcement (suspension, going concern)."]
    rows = []
    for e in entries:
        rows.append({"label": e.get("entry_label") or e["setup_name"], "setup": e["setup_name"],
                     "zone": f"{_fmt(e['buy_zone']['low'])} – {_fmt(e['buy_zone']['high'])}", "limit": e["ideal"],
                     "trigger": e.get("trigger"), "stop": e["stop"], "targets": [t["price"] for t in e["targets"]],
                     "rr": e["reward_risk"], "actionable": e["actionable"], "triggered": e["triggered"], "why": e.get("why"), "sources": None})
    expl["entries"] = rows
    expl["score_breakdown"] = [{"label": "Starting SMC score", "score": None, "weight": 0, "contribution": 50, "available": True}] + [
        {"label": p["reason"], "score": None, "weight": 0, "contribution": p["points"], "available": True} for p in pts]
    expl["standing_note"] = KILL_ZONE_NOTE
    changes = []
    if previous and previous.get("verdict"):
        if previous["verdict"] != verdict:
            changes.append(f"Verdict changed {previous['verdict']} → {verdict}.")
        d = score - (previous.get("score") or score)
        if abs(d) >= 0.5:
            changes.append(f"SMC score {'+' if d > 0 else ''}{d:.1f} (from {previous['score']:.0f} to {score:.0f}).")
    else:
        changes.append("First SMC-engine report for this stock.")
    expl["changes"] = changes

    s = f"{verdict}: {name} ({symbol}) has an SMC score of {score:.0f}/100. "
    s += (f"Weekly bias {htf['bias']}; daily structure " + (f"{'bullish' if ev['dir'] == 'up' else 'bearish'} ({ev['kind'].upper()} {'up' if ev['dir'] == 'up' else 'down'} through {_fmt(ev['level'])})" if ev else "undecided")
          + (f"; price is in {rng['zone']} ({rng['pos'] * 100:.0f}% of the leg, equilibrium {_fmt(rng['eq'])})" if rng else "") + ". ")
    if review:
        s += f"Your position is {review['pl_pct']:+.1f}%. {review['action']} because " + "; ".join(review["reasons"]) + ". "
    elif verdict == "AVOID":
        why = [f["title"] for f in hard_flags] + (["illiquid (grade E)"] if liquidity["grade"] == "E" else [])
        if bearish_flow and not why:
            why.append("bearish order flow (daily break down, weekly not bullish) and no bullish reaction at a block yet")
        s += "Avoid for now: " + "; ".join(why) + ". Wait for a bullish CHoCH."
    elif plan:
        s += (f"{plan.get('entry_label')}: {_fmt(plan['buy_zone']['low'])}–{_fmt(plan['buy_zone']['high'])}, limit {_fmt(plan['ideal'])}, "
              f"stop {_fmt(plan['stop'])}, TP " + " / ".join(_fmt(t["price"]) for t in plan["targets"][:2]) + f", reward:risk {plan['reward_risk']:.2f} after fees.")
        if verdict == "WATCH":
            failed = [c["label"] for c in checklist if not c["passed"]]
            if failed:
                s += " Not a buy yet: " + "; ".join(failed) + "."
    else:
        s += "No bullish order block, fair value gap or retest level close enough below price."
    expl["summary"] = s

    ctx = {**smc["summary"],
           "obs": [{"dir": x["dir"], "low": round(x["bottom"], 2), "high": round(x["top"], 2), "status": x["status"], "touches": x["touches"],
                    "date": smc["dates"][x["b"]], "swept": x["swept"], "fvg": x["fvg"]}
                   for x in sorted([x for x in smc["obs"] if x["status"] != "broken" and abs(x["mean"] - close) <= 8 * atr], key=lambda x: -x["top"])[:6]],
           "fvgs": [{"dir": f["dir"], "low": round(f["bottom"], 2), "high": round(f["top"], 2), "status": f["status"], "date": smc["dates"][f["idx"]]}
                    for f in sorted([f for f in smc["fvgs"] if f["status"] != "filled" and abs(f["ce"] - close) <= 8 * atr], key=lambda f: -f["top"])[:6]],
           "sweeps": [{"side": x["side"], "level": round(x["level"], 2), "date": smc["dates"][x["idx"]]} for x in smc["sweeps"][-4:]],
           "liquidity_above": sorted({p["price"] for p in smc["pools"] if p["kind"] == "buy_stops_above" and p["price"] > close})[:4],
           "liquidity_below": sorted({p["price"] for p in smc["pools"] if p["kind"] == "sell_stops_below" and p["price"] < close}, reverse=True)[:4]}
    labels = smc.get("labels") or {"ltf": "daily", "htf": "weekly"}
    ctx["labels"] = labels
    out = {"engine": "smc", "label": "SMC", "verdict": verdict, "score": score, "adjustments": pts, "plan": plan, "entries": entries,
           "setups": setups[:6], "checklist": checklist, "red_flags": hard_flags, "position_review": review, "explanation": expl,
           "close": close, "date": tkey(df.index[-1]), "smc": ctx}
    if labels["ltf"] != "daily":
        out = relabel(out, labels["ltf"], labels["htf"])
        out["explanation"]["standing_note"] = SESSION_NOTE
    return out


SESSION_NOTE = ("Kill zones (the London / New York session windows) don't exist on the CSE. The closest equivalent is the "
                "first hour after the 09:30 open and the last hour into the 14:30 close, when most CSE volume trades; the "
                "chart can shade them (drawer: SMC → session windows). They are shown only and not scored.")


def relabel(obj: Any, ltf: str, htf: str) -> Any:
    """Swap the words daily/weekly in user-visible text for the chart's timeframe and its higher timeframe
    (e.g. '15m' / '1H'). Word-boundary only, so ids such as 'weekly_ob' are left alone."""
    import re
    cap = lambda w: w[:1].upper() + w[1:]
    words = {"weekly": htf, "Weekly": cap(htf), "daily": ltf, "Daily": cap(ltf)}

    def sub(t: str) -> str:   # one pass, so a higher timeframe called 'daily' (4H charts) is not replaced again
        return re.sub(r"\b(weekly|Weekly|daily|Daily)\b", lambda m: words[m.group(1)], t)
    if isinstance(obj, dict):
        return {k: (v if k in ("kind", "engine", "verdict", "pattern_id") else relabel(v, ltf, htf)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [relabel(v, ltf, htf) for v in obj]
    if isinstance(obj, str):
        return sub(obj)
    return obj
