"""Every bullish pattern and its price-mirrored bearish twin must be detected symmetrically, with sane levels.

A mirrored series (prices flipped around a constant) is the same chart upside down, so a detector that finds one
but not the other has a sign bug. This caught inverse head & shoulders being rejected by the shoulder check."""
import pytest

from test_engine import make_series, types

M = 120.0

# name: (points, lead_from, breakout_vol_at, bullish type, bearish twin)
PAIRS = {
    "double bottom / top": ([(0, 112), (15, 95), (30, 106), (45, 95.5), (62, 112)], 125, None, "double_bottom", "double_top"),
    "head and shoulders (inverse / top)": ([(0, 88), (10, 100), (20, 94), (30, 108), (40, 94.5), (50, 100.5), (62, 86)], 80, None,
                                           "head_and_shoulders", "inverse_head_and_shoulders"),
    "ascending / descending triangle": ([(0, 90), (10, 100), (20, 92), (30, 100), (40, 95), (50, 100), (55, 97.5), (62, 108)], 81, 58,
                                        "ascending_triangle", "descending_triangle"),
    "bull / bear flag": ([(0, 100), (8, 118), (20, 114), (26, 124)], 96, 21, "bull_flag", "bear_flag"),
    "falling / rising wedge": ([(0, 110), (10, 100), (18, 106), (28, 98), (34, 102), (44, 96), (48, 99), (56, 104)], 118, None,
                               "falling_wedge", "rising_wedge"),
}


def _flip(pts, lead_from):
    return [(i, 2 * M - p) for i, p in pts], 2 * M - lead_from


def _ordered(p, bullish):
    lv = p.levels
    if not {"breakout", "target", "invalidation"} <= set(lv):
        return True
    return lv["target"] > lv["breakout"] > lv["invalidation"] if bullish else lv["target"] < lv["breakout"] < lv["invalidation"]


@pytest.mark.parametrize("name", list(PAIRS))
def test_mirrored_twin_is_detected(name):
    pts, lead, vol, bull_t, bear_t = PAIRS[name]
    fp, fl = _flip(pts, lead)
    a = types(make_series(pts, lead_from=lead, breakout_vol_at=vol))
    b = types(make_series(fp, lead_from=fl, breakout_vol_at=vol))
    assert bull_t in a, f"{bull_t} missing; found {sorted(a)}"
    assert bear_t in b, f"{bear_t} missing from the mirrored chart; found {sorted(b)}"
    pa, pb = a[bull_t][0], b[bear_t][0]
    assert pa.status == pb.status == "confirmed"
    # a head & shoulders top is bearish and its inverse is bullish, whichever of the pair is the 'original'
    assert pa.direction != pb.direction
    assert _ordered(pa, pa.direction == "bullish") and _ordered(pb, pb.direction == "bullish")


def test_inverse_head_and_shoulders_levels():
    pts, lead, _, _, _ = PAIRS["head and shoulders (inverse / top)"]
    fp, fl = _flip(pts, lead)
    p = types(make_series(fp, lead_from=fl))["inverse_head_and_shoulders"][0]
    lv = p.levels
    assert p.direction == "bullish" and lv["target"] > lv["breakout"] > lv["invalidation"]
    assert "Neckline broken" in p.explanation
