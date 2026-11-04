"""Unit tests for corporate actions, log-scale Fibonacci, flips, confluence and signals."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from cse_advisor.analysis import indicators
from cse_advisor.analysis.confluence import cluster, zones_for_plan
from cse_advisor.analysis.corporate_actions import (
    adjust_prices, parse_proportion, parse_rights_price, parse_split, phases, pre_rights_equivalent,
    price_from_listing, terp,
)
from cse_advisor.analysis.fibonacci import fibonacci, level_price
from cse_advisor.analysis.signals import compute_signals, intraday_volume_at_price


# ------------------------------------------------------------------ corporate actions
def test_terp_and_factor_kzoo():
    # KZOO 3 Mar 2026: close 20.70, rights 2 new for 1 at Rs 7  ->  TERP 11.567, factor 0.5588
    t = terp(20.7, 2, 1, 7)
    assert t == pytest.approx(11.567, abs=1e-3)
    assert t / 20.7 == pytest.approx(0.5588, abs=1e-4)
    assert pre_rights_equivalent(10.0, 2, 1, 7) == pytest.approx(16.0)


def test_parsers():
    assert parse_proportion("Two (2) new Ordinary Voting Shares for every One (01) ordinary voting share") == (2.0, 1.0)
    assert parse_proportion("1 new share for every 5 shares held") == (1.0, 5.0)
    assert parse_split("each share to be subdivided into ten (10) shares") == 10.0
    assert parse_rights_price("at a consideration of Rs. 7.00 per share") == 7.0
    shares, price = price_from_listing("Allotted/Listed 614,156,734 614,156,734 Raised (Rs.) 4,299,097,138/- 4,299,097,138/-")
    assert shares == 614_156_734 and price == pytest.approx(7.0, abs=1e-3)


def _frame(closes, start="2026-01-01"):
    idx = pd.bdate_range(start, periods=len(closes))
    c = np.array(closes, float)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1000.0,
                         "turnover": c * 1000, "trades": None, "open_estimated": True, "source": "history"}, index=idx)


def test_adjust_prices_rights():
    raw = _frame([20.0] * 10 + [11.5] * 10)
    ex = raw.index[10].date()
    adj, acts = adjust_prices(raw, [{"kind": "rights", "ex_date": ex, "ratio_new": 2, "ratio_old": 1, "price": 7.0}])
    f = terp(20.0, 2, 1, 7) / 20.0
    assert acts[0]["applied"] and acts[0]["factor"] == pytest.approx(f)
    assert adj["close"].iloc[0] == pytest.approx(20.0 * f)
    assert adj["close"].iloc[-1] == 11.5                      # post-XR untouched
    assert adj["volume"].iloc[0] == pytest.approx(1000 / f)
    assert adj["raw_close"].iloc[0] == 20.0
    # XR-day estimated open uses the ADJUSTED previous close (TERP), not the old 20.0
    assert adj["open"].iloc[10] <= adj["high"].iloc[10]
    assert adj["open"].iloc[10] == pytest.approx(min(max(20.0 * f, adj["low"].iloc[10]), adj["high"].iloc[10]))


def test_adjust_without_price_is_skipped():
    raw = _frame([20.0] * 10 + [11.5] * 10)
    adj, acts = adjust_prices(raw, [{"kind": "rights", "ex_date": raw.index[10].date(), "ratio_new": 2, "ratio_old": 1, "price": None}])
    assert not acts[0]["applied"] and acts[0]["reason"] == "subscription price unknown"
    assert adj["close"].iloc[0] == 20.0


def test_phases_pre_xr_and_overhang():
    a = [{"kind": "rights", "ex_date": date(2026, 3, 4), "ratio_new": 2, "ratio_old": 1, "price": 7.0, "listing_date": date(2026, 4, 10)}]
    pre = phases(a, date(2026, 3, 3), 20.7)
    assert pre["phase"] == "pre_xr" and pre["terp_today"] == pytest.approx(11.567, abs=1e-3)
    post = phases(a, date(2026, 4, 9), 12.0)
    assert post["phase"] == "overhang" and post["score_adjustment"] < 0
    assert post["pre_rights_equivalent"] == pytest.approx(22.0)
    late = phases(a, date(2026, 6, 30), 9.0)
    assert late["phase"] is None


# ------------------------------------------------------------------ fibonacci
def test_log_fib_matches_ril_levels():
    # RIL: a 6.2 -> 38.8 leg on a log scale gives 0.236 / 0.5 / 0.786 = 25.2 / 15.5 / 9.2
    lo, hi = 6.21, 38.8
    assert level_price(lo, hi, 0.236, "up") == pytest.approx(25.2, abs=0.1)
    assert level_price(lo, hi, 0.5, "up") == pytest.approx(15.5, abs=0.1)
    assert level_price(lo, hi, 0.786, "up") == pytest.approx(9.2, abs=0.1)


def test_fib_ignores_freak_wick():
    closes = list(np.linspace(10, 15, 60)) + list(np.linspace(15, 11, 30))
    df = _frame(closes)
    df["open"] = df["close"].shift().fillna(df["close"])
    df.iloc[70, df.columns.get_loc("high")] = 30.0           # one-bar spike far beyond the body
    df = indicators.add_all(df)
    fib = fibonacci(df)
    assert fib and fib["high"]["price"] < 16                  # anchored on the real swing high, not the 30 wick


# ------------------------------------------------------------------ confluence
def test_confluence_zones_order():
    cands = [{"price": 9.3, "weight": 2.0, "kind": "fib", "text": "Fib 0.5"},
             {"price": 9.8, "low": 9.6, "high": 9.9, "weight": 2.0, "kind": "sr", "text": "support 9.8"},
             {"price": 6.9, "weight": 2.0, "kind": "fib", "text": "Fib 0.786"},
             {"price": 6.4, "low": 6.3, "high": 6.5, "weight": 2.6, "kind": "sr", "text": "support 6.3"},
             {"price": 13.1, "low": 13.0, "high": 13.2, "weight": 2.0, "kind": "sr", "text": "resistance 13.1"}]
    cl = cluster(cands, close=12.0, atr=0.5)
    z = zones_for_plan(cl, 12.0, 0.5)
    assert z["zone1"]["low"] <= 9.3 and z["zone1"]["high"] >= 9.8
    assert z["zone2"]["low"] <= 6.4 and z["zone2"]["high"] >= 6.9
    assert z["sell_zones"][0]["low"] <= 13.1 <= z["sell_zones"][0]["high"] + 0.01


# ------------------------------------------------------------------ signals
def test_volatility_guard_whipsaw():
    closes = [10.0] * 40
    df = _frame(closes)
    df.iloc[-1, df.columns.get_loc("high")] = 17.0            # huge upper wick, close unchanged
    df = indicators.add_all(df)
    s = compute_signals(df)
    assert s["volatility"]["guard"]


def test_extension_flag():
    closes = list(np.linspace(10, 11, 30)) + list(np.linspace(11, 20, 15))
    df = indicators.add_all(_frame(closes))
    s = compute_signals(df)
    assert s["extension"]["flag"]


def test_intraday_vap():
    v = intraday_volume_at_price([{"p": 28.0, "q": 2_540_096}, {"p": 28.5, "q": 529_716}, {"p": 27.9, "q": 253_791}])
    assert v["top"]["price"] == 28.0 and v["top"]["share"] > 0.7
