"""Plan text must not contradict itself, and a breakout that has already run its course is not an entry any more."""
from cse_advisor.strategy.recommend import find_setups, plan_explain

from test_engine import make_series


def _plan(max_chase, zl=21.0, zh=21.4, ideal=21.1, rr=0.6):
    return {"setup": "breakout", "setup_name": "Double bottom", "why": "x", "buy_zone": {"low": zl, "high": zh}, "ideal": ideal,
            "ideal_note": "lower quarter of the buy zone", "max_chase": max_chase, "cost_per_side_pct": 1.32, "stop": 18.2,
            "stop_steps": ["start from the invalidation level"], "stop_pct": -13.7, "stop_net_pct": -16.0,
            "targets": [{"price": 23.0, "source": "measured move", "pct": 9.0, "net_pct": 6.0, "label": "T1"},
                        {"price": 24.5, "source": "resistance", "pct": 16.0, "net_pct": 13.1, "label": "T2"}],
            "reward_risk": rr, "zone_sources": None, "trigger": None, "time_stop_weeks": 6,
            "size": {"qty": 1000, "value": 21000, "risk_lkr": 10000, "risk_pct": 1.0, "capital": 1_000_000, "qty_by_risk": 1000,
                     "qty_by_cap": 9000, "qty_by_liquidity": 5000, "binding": "risk budget", "days_to_exit": 1.0}}


def _watch(plan):
    return plan_explain(plan, [], [], {"adv_volume_20": 100_000}, 0.95)["watch_next"]


def test_max_chase_below_the_zone_is_not_given_as_buy_advice():
    # RIL, 4 Nov 2026: "limit order at 21.10 (zone 21.00-21.40). Don't pay above 19.50" contradicted itself
    text = " ".join(_watch(_plan(max_chase=19.5)))
    assert "Don't pay above" not in text
    assert "No price inside the buy zone 21.00–21.40 gives reward:risk" in text and "19.50 or lower" in text


def test_max_chase_inside_the_zone_and_above_it():
    # the max-chase falls inside the zone, below the planned limit: the limit itself misses the required reward:risk
    inside = " ".join(_watch(_plan(max_chase=21.05, rr=1.7)))
    assert "reward:risk is only 1.70 there" in inside and "21.05 or lower" in inside and "Don't pay above" not in inside
    # the limit is within the max-chase: plain advice
    fine = " ".join(_watch(_plan(max_chase=22.0, rr=2.4)))
    assert "Don't pay above 22.00" in fine


def _breakout(df, level, target):
    return {"direction": "bullish", "category": "chart_pattern", "type": "ascending_triangle", "status": "confirmed", "id": "p1",
            "name": "Ascending triangle", "priority": 60, "tier": 1, "context": {"age_bars": 2},
            "levels": {"breakout": level, "target": target, "invalidation": level - 6}}


def test_a_breakout_that_ran_to_its_target_is_not_an_entry():
    df = make_series([(0, 100), (40, 140)])
    close, atr = float(df["close"].iloc[-1]), float(df["atr"].iloc[-1])
    trend = {"direction": 0, "state": "range"}
    # breakout level just under the close, target well above: a live entry
    live = find_setups(df, [_breakout(df, close - 0.5 * atr, close + 6 * atr)], [], trend)
    assert any(s["kind"] == "breakout" for s in live)
    # the close is at / beyond the pattern's own target: the move has played out
    done = find_setups(df, [_breakout(df, close - 0.5 * atr, close - 0.01)], [], trend)
    assert not any(s["kind"] == "breakout" for s in done)
    # more than 3 ATR past the breakout level: no longer a sensible entry either
    ran = find_setups(df, [_breakout(df, close - 3.5 * atr, close + 6 * atr)], [], trend)
    assert not any(s["kind"] == "breakout" for s in ran)
