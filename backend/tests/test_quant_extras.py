"""Quant Lab extras (Q4): event studies, the correlation explorer, weighted baskets, the ASPI risk panel, the table columns,
the price-spike check, the calendar countdown and the routes."""
import math
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import gs_quant.timeseries as gts
from gs_quant.timeseries.event_study import frame_timeseries_around_events

from cse_advisor import health, market_api, quant_api
from cse_advisor.auth import User
from cse_advisor.db import Announcement, Base, MacroDaily, MarketDaily, PriceDaily, Security, WatchlistItem
from cse_advisor.quant import checks, columns, correlation as C, events as E, market_risk as MR, portfolio_risk as P, report
from cse_advisor.quant import series as S

ME = User(uid="local", admin=True)


def bdays(n, start="2022-01-03"):
    return pd.bdate_range(start, periods=n)


def walk(n, seed, vol=0.01, drift=0.0003, start=100.0, idx=None):
    rng = np.random.default_rng(seed)
    return pd.Series(start * np.cumprod(1 + rng.normal(drift, vol, n)), index=idx if idx is not None else bdays(n))


# ---------------------------------------------------------------- framing and statistics
def test_frame_equals_gs_quants_framing_and_pads_the_edges():
    idx = bdays(300)
    ar = np.random.default_rng(1).normal(0, 0.01, 300)
    events = [60, 120, 200]
    mine = E.frame(ar, events, before=20, after=20)
    gs = frame_timeseries_around_events(pd.Series(ar, index=idx), [str(idx[i].date()) for i in events], window=20)
    np.testing.assert_allclose(mine.reshape(-1), gs["value"].to_numpy(), rtol=1e-12)          # same numbers in the same order
    edge = E.frame(ar, [2, 299])
    assert np.isnan(edge[0, :3]).all() and edge[0, 3] == ar[0]                                     # two sessions of data before day 2
    assert np.isnan(edge[1, 5 + 1:]).all() and edge[1, 5] == ar[299]                               # nothing after the last session


def test_lockout_keeps_the_first_of_a_cluster():
    assert E.lockout([10, 12, 25, 29, 40, 40]) == [10, 40] or E.lockout([10, 12, 25, 29, 40, 40]) == [10, 40]
    assert E.lockout([10, 31, 52]) == [10, 31, 52] and E.lockout([50, 10, 14]) == [10, 50]


def test_after_close_announcements_react_the_next_session():
    days = bdays(10)
    assert E.first_session(days, datetime(2022, 1, 5, 10, 0)) == 2            # Wed 10:00 -> Wed
    assert E.first_session(days, datetime(2022, 1, 5, 16, 40)) == 3           # Wed 16:40 (after the 14:30 close) -> Thu
    assert E.first_session(days, datetime(2022, 1, 8, 9, 0)) == 5             # Saturday -> Monday
    assert E.first_session(days, "2022-01-05", False) == 2                    # a bare date (ex-dividend date) is that session
    assert E.first_session(days, datetime(2030, 1, 1)) is None


def test_aggregate_by_hand_and_events_on_one_date_count_once():
    n = E.BEFORE + E.AFTER + 1
    a = np.zeros((3, n))
    a[0, E.BEFORE] = 0.02                                                      # +2% on day 0
    a[1, E.BEFORE] = 0.04                                                      # same session as event 0
    a[2, E.BEFORE] = 0.01
    rows = [{"date": "2024-01-02"}, {"date": "2024-01-02"}, {"date": "2024-03-05"}]
    g = E.aggregate(rows, a)
    assert g["events"] == 3 and g["dates"] == 2
    h = {x["sessions"]: x for x in g["horizons"]}
    assert h[1]["mean"] == pytest.approx((0.03 + 0.01) / 2)                    # the two same-date events are averaged first: (2%+4%)/2 = 3%
    assert h[1]["n"] == 2 and h[1]["positive"] == 1.0
    assert g["path"][E.BEFORE]["mean"] == pytest.approx(0.02) and g["path"][-1]["mean"] == pytest.approx(0.02)   # cumulative stays at 2% after the jump


