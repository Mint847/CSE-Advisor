"""Replay the app on the dates of the SLEG KZOO / JKH / RIL posts and check it finds the
same levels and calls (tolerance: 2% or one tick). Fixtures are public cse.lk price data;
expected values are the analyst's numbers, typed in by hand."""
from __future__ import annotations

import pytest

from replay import run


def near(x: float, target: float, tol: float = 0.02) -> bool:
    return abs(x - target) <= max(tol * target, 0.1)


def fib_price(r: dict, ratio: float) -> float:
    return next(x["price"] for x in r["levels"]["fib"]["levels"] if x["ratio"] == ratio)


def zone_overlaps(z: dict | None, lo: float, hi: float) -> bool:
    return bool(z) and z["low"] <= hi and z["high"] >= lo


def all_zone_ranges(r: dict) -> list[tuple[float, float]]:
    return [(z["low"], z["high"]) for z in r["levels"]["zones"]]


@pytest.fixture(scope="module")
def kz():
    return {d: run("KZOO", d) for d in ("2026-02-20", "2026-03-03", "2026-03-05", "2026-03-20", "2026-03-26",
                                        "2026-04-09", "2026-04-13", "2026-10-29")}


def test_kzoo_feb20_fib_and_extension(kz):
    r = kz["2026-02-20"]
    # analyst: Fib 0.236 = 21.6, 0.382 = 18.6, 0.5 = 16.6, 0.618 = 14.7, 0.786 = 12.4
    for ratio, want in ((0.236, 21.6), (0.382, 18.6), (0.5, 16.6), (0.618, 14.7), (0.786, 12.4)):
        assert near(fib_price(r, ratio), want), (ratio, fib_price(r, ratio))
    assert r["recommendation"]["analyst"]["signals"]["extension"]["flag"]          # "way over-extended", RSI past 90
    assert r["verdict_analyst"] != "BUY"
    assert any(p["type"] == "bearish_rsi_divergence" for p in r["patterns"])


def test_kzoo_mar3_terp(kz):
    corp = kz["2026-03-03"]["recommendation"]["analyst"]["corporate"]
    assert corp["phase"] == "pre_xr" and near(corp["terp_today"], 11.567, 0.001)
    assert kz["2026-03-03"]["recommendation"]["analyst"]["signals"]["volume"]["down_volume_rising"]   # "selling volumes rising"


def test_kzoo_mar5_avoid_volatile(kz):
    r = kz["2026-03-05"]
    assert r["recommendation"]["analyst"]["signals"]["volatility"]["guard"] and r["verdict_analyst"] == "AVOID"


def test_kzoo_post_xr_fib_matches_april_chart(kz):
    r = kz["2026-04-09"]
    # analyst (adjusted chart): 0.236 12.1, 0.382 10.4, 0.5 9.3, 0.618 8.2, 0.66 7.9, 0.786 6.9
    for ratio, want in ((0.236, 12.1), (0.382, 10.4), (0.5, 9.3), (0.618, 8.2), (0.66, 7.9), (0.786, 6.9)):
        assert near(fib_price(r, ratio), want), (ratio, fib_price(r, ratio))


def test_kzoo_mar20_support_and_pre_rights_value(kz):
    r = kz["2026-03-20"]
    entry_zones = [(e["buy_zone"]["low"], e["buy_zone"]["high"]) for e in r["recommendation"]["analyst"]["entries"]]
    assert any(lo <= 9.8 <= hi + 0.3 for lo, hi in all_zone_ranges(r) + entry_zones)   # "held the 9.8 support"
    assert near(r["recommendation"]["analyst"]["corporate"]["pre_rights_equivalent"], 16.0, 0.03)  # "10 = Rs 16 pre-rights"


def test_kzoo_april_buy_zones_and_overhang(kz):
    r = kz["2026-04-09"]
    conf = r["levels"]["confluence"]
    assert zone_overlaps(conf["zone1"], 9.3, 9.8)                          # Buy zone 1 (Fib 0.5 + 9.8 support)
    assert zone_overlaps(conf["zone2"], 6.3, 6.9 + 0.15)                   # Buy zone 2 (Fib 0.786 + 6.3 support)
    assert r["recommendation"]["analyst"]["corporate"]["phase"] == "overhang"        # rights shares reaching CDS accounts
    tps = [t for e in r["recommendation"]["analyst"]["entries"] for t in (x["price"] for x in e["targets"])] + \
          [z["low"] for z in conf["sell_zones"]] + [z["high"] for z in conf["sell_zones"]]
    assert any(near(t, 13.1, 0.03) for t in tps)                           # TP 13.1 resistance
    labels = [e["entry_label"] for e in r["recommendation"]["analyst"]["entries"]]
    assert "Entry 1" in labels and "Zone 2" in labels


def test_kzoo_apr13_in_buy_zone_1(kz):
    r = kz["2026-04-13"]
    assert zone_overlaps(r["levels"]["confluence"]["zone1"], 9.3, 9.8) or any(
        e["entry_label"] == "Entry 2" and e["buy_zone"]["low"] <= 9.8 for e in r["recommendation"]["analyst"]["entries"])


def test_kzoo_oct29_base_retest_watch(kz):
    r = kz["2026-10-29"]
    assert r["verdict_analyst"] == "WATCH"
    assert any(s["kind"] == "base_retest" for s in r["recommendation"]["analyst"]["setups"])
    assert any(lo <= 6.4 and hi >= 5.7 for lo, hi in all_zone_ranges(r))  # base support 5.7-6.4
    assert r["recommendation"]["analyst"]["signals"]["momentum"]["oversold_turn"]


def test_jkh_levels():
    jun = run("JKH", "2026-06-15")
    assert any(lo - 0.3 <= 20.0 <= hi + 0.3 for lo, hi in all_zone_ranges(jun))       # "20 a support again"
    assert any(z["low"] - 0.3 <= 21.8 <= z["high"] + 0.3 for z in jun["levels"]["confluence"]["sell_zones"])  # 21.8 resistance
    jul = run("JKH", "2026-07-27")
    assert any(lo <= 19.6 <= hi + 0.1 for lo, hi in all_zone_ranges(jul))             # value-area low 19.60
    assert zone_overlaps(jul["levels"]["confluence"]["zone1"], 17.3, 18.0)            # highlighted zone below
    jan = run("JKH", "2026-01-12")
    assert any(e["setup"] == "trendline_break" for e in jan["recommendation"]["analyst"]["entries"])  # breakout entry ~22


def test_ril_levels():
    jun = run("RIL", "2026-06-01")
    ap = jun["levels"]["anchored_profile"]
    assert near(ap["poc"], 28.0, 0.04)                                   # "28 is the major volume resistance since the peak"
    assert any(z["low"] - 0.5 <= 31.3 <= z["high"] + 0.5 for z in jun["levels"]["confluence"]["sell_zones"])  # TP1 31.3
    jul = run("RIL", "2026-07-28")
    assert any(lo <= 24.0 and hi >= 23.0 for lo, hi in all_zone_ranges(jul))          # 23-24 support
    oct_ = run("RIL", "2026-10-16")
    assert oct_["recommendation"]["analyst"]["signals"]["drawdown"]["pct"] <= -45                 # ~50% drop from the peak
