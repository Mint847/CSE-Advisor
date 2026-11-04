"""Rule-based chart-pattern detection on ZigZag pivots.

Every detector returns `Pattern` objects with drawing geometry (lines, zones,
markers) and key levels (breakout, target, invalidation).
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from .model import Pattern, round_price, tkey, tfuture
from .pivots import Pivot, fit_line

BREAK_ATR = 0.3          # close must clear a line by this many ATRs to count as a breakout
VOL_CONFIRM = 1.5        # relative volume needed to call a breakout volume-confirmed
# A breakout has FAILED when a close comes back through the invalidation level within this many bars of it. The old
# rule looked at every later bar up to today, so a pattern that played out years ago was stamped 'failed' as soon
# as price wandered back, and doubles / head & shoulders / flags had no failure check at all.
FAIL_WINDOW = 10


class _Ctx:
    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.o, self.h, self.l, self.c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        self.atr = df["atr"].bfill().fillna((df["high"] - df["low"]).mean()).to_numpy(float)
        self.rv = df["rel_vol"].to_numpy(float) if "rel_vol" in df else np.full(len(df), np.nan)
        self.n = len(df)
        self.dates = [tkey(d) for d in df.index]

    def t(self, i: int) -> str:
        """Date for bar i; indices beyond the last bar are projected on business days."""
        if i < self.n:
            return self.dates[max(0, i)]
        last = self.df.index[-1]
        return tfuture(self.df.index, i - self.n + 1)

    def volc(self, i: int) -> bool:
        return bool(np.isfinite(self.rv[i]) and self.rv[i] >= VOL_CONFIRM)

    def failed_after(self, bo: int, inv: float, bullish: bool) -> bool:
        """A close back through `inv` within FAIL_WINDOW bars after the breakout bar `bo`."""
        seg = self.c[bo + 1: bo + 1 + FAIL_WINDOW]
        return bool(seg.size and ((seg < inv).any() if bullish else (seg > inv).any()))

    def pt(self, i: int, price: float) -> dict:
        return {"time": self.t(i), "price": round_price(price)}


def _line(m: float, b: float):
    return lambda x: m * x + b


# --------------------------------------------------------------------------- triangles, wedges, rectangles
def _triangle_family(cx: _Ctx, piv: list[Pivot]) -> list[tuple[float, Pattern]]:
    cands: list[tuple[float, Pattern]] = []
    conf_piv = [p for p in piv if p.confirmed]
    for end in range(3, len(conf_piv)):
        for size in (6, 5, 4):
            if end - size + 1 < 0:
                continue
            w = conf_piv[end - size + 1:end + 1]
            highs, lows = [p for p in w if p.kind == "H"], [p for p in w if p.kind == "L"]
            if len(highs) < 2 or len(lows) < 2:
                continue
            i0, i1 = w[0].idx, w[-1].idx
            span = i1 - i0
            if span < 15 or span > 160:
                continue
            a = cx.atr[i1]
            mu, bu, ru = fit_line(highs)
            ml, bl, rl = fit_line(lows)
            if ru > 0.6 * a or rl > 0.6 * a:
                continue
            up, lo = _line(mu, bu), _line(ml, bl)
            w0, w1 = up(i0) - lo(i0), up(i1) - lo(i1)
            if w1 <= 0.3 * a or w0 < max(1.5 * a, 0.03 * cx.c[i1]):
                continue
            seg = slice(i0, i1 + 1)
            xs = np.arange(i0, i1 + 1)
            if (cx.c[seg] > up(xs) + 0.5 * a).any() or (cx.c[seg] < lo(xs) - 0.5 * a).any():
                continue
            su, sl = mu * span / a, ml * span / a          # drift over the pattern, in ATRs
            flat_u, flat_l = abs(su) < 1.0, abs(sl) < 1.0
            converging = w1 < 0.8 * w0
            if flat_u and sl >= 1.0 and converging:
                ptype, bias = "ascending_triangle", "bullish"
            elif su <= -1.0 and flat_l and converging:
                ptype, bias = "descending_triangle", "bearish"
            elif su <= -1.0 and sl >= 1.0:
                ptype, bias = "symmetric_triangle", "neutral"
            elif flat_u and flat_l and w1 >= 0.7 * w0:
                ptype, bias = "rectangle", "neutral"
            elif su >= 1.0 and sl >= 1.0 and converging and sl > su:
                ptype, bias = "rising_wedge", "bearish"
            elif su <= -1.0 and sl <= -1.0 and converging and su < sl:
                ptype, bias = "falling_wedge", "bullish"
            else:
                continue
            p = _resolve_breakout(cx, ptype, bias, w, up, lo, w0)
            if p is None:
                continue
            fit_quality = 1 - (ru + rl) / (1.2 * a)
            cands.append((size + fit_quality + (0.5 if p.status != "expired" else 0), p))
    return cands


def _resolve_breakout(cx: _Ctx, ptype: str, bias: str, w: list[Pivot], up, lo, height: float) -> Pattern | None:
    i0, i1 = w[0].idx, w[-1].idx
    status, direction, bo = "forming", bias, None
    limit = min(cx.n, i1 + 40)
    for j in range(i1 + 1, limit):
        a = cx.atr[j]
        if up(j) - lo(j) <= 0:   # apex passed without breakout
            status = "expired"
            break
        if cx.c[j] > up(j) + BREAK_ATR * a:
            bo, direction = j, "bullish"
            break
        if cx.c[j] < lo(j) - BREAK_ATR * a:
            bo, direction = j, "bearish"
            break
    else:
        if cx.n - 1 - i1 > 30:
            status = "expired"
    names = {"ascending_triangle": "Ascending triangle", "descending_triangle": "Descending triangle",
             "symmetric_triangle": "Symmetric triangle", "rectangle": "Rectangle", "rising_wedge": "Rising wedge",
             "falling_wedge": "Falling wedge"}
    end_i = bo if bo is not None else min(cx.n - 1, i1 + 40) if status == "expired" else cx.n - 1
    draw_to = bo if bo is not None else cx.n - 1 if status == "forming" else i1
    lines = [
        {"from": cx.pt(i0, up(i0)), "to": cx.pt(draw_to, up(draw_to)), "role": "resistance", "style": "solid"},
        {"from": cx.pt(i0, lo(i0)), "to": cx.pt(draw_to, lo(draw_to)), "role": "support", "style": "solid"},
    ]
    anchors = [{"time": cx.t(p.idx), "price": round_price(p.price), "label": p.kind} for p in w]
    levels: dict[str, float] = {}
    markers = []
    vol_ok = False
    explanation = (f"{names[ptype]} from {cx.t(i0)} to {cx.t(i1)} ({i1 - i0} bars, {len(w)} touches). "
                   f"Upper line {up(i1):.2f}, lower line {lo(i1):.2f}; starting height {height:.2f}.")
    if bo is not None:
        status = "confirmed"
        vol_ok = cx.volc(bo)
        if direction == "bullish":
            lvl, tgt, inv = up(bo), up(bo) + height, lo(bo)
        else:
            lvl, tgt, inv = lo(bo), lo(bo) - height, up(bo)
        # failure: price closed back through the invalidation level soon after the breakout
        if cx.failed_after(bo, inv, direction == "bullish"):
            status = "failed"
        levels = {"breakout": round_price(lvl), "target": round_price(tgt), "invalidation": round_price(inv)}
        proj = bo + max(5, (i1 - i0) // 3)
        lines.append({"from": cx.pt(bo, lvl), "to": cx.pt(proj, tgt), "role": "target_projection", "style": "dashed"})
        lines.append({"from": cx.pt(bo, inv), "to": cx.pt(proj, inv), "role": "invalidation", "style": "dotted"})
        markers.append({"time": cx.t(bo), "position": "belowBar" if direction == "bullish" else "aboveBar",
                        "shape": "arrowUp" if direction == "bullish" else "arrowDown", "text": "Breakout" if direction == "bullish" else "Breakdown"})
        explanation += (f" {'Broke out above' if direction == 'bullish' else 'Broke down below'} {lvl:.2f} on {cx.t(bo)}"
                        f" (close {cx.c[bo]:.2f}{', volume ' + format(cx.rv[bo], '.1f') + '× average' if np.isfinite(cx.rv[bo]) else ''})."
                        f" Measured-move target {tgt:.2f} (= breakout ± pattern height); invalid if it closes back past {inv:.2f}.")
        if status == "failed":
            explanation += f" The breakout FAILED: within {FAIL_WINDOW} bars price closed back through the invalidation level."
    elif status == "forming":
        lvl_up, lvl_dn = up(cx.n - 1), lo(cx.n - 1)
        if bias == "bearish":
            levels = {"breakout": round_price(lvl_dn), "target": round_price(lvl_dn - height), "invalidation": round_price(lvl_up)}
        else:
            levels = {"breakout": round_price(lvl_up), "target": round_price(lvl_up + height), "invalidation": round_price(lvl_dn)}
        explanation += (f" Still forming: a close above {lvl_up:.2f} (+{BREAK_ATR} ATR) on strong volume triggers a bullish breakout;"
                        f" a close below {lvl_dn:.2f} triggers a breakdown.")
    else:
        explanation += " The pattern expired without a decisive breakout."
    conf = 0.45 + 0.07 * len(w) + (0.1 if vol_ok else 0)
    return Pattern(type=ptype, direction=direction, status=status, start=cx.t(i0), end=cx.t(end_i), end_index=end_i,
                   anchors=anchors, lines=lines, markers=markers, levels=levels, confidence=float(min(conf, 0.95)),
                   context={"volume_confirmed": vol_ok, "touches": len(w), "height": round_price(height)},
                   explanation=explanation)


# --------------------------------------------------------------------------- double tops/bottoms
def _doubles(cx: _Ctx, piv: list[Pivot]) -> list[tuple[float, Pattern]]:
    out = []
    cp = [p for p in piv if p.confirmed]
    for k in range(len(cp) - 2):
        p1, mid, p2 = cp[k], cp[k + 1], cp[k + 2]
        if p1.kind != p2.kind or mid.kind == p1.kind:
            continue
        top = p1.kind == "H"
        a = cx.atr[p2.idx]
        price = cx.c[p2.idx]
        tol = max(0.5 * a, 0.015 * price)
        if abs(p1.price - p2.price) > tol or not (8 <= p2.idx - p1.idx <= 120):
            continue
        extreme = max(p1.price, p2.price) if top else min(p1.price, p2.price)
        depth = (min(p1.price, p2.price) - mid.price) if top else (mid.price - max(p1.price, p2.price))
        if depth < max(1.5 * a, 0.04 * price):
            continue
        neck = mid.price
        status, bo, fail_i = "forming", None, None
        for j in range(p2.idx + 1, min(cx.n, p2.idx + 60)):
            aj = cx.atr[j]
            if (top and cx.c[j] > extreme + BREAK_ATR * aj) or (not top and cx.c[j] < extreme - BREAK_ATR * aj):
                status, fail_i = "failed", j
                break
            if (top and cx.c[j] < neck - 0.2 * aj) or (not top and cx.c[j] > neck + 0.2 * aj):
                status, bo = "confirmed", j
                break
        else:
            if cx.n - 1 - p2.idx > 40:
                status = "expired"
        ptype = "double_top" if top else "double_bottom"
        direction = "bearish" if top else "bullish"
        height = abs(extreme - neck)
        tgt = neck - height if top else neck + height
        inv = extreme + (0.3 * a if top else -0.3 * a)
        broke_then_failed = bo is not None and cx.failed_after(bo, inv, not top)
        if broke_then_failed:
            status = "failed"
        end_i = bo if bo is not None else fail_i if fail_i is not None else cx.n - 1 if status == "forming" else min(cx.n - 1, p2.idx + 40)
        lines = [{"from": cx.pt(p1.idx, extreme), "to": cx.pt(p2.idx, extreme), "role": "resistance" if top else "support", "style": "solid"},
                 {"from": cx.pt(p1.idx, neck), "to": cx.pt(end_i, neck), "role": "neckline", "style": "solid"}]
        markers = []
        if bo is not None:
            proj = bo + max(5, (p2.idx - p1.idx) // 2)
            lines.append({"from": cx.pt(bo, neck), "to": cx.pt(proj, tgt), "role": "target_projection", "style": "dashed"})
            markers.append({"time": cx.t(bo), "position": "aboveBar" if top else "belowBar",
                            "shape": "arrowDown" if top else "arrowUp", "text": "Neckline break"})
        vol_ok = bo is not None and cx.volc(bo)
        label = "Top" if top else "Bottom"
        expl = (f"Double {label.lower()}: two {'peaks' if top else 'troughs'} at {p1.price:.2f} ({cx.t(p1.idx)}) and {p2.price:.2f} ({cx.t(p2.idx)}),"
                f" neckline {neck:.2f}. ")
        if status == "confirmed":
            expl += f"Neckline broken on {cx.t(bo)}; measured target {tgt:.2f}; invalid above/below {inv:.2f}."
        elif status == "forming":
            expl += f"Not yet confirmed: needs a close {'below' if top else 'above'} the {neck:.2f} neckline."
        elif status == "failed":
            expl += (f"Neckline broken on {cx.t(bo)}, then FAILED: within {FAIL_WINDOW} bars price closed back beyond the {'peaks' if top else 'troughs'}."
                     if broke_then_failed else f"FAILED: price closed beyond the {'peaks' if top else 'troughs'} instead of breaking the neckline.")
        out.append((4.5 + (1 if status in ("confirmed", "forming") else 0), Pattern(
            type=ptype, direction=direction, status=status, start=cx.t(p1.idx), end=cx.t(end_i), end_index=end_i,
            anchors=[{"time": cx.t(p1.idx), "price": round_price(p1.price), "label": label[0] + "1"},
                     {"time": cx.t(mid.idx), "price": round_price(neck), "label": "N"},
                     {"time": cx.t(p2.idx), "price": round_price(p2.price), "label": label[0] + "2"}],
            lines=lines, markers=markers,
            levels={"breakout": round_price(neck), "target": round_price(tgt), "invalidation": round_price(inv)},
            confidence=0.6 + (0.1 if vol_ok else 0), context={"volume_confirmed": vol_ok, "height": round_price(height)},
            explanation=expl)))
    return out


# --------------------------------------------------------------------------- head & shoulders
def _head_shoulders(cx: _Ctx, piv: list[Pivot]) -> list[tuple[float, Pattern]]:
    out = []
    cp = [p for p in piv if p.confirmed]
    for k in range(len(cp) - 4):
        ls, n1, hd, n2, rs = cp[k:k + 5]
        if not (ls.kind == hd.kind == rs.kind and n1.kind == n2.kind != ls.kind):
            continue
        top = ls.kind == "H"
        a = cx.atr[rs.idx]
        price = cx.c[rs.idx]
        sgn = 1 if top else -1
        if sgn * (hd.price - max(ls.price, rs.price) if top else hd.price - min(ls.price, rs.price)) < max(0.5 * a, 0.02 * price):
            continue
        if abs(ls.price - rs.price) > max(1.2 * a, 0.05 * price):
            continue
        m = (n2.price - n1.price) / (n2.idx - n1.idx)
        neck = _line(m, n1.price - m * n1.idx)
        # both shoulders must sit on the outer side of the neckline (above it for a top, below it for a bottom);
        # the else-branch is already signed for the bottom case, so it must not be multiplied by sgn again
        # (that rejected every valid inverse head & shoulders)
        shoulder_gap = (min(ls.price, rs.price) - neck(rs.idx)) if top else (neck(rs.idx) - max(ls.price, rs.price))
        if shoulder_gap <= 0:
            continue
        status, bo, fail_i = "forming", None, None
        for j in range(rs.idx + 1, min(cx.n, rs.idx + 50)):
            aj = cx.atr[j]
            if (top and cx.c[j] > rs.price + BREAK_ATR * aj and cx.c[j] > hd.price) or (not top and cx.c[j] < rs.price - BREAK_ATR * aj and cx.c[j] < hd.price):
                status, fail_i = "failed", j
                break
            if (top and cx.c[j] < neck(j) - 0.2 * aj) or (not top and cx.c[j] > neck(j) + 0.2 * aj):
                status, bo = "confirmed", j
                break
        else:
            if cx.n - 1 - rs.idx > 35:
                status = "expired"
        # a steep neckline extrapolated to the breakout bar can run past the shoulders (COMB: breakout 177 with a
        # 77.81 invalidation): the shoulders must still sit outside the neckline where it is broken
        nb = neck(bo if bo is not None else cx.n - 1)
        if (top and nb >= min(ls.price, rs.price)) or ((not top) and nb <= max(ls.price, rs.price)):
            continue
        height = abs(hd.price - neck(hd.idx))
        end_i = bo if bo is not None else fail_i if fail_i is not None else cx.n - 1 if status == "forming" else min(cx.n - 1, rs.idx + 35)
        lvl = neck(bo if bo is not None else cx.n - 1)
        tgt = lvl - height if top else lvl + height
        inv = rs.price + (0.3 * a if top else -0.3 * a)
        broke_then_failed = bo is not None and cx.failed_after(bo, inv, not top)
        if broke_then_failed:
            status = "failed"
        ptype = "head_and_shoulders" if top else "inverse_head_and_shoulders"
        pts = [ls, n1, hd, n2, rs]
        lines = [{"from": cx.pt(a_.idx, a_.price), "to": cx.pt(b_.idx, b_.price), "role": "outline", "style": "solid"}
                 for a_, b_ in zip(pts, pts[1:])]
        lines.append({"from": cx.pt(ls.idx, neck(ls.idx)), "to": cx.pt(end_i, neck(end_i)), "role": "neckline", "style": "solid"})
        markers = []
        if bo is not None:
            proj = bo + max(5, (rs.idx - ls.idx) // 3)
            lines.append({"from": cx.pt(bo, lvl), "to": cx.pt(proj, tgt), "role": "target_projection", "style": "dashed"})
            markers.append({"time": cx.t(bo), "position": "aboveBar" if top else "belowBar",
                            "shape": "arrowDown" if top else "arrowUp", "text": "Neckline break"})
        vol_ok = bo is not None and cx.volc(bo)
        name = "Head & shoulders" if top else "Inverse head & shoulders"
        expl = (f"{name}: left shoulder {ls.price:.2f}, head {hd.price:.2f}, right shoulder {rs.price:.2f}; "
                f"neckline now {lvl:.2f}. ")
        expl += {"confirmed": f"Neckline broken on {cx.t(bo) if bo is not None else ''}; target {tgt:.2f} (head-to-neckline height projected).",
                 "forming": f"Awaiting a neckline break at about {lvl:.2f}.",
                 "failed": (f"Neckline broken on {cx.t(bo)}, then FAILED: within {FAIL_WINDOW} bars price closed back beyond the right shoulder."
                            if broke_then_failed else "FAILED: price moved back beyond the right shoulder and head."),
                 "expired": "Expired without a neckline break."}[status]
        out.append((6 + (1 if status in ("confirmed", "forming") else 0), Pattern(
            type=ptype, direction="bearish" if top else "bullish", status=status, start=cx.t(ls.idx), end=cx.t(end_i),
            end_index=end_i,
            anchors=[{"time": cx.t(p.idx), "price": round_price(p.price), "label": lab}
                     for p, lab in zip(pts, ["LS", "", "H", "", "RS"])],
            lines=lines, markers=markers,
            levels={"breakout": round_price(lvl), "target": round_price(tgt), "invalidation": round_price(inv)},
            confidence=0.65 + (0.1 if vol_ok else 0), context={"volume_confirmed": vol_ok, "height": round_price(height)},
            explanation=expl)))
    return out


# --------------------------------------------------------------------------- flags
def _flags(cx: _Ctx, piv: list[Pivot]) -> list[tuple[float, Pattern]]:
    out = []
    cp = [p for p in piv if p.confirmed]
    for tip in cp:
        bull = tip.kind == "H"
        lo_i = max(0, tip.idx - 15)
        if tip.idx - lo_i < 2:
            continue
        # pole starts at the extreme of the 15 bars before the tip (poles often launch from a quiet base, not a swing pivot)
        seg = cx.l[lo_i:tip.idx - 1] if bull else cx.h[lo_i:tip.idx - 1]
        b_i = lo_i + int(seg.argmin() if bull else seg.argmax())
        base = Pivot(b_i, float(cx.l[b_i] if bull else cx.h[b_i]), "L" if bull else "H")
        pole = abs(tip.price - base.price)
        a = cx.atr[tip.idx]
        if pole < max(3 * a, 0.08 * cx.c[tip.idx]):
            continue
        status, bo, cons_end = "expired", None, None
        upper = lower = None
        for j in range(tip.idx + 4, min(cx.n, tip.idx + 46)):
            # the channel is fitted on the consolidation bars only: including the pole's tip bar (far above the
            # flag) tilted the upper line steeply down, so at the breakout it sat below the consolidation lows
            seg = range(tip.idx + 1, j)
            retrace = (tip.price - cx.l[tip.idx + 1:j + 1].min()) if bull else (cx.h[tip.idx + 1:j + 1].max() - tip.price)
            if retrace > 0.5 * pole:
                status = "invalid"
                break
            xs = np.array(list(seg), float)
            hi_line = np.polyfit(xs, cx.h[tip.idx + 1:j], 1)
            lo_line = np.polyfit(xs, cx.l[tip.idx + 1:j], 1)
            upper, lower = _line(*hi_line), _line(*lo_line)
            drift = (hi_line[0] if bull else lo_line[0]) * len(xs) / a
            if (bull and drift > 0.5) or (not bull and drift < -0.5):
                status = "invalid"
                break
            cons_end = j - 1
            if bull and cx.c[j] > upper(j) + 0.2 * cx.atr[j]:
                status, bo = "confirmed", j
                break
            if not bull and cx.c[j] < lower(j) - 0.2 * cx.atr[j]:
                status, bo = "confirmed", j
                break
        else:
            if cons_end is not None and cx.n - 1 - tip.idx <= 45:
                status = "forming"
        if status not in ("confirmed", "forming") or upper is None:
            continue
        end_i = bo if bo is not None else cx.n - 1
        cons_lo = float(cx.l[tip.idx + 1:end_i + 1].min()) if bull else float(cx.h[tip.idx + 1:end_i + 1].max())
        lvl = upper(end_i) if bull else lower(end_i)
        # the breakout level must sit on the outer side of the consolidation, else the geometry is degenerate
        if (bull and lvl - cons_lo < 0.25 * a) or ((not bull) and cons_lo - lvl < 0.25 * a):
            continue
        if bo is not None and cx.failed_after(bo, cons_lo, bull):
            status = "failed"
        tgt = lvl + pole if bull else lvl - pole
        ptype = "bull_flag" if bull else "bear_flag"
        lines = [{"from": cx.pt(base.idx, base.price), "to": cx.pt(tip.idx, tip.price), "role": "pole", "style": "solid"},
                 {"from": cx.pt(tip.idx + 1, upper(tip.idx + 1)), "to": cx.pt(end_i, upper(end_i)), "role": "flag_upper", "style": "solid"},
                 {"from": cx.pt(tip.idx + 1, lower(tip.idx + 1)), "to": cx.pt(end_i, lower(end_i)), "role": "flag_lower", "style": "solid"}]
        markers = []
        if bo is not None:
            lines.append({"from": cx.pt(bo, lvl), "to": cx.pt(bo + (tip.idx - base.idx) + 3, tgt), "role": "target_projection", "style": "dashed"})
            markers.append({"time": cx.t(bo), "position": "belowBar" if bull else "aboveBar",
                            "shape": "arrowUp" if bull else "arrowDown", "text": "Flag breakout"})
        vol_ok = bo is not None and cx.volc(bo)
        expl = (f"{'Bull' if bull else 'Bear'} flag: a {pole:.2f} ({pole / base.price * 100:.0f}%) pole in {tip.idx - base.idx} bars "
                f"from {base.price:.2f} to {tip.price:.2f}, then a tight counter-trend consolidation. ")
        expl += (f"{'Broke out' if bull else 'Broke down'} on {cx.t(bo)}; target {tgt:.2f} (pole length {'added to' if bull else 'subtracted from'} the breakout)"
                 + (f", then FAILED: within {FAIL_WINDOW} bars price closed back beyond {cons_lo:.2f}." if status == "failed" else ".") if bo is not None
                 else f"Forming: a close {'above' if bull else 'below'} {lvl:.2f} triggers it (target {tgt:.2f}).")
        out.append((5.5, Pattern(
            type=ptype, direction="bullish" if bull else "bearish", status=status, start=cx.t(base.idx), end=cx.t(end_i),
            end_index=end_i,
            anchors=[{"time": cx.t(base.idx), "price": round_price(base.price), "label": "Pole"},
                     {"time": cx.t(tip.idx), "price": round_price(tip.price), "label": ""}],
            lines=lines, markers=markers,
            levels={"breakout": round_price(lvl), "target": round_price(tgt), "invalidation": round_price(cons_lo)},
            confidence=0.6 + (0.15 if vol_ok else 0), context={"volume_confirmed": vol_ok, "pole": round_price(pole),
                     # the original detector allowed 25 bars (20 while forming); longer flags come from the analyst methods
                     "long_flag": (end_i - tip.idx > 25) if status == "confirmed" else (cx.n - 1 - tip.idx > 20)},
            explanation=expl)))
    return out


# --------------------------------------------------------------------------- liquidity sweeps
def _sweeps(cx: _Ctx, piv: list[Pivot], lookback: int = 150) -> list[Pattern]:
    out = []
    cp = [p for p in piv if p.confirmed]
    last_i = -99
    for i in range(max(5, cx.n - lookback), cx.n):
        a = cx.atr[i]
        for kind in ("L", "H"):
            best = None
            for p in cp:
                if p.kind != kind or not (i - 60 <= p.idx <= i - 3):
                    continue
                seg_l, seg_h = cx.l[p.idx + 1:i], cx.h[p.idx + 1:i]
                if kind == "L" and seg_l.size and seg_l.min() <= p.price:
                    continue
                if kind == "H" and seg_h.size and seg_h.max() >= p.price:
                    continue
                if kind == "L" and cx.l[i] < p.price - 0.05 * a and cx.c[i] > p.price:
                    best = p if best is None or p.price < best.price else best
                if kind == "H" and cx.h[i] > p.price + 0.05 * a and cx.c[i] < p.price:
                    best = p if best is None or p.price > best.price else best
            if best is None or i - last_i < 3:
                continue
            last_i = i
            bull = kind == "L"
            vol_ok = cx.volc(i)
            out.append(Pattern(
                type="liquidity_sweep_low" if bull else "liquidity_sweep_high",
                direction="bullish" if bull else "bearish", status="confirmed",
                start=cx.t(best.idx), end=cx.t(i), end_index=i,
                anchors=[{"time": cx.t(best.idx), "price": round_price(best.price), "label": "Swing " + ("low" if bull else "high")}],
                lines=[{"from": cx.pt(best.idx, best.price), "to": cx.pt(i, best.price), "role": "swept_level", "style": "dashed"}],
                markers=[{"time": cx.t(i), "position": "belowBar" if bull else "aboveBar",
                          "shape": "arrowUp" if bull else "arrowDown", "text": "Sweep"}],
                levels={"level": round_price(best.price), "invalidation": round_price(cx.l[i] if bull else cx.h[i])},
                confidence=0.55 + (0.15 if vol_ok else 0), context={"volume_confirmed": vol_ok},
                explanation=(f"Liquidity sweep on {cx.t(i)}: price {'dipped below' if bull else 'spiked above'} the {cx.t(best.idx)} swing "
                             f"{'low' if bull else 'high'} at {best.price:.2f} (where stop orders sit), touching {(cx.l[i] if bull else cx.h[i]):.2f}, "
                             f"then closed back {'above' if bull else 'below'} it at {cx.c[i]:.2f}. Those stops were absorbed, "
                             f"a common {'bottoming' if bull else 'topping'} signal. Invalid on a close beyond {(cx.l[i] if bull else cx.h[i]):.2f}."),
            ))
    return out


# --------------------------------------------------------------------------- squeeze breakouts
def _squeezes(cx: _Ctx, lookback: int = 150) -> list[Pattern]:
    df = cx.df
    if "bb_width_pct" not in df:
        return []
    pct, up, lo, mid = (df[k].to_numpy(float) for k in ("bb_width_pct", "bb_up", "bb_lo", "bb_mid"))
    out, last = [], -99
    for i in range(max(25, cx.n - lookback), cx.n):
        window = pct[i - 6:i]
        if not np.isfinite(window).any() or np.nanmin(window) > 0.15 or i - last < 10:
            continue
        for bull in (True, False):
            if bull and not (cx.c[i] > up[i] and cx.c[i - 1] <= up[i - 1]):
                continue
            if not bull and not (cx.c[i] < lo[i] and cx.c[i - 1] >= lo[i - 1]):
                continue
            last = i
            s = i - 10
            vol_ok = cx.volc(i)
            out.append(Pattern(
                type="squeeze_breakout_up" if bull else "squeeze_breakout_down",
                direction="bullish" if bull else "bearish", status="confirmed",
                start=cx.t(s), end=cx.t(i), end_index=i,
                zones=[{"t1": cx.t(s), "t2": cx.t(i - 1), "low": round_price(float(np.nanmin(lo[s:i]))),
                        "high": round_price(float(np.nanmax(up[s:i]))), "role": "squeeze"}],
                markers=[{"time": cx.t(i), "position": "belowBar" if bull else "aboveBar",
                          "shape": "arrowUp" if bull else "arrowDown", "text": "Squeeze ↑" if bull else "Squeeze ↓"}],
                levels={"breakout": round_price(up[i] if bull else lo[i]), "invalidation": round_price(mid[i])},
                confidence=0.55 + (0.15 if vol_ok else 0), context={"volume_confirmed": vol_ok},
                explanation=(f"Bollinger squeeze: band width fell into the lowest 15% of the last 120 bars (volatility compression), "
                             f"then on {cx.t(i)} price closed {'above the upper' if bull else 'below the lower'} band at {cx.c[i]:.2f}. "
                             f"Compression often precedes a directional move. Invalid back through the 20-day mean {mid[i]:.2f}."),
            ))
    return out


# --------------------------------------------------------------------------- RSI divergences
def _divergences(cx: _Ctx, piv: list[Pivot], lookback: int = 200) -> list[Pattern]:
    if "rsi" not in cx.df:
        return []
    rsi = cx.df["rsi"].to_numpy(float)
    out = []
    cp = [p for p in piv if p.confirmed and p.idx >= cx.n - lookback]
    for kind in ("L", "H"):
        same = [p for p in cp if p.kind == kind]
        for p1, p2 in zip(same, same[1:]):
            if not (5 <= p2.idx - p1.idx <= 60) or not (np.isfinite(rsi[p1.idx]) and np.isfinite(rsi[p2.idx])):
                continue
            r1, r2 = rsi[p1.idx], rsi[p2.idx]
            bull = kind == "L"
            hidden = False
            if bull and not (p2.price < p1.price and r2 > r1 + 2 and r1 < 40):
                # hidden bullish: higher low in price, lower low in RSI (trend continuation)
                if p2.price > p1.price and r2 < r1 - 3 and r2 < 50:
                    hidden = True
                else:
                    continue
            if not bull and not (p2.price > p1.price and r2 < r1 - 2 and r1 > 60):
                # hidden bearish: lower high in price, higher high in RSI
                if p2.price < p1.price and r2 > r1 + 3 and r2 > 50:
                    hidden = True
                else:
                    continue
            if hidden:
                out.append(Pattern(
                    type="hidden_bullish_divergence" if bull else "hidden_bearish_divergence",
                    direction="bullish" if bull else "bearish", status="confirmed",
                    start=cx.t(p1.idx), end=cx.t(p2.idx), end_index=p2.idx,
                    anchors=[{"time": cx.t(p1.idx), "price": round_price(p1.price)}, {"time": cx.t(p2.idx), "price": round_price(p2.price)}],
                    lines=[{"from": cx.pt(p1.idx, p1.price), "to": cx.pt(p2.idx, p2.price), "role": "divergence_price", "style": "dashed"},
                           {"from": {"time": cx.t(p1.idx), "price": round(float(r1), 1)}, "to": {"time": cx.t(p2.idx), "price": round(float(r2), 1)},
                            "role": "divergence_rsi", "style": "dashed", "pane": "rsi"}],
                    markers=[{"time": cx.t(p2.idx), "position": "belowBar" if bull else "aboveBar", "shape": "circle", "text": "H-Div"}],
                    levels={"level": round_price(p2.price), "invalidation": round_price(p2.price - (0.5 if bull else -0.5) * cx.atr[p2.idx])},
                    confidence=0.5,
                    explanation=(f"Hidden {'bullish' if bull else 'bearish'} divergence: price made a {'higher low' if bull else 'lower high'} "
                                 f"({p1.price:.2f} → {p2.price:.2f}) while RSI made a {'lower low' if bull else 'higher high'} ({r1:.0f} → {r2:.0f}). "
                                 f"This usually signals the {'uptrend' if bull else 'downtrend'} is likely to CONTINUE."),
                ))
                continue
            out.append(Pattern(
                type="bullish_rsi_divergence" if bull else "bearish_rsi_divergence",
                direction="bullish" if bull else "bearish", status="confirmed",
                start=cx.t(p1.idx), end=cx.t(p2.idx), end_index=p2.idx,
                anchors=[{"time": cx.t(p1.idx), "price": round_price(p1.price)}, {"time": cx.t(p2.idx), "price": round_price(p2.price)}],
                lines=[{"from": cx.pt(p1.idx, p1.price), "to": cx.pt(p2.idx, p2.price), "role": "divergence_price", "style": "solid"},
                       {"from": {"time": cx.t(p1.idx), "price": round(float(r1), 1)}, "to": {"time": cx.t(p2.idx), "price": round(float(r2), 1)},
                        "role": "divergence_rsi", "style": "solid", "pane": "rsi"}],
                markers=[{"time": cx.t(p2.idx), "position": "belowBar" if bull else "aboveBar", "shape": "circle", "text": "Div"}],
                levels={"level": round_price(p2.price), "invalidation": round_price(p2.price - (0.5 if bull else -0.5) * cx.atr[p2.idx])},
                confidence=0.55,
                explanation=(f"{'Bullish' if bull else 'Bearish'} RSI divergence: price made a {'lower low' if bull else 'higher high'} "
                             f"({p1.price:.2f} → {p2.price:.2f}) but RSI made a {'higher low' if bull else 'lower high'} ({r1:.0f} → {r2:.0f}). "
                             f"Momentum is {'no longer confirming the decline' if bull else 'fading under the advance'}."),
            ))
    return out


# --------------------------------------------------------------------------- dedupe & entry point
def _overlap(a: Pattern, b: Pattern) -> float:
    a0, a1 = pd.Timestamp(a.start), pd.Timestamp(a.end)
    b0, b1 = pd.Timestamp(b.start), pd.Timestamp(b.end)
    inter = (min(a1, b1) - max(a0, b0)).days
    union = (max(a1, b1) - min(a0, b0)).days or 1
    return max(0, inter) / union


def detect_chart_patterns(df: pd.DataFrame, pivots: list[Pivot]) -> list[Pattern]:
    cx = _Ctx(df)
    structural = _triangle_family(cx, pivots) + _doubles(cx, pivots) + _head_shoulders(cx, pivots) + _flags(cx, pivots)
    structural.sort(key=lambda x: (x[0], x[1].end_index), reverse=True)
    kept: list[Pattern] = []
    for _, p in structural:
        if all(_overlap(p, q) < 0.4 for q in kept):
            kept.append(p)
    return kept + _sweeps(cx, pivots) + _squeezes(cx) + _divergences(cx, pivots)
