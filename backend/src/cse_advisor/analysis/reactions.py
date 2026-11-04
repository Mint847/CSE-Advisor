"""How the price actually reacted to past announcements (event study on daily closes).

For each event: the first session that could react (an announcement after the 14:30 close counts from the
next session), the close-to-close move that session, and the move over 5 sessions, both raw and in excess
of the ASPI over the same sessions. Aggregates per event kind say whether this stock has a habit; with
fewer than MIN_N events it says so instead of reading noise.
"""
from __future__ import annotations

from datetime import datetime, time as dtime
from typing import Any

import numpy as np
import pandas as pd

MIN_N = 8
CLOSE = dtime(14, 30)


def first_session(closes: pd.Series, when: datetime) -> int | None:
    """Index of the first daily bar that could react to something published at `when`."""
    d = pd.Timestamp(when.date())
    i = int(closes.index.searchsorted(d))
    if i < len(closes) and closes.index[i] == d and when.time() >= CLOSE:
        i += 1                    # published after the close: tomorrow's session reacts
    return i if 0 < i < len(closes) else None


def reaction(closes: pd.Series, aspi: pd.Series | None, when: datetime, span: int = 5) -> dict[str, Any] | None:
    i = first_session(closes, when)
    if i is None:
        return None
    prev = float(closes.iloc[i - 1])
    if not prev:
        return None
    j = min(len(closes) - 1, i + span - 1)
    r0 = float(closes.iloc[i]) / prev - 1
    r5 = float(closes.iloc[j]) / prev - 1
    out = {"session": str(closes.index[i].date()), "r0": round(r0 * 100, 2), "r5": round(r5 * 100, 2), "complete": j - i == span - 1}
    if aspi is not None and not aspi.empty:
        a = aspi.reindex(closes.index).ffill()
        ap, a0, a5 = a.iloc[i - 1], a.iloc[i], a.iloc[j]
        if pd.notna(ap) and pd.notna(a0) and pd.notna(a5) and ap:
            out["x0"] = round((r0 - (a0 / ap - 1)) * 100, 2)
            out["x5"] = round((r5 - (a5 / ap - 1)) * 100, 2)
    return out


def stats(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per kind: count, median day move, median 5-session excess move and how often that was positive."""
    by: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        if e.get("reaction"):
            by.setdefault(e["kind"], []).append(e["reaction"])
    out = []
    for kind, rs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        x5 = [r["x5"] for r in rs if r.get("x5") is not None and r.get("complete")]
        r0 = [r["r0"] for r in rs]
        row = {"kind": kind, "n": len(rs), "median_day_pct": round(float(np.median(r0)), 2) if r0 else None,
               "median_5d_excess_pct": round(float(np.median(x5)), 2) if x5 else None,
               "pct_positive_5d": round(100 * float(np.mean([x > 0 for x in x5])), 0) if x5 else None,
               "readable": len(x5) >= MIN_N}
        if not row["readable"]:
            row["note"] = f"only {len(x5)} complete event{'s' if len(x5) != 1 else ''}: too few to read a habit"
        out.append(row)
    return out


def xd_drop(closes: pd.Series, xd: str, dps: float) -> dict[str, Any] | None:
    """How much of a cash dividend came off the price on the ex-dividend day (100% = the full DPS)."""
    if not dps:
        return None
    d = pd.Timestamp(xd)
    i = int(closes.index.searchsorted(d))
    if not (0 < i < len(closes)) or closes.index[i] != d:
        return None
    drop = float(closes.iloc[i - 1] - closes.iloc[i])
    return {"xd": xd, "dps": dps, "drop": round(drop, 2), "drop_vs_dps_pct": round(100 * drop / dps, 0),
            "yield_pct": round(100 * dps / float(closes.iloc[i - 1]), 2)}
