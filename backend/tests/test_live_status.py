"""The live check compares the live price with the stored analysis' levels."""
from cse_advisor.live_status import live_overlay, pattern_status, plan_status

PLAN = {"buy_zone": {"low": 17.5, "high": 17.8}, "ideal": 17.6, "stop": 17.0, "trigger": None, "max_chase": 18.2,
        "targets": [{"price": 18.6}, {"price": 19.4}]}


def _pat(direction="bearish", status="confirmed", age=1, **lv):
    return {"id": "p1", "name": "Bear flag", "category": "chart_pattern", "direction": direction, "status": status,
            "levels": {"breakout": 101.0, "target": 77.0, "invalidation": 102.0, **lv}, "context": {"age_bars": age}}


def test_the_dock_case_bearish_flag_overtaken_by_a_jump():
    # DOCK 4 Nov 2026: the stored bear flag says target 77 / invalidation 102, the live price is 109
    st = pattern_status(_pat(), last=109.0, high=109.0, low=99.0)
    assert st["state"] == "invalidated" and "above its invalidation level 102.00" in st["text"] and "fails if the session closes" in st["text"]


def test_pattern_target_and_intact_and_old():
    assert pattern_status(_pat(), 95.0, 99.0, 94.0)["state"] == "intact"
    assert pattern_status(_pat(), 80.0, 99.0, 76.5)["state"] == "target"                  # the low touched 77
    assert pattern_status(_pat(age=40), 109.0, 109.0, 99.0) is None                       # history, not a signal
    assert pattern_status(_pat(status="failed"), 109.0, 109.0, 99.0) is None
    f = pattern_status(_pat("bullish", "forming", breakout=104.0, target=115.0, invalidation=95.0), 105.0, 105.0, 100.0)
    assert f["state"] == "breaking" and "only on a close" in f["text"]


def test_plan_states():
    assert plan_status(PLAN, 17.65, 17.9, 17.5)["state"] == "in_zone"
    assert plan_status(PLAN, 18.5, 18.6, 17.9)["state"] == "t1_reached"
    assert plan_status(PLAN, 18.4, 18.5, 17.9)["state"] == "above_max_chase"
    assert plan_status(PLAN, 17.1, 17.9, 16.9)["state"] == "stop_level_hit"
    assert plan_status(PLAN, 17.95, 18.0, 17.7)["state"] == "above_zone"
    assert plan_status(PLAN, 17.3, 17.9, 17.2)["state"] == "below_zone"
    trig = {**PLAN, "trigger": 17.9, "buy_zone": {"low": 17.9, "high": 18.1}}
    assert plan_status(trig, 17.8, 17.85, 17.6)["state"] == "waiting_trigger"
    assert plan_status(trig, 18.0, 18.05, 17.7)["state"] == "trigger_crossed"


def test_overlay_needs_a_quote_from_a_later_session():
    a = {"date": "2026-11-03", "recommendation": {"close": 98.7, "original": {"plan": PLAN}}, "patterns": [_pat()]}
    q = {"time": "2026-11-04", "close": 109.0, "high": 109.0, "low": 99.0, "last_trade": "14:29:50", "open": 99.0}
    o = live_overlay(a, q)
    assert o["patterns"]["p1"]["state"] == "invalidated" and o["plans"]["original"]["state"] == "stop_level_hit" or o["plans"]["original"]["state"]
    assert o["change_pct"] > 10 and any("invalidation" in x for x in o["summary"])
    assert live_overlay(a, {**q, "time": "2026-11-03"}) is None          # same session as the analysis: nothing new
    assert live_overlay(a, None) is None
