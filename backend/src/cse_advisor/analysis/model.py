"""Generic pattern format shared by every detector and the chart renderer.

The frontend draws any pattern purely from `anchors`, `lines`, `zones` and
`markers`, so adding a new detector needs no frontend changes.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

from ..config import pattern_catalog, settings


@dataclass
class Pattern:
    type: str
    direction: str                     # bullish | bearish | neutral
    status: str                        # forming | confirmed | failed | expired
    start: str                         # YYYY-MM-DD
    end: str
    end_index: int                     # bar index of the last relevant bar (breakout or last pivot)
    anchors: list[dict[str, Any]] = field(default_factory=list)   # {time, price, label?, pane?}
    lines: list[dict[str, Any]] = field(default_factory=list)     # {from:{time,price}, to:{time,price}, role, style, pane?}
    zones: list[dict[str, Any]] = field(default_factory=list)     # {t1, t2, low, high, role}
    markers: list[dict[str, Any]] = field(default_factory=list)   # {time, position, shape, text}
    levels: dict[str, float] = field(default_factory=dict)        # breakout / target / invalidation
    confidence: float = 0.5
    explanation: str = ""
    context: dict[str, Any] = field(default_factory=dict)         # flags used by priority scoring
    bars: list[str] = field(default_factory=list)                 # bars forming a candlestick pattern
    # filled by score_patterns()
    category: str = ""
    name: str = ""
    tier: int = 3
    engine: str = "original"                                      # original | analyst | smc
    priority: float = 0.0
    priority_tier: str = "low"
    reliability: dict[str, Any] = field(default_factory=dict)
    id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def round_price(x: float) -> float:
    return float(round(x, 2))


# ---- time labels shared by every detector (daily: YYYY-MM-DD, intraday: YYYY-MM-DD HH:MM)
def _is_daily(ts: Any) -> bool:
    return ts.hour == 0 and ts.minute == 0


def tkey(ts: Any) -> str:
    return str(ts.date()) if _is_daily(ts) else ts.strftime("%Y-%m-%d %H:%M")


def bar_step_minutes(index: Any) -> int:
    """The bar size of an intraday index (most common gap between bars of the same day)."""
    import pandas as pd
    idx = pd.DatetimeIndex(index[-300:])
    if len(idx) < 2:
        return 5
    d = pd.Series(idx[1:] - idx[:-1])
    same_day = pd.Series(idx[1:].normalize() == idx[:-1].normalize())
    mins = (d[same_day.to_numpy()].dt.total_seconds() // 60).astype(int)
    mins = mins[mins > 0]
    if mins.empty:
        return 240
    return int(mins.min())       # bars sit on a session-anchored grid, so the smallest gap is the bar size


def tfuture(index: Any, n: int) -> str:
    """Label of the bar n bars after the last one: business days on daily bars, session-aware bar
    steps (skipping nights and weekends) on intraday bars."""
    import pandas as pd
    last = index[-1]
    if _is_daily(last):
        return str((last + pd.offsets.BDay(n)).date())
    step = bar_step_minutes(index)
    oh, om = map(int, settings()["market"]["open"].split(":"))
    ch, cm = map(int, settings()["market"]["close"].split(":"))
    o, c = oh * 60 + om, ch * 60 + cm
    t = pd.Timestamp(last)
    for _ in range(n):
        t = t + pd.Timedelta(minutes=step)
        if t.hour * 60 + t.minute >= c:
            t = (t.normalize() + pd.offsets.BDay(1)) + pd.Timedelta(minutes=o)
    return tkey(t)


def score_patterns(patterns: list[Pattern], n_bars: int, trend_dir: int,
                   key_levels: list[float], atr_now: float, liquidity_grade: str,
                   estimated_open_dates: set[str], track_record: bool = False) -> list[Pattern]:
    """Attach catalog metadata and compute the 0-100 display priority. With `track_record` (daily bars only: the
    study measured daily bars) the measured history of the pattern type is attached and added to its explanation."""
    cat = pattern_catalog()
    pcfg = settings()["patterns"]
    for p in patterns:
        meta = cat.get(p.type, {"name": p.type, "tier": 3, "category": "other", "base_reliability": 0.4})
        p.category, p.name, p.tier = meta["category"], meta["name"], meta["tier"]
        p.engine = meta.get("engine") or ("analyst" if p.context.get("long_flag") else "original")
        base = float(meta.get("base_reliability", 0.4))
        p.reliability = {"base": base, "source": "prior (pending CSE backtest)"}
        if track_record and p.direction in ("bullish", "bearish"):
            from .track_record import lookup, sentence
            tr = lookup(p.type, p.direction)
            if tr:
                p.reliability["track_record"] = tr
                p.explanation = f"{p.explanation} {sentence(tr, p.direction)}"

        mult = 1.0
        ref = p.levels.get("breakout") or p.levels.get("level") or (p.anchors[-1]["price"] if p.anchors else None)
        if ref is not None and atr_now > 0 and any(abs(ref - lv) <= 0.5 * atr_now for lv in key_levels):
            mult *= 1.25
            p.context["at_key_level"] = True
        if p.context.get("volume_confirmed"):
            mult *= 1.15
        sign = 1 if p.direction == "bullish" else -1 if p.direction == "bearish" else 0
        if sign and trend_dir:
            if sign == trend_dir:
                mult *= 1.1
                p.context["trend_aligned"] = True
            elif p.category == "candlestick" and not p.context.get("at_key_level"):
                mult *= 0.75
                p.context["against_trend"] = True
        if liquidity_grade == "E":
            mult *= 0.5
        if p.category == "candlestick" and any(b in estimated_open_dates for b in p.bars):
            mult *= float(pcfg["estimated_open_penalty"])
            p.context["estimated_open"] = True

        half_life = pcfg["candle_half_life_bars"] if p.category == "candlestick" else pcfg["chart_half_life_bars"]
        age = max(0, n_bars - 1 - p.end_index) if p.status != "forming" else 0
        recency = 0.5 ** (age / half_life)
        status_mult = {"confirmed": 1.0, "forming": 0.7, "failed": 0.3, "expired": 0.2}.get(p.status, 0.5)

        p.priority = round(min(100.0, base * 100 * mult * recency * status_mult * (0.6 + 0.4 * p.confidence) / 0.8), 1)
        p.priority_tier = "high" if p.priority >= 70 else "medium" if p.priority >= 40 else "low"
        p.context["age_bars"] = age
        # one-per-stock drawings keep a stable id so a per-drawing show/hide choice survives re-analysis
        key = p.type if p.type in ("smc_structure", "premium_discount") else f"{p.type}|{p.start}" if p.category == "level" \
            else f"{p.type}|{p.start}|{p.end}"
        p.id = "p_" + hashlib.md5(key.encode()).hexdigest()[:10]
    patterns.sort(key=lambda x: x.priority, reverse=True)
    return patterns
