"""Smart-money concepts (SMC / ICT) on end-of-day bars, for the SMC engine.

Order blocks and premium / discount follow the bootcamp examples:
- a bullish order block (OB) is the run of down candles just before the up move that broke
  structure, from the top of that run to its lowest wick (bearish OB: the mirror image).
  Price tends to come back to it and often only taps the top (proximal line); the stop goes
  under the bottom (distal line). A wide higher-timeframe block is refined to a smaller one.
- the dealing range is the impulse leg that broke structure; its 0.5 level is equilibrium.
  Below it is discount (where longs are taken), above it is premium.
Fair value gaps, liquidity sweeps, BOS / CHoCH, equal highs / lows and break-and-retest follow
common ICT practice. The weekly chart gives the higher-timeframe bias, daily bars give entries.
A break needs a CLOSE beyond the swing; a wick beyond it that closes back inside is a sweep."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd

from .model import Pattern, round_price, tkey, tfuture

FUTURE_BARS = 10


def _atr(df: pd.DataFrame, n: int = 14) -> np.ndarray:
    h, l, c = df["high"], df["low"], df["close"]
    prev = c.shift(1)
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean().bfill().to_numpy(float)


def candle_dirs(df: pd.DataFrame) -> np.ndarray:
    """+1 up candle, -1 down candle, 0 flat. CSE history often lacks the open, so an estimated
    open falls back to the change from the previous close."""
    o, c = df["open"].to_numpy(float), df["close"].to_numpy(float)
    prev = np.concatenate([[np.nan], c[:-1]])
    est = df["open_estimated"].fillna(False).astype(bool).to_numpy() if "open_estimated" in df else np.zeros(len(df), bool)
    body = np.where(est | ~np.isfinite(o), c - prev, c - o)
    return np.sign(np.nan_to_num(body))


def swings(h: np.ndarray, l: np.ndarray, k: int) -> list[dict[str, Any]]:
    """Fractal swing points: a high above the k bars on each side. Known only k bars later."""
    out = []
    for i in range(k, len(h) - k):
        if h[i] >= h[i - k:i + k + 1].max() and (h[i] > h[i - k:i]).all():
            out.append({"idx": i, "price": float(h[i]), "kind": "H", "known": i + k})
        if l[i] <= l[i - k:i + k + 1].min() and (l[i] < l[i - k:i]).all():
            out.append({"idx": i, "price": float(l[i]), "kind": "L", "known": i + k})
    return out


def structure(c: np.ndarray, h: np.ndarray, l: np.ndarray, sw: list[dict]) -> dict[str, Any]:
    """Walk the bars: a close beyond the latest swing is a break (BOS with the trend, CHoCH against
    it); a wick beyond it that closes back inside is a liquidity sweep."""
    known = defaultdict(list)
    for s in sw:
        known[s["known"]].append(s)
    hi = lo = None
    trend = 0
    events, sweeps = [], []
    for j in range(len(c)):
        for s in known.get(j, []):
            if s["kind"] == "H":
                hi = {**s, "swept": False}
            else:
                lo = {**s, "swept": False}
        if hi and c[j] > hi["price"]:
            events.append({"idx": j, "dir": "up", "kind": "choch" if trend == -1 else "bos", "level": hi["price"], "swing_idx": hi["idx"]})
            trend, hi = 1, None
        elif hi and h[j] > hi["price"] and not hi["swept"]:
            sweeps.append({"idx": j, "side": "buy", "level": hi["price"], "swing_idx": hi["idx"]})
            hi["swept"] = True
        if lo and c[j] < lo["price"]:
            events.append({"idx": j, "dir": "down", "kind": "choch" if trend == 1 else "bos", "level": lo["price"], "swing_idx": lo["idx"]})
            trend, lo = -1, None
        elif lo and l[j] < lo["price"] and not lo["swept"]:
            sweeps.append({"idx": j, "side": "sell", "level": lo["price"], "swing_idx": lo["idx"]})
            lo["swept"] = True
    return {"events": events, "sweeps": sweeps, "trend": trend}


def order_blocks(o: np.ndarray, c: np.ndarray, h: np.ndarray, l: np.ndarray, dirs: np.ndarray, atr: np.ndarray,
                 events: list[dict], sweeps: list[dict], fvg_list: list[dict], tf: str) -> list[dict[str, Any]]:
    n = len(c)
    out: dict[int, dict] = {}
    for ev in events:
        j, s = ev["idx"], ev["swing_idx"]
        up = ev["dir"] == "up"
        seg = l[s:j + 1] if up else h[s:j + 1]
        m = s + int(np.argmin(seg) if up else np.argmax(seg))
        want = -1 if up else 1
        # the last opposite-colour candle at (or just before) the leg's extreme, then the run before it
        e = next((x for x in range(m, max(s, m - 3) - 1, -1) if dirs[x] == want), m)
        b = e
        while b - 1 > s and dirs[b - 1] == want and e - b < 5:
            b -= 1
        if up:
            top, bottom = float(h[b:e + 1].max()), float(l[b:m + 1].min())
        else:
            top, bottom = float(h[b:m + 1].max()), float(l[b:e + 1].min())
        refined = False
        if top - bottom > 2.5 * atr[j]:
            if up:
                top, bottom = float(h[e]), float(l[e:m + 1].min())
            else:
                top, bottom = float(h[e:m + 1].max()), float(l[e])
            refined = True
        if top <= bottom:
            continue
        status, touches, first_touch, broken_at, outside = "fresh", 0, None, None, True
        for k in range(j + 1, n):
            if (up and c[k] < bottom) or (not up and c[k] > top):
                status, broken_at = "broken", k
                break
            inside = l[k] <= top if up else h[k] >= bottom
            if inside and outside:
                touches += 1
                first_touch = first_touch if first_touch is not None else k
                status = "tested"
            outside = not inside
        swept = any(sw_["side"] == ("sell" if up else "buy") and abs(sw_["idx"] - m) <= 1 for sw_ in sweeps)
        fvg = any(f["dir"] == ev["dir"] and m < f["idx"] <= j + 1 for f in fvg_list)
        prev = out.get(m)
        if prev and prev["bos_idx"] <= j:
            continue
        out[m] = {"dir": ev["dir"], "tf": tf, "b": b, "e": e, "m": m, "bos_idx": j, "bos_kind": ev["kind"], "bos_level": ev["level"],
                  "top": top, "bottom": bottom, "mean": (top + bottom) / 2, "status": status, "touches": touches,
                  "first_touch": first_touch, "broken_at": broken_at, "refined": refined, "swept": swept, "fvg": fvg,
                  "displacement_atr": float(((h[m:j + 1].max() - top) if up else (bottom - l[m:j + 1].min())) / atr[j])}
    return sorted(out.values(), key=lambda x: x["bos_idx"])


def fair_value_gaps(c: np.ndarray, h: np.ndarray, l: np.ndarray, dirs: np.ndarray, atr: np.ndarray,
                    lookback: int = 160) -> list[dict[str, Any]]:
    """3-candle imbalance: candle 1's high below candle 3's low (bullish) after a displacement candle."""
    n = len(c)
    out = []
    for i in range(max(2, n - lookback), n):
        a = atr[i - 1]
        disp = h[i - 1] - l[i - 1] >= 1.0 * a
        for up in (True, False):
            if up and not (l[i] > h[i - 2] and l[i] - h[i - 2] >= 0.25 * a and disp and dirs[i - 1] > 0):
                continue
            if not up and not (h[i] < l[i - 2] and l[i - 2] - h[i] >= 0.25 * a and disp and dirs[i - 1] < 0):
                continue
            bottom, top = (float(h[i - 2]), float(l[i])) if up else (float(h[i]), float(l[i - 2]))
            status, deepest = "open", None
            for k in range(i + 1, n):
                if (up and l[k] <= bottom) or (not up and h[k] >= top):
                    status = "filled"
                    break
                if (up and l[k] < top) or (not up and h[k] > bottom):
                    status = "partial"
                    deepest = min(deepest or top, float(l[k])) if up else max(deepest or bottom, float(h[k]))
            out.append({"dir": "up" if up else "down", "idx": i - 1, "top": top, "bottom": bottom, "ce": (top + bottom) / 2,
                        "status": status, "deepest": deepest})
    return out


def equal_levels(sw: list[dict], h: np.ndarray, l: np.ndarray, atr: np.ndarray, lookback: int = 150) -> list[dict[str, Any]]:
    """Two or more swing highs (lows) at about the same price that are still untaken: resting liquidity."""
    n = len(h)
    out = []
    for kind in ("H", "L"):
        pts = [s for s in sw if s["kind"] == kind and s["idx"] >= n - lookback]
        for a_, b_ in zip(pts, pts[1:]):
            tol = max(0.2 * atr[b_["idx"]], 0.004 * b_["price"])
            if abs(a_["price"] - b_["price"]) > tol or b_["idx"] - a_["idx"] < 3:
                continue
            lvl = max(a_["price"], b_["price"]) if kind == "H" else min(a_["price"], b_["price"])
            after = slice(b_["idx"] + 1, n)
            taken = (h[after].max(initial=-np.inf) > lvl) if kind == "H" else (l[after].min(initial=np.inf) < lvl)
            if not taken:
                q = {"kind": "EQH" if kind == "H" else "EQL", "price": float(lvl), "i1": a_["idx"], "i2": b_["idx"]}
                dup = next((i for i, x in enumerate(out) if x["kind"] == q["kind"] and abs(x["price"] - q["price"]) <= tol), None)
                if dup is None:
                    out.append(q)
                else:
                    out[dup] = {**q, "i1": out[dup]["i1"]}       # same pool seen again: keep one, spanning both
    return out


def dealing_range(c: np.ndarray, h: np.ndarray, l: np.ndarray, events: list[dict]) -> dict[str, Any] | None:
    """The impulse leg behind the latest break; 0.5 = equilibrium, OTE = 0.62-0.79 retracement."""
    if not events:
        return None
    ev = events[-1]
    j, s = ev["idx"], ev["swing_idx"]
    if ev["dir"] == "up":
        m = s + int(np.argmin(l[s:j + 1]))
        x = m + int(np.argmax(h[m:]))
        low, high, i_low, i_high = float(l[m]), float(h[x]), m, x
    else:
        m = s + int(np.argmax(h[s:j + 1]))
        x = m + int(np.argmin(l[m:]))
        high, low, i_high, i_low = float(h[m]), float(l[x]), m, x
    rng = high - low
    if rng <= 0:
        return None
    pos = (float(c[-1]) - low) / rng
    ote = (high - 0.79 * rng, high - 0.62 * rng) if ev["dir"] == "up" else (low + 0.62 * rng, low + 0.79 * rng)
    return {"dir": ev["dir"], "low": low, "high": high, "eq": (low + high) / 2, "pos": pos, "i_low": i_low, "i_high": i_high,
            "start": min(i_low, i_high), "ote_low": ote[0], "ote_high": ote[1],
            "zone": "discount" if pos < 0.5 else "premium", "in_ote": ote[0] <= float(c[-1]) <= ote[1]}


def _weekly(df: pd.DataFrame) -> pd.DataFrame:
    g = df.resample("W-FRI")
    wk = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last()}).dropna(subset=["close"])
    first_day = df.index.to_series().resample("W-FRI").first().dropna()
    wk.index = pd.DatetimeIndex(first_day.loc[wk.index].values)   # label weeks by their first trading day (matches the chart)
    wk["open_estimated"] = False
    return wk


def _htf(df: pd.DataFrame, k: int = 2, htf_df: pd.DataFrame | None = None) -> dict[str, Any]:
    """Structure, order blocks and dealing range on the higher timeframe: weekly bars built from the daily
    frame, or the frame the engine passes in (e.g. 1H bars for a 15m chart)."""
    if htf_df is None:
        wk = _weekly(df)
        hdates = [tkey(d) for d in wk.index]
    else:
        wk = htf_df[["open", "high", "low", "close"]].dropna()
        wk = wk[wk.index <= df.index[-1]]
        # label each higher-timeframe bar by the first chart bar inside it, so drawings snap onto the chart
        pos = df.index.searchsorted(wk.index)
        hdates = [tkey(df.index[i]) if i < len(df) else tkey(df.index[-1]) for i in pos]
    if len(wk) < 20:
        return {"bias": "neutral", "last": None, "range": None, "obs": [], "weeks": len(wk), "dates": hdates}
    o, c, h, l = (wk[x].to_numpy(float) for x in ("open", "close", "high", "low"))
    atr = _atr(wk)
    dirs = candle_dirs(wk)
    sw = swings(h, l, k)
    st = structure(c, h, l, sw)
    fv = fair_value_gaps(c, h, l, dirs, atr, lookback=len(wk))
    obs = order_blocks(o, c, h, l, dirs, atr, st["events"], st["sweeps"], fv, "W")
    last = st["events"][-1] if st["events"] else None
    return {"bias": "bullish" if st["trend"] == 1 else "bearish" if st["trend"] == -1 else "neutral",
            "last": last, "range": dealing_range(c, h, l, st["events"]), "obs": obs, "weeks": len(wk),
            "dates": hdates}


# ---------------------------------------------------------------------------- drawings
def _patterns(df: pd.DataFrame, st: dict, obs: list[dict], htf: dict, fvgs: list[dict], eqs: list[dict],
              rng: dict | None, close: float, atr_now: float, ltf_label: str = "daily", htf_label: str = "weekly",
              htf_short: str = "W") -> list[Pattern]:
    n = len(df)
    dates = [tkey(x) for x in df.index]
    fut = tfuture(df.index, FUTURE_BARS)
    out: list[Pattern] = []

    evs = st["events"][-6:]
    if evs:
        last = evs[-1]
        lines = [{"from": {"time": dates[e["swing_idx"]], "price": round_price(e["level"])}, "to": {"time": dates[e["idx"]], "price": round_price(e["level"])},
                  "role": "bos", "style": "dashed", "label": f"{'CHoCH' if e['kind'] == 'choch' else 'BOS'} {'↑' if e['dir'] == 'up' else '↓'}",
                  "dir": e["dir"]} for e in evs]
        txt = "; ".join(f"{'CHoCH' if e['kind'] == 'choch' else 'BOS'} {'up' if e['dir'] == 'up' else 'down'} at {e['level']:.2f} on {dates[e['idx']]}" for e in evs[-3:])
        out.append(Pattern(type="smc_structure", direction="bullish" if last["dir"] == "up" else "bearish", status="confirmed",
                           start=dates[evs[0]["swing_idx"]], end=dates[last["idx"]], end_index=last["idx"], lines=lines,
                           confidence=0.6, context={"key": "structure"},
                           explanation=(f"Market structure ({ltf_label}, close-based breaks): {txt}. The latest break is "
                                        f"{'up' if last['dir'] == 'up' else 'down'}, so {ltf_label} order flow is {'bullish' if last['dir'] == 'up' else 'bearish'}. "
                                        "BOS = break in the trend's direction; CHoCH = change of character (first break against it).")))

    def ob_pattern(ob: dict, idx_dates: list[str], wk: bool) -> Pattern:
        up = ob["dir"] == "up"
        t1 = idx_dates[ob["b"]]
        t2 = dates[ob["broken_at"]] if (not wk and ob["broken_at"] is not None) else fut
        name = (htf_label.capitalize() + " " if wk else "") + ("bullish" if up else "bearish") + " order block"
        touched = f"tested {ob['touches']}×" if ob["touches"] else "not revisited yet (fresh)"
        expl = (f"{name[0].upper() + name[1:]} {ob['bottom']:.2f}–{ob['top']:.2f}: the {'down' if up else 'up'} candles before the "
                f"{'up' if up else 'down'} move that made a {'CHoCH' if ob['bos_kind'] == 'choch' else 'BOS'} through {ob['bos_level']:.2f} "
                f"on {idx_dates[ob['bos_idx']]}. {'Refined to the last candle because the full block was wide. ' if ob['refined'] else ''}"
                f"Proximal (entry) line {(ob['top'] if up else ob['bottom']):.2f}, distal (stop) line {(ob['bottom'] if up else ob['top']):.2f}, "
                f"mean threshold {ob['mean']:.2f}; {touched}."
                + (" It swept liquidity before the move (stops taken first)." if ob["swept"] else "")
                + (" The move left a fair value gap (displacement)." if ob["fvg"] else ""))
        return Pattern(type=("weekly_" if wk else "") + ("bullish" if up else "bearish") + "_order_block",
                       direction="bullish" if up else "bearish", status="confirmed" if ob["status"] != "broken" else "failed",
                       start=t1, end=idx_dates[ob["bos_idx"]], end_index=n - 1 if wk else ob["bos_idx"],
                       zones=[{"t1": t1, "t2": t2, "low": round_price(ob["bottom"]), "high": round_price(ob["top"]),
                               "role": "order_block", "label": (f"{htf_short}-OB" if wk else "OB") + (" ↑" if up else " ↓")}],
                       levels={"proximal": round_price(ob["top"] if up else ob["bottom"]), "distal": round_price(ob["bottom"] if up else ob["top"]),
                               "mean": round_price(ob["mean"])},
                       confidence=min(0.9, 0.55 + 0.1 * ob["swept"] + 0.1 * ob["fvg"] + (0.05 if ob["status"] == "fresh" else 0)),
                       context={"key": f"ob|{ob['tf']}|{ob['m']}", "fresh": ob["status"] == "fresh", "touches": ob["touches"],
                                "swept": ob["swept"], "fvg": ob["fvg"], "htf": wk},
                       explanation=expl)

    # the key blocks: the nearest bullish block at/below price and the nearest bearish block above it
    live = [ob for ob in obs if ob["status"] != "broken" and ob["bos_idx"] >= n - 160]
    below = [ob for ob in live if ob["dir"] == "up" and ob["bottom"] <= close + 0.2 * atr_now]
    above = [ob for ob in live if ob["dir"] == "down" and ob["top"] >= close - 0.2 * atr_now]
    key = {id(max(below, key=lambda ob: ob["top"])) if below else None, id(min(above, key=lambda ob: ob["bottom"])) if above else None}
    drawn: list[dict] = []
    for d in ("up", "down"):
        side = sorted([ob for ob in live if ob["dir"] == d], key=lambda ob: (id(ob) not in key, abs(ob["mean"] - close)))[:2]
        for ob in side:
            p = ob_pattern(ob, dates, False)
            p.context["key_block"] = id(ob) in key
            out.append(p)
            drawn.append(ob)
    wdates = htf.get("dates") or []
    for d in ("up", "down"):
        side = sorted([ob for ob in htf.get("obs", []) if ob["dir"] == d and ob["status"] != "broken"
                       and abs(ob["mean"] - close) / close <= 0.35], key=lambda ob: abs(ob["mean"] - close))
        for ob in side[:1]:
            if not any(x["dir"] == d and x["bottom"] <= ob["top"] and ob["bottom"] <= x["top"] for x in drawn):
                out.append(ob_pattern(ob, wdates, True))

    open_f = [f for f in fvgs if f["status"] != "filled"]
    for d in ("up", "down"):
        side = sorted([f for f in open_f if f["dir"] == d], key=lambda f: abs(f["ce"] - close))[:3]
        for f in side:
            up = d == "up"
            out.append(Pattern(
                type="bullish_fvg" if up else "bearish_fvg", direction="bullish" if up else "bearish", status="confirmed" if f["status"] == "open" else "forming",
                start=dates[f["idx"]], end=dates[f["idx"]], end_index=f["idx"],
                zones=[{"t1": dates[f["idx"]], "t2": fut, "low": round_price(f["bottom"]), "high": round_price(f["top"]), "role": "fvg", "label": "FVG"}],
                levels={"top": round_price(f["top"]), "bottom": round_price(f["bottom"]), "ce": round_price(f["ce"])},
                confidence=0.5, context={"key": f"fvg|{f['idx']}", "partial": f["status"] == "partial"},
                explanation=(f"{'Bullish' if up else 'Bearish'} fair value gap {f['bottom']:.2f}–{f['top']:.2f} left on {dates[f['idx']]}: the "
                             f"{'up' if up else 'down'} candle moved so fast that candle 1's {'high' if up else 'low'} and candle 3's {'low' if up else 'high'} never overlapped. "
                             f"Price tends to come back to fill it; its middle (consequent encroachment) is {f['ce']:.2f}. "
                             + ("Partly filled already." if f["status"] == "partial" else "Not filled yet."))))

    for sw_ in [x for x in st["sweeps"] if x["idx"] >= n - 30][-3:]:
        sell = sw_["side"] == "sell"
        out.append(Pattern(
            type="sell_side_sweep" if sell else "buy_side_sweep", direction="bullish" if sell else "bearish", status="confirmed",
            start=dates[sw_["swing_idx"]], end=dates[sw_["idx"]], end_index=sw_["idx"],
            lines=[{"from": {"time": dates[sw_["swing_idx"]], "price": round_price(sw_["level"])}, "to": {"time": dates[sw_["idx"]], "price": round_price(sw_["level"])},
                    "role": "swept_level", "style": "dotted"}],
            markers=[{"time": dates[sw_["idx"]], "position": "belowBar" if sell else "aboveBar", "shape": "arrowUp" if sell else "arrowDown",
                      "text": "SSL sweep" if sell else "BSL sweep"}],
            levels={"level": round_price(sw_["level"])}, confidence=0.55, context={"key": f"sweep|{sw_['idx']}"},
            explanation=(f"{'Sell' if sell else 'Buy'}-side liquidity sweep on {dates[sw_['idx']]}: price traded "
                         f"{'below the swing low' if sell else 'above the swing high'} {sw_['level']:.2f} (from {dates[sw_['swing_idx']]}), "
                         f"where stop orders rest, then closed back {'above' if sell else 'below'} it. Smart money often fills "
                         f"{'buy' if sell else 'sell'} orders against those stops before a move the other way.")))

    for q in eqs[-4:]:
        hi = q["kind"] == "EQH"
        out.append(Pattern(
            type="equal_highs" if hi else "equal_lows", direction="bullish" if hi else "bearish", status="forming",
            start=dates[q["i1"]], end=dates[q["i2"]], end_index=q["i2"],
            lines=[{"from": {"time": dates[q["i1"]], "price": round_price(q["price"])}, "to": {"time": fut, "price": round_price(q["price"])},
                    "role": "liquidity", "style": "dotted", "label": f"{q['kind']} {q['price']:.2f}"}],
            levels={"level": round_price(q["price"])}, confidence=0.5, context={"key": f"eq|{q['kind']}|{q['i1']}"},
            explanation=(f"Equal {'highs' if hi else 'lows'} near {q['price']:.2f} ({dates[q['i1']]} and {dates[q['i2']]}): "
                         f"{'buy' if hi else 'sell'}-side liquidity that price is drawn to. "
                         + ("A likely target for longs." if hi else "A likely sweep target below price; don't put the stop right under it."))))

    if rng:
        up = rng["dir"] == "up"
        t0 = dates[rng["start"]]
        lines = [{"from": {"time": t0, "price": round_price(rng["high"])}, "to": {"time": fut, "price": round_price(rng["high"])}, "role": "range", "style": "solid",
                  "label": f"{'1.0' if up else '0'} range high {rng['high']:.2f}"},
                 {"from": {"time": t0, "price": round_price(rng["eq"])}, "to": {"time": fut, "price": round_price(rng["eq"])}, "role": "eq", "style": "dashed",
                  "label": f"0.5 EQUILIBRIUM {rng['eq']:.2f}"},
                 {"from": {"time": t0, "price": round_price(rng["low"])}, "to": {"time": fut, "price": round_price(rng["low"])}, "role": "range", "style": "solid",
                  "label": f"{'0' if up else '1.0'} range low {rng['low']:.2f}"}]
        where = (f"in discount ({rng['pos'] * 100:.0f}% of the range, below equilibrium)" if rng["pos"] < 0.5
                 else f"in premium ({rng['pos'] * 100:.0f}% of the range, above equilibrium)")
        out.append(Pattern(
            type="premium_discount", direction="bullish" if up else "bearish", status="confirmed",
            start=t0, end=dates[max(rng["i_low"], rng["i_high"])], end_index=n - 1, lines=lines,
            zones=[{"t1": t0, "t2": fut, "low": round_price(rng["ote_low"]), "high": round_price(rng["ote_high"]), "role": "ote", "label": "OTE"}],
            levels={"high": round_price(rng["high"]), "equilibrium": round_price(rng["eq"]), "low": round_price(rng["low"])},
            confidence=0.6, context={"key": "pd", "pos": round(rng["pos"], 3)},
            explanation=(f"Dealing range of the latest {'up' if up else 'down'} leg: {rng['low']:.2f} → {rng['high']:.2f}, equilibrium (0.5) "
                         f"{rng['eq']:.2f}. Price {close:.2f} is {where}. "
                         + ("Longs are taken in discount: wait for price to come back below equilibrium into an order block or fair value gap. "
                            if up else "In a down leg the premium half is where sellers step in; longs wait for a bullish CHoCH. ")
                         + f"Optimal trade entry (OTE, 0.62–0.79 retracement): {rng['ote_low']:.2f}–{rng['ote_high']:.2f}.")))
    return out


def _pools(sw: list[dict], eqs: list[dict], h: np.ndarray, l: np.ndarray, dates: list[str], lookback: int = 160) -> list[dict[str, Any]]:
    """Untaken swing highs / lows and equal highs / lows: where stop orders (liquidity) rest."""
    n = len(h)
    pools = []
    for s in sw:
        if s["idx"] < n - lookback:
            continue
        after = slice(s["idx"] + 1, n)
        taken = (h[after].max(initial=-np.inf) > s["price"]) if s["kind"] == "H" else (l[after].min(initial=np.inf) < s["price"])
        if not taken:
            pools.append({"price": round(s["price"], 2), "kind": "buy_stops_above" if s["kind"] == "H" else "sell_stops_below",
                          "time": dates[s["idx"]], "source": "swing"})
    for q in eqs:
        pools.append({"price": round(q["price"], 2), "kind": "buy_stops_above" if q["kind"] == "EQH" else "sell_stops_below",
                      "time": dates[q["i1"]], "source": q["kind"]})
    return pools


def analyze_smc(df: pd.DataFrame, k: int = 3, htf_df: pd.DataFrame | None = None, ltf_label: str = "daily",
                htf_label: str = "weekly", htf_short: str = "W") -> dict[str, Any]:
    """Everything the SMC engine needs, plus drawable patterns. Daily charts use weekly bars as the higher
    timeframe; intraday charts pass their higher timeframe in `htf_df` (5m->15m, 15m->1H, 1H->4H, 4H->daily)."""
    o, c, h, l = (df[x].to_numpy(float) for x in ("open", "close", "high", "low"))
    n = len(c)
    atr = df["atr"].bfill().to_numpy(float) if "atr" in df else _atr(df)
    atr = np.where(np.isfinite(atr) & (atr > 0), atr, np.nanmean(h - l))
    dirs = candle_dirs(df)
    dates = [tkey(x) for x in df.index]
    sw = swings(h, l, k)
    st = structure(c, h, l, sw)
    fv = fair_value_gaps(c, h, l, dirs, atr)
    obs = order_blocks(o, c, h, l, dirs, atr, st["events"], st["sweeps"], fv, "D")
    eqs = equal_levels(sw, h, l, atr)
    rng = dealing_range(c, h, l, st["events"])
    htf = _htf(df, htf_df=htf_df)
    close, atr_now = float(c[-1]), float(atr[-1])
    patterns = _patterns(df, st, obs, htf, fv, eqs, rng, close, atr_now, ltf_label, htf_label, htf_short)
    last = st["events"][-1] if st["events"] else None
    hl = htf.get("last")
    return {
        "events": st["events"], "sweeps": st["sweeps"], "trend": st["trend"], "obs": obs, "fvgs": fv, "eqs": eqs,
        "range": rng, "htf": htf, "patterns": patterns, "labels": {"ltf": ltf_label, "htf": htf_label, "htf_short": htf_short}, "pools": _pools(sw, eqs, h, l, dates), "dates": dates,
        "summary": {
            "bias": "bullish" if st["trend"] == 1 else "bearish" if st["trend"] == -1 else "neutral",
            "last": {"kind": last["kind"], "dir": last["dir"], "level": round(last["level"], 2), "date": dates[last["idx"]]} if last else None,
            "htf_bias": htf["bias"],
            "htf_last": {"kind": hl["kind"], "dir": hl["dir"], "level": round(hl["level"], 2), "date": htf["dates"][hl["idx"]]} if hl else None,
            "range": {kk: (round(v, 3) if isinstance(v, float) else v) for kk, v in rng.items() if kk not in ("i_low", "i_high", "start")} if rng else None,
            "htf_range": {kk: (round(v, 3) if isinstance(v, float) else v) for kk, v in htf["range"].items() if kk not in ("i_low", "i_high", "start")} if htf.get("range") else None,
        },
    }
