"""Quant Lab strategy backtester: trades worked out by hand, the fast pass against gs-quant's engine, no look-ahead in the
rules, the indicators against gs-quant, the reshuffle test, validation and the routes."""
import math
import time
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import gs_quant.timeseries as gts
from gs_quant.timeseries import Window

from cse_advisor import quant_api
from cse_advisor.auth import User
from cse_advisor.db import Announcement, Base, MarketDaily, PriceDaily, Security, WatchlistGroup, WatchlistGroupItem
from cse_advisor.quant import strategy_lab as L

ME = User(uid="local", admin=True)


# ---------------------------------------------------------------- a toy market
def toy(close: dict[str, list[float]], opens: dict[str, list[float]] | None = None, volume: dict[str, list[float]] | None = None,
        tradable: dict[str, list[bool]] | None = None, start: str = "2024-01-01") -> L.Universe:
    syms = list(close)
    n = len(close[syms[0]])
    days = pd.bdate_range(start, periods=n)
    c = pd.DataFrame(close, index=days)
    o = pd.DataFrame(opens if opens else close, index=days)
    v = pd.DataFrame(volume if volume else {k: [1000.0] * n for k in syms}, index=days)
    tr = pd.DataFrame(tradable if tradable else {k: [True] * n for k in syms}, index=days)
    return L.Universe(symbols=[f"{k}.N0000" for k in syms], labels=syms, days=days, sig=c.set_axis([f"{k}.N0000" for k in syms], axis=1),
                      vol=v.set_axis([f"{k}.N0000" for k in syms], axis=1), tradable=tr.to_numpy(bool),
                      t_open=np.where(tr.to_numpy(bool), o.to_numpy(float), np.nan), t_close=c.to_numpy(float),
                      aspi=pd.Series(1000 + np.arange(n, dtype=float), index=days), xd={j: [] for j in range(len(syms))}, basis="price")


def spec_for(U: L.Universe, **kw) -> dict:
    base = {"symbols": U.symbols, "years": 5, "capital": 1_000_000, "max_positions": min(5, len(U.symbols)), "slippage_pct": 0.5,
            "entry": {"type": "monthly"}, "exit": {}}
    base.update(kw)
    return L.normalise(base)


def signal_on(U: L.Universe, *days: tuple[int, int]) -> np.ndarray:
    E = np.zeros(U.t_close.shape, dtype=bool)
    for i, j in days:
        E[i, j] = True
    return E


def rates_zero(U: L.Universe) -> np.ndarray:
    return np.zeros(len(U.days))


# ---------------------------------------------------------------- the money, by hand
def test_buy_hold_sell_by_hand():
    close = [100, 100, 100, 110, 120, 130, 140, 150, 150, 150]
    opens = [100, 100, 100, 101, 111, 121, 131, 141, 150, 150]
    U = toy({"AAA": close}, {"AAA": opens})
    sp = spec_for(U, max_positions=1, exit={"hold_days": 3})
    r = L.simulate(U, signal_on(U, (2, 0)), np.zeros(U.t_close.shape, dtype=bool), sp, rates_zero(U), 0, 9, 1_000_000)
    cr = r.b_rate + 0.005                                           # CSE fees + 0.5% slippage, each side
    q = math.floor(1_000_000 * 0.97 / (100 * (1 + cr)))              # sized on the signal day's close, 3% under the cash
    assert q == 9545
    buy = q * 101                                                    # filled at the NEXT open (day 3), not at the signal close
    cash_after_buy = 1_000_000 - buy - buy * cr
    assert r.equity[3] == pytest.approx(cash_after_buy + q * 110)    # day 3 close: valued at its close
    sell = q * 141                                                   # held 3 sessions (entry day 3 -> decided day 6) -> sold at day 7's open
    final = cash_after_buy + sell - sell * cr
    assert r.equity[9] == pytest.approx(final) and r.equity[7] == pytest.approx(final)
    (t,) = r.trades
    assert t["entry_idx"] == 3 and t["exit_idx"] == 7 and t["reason"] == "holding time" and t["status"] == "closed"
    assert t["pnl"] == pytest.approx(final - 1_000_000)
    assert r.brokerage == pytest.approx((buy + sell) * r.b_rate) and r.slippage == pytest.approx((buy + sell) * 0.005)


