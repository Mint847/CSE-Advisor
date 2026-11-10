"""Phase 4 (portfolio and research): the fee breakdown, editing positions, partial sells, the split/bonus heads-up, fees in the
portfolio totals, and the EQS screener."""
from datetime import date, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cse_advisor import eqs, main, portfolio, position_ops as PO
from cse_advisor.auth import User
from cse_advisor.db import Base, CorporateAction, Note, Position, Security

ME = User(uid="local", admin=True)
TODAY = date(2026, 11, 10)


@pytest.fixture()
def DB(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Sess = sessionmaker(bind=eng, expire_on_commit=False)
    monkeypatch.setattr(main, "SessionLocal", Sess)
    with Sess.begin() as s:
        s.add(Security(symbol="AAA.N0000", cse_id=1, name="AAA PLC"))
    return Sess


def add_pos(S, **kw):
    base = dict(user_id="local", symbol="AAA.N0000", entry_date=date(2026, 1, 5), entry_price=100.0, quantity=1000)
    base.update(kw)
    with S.begin() as s:
        p = Position(**base)
        s.add(p)
        s.flush()
        return p.id


# ---------------------------------------------------------------- fee breakdown
def test_fee_parts_add_up_to_the_all_in_rate():
    b = PO.fee_breakdown({"costs": {"brokerage_pct_per_side": 1.12, "slippage_pct": 0.2}})
    assert b["per_side_pct"] == 1.12 and sum(p["pct"] for p in b["parts"]) == pytest.approx(1.12) and len(b["parts"]) == 5
    lines = PO.fee_lines(1_000_000, {"costs": {"brokerage_pct_per_side": 1.12}})
    assert {x["label"]: x["lkr"] for x in lines} == {"Brokerage": 6400.0, "CSE fee": 840.0, "SEC fee": 720.0, "CDS fee": 240.0, "Share transaction levy": 3000.0}


def test_a_different_all_in_rate_gets_a_balancing_line_so_the_total_still_matches():
    higher = PO.fee_breakdown({"costs": {"brokerage_pct_per_side": 1.50}})
    assert sum(p["pct"] for p in higher["parts"]) == pytest.approx(1.50) and higher["parts"][-1]["label"].startswith("Other") and higher["parts"][-1]["pct"] == pytest.approx(0.38)
    lower = PO.fee_breakdown({"costs": {"brokerage_pct_per_side": 1.0}})
    assert sum(p["pct"] for p in lower["parts"]) == pytest.approx(1.0) and lower["parts"][-1]["label"].startswith("Adjustment")
    own = PO.fee_breakdown({"costs": {"brokerage_pct_per_side": 1.0, "fee_breakdown": [{"label": "All-in", "pct": 1.0}]}})
    assert own["parts"] == [{"label": "All-in", "pct": 1.0}]


# ---------------------------------------------------------------- edit
def test_edit_changes_only_what_is_sent_and_checks_it(DB):
    pid = add_pos(DB, stop=90.0, target=130.0, notes="first")
    with DB.begin() as s:
        PO.apply_edit(s.get(Position, pid), {"quantity": 1200, "entry_price": 98.5, "stop": None}, TODAY)
    with DB() as s:
        p = s.get(Position, pid)
        assert (p.quantity, p.entry_price, p.stop, p.target, p.notes, p.entry_date) == (1200, 98.5, None, 130.0, "first", date(2026, 1, 5))
    bad = [({"quantity": 0}, "at least 1"), ({"entry_price": -1}, "above 0"), ({"entry_date": TODAY + timedelta(days=1)}, "future"), ({"target": -5}, "target")]
    for e, msg in bad:
        with DB() as s, pytest.raises(ValueError, match=msg):
            PO.apply_edit(s.get(Position, pid), e, TODAY)


def test_editing_a_closed_position_checks_the_sell_side(DB):
    pid = add_pos(DB, closed=True, exit_date=date(2026, 3, 1), exit_price=120.0)
    with DB.begin() as s:
        PO.apply_edit(s.get(Position, pid), {"exit_price": 125.0, "exit_date": date(2026, 3, 2)}, TODAY)
    with DB() as s:
        p = s.get(Position, pid)
        assert (p.exit_price, p.exit_date) == (125.0, date(2026, 3, 2))
        with pytest.raises(ValueError, match="before the buy"):
            PO.apply_edit(p, {"exit_date": date(2026, 1, 1)}, TODAY)
        with pytest.raises(ValueError, match="sell price"):
            PO.apply_edit(p, {"exit_price": 0}, TODAY)


def test_edit_route_clears_stop_and_keeps_the_rest(DB):
    pid = add_pos(DB, stop=90.0)
    main.edit_position(pid, main.PositionEdit(stop=None, quantity=900), ME)
    with DB() as s:
        p = s.get(Position, pid)
        assert p.stop is None and p.quantity == 900 and p.entry_price == 100.0
    with pytest.raises(HTTPException) as e:
        main.edit_position(pid, main.PositionEdit(quantity=0), ME)
    assert e.value.status_code == 400 and "at least 1" in e.value.detail
    with pytest.raises(HTTPException) as e:
        main.edit_position(pid, main.PositionEdit(quantity=5), User(uid="someone-else", admin=False))
    assert e.value.status_code == 404


# ---------------------------------------------------------------- partial sells
def test_partial_sell_splits_the_position_and_the_profit_is_worked_out_by_hand(DB):
    pid = add_pos(DB, quantity=1000, entry_price=100.0, stop=90.0)
    out = main.close_position(pid, main.PositionClose(exit_date=date(2026, 6, 1), exit_price=120.0, quantity=400, review="took some off"), ME)
    assert out == {"ok": True, "sold": 400, "remaining": 600}
    with DB() as s:
        rows = s.scalars(select(Position).order_by(Position.id)).all()
        notes = s.scalars(select(Note)).all()
    keep, part = rows
    assert (keep.closed, keep.quantity, keep.stop) == (False, 600, 90.0)
    assert (part.closed, part.quantity, part.entry_price, part.entry_date, part.exit_price, part.stop) == (True, 400, 100.0, date(2026, 1, 5), 120.0, 90.0)
    assert "sold 400 of 1000" in part.notes and notes[0].position_id == part.id and notes[0].tags == ["review"]
    f = 0.0112
    fig = portfolio.position_figures({"quantity": 400, "entry_price": 100.0, "closed": True, "exit_price": 120.0}, 1.12)
    assert fig["realised_net"] == pytest.approx(400 * 120 * (1 - f) - 400 * 100 * (1 + f), abs=0.01)       # 48,000 - 537.6 - 40,000 - 448 = 7,014.4
    assert fig["realised_net"] == pytest.approx(7014.4, abs=0.01)
    # selling the rest closes the original row
    main.close_position(pid, main.PositionClose(exit_date=date(2026, 7, 1), exit_price=110.0), ME)
    with DB() as s:
        assert s.get(Position, pid).closed and s.get(Position, pid).quantity == 600 and s.get(Position, pid).exit_price == 110.0


def test_selling_the_whole_quantity_is_a_plain_close_and_overselling_is_refused(DB):
    pid = add_pos(DB, quantity=500)
    with pytest.raises(HTTPException) as e:
        main.close_position(pid, main.PositionClose(exit_date=date(2026, 6, 1), exit_price=120.0, quantity=501), ME)
    assert e.value.status_code == 400 and "hold 500" in e.value.detail
    with pytest.raises(HTTPException) as e:
        main.close_position(pid, main.PositionClose(exit_date=date(2025, 6, 1), exit_price=120.0), ME)
    assert "before the buy" in e.value.detail
    with pytest.raises(HTTPException):
        main.close_position(pid, main.PositionClose(exit_date=date(2026, 6, 1), exit_price=120.0, quantity=0), ME)
    out = main.close_position(pid, main.PositionClose(exit_date=date(2026, 6, 1), exit_price=120.0, quantity=500), ME)
    assert out == {"ok": True, "sold": 500, "remaining": 0}
    with DB() as s:
        assert len(s.scalars(select(Position)).all()) == 1 and s.get(Position, pid).closed
    with pytest.raises(HTTPException) as e:
        main.close_position(pid, main.PositionClose(exit_date=date(2026, 6, 2), exit_price=121.0), ME)
    assert "already closed" in e.value.detail


# ---------------------------------------------------------------- split / bonus since the purchase
def test_a_split_after_the_buy_suggests_todays_shares_and_price(DB):
    pid = add_pos(DB, entry_date=date(2024, 3, 1), entry_price=190.0, quantity=500)
    with DB.begin() as s:
        s.add(CorporateAction(symbol="AAA.N0000", kind="split", ex_date=date(2024, 10, 21), ratio_new=10, ratio_old=1))
        s.add(CorporateAction(symbol="AAA.N0000", kind="rights", ex_date=date(2025, 5, 1), ratio_new=1, ratio_old=5, price=15.0))
        s.add(CorporateAction(symbol="AAA.N0000", kind="split", ex_date=date(2023, 1, 1), ratio_new=2, ratio_old=1))              # before the buy: not shown
        s.add(CorporateAction(symbol="AAA.N0000", kind="bonus", ex_date=date(2025, 8, 1), ratio_new=1, ratio_old=1, ignore=True))   # ignored
    with DB() as s:
        h = PO.corp_hints(s, s.get(Position, pid))
    assert [x["kind"] for x in h] == ["split", "rights"] and h[0]["share_multiplier"] == 10 and h[1]["share_multiplier"] is None
    assert h[1]["suggest"] == {"quantity": 5000, "entry_price": 19.0} and "5,000 shares bought at 19" in h[1]["text"] and "price adjusted" in h[1]["text"]
    with DB.begin() as s:
        s.get(Position, pid).closed = True
    with DB() as s:
        assert PO.corp_hints(s, s.get(Position, pid)) == []                         # nothing to fix on a closed position


def test_a_bonus_issue_multiplies_the_shares_by_one_plus_the_ratio(DB):
    pid = add_pos(DB, entry_date=date(2026, 1, 5), quantity=1000, entry_price=60.0)
    with DB.begin() as s:
        s.add(CorporateAction(symbol="AAA.N0000", kind="bonus", ex_date=date(2026, 5, 1), ratio_new=1, ratio_old=2))
    with DB() as s:
        (h,) = PO.corp_hints(s, s.get(Position, pid))
    assert h["share_multiplier"] == pytest.approx(1.5) and h["suggest"] == {"quantity": 1500, "entry_price": 40.0}


# ---------------------------------------------------------------- fees in the portfolio totals
def test_portfolio_totals_show_the_fees_paid_and_the_fee_to_sell_what_is_open():
    rows = [
        {"id": 1, "symbol": "AAA.N0000", "quantity": 1000, "entry_price": 100.0, "closed": False, "price": 110.0, "stop_used": None},
        {"id": 2, "symbol": "BBB.N0000", "quantity": 500, "entry_price": 50.0, "closed": True, "exit_price": 60.0},
    ]
    s = portfolio.summarize(rows, 1_000_000, 1.12)
    f = 0.0112
    assert s["fees_paid"] == pytest.approx(1000 * 100 * f + 500 * 50 * f + 500 * 60 * f)          # both buys and the one sale
    assert s["fees_to_sell_open"] == pytest.approx(1000 * 110 * f)


def test_costs_route_returns_the_breakdown():
    out = main.costs(ME)
    assert out["per_side_pct"] == pytest.approx(1.12) and sum(p["pct"] for p in out["parts"]) == pytest.approx(out["per_side_pct"])


# ---------------------------------------------------------------- EQS
def stock(sym, **kw):
    base = {"symbol": sym, "name": sym.split(".")[0] + " PLC", "price": 50.0, "change_pct": 1.0, "turnover": 5e6, "volume": 1e5, "market_cap": 1e9,
            "sector": "Banks", "industry": "Banks", "mine": False, "held": False, "scr": None, "quant": None}
    base.update(kw)
    return base


UNIVERSE = [
    stock("AAA.N0000", scr={"ret120": 0.30, "rs_rank": 0.9, "dist52": -0.05, "trendup": True, "qualifies": True, "hits": 3, "turn": 4e6, "above50": True}, quant={"vol": 0.25, "sharpe": 1.0, "beta": 0.8, "mdd": -0.15}, mine=True),
    stock("BBB.N0000", scr={"ret120": -0.10, "rs_rank": 0.2, "dist52": -0.40, "trendup": False, "qualifies": False, "hits": 0, "turn": 9e6, "above50": False}, quant={"vol": 0.55, "sharpe": -0.4, "beta": 1.6, "mdd": -0.45}),
    stock("CCC.N0000", sector="Telecom", change_pct=6.0, turnover=2e7, scr={"ret120": 0.10, "rs_rank": 0.5, "dist52": -0.12, "trendup": True, "qualifies": False, "hits": 2, "turn": 1e7, "above50": True}),
    stock("DDD.N0000", sector=None, change_pct=None, turnover=1e3),                                    # illiquid: no candidate or risk figures
]


def run(filters, **kw):
    return eqs.run(UNIVERSE, {"filters": filters, **kw})


def syms(r):
    return [x["symbol"].split(".")[0] for x in r["rows"]]


def test_numeric_bool_text_and_range_filters_and_percent_units():
    assert syms(run([{"field": "ret120", "op": ">", "value": 5}], sort={"field": "ret120", "dir": "desc"})) == ["AAA", "CCC"]          # 30% and 10% (stored as 0.30, 0.10)
    assert syms(run([{"field": "vol", "op": "<", "value": 30}])) == ["AAA"]
    assert syms(run([{"field": "dist52", "op": "between", "value": [-15, -1]}], sort={"field": "dist52", "dir": "desc"})) == ["AAA", "CCC"]
    assert syms(run([{"field": "qualifies", "op": "is", "value": True}])) == ["AAA"]
    assert syms(run([{"field": "trendup", "op": "is", "value": False}])) == ["BBB"]
    assert syms(run([{"field": "sector", "op": "is", "value": "telecom"}])) == ["CCC"]
    assert syms(run([{"field": "sector", "op": "in", "value": ["Banks", "Telecom"]}], sort={"field": "price", "dir": "asc"})) == ["AAA", "BBB", "CCC"]
    assert syms(run([{"field": "name", "op": "contains", "value": "bb"}])) == ["BBB"]
    assert syms(run([{"field": "mine", "op": "is", "value": True}])) == ["AAA"]
    assert syms(run([{"field": "sharpe", "op": ">", "value": 0.5}, {"field": "beta", "op": "<=", "value": 0.8}])) == ["AAA"]      # conditions are ANDed


def test_stocks_without_a_value_are_left_out_counted_and_always_sorted_last():
    r = run([{"field": "turnover", "op": ">", "value": 0}], sort={"field": "vol", "dir": "desc"})
    assert syms(r) == ["BBB", "AAA", "CCC", "DDD"]                                                  # CCC and DDD have no vol: last, in their own order
    r = run([{"field": "vol", "op": ">", "value": 0}])
    assert r["matched"] == 2 and r["total"] == 4 and any("2 stocks have no value for “Volatility, 1 year”" in n and "liquid stocks and the stocks you track" in n for n in r["notes"])
    assert run([{"field": "change_pct", "op": ">", "value": 0}])["notes"][0].startswith("1 stocks have no value for “Change today”")
    asc = run([], sort={"field": "vol", "dir": "asc"})
    assert syms(asc) == ["AAA", "BBB", "CCC", "DDD"]


def test_columns_limit_and_presets():
    r = run([{"field": "ret120", "op": ">", "value": 0}], sort={"field": "sharpe", "dir": "desc"}, limit=1)
    assert r["columns"] == ["price", "change_pct", "turnover", "ret120", "sharpe"] and r["shown"] == 1 and r["matched"] == 2 and r["rows"][0]["values"]["sharpe"] == 1.0
    for p in eqs.PRESETS:                                                                           # every preset is valid on its own
        out = eqs.run(UNIVERSE, p["spec"])
        assert out["total"] == 4
    assert syms(eqs.run(UNIVERSE, next(p for p in eqs.PRESETS if p["id"] == "rule")["spec"])) == ["AAA"]
    cat = eqs.catalog()
    assert {f["id"] for f in cat["fields"]} == set(eqs.FIELDS)
    assert next(f for f in cat["fields"] if f["id"] == "vol")["ops"] == eqs.NUM_OPS and next(f for f in cat["fields"] if f["id"] == "sector")["ops"] == eqs.TEXT_OPS


@pytest.mark.parametrize("bad, msg", [
    ({"filters": [{"field": "nope", "op": ">", "value": 1}]}, "Unknown field"),
    ({"filters": [{"field": "vol", "op": "contains", "value": 1}]}, "does not apply"),
    ({"filters": [{"field": "vol", "op": ">", "value": "abc"}]}, "enter a number"),
    ({"filters": [{"field": "vol", "op": "between", "value": [1]}]}, "two numbers"),
    ({"filters": [{"field": "vol", "op": ">", "value": float("inf")}]}, "not valid"),
    ({"filters": [{"field": "trendup", "op": "is", "value": "yes"}]}, "yes or no"),
    ({"filters": [{"field": "sector", "op": "in", "value": []}]}, "at least one"),
    ({"filters": [{"field": "name", "op": "contains", "value": ""}]}, "something to look for"),
    ({"filters": [{"field": "vol", "op": ">", "value": 1}] * 11}, "at most 10"),
    ({"filters": [], "sort": {"field": "vol", "dir": "up"}}, "Sort by"),
])
def test_eqs_rejects_bad_screens_in_plain_words(bad, msg):
    with pytest.raises(ValueError, match=msg):
        eqs.run(UNIVERSE, bad)


def test_eqs_route_maps_errors_to_400(monkeypatch):
    monkeypatch.setattr(main, "_stock_rows", lambda u: (UNIVERSE, []))
    out = main.eqs_run({"filters": [{"field": "qualifies", "op": "is", "value": True}]}, ME)
    assert out["matched"] == 1 and out["rows"][0]["symbol"] == "AAA.N0000"
    with pytest.raises(HTTPException) as e:
        main.eqs_run({"filters": [{"field": "nope", "op": ">", "value": 1}]}, ME)
    assert e.value.status_code == 400 and "Unknown field" in e.value.detail
    assert len(main.eqs_catalog(ME)["presets"]) == len(eqs.PRESETS)


# ---------------------------------------------------------------- acknowledging the split / bonus note
def test_editing_the_shares_or_dismissing_clears_the_note_until_a_new_action_appears(DB):
    pid = add_pos(DB, entry_date=date(2026, 1, 5), quantity=1000, entry_price=60.0)
    with DB.begin() as s:
        s.add(CorporateAction(symbol="AAA.N0000", kind="bonus", ex_date=date(2026, 5, 1), ratio_new=1, ratio_old=2))
    assert main._positions("local")[0]["corp_since_entry"][0]["suggest"] == {"quantity": 1500, "entry_price": 40.0}
    main.edit_position(pid, main.PositionEdit(quantity=1500, entry_price=40.0), ME)           # you applied the suggestion
    assert main._positions("local")[0]["corp_since_entry"] == []                               # no second suggestion (it would say 2,250)
    main.edit_position(pid, main.PositionEdit(stop=35.0), ME)                                  # an unrelated edit changes nothing here
    assert main._positions("local")[0]["corp_since_entry"] == []
    with DB.begin() as s:
        s.add(CorporateAction(symbol="AAA.N0000", kind="split", ex_date=date(2026, 8, 1), ratio_new=2, ratio_old=1))
    (h,) = main._positions("local")[0]["corp_since_entry"]                                    # a NEW action is shown again
    assert h["kind"] == "split" and h["suggest"] == {"quantity": 3000, "entry_price": 20.0}
    assert main.acknowledge_corp(pid, ME) == {"ok": True, "dismissed": 1}                       # dismissed without editing
    assert main._positions("local")[0]["corp_since_entry"] == []
    assert main.acknowledge_corp(pid, ME)["dismissed"] == 0
    with pytest.raises(HTTPException) as e:
        main.acknowledge_corp(pid, User(uid="someone-else", admin=False))
    assert e.value.status_code == 404
