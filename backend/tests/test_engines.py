"""The Original and Analyst engines run side by side and independently."""
from __future__ import annotations

import pytest

from cse_advisor.strategy.recommend import agreement
from replay import run


@pytest.fixture(scope="module")
def kz():
    return {d: run("KZOO", d) for d in ("2026-03-05", "2026-04-09", "2026-10-29")}


def test_both_engines_present(kz):
    for r in kz.values():
        rec = r["recommendation"]
        assert rec["original"]["engine"] == "original" and rec["analyst"]["engine"] == "analyst"
        assert r["verdict"] == rec["original"]["verdict"] and r["verdict_analyst"] == rec["analyst"]["verdict"]
        assert rec["agreement"]["state"] in {"agree", "mixed", "differ"}


def test_original_is_the_original_checklist(kz):
    o = kz["2026-04-09"]["recommendation"]["original"]
    assert len(o["checklist"]) == 7
    labels = " ".join(c["label"] for c in o["checklist"])
    for analyst_only in ("over-extended", "volatility", "overhang", "Level score"):
        assert analyst_only not in labels
    assert "adjustments" not in o and o["entries"] == []
    assert all(s["kind"] in {"breakout", "pending_breakout", "reversal", "pullback"} for s in o["setups"])


def test_original_ignores_analyst_patterns(kz):
    """The composite score of the Original engine must not move when analyst-only patterns change."""
    from cse_advisor.strategy.recommend import original_patterns
    r = kz["2026-04-09"]
    pats = r["patterns"]
    kept = original_patterns(pats)
    assert all(p["engine"] == "original" for p in kept)
    assert not any(p["type"] in ("fibonacci", "trendline_resistance", "hidden_bearish_divergence") for p in kept)


def test_analyst_engine_calls(kz):
    assert kz["2026-03-05"]["verdict_analyst"] == "AVOID"     # analyst: avoid until direction is clear
    assert kz["2026-10-29"]["verdict_analyst"] == "WATCH"     # analyst: good to watch
    a = kz["2026-04-09"]["recommendation"]["analyst"]
    assert a["explanation"]["score_breakdown"][0]["contribution"] == 50
    assert any(c["label"].startswith("Level score") for c in a["checklist"])
    assert {e["entry_label"] for e in a["entries"]} >= {"Entry 1", "Zone 2"}


def test_original_rights_issue_is_red_flag():
    from datetime import datetime
    from cse_advisor.analysis.news import news_score
    anns = [{"title": "Rights Issue", "date": datetime(2026, 3, 1), "category": "rights_issue", "sentiment": -0.2, "materiality": 5}]
    asof = datetime(2026, 3, 10)
    assert news_score(anns, asof, legacy=True)["red_flags"]
    assert not news_score(anns, asof)["red_flags"]


@pytest.mark.parametrize("a,b,state", [("BUY", "BUY", "agree"), ("BUY", "AVOID", "differ"), ("WATCH", "AVOID", "mixed"),
                                       ("HOLD", "SELL", "differ"), ("WATCH", "BUY", "mixed")])
def test_agreement(a, b, state):
    assert agreement(a, b)["state"] == state


@pytest.mark.parametrize("a,b,c,state", [("WATCH", "WATCH", "WATCH", "agree"), ("WATCH", "WATCH", "AVOID", "mixed"),
                                         ("BUY", "WATCH", "AVOID", "differ"), ("BUY", "BUY", "WATCH", "mixed")])
def test_agreement_three(a, b, c, state):
    assert agreement(a, b, c)["state"] == state


def test_smc_engine_is_independent(kz):
    for r in kz.values():
        rec = r["recommendation"]
        s = rec["smc"]
        assert s["engine"] == "smc" and r["verdict_smc"] == s["verdict"]
        assert len(s["checklist"]) == 9 and s["explanation"]["score_breakdown"][0]["contribution"] == 50
        assert all(st["kind"] in {"order_block", "fvg", "bos_retest", "weekly_ob"} for st in s["setups"])
    from cse_advisor.strategy.recommend import original_patterns
    assert not any(p["engine"] == "smc" for p in original_patterns(kz["2026-04-09"]["patterns"]))
