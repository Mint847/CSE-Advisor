"""Momentum, extension, volume-behaviour, volatility and drawdown signals.

These are the checks the analyst writes in words:
- "RSI went past 90, then a bearish divergence; price is way over-extended from all major MAs"
- "RSI moved above 50" / "RSI entering oversold levels, 19 can be a potential bottom"
- "volumes exceed 30mn for the third consecutive day" / "selling volumes are in a rising trend"
- "daily volumes were in a declining trend" / "lack of a proper rising volume trend"
- "highly volatile, better to avoid trading till we see a clear directional change"
- "dropped 40% from its peak, so it has had the cooling-off period needed for the next move"
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from .model import tkey


def _f(x: Any) -> float | None:
    try:
        v = float(x)
        return v if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def block_trade_days(df: pd.DataFrame, lookback: int = 60) -> list[str]:
    """Days that look like crossings / negotiated block trades: huge volume, almost no price range.
    They are excluded from accumulation/distribution reads."""
    out = []
    v, avg = df["volume"].to_numpy(float), df["volume"].rolling(20, min_periods=5).median().shift().to_numpy(float)
    rng = ((df["high"] - df["low"]) / df["close"]).to_numpy(float)
    chg = df["close"].pct_change().abs().to_numpy(float)
    for j in range(max(1, len(df) - lookback), len(df)):
        if np.isfinite(avg[j]) and avg[j] > 0 and v[j] >= 8 * avg[j] and rng[j] <= 0.015 and (not np.isfinite(chg[j]) or chg[j] <= 0.015):
            out.append(tkey(df.index[j]))
    return out


def compute_signals(df: pd.DataFrame, intraday_vap: dict[str, Any] | None = None) -> dict[str, Any]:
    n = len(df)
    last = df.iloc[-1]
    c = df["close"].to_numpy(float)
    vol = df["volume"].to_numpy(float)
    atr = _f(last.get("atr")) or float((df["high"] - df["low"]).tail(14).mean())
    close = float(c[-1])
    rsi = df["rsi"].to_numpy(float) if "rsi" in df else np.full(n, np.nan)
    rsi_ma = df["rsi_ma"].to_numpy(float) if "rsi_ma" in df else np.full(n, np.nan)
    out: dict[str, Any] = {"notes_for": [], "notes_against": []}

    # ---- extension / exhaustion
    e21 = _f(last.get("ema21")) or _f(last.get("ema20"))
    r_now = _f(rsi[-1])
    r_max10 = float(np.nanmax(rsi[-10:])) if np.isfinite(rsi[-10:]).any() else None
    ext = {"pct_above_ema21": None, "atr_above_ema21": None, "rsi": r_now, "flag": False, "extreme": False}
    if e21:
        ext["pct_above_ema21"] = round((close / e21 - 1) * 100, 1)
        ext["atr_above_ema21"] = round((close - e21) / atr, 2) if atr else None
        r_max15 = float(np.nanmax(rsi[-15:])) if np.isfinite(rsi[-15:]).any() else 0.0
        ext["rsi_max15"] = round(r_max15, 1)
        ext["extreme"] = bool((r_now or 0) >= 90 or r_max15 >= 90 or ext["pct_above_ema21"] >= 40)
        ext["flag"] = bool((ext["atr_above_ema21"] or 0) >= 3 or ext["pct_above_ema21"] >= 25 or (r_now or 0) >= 80
                           or (ext["extreme"] and ext["pct_above_ema21"] >= 12))
    if ext["flag"]:
        out["notes_against"].append(
            f"Over-extended: price is {ext['pct_above_ema21']:+.0f}% ({ext['atr_above_ema21']:.1f} ATR) from the 21-day EMA"
            + (f", RSI {r_now:.0f}" if r_now else "")
            + (f" after peaking at {ext['rsi_max15']:.0f}" if ext.get("rsi_max15", 0) >= 85 else "")
            + ". Don't chase; a pullback toward the first Fibonacci supports is the usual next step.")
    out["extension"] = ext

    # ---- momentum confirmation
    mom = {"rsi": r_now, "rsi_ma": _f(rsi_ma[-1]), "cross_above_50": None, "cross_below_50": None,
           "above_rsi_ma": bool(r_now is not None and _f(rsi_ma[-1]) is not None and r_now > rsi_ma[-1]),
           "oversold_turn": False, "overbought_turn": False}
    for j in range(max(1, n - 5), n):
        if np.isfinite(rsi[j]) and np.isfinite(rsi[j - 1]):
            if rsi[j - 1] < 50 <= rsi[j]:
                mom["cross_above_50"] = tkey(df.index[j])
            if rsi[j - 1] >= 50 > rsi[j]:
                mom["cross_below_50"] = tkey(df.index[j])
    r_min10 = float(np.nanmin(rsi[-10:])) if np.isfinite(rsi[-10:]).any() else None
    if r_min10 is not None and r_now is not None and r_min10 < 32 and r_now >= r_min10 + 5:
        mom["oversold_turn"] = True
        mom["rsi_min10"] = round(r_min10, 1)
        out["notes_for"].append(f"RSI dipped to {r_min10:.0f} (oversold) and has turned up to {r_now:.0f}: selling is exhausting.")
    if r_max10 is not None and r_now is not None and r_max10 > 78 and r_now <= r_max10 - 8:
        mom["overbought_turn"] = True
        out["notes_against"].append(f"RSI peaked at {r_max10:.0f} and has rolled over to {r_now:.0f}: momentum is fading.")
    if mom["cross_above_50"]:
        out["notes_for"].append(f"RSI crossed back above 50 on {mom['cross_above_50']} (momentum turning positive).")
    if mom["cross_below_50"]:
        out["notes_against"].append(f"RSI fell below 50 on {mom['cross_below_50']} (momentum turning negative).")
    out["momentum"] = mom

    # ---- volume behaviour
    blocks = block_trade_days(df)
    bset = set(blocks)
    avg20 = df["volume"].rolling(20, min_periods=5).mean().shift().to_numpy(float)
    streak, j = 0, n - 1
    while j > 0 and np.isfinite(avg20[j]) and avg20[j] > 0 and vol[j] >= 2 * avg20[j] and tkey(df.index[j]) not in bset:
        streak += 1
        j -= 1
    up = np.r_[False, c[1:] > c[:-1]]
    dn = np.r_[False, c[1:] < c[:-1]]
    dv_recent = vol[-5:][dn[-5:]].sum() if n >= 10 else 0.0
    dv_prior = vol[-10:-5][dn[-10:-5]].sum() if n >= 10 else 0.0
    down_rising = bool(n >= 10 and dn[-5:].sum() >= 2 and dv_recent > 1.5 * max(dv_prior, 1e-9) and dv_recent > 0)
    v5 = pd.Series(vol).rolling(5, min_periods=3).mean().to_numpy(float)
    trend_val = None
    if n >= 25 and np.isfinite(v5[-20:]).all() and (v5[-20:] > 0).all():
        y = np.log(v5[-20:])
        trend_val = float(np.polyfit(np.arange(20), y, 1)[0] * 20)
    vtrend = "rising" if trend_val is not None and trend_val > 0.35 else "declining" if trend_val is not None and trend_val < -0.35 else "flat"
    from .fibonacci import robust_extremes
    hi_rob = robust_extremes(df)[0]
    hi_i = int(np.nanargmax(hi_rob))
    since_peak = vol[hi_i + 1:]
    highest_since_peak = bool(n - 1 > hi_i + 3 and len(since_peak) and vol[-1] >= since_peak.max() and vol[-1] > 0)
    volb = {"high_volume_streak": streak, "down_volume_rising": down_rising, "volume_trend": vtrend,
            "volume_trend_log_change_20d": round(trend_val, 2) if trend_val is not None else None,
            "highest_since_peak": highest_since_peak, "peak_date": tkey(df.index[hi_i]), "block_trade_days": blocks}
    if streak >= 3 and c[-1] >= c[-1 - streak]:
        out["notes_for"].append(f"Volume has been at least 2× the 20-day average for {streak} sessions in a row while price held up: accumulation.")
    if down_rising:
        out["notes_against"].append("Selling volume is rising: down-day volume over the last 5 sessions is over 1.5× the 5 before (distribution).")
    if vtrend == "declining" and close < float(df["close"].iloc[-21]) if n > 21 else False:
        out["notes_against"].append("Volume is in a declining trend: there is little buying interest behind this move.")
    if vtrend == "rising" and up[-5:].sum() >= 3:
        out["notes_for"].append("Volume is in a rising trend with mostly up days: interest is building.")
    if highest_since_peak:
        out["notes_for" if c[-1] >= c[-2] else "notes_against"].append(
            f"Today's volume is the highest since the {volb['peak_date']} peak, so a large group of buyers and sellers is active at this price.")
    if blocks:
        out["notes_against"].append(f"Probable crossing / block-trade days ({', '.join(blocks[-3:])}) are ignored in the volume reads.")
    out["volume"] = volb

    # ---- volatility guard
    tr = (df["high"] - df["low"]).to_numpy(float)
    atr_prev = df["atr"].shift().to_numpy(float) if "atr" in df else np.full(n, np.nan)
    pct = df["close"].pct_change().to_numpy(float)
    reasons, notes = [], []
    hi_, lo_ = df["high"].to_numpy(float), df["low"].to_numpy(float)
    big = []
    for j in range(max(1, n - 5), n):
        if not (np.isfinite(atr_prev[j]) and atr_prev[j] > 0 and tr[j] > 0):
            continue
        mult = tr[j] / atr_prev[j]
        upper_w = (hi_[j] - max(c[j], c[j - 1])) / tr[j]
        lower_w = (min(c[j], c[j - 1]) - lo_[j]) / tr[j]
        if mult >= 3:
            big.append((j, np.sign(c[j] - c[j - 1])))
        recent = j >= n - 3
        if recent and mult >= 3 and max(upper_w, lower_w) >= 0.5:
            reasons.append(f"{tkey(df.index[j])}: a whipsaw session, range {tr[j]:.2f} = {mult:.1f}× normal with a "
                           f"{'long upper' if upper_w >= lower_w else 'long lower'} wick (price breakers / panic)")
        elif recent and np.isfinite(pct[j]) and abs(pct[j]) >= 0.2:
            reasons.append(f"{tkey(df.index[j])}: price moved {pct[j] * 100:+.0f}% in one session")
    if len({s for _, s in big if s}) == 2:
        reasons.append("violent swings in both directions within 5 sessions")
    atr_pct = (df["atr"] / df["close"]).to_numpy(float) if "atr" in df else None
    if atr_pct is not None and n > 60:
        med = float(np.nanmedian(atr_pct[-120:]))
        if med > 0 and atr_pct[-1] >= 2 * med:
            notes.append(f"volatility (ATR) is {atr_pct[-1] / med:.1f}× its 6-month median, so use wider stops and smaller size")
    out["volatility"] = {"guard": bool(reasons), "reasons": reasons, "notes": notes}
    for t in notes:
        out["notes_against"].append(t[0].upper() + t[1:] + ".")
    if reasons:
        out["notes_against"].append("Highly volatile: " + "; ".join(reasons) + ". Better to wait for a clear direction before trading.")

    # ---- drawdown from peak
    peak = float(hi_rob[hi_i])
    dd = (close / peak - 1) * 100
    out["drawdown"] = {"peak": round(peak, 2), "peak_date": tkey(df.index[hi_i]), "pct": round(dd, 1),
                       "cooling_off": bool(dd <= -40)}
    if dd <= -40:
        out["notes_for"].append(f"Price is {abs(dd):.0f}% below its {peak:.2f} peak ({out['drawdown']['peak_date']}); a fall of this size often completes the cooling-off before the next move.")

    # ---- today's volume at price (intraday trades)
    if intraday_vap and intraday_vap.get("top"):
        t = intraday_vap["top"]
        out["today_vap"] = intraday_vap
        if t["share"] >= 0.5:
            side = "sellers" if close <= t["price"] else "buyers"
            out["notes_against" if side == "sellers" else "notes_for"].append(
                f"{t['share'] * 100:.0f}% of today's volume traded at {t['price']:.2f}: {side} are concentrated there, "
                + ("and buyers need to absorb that supply before the next resistance." if side == "sellers" else "so it is a demand level to watch on pullbacks."))
    return out


def intraday_volume_at_price(trades: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Group today's individual trades (cse.lk period=1) by price."""
    if not trades:
        return None
    by: dict[float, float] = {}
    count: dict[float, int] = {}
    for t in trades:
        p, q = t.get("p"), t.get("q")
        if p is None or not q:
            continue
        by[float(p)] = by.get(float(p), 0) + float(q)
        count[float(p)] = count.get(float(p), 0) + 1
    tot = sum(by.values())
    if tot <= 0:
        return None
    rows = sorted(({"price": p, "volume": v, "trades": count[p], "share": round(v / tot, 3)} for p, v in by.items()), key=lambda r: r["price"])
    top = max(rows, key=lambda r: r["volume"])
    return {"total_volume": tot, "rows": rows, "top": top}
