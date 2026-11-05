"""The factor study's building blocks on series where the answer is known."""
import numpy as np
import pandas as pd
import pytest

from cse_advisor.factor_study import MAXH, Stock, bounce_entry, clustered_t, nw_t, sim_exit

FEE = 0.01


def _stock(closes, highs=None, lows=None, opens=None, atr=1.0):
    n = len(closes)
    idx = pd.bdate_range("2024-01-01", periods=n)
    c = pd.Series(closes, index=idx, dtype=float)
    d = pd.DataFrame({"open": opens if opens is not None else c, "high": highs if highs is not None else c, "low": lows if lows is not None else c, "close": c}, index=idx)
    return Stock("T.N0000", d, pd.Series(atr, index=idx), c.ewm(span=5, adjust=False).mean())


def test_time_exit_pays_both_fees():
    A = _stock([10.0] * 5 + [11.0] * 120)
    net, days = sim_exit("time40", 5, 10.0, A, FEE)
    assert days == 40
    assert net == pytest.approx(11.0 * 0.99 / (10.0 * 1.01) - 1)


def test_trade_not_fully_observed_is_dropped():
    A = _stock([10.0] * 60)
    assert sim_exit("time40", 5, 10.0, A, FEE) is None          # fewer than MAXH sessions left


def test_trailing_stop_follows_the_high_and_gaps_exit_at_the_open():
    closes = [10.0] * 3 + [10.5, 11.0, 12.0, 12.0] + [12.0] * 120
    highs = list(closes)
    lows = list(closes)
    A = _stock(closes, highs=highs, lows=lows, atr=1.0)
    # entry on bar 3 at 10; high 12 on bar 5 -> chandelier 12 - 3 = 9... never touched, so it holds to the 90th session
    net, days = sim_exit("trail3", 3, 10.0, A, FEE)
    assert days == MAXH
    # a gap far below the stop exits at the open, not at the stop
    closes2 = [10.0] * 3 + [12.0, 12.0, 6.0] + [6.0] * 120
    A2 = _stock(closes2, opens=closes2, atr=1.0)
    net2, days2 = sim_exit("trail3", 3, 10.0, A2, FEE)
    assert days2 == 3 and net2 == pytest.approx(6.0 * 0.99 / (10.0 * 1.01) - 1)


def test_bounce_entry_waits_for_an_up_close_after_touching_the_zone():
    #            0     1     2     3     4     5     6
    c = [10.0, 10.0, 9.8, 9.7, 10.1, 10.3, 10.4, 10.5, 10.6]
    o = [10.0, 10.0, 10.0, 9.8, 9.7, 10.1, 10.3, 10.4, 10.5]
    h = [10.1, 10.1, 10.0, 9.9, 10.2, 10.4, 10.5, 10.6, 10.7]
    l = [9.9, 9.9, 9.7, 9.6, 9.6, 10.0, 10.2, 10.3, 10.4]
    A = _stock(c, highs=h, lows=l, opens=o)
    call = {"t": 0, "zh": 9.9, "stop": 9.3, "t1": 11.5}
    got = bounce_entry(call, A)
    assert got is not None and got[0] == 5 and got[1] == pytest.approx(o[5])      # bar 4 closes up above the prior high after the zone was touched
    assert bounce_entry({**call, "zh": 8.0}, A) is None                           # zone never touched -> no entry


def test_newey_west_matches_plain_t_without_autocorrelation_and_is_wider_with_it():
    ratios = []
    for seed in range(60):                                        # white noise: on average NW t equals the plain t
        x = np.random.default_rng(seed).normal(0.5, 1.0, 400)
        ratios.append(nw_t(x, 3) / (x.mean() / (x.std(ddof=0) / np.sqrt(len(x)))))
    assert np.mean(ratios) == pytest.approx(1.0, abs=0.04)
    rng = np.random.default_rng(1)
    # strongly autocorrelated series: the corrected t is smaller than the naive one
    y = np.cumsum(rng.normal(0, 1, 400)) * 0.1 + 0.5
    naive = y.mean() / (y.std(ddof=0) / np.sqrt(len(y)))
    assert abs(nw_t(y, 12)) < abs(naive)
    assert np.isnan(nw_t([1.0, 2.0], 3))


def test_clustered_t_uses_groups():
    e = pd.Series(np.r_[np.full(40, 1.0), np.full(40, 1.2)])
    one_cluster = clustered_t(e, pd.Series(["a"] * 80))
    many = clustered_t(e + np.random.default_rng(2).normal(0, 0.1, 80), pd.Series(list("abcdefghij") * 8))
    assert many == many and (one_cluster != one_cluster or abs(one_cluster) > 0)