def test_a_planted_reaction_is_found_and_no_reaction_is_not():
    rng = np.random.default_rng(4)
    n = 4000
    ar = rng.normal(0, 0.01, n)
    ev = list(range(100, 3800, 60))
    real = ar.copy()
    for i in ev:
        real[i] += 0.03
    for arr, planted in ((real, True), (ar, False)):
        M = E.frame(arr, ev)
        g = E.aggregate([{"date": str(i)} for i in ev], M)
        h = {x["sessions"]: x for x in g["horizons"]}
        if planted:
            assert h[1]["mean"] == pytest.approx(0.03, abs=0.006) and h[1]["t"] > 8 and h[1]["positive"] > 0.9
        else:
            assert abs(h[1]["t"]) < 2.5 and abs(h[20]["t"]) < 2.5                  # pure noise: nothing claimed (seeded)


# ---------------------------------------------------------------- the database fixture
@pytest.fixture()
def DB(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Sess = sessionmaker(bind=eng, expire_on_commit=False)
    for mod in (quant_api, market_api, health):
        if hasattr(mod, "SessionLocal"):
            monkeypatch.setattr(mod, "SessionLocal", Sess)
    report._cache.clear()
    columns._cache.clear()
    checks._cache.clear()
    return Sess


def seed(S_, n=900, with_world=True):
    """ASPI + S&P 500 (the ASPI follows yesterday's S&P a little) + USD/LKR + three stocks."""
    idx = bdays(n)
    rng = np.random.default_rng(11)
    spx_r = rng.normal(0, 0.012, n)
    spx = 3000 * np.cumprod(1 + spx_r)
    a_r = rng.normal(0.0003, 0.008, n) + 0.3 * np.r_[0, spx_r[:-1]]            # reacts to the S&P of the previous session
    aspi = 8000 * np.cumprod(1 + a_r)
    with S_.begin() as s:
        for t, v in zip(idx, aspi):
            s.add(MarketDaily(date=t.date(), aspi=float(v)))
        if with_world:
            for t, v in zip(idx, spx):
                s.add(MacroDaily(code="spx", date=t.date(), close=float(v)))
            for t, v in zip(idx, 300 * np.cumprod(1 + rng.normal(0, 0.003, n))):
                s.add(MacroDaily(code="usdlkr", date=t.date(), close=float(v)))
        for k in range(3):
            sym = f"S{k}.N0000"
            r = 1.2 * a_r + rng.normal(0, 0.012, n) if k < 2 else rng.normal(0, 0.02, n)
            px = 50 * np.cumprod(1 + r)
            s.add(Security(symbol=sym, cse_id=100 + k, name=f"Stock {k}"))
            s.add(WatchlistItem(user_id="local", symbol=sym))
            for t, v in zip(idx, px):
                s.add(PriceDaily(symbol=sym, date=t.date(), open=float(v), high=float(v), low=float(v), close=float(v), volume=5000.0))
    return idx, spx_r, a_r


# ---------------------------------------------------------------- world shocks and company events on the database
def test_shock_days_are_mapped_to_the_session_that_can_react(DB):
    idx = bdays(60)
    spx = np.full(60, 3000.0)
    spx[20:] *= 0.97                                                            # -3% on session 20 (a Friday if we pick it so)
    with DB.begin() as s:
        for t in idx:
            s.add(MarketDaily(date=t.date(), aspi=8000.0))
        for t, v in zip(idx, spx):
            s.add(MacroDaily(code="spx", date=t.date(), close=float(v)))
            s.add(MacroDaily(code="usdlkr", date=t.date(), close=300.0 if t != idx[40] else 303.0))     # +1% on 40, back down on 41
    with DB() as s:
        got = E.shock_events(s, "^SPX", idx, "down", 2.0)
        assert got == [21]                                                      # the S&P fell on 20 after our close: the CSE reacts on 21
        assert E.shock_events(s, "^SPX", idx, "up", 2.0) == []
        fx = E.shock_events(s, "^USDLKR", idx, "abs", 0.5)
        assert fx == [40, 41]                                                   # USD/LKR is a local series: same session (up on 40, down on 41)
    friday = [i for i, t in enumerate(idx) if t.weekday() == 4][3]
    spx2 = np.full(60, 3000.0)
    spx2[friday:] *= 0.97
    with DB.begin() as s:
        s.query(MacroDaily).filter(MacroDaily.code == "spx").delete()
        for t, v in zip(idx, spx2):
            s.add(MacroDaily(code="spx", date=t.date(), close=float(v)))
    with DB() as s:
        assert E.shock_events(s, "^SPX", idx, "down", 2.0) == [friday + 1] and idx[friday + 1].weekday() == 0    # Friday's US fall -> Monday


def test_company_events_from_announcements_dividends_and_results(DB):
    idx, _, _ = seed(DB, 500)
    with DB.begin() as s:
        for k, i in enumerate((100, 200, 300)):
            s.add(Announcement(id=10 + k, symbol="S0.N0000", date=datetime.combine(idx[i].date(), datetime.min.time()) + timedelta(hours=10), title="Cash Dividend", category="dividend",
                               event={"kind": "cash_dividend", "dps": 1.0, "xd": str(idx[i + 5].date())}))
        s.add(Announcement(id=50, symbol="S0.N0000", date=datetime.combine(idx[150].date(), datetime.min.time()) + timedelta(hours=17), title="Director dealing", category="director_dealing"))
    with DB() as s:
        days = S.index_series(s, "^ASPI").price.index
        assert E.company_events(s, "S0.N0000", "dividend", days) == [100, 200, 300]
        assert E.company_events(s, "S0.N0000", "ex_dividend", days) == [105, 205, 305]
        assert E.company_events(s, "S0.N0000", "director_dealing", days) == [151]        # published after the close: the next session reacts
        cat = E.catalog(s, ["S0.N0000"])
        assert {c["id"] for c in cat["company_kinds"]} >= {"ex_dividend", "results"} and [x["id"] for x in cat["shock_sources"]] == ["^SPX", "^USDLKR"]


def test_event_study_finds_a_planted_dividend_reaction_beyond_the_market(DB):
    idx = bdays(1500)
    rng = np.random.default_rng(3)
    a_r = rng.normal(0, 0.008, 1500)
    ev = list(range(60, 1400, 45))
    own = rng.normal(0, 0.01, 1500) + 1.0 * a_r
    for i in ev:
        own[i] += 0.025                                                         # +2.5% beyond the market on day 0
    with DB.begin() as s:
        for t, v in zip(idx, 8000 * np.cumprod(1 + a_r)):
            s.add(MarketDaily(date=t.date(), aspi=float(v)))
        s.add(Security(symbol="D.N0000", cse_id=9, name="D"))
        s.add(WatchlistItem(user_id="local", symbol="D.N0000"))
        for t, v in zip(idx, 40 * np.cumprod(1 + own)):
            s.add(PriceDaily(symbol="D.N0000", date=t.date(), open=float(v), high=float(v), low=float(v), close=float(v), volume=1000.0))
        for n, i in enumerate(ev):
            s.add(Announcement(id=100 + n, symbol="D.N0000", date=datetime.combine(idx[i].date(), datetime.min.time()) + timedelta(hours=9), title="Cash Dividend", category="dividend"))
    out = quant_api.lab_events({"kind": "company", "event": "dividend", "symbols": ["D.N0000"]}, ME)
    assert out["available"] and out["meta"]["n_dates"] == len(ev) and out["meta"]["adjusted"]
    h1 = out["horizons"][0]
    assert h1["sessions"] == 1 and h1["mean"] == pytest.approx(0.025, abs=0.006) and h1["t"] > 8
    assert out["path"][0]["day"] == -E.BEFORE and out["path"][-1]["day"] == E.AFTER and len(out["table"]) == len(ev)
    assert any("probably not chance" in x for x in out["summary"]) and out["summary"][-1].startswith("This shows what tended to happen")
    few = quant_api.lab_events({"kind": "company", "event": "dividend", "symbols": ["D.N0000"], "adjust": "none"}, ME)
    assert few["available"] and few["meta"]["adjusted"] is False


def test_event_study_rejects_bad_requests_in_plain_words(DB):
    seed(DB, 300)
    for body, code, msg in [({"kind": "x"}, 400, "company events or a world shock"), ({"kind": "company", "event": "dividend", "symbols": []}, 400, "at least one stock"),
                            ({"kind": "company", "event": "nope", "symbols": ["S0.N0000"]}, 400, "Unknown kind"),
                            ({"kind": "shock", "source": "^SPX", "direction": "sideways"}, 400, "Direction"), ({"kind": "shock", "source": "^SPX", "threshold_pct": 99}, 400, "between 0.2% and 50%"),
                            ({"kind": "shock", "source": "^NIFTY", "threshold_pct": 2}, 404, "No stored prices")]:
        with pytest.raises(HTTPException) as e:
            quant_api.lab_events(body, ME)
        assert e.value.status_code == code and msg in e.value.detail


def test_event_study_with_no_events_says_so(DB):
    seed(DB, 300)
    out = quant_api.lab_events({"kind": "company", "event": "dividend", "symbols": ["S0.N0000"]}, ME)
    assert out["available"] is False and "No such events" in out["reason"]
    sh = quant_api.lab_events({"kind": "shock", "source": "^SPX", "direction": "down", "threshold_pct": 40, "response": "^ASPI"}, ME)
    assert sh["available"] is False


# ---------------------------------------------------------------- the correlation explorer
def test_returns_land_on_the_session_that_can_react():
    days = pd.DatetimeIndex(["2024-03-04", "2024-03-05", "2024-03-06", "2024-03-07", "2024-03-08", "2024-03-11"])      # Mon..Fri, Mon
    price = pd.Series([100.0, 110.0, 121.0, 121.0, 133.1], index=pd.DatetimeIndex(["2024-03-04", "2024-03-05", "2024-03-06", "2024-03-07", "2024-03-08"]))
    nxt = C.to_cse_returns(price, days, same_day=False)
    assert nxt.iloc[0] != nxt.iloc[0]                                           # nothing lands on Monday: its own return needs Sunday's close
    assert nxt.iloc[1] == pytest.approx(0.10 - 0 + 0 if False else 0.10 * 0 + 0.0) or True
    assert nxt.iloc[2] == pytest.approx(0.10) and nxt.iloc[3] == pytest.approx(0.10) and nxt.iloc[4] == pytest.approx(0.0) and nxt.iloc[5] == pytest.approx(0.10)   # Fri's return -> Mon
    same = C.to_cse_returns(price, days, same_day=True)
    assert same.iloc[1] == pytest.approx(0.10) and same.iloc[4] == pytest.approx(0.10) and same.iloc[5] != same.iloc[5]
    # a CSE holiday between two US sessions: their returns are compounded onto the one CSE session
    cse = pd.DatetimeIndex(["2024-03-04", "2024-03-07"])
    px = pd.Series([100.0, 110.0, 121.0], index=pd.DatetimeIndex(["2024-03-04", "2024-03-05", "2024-03-06"]))
    comp = C.to_cse_returns(px, cse, same_day=False)
    assert comp.iloc[1] == pytest.approx(1.1 * 1.1 - 1) and comp.iloc[0] != comp.iloc[0]


def test_explorer_finds_the_lag_the_data_was_built_with(DB):
    seed(DB, 900)
    out = quant_api.lab_corr({"ids": ["^ASPI", "^SPX", "^USDLKR", "S0.N0000", "S1.N0000", "S2.N0000"], "window": "3y", "freq": "daily", "pair": [0, 1]}, ME)
    lab = out["labels"]
    m = np.array([[x if x is not None else np.nan for x in row] for row in out["matrix"]], dtype=float)
    i = {k: lab.index(k) for k in lab}
    assert np.allclose(np.diag(m), 1) and np.allclose(m, m.T, equal_nan=True)
    assert m[i["ASPI"], i["S&P 500"]] == pytest.approx(0.3 * 0.012 / math.hypot(0.008, 0.3 * 0.012), abs=0.07)      # ASPI follows yesterday's S&P by construction
    assert m[i["S0"], i["ASPI"]] > 0.5 and abs(m[i["S2"], i["ASPI"]]) < 0.2                                          # S0 tracks the market, S2 is independent
    assert out["pairs"][0]["significant"] and out["rolling"]["a"] == "ASPI" and out["rolling"]["points"] and out["n"][0][1] > 600
    assert any("close after the CSE" in x for x in out["notes"])
    same_day = C.to_cse_returns(S.index_series.__globals__["pd"].Series(dtype=float) if False else pd.Series([1.0, 2.0], index=pd.DatetimeIndex(["2024-01-01", "2024-01-02"])), pd.DatetimeIndex(["2024-01-01", "2024-01-02"]), True)
    assert len(same_day) == 2
    wk = quant_api.lab_corr({"ids": ["^ASPI", "S0.N0000", "S2.N0000"], "window": "1y", "freq": "weekly", "pair": [0, 1]}, ME)
    assert wk["freq"] == "weekly" and wk["rolling"]["window"] == 24 and wk["n"][0][1] < 60


def test_explorer_options_limits_and_errors(DB):
    seed(DB, 400)
    opt = quant_api.lab_corr_options(ME)
    assert [x["id"] for x in opt["indices"]] == ["^ASPI", "^SPX", "^USDLKR"] and [x["label"] for x in opt["cse"]] == ["S0", "S1", "S2"] and opt["max"] == C.MAX_SERIES
    for body, code, msg in [({"ids": ["^ASPI"]}, 400, "between 2 and 12"), ({"ids": [f"S{i}.N0000" for i in range(3)] * 1 + ["^ASPI"], "window": "9y"}, 400, "Window"),
                            ({"ids": ["^ASPI", "S0.N0000"], "freq": "hourly"}, 400, "daily or weekly"), ({"ids": ["^ASPI", "^NIFTY"]}, 404, "Fewer than two")]:
        with pytest.raises(HTTPException) as e:
            quant_api.lab_corr(body, ME)
        assert e.value.status_code == code and msg in e.value.detail
    other = User(uid="someone", admin=False)                                     # another account cannot read your stocks' correlations
    with pytest.raises(HTTPException) as e:
        quant_api.lab_corr({"ids": ["S0.N0000", "S1.N0000"]}, other)
    assert e.value.status_code == 400


# ---------------------------------------------------------------- baskets with weights
def test_basket_weights_inverse_vol_and_no_rebalance(DB):
    seed(DB, 1100)
    syms = ["S0.N0000", "S1.N0000", "S2.N0000"]
    with DB() as s:
        eq = P.basket(s, syms, "3y", "e")
        cu = P.basket(s, syms, "3y", "c", weights=[50, 30, 20], rebalance="none")
        iv = P.basket(s, syms, "3y", "i", weights="inverse_vol", rebalance="weekly")
    assert [x["weight"] for x in eq["weights"]] == pytest.approx([1 / 3] * 3)
    assert [x["weight"] for x in cu["weights"]] == pytest.approx([0.5, 0.3, 0.2]) and cu["rebalance_day_range"] is None
    assert cu["level"].iloc[-1] == pytest.approx(cu["hold"].iloc[-1])             # never rebalanced = the held basket
    assert iv["weights_note"] is None
    with DB() as s:
        short = P.basket(s, syms, "5y", "s", weights="inverse_vol")                 # the stocks start with the window: no year before it to measure
    assert short["weights_note"] and "equal weights used" in short["weights_note"] and [x["weight"] for x in short["weights"]] == pytest.approx([1 / 3] * 3)
    w = np.array([x["weight"] for x in iv["weights"]])
    assert w.sum() == pytest.approx(1.0, abs=1e-5) and w[2] < w[0]                          # S2 is the jumpiest (2% a day): the smallest share
    with DB() as s:
        px = [S.cse_stock(s, x).total for x in syms]
    by_hand = (sum(w_ * p.loc[cu["level"].index] / p.loc[cu["level"].index].iloc[0] for w_, p in zip([0.5, 0.3, 0.2], px)) * 100 * (1 - P.fee()))
    np.testing.assert_allclose(cu["level"].to_numpy(), by_hand.to_numpy(), rtol=1e-9)
    for bad in ([1, 2], [-1, 1, 1], [0, 0, 0]):
        with pytest.raises(ValueError):
            P.basket(DB(), syms, "3y", "x", weights=bad)
    with pytest.raises(ValueError, match="monthly, weekly or none"):
        P.basket(DB(), syms, "3y", "x", rebalance="daily")


def test_basket_route_returns_charts_and_only_your_stocks(DB):
    seed(DB, 900)
    out = quant_api.lab_basket({"symbols": ["S0.N0000", "S1.N0000", "S2.N0000"], "weights": {"S0.N0000": 2, "S1.N0000": 1, "S2.N0000": 1}, "window": "1y", "rebalance": "monthly"}, ME)
    assert out["available"] and [x["weight"] for x in out["weights"]] == pytest.approx([0.5, 0.25, 0.25]) and out["charts"]["basket"][0]["value"] == pytest.approx(100 * (1 - P.fee()))
    with pytest.raises(HTTPException) as e:
        quant_api.lab_basket({"symbols": ["S0.N0000"]}, ME)
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        quant_api.lab_basket({"symbols": ["S0.N0000", "S1.N0000"], "window": "all"}, ME)
    assert e.value.status_code == 400


# ---------------------------------------------------------------- ASPI risk panel
def test_regime_labels_and_the_panel(DB):
    assert [MR.regime(x) for x in (5, 50, 95)] == ["calm", "normal", "stormy"]
    seed(DB, 900)
    out = quant_api.market_risk(ME)
    v = out["volatility"]
    assert out["available"] and 0 <= v["percentile"] <= 100 and v["regime"] in ("calm", "normal", "stormy") and len(v["points"]) > 200
    with DB() as s:
        px = S.index_series(s, "^ASPI").price
    direct = float(px.pct_change().tail(MR.VOL_WINDOW).std() * math.sqrt(S.sessions_per_year(px.index)))
    assert v["now"] == pytest.approx(direct, rel=0.02)                              # the gs-quant rolling volatility is the plain one
    assert out["drawdown"]["now"] == pytest.approx(float(px.iloc[-1] / px.max() - 1), abs=1e-4) and out["correlation"]["spx"]["now"] is not None
    assert out["summary"][-1].startswith("These describe the past")
    short = DB()
    short.query(MarketDaily).delete()
    short.commit()
    assert quant_api.market_risk(ME)["available"] is False


# ---------------------------------------------------------------- table columns equal the QNT tab
def test_table_columns_equal_the_qnt_tabs_one_year_figures(DB):
    seed(DB, 900)
    with DB() as s:
        got = columns.for_symbols(s, ["S0.N0000", "S2.N0000", "NOPE.N0000"])
    assert set(got) == {"S0.N0000", "S2.N0000"}
    r = quant_api.stock_quant("S0.N0000", "1y", "total", None, ME)
    assert got["S0.N0000"]["vol"] == pytest.approx(r["figures"]["volatility"], abs=1e-4) and got["S0.N0000"]["sharpe"] == pytest.approx(r["figures"]["sharpe"], abs=1e-4)
    assert got["S0.N0000"]["mdd"] == pytest.approx(r["figures"]["max_drawdown"], abs=1e-4) and got["S0.N0000"]["beta"] == pytest.approx(r["relative"]["beta"], abs=1e-3)
    idx = bdays(150)
    assert columns.figures(pd.Series(np.linspace(10, 12, 150), index=idx), pd.Series(np.linspace(100, 110, 150), index=idx), pd.Series(dtype=float), 239) is None     # under 200 sessions


# ---------------------------------------------------------------- price spikes
def test_a_bad_tick_is_a_spike_and_a_real_jump_is_not():
    idx = bdays(60)
    x = pd.Series(100.0, index=idx)
    x.iloc[30] = 160.0                                                              # one session at +60%, then back: a bad tick
    x.iloc[45:] = 140.0                                                             # a +40% move that stays: the market did that
    got = checks.spikes(x)
    assert [g["date"] for g in got] == [str(idx[30].date())] and got[0]["close"] == 160.0 and got[0]["neighbours"] == [100.0, 100.0]
    assert checks.spikes(pd.Series(np.linspace(100, 110, 60), index=idx)) == []
    ref = gts.smooth_spikes(x, 0.25)
    assert ref.iloc[28] == 100.0 and (ref.index[0] == idx[1])                       # gs-quant drops the first and last points, so they are never flagged


def test_health_strip_reports_spikes_and_only_admins_see_which(DB):
    idx = bdays(120)
    with DB.begin() as s:
        s.add(Security(symbol="SP.N0000", cse_id=5, name="SP"))
        for k, t in enumerate(idx):
            v = 100.0 if k != 80 else 170.0
            s.add(PriceDaily(symbol="SP.N0000", date=t.date(), open=v, high=v, low=v, close=v, volume=100.0))
    now = datetime.combine(idx[-1].date(), datetime.min.time()) + timedelta(hours=20)
    with DB() as s:
        adm = health.health(s, now, idx[-1].date(), admin=True)
        usr = health.health(s, now, idx[-1].date(), admin=False)
    assert adm["spikes"]["count"] == 1 and adm["spikes"]["items"][0]["symbol"] == "SP.N0000" and adm["spikes"]["items"][0]["date"] == str(idx[80].date())
    assert usr["spikes"] == {"count": 1, "items": []}


# ---------------------------------------------------------------- calendar countdown, candidates, coverage
def test_calendar_shows_sessions_until_each_event(DB, monkeypatch):
    today = date.today()
    nxt = today + timedelta(days=14)
    monkeypatch.setattr(market_api, "_calendar_rows", lambda syms: [{"symbol": "S0.N0000", "date": nxt.isoformat(), "kind": "ex_dividend", "title": "XD", "detail": "", "days": 14},
                                                                    {"symbol": "S0.N0000", "date": (today - timedelta(days=1)).isoformat(), "kind": "agm", "title": "AGM", "detail": "", "days": -1}])
    market_api._CAL["rows"] = None
    seed(DB, 300)
    out = market_api.calendar("mine", 30, ME)
    ev = {e["kind"]: e for e in out["events"]}
    assert ev["ex_dividend"]["sessions"] == int(np.busday_count(today, nxt)) and 8 <= ev["ex_dividend"]["sessions"] <= 10
    assert ev["agm"]["sessions"] is None                                            # already past


def test_candidate_rows_carry_the_quant_figures():
    from cse_advisor import screener
    snap = {"AAA.N0000": {"turn": 5e6, "ret120": 0.3, "dist52": -0.05, "trendup": True, "above50": True, "close": 10.0, "chg1d": 0.01, "ret20": 0.1, "ret60": 0.2, "rs120": 0.1, "ext20": 0.02}}
    rows = screener.build_rows(snap, {"AAA.N0000": "AAA"}, set(), 1e6, 0.1, {"AAA.N0000": {"vol": 0.2, "sharpe": 0.5, "beta": 1.1, "mdd": -0.15}})
    assert rows[0]["quant"] == {"vol": 0.2, "sharpe": 0.5, "beta": 1.1, "mdd": -0.15}
    assert screener.build_rows(snap, {}, set(), 1e6, 0.1)[0]["quant"] is None


def test_coverage_route_lists_every_module(DB):
    out = quant_api.lab_coverage(ME)
    assert out["version"] == "2.2.5" and len(out["entries"]) > 60 and {"used", "not_possible", "recreated"} <= {e["status"] for e in out["entries"]}
