"""Measured track record of each pattern type on CSE history, attached to its explanation.

The numbers come from `python -m cse_advisor.pattern_study --write-track-record`, which writes
config/pattern_track_record.json: for every (pattern type, direction) the forward 10 / 20-session returns after
the pattern first confirmed, against random days of the same stock on the same side of its 50-day average,
with significance clustered by calendar month. Without the file nothing is attached and nothing changes.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from ..config import BACKEND_DIR

PATH = BACKEND_DIR / "config" / "pattern_track_record.json"
LABEL_TEXT = {
    "edge": "a measurable edge, stable across both halves of the sample",
    "weak": "a weak edge; not reliable on its own",
    "no edge": "no reliable edge",
    "wrong way": "it moved the OPPOSITE way to what the pattern implies",
}


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def lookup(ptype: str, direction: str) -> dict[str, Any] | None:
    rec = (load().get("patterns") or {}).get(f"{ptype}|{direction}")
    return {**rec, "span": load().get("span"), "stocks": load().get("stocks")} if rec else None


def sentence(rec: dict[str, Any], direction: str) -> str:
    """One plain-language sentence about what happened after this pattern in the past."""
    # median_20d / baseline_median_20d are the actual price moves; hit_20d is the share of cases price went the
    # pattern's way (up for a bullish pattern, down for a bearish one)
    word = "rose" if direction == "bullish" else "fell"
    m, b = rec["median_20d"], rec["baseline_median_20d"]
    return (f"Track record ({rec.get('stocks', '?')} CSE stocks, {rec.get('span', '')}, n={rec['n']:,}): 20 sessions after this pattern price "
            f"{'rose' if m >= 0 else 'fell'} a median {abs(m):.1f}%, against a median {b:+.1f}% on comparable days; "
            f"it {word} in {rec['hit_20d']:.0f}% of cases vs {rec['baseline_hit_20d']:.0f}% on comparable days. Verdict: {LABEL_TEXT[rec['label']]}.")