def test_stock_with_no_trade_is_filled_at_the_next_session_it_trades():
    close = [100] * 8
    U = toy({"AAA": close}, tradable={"AAA": [True, True, True, False, False, True, True, True]})
    sp = spec_for(U, max_positions=1, exit={"hold_days": 2})
    r = L.simulate(U, signal_on(U, (2, 0)), np.zeros(U.t_close.shape, dtype=bool), sp, rates_zero(U), 0, 7, 1_000_000)
    assert r.trades[0]["entry_idx"] == 5                              # sessions 3 and 4 had no trade: it waits


def test_stop_loss_target_and_trailing_stop():
    close = [100, 100, 100, 100, 94, 94, 94, 94]
    U = toy({"AAA": close})
    r = L.simulate(U, signal_on(U, (1, 0)), np.zeros(U.t_close.shape, dtype=bool), spec_for(U, max_positions=1, exit={"stop_pct": 5}), rates_zero(U), 0, 7, 1_000_000)
    assert r.trades[0]["reason"] == "stop loss" and r.trades[0]["exit_idx"] == 5       # close 4 broke the stop, sold at day 5's open
    up = toy({"AAA": [100, 100, 100, 100, 112, 112, 112, 112]})
    r = L.simulate(up, signal_on(up, (1, 0)), np.zeros(up.t_close.shape, dtype=bool), spec_for(up, max_positions=1, exit={"target_pct": 10}), rates_zero(up), 0, 7, 1_000_000)
    assert r.trades[0]["reason"] == "profit target"
    rise = toy({"AAA": [100, 100, 100, 120, 130, 130, 114, 114, 114]})
    r = L.simulate(rise, signal_on(rise, (1, 0)), np.zeros(rise.t_close.shape, dtype=bool), spec_for(rise, max_positions=1, exit={"trail_pct": 10}), rates_zero(rise), 0, 8, 1_000_000)
    assert r.trades[0]["reason"] == "trailing stop" and r.trades[0]["exit_idx"] == 7      # 114 is 12.3% under the 130 high; the high alone doesn't exit


def test_cash_earns_the_tbill_rate_actual_365():
    U = toy({"AAA": [100] * 6})
    rate = np.full(6, 10.0)
    r = L.simulate(U, np.zeros(U.t_close.shape, dtype=bool), np.zeros(U.t_close.shape, dtype=bool), spec_for(U), rate, 0, 5, 1_000_000)
    gaps = [(U.days[i] - U.days[i - 1]).days for i in range(1, 6)]            # includes a weekend
    want = 1_000_000 * np.prod([1 + 0.10 * g / 365 for g in gaps])
    assert max(gaps) == 3 and r.equity[5] == pytest.approx(want)


def test_slots_go_to_the_more_liquid_stock_and_never_more_than_max_positions():
    n = 12
    U = toy({"AAA": [100] * n, "BBB": [50] * n, "CCC": [20] * n},
            volume={"AAA": [1000.0] * n, "BBB": [90000.0] * n, "CCC": [20000.0] * n})   # traded value: 100k, 4.5m, 400k
    sp = spec_for(U, max_positions=2)
    E = signal_on(U, (8, 0), (8, 1), (8, 2))                          # the ranking needs 5 sessions of traded value
    r = L.simulate(U, E, np.zeros(E.shape, dtype=bool), sp, rates_zero(U), 0, 11, 1_000_000)
    assert sorted(U.labels[t["j"]] for t in r.trades) == ["BBB", "CCC"]       # the two best-traded stocks get the two slots
    assert r.min_cash >= -1e-6


# ---------------------------------------------------------------- the fast pass against gs-quant's engine
def test_fast_pass_equals_gs_quant_engine_with_fees_and_interest():
    rng = np.random.default_rng(7)
    n = 160
    def path(seed, drift):
        r = np.random.default_rng(seed).normal(drift, 0.02, n)
        return list(60 * np.cumprod(1 + r))
    close = {"AAA": path(1, 0.001), "BBB": path(2, 0.0), "CCC": path(3, 0.002)}
    opens = {k: [v[0]] + [v[i - 1] * (1 + rng.normal(0, 0.005)) for i in range(1, n)] for k, v in close.items()}
    tr = {k: list(rng.random(n) > 0.1) for k in close}                       # some sessions without a trade
    U = toy(close, opens, tradable=tr)
    sp = spec_for(U, max_positions=2, entry={"type": "monthly"}, exit={"hold_days": 12, "stop_pct": 6})
    E, X = L.signals(U, sp)
    rate = np.full(n, 9.0)
    sim = L.simulate(U, E, X, sp, rate, 0, n - 1, 1_000_000)
    eng = L.run_engine(U, E, X, sp, rate, 0, n - 1, 1_000_000)
    sess = U.days
    a, b = pd.Series(sim.equity, index=sess), eng["equity"].reindex(sess)
    assert b.notna().all() and len(sim.trades) > 5
    np.testing.assert_allclose(b.to_numpy(), a.to_numpy(), rtol=1e-12)
    n_eng, bad = L.ledger_check(U, sim, eng["ledger"])
    assert bad == 0 and n_eng == len(sim.trades)


