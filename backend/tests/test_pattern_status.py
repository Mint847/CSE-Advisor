"""A breakout is 'failed' only when price closes back through the invalidation within FAIL_WINDOW bars of it.

Before, triangles were stamped 'failed' if price ever came back (even years later) and doubles / head & shoulders /
flags could never fail, so 'confirmed' and 'failed' did not mean what they said."""
from cse_advisor.analysis.chart_patterns import FAIL_WINDOW, _Ctx, _doubles
from cse_advisor.analysis.pivots import zigzag

from test_engine import make_series, types

BASE = [(0, 112), (15, 95), (30, 106), (45, 95.5)]          # a double bottom's two troughs and neckline


def _db(extra, **kw):
    """The double-bottom detector on its own: a failed pattern scores lower than overlapping candidates and would
    lose the de-duplication step in detect_chart_patterns, which is not what is being tested here."""
    df = make_series(BASE + extra, lead_from=125, **kw)
    return [p for _, p in _doubles(_Ctx(df), zigzag(df, df["atr"], k=1.5)) if p.type == "double_bottom"]


def test_double_bottom_that_collapses_right_after_the_neckline_break_is_failed():
    found = _db([(52, 107.5), (58, 90)])
    assert found and found[0].status == "failed"
    assert "FAILED" in found[0].explanation and f"within {FAIL_WINDOW} bars" in found[0].explanation


def test_double_bottom_that_works_and_falls_apart_much_later_stays_confirmed():
    found = _db([(52, 107.5), (62, 114), (95, 88)])
    assert found and found[0].status == "confirmed", [p.status for p in found]
    assert "FAILED" not in found[0].explanation


def test_old_triangle_breakout_is_not_failed_by_a_later_collapse():
    pts = [(0, 90), (10, 100), (20, 92), (30, 100), (40, 95), (50, 100), (55, 97.5), (62, 108), (90, 113), (140, 80)]
    tri = types(make_series(pts, breakout_vol_at=58, lead_from=81)).get("ascending_triangle", [])
    assert tri and tri[0].status == "confirmed", [p.status for p in tri]


def test_triangle_that_breaks_and_immediately_reverses_is_failed():
    pts = [(0, 90), (10, 100), (20, 92), (30, 100), (40, 95), (50, 100), (55, 97.5), (62, 108), (66, 90)]
    tri = types(make_series(pts, breakout_vol_at=58, lead_from=81)).get("ascending_triangle", [])
    assert tri and tri[0].status == "failed"


def test_bull_flag_levels_are_ordered_and_it_can_fail():
    pts = [(0, 100), (8, 118), (20, 114), (26, 124)]
    flag = types(make_series(pts, lead_from=96, breakout_vol_at=21))["bull_flag"][0]
    lv = flag.levels
    assert lv["target"] > lv["breakout"] > lv["invalidation"]                  # the old fit put the breakout below the flag's own lows
    failed = types(make_series(pts + [(29, 100)], lead_from=96, breakout_vol_at=21)).get("bull_flag", [])
    assert failed and failed[0].status == "failed"
