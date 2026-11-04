"""The measured track record is attached to a pattern's explanation, in the right words for each direction."""
import numpy as np
import pandas as pd

from cse_advisor.analysis import track_record as tr
from cse_advisor.analysis.model import Pattern, score_patterns
from cse_advisor.pattern_study import track_record as build_record

BULL = {"n": 1378, "months": 120, "label": "edge", "median_20d": 2.1, "baseline_median_20d": 0.4, "hit_20d": 53.0, "baseline_hit_20d": 46.0,
        "span": "2013–2026", "stocks": 43}
BEAR = {**BULL, "label": "no edge", "median_20d": -1.5, "baseline_median_20d": 0.8, "hit_20d": 51.0, "baseline_hit_20d": 54.0}


def test_sentences_use_the_real_price_move():
    s = tr.sentence(BULL, "bullish")
    assert "rose a median 2.1%" in s and "+0.4% on comparable days" in s and "it rose in 53% of cases vs 46%" in s and "measurable edge" in s
    # a bearish pattern followed by a FALL: say "fell", not "rose"
    s = tr.sentence(BEAR, "bearish")
    assert "fell a median 1.5%" in s and "+0.8% on comparable days" in s and "it fell in 51% of cases vs 54%" in s and "no reliable edge" in s
    assert "OPPOSITE" in tr.sentence({**BEAR, "label": "wrong way"}, "bearish")


def test_attached_only_when_asked_and_available(monkeypatch):
    monkeypatch.setattr(tr, "load", lambda: {"span": "2013–2026", "stocks": 43, "patterns": {"bull_flag|bullish": BULL}})
    mk = lambda: [Pattern(type="bull_flag", direction="bullish", status="confirmed", start="2026-10-01", end="2026-10-20", end_index=99, explanation="Bull flag.")]
    off = score_patterns(mk(), 100, 1, [], 1.0, "B", set())[0]
    assert "Track record" not in off.explanation and "track_record" not in off.reliability
    on = score_patterns(mk(), 100, 1, [], 1.0, "B", set(), track_record=True)[0]
    assert on.explanation.startswith("Bull flag. Track record (43 CSE stocks") and on.reliability["track_record"]["label"] == "edge"
    other = [Pattern(type="doji", direction="neutral", status="confirmed", start="2026-10-01", end="2026-10-01", end_index=99, explanation="Doji.")]
    assert "Track record" not in score_patterns(other, 100, 1, [], 1.0, "B", set(), track_record=True)[0].explanation


def _events(effect: float, n_months=100, per_month=3, seed=1):
    """Synthetic events: bullish pattern whose 20d return is `effect` above a trend-matched baseline of 0.5."""
    rng = np.random.default_rng(seed)
    rows = []
    for m in range(n_months):
        for k in range(per_month):
            yr = 2013 + m // 12
            rows.append({"sym": f"S{k}", "type": "bull_flag", "direction": "bullish", "date": f"{yr}-{m % 12 + 1:02d}-10", "year": yr,
                         "r10": effect / 2 + rng.normal(0, 4), "r20": effect + rng.normal(0, 6),
                         "b10_tm": 0.25, "b20_tm": 0.5, "b10_tm_hit": 46.0, "b20_tm_hit": 46.0})
    return pd.DataFrame(rows)


def test_labels_are_conservative():
    assert build_record(_events(+4.0))["patterns"]["bull_flag|bullish"]["label"] == "edge"
    assert build_record(_events(-4.0))["patterns"]["bull_flag|bullish"]["label"] == "wrong way"
    assert build_record(_events(0.0))["patterns"]["bull_flag|bullish"]["label"] == "no edge"
    assert not build_record(_events(4.0, n_months=10, per_month=3))["patterns"]          # too few months to say anything