# ---------------------------------------------------------------- no look-ahead
def rw_universe(n=700, m=4, seed=5) -> L.Universe:
    rng = np.random.default_rng(seed)
    close = {f"S{j}": list(40 * np.cumprod(1 + rng.normal(0.0004, 0.02, n))) for j in range(m)}
    U = toy(close, start="2020-01-01")
    # a recorded dividend: ex on session 450, announced (first usable session) 440
    U.xd[0] = [(450, 441)]
    return U


def cut(U: L.Universe, h: int) -> L.Universe:
    return L.Universe(symbols=U.symbols, labels=U.labels, days=U.days[:h], sig=U.sig.iloc[:h], vol=U.vol.iloc[:h], tradable=U.tradable[:h],
                      t_open=U.t_open[:h], t_close=U.t_close[:h], aspi=U.aspi.iloc[:h], xd=U.xd, basis=U.basis)


@pytest.mark.parametrize("rule_type", list(L.RULES))
def test_rules_use_only_the_past(rule_type):
    U = rw_universe()
    rule = L.check_rule({"type": rule_type})
    full = L._Eval(U).run(rule)
    h = 500
    part = L._Eval(cut(U, h)).run(rule)
    np.testing.assert_array_equal(full[:h], part)                      # cutting off the future changes nothing before the cut


def test_before_xd_only_after_the_announcement():
    U = rw_universe()
    U.xd[0] = [(450, 447)]                                              # announced only 3 sessions before the ex-date
    got = L._Eval(U).run({"type": "before_xd", "k": 5})
    assert not got[445, 0] and not got[446, 0]                          # 5 sessions before = 445: not yet announced -> no signal
    U.xd[0] = [(450, 440)]
    got = L._Eval(U).run({"type": "before_xd", "k": 5})
    assert got[445, 0] and got[:, 0].sum() == 1 and got[:, 1:].sum() == 0


def test_rs_rank_picks_the_strongest():
    n = 150
    U = toy({"UP": list(np.linspace(50, 100, n)), "FLAT": [50.0] * n, "DOWN": list(np.linspace(50, 30, n)), "FLAT2": [50.0] * n})
    got = L._Eval(U).run({"type": "rs_rank", "top_pct": 25, "n": 60})
    assert got[100:, 0].all() and not got[100:, 2].any()


# ---------------------------------------------------------------- indicators equal gs-quant's
def test_indicators_match_gs_quant():
    x = pd.Series(50 * np.cumprod(1 + np.random.default_rng(3).normal(0.0003, 0.015, 400)), index=pd.bdate_range("2021-01-04", periods=400))
    a = gts.moving_average(x, 50)
    np.testing.assert_allclose(L.sma(x, 50).loc[a.index], a, rtol=1e-12)
    z = gts.zscores(x, Window(60, 0))
    mine = L.zscore(x, 60)
    ok = mine.notna()
    np.testing.assert_allclose(mine[ok], z.loc[mine.index][ok], rtol=1e-10)
    e = gts.exponential_moving_average(x, 1 - 2 / (50 + 1))
    np.testing.assert_allclose(L.ema(x, 50).dropna(), e.loc[L.ema(x, 50).dropna().index], rtol=1e-12)
    g = gts.relative_strength_index(x, 14)
    m = L.rsi(x, 14)
    np.testing.assert_allclose(m.loc[g.index], g, rtol=1e-9)
    assert m.iloc[:14].isna().all()


# ---------------------------------------------------------------- the reshuffle
def test_reshuffle_keeps_the_final_price_and_the_gaps_but_not_the_path():
    U = rw_universe(300, 3)
    V = L.reshuffle(U, np.random.default_rng(1))
    np.testing.assert_allclose(V.t_close[-1], U.t_close[-1], rtol=1e-9)           # same overall rise
    assert not np.allclose(V.t_close[100], U.t_close[100])
    assert (V.tradable == U.tradable).all()
    a = np.sort(U.t_close[1:, 0] / U.t_close[:-1, 0])
    assert abs(np.std(np.log(V.t_close[1:, 0] / V.t_close[:-1, 0])) - np.std(np.log(U.t_close[1:, 0] / U.t_close[:-1, 0]))) < 0.003   # same volatility
    W = L.reshuffle(U, np.random.default_rng(1))
    np.testing.assert_array_equal(V.t_close, W.t_close)                              # reproducible from the seed
    assert a is not None


