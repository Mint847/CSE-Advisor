"""Scoring, setup detection, trade plan (buy/sell prices), verdict and the in-depth
explanation. Every sentence in the explanation is generated from computed values,
so each claim can be traced to a number the engines produced."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from ..config import settings
from .ticks import round_tick, tick_size
from ..analysis.model import tkey

GRADE_ORDER = "ABCDE"
TREND_BASE = {"strong_up": 80, "up": 68, "range": 50, "down": 32, "strong_down": 20}
TREND_TEXT = {"strong_up": "a strong uptrend", "up": "an uptrend", "range": "a sideways range",
              "down": "a downtrend", "strong_down": "a strong downtrend"}


def _fmt(x: float | None, nd: int = 2) -> str:
    return "n/a" if x is None or (isinstance(x, float) and not math.isfinite(x)) else f"{x:,.{nd}f}"


def _pct(a: float, b: float) -> float:
    return (a / b - 1) * 100 if b else 0.0


# ============================================================================ component scores
def component_scores(trend: dict, patterns: list[dict], liquidity: dict, fundamentals: dict | None,
                     news: dict | None, market: dict | None, last: pd.Series) -> list[dict[str, Any]]:
    w = settings()["weights"]
    # trend & pattern
    tp = TREND_BASE[trend["state"]] + 6 * (trend["quality"] - 0.5)
    active = [p for p in patterns if p["status"] in ("confirmed", "forming") and p["context"].get("age_bars", 99) <= 10]
    bull = sum(p["priority"] for p in active if p["direction"] == "bullish") / 100
    bear = sum(p["priority"] for p in active if p["direction"] == "bearish") / 100
    tp += min(20, bull * 10) - min(20, bear * 10)
    rsi = float(last.get("rsi", np.nan))
    if np.isfinite(rsi):
        if rsi > 75:
            tp -= 5
        elif 40 <= rsi <= 60 and trend["direction"] > 0:
            tp += 3
    tp = float(np.clip(tp, 0, 100))
    # liquidity
    gmap = {"A": 90, "B": 78, "C": 62, "D": 42, "E": 20}
    lq = 0.6 * gmap[liquidity["grade"]] + 0.4 * liquidity["flow"]["score"]
    lq += {"awakening": 8, "active": 4, "frenzy": -10, "drying_up": -6, "dormant": -6}.get(liquidity["regime"], 0)
    lq = float(np.clip(lq, 0, 100))
    comps = [
        {"factor": "trend_pattern", "label": "Trend & patterns", "score": round(tp, 1), "weight": w["trend_pattern"], "available": True},
        {"factor": "liquidity", "label": "Liquidity", "score": round(lq, 1), "weight": w["liquidity"], "available": True},
        {"factor": "fundamentals", "label": "Fundamentals", "score": fundamentals.get("score") if fundamentals else None, "weight": w["fundamentals"]},
        {"factor": "news", "label": "News & announcements", "score": news.get("score") if news else None, "weight": w["news"]},
        {"factor": "macro", "label": "Market regime", "score": market.get("score") if market else None, "weight": w["macro"]},
    ]
    for c in comps:
        c["available"] = c["score"] is not None
    tot = sum(c["weight"] for c in comps if c["available"])
    for c in comps:
        c["effective_weight"] = round(c["weight"] / tot, 3) if c["available"] else 0.0
        c["contribution"] = round((c["score"] - 50) * c["effective_weight"], 2) if c["available"] else 0.0
    return comps


def composite(comps: list[dict]) -> float:
    return round(sum(c["score"] * c["effective_weight"] for c in comps if c["available"]), 1)


# ============================================================================ setups
def _nearest_support(zones: list[dict], price: float) -> dict | None:
    below = [z for z in zones if z["price"] < price]
    return max(below, key=lambda z: z["price"]) if below else None


def find_setups(df: pd.DataFrame, patterns: list[dict], zones: list[dict], trend: dict) -> list[dict[str, Any]]:
    """Classic engine setups: pattern breakouts, pending breakouts, reversals at support, pullbacks."""
    last = df.iloc[-1]
    close, atr = float(last["close"]), float(last["atr"])
    n = len(df)
    setups = []
    for p in patterns:
        if p["direction"] != "bullish":
            continue
        age = p["context"].get("age_bars", 99)
        lv = p["levels"]
        if p["category"] in ("chart_pattern", "liquidity") and p["type"] not in ("liquidity_sweep_low", "volume_awakening") \
                and p["status"] == "confirmed" and age <= 7 and lv.get("breakout") and close >= lv["breakout"] - 0.5 * atr \
                and close <= lv["breakout"] + 3 * atr and not (lv.get("target") and close >= lv["target"]):
            # (a breakout whose price has already run to the pattern's target, or more than 3 ATR past the breakout,
            # is no longer an entry: the zone sits far below the market and T1 is behind or at the price)
            setups.append({
                "kind": "breakout", "triggered": True, "pattern_id": p["id"], "pattern_name": p["name"],
                "zone_low": lv["breakout"], "zone_high": lv["breakout"] + 0.5 * atr,
                "stop_base": lv.get("invalidation", lv["breakout"] - 1.5 * atr),
                "stop_reason": f"the {p['name'].lower()} invalidation level {_fmt(lv.get('invalidation'))}",
                "pattern_target": lv.get("target"), "quality": 3 + p["priority"] / 100,
                "why": f"confirmed {p['name'].lower()} breakout at {_fmt(lv['breakout'])} {age} bar(s) ago",
            })
        elif p["category"] == "chart_pattern" and p["status"] == "forming" and p["priority"] >= 35 and lv.get("breakout"):
            trig = lv["breakout"] + 0.3 * atr
            setups.append({
                "kind": "pending_breakout", "triggered": False, "pattern_id": p["id"], "pattern_name": p["name"],
                "zone_low": trig, "zone_high": trig + 0.4 * atr, "trigger": trig,
                "stop_base": lv.get("invalidation", lv["breakout"] - 1.5 * atr),
                "stop_reason": f"the {p['name'].lower()} lower boundary {_fmt(lv.get('invalidation'))}",
                "pattern_target": lv.get("target"), "quality": 1.5 + p["priority"] / 100,
                "why": f"{p['name'].lower()} still forming; it triggers on a close above {_fmt(trig)}",
            })
        elif p["type"] in ("liquidity_sweep_low", "bullish_rsi_divergence") or (p["category"] == "candlestick" and p["tier"] == 2):
            if age <= 3 and p["status"] == "confirmed" and (p["context"].get("at_key_level") or p["type"] == "liquidity_sweep_low"):
                base = lv.get("level") or close
                setups.append({
                    "kind": "reversal", "triggered": True, "pattern_id": p["id"], "pattern_name": p["name"],
                    "zone_low": min(base, close), "zone_high": max(base, close) + 0.2 * atr,
                    "stop_base": lv.get("invalidation", base - atr),
                    "stop_reason": f"the {p['name'].lower()} low {_fmt(lv.get('invalidation'))}",
                    "pattern_target": None, "quality": 2.5 + p["priority"] / 100,
                    "why": f"{p['name'].lower()} at a key support level {age} bar(s) ago",
                })
    if trend["direction"] > 0:
        sup = _nearest_support(zones, close)
        e20, e50 = float(last.get("ema20", np.nan)), float(last.get("ema50", np.nan))
        if sup and close - sup["high"] <= 1.2 * atr:
            setups.append({
                "kind": "pullback", "triggered": close <= sup["high"] + 0.5 * atr, "pattern_id": None,
                "pattern_name": "Pullback to support", "zone_low": sup["low"], "zone_high": sup["high"],
                "stop_base": sup["low"], "stop_reason": f"the support zone low {_fmt(sup['low'])} ({sup['touches']} touches)",
                "pattern_target": None, "quality": 2 + min(sup["touches"], 5) / 10,
                "why": f"uptrend pulling back to support {_fmt(sup['low'])}–{_fmt(sup['high'])} ({sup['touches']} touches, last {sup['last_touch']})",
            })
        elif np.isfinite(e20) and 0 <= close - e20 <= 1.0 * atr:
            setups.append({
                "kind": "pullback", "triggered": True, "pattern_id": None, "pattern_name": "Pullback to EMA20",
                "zone_low": e20 - 0.3 * atr, "zone_high": e20 + 0.3 * atr,
                "stop_base": min(e50, e20 - 1.5 * atr) if np.isfinite(e50) else e20 - 1.5 * atr,
                "stop_reason": "below the 50-day EMA / 1.5 ATR under the 20-day EMA",
                "pattern_target": None, "quality": 1.8,
                "why": f"uptrend pulling back to the rising 20-day EMA ({_fmt(e20)})",
            })
    setups.sort(key=lambda s: (s["triggered"], s["quality"]), reverse=True)
    return setups


def _src(zone: dict, k: int = 3) -> str:
    return " + ".join(zone["sources"][:k])


# ============================================================================ trade plan
def build_plan(setup: dict, df: pd.DataFrame, zones: list[dict], profile: dict, pools: list[dict],
               liquidity: dict, fundamentals: dict | None, sell_zones: list[dict] | None = None) -> dict[str, Any]:
    cfg = settings()
    f = (cfg["costs"]["brokerage_pct_per_side"] + cfg["costs"]["slippage_pct"]) / 100
    risk_cfg = cfg["risk"]
    min_rr = cfg["signals"]["min_reward_risk"]
    last = df.iloc[-1]
    close, atr = float(last["close"]), float(last["atr"])
    notes: list[str] = []

    zl, zh = round_tick(setup["zone_low"], "up"), round_tick(setup["zone_high"], "down")
    if zh < zl:
        zh = zl
    ideal_raw = zl + 0.25 * (zh - zl)
    ideal_note = "lower quarter of the buy zone (patient limit order)"
    cluster = [x for x in (profile.get("hvn") or []) + [p["price"] for p in pools if p["kind"] == "round_number"] if zl <= x <= zh]
    if cluster:
        c = min(cluster)
        ideal_raw = c + tick_size(c)
        ideal_note = f"one tick above the {_fmt(c)} volume/round-number cluster where buyers are concentrated"
    if setup.get("ideal_price") is not None:
        ideal_raw, ideal_note = setup["ideal_price"], setup["ideal_note"]
    ideal = round_tick(max(zl, min(zh, ideal_raw)), "down")
    if ideal < zl:
        ideal = zl

    # ---- stop
    stop = setup["stop_base"] - 0.5 * atr
    stop_steps = [f"start from {setup['stop_reason']}", f"subtract 0.5 ATR ({_fmt(0.5 * atr)}) for noise → {_fmt(stop)}"]
    nearby = [p["price"] for p in pools if p["kind"] in ("sell_stops_below", "round_number") and stop <= p["price"] <= stop + 0.5 * atr]
    if nearby:
        pool = min(nearby)
        stop = pool - 0.2 * atr
        stop_steps.append(f"moved below the {_fmt(pool)} liquidity pool (obvious stop cluster) so a routine sweep doesn't stop you out → {_fmt(stop)}")
    if ideal - stop > 3 * atr:
        stop = ideal - 3 * atr
        stop_steps.append(f"capped at 3 ATR below entry → {_fmt(stop)}")
    elif ideal - stop < 1.0 * atr:
        stop = ideal - 1.0 * atr
        stop_steps.append(f"widened to 1 ATR below entry to avoid normal daily noise → {_fmt(stop)}")
    stop = round_tick(stop, "down")
    stop_steps.append(f"rounded down to the CSE tick → {_fmt(stop)}")
    risk_ps = ideal - stop

    # ---- targets
    res = sorted([z for z in zones if z["low"] > ideal + 0.5 * atr], key=lambda z: z["low"])
    swing_hi = sorted([p["price"] for p in pools if p["kind"] == "buy_stops_above" and p["price"] > ideal + 0.5 * atr])
    hi52 = (fundamentals or {}).get("market", {}).get("high_52w") or float(df["high"].tail(250).max())
    cands: list[tuple[float, str]] = []
    for z in res:
        cands.append((z["low"] - tick_size(z["low"]), f"just below the resistance zone {_fmt(z['low'])}–{_fmt(z['high'])} ({z['touches']} touches)"))
    for h in swing_hi:
        cands.append((h - tick_size(h), f"just below the untaken swing high {_fmt(h)} (buy-stop liquidity)"))
    if hi52 and hi52 > ideal + 0.5 * atr:
        cands.append((hi52 - tick_size(hi52), f"just below the 52-week high {_fmt(hi52)}"))
    for sz in sell_zones or []:
        if sz["low"] > ideal + 0.5 * atr:
            cands.append((sz["low"] - tick_size(sz["low"]),
                          f"just below the {_fmt(sz['low'])}–{_fmt(sz['high'])} sell zone where sellers can become strong ({_src(sz)})"))
    pt = setup.get("pattern_target")
    if pt and pt > ideal + atr:
        cands.append((pt, setup.get("target_note") or f"{setup['pattern_name'].lower()} measured-move target"))
    vb = (fundamentals or {}).get("valuation_band")
    if vb and vb["high"] > ideal + atr:
        cands.append((vb["high"], f"top of the fundamental valuation band ({vb['method']})"))
    # a target must clear at least 1 ATR and the round-trip costs + 1%, otherwise it isn't worth trading
    min_gain = max(1.0 * atr, ideal * (2 * f + 0.01))
    cands = sorted({round_tick(p, "down"): why for p, why in cands if p >= ideal + min_gain}.items())
    targets: list[dict[str, Any]] = []
    for p, why in cands:
        if not targets or p >= targets[-1]["price"] + 0.5 * atr:
            targets.append({"price": p, "source": why})
        if len(targets) == 3:
            break
    if len(targets) < 2:
        base_t = targets[-1]["price"] if targets else ideal
        for mult in (2, 3):
            p = round_tick(ideal + mult * risk_ps, "down")
            if p > base_t + 0.5 * atr and len(targets) < 2:
                targets.append({"price": p, "source": f"{mult}× the risk per share above entry (no overhead resistance in the 1-year history)"})
    for i, t in enumerate(targets):
        t["label"] = f"T{i + 1}"
        t["pct"] = round(_pct(t["price"], ideal), 2)
        t["net_pct"] = round(((t["price"] * (1 - f)) / (ideal * (1 + f)) - 1) * 100, 2)

    # ---- reward:risk after costs (half sold at T1, half at T2)
    t1 = targets[0]["price"] if targets else ideal + 2 * risk_ps
    t2 = targets[1]["price"] if len(targets) > 1 else t1
    tb = 0.5 * (t1 + t2)
    net_risk = ideal * (1 + f) - stop * (1 - f)
    net_reward = tb * (1 - f) - ideal * (1 + f)
    rr = net_reward / net_risk if net_risk > 0 else 0.0
    max_chase = round_tick((1 - f) * (tb + min_rr * stop) / ((1 + min_rr) * (1 + f)), "down")

    # ---- position size
    capital = risk_cfg["capital_lkr"]
    risk_amt = capital * risk_cfg["risk_per_trade_pct"] / 100
    qty_risk = math.floor(risk_amt / net_risk) if net_risk > 0 else 0
    qty_cap = math.floor(capital * risk_cfg["max_position_pct"] / 100 / (ideal * (1 + f)))
    adv = liquidity.get("adv_volume_20") or 0
    qty_liq = math.floor(risk_cfg["max_days_to_exit"] * risk_cfg["adv_participation"] * adv)
    qty = max(0, min(qty_risk, qty_cap, qty_liq))
    binding = "risk budget" if qty == qty_risk else "max position size" if qty == qty_cap else "liquidity (days-to-exit)"
    dte = qty / (risk_cfg["adv_participation"] * adv) if adv > 0 else None

    return {
        "setup": setup["kind"], "setup_name": setup["pattern_name"], "triggered": setup["triggered"],
        "entry_label": setup.get("entry_label"), "why": setup.get("why"),
        "zone_sources": (setup.get("confluence") or {}).get("sources"),
        "trigger": round_tick(setup["trigger"], "up") if setup.get("trigger") else None,
        "buy_zone": {"low": zl, "high": zh}, "ideal": ideal, "ideal_note": ideal_note, "max_chase": max_chase,
        "stop": stop, "stop_steps": stop_steps, "stop_pct": round(_pct(stop, ideal), 2),
        "stop_net_pct": round(((stop * (1 - f)) / (ideal * (1 + f)) - 1) * 100, 2),
        "targets": targets, "reward_risk": round(rr, 2), "cost_per_side_pct": round(f * 100, 2),
        "actionable": bool(rr >= min_rr and max_chase >= zl),
        "size": {"qty": qty, "value": round(qty * ideal, 0), "risk_lkr": round(qty * net_risk, 0),
                 "qty_by_risk": qty_risk, "qty_by_cap": qty_cap, "qty_by_liquidity": qty_liq, "binding": binding,
                 "days_to_exit": None if dte is None else round(dte, 2), "capital": capital,
                 "risk_pct": risk_cfg["risk_per_trade_pct"]},
        "atr": round(atr, 3), "close": close, "notes": notes,
        "trailing": {"method": "chandelier 3×ATR from the 22-day high, or a daily close below the 20-day EMA after T1"},
        "time_stop_weeks": 6,
    }


# ============================================================================ Classic engine (id: original)
def original_patterns(patterns: list[dict]) -> list[dict]:
    """Patterns the Classic engine scores: everything except the analyst- and SMC-engine drawings."""
    return [p for p in patterns if p.get("engine", "original") == "original" and p.get("category") != "level"]


def recommend_original(symbol: str, name: str, df: pd.DataFrame, trend: dict, patterns: list[dict], zones: list[dict],
                       profile: dict, pools: list[dict], liquidity: dict, fundamentals: dict | None, news: dict | None,
                       market: dict | None, position: dict | None, previous: dict | None) -> dict[str, Any]:
    """The original engine: 5-factor composite score, pattern/pullback setups, 7-item BUY checklist.
    It ignores every analyst-style input (Fibonacci, confluence, corporate phases, extension checks)."""
    cfg = settings()["signals"]
    patterns = original_patterns(patterns)
    last = df.iloc[-1]
    close, atr = float(last["close"]), float(last["atr"])
    comps = component_scores(trend, patterns, liquidity, fundamentals, news, market, last)
    score = composite(comps)
    setups = find_setups(df, patterns, zones, trend)
    setup = setups[0] if setups else None
    plan = build_plan(setup, df, zones, profile, pools, liquidity, fundamentals) if setup else None

    red_flags = list((news or {}).get("red_flags") or [])
    fund_flags = list((fundamentals or {}).get("flags") or [])
    grade_ok = GRADE_ORDER.index(liquidity["grade"]) <= GRADE_ORDER.index(cfg["min_liquidity_grade"])
    reversal_like = bool(plan and plan["setup"] == "reversal")
    checklist = [
        {"label": f"Composite score ≥ {cfg['buy_score']}", "passed": score >= cfg["buy_score"], "value": f"{score:.0f}"},
        {"label": "A setup has triggered", "passed": bool(plan and plan["triggered"]),
         "value": (plan["setup_name"] + (" (triggered)" if plan["triggered"] else " (waiting for trigger)")) if plan else "no bullish setup"},
        {"label": f"Liquidity grade ≥ {cfg['min_liquidity_grade']}", "passed": grade_ok, "value": liquidity["grade"]},
        {"label": "No red-flag announcements", "passed": not red_flags, "value": ", ".join(f["category"] for f in red_flags) or "none"},
        {"label": f"Reward:risk ≥ {cfg['min_reward_risk']} after fees", "passed": bool(plan and plan["reward_risk"] >= cfg["min_reward_risk"]),
         "value": f"{plan['reward_risk']:.2f}" if plan else "n/a"},
        {"label": "Price not above max-chase", "passed": bool(plan and close <= plan["max_chase"]),
         "value": f"close {_fmt(close)} vs max {_fmt(plan['max_chase'])}" if plan else "n/a"},
        {"label": "Trend not down (or reversal setup)", "passed": trend["direction"] >= 0 or reversal_like,
         "value": trend["state"].replace("_", " ")},
    ]
    review = None
    if position:
        review = _position_review(position, df, trend, patterns, liquidity, score, red_flags, plan)
        verdict = review["action"]
    elif red_flags or liquidity["grade"] == "E":
        verdict = "AVOID"
    elif all(c["passed"] for c in checklist):
        verdict = "BUY"
    elif trend["direction"] < 0 and not reversal_like:
        verdict = "AVOID"
    else:
        verdict = "WATCH"
    expl = _explain(symbol, name, verdict, score, comps, trend, patterns, liquidity, fundamentals, news, market,
                    plan, setups, checklist, close, atr, df, review, previous, red_flags, fund_flags)
    return {"engine": "original", "label": "Classic", "verdict": verdict, "score": score, "components": comps,
            "plan": plan, "entries": [], "setups": setups[:6], "checklist": checklist, "red_flags": red_flags,
            "fundamental_flags": fund_flags, "position_review": review, "explanation": expl,
            "close": close, "date": tkey(df.index[-1])}


AGREE_TEXT = {"agree": "The engines agree", "mixed": "The engines partly disagree", "differ": "The engines disagree"}
BULLISH = {"BUY", "HOLD"}
BEARISH = {"AVOID", "SELL"}


def agreement(a: str, b: str, c: str | None = None) -> dict[str, str]:
    """agree = every engine gives the same verdict; differ = at least one bullish (BUY/HOLD) and one
    bearish (AVOID/SELL); otherwise mixed."""
    vs = [v for v in (a, b, c) if v]
    if len(set(vs)) == 1:
        state = "agree"
    elif any(v in BULLISH for v in vs) and any(v in BEARISH for v in vs):
        state = "differ"
    else:
        state = "mixed"
    text = f"{AGREE_TEXT[state]}: Classic {a}, Levels {b}" + (f", SMC {c}" if c else "") + "."
    return {"state": state, "text": text}


# ============================================================================ position review
def _position_review(pos: dict, df: pd.DataFrame, trend: dict, patterns: list[dict], liquidity: dict,
                     score: float, red_flags: list, plan: dict | None) -> dict[str, Any]:
    cfg = settings()["signals"]
    last = df.iloc[-1]
    close, atr = float(last["close"]), float(last["atr"])
    entry = pos["entry_price"]
    chand = float(df["high"].tail(22).max()) - 3 * atr
    stop = max(x for x in (pos.get("stop") or 0, chand if close > entry else 0) if x is not None)
    stop = round_tick(stop, "down") if stop else None
    target = pos.get("target") or (plan["targets"][0]["price"] if plan and plan["targets"] else None)
    reasons_sell, reasons_trim = [], []
    if stop and float(last["low"]) <= stop:
        reasons_sell.append(f"the session low {_fmt(float(last['low']))} touched the stop {_fmt(stop)}")
    bear_recent = [p for p in patterns if p["direction"] == "bearish" and p["status"] == "confirmed"
                   and p["context"].get("age_bars", 99) <= 5 and p["priority"] >= 60]
    for p in bear_recent:
        reasons_sell.append(f"bearish {p['name'].lower()} confirmed ({p['priority']:.0f} priority)")
    if trend["direction"] < 0 and close < float(last.get("ema50", np.inf)) and float(last.get("rel_vol") or 0) >= 1.3:
        reasons_sell.append("trend turned down with a high-volume close below the 50-day EMA")
    if score < cfg["exit_score"]:
        reasons_sell.append(f"composite score fell to {score:.0f} (exit threshold {cfg['exit_score']})")
    for f in red_flags:
        reasons_sell.append(f"red-flag announcement: {f['title']}")
    if target and float(last["high"]) >= target:
        reasons_trim.append(f"target {_fmt(target)} reached; take partial profit and trail the rest")
    if liquidity["regime"] in ("frenzy", "drying_up"):
        reasons_trim.append(f"liquidity regime is {liquidity['regime'].replace('_', ' ')}")
    if float(last.get("rsi") or 0) > 78:
        reasons_trim.append(f"RSI {float(last['rsi']):.0f} is very overbought")
    if any(p["type"] == "bearish_rsi_divergence" and p["context"].get("age_bars", 99) <= 5 for p in patterns):
        reasons_trim.append("fresh bearish RSI divergence")
    action = "SELL" if reasons_sell else "TRIM" if reasons_trim else "HOLD"
    pl = (close - entry) * pos["quantity"]
    return {"action": action, "reasons": reasons_sell or reasons_trim or ["trend and liquidity still support the position"],
            "entry": entry, "quantity": pos["quantity"], "close": close, "pl_lkr": round(pl, 0),
            "pl_pct": round(_pct(close, entry), 2), "suggested_stop": stop, "chandelier": round(chand, 2), "target": target}


# ============================================================================ explanation
def _explain(symbol, name, verdict, score, comps, trend, patterns, liquidity, fundamentals, news, market,
             plan, setups, checklist, close, atr, df, review, previous, red_flags, fund_flags) -> dict[str, Any]:
    pf, pa = [], []   # reasons for / against: {text, factor, strength, pattern_id?}

    # trend
    pos_votes = [v for v in trend["votes"] if v["vote"] > 0]
    neg_votes = [v for v in trend["votes"] if v["vote"] < 0]
    tline = f"{len(pos_votes)} of {len(trend['votes'])} trend checks are bullish, {len(neg_votes)} bearish → {TREND_TEXT[trend['state']]}"
    (pf if trend["direction"] > 0 else pa if trend["direction"] < 0 else pa).append(
        {"text": f"Trend: {tline}. " + "; ".join(f"{v['method']}: {v['detail']}" for v in trend["votes"]) + ".",
         "factor": "trend_pattern", "strength": 3})
    # patterns (recent / active only)
    for p in patterns:
        if p.get("engine") == "analyst" or p.get("category") == "level":
            continue   # analyst-engine drawings are explained by the Levels engine
        if p["status"] not in ("confirmed", "forming") or p["context"].get("age_bars", 99) > 15 or p["priority"] < 35:
            continue
        txt = f"{p['name']} ({p['status']}, priority {p['priority']:.0f}): {p['explanation']}"
        item = {"text": txt, "factor": "trend_pattern", "strength": 2 if p["priority"] >= 60 else 1, "pattern_id": p["id"]}
        if p["direction"] == "bullish":
            pf.append(item)
        elif p["direction"] == "bearish":
            pa.append(item)
    last = df.iloc[-1]
    rsi = float(last.get("rsi", np.nan))
    if np.isfinite(rsi):
        if rsi >= 70:
            pa.append({"text": f"RSI is {rsi:.0f}, overbought, so the stock is stretched in the short term and a pullback entry is safer.", "factor": "trend_pattern", "strength": 2})
        elif rsi <= 30:
            (pf if trend["direction"] >= 0 else pa).append({"text": f"RSI is {rsi:.0f}, oversold.", "factor": "trend_pattern", "strength": 1})
    rv = last.get("rel_vol")
    if rv is not None and np.isfinite(rv) and rv >= 1.5:
        up = last["close"] >= df["close"].iloc[-2]
        (pf if up else pa).append({"text": f"Today's volume was {rv:.1f}× the 20-day average on a{'n up' if up else ' down'} day.", "factor": "liquidity", "strength": 2})

    # liquidity
    g = liquidity["grade"]
    gtxt = f"Liquidity grade {g}: " + "; ".join(liquidity["grade_notes"])
    if liquidity.get("spread_est_pct") is not None:
        gtxt += f"; estimated spread {liquidity['spread_est_pct']:.2f}%"
    (pf if g in "AB" else pa if g in "DE" else pf).append({"text": gtxt + ".", "factor": "liquidity", "strength": 3 if g in "ADE" else 2})
    fl = liquidity["flow"]
    ftxt = (f"Money flow reads {fl['label']} (score {fl['score']:.0f}/100): up-day vs down-day volume {fl['up_down_volume_ratio']:.2f}×, "
            f"5-day turnover {fl['turnover_5_vs_20']:.2f}× the 20-day average, OBV trend {'rising' if fl['obv_slope_norm'] > 0 else 'falling'}.")
    if fl.get("avg_trade_size"):
        ats = fl["avg_trade_size"]
        ftxt += f" Average trade size LKR {ats['last5']:,.0f} (5d) vs LKR {ats['last20']:,.0f} (20d)."
    (pf if fl["label"] == "accumulation" else pa if fl["label"] == "distribution" else pf).append({"text": ftxt, "factor": "liquidity", "strength": 2})
    rtxt = f"Liquidity regime: {liquidity['regime_text']}."
    if liquidity["regime"] in ("awakening", "active"):
        pf.append({"text": rtxt, "factor": "liquidity", "strength": 2})
    elif liquidity["regime"] in ("frenzy", "drying_up", "dormant"):
        pa.append({"text": rtxt, "factor": "liquidity", "strength": 2})

    # fundamentals
    if fundamentals and fundamentals.get("has_data"):
        for c in fundamentals.get("components", []):
            item = {"text": f"Fundamentals ({c['name']}): {c['reason']} → {c['score']:.0f}/100.", "factor": "fundamentals", "strength": 1}
            (pf if c["score"] >= 60 else pa if c["score"] <= 40 else pf).append(item)
        for fflag in fund_flags:
            pa.append({"text": f"Fundamental warning: {fflag}.", "factor": "fundamentals", "strength": 2})
    else:
        pa.append({"text": "No financial figures entered yet, so fundamentals are excluded from the score (weight redistributed). Add them on the Fundamentals tab.", "factor": "fundamentals", "strength": 0})
    # news
    for f in red_flags:
        pa.append({"text": f"Red-flag announcement ({f['category'].replace('_', ' ')}, {f['date']}): {f['title']}.", "factor": "news", "strength": 3})
    if news and news.get("score") is not None:
        s = news["score"]
        if s >= 58:
            pf.append({"text": f"Recent announcements lean positive (news score {s:.0f}/100; {news['detail']}).", "factor": "news", "strength": 1})
        elif s <= 42:
            pa.append({"text": f"Recent announcements lean negative (news score {s:.0f}/100; {news['detail']}).", "factor": "news", "strength": 1})
    # market
    if market and market.get("score") is not None:
        item = {"text": f"Market regime {market['regime'].replace('_', '-')} ({market['score']:.0f}/100): {market['detail']}.", "factor": "macro", "strength": 1}
        (pf if market["score"] >= 55 else pa if market["score"] <= 45 else pf).append(item)

    pf.sort(key=lambda x: -x["strength"])
    pa.sort(key=lambda x: -x["strength"])

    # ---- prices explained
    px = plan_explain(plan, setups, patterns, liquidity, atr)
    buy_prices, sell_prices, sizing = px["buy_prices"], px["sell_prices"], px["sizing"]
    scenarios, invalidation, watch = px["scenarios"], px["invalidation"], px["watch_next"]
    if not plan:
        watch.append("No bullish setup at the moment. Wait for a pullback to support in an uptrend, a confirmed breakout, or a reversal signal at support.")
    near_res = [p for p in patterns if p["status"] == "forming" and p["priority"] >= 30]
    for p in near_res[:2]:
        if p["levels"].get("breakout"):
            watch.append(f"{p['name']} forming: watch {_fmt(p['levels']['breakout'])} (invalid at {_fmt(p['levels'].get('invalidation'))}).")
    if liquidity["regime"] == "awakening":
        watch.append("Volume is awakening, so watch whether price follows through on the next sessions.")

    # ---- changes since last report
    changes = []
    if previous:
        if previous.get("verdict") != verdict:
            changes.append(f"Verdict changed {previous.get('verdict')} → {verdict}.")
        d = score - (previous.get("score") or score)
        if abs(d) >= 0.5:
            changes.append(f"Score {'+' if d > 0 else ''}{d:.1f} (from {previous.get('score'):.0f} to {score:.0f}).")
        old_ids = set(previous.get("pattern_ids") or [])
        new = [p for p in patterns if p["id"] not in old_ids and p["priority"] >= 40]
        for p in new[:5]:
            changes.append(f"New pattern: {p['name']} ({p['status']}, priority {p['priority']:.0f}).")
    else:
        changes.append("First report for this stock.")

    # ---- summary
    top_for = pf[0]["text"].split(". ")[0].rstrip(".") if pf else ""
    head = {
        "BUY": "BUY", "WATCH": "WATCH", "AVOID": "AVOID", "HOLD": "HOLD", "TRIM": "TRIM", "SELL": "SELL",
    }[verdict]
    summary = f"{head}: {name} ({symbol}) scores {score:.0f}/100 and is in {TREND_TEXT[trend['state']]} with liquidity grade {liquidity['grade']} ({liquidity['flow']['label']}). "
    if review:
        summary += f"Your position is {review['pl_pct']:+.1f}% (LKR {review['pl_lkr']:,.0f}). {review['action']} because " + "; ".join(review["reasons"]) + ". "
        if review.get("suggested_stop"):
            summary += (f"Exit next session; if you decide to keep it, use a stop at {_fmt(review['suggested_stop'])}. " if review["action"] == "SELL"
                        else f"Keep the stop at {_fmt(review['suggested_stop'])}. ")
    elif verdict == "BUY" and plan:
        summary += (f"Setup: {plan['setup_name'].lower()}. Buy with a limit at {_fmt(plan['ideal'])} (zone {_fmt(plan['buy_zone']['low'])}–{_fmt(plan['buy_zone']['high'])}, "
                    f"max {_fmt(plan['max_chase'])}), stop {_fmt(plan['stop'])}, targets "
                    + " / ".join(_fmt(t["price"]) for t in plan["targets"]) + f", reward:risk {plan['reward_risk']:.1f} after fees.")
    elif verdict == "WATCH":
        failed = [c["label"] for c in checklist if not c["passed"]]
        summary += ("Not a buy yet. Unmet conditions: " + "; ".join(failed) + ". ") if failed else ""
        if plan:
            summary += (f"Best setup: {plan['setup_name'].lower()}" + (f", trigger {_fmt(plan['trigger'])}" if plan.get("trigger") else "")
                        + f"; planned entry {_fmt(plan['ideal'])}, stop {_fmt(plan['stop'])}.")
            if not plan.get("actionable"):
                t1 = plan["targets"][0] if plan["targets"] else None
                summary += (f" That plan is not actionable yet: reward:risk after fees is {plan['reward_risk']:.2f} "
                            f"(needs ≥ {settings()['signals']['min_reward_risk']}): the stop is {abs(plan['stop_net_pct']):.1f}% below the entry"
                            + (f" but T1 is only {t1['net_pct']:.1f}% above it (both net of costs)." if t1 else " and there is no target above it.")
                            )
    elif verdict == "AVOID":
        why = [f["title"] for f in red_flags] or (["illiquid (grade E)"] if liquidity["grade"] == "E" else [f"the trend is {trend['state'].replace('_', ' ')} with no reversal setup"])
        summary += "Avoid for now: " + "; ".join(why) + "."
    if top_for and verdict in ("BUY", "WATCH"):
        summary += f" Strongest point: {top_for}."

    return {"summary": summary, "reasons_for": pf, "reasons_against": pa, "buy_prices": buy_prices,
            "sell_prices": sell_prices, "sizing": sizing, "scenarios": scenarios, "invalidation": invalidation,
            "watch_next": watch, "changes": changes,
            "score_breakdown": [{"label": c["label"], "score": c["score"], "weight": c["effective_weight"],
                                 "contribution": c["contribution"], "available": c["available"]} for c in comps]}


def plan_explain(plan: dict | None, setups: list[dict], patterns: list[dict], liquidity: dict, atr: float) -> dict[str, Any]:
    """Buy/sell price tables, sizing, scenarios and invalidation for one plan (shared by both engines)."""
    buy_prices, sell_prices, sizing, scenarios, invalidation, watch = [], [], None, [], [], []
    if plan:
        zl, zh, ideal = plan["buy_zone"]["low"], plan["buy_zone"]["high"], plan["ideal"]
        st = next((s for s in setups if s["pattern_name"] == plan["setup_name"]), setups[0] if setups else {"why": plan.get("why") or plan["setup_name"]})
        buy_prices = [
            {"label": "Buy zone", "price": f"{_fmt(zl)} – {_fmt(zh)}",
             "how": (f"Setup: {st['why']}. Lower bound {_fmt(zl)} = "
                     + ("the breakout level, where a retest should hold" if plan["setup"] == "breakout"
                        else "the trigger (breakout level + 0.3 ATR)" if plan["setup"] == "pending_breakout"
                        else "the bottom of the support zone" if plan["setup"] == "pullback"
                        else "the bottom of the confluence zone (" + " + ".join((plan.get("zone_sources") or [])[:3]) + ")" if plan["setup"] in ("fib_pullback", "zone2")
                        else "the flipped level, where a retest should hold" if plan["setup"] == "breakout_retest"
                        else "the broken resistance, bought on a retest after the trigger close" if plan["setup"] == "resistance_break"
                        else "the base support" if plan["setup"] == "base_retest"
                        else "the broken trendline" if plan["setup"] == "trendline_break"
                        else "the order-block low (distal line); the limit sits at its top, the proximal line" if plan["setup"] in ("order_block", "weekly_ob")
                        else "the bottom of the fair value gap; the limit sits at its middle (consequent encroachment)" if plan["setup"] == "fvg"
                        else "the broken structure level, bought on the retest" if plan["setup"] == "bos_retest" else "the reclaimed level")
                     + f"; upper bound {_fmt(zh)} = lower bound + up to 0.5 ATR (ATR {_fmt(atr)}). Both are rounded to the CSE tick ({tick_size(zl):.2f}).")},
            {"label": "Ideal limit", "price": _fmt(ideal), "how": f"Placed at the {plan['ideal_note']}."},
            {"label": "Max chase", "price": _fmt(plan["max_chase"]),
             "how": (f"The highest entry that still gives reward:risk ≥ {settings()['signals']['min_reward_risk']} after "
                     f"{plan['cost_per_side_pct']:.2f}% costs per side, assuming half is sold at T1 and half at T2 with the stop at {_fmt(plan['stop'])}. Above this, don't buy; wait for a pullback.")},
        ]
        if plan.get("trigger"):
            buy_prices.insert(0, {"label": "Trigger", "price": _fmt(plan["trigger"]),
                                  "how": "Only buy after a daily CLOSE above this level on volume at least 1.5× the 20-day average "
                                         f"(≈ {liquidity['adv_volume_20'] * 1.5:,.0f} shares). Until then the pattern is unconfirmed."})
        sell_prices.append({"label": "Stop-loss", "price": _fmt(plan["stop"]), "pct": plan["stop_pct"], "net_pct": plan["stop_net_pct"],
                            "how": "; ".join(plan["stop_steps"]) + ". Exit on a daily close below it."})
        for t in plan["targets"]:
            sell_prices.append({"label": t["label"], "price": _fmt(t["price"]), "pct": t["pct"], "net_pct": t["net_pct"],
                                "how": f"{t['source'][0].upper() + t['source'][1:]}. Gross {t['pct']:+.1f}%, net of costs {t['net_pct']:+.1f}%."})
        sell_prices.append({"label": "Reward:risk", "price": f"{plan['reward_risk']:.2f}", "pct": None, "net_pct": None,
                            "how": "Net reward (half at T1, half at T2) ÷ net risk to the stop, both after brokerage and slippage on each side."})
        sz = plan["size"]
        sizing = (f"Suggested quantity {sz['qty']:,} shares (≈ LKR {sz['value']:,.0f}). Risking {sz['risk_pct']}% of LKR {sz['capital']:,.0f} "
                  f"allows {sz['qty_by_risk']:,} shares, the max position size allows {sz['qty_by_cap']:,}, and liquidity "
                  f"({settings()['risk']['max_days_to_exit']} days at {int(settings()['risk']['adv_participation'] * 100)}% of the "
                  f"{liquidity['adv_volume_20']:,.0f}-share average daily volume) allows {sz['qty_by_liquidity']:,}. The binding limit is the {sz['binding']}. "
                  + (f"You could exit the full position in about {sz['days_to_exit']:.1f} sessions. " if sz["days_to_exit"] is not None else "")
                  + f"Loss if stopped out ≈ LKR {sz['risk_lkr']:,.0f}.")
        t1 = plan["targets"][0] if plan["targets"] else None
        t2 = plan["targets"][1] if len(plan["targets"]) > 1 else None
        scenarios = [
            {"name": "Bull", "text": (f"Holds above {_fmt(zl)} and clears {_fmt(t1['price']) if t1 else 'resistance'} on rising volume → sell half at T1, "
                                      f"trail the rest toward {_fmt(t2['price']) if t2 else 'higher'} ({t2['net_pct']:+.1f}% net)." if t2 else
                                      f"Holds the zone and reaches T1 {_fmt(t1['price']) if t1 else ''}.")},
            {"name": "Base", "text": f"Chops between {_fmt(plan['stop'])} and {_fmt(t1['price']) if t1 else 'resistance'}. If T1 isn't reached within {plan['time_stop_weeks']} weeks, exit (time stop) and free the capital."},
            {"name": "Bear", "text": f"A daily close below {_fmt(plan['stop'])} invalidates the setup: exit for about {plan['stop_net_pct']:+.1f}% net (≈ LKR {plan['size']['risk_lkr']:,.0f})."},
        ]
        invalidation = [f"Daily close below the stop {_fmt(plan['stop'])}."]
        pat = next((p for p in patterns if p["id"] == st.get("pattern_id")), None)
        if pat and pat["levels"].get("invalidation"):
            invalidation.append(f"{pat['name']} fails: close back through {_fmt(pat['levels']['invalidation'])}.")
        invalidation += ["A rights issue, director selling or another red-flag announcement.",
                         "Liquidity regime turns to 'drying up' before T1, or the money-flow score falls below 38 (distribution).",
                         "Market regime flips to risk-off (ASPI below a falling 50-day EMA)."]
        if plan.get("trigger"):
            watch.append(f"Trigger: a close above {_fmt(plan['trigger'])} on ≥ 1.5× average volume.")
        mc = plan["max_chase"]
        if mc < zl:
            # even the bottom of the zone is above the highest price that still gives the required reward:risk
            watch.append(f"No price inside the buy zone {_fmt(zl)}–{_fmt(zh)} gives reward:risk ≥ {settings()['signals']['min_reward_risk']} with this stop and these targets "
                         f"(at the {_fmt(ideal)} limit it is {plan['reward_risk']:.2f}); it would take an entry at {_fmt(mc)} or lower, below the zone. "
                         "Treat this plan as unattractive unless price gets there.")
        elif ideal > mc:
            watch.append(f"Preferred entry: a limit order at {_fmt(ideal)} (zone {_fmt(zl)}–{_fmt(zh)}), but reward:risk is only {plan['reward_risk']:.2f} there; "
                         f"it reaches the required {settings()['signals']['min_reward_risk']} at {_fmt(mc)} or lower.")
        else:
            watch.append(f"Preferred entry: a limit order at {_fmt(ideal)} (zone {_fmt(zl)}–{_fmt(zh)}). Don't pay above {_fmt(mc)}.")
    return {"buy_prices": buy_prices, "sell_prices": sell_prices, "sizing": sizing, "scenarios": scenarios,
            "invalidation": invalidation, "watch_next": watch}


def split_engines(rec: dict | None) -> tuple[dict, dict | None]:
    """(original, analyst) from a stored recommendation; rows saved before the engine split have no analyst part."""
    rec = rec or {}
    if "original" in rec:
        return rec["original"], rec.get("analyst")
    return rec, None


# What the user sees: engine ids stay "original" / "analyst" / "smc" (stored analyses, saved preferences, the API)
ENGINE_TAG = {"original": "CLS", "analyst": "LVL", "smc": "SMC"}
ENGINE_NAME = {"original": "Classic", "analyst": "Levels", "smc": "SMC"}


def engine_recs(rec: dict | None) -> list[tuple[str, dict | None]]:
    """[(tag, engine result)] for the three engines; older rows lack the later engines."""
    ro, ra = split_engines(rec)
    return [(ENGINE_TAG["original"], ro), (ENGINE_TAG["analyst"], ra), (ENGINE_TAG["smc"], (rec or {}).get("smc"))]


def plan_summary(plan: dict | None) -> dict | None:
    if not plan:
        return None
    return {"ideal": plan["ideal"], "stop": plan["stop"], "actionable": plan.get("actionable", False), "zone": plan["buy_zone"],
            "trigger": plan.get("trigger"), "t1": plan["targets"][0]["price"] if plan["targets"] else None,
            "label": plan.get("entry_label") or plan.get("setup_name"), "rr": plan.get("reward_risk")}
