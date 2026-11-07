"""The parts that turn CSE payloads into prices, prices into alerts, and alerts into Telegram messages."""
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cse_advisor import ingest, jobs, telegram_bot
from cse_advisor.cse_client import ms_to_date
from cse_advisor.db import Analysis, Base, MarketDaily, Position, PositionReview, PriceDaily, Security

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture()
def S(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Sess = sessionmaker(bind=eng, expire_on_commit=False)
    for mod in (jobs, telegram_bot):
        monkeypatch.setattr(mod, "SessionLocal", Sess)
    with Sess.begin() as s:
        s.add_all([Security(symbol="JKH.N0000", cse_id=1, name="JOHN KEELLS HOLDINGS PLC"),
                   Security(symbol="ABAN.N0000", cse_id=2, name="ABANS ELECTRICALS PLC")])
    return Sess


# ---------------------------------------------------------------- the trade summary -> daily bars
def fake_client(rows):
    return SimpleNamespace(trade_summary=lambda: rows, market_summary=lambda: {"tradeVolume": 1e9, "shareVolume": 2e7, "trades": 9000},
                           aspi=lambda: {"value": 21130.3, "timestamp": rows[0]["lastTradedTime"]}, spsl20=lambda: {"value": 6000.0})


def test_trade_summary_becomes_bars_with_a_real_open_and_a_flagged_estimated_one(S, monkeypatch):
    rows = json.loads((FIX / "trade_summary.json").read_text())
    monkeypatch.setattr(ingest, "client", lambda: fake_client(rows))
    with S.begin() as s:
        out = ingest.record_eod(s, ["ABAN.N0000", "JKH.N0000", "NTRD.N0000", "MISSING.N0000"])
    assert out["stored"] == ["ABAN.N0000", "JKH.N0000"]                         # no price, or not in the summary: skipped
    d = ms_to_date(rows[0]["lastTradedTime"])
    assert out["date"] == str(d)
    with S() as s:
        aban = s.scalar(select(PriceDaily).where(PriceDaily.symbol == "ABAN.N0000"))
        assert (aban.date, aban.open, aban.high, aban.low, aban.close) == (d, 1025.25, 1044.0, 1002.5, 1013.75)
        assert aban.volume == 930 and aban.turnover == 947959.25 and aban.trades == 56 and aban.open_estimated is False and aban.source == "eod"
        jkh = s.scalar(select(PriceDaily).where(PriceDaily.symbol == "JKH.N0000"))
        assert jkh.open == 18.9 and jkh.open_estimated is True                  # no open in the payload: previous close, flagged
        md = s.get(MarketDaily, d)
        assert md.aspi == 21130.3 and md.advancers == 1 and md.decliners == 1   # ABAN up, JKH down, the untraded one flat


def test_running_it_twice_updates_the_same_bar(S, monkeypatch):
    rows = json.loads((FIX / "trade_summary.json").read_text())
    monkeypatch.setattr(ingest, "client", lambda: fake_client(rows))
    with S.begin() as s:
        ingest.record_eod(s, ["ABAN.N0000"])
    rows[0]["price"] = rows[0]["closingPrice"] = 1020.0
    with S.begin() as s:
        ingest.record_eod(s, ["ABAN.N0000"])
    with S() as s:
        bars = s.scalars(select(PriceDaily).where(PriceDaily.symbol == "ABAN.N0000")).all()
        assert len(bars) == 1 and bars[0].close == 1020.0


# ---------------------------------------------------------------- the alert builder
TODAY, YDAY = date(2026, 11, 6), date(2026, 11, 5)


def analysis(S, sym="JKH.N0000", d=TODAY, verdict="BUY", va="WATCH", vs="WATCH", patterns=None, rec=None):
    with S.begin() as s:
        s.add(Analysis(symbol=sym, date=d, verdict=verdict, score=72.0, verdict_analyst=va, score_analyst=60.0, verdict_smc=vs, score_smc=40.0,
                       patterns=patterns or [], levels={}, liquidity={}, trend={}, recommendation=rec or {}))


def prev(verdict="WATCH", va="WATCH", vs="WATCH", d=YDAY, ids=()):
    return {"JKH.N0000": {"verdict": verdict, "score": 60.0, "date": d, "verdict_analyst": va, "score_analyst": 50.0,
                          "verdict_smc": vs, "score_smc": 40.0, "pattern_ids": set(ids)}}


def pat(pid, tier="high", cat="chart_pattern", age=1):
    return {"id": pid, "name": "Ascending triangle", "category": cat, "priority_tier": tier, "direction": "bullish", "status": "confirmed",
            "context": {"age_bars": age}}


def test_a_signal_change_in_each_engine_is_reported(S):
    analysis(S, verdict="BUY", va="AVOID", vs="WATCH")
    texts = [a["text"] for a in jobs.build_alerts(["JKH.N0000"], prev(verdict="WATCH", va="WATCH", vs="WATCH"), [])]
    assert any(t.startswith("[CLS] WATCH → BUY") for t in texts) and any(t.startswith("[LVL] WATCH → AVOID") for t in texts)
    assert not any(t.startswith("[SMC]") for t in texts)                         # unchanged


def test_a_rerun_on_the_same_day_with_the_same_verdicts_says_nothing(S):
    analysis(S, verdict="BUY", va="WATCH", vs="WATCH", patterns=[pat("p1")])
    assert jobs.build_alerts(["JKH.N0000"], prev(verdict="BUY", d=TODAY, ids=["p1"]), []) == []


def test_only_new_fresh_high_priority_chart_patterns_alert(S):
    analysis(S, verdict="WATCH", patterns=[pat("new"), pat("old"), pat("low", tier="medium"), pat("smc1", cat="smc"), pat("candle", cat="candlestick"),
                                           pat("stale", age=9)])
    out = jobs.build_alerts(["JKH.N0000"], prev(verdict="WATCH", ids=["old"]), [])
    assert [a["kind"] for a in out] == ["new_pattern"] and "Ascending triangle" in out[0]["text"]


def test_new_results_and_material_announcements_are_reported(S):
    rec = {"catalysts": {"latest_results": {"period_end": "2026-09-30", "net_profit": 5.2e9, "profit_yoy_pct": 213.0, "eps": 3.1}}}
    analysis(S, verdict="WATCH", rec=rec)
    anns = [{"symbol": "JKH.N0000", "title": "Dividend", "category": "dividend", "materiality": 3},
            {"symbol": "JKH.N0000", "title": "AGM notice", "category": "meeting", "materiality": 1}]
    out = jobs.build_alerts(["JKH.N0000"], prev(verdict="WATCH"), anns, figures={"JKH.N0000": {"parsed": ["Q2"]}})
    kinds = {a["kind"]: a["text"] for a in out}
    assert "profit LKR 5,200M (+213% YoY), EPS 3.10" in kinds["results"]
    assert kinds["announcement"] == "Announcement: Dividend (dividend)" and len([a for a in out if a["kind"] == "announcement"]) == 1


def test_a_held_stock_gets_its_own_trim_and_sell_alerts(S):
    analysis(S, verdict="WATCH")
    with S.begin() as s:
        s.add(Position(user_id="local", symbol="JKH.N0000", entry_date=date(2026, 10, 1), entry_price=20.0, quantity=100))
        s.flush()
        s.add(PositionReview(user_id="local", symbol="JKH.N0000", position_id=1, date=TODAY, verdict="TRIM", score=50.0,
                             recommendation={"original": {"position_review": {"action": "TRIM", "reasons": ["momentum faded", "below EMA21"]}},
                                             "analyst": {"position_review": {"action": "HOLD", "reasons": []}}}))
    out = [a["text"] for a in jobs.build_alerts(["JKH.N0000"], prev(verdict="WATCH"), [], user_id="local")]
    assert "[CLS] Position: TRIM because momentum faded; below EMA21" in out and not any("[LVL]" in t for t in out)
    assert jobs.build_alerts(["JKH.N0000"], prev(verdict="WATCH"), [], user_id="someone-else") == []     # not their position


def test_announcements_in_a_sent_digest_are_marked_notified(S):
    from cse_advisor.db import Announcement
    with S.begin() as s:
        s.add_all([Announcement(id=1, symbol="JKH.N0000", title="Dividend", date=datetime(2026, 11, 5), notified=False),
                   Announcement(id=2, symbol="JKH.N0000", title="Other", date=datetime(2026, 11, 5), notified=False)])
    assert jobs._mark_notified([1]) == 1 and jobs._mark_notified([]) == 0
    with S() as s:
        assert [a.notified for a in s.scalars(select(Announcement).order_by(Announcement.id))] == [True, False]


# ---------------------------------------------------------------- Telegram messages
def rich_analysis(S):
    plan = {"setup": "pullback", "setup_name": "Pullback to support", "entry_label": None, "triggered": True, "trigger": 19.0,
            "buy_zone": {"low": 18.0, "high": 18.5}, "ideal": 18.2, "stop": 17.5, "targets": [{"label": "T1", "price": 20.0}, {"label": "T2", "price": 21.0}],
            "reward_risk": 2.4}
    eng = lambda v: {"verdict": v, "plan": plan, "close": 18.8, "explanation": {"reasons_for": [{"text": "Uptrend intact. More text"}], "reasons_against": []}}
    analysis(S, verdict="BUY", va="WATCH", vs="AVOID", rec={"original": eng("BUY"), "analyst": eng("WATCH"), "smc": eng("AVOID"), "close": 18.8,
                                                           "agreement": {"state": "mixed"}})
    with S.begin() as s:
        a = s.scalar(select(Analysis))
        a.trend = {"state": "strong_up"}
        a.liquidity = {"grade": "B", "regime": "normal", "flow": {"label": "balanced"}}


def test_report_text_names_the_three_engines_and_the_plan(S):
    rich_analysis(S)
    t = telegram_bot.report_text("JKH.N0000")
    assert "<b>JKH</b>" in t and "CLS <b>BUY</b>" in t and "LVL <b>WATCH</b>" in t and "SMC <b>AVOID</b>" in t
    assert "zone 18.00–18.50" in t and "limit <b>18.20</b>" in t and "stop <b>17.50</b>" in t and "R:R 2.40" in t
    assert "Uptrend intact" in t and "More text" not in t                         # first sentence of each reason only
    assert telegram_bot.report_text("NONE.N0000") == "No analysis yet for NONE.N0000."


def test_the_digest_lists_alerts_escaped_and_the_stocks_best_first(S, monkeypatch):
    rich_analysis(S)
    sent = []
    monkeypatch.setattr(telegram_bot, "enabled", lambda: True)
    monkeypatch.setattr(telegram_bot, "settings", lambda: {"telegram": {"daily_digest": True, "enabled": True}})
    monkeypatch.setattr(telegram_bot, "send_text", lambda text, chat=None: sent.append(text))
    telegram_bot.send_digest(["JKH.N0000"], [{"symbol": "JKH.N0000", "kind": "announcement", "text": "Profit <up> & rising"}])
    assert "🔔 <b>Alerts</b>" in sent[0] and "<b>JKH</b>: Profit &lt;up&gt; &amp; rising" in sent[0]
    assert "<b>JKH</b>" in sent[1]
    sent.clear()
    monkeypatch.setattr(telegram_bot, "enabled", lambda: False)
    telegram_bot.send_digest(["JKH.N0000"], [])
    assert sent == []                                                             # off: nothing is sent


def test_messages_are_split_under_telegrams_limit_and_the_watchlist_command_answers(S, monkeypatch):
    parts = telegram_bot._chunks("\n".join(["x" * 100] * 80), 3900)
    assert len(parts) == 3 and all(len(p) <= 3900 for p in parts)
    rich_analysis(S)
    with S.begin() as s:
        from cse_advisor.db import WatchlistItem
        s.add(WatchlistItem(user_id="local", symbol="JKH.N0000"))
    sent = []
    monkeypatch.setattr(telegram_bot, "send_text", lambda text, chat=None: sent.append(text))
    telegram_bot.handle_command("/watchlist", "123")
    assert "<b>JKH</b> 18.80" in sent[0] and "CLS 🟢BUY" in sent[0]