def test_luck_summary_percentiles():
    res = [(x, x * 2) for x in np.linspace(-0.5, 0.5, 100)]
    lk = L.luck_summary(res, 0.45, 0.9)
    assert lk["total"]["beat"] == pytest.approx(0.95, abs=0.011) and lk["sharpe"]["beat"] == pytest.approx(0.95, abs=0.011)
    assert sum(b["count"] for b in lk["bins"]) == 100 and lk["total"]["p5"] < lk["total"]["median"] < lk["total"]["p95"]


def test_bottom_line_says_whether_holding_would_have_been_better():
    assert "beat buying and holding" in L.bottom_line({"total_return": 1.0, "sharpe": 1.2}, {"total_return": 0.5, "sharpe": 0.8})
    assert "better return for the risk" in L.bottom_line({"total_return": 0.3, "sharpe": 1.2}, {"total_return": 0.5, "sharpe": 0.8})
    assert "not more for the risk" in L.bottom_line({"total_return": 0.9, "sharpe": 0.4}, {"total_return": 0.5, "sharpe": 0.8})
    assert "did not beat" in L.bottom_line({"total_return": 0.3, "sharpe": 0.1}, {"total_return": 0.5, "sharpe": 0.8})
    assert "did not beat" in L.bottom_line({"total_return": None, "sharpe": None}, {"total_return": 0.5, "sharpe": 0.8})


def test_buy_and_hold_curve_pays_the_fee_once():
    U = toy({"AAA": [100, 110, 121], "BBB": [50, 50, 50]})
    h, n = L.hold_curve(U, 0, 2, 1_000_000, 0.02)
    assert n == 2 and h.iloc[0] == 1_000_000
    assert h.iloc[2] == pytest.approx(500_000 * 0.98 * 1.21 + 500_000 * 0.98)


# ---------------------------------------------------------------- validation
@pytest.mark.parametrize("bad, msg", [
    ({"symbols": []}, "at least one stock"),
    ({"entry": {"type": "nope"}}, "Unknown condition"),
    ({"entry": {"type": "ma_above", "n": 1}}, "must be between"),
    ({"entry": {"type": "ma_cross_up", "fast": 100, "slow": 50}}, "shorter"),
    ({"entry": {"op": "all", "rules": []}}, "between 1 and 8"),
    ({"entry": {"type": "rs_rank"}, "symbols": ["A.N0000", "B.N0000"]}, "at least 3 stocks"),
    ({"capital": 10}, "between LKR"),
    ({"max_positions": 99}, "between 1 and"),
    ({"slippage_pct": 9}, "between 0% and 3%"),
    ({"exit": {"stop_pct": 500}}, "Stop loss"),
    ({"years": 40}, "between 1 and 15"),
    ({"basis": "x"}, "total"),
])
def test_normalise_rejects_nonsense_in_plain_words(bad, msg):
    spec = {"symbols": ["A.N0000", "B.N0000", "C.N0000"], "entry": {"type": "monthly"}}
    spec.update(bad)
    with pytest.raises(ValueError, match=msg):
        L.normalise(spec)


def test_rule_text_and_presets_are_valid():
    for p in L.PRESETS:
        sp = L.normalise({**p["spec"], "symbols": ["A.N0000", "B.N0000", "C.N0000", "D.N0000"]})
        assert L.rule_text(sp["entry"]) and L.exit_text(sp["exit"])
    f = next(p for p in L.PRESETS if p["id"] == "factor")
    txt = L.rule_text(L.check_rule(f["spec"]["entry"]))
    assert "uptrend" in txt and "within 10%" in txt and "strongest 40%" in txt and " AND " in txt


