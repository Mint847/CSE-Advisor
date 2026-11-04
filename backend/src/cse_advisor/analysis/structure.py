"""Market structure: stand-alone trendlines, moving-average events and swing labels.

- Descending trendline from the major high through later lower highs (dynamic resistance);
  ascending trendline from the latest major low through higher lows (dynamic support).
- MA events: golden/death cross (EMA50 vs EMA200), rejection at / reclaim of the 200-day average.
- Swing labels HH / HL / LH / LL on the latest confirmed pivots.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .model import Pattern, round_price, tkey, tfuture
from .pivots import Pivot

BREAK_ATR = 0.3


def _upper_hull_line(anchor: Pivot, later: list[Pivot]) -> tuple[float, list[Pivot]] | None:
    """Line from anchor high through the later pivot that keeps every other later high below it."""
    best_m, best = None, None
    for p in later:
        if p.idx <= anchor.idx:
            continue
        m = (p.price - anchor.price) / (p.idx - anchor.idx)
        if best_m is None or m > best_m:
            best_m, best = m, p
    if best_m is None or best_m >= 0:
        return None
    return best_m, [anchor, best]


def _lower_hull_line(anchor: Pivot, later: list[Pivot]) -> tuple[float, list[Pivot]] | None:
    best_m, best = None, None
    for p in later:
        if p.idx <= anchor.idx:
            continue
        m = (p.price - anchor.price) / (p.idx - anchor.idx)
        if best_m is None or m < best_m:
            best_m, best = m, p
    if best_m is None or best_m <= 0:
        return None
    return best_m, [anchor, best]


def trendlines(df: pd.DataFrame, pivots: list[Pivot], lookback: int = 250) -> list[Pattern]:
    n = len(df)
    if n < 30:
        return []
    c = df["close"].to_numpy(float)
    atr = df["atr"].bfill().fillna((df["high"] - df["low"]).mean()).to_numpy(float)
    dates = [tkey(x) for x in df.index]
    t_end = tfuture(df.index, 10)
    conf = [p for p in pivots if p.confirmed and p.idx >= n - lookback]
    out: list[Pattern] = []

    # descending resistance from the highest high in the window
    highs = [p for p in conf if p.kind == "H"]
    if len(highs) >= 2:
        top = max(highs, key=lambda p: p.price)
        res = _upper_hull_line(top, [p for p in highs if p.idx > top.idx])
        if res:
            m, pts = res
            line = lambda x: top.price + m * (x - top.idx)  # noqa: E731
            touches = [p for p in highs if p.idx >= top.idx and abs(p.price - line(p.idx)) <= 0.4 * atr[p.idx]]
            last_touch = max(p.idx for p in touches)
            brk = next((j for j in range(last_touch + 1, n) if c[j] > line(j) + BREAK_ATR * atr[j]), None)
            now = line(n - 1)
            status = "confirmed" if brk is not None else "forming"
            draw_to = brk if brk is not None else n - 1
            expl = (f"Descending trendline from the {top.price:.2f} high ({dates[top.idx]}) through {len(touches)} lower highs; "
                    f"it is at {now:.2f} today. ")
            expl += (f"Price broke above it on {dates[brk]} (close {c[brk]:.2f}), a sign the downtrend is losing control."
                     if brk is not None else
                     f"It acts as dynamic resistance: expect selling pressure near {now:.2f}; a close above {now + BREAK_ATR * atr[-1]:.2f} breaks it.")
            out.append(Pattern(
                type="trendline_resistance", direction="bullish" if brk is not None else "bearish", status=status,
                start=dates[top.idx], end=dates[draw_to], end_index=brk if brk is not None else n - 1,
                anchors=[{"time": dates[p.idx], "price": round_price(p.price)} for p in touches],
                lines=[{"from": {"time": dates[top.idx], "price": round_price(top.price)},
                        "to": {"time": dates[draw_to] if brk is not None else t_end,
                               "price": round_price(line(draw_to) if brk is not None else line(n - 1 + 10))},
                        "role": "trendline", "style": "solid", "label": f"Trendline {now:.2f}"}],
                markers=[{"time": dates[brk], "position": "belowBar", "shape": "arrowUp", "text": "TL break"}] if brk is not None else [],
                levels={"level": round_price(now), "breakout": round_price(now), "slope_per_bar": round(float(m), 4)},
                confidence=min(0.9, 0.5 + 0.1 * len(touches)), explanation=expl,
                context={"touches": len(touches), "broken_index": brk, "volume_confirmed": bool(brk is not None and df["rel_vol"].iloc[brk] >= 1.5)}))

    # ascending support from the lowest low after the window's high (or the window low)
    lows = [p for p in conf if p.kind == "L"]
    if len(lows) >= 2:
        recent = [p for p in lows if p.idx >= n - 120] or lows
        base = min(recent, key=lambda p: p.price)
        res = _lower_hull_line(base, [p for p in lows if p.idx > base.idx])
        if res:
            m, pts = res
            line = lambda x: base.price + m * (x - base.idx)  # noqa: E731
            touches = [p for p in lows if p.idx >= base.idx and abs(p.price - line(p.idx)) <= 0.4 * atr[p.idx]]
            last_touch = max(p.idx for p in touches)
            brk = next((j for j in range(last_touch + 1, n) if c[j] < line(j) - BREAK_ATR * atr[j]), None)
            now = line(n - 1)
            draw_to = brk if brk is not None else n - 1
            expl = (f"Rising trendline from the {base.price:.2f} low ({dates[base.idx]}) through {len(touches)} higher lows; "
                    f"it is at {now:.2f} today. ")
            expl += (f"Price closed below it on {dates[brk]}: the higher-low structure is broken." if brk is not None
                     else f"Dynamic support: buyers have defended it so far; a close below {now - BREAK_ATR * atr[-1]:.2f} breaks it.")
            out.append(Pattern(
                type="trendline_support", direction="bearish" if brk is not None else "bullish",
                status="confirmed" if brk is not None else "forming",
                start=dates[base.idx], end=dates[draw_to], end_index=brk if brk is not None else n - 1,
                anchors=[{"time": dates[p.idx], "price": round_price(p.price)} for p in touches],
                lines=[{"from": {"time": dates[base.idx], "price": round_price(base.price)},
                        "to": {"time": dates[draw_to] if brk is not None else t_end,
                               "price": round_price(line(draw_to) if brk is not None else line(n - 1 + 10))},
                        "role": "trendline", "style": "solid", "label": f"Trendline {now:.2f}"}],
                markers=[{"time": dates[brk], "position": "aboveBar", "shape": "arrowDown", "text": "TL break"}] if brk is not None else [],
                levels={"level": round_price(now), "slope_per_bar": round(float(m), 4)},
                confidence=min(0.9, 0.5 + 0.1 * len(touches)), explanation=expl,
                context={"touches": len(touches), "broken_index": brk}))
    return out


def swing_labels(df: pd.DataFrame, pivots: list[Pivot], count: int = 6) -> Pattern | None:
    conf = [p for p in pivots if p.confirmed]
    if len(conf) < 3:
        return None
    dates = [tkey(x) for x in df.index]
    labels = []
    last_h = last_l = None
    for p in conf:
        if p.kind == "H":
            lab = "H" if last_h is None else ("HH" if p.price > last_h else "LH")
            last_h = p.price
        else:
            lab = "L" if last_l is None else ("HL" if p.price > last_l else "LL")
            last_l = p.price
        labels.append((p, lab))
    labels = labels[-count:]
    recent = [lab for _, lab in labels]
    bull = sum(1 for x in recent[-4:] if x in ("HH", "HL"))
    bear = sum(1 for x in recent[-4:] if x in ("LH", "LL"))
    expl = "Latest swings: " + ", ".join(f"{lab} {p.price:.2f} ({dates[p.idx]})" for p, lab in labels) + ". "
    if recent and recent[-1] == "HL":
        expl += "The latest low is a higher low: buyers stepped in earlier than last time."
    elif recent and recent[-1] == "LH":
        expl += "The latest high is a lower high: sellers are active at lower prices."
    return Pattern(type="swing_structure", direction="bullish" if bull > bear else "bearish" if bear > bull else "neutral",
                   status="confirmed", start=dates[labels[0][0].idx], end=dates[labels[-1][0].idx], end_index=len(df) - 1,
                   anchors=[{"time": dates[p.idx], "price": round_price(p.price), "label": lab} for p, lab in labels],
                   confidence=0.6, explanation=expl, context={"labels": recent})


def ma_events(df: pd.DataFrame, lookback: int = 60) -> dict[str, Any]:
    """Golden/death crosses and 200-day average rejections/reclaims (on EMA200 and SMA200)."""
    n = len(df)
    dates = [tkey(x) for x in df.index]
    c, h = df["close"].to_numpy(float), df["high"].to_numpy(float)
    out: dict[str, Any] = {"golden_cross": None, "death_cross": None, "rejection_200": None, "reclaim_200": None,
                           "lost_200": None, "position": {}}
    e50, e200 = df.get("ema50"), df.get("ema200")
    if e50 is not None and e200 is not None:
        d = (e50 - e200).to_numpy(float)
        for j in range(max(1, n - lookback), n):
            if np.isfinite(d[j]) and np.isfinite(d[j - 1]):
                if d[j - 1] <= 0 < d[j]:
                    out["golden_cross"] = dates[j]
                elif d[j - 1] >= 0 > d[j]:
                    out["death_cross"] = dates[j]
    ma_col = "sma200" if "sma200" in df and df["sma200"].notna().iloc[-1] else "ema200"
    ma = df[ma_col].to_numpy(float) if ma_col in df else None
    if ma is not None and np.isfinite(ma[-1]):
        out["ma200_name"] = "200-day SMA" if ma_col == "sma200" else "200-day EMA"
        out["ma200"] = round(float(ma[-1]), 3)
        for j in range(max(5, n - lookback), n):
            if not np.isfinite(ma[j]):
                continue
            below_before = all(np.isfinite(ma[k]) and c[k] < ma[k] for k in range(j - 5, j))
            if below_before and h[j] >= ma[j] * 0.995 and c[j] < ma[j]:
                out["rejection_200"] = {"date": dates[j], "price": round(float(ma[j]), 2)}
            if below_before and c[j] > ma[j]:
                out["reclaim_200"] = {"date": dates[j], "price": round(float(ma[j]), 2)}
            above_before = all(np.isfinite(ma[k]) and c[k] > ma[k] for k in range(j - 5, j))
            if above_before and c[j] < ma[j]:
                out["lost_200"] = {"date": dates[j], "price": round(float(ma[j]), 2)}
    for col, label in (("ema21", "EMA21"), ("ema50", "EMA50"), ("ema200", "EMA200"), ("sma200", "SMA200")):
        if col in df and pd.notna(df[col].iloc[-1]):
            out["position"][label] = {"value": round(float(df[col].iloc[-1]), 3), "above": bool(c[-1] > df[col].iloc[-1])}
    pos = out["position"]
    out["below_all"] = bool(pos) and all(not v["above"] for v in pos.values())
    out["above_all"] = bool(pos) and all(v["above"] for v in pos.values())
    return out
