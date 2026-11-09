"""Sector data, heatmap, the news/announcement feed, the calendar, key stats and the quote card."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cse_advisor import accounts, listed_meta, market_api
from cse_advisor.auth import User
from cse_advisor.db import Announcement, Base, Headline, ListedMeta, Security, SectorDaily, WatchlistItem

ME = User(uid="uid-a", admin=False)
NOW = datetime.now()


@pytest.fixture()
def S(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Sess = sessionmaker(bind=eng, expire_on_commit=False)
    for mod in (listed_meta, market_api, accounts):
        if hasattr(mod, "SessionLocal"):
            monkeypatch.setattr(mod, "SessionLocal", Sess)
    monkeypatch.setattr(market_api, "_INFO", {})
    monkeypatch.setattr(market_api, "_CAL", {"at": 0.0, "rows": None})
    with Sess.begin() as s:
        s.add_all([Security(symbol="JKH.N0000", cse_id=1, name="JOHN KEELLS HOLDINGS PLC"),
                   Security(symbol="COMB.N0000", cse_id=2, name="COMMERCIAL BANK OF CEYLON PLC")])
        s.add(WatchlistItem(user_id="uid-a", symbol="JKH.N0000"))
    return Sess


LISTED = [
    {"symbol": "JKH.N0000", "name": "JOHN KEELLS", "price": 20.0, "change_pct": 2.0, "turnover": 5e6, "market_cap": 2.0e10},
    {"symbol": "COMB.N0000", "name": "COMMERCIAL BANK", "price": 200.0, "change_pct": -1.0, "turnover": 9e6, "market_cap": 3.0e11},
    {"symbol": "DEAD.N0000", "name": "NO PRICE", "price": None, "change_pct": None, "turnover": 0, "market_cap": 1e9},
    {"symbol": "NEW.N0000", "name": "UNTRACKED", "price": 10.0, "change_pct": 0.0, "turnover": 1e6, "market_cap": None},
]


# ---------------------------------------------------------------- sector data from TradingView
class Resp:
    def __init__(self, data): self._d = data
    def raise_for_status(self): pass
    def json(self): return self._d


def scanner(rows):
    return Resp({"data": [{"s": f"CSELK:{r[0]}", "d": list(r)} for r in rows]})


def test_scanner_rows_are_parsed_and_stored(S, monkeypatch):
    monkeypatch.setattr(listed_meta.httpx, "post", lambda *a, **k: scanner([
        ("JKH.N0000", "John Keells", "Miscellaneous", "Conglomerates", 2.0e10), ("COMB.N0000", "Commercial", "Finance", "Banks", 3.0e11),
        ("ODD.N0000", "No data", None, None, None), ("", "bad", "x", "y", 1)]))
    rows = listed_meta.fetch()
    assert [r["symbol"] for r in rows] == ["JKH.N0000", "COMB.N0000", "ODD.N0000"] and rows[2]["sector"] is None
    with S.begin() as s:
        assert listed_meta.refresh(s, rows) == {"stocks": 3, "new": 3, "with_sector": 2}
    with S.begin() as s:
        assert listed_meta.refresh(s, rows)["new"] == 0                                   # a second run updates, adds nothing
    with S() as s:
        assert listed_meta.lookup(s)["COMB.N0000"] == {"sector": "Finance", "industry": "Banks", "market_cap": 3.0e11}


def test_empty_answer_is_an_error_and_fresh_data_is_not_refetched(S, monkeypatch):
    with S.begin() as s:
        with pytest.raises(RuntimeError):
            listed_meta.refresh(s, [])
    calls = []
    monkeypatch.setattr(listed_meta, "fetch", lambda *a, **k: calls.append(1) or [{"symbol": "A.N0000", "name": "A", "sector": "Finance", "industry": None, "market_cap": 1.0}])
    assert listed_meta.top_up()["stocks"] == 1 and len(calls) == 1
    assert "fresh" in listed_meta.top_up()["skipped"] and len(calls) == 1
    with S.begin() as s:
        s.get(ListedMeta, "A.N0000").updated_at = datetime.utcnow() - timedelta(days=9)
    listed_meta.top_up()
    assert len(calls) == 2                                                                 # a week old: refreshed


# ---------------------------------------------------------------- heatmap
def test_heatmap_lists_priced_stocks_with_sector_and_mine(S, monkeypatch):
    monkeypatch.setattr(market_api, "_listed", lambda: LISTED)
    with S.begin() as s:
        s.add(ListedMeta(symbol="JKH.N0000", sector="Miscellaneous", market_cap=1.0))
        s.add(ListedMeta(symbol="NEW.N0000", sector="Finance", market_cap=5e9))
        s.add(SectorDaily(code="BNK", date=date(2026, 11, 6), name="Banks", change_pct=-0.8, value=100.0))
    h = market_api.heatmap(ME)
    by = {x["symbol"]: x for x in h["stocks"]}
    assert set(by) == {"JKH.N0000", "COMB.N0000", "NEW.N0000"}                            # no price: skipped
    assert by["JKH.N0000"]["mine"] and not by["COMB.N0000"]["mine"]
    assert by["JKH.N0000"]["market_cap"] == 2.0e10                                         # the CSE figure wins
    assert by["NEW.N0000"]["market_cap"] == 5e9 and by["NEW.N0000"]["sector"] == "Finance"   # filled from the sector table
    assert by["COMB.N0000"]["sector"] == "Other"
    assert h["indices"][0]["code"] == "BNK" and h["indices_date"] == "2026-11-06"


# ---------------------------------------------------------------- feed
def seed_feed(S):
    with S.begin() as s:
        s.add_all([
            Announcement(id=1, symbol="JKH.N0000", date=NOW - timedelta(days=1), title="Dividend declared", category="dividend", sentiment=0.6, materiality=4),
            Announcement(id=2, symbol="COMB.N0000", date=NOW - timedelta(days=2), title="Director dealing", category="director_dealing", sentiment=-0.2, materiality=2),
            Announcement(id=3, symbol="JKH.N0000", date=NOW - timedelta(days=40), title="Old notice", category="other", sentiment=0.0, materiality=1),
            Headline(id="1", source="Daily FT", title="Keells profit jumps", link="http://x/1", published=NOW - timedelta(hours=5), symbols=["JKH.N0000"], tone=0.5, kind="company", cluster="c1"),
            Headline(id="2", source="EconomyNext", title="Keells profit jumps (again)", link="http://x/2", published=NOW - timedelta(hours=4), symbols=["JKH.N0000"], tone=0.5, kind="company", cluster="c1"),
            Headline(id="3", source="Daily FT", title="Commercial Bank raises capital", link="http://x/3", published=NOW - timedelta(hours=6), symbols=["COMB.N0000"], tone=-0.4, kind="company"),
            Headline(id="4", source="CBSL", title="Policy rate held", link="http://x/4", published=NOW - timedelta(hours=7), symbols=[], tone=0.0, kind="macro"),
        ])


def test_feed_mine_all_and_filters(S):
    seed_feed(S)
    mine = market_api.feed("mine", "all", "all", "", 14, None, 150, ME)
    ids = [i["id"] for i in mine["items"]]
    assert "a1" in ids and "a2" not in ids and "h3" not in ids                              # only JKH of the two stocks is mine
    assert "h4" in ids                                                                       # macro news concerns everyone
    assert sum(1 for i in ids if i in ("h1", "h2")) == 1                                    # the same story once
    assert "a3" not in ids                                                                   # older than 14 days
    assert ids == sorted(ids, key=lambda i: next(x["at"] for x in mine["items"] if x["id"] == i), reverse=True)
    allf = market_api.feed("all", "all", "all", "", 14, None, 150, ME)
    assert {"a1", "a2", "h3"} <= {i["id"] for i in allf["items"]}
    assert [i["id"] for i in market_api.feed("all", "announcement", "all", "", 60, None, 150, ME)["items"]][-1] == "a3"
    assert {i["type"] for i in market_api.feed("all", "headline", "all", "", 14, None, 150, ME)["items"]} == {"headline"}
    assert [i["id"] for i in market_api.feed("all", "all", "negative", "", 14, None, 150, ME)["items"]] == ["h3", "a2"]
    assert [i["id"] for i in market_api.feed("all", "all", "all", "commercial", 14, None, 150, ME)["items"]] == ["h3", "a2"]   # name or title match
    assert {i["id"] for i in market_api.feed("all", "all", "all", "", 14, "COMB.N0000", 150, ME)["items"]} == {"a2", "h3"}
    assert market_api.feed("all", "all", "all", "", 14, None, 2, ME)["count"] > 2 and len(market_api.feed("all", "all", "all", "", 14, None, 2, ME)["items"]) == 2


# ---------------------------------------------------------------- calendar
def test_calendar_scopes_and_caches(S, monkeypatch):
    calls = []
    today = date.today()

    def fake(symbols):
        calls.append(list(symbols))
        return [{"symbol": "JKH.N0000", "date": (today + timedelta(days=5)).isoformat(), "kind": "ex_dividend", "title": "Ex-dividend Rs 1", "detail": "", "days": 5},
                {"symbol": "COMB.N0000", "date": (today + timedelta(days=30)).isoformat(), "kind": "agm", "title": "AGM", "detail": "", "days": 30},
                {"symbol": "JKH.N0000", "date": (today + timedelta(days=100)).isoformat(), "kind": "results_due", "title": "Results", "detail": "", "days": 100}]
    monkeypatch.setattr(market_api, "_calendar_rows", fake)
    monkeypatch.setattr(market_api.ingest, "watchlist_symbols", lambda s: ["JKH.N0000", "COMB.N0000"])
    c = market_api.calendar("mine", 90, ME)
    assert [e["title"] for e in c["events"]] == ["Ex-dividend Rs 1"]                        # mine only, and the 100-day one is past the horizon
    assert [e["symbol"] for e in market_api.calendar("all", 120, ME)["events"]] == ["JKH.N0000", "COMB.N0000", "JKH.N0000"]
    assert len(calls) == 1                                                                   # built once, then cached


# ---------------------------------------------------------------- key stats and the quote card
INFO = {"p12HiPrice": 250.0, "p12LowPrice": 150.0, "allHiPrice": 525.0, "allLowPrice": 39.0, "ytdHiPrice": 240.0, "ytdLowPrice": 160.0,
        "previousClose": 199.0, "marketCap": 3.0e11, "marketCapPercentage": 4.2, "foreignPercentage": 12.5, "ytdShareVolume": 1.2e8,
        "quantityIssued": 1.5e9, "isin": "LK0053N00005", "hiTrade": 201.0, "lowTrade": 197.0}


def test_key_stats_position_in_the_range():
    k = market_api.key_stats(INFO, {"sector": "Finance", "industry": "Banks"}, 200.0)
    assert k["pos_52w"] == 50 and k["pct_from_high"] == -20.0 and k["sector"] == "Finance" and k["market_cap_pct"] == 4.2
    k = market_api.key_stats(None, None, 10.0)
    assert k["pos_52w"] is None and k["hi_52w"] is None and k["market_cap"] is None            # nothing known: no crash


def test_quote_card_for_an_untracked_stock(S, monkeypatch):
    monkeypatch.setattr(market_api, "_listed", lambda: LISTED)
    monkeypatch.setattr(market_api, "client", lambda: SimpleNamespace(company_info=lambda sym: {"reqSymbolInfo": INFO}))
    with S.begin() as s:
        s.add(ListedMeta(symbol="NEW.N0000", sector="Finance", industry="Banks", market_cap=5e9))
    q = market_api.quote_card("NEW.N0000", ME)
    assert q["tracked"] is False and q["held"] is False and q["lists"] == []
    assert q["quote"]["price"] == 10.0 and q["stats"]["hi_52w"] == 250.0 and q["stats"]["sector"] == "Finance"
    assert market_api.quote_card("JKH.N0000", ME)["tracked"] is True
    with pytest.raises(HTTPException) as e:
        market_api.quote_card("NOPE.N0000", ME)
    assert e.value.status_code == 404


def test_company_info_falls_back_to_the_stored_copy_when_the_cse_is_down(S, monkeypatch):
    def boom():
        raise RuntimeError("down")
    monkeypatch.setattr(market_api, "client", boom)
    with S.begin() as s:
        s.get(Security, "JKH.N0000").info = {"p12HiPrice": 30.0}
    assert market_api._company_info("JKH.N0000") == {"p12HiPrice": 30.0}
    assert market_api._company_info("NEW.N0000") is None


# ---------------------------------------------------------------- compare
def test_compare_series_of_every_kind(S, monkeypatch):
    from datetime import date as d_
    from cse_advisor.db import GlobalPrice, GlobalStock, MacroDaily, MarketDaily
    with S.begin() as s:
        for i in range(3):
            s.add(MarketDaily(date=d_(2026, 11, 2 + i), aspi=21000 + i))
            s.add(MacroDaily(code="spx", date=d_(2026, 11, 2 + i), close=7000 + i))
            s.add(GlobalPrice(symbol="NASDAQ:NVDA", date=d_(2026, 11, 2 + i), open=1, high=1, low=1, close=180 + i, volume=1))
        s.add(GlobalStock(symbol="NASDAQ:NVDA", name="NVIDIA", group="AI & chips", aliases=["Nvidia"]))
    import pandas as pd
    monkeypatch.setattr("cse_advisor.engine.load_adjusted", lambda s, sym: (pd.DataFrame({"close": [10.0, 11.0]}, index=pd.to_datetime(["2026-11-02", "2026-11-03"])), []))
    from cse_advisor import tv_client
    monkeypatch.setattr(tv_client, "fetch_bars", lambda sym, tf, n, exchange="CSELK": [{"ts": datetime(2026, 11, 2, 9, 30), "close": 5.0}] if sym == "OTHER.N0000" else [])
    monkeypatch.setattr(market_api, "_TV_CACHE", {})
    r = market_api.compare("^ASPI,^SPX,NASDAQ:NVDA,JKH.N0000", ME)["series"]
    assert (r["^ASPI"]["label"], len(r["^ASPI"]["points"]), r["^ASPI"]["points"][-1]) == ("ASPI", 3, {"time": "2026-11-04", "value": 21002})
    assert r["^SPX"]["label"] == "S&P 500" and r["^SPX"]["points"][0]["value"] == 7000
    assert r["NASDAQ:NVDA"]["label"] == "NVIDIA" and len(r["NASDAQ:NVDA"]["points"]) == 3
    assert r["JKH.N0000"] == {"label": "JKH", "points": [{"time": "2026-11-02", "value": 10.0}, {"time": "2026-11-03", "value": 11.0}]}
    r = market_api.compare("OTHER.N0000,NOPE.N0000,^ASPI,^SPX,^NDX", ME)["series"]
    assert r["OTHER.N0000"]["points"] == [{"time": "2026-11-02", "value": 5.0}]          # a CSE stock nobody tracks: TradingView
    assert r["NOPE.N0000"]["error"] == "no data" and "^NDX" not in r                       # at most four


def test_compare_options(S):
    from datetime import date as d_
    from cse_advisor.db import GlobalStock, MacroDaily
    with S.begin() as s:
        s.add(MacroDaily(code="spx", date=d_(2026, 11, 2), close=7000))
        s.add(GlobalStock(symbol="NASDAQ:NVDA", name="NVIDIA", group="AI & chips", aliases=["Nvidia"]))
    o = market_api.compare_options(ME)
    assert [x["id"] for x in o["indices"]] == ["^ASPI", "^SPX"]                            # only series that have data
    assert o["cse"] == [{"id": "JKH.N0000", "label": "JKH", "name": "JOHN KEELLS HOLDINGS PLC"}]
    assert o["world"] == [{"id": "NASDAQ:NVDA", "label": "NVDA", "name": "NVIDIA"}]