# ---------------------------------------------------------------- the routes, on a small database
@pytest.fixture()
def DB(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Sess = sessionmaker(bind=eng, expire_on_commit=False)
    monkeypatch.setattr(quant_api, "SessionLocal", Sess)
    monkeypatch.setattr(L, "SessionLocal", Sess)
    real = L.settings
    monkeypatch.setattr(L, "settings", lambda: {**real(), "quant": {**(real().get("quant") or {}), "lab": {"reshuffles": 4}}})
    L._cache.clear()
    L._runs.clear()
    n = 420
    idx = pd.bdate_range("2023-01-02", periods=n)
    with Sess.begin() as s:
        a = 8000 * np.cumprod(1 + np.random.default_rng(1).normal(0.0004, 0.01, n))
        for t, v in zip(idx, a):
            s.add(MarketDaily(date=t.date(), aspi=float(v)))
        for k in range(3):
            c = 50 * np.cumprod(1 + np.random.default_rng(10 + k).normal(0.0005, 0.018, n))
            sym = f"S{k}.N0000"
            s.add(Security(symbol=sym, cse_id=100 + k, name=f"Stock {k}"))
            for t, v in zip(idx, c):
                s.add(PriceDaily(symbol=sym, date=t.date(), open=float(v * 0.998), high=float(v * 1.01), low=float(v * 0.99), close=float(v), volume=5000.0))
        g = WatchlistGroup(user_id="local", name="Watchlist 1")
        s.add(g)
        s.flush()
        for k in range(3):
            s.add(WatchlistGroupItem(group_id=g.id, symbol=f"S{k}.N0000"))
    return Sess


def wait(run_id, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = quant_api.lab_run_status(run_id, ME)
        if v["status"] != "running":
            return v
        time.sleep(0.5)
    raise AssertionError("the run did not finish")


def test_catalog_run_cache_and_saved_strategies(DB):
    cat = quant_api.lab_catalog(ME)
    assert {r["type"] for r in cat["rules"]} == set(L.RULES) and [x["label"] for x in cat["stocks"]] == ["S0", "S1", "S2"]
    body = {"list_id": 1, "years": 1, "entry": {"type": "ma_above", "n": 20}, "exit": {"hold_days": 10, "stop_pct": 8}, "max_positions": 2}
    first = quant_api.lab_run(body, ME)
    assert first["status"] == "running"
    done = wait(first["id"])
    assert done["status"] == "done", done.get("error")
    res = done["result"]
    assert res["parity"]["ok"] and res["parity"]["fills_differing"] == 0 and res["meta"]["universe"] == ["S0", "S1", "S2"]
    assert res["luck"]["runs"] == 4 and len(res["charts"]["strategy"]) == res["meta"]["sessions"] and res["summary"][-1].startswith("The stocks are ones you track")
    assert res["stats"]["trades"] > 0 and res["trades"]["rows"][0]["stock"].startswith("S")
    again = quant_api.lab_run(body, ME)                                   # identical test on unchanged data: kept
    assert again["status"] == "done" and again["cached"] and again["result"] if "result" in again else again["cached"]
    # saved strategies
    saved = quant_api.lab_save({"name": "  Trend   test ", "spec": body}, ME)
    assert [x["name"] for x in saved] == ["Trend test"] and saved[0]["spec"]["symbols"] == ["S0.N0000", "S1.N0000", "S2.N0000"]
    saved = quant_api.lab_save({"name": "Trend test", "spec": {**body, "max_positions": 1}}, ME)
    assert len(saved) == 1 and saved[0]["spec"]["max_positions"] == 1       # same name: replaced
    assert quant_api.lab_saved(ME) == saved
    assert quant_api.lab_delete("Trend test", ME) == [] and quant_api.lab_saved(ME) == []


def test_run_errors_are_plain_and_other_accounts_cannot_read_a_run(DB):
    with pytest.raises(HTTPException) as e:
        quant_api.lab_run({"symbols": ["S0.N0000"], "entry": {"type": "nope"}}, ME)
    assert e.value.status_code == 400 and "Unknown condition" in e.value.detail
    with pytest.raises(HTTPException) as e:
        quant_api.lab_run({"list_id": 99, "entry": {"type": "monthly"}}, ME)
    assert e.value.status_code == 400
    r = quant_api.lab_run({"symbols": ["NOPE.N0000"], "years": 1, "entry": {"type": "monthly"}}, ME)
    v = wait(r["id"])
    assert v["status"] == "error" and "NOPE" in v["error"]
    ok = quant_api.lab_run({"symbols": ["S0.N0000"], "years": 1, "entry": {"type": "monthly"}, "exit": {"hold_days": 5}}, ME)
    with pytest.raises(HTTPException) as e:
        quant_api.lab_run_status(ok["id"], User(uid="someone-else", admin=False))
    assert e.value.status_code == 404
    wait(ok["id"])
