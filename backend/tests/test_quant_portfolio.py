"""PORT risk and performance: the time-weighted equity curve worked out by hand, positions bought before as-traded prices
are stored, dividends across a share split, risk shares / VaR / diversification on toy data, stress episodes, the
basket against gs-quant, and the routes on a small database."""
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import gs_quant.timeseries as ts

from cse_advisor import quant_api
from cse_advisor.auth import User
from cse_advisor.db import Announcement, Base, MarketDaily, Position, PriceDaily, Security, WatchlistGroup, WatchlistGroupItem
from cse_advisor.quant import portfolio_risk as P
from cse_advisor.quant import series as S_

ME = User(uid="local", admin=True)


@pytest.fixture()
def S(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Sess = sessionmaker(bind=eng, expire_on_commit=False)
    monkeypatch.setattr(quant_api, "SessionLocal", Sess)
    return Sess


def add_prices(s, sym, closes: pd.Series, source="eod"):
    s.merge(Security(symbol=sym, cse_id=abs(hash(sym)) % 10_000, name=sym))
    for t, c in closes.items():
        s.add(PriceDaily(symbol=sym, date=t.date(), open=float(c), high=float(c), low=float(c), close=float(c), volume=1000.0, source=source))


def add_market(s, idx, values=None):
    vals = values if values is not None else np.linspace(1000, 1100, len(idx))
    for t, v in zip(idx, vals):
        s.add(MarketDaily(date=t.date(), aspi=float(v)))


def walk(n, seed, vol=0.012, start=100.0, idx=None):
    rng = np.random.default_rng(seed)
    idx = idx if idx is not None else pd.bdate_range("2021-01-04", periods=n)
    return pd.Series(start * np.cumprod(1 + rng.normal(0.0003, vol, n)), index=idx)


# ---------------------------------------------------------------- the equity curve, by hand
def test_time_weighted_curve_by_hand(S, monkeypatch):
    """Two buys, one sale and a dividend; fees 1% a side. Each day's return worked out by hand below."""
    monkeypatch.setattr(P, "fee", lambda: 0.01)
    idx = pd.bdate_range("2024-01-01", periods=6)                    # d0..d5
    px = pd.Series([10, 10, 11, 12, 12, 13], index=idx, dtype=float)
    with S.begin() as s:
        add_market(s, idx)
        add_prices(s, "ABC.N0000", px)
        s.add(Position(user_id="local", symbol="ABC.N0000", entry_date=idx[1].date(), entry_price=10.0, quantity=100,
                       closed=True, exit_date=idx[5].date(), exit_price=13.0))
        s.add(Position(user_id="local", symbol="ABC.N0000", entry_date=idx[3].date(), entry_price=12.0, quantity=100))
        s.add(Announcement(id=1, symbol="ABC.N0000", date=datetime(2024, 1, 2), title="Cash Dividend", category="dividend",
                           event={"kind": "cash_dividend", "dps": 0.5, "xd": str(idx[4].date())}))
    with S() as s:
        c = P.equity_curve(s, "local")
    r = [
        1000 / 1010 - 1,                       # d1: bought 100 at 10 + 1% fee (1010), worth 1000 at the close
        1100 / 1000 - 1,                       # d2: 10 -> 11
        2400 / (1100 + 1212) - 1,              # d3: second buy 100 at 12 + 1% (1212); both worth 2400
        (2400 + 100) / 2400 - 1,               # d4: dividend 0.5 on the 200 shares held before the XD date
        (1300 + 1287) / 2400 - 1,              # d5: the first lot sold at 13 less 1% (1287); the second worth 1300
    ]
    want = 100 * np.cumprod([1.0] + [1 + x for x in r])
    assert c["available"] and c["start"] == str(idx[0].date())
    np.testing.assert_allclose(c["curve"].to_numpy(), want, rtol=1e-12)
    assert c["dividends"] == pytest.approx(100.0) and c["money_in"] == pytest.approx(2222.0) and c["money_out"] == pytest.approx(1287.0)
    assert c["pnl"].iloc[-1] == pytest.approx(1300 + 1287 + 100 - 2222)        # profit in rupees, fees included
    assert c["entry_estimated"] == []


def test_buy_before_as_traded_prices_uses_the_price_paid(S, monkeypatch):
    """Older bars are TradingView's split-adjusted history (no as-traded price). A position bought then at the price
    actually paid (10x the adjusted price: a 1-for-10 split since) is valued from the price paid, not shown as a 90% loss."""
    monkeypatch.setattr(P, "fee", lambda: 0.0)
    idx = pd.bdate_range("2024-01-01", periods=8)
    adj = pd.Series([20, 20, 21, 22, 22, 23, 24, 24], index=idx, dtype=float)
    with S.begin() as s:
        add_market(s, idx)
        add_prices(s, "JK.N0000", adj.iloc[:5], source="tv_adj")
        add_prices(s, "JK.N0000", adj.iloc[5:], source="eod")
        s.add(Position(user_id="local", symbol="JK.N0000", entry_date=idx[1].date(), entry_price=200.0, quantity=50))
    with S() as s:
        c = P.equity_curve(s, "local")
        h = P.holdings(s, "local")
    assert c["entry_estimated"] == ["JK"]
    assert c["value"].iloc[-1] == pytest.approx(50 * 24 * 10)                 # 500 shares now at 24
    assert c["curve"].iloc[-1] == pytest.approx(100 * 24 / 20)                 # the same +20% as the stock
    assert h[0]["value"] == pytest.approx(12000.0)


def test_dividend_before_a_split_is_left_out_when_it_cannot_be_converted(S):
    idx = pd.bdate_range("2024-01-01", periods=120)
    adj = pd.Series(20.0, index=idx)
    with S.begin() as s:
        add_prices(s, "JK.N0000", adj.iloc[:100], source="tv_adj")
        add_prices(s, "JK.N0000", adj.iloc[100:], source="eod")
        s.add(Announcement(id=1, symbol="JK.N0000", date=datetime(2024, 1, 5), title="Cash Dividend", category="dividend",
                           event={"kind": "cash_dividend", "dps": 5.0, "xd": str(idx[10].date())}))     # paid before the split
        s.add(Announcement(id=2, symbol="JK.N0000", date=datetime(2024, 2, 20), title="Sub-Division Of Shares", category="share_split"))
        s.add(Announcement(id=3, symbol="JK.N0000", date=datetime(2024, 4, 1), title="Cash Dividend", category="dividend",
                           event={"kind": "cash_dividend", "dps": 0.5, "xd": str(idx[70].date())}))     # after it: fine
    with S() as s:
        b = S_.cse_stock(s, "JK.N0000")
    assert [d["dps"] for d in b.dividends] == [0.5]
    assert [d["dps"] for d in b.dividends_left_out] == [5.0]
    assert b.total.iloc[-1] == pytest.approx(20.0 * 20.5 / 20.0)                 # only the 0.5 reinvested


# ---------------------------------------------------------------- risk of the holdings, on toy data
def test_risk_shares_var_and_diversification():
    rng = np.random.default_rng(3)
    common = rng.normal(0, 0.01, 500)
    rets = pd.DataFrame({"A": common + rng.normal(0, 0.01, 500), "B": common + rng.normal(0, 0.02, 500), "C": rng.normal(0, 0.005, 500)},
                        index=pd.bdate_range("2023-01-02", periods=500))
    values = pd.Series({"A": 50_000.0, "B": 30_000.0, "C": 20_000.0})
    f = P.risk_figures(rets, values, None, 240)
    w = values / values.sum()
    cov = rets.cov().to_numpy() * 240
    assert f["volatility"] == pytest.approx(np.sqrt(w @ cov @ w))
    assert f["risk_share"].sum() == pytest.approx(1.0)
    assert f["risk_share"]["B"] > w["B"]                                        # the jumpiest holding carries more than its weight
    assert f["diversification_ratio"] > 1.0
    pnl = rets.to_numpy() @ values.to_numpy()
    assert f["var95"] == pytest.approx(np.percentile(pnl, 5)) and f["cvar95"] < f["var95"]
    same = P.risk_figures(pd.DataFrame({"A": rets["A"], "B": rets["A"]}), pd.Series({"A": 1.0, "B": 1.0}), None, 240)
    assert same["diversification_ratio"] == pytest.approx(1.0)                  # two identical holdings: no diversification


def test_portfolio_beta_to_the_market():
    rng = np.random.default_rng(4)
    m = pd.Series(rng.normal(0, 0.01, 300), index=pd.bdate_range("2023-01-02", periods=300))
    rets = pd.DataFrame({"A": 2 * m, "B": 0.5 * m})
    f = P.risk_figures(rets, pd.Series({"A": 1.0, "B": 1.0}), m, 240)
    assert f["beta"] == pytest.approx(1.25)


def test_worst_episodes_do_not_overlap():
    idx = pd.bdate_range("2020-01-01", periods=60)
    x = pd.Series(100.0, index=idx)
    x.iloc[10:] = 80.0                                                          # -20% on day 10
    x.iloc[40:] = 70.0                                                          # -12.5% on day 40
    eps = P.worst_episodes(x, 5, 2)
    assert [round(e[2], 3) for e in eps] == [-0.2, -0.125]
    assert eps[0][1] < eps[1][0] or eps[1][1] < eps[0][0]


# ---------------------------------------------------------------- baskets: gs-quant parity and the "too new" rule
def test_rebalanced_end_matches_gs_quant():
    idx = pd.bdate_range("2022-01-10", periods=400)
    series = [walk(400, k, vol=0.03, idx=idx) for k in range(4)]
    px = pd.concat(series, axis=1)
    gs = ts.basket_series(series, [0.25] * 4, costs=[0.0112] * 4, rebal_freq=ts.RebalFreq.MONTHLY)
    from dateutil.relativedelta import relativedelta
    c = px.index                                                               # gs-quant's own monthly dates: start + n months
    dates = {min(c[c >= c[0] + i * relativedelta(months=1)]) for i in range(1, 30) if c[0] + i * relativedelta(months=1) < c[-1]}
    assert P.rebalanced_end(px, dates, 0.0112) == pytest.approx(float(gs.iloc[-1]), rel=1e-12)
    assert P.rebalanced_end(px, P.monthly_on_day(px.index, 10), 0.0112) == pytest.approx(float(gs.iloc[-1]), rel=1e-12)   # starts on the 10th


def test_list_basket_route_leaves_out_a_new_listing(S):
    idx = pd.bdate_range("2021-01-04", periods=900)
    with S.begin() as s:
        add_market(s, idx, walk(900, 1, vol=0.008, start=8000, idx=idx).to_numpy())
        add_prices(s, "AAA.N0000", walk(900, 2, idx=idx))
        add_prices(s, "BBB.N0000", walk(900, 3, idx=idx))
        add_prices(s, "NEW.N0000", walk(900, 4, idx=idx).iloc[-100:])           # listed about 5 months ago
        g = WatchlistGroup(user_id="local", name="Watchlist 1")
        s.add(g)
        s.flush()
        for k in ("AAA.N0000", "BBB.N0000", "NEW.N0000"):
            s.add(WatchlistGroupItem(group_id=g.id, symbol=k))
    out = quant_api.portfolio_baskets("3y", ME)
    b = out[0]
    assert b["available"] and b["name"] == "Watchlist 1" and b["too_new"] == ["NEW"] and b["symbols"] == ["AAA", "BBB"]
    lo, hi = b["rebalance_day_range"]
    assert lo <= hi and b["charts"]["basket"][0]["value"] == pytest.approx(100 * (1 - P.fee()))      # the first purchase pays the fee
    assert b["figures"]["total_return"] is not None and b["hold_figures"]["total_return"] is not None
    one = quant_api.portfolio_baskets("1y", ME)[0]
    assert one["too_new"] == ["NEW"]                                           # still shorter than a year


def test_portfolio_route_with_and_without_positions(S):
    idx = pd.bdate_range("2023-01-02", periods=400)
    with S.begin() as s:
        add_market(s, idx, walk(400, 1, vol=0.008, start=8000, idx=idx).to_numpy())
    empty = quant_api.portfolio_quant(ME)
    assert empty["curve"]["available"] is False and empty["risk"]["available"] is False
    with S.begin() as s:
        add_prices(s, "AAA.N0000", walk(400, 2, idx=idx))
        add_prices(s, "BBB.N0000", walk(400, 3, idx=idx))
        s.add(Position(user_id="local", symbol="AAA.N0000", entry_date=date(2023, 3, 1), entry_price=100.0, quantity=100))
        s.add(Position(user_id="local", symbol="BBB.N0000", entry_date=date(2023, 6, 1), entry_price=100.0, quantity=50))
    d = quant_api.portfolio_quant(ME)
    c, r = d["curve"], d["risk"]
    assert c["available"] and c["figures"]["volatility"] is not None and c["relative"]["beta"] is not None
    assert c["charts"]["portfolio"][0]["value"] == 100 and len(c["charts"]["aspi"]) == len(c["charts"]["portfolio"])
    assert r["available"] and sum(x["risk_share"] for x in r["rows"]) == pytest.approx(1.0, abs=1e-5)
    assert len(r["correlation"]["matrix"]) == 2 and r["var95_lkr"] < 0
    assert {x["horizon"] for x in r["stress"]} == {"1 week", "1 month", "3 months"}
