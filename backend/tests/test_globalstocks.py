"""World stocks: the preset list, time zones, prices, the engines, big moves, the headlines around them, quotes, routes."""
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cse_advisor import globalstocks as gx
from cse_advisor import globalstocks_api as api
from cse_advisor.auth import User
from cse_advisor.db import Base, GlobalAnalysis, GlobalHeadline, GlobalMove, GlobalPrice, GlobalStock, JobRun

ADMIN = User(uid="local", admin=True)
NVDA = "NASDAQ:NVDA"


@pytest.fixture()
def S(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Sess = sessionmaker(bind=eng, expire_on_commit=False)
    for mod in (gx, api):
        monkeypatch.setattr(mod, "SessionLocal", Sess)
    monkeypatch.setattr(gx, "_quotes", {"at": 0.0, "rows": {}})
    monkeypatch.setattr(gx, "_intra", {})
    monkeypatch.setattr(gx, "_ia", {})
    monkeypatch.setattr(gx, "_get", lambda url: (_ for _ in ()).throw(AssertionError("network in a test: " + url)))
    monkeypatch.setattr(gx.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network in a test")))
    return Sess


def walk(n=320, start=100.0, seed=1, jumps=None):
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0005, 0.015, n)
    for i, v in (jumps or {}).items():
        r[i] = v
    close = start * np.cumprod(1 + r)
    idx = pd.bdate_range("2025-06-02", periods=n)
    return pd.DataFrame({"open": close * 0.998, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1e6}, index=idx)


def put_frame(S, symbol, df):
    with S.begin() as s:
        for ts, r in df.iterrows():
            s.add(GlobalPrice(symbol=symbol, date=ts.date(), open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume))


def add_stock(S, symbol=NVDA, **kw):
    with S.begin() as s:
        s.add(GlobalStock(symbol=symbol, name=kw.get("name", "NVIDIA"), group=kw.get("group", "AI & chips"),
                          aliases=kw.get("aliases", ["Nvidia", "NVDA", "Jensen Huang"]), currency=kw.get("currency", "USD")))


# ---------------------------------------------------------------- the list
def test_presets_are_added_once_and_a_deleted_one_stays_deleted(S):
    with S.begin() as s:
        assert gx.seed(s) == len(gx.PRESETS)
    with S.begin() as s:
        gx.remove(s, NVDA)
    with S.begin() as s:
        assert gx.seed(s) == 0
        syms = [g.symbol for g in gx.stocks(s)]
    assert NVDA not in syms and "KRX:000660" in syms and "NASDAQ:MU" in syms
    groups = [g for g in dict.fromkeys(p["group"] for p in gx.PRESETS)]
    assert groups == gx.GROUP_ORDER


def test_default_aliases_from_a_company_name():
    assert gx.default_aliases("Palantir Technologies Inc.", "PLTR") == ["Palantir Technologies", "PLTR"]
    assert gx.default_aliases("Samsung Electronics Co., Ltd.", "005930") == ["Samsung Electronics"]


# ---------------------------------------------------------------- prices and time zones
def test_bars_are_moved_to_the_exchange_clock(monkeypatch):
    from cse_advisor import tv_client
    # TradingView answers in Colombo wall time: a NASDAQ session opening 09:30 New York (EST) is 20:00 in Colombo
    rows = [{"ts": datetime(2026, 11, 5, 20, 0), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
            {"ts": datetime(2026, 11, 6, 20, 0), "open": 1.5, "high": 2, "low": 1, "close": 1.8, "volume": None}]
    monkeypatch.setattr(tv_client, "fetch_bars", lambda t, tf, n, exchange: rows)
    d = gx.bars(NVDA, "1D")
    assert [str(x.date()) for x in d.index] == ["2026-11-05", "2026-11-06"] and d["volume"].iloc[1] == 0
    i = gx.bars(NVDA, "1h")
    assert str(i.index[0]) == "2026-11-05 09:30:00"                                         # New York wall time
    k = gx.bars("KRX:000660", "1h")
    assert str(k.index[0]) == "2026-11-05 23:30:00"                                         # Seoul is 3.5 h ahead of Colombo


def test_stored_bars_are_upserted(S):
    df = walk(5)
    with S.begin() as s:
        gx.store_bars(s, NVDA, df)
        gx.store_bars(s, NVDA, df.assign(close=df["close"] + 1))
    with S() as s:
        f = gx.frame(s, NVDA)
    assert len(f) == 5 and np.allclose(f["close"], df["close"] + 1) and (f["turnover"] == f["close"] * f["volume"]).all()


def test_completed_drops_the_bar_still_forming():
    idx = pd.DatetimeIndex(["2026-11-06 14:00", "2026-11-06 15:00"])
    df = pd.DataFrame({"close": [1, 2]}, index=idx)
    assert len(gx.completed(df, "1h", NVDA, now=datetime(2026, 11, 6, 15, 30))) == 1
    assert len(gx.completed(df, "1h", NVDA, now=datetime(2026, 11, 6, 16, 1))) == 2


# ---------------------------------------------------------------- the engines
def test_engines_run_and_cse_only_parts_are_removed(S):
    add_stock(S)
    put_frame(S, NVDA, walk())
    with S.begin() as s:
        r = gx.analyze(s, s.get(GlobalStock, NVDA))
    assert r["verdict"] in ("BUY", "WATCH", "AVOID") and r["verdict_analyst"] and r["verdict_smc"]
    for k in ("original", "analyst", "smc"):
        plan = r["recommendation"][k].get("plan")
        assert plan is None or plan["size"] is None
    assert r["recommendation"]["world"]["currency"] == "USD"
    assert all("LKR" not in n for n in r["liquidity"].get("notes", []))
    with S() as s:
        row = gx.latest_analysis(s, NVDA)
        assert row.verdict == r["verdict"] and row.result["recommendation"]["world"]


def test_too_little_history_gives_no_analysis(S):
    add_stock(S)
    put_frame(S, NVDA, walk(20))
    with S.begin() as s:
        assert gx.analyze(s, s.get(GlobalStock, NVDA)) is None


# ---------------------------------------------------------------- big moves
def test_big_moves_are_found_once(S):
    put_frame(S, NVDA, walk(300, jumps={250: -0.092, 270: 0.061, 280: 0.02}))
    with S.begin() as s:
        got = gx.find_moves(s, NVDA, threshold=4)
    big = {round(m.pct, 1) for m in got}
    assert -9.2 in big and 6.1 in big and 2.0 not in big
    with S.begin() as s:
        assert len(gx.find_moves(s, NVDA, threshold=4)) == len(got)
        assert len(s.scalars(select(GlobalMove)).all()) == len(got)                        # not duplicated


# ---------------------------------------------------------------- news
def test_names_match_as_names_not_as_words():
    g = GlobalStock(symbol="NASDAQ:META", name="Meta", aliases=["Meta Platforms", "Meta"])
    p = gx._patterns(g)
    assert gx.about("Meta shares slide after earnings", p) and gx.about("META stock", p)
    assert not gx.about("A meta-analysis of chip demand", p) and not gx.about("the meta story", p)
    i = gx._patterns(GlobalStock(symbol="NASDAQ:INTC", name="Intel", aliases=["Intel"]))
    assert gx.about("Intel falls 5%", i) and not gx.about("Artificial intelligence spending", i)
    n = gx._patterns(GlobalStock(symbol=NVDA, name="NVIDIA", aliases=["Nvidia", "Jensen Huang"]))
    assert gx.about("NVIDIA unveils chip", n) and gx.about("Jensen Huang speaks", n)


def item(title, when, link=None):
    link = link or "http://x/" + title.replace(" ", "-")
    return {"id": link[-40:], "title": title, "link": link, "outlet": "Reuters", "published": when}


def test_headlines_around_a_move_put_the_matching_direction_first(S):
    add_stock(S)
    d = date(2026, 10, 30)
    with S.begin() as s:
        s.add(GlobalMove(symbol=NVDA, date=d, pct=-4.2, news_checked=True))
        gs = s.get(GlobalStock, NVDA)
        n = gx.store_news(s, gs, [
            item("Can you make money off Nvidia stock now?", datetime(2026, 10, 30, 9, 0)),
            item("Nvidia shares drop amid chip selloff", datetime(2026, 10, 31, 1, 0)),
            item("Nvidia stock jumps on deal", datetime(2026, 10, 30, 22, 0)),
            item("Developer blog: CUDA tips", datetime(2026, 10, 30, 10, 0)),                # doesn't name the company: dropped
            item("Nvidia earnings preview", datetime(2026, 10, 20, 10, 0)),                   # outside the window
        ])
    assert n == 4
    with S() as s:
        hs = gx.move_headlines(s, NVDA, d)
    assert [h["title"] for h in hs][0] == "Nvidia shares drop amid chip selloff"
    assert "Nvidia earnings preview" not in [h["title"] for h in hs] and len(hs) == 3


def test_move_news_search_is_dated_and_marks_the_move(S):
    add_stock(S)
    seen = []
    xml = ("<rss><channel><item><title>Nvidia stock falls 9% - Reuters</title><link>http://r/1</link><source>Reuters</source>"
           "<pubDate>Thu, 05 Nov 2026 21:00:00 GMT</pubDate></item></channel></rss>")
    with S.begin() as s:
        mv = GlobalMove(symbol=NVDA, date=date(2026, 11, 5), pct=-9.26, news_checked=False)
        s.add(mv)
        assert gx.fetch_move_news(s, s.get(GlobalStock, NVDA), mv, get=lambda u: seen.append(u) or xml) == 1
        assert mv.news_checked
    assert "after%3A2026-11-04" in seen[0] and "before%3A2026-11-07" in seen[0]
    with S() as s:
        h = s.scalar(select(GlobalHeadline))
        assert (h.title, h.outlet, h.move_date) == ("Nvidia stock falls 9%", "Reuters", date(2026, 11, 5))
        assert h.tone is not None and h.tone < 0


# ---------------------------------------------------------------- quotes
def test_quotes_are_cached_and_survive_a_failed_refresh(monkeypatch):
    monkeypatch.setattr(gx, "_quotes", {"at": 0.0, "rows": {}})
    calls = []

    def scan(syms):
        calls.append(syms)
        if len(calls) > 1:
            raise RuntimeError("down")
        return {s: {"close": 1.0, "change": 2.0} for s in syms}
    monkeypatch.setattr(gx, "scan", scan)
    assert gx.quotes([NVDA])[NVDA]["close"] == 1.0
    assert gx.quotes([NVDA])[NVDA]["close"] == 1.0 and len(calls) == 1                       # from the cache
    assert gx.quotes([NVDA], max_age=0)[NVDA]["close"] == 1.0                                # refresh failed: the last quote stays


def test_resolve_tries_exchanges_in_order(monkeypatch):
    monkeypatch.setattr(gx, "scan", lambda c: {"NYSE:ORCL": {"description": "Oracle Corporation", "currency": "USD"}} if "NYSE:ORCL" in c else {})
    assert gx.resolve("orcl") == {"symbol": "NYSE:ORCL", "name": "Oracle Corporation", "currency": "USD"}
    with pytest.raises(ValueError):
        gx.resolve("NOPE")
    with pytest.raises(ValueError):
        gx.resolve("  ")


# ---------------------------------------------------------------- the nightly run and the routes
def test_run_all_records_its_run(S, monkeypatch):
    with S.begin() as s:
        s.merge(__import__("cse_advisor.db", fromlist=["Preference"]).Preference(key=gx.SEEDED_KEY, value=True))
    add_stock(S)
    add_stock(S, "KRX:000660", name="SK Hynix", aliases=["SK Hynix"], currency=None)
    monkeypatch.setattr(gx, "scan", lambda syms: {"KRX:000660": {"currency": "KRW"}})
    monkeypatch.setattr(gx, "refresh_one", lambda sym, news=True, move_news=0: {"bars": 1} if sym == NVDA else {"error": "no data"})
    out = gx.run_all()
    assert set(out) == {NVDA, "KRX:000660"}
    with S() as s:
        r = s.scalar(select(JobRun).where(JobRun.job == "global"))
        assert r.ok is False and "KRX:000660" in r.message
        assert s.get(GlobalStock, "KRX:000660").currency == "KRW"


def test_list_route(S, monkeypatch):
    with S.begin() as s:
        s.merge(__import__("cse_advisor.db", fromlist=["Preference"]).Preference(key=gx.SEEDED_KEY, value=True))
    add_stock(S)
    put_frame(S, NVDA, walk(80, jumps={78: -0.09}))
    with S.begin() as s:
        gx.find_moves(s, NVDA, threshold=4, days=4000)
    monkeypatch.setattr(gx, "quotes", lambda syms, max_age=None: {NVDA: {"close": 186.6, "change": 2.78, "currency": "USD", "delay_min": 15}})
    d = api.world_list(ADMIN)
    r = d["rows"][0]
    assert r["ticker"] == "NVDA" and r["exchange"] == "NASDAQ" and r["quote"]["change_pct"] == 2.78 and r["quote"]["delay_min"] == 15
    assert len(r["spark"]) == 60 and r["analysis"] is None
    assert r["move"] is None or r["move"]["pct"] < -8
    monkeypatch.setattr(gx, "quotes", lambda syms, max_age=None: {})                          # scanner down: the stored close
    r = api.world_list(ADMIN)["rows"][0]
    assert r["quote"]["stored"] and r["quote"]["close"] == pytest.approx(r["spark"][-1])


def test_add_edit_delete_routes(S, monkeypatch):
    monkeypatch.setattr(gx, "resolve", lambda q: {"symbol": "NYSE:ORCL", "name": "Oracle Corporation", "currency": "USD"})
    monkeypatch.setattr(gx, "refresh_one", lambda sym, news=True, move_news=0: {"bars": 1500})
    monkeypatch.setattr(gx, "backfill_move_news_async", lambda sym, limit=12: False)
    r = api.world_add(api.AddIn(query="orcl", group="Software"), ADMIN)
    assert r["symbol"] == "NYSE:ORCL"
    with S() as s:
        g = s.get(GlobalStock, "NYSE:ORCL")
        assert g.group == "Software" and g.aliases == ["Oracle", "ORCL"]
    with pytest.raises(HTTPException):
        api.world_add(api.AddIn(query="orcl"), ADMIN)                                         # already there
    api.world_edit("NYSE:ORCL", api.EditIn(group="Big tech", aliases=["Oracle", "Larry Ellison"]), ADMIN)
    put_frame(S, "NYSE:ORCL", walk(5))
    api.world_delete("NYSE:ORCL", ADMIN)
    with S() as s:
        assert s.get(GlobalStock, "NYSE:ORCL") is None and not s.scalars(select(GlobalPrice)).all()
    with pytest.raises(HTTPException) as e:
        api.world_delete("NYSE:ORCL", ADMIN)
    assert e.value.status_code == 404


def test_analysis_and_candle_routes(S):
    add_stock(S)
    with pytest.raises(HTTPException) as e:
        api.world_analysis(NVDA, "1D", ADMIN)
    assert e.value.status_code == 404                                                         # not analysed yet
    put_frame(S, NVDA, walk())
    with S.begin() as s:
        gx.analyze(s, s.get(GlobalStock, NVDA))
    a = api.world_analysis(NVDA, "1D", ADMIN)
    assert a["symbol"] == NVDA and a["name"] == "NVIDIA" and a["currency"] == "USD" and a["patterns"] is not None
    c = api.world_candles(NVDA, "1D", ADMIN)
    assert len(c["bars"]) == 320 and "ema50" in c["indicators"]
    w = api.world_candles(NVDA, "1W", ADMIN)
    assert 60 <= len(w["bars"]) <= 70
    with pytest.raises(HTTPException):
        api.world_candles(NVDA, "7m", ADMIN)


def test_a_stock_whose_prices_did_not_come_gets_a_second_try(S, monkeypatch):
    with S.begin() as s:
        s.merge(__import__("cse_advisor.db", fromlist=["Preference"]).Preference(key=gx.SEEDED_KEY, value=True))
    add_stock(S)
    monkeypatch.setattr(gx, "scan", lambda syms: {})
    monkeypatch.setattr(gx.time, "sleep", lambda s: None)
    calls = []
    monkeypatch.setattr(gx, "refresh_one", lambda sym, news=True, move_news=0: calls.append(news) or {"bars": 0 if len(calls) == 1 else 30})
    out = gx.run_all()
    assert calls == [True, False] and out[NVDA]["bars"] == 30 and out[NVDA]["second_try"]


def test_price_verbs_set_the_tone():
    assert gx.headline_tone("Nvidia stock sinks 5% as chipmaker investigates its CEO") <= -0.4
    assert gx.headline_tone("Micron soars on record memory prices") >= 0.4
    assert -0.4 < gx.headline_tone("Nvidia to hold its developer conference in March") < 0.4
