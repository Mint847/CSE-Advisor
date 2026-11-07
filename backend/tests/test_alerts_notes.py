"""Your own alerts (rules, firing, once-a-day memory, inbox, Telegram) and notes."""
from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cse_advisor import accounts, alerts, alerts_api, jobs, main, telegram_bot
from cse_advisor.auth import User
from cse_advisor.db import LOCAL_UID, AlertEvent, Analysis, Base, Note, Position, PriceDaily, Security, UserAlert

TODAY = date(2026, 11, 6)
SYM = "JKH.N0000"
ME = User(uid="uid-a", admin=False)
OTHER = User(uid="uid-b", admin=False)


@pytest.fixture()
def S(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Sess = sessionmaker(bind=eng, expire_on_commit=False)
    for mod in (alerts, alerts_api, main, jobs):
        monkeypatch.setattr(mod, "SessionLocal", Sess)
    sent: list[str] = []
    monkeypatch.setattr(telegram_bot, "enabled", lambda: True)
    monkeypatch.setattr(telegram_bot, "send_text", lambda text, chat=None: sent.append(text))
    monkeypatch.setattr(alerts, "_stats_cache", {})
    monkeypatch.setattr(accounts, "refresh_review", lambda s, uid, sym: None)
    with Sess.begin() as s:
        s.add_all([Security(symbol=SYM, cse_id=1, name="JOHN KEELLS HOLDINGS PLC"), Security(symbol="ABAN.N0000", cse_id=2, name="ABANS")])
        # 30 sessions: highs up to 200 (52-week high), volume 1,000 each
        for i in range(30):
            d = TODAY - timedelta(days=30 - i)
            s.add(PriceDaily(symbol=SYM, date=d, open=150, high=200 if i == 3 else 160, low=140, close=150, volume=1000))
    Sess.sent = sent
    return Sess


def quote(price=150.0, chg=0.0, vol=500, high=None, day=TODAY):
    return {"symbol": SYM, "time": str(day), "close": price, "change_pct": chg, "volume": vol, "high": high or price}


def add_rule(Sess, kind, uid=LOCAL_UID, symbol=SYM, once=False, **params):
    with Sess.begin() as s:
        r = UserAlert(user_id=uid, symbol=symbol, kind=kind, params=alerts.clean_params(kind, params), once=once)
        s.add(r)
        s.flush()
        return r.id


def inbox(Sess, uid=LOCAL_UID):
    with Sess() as s:
        return list(s.scalars(select(AlertEvent).where(AlertEvent.user_id == uid).order_by(AlertEvent.id)))


# ---------------------------------------------------------------- rule checking
def test_params_are_checked_and_trimmed():
    assert alerts.clean_params("price_above", {"level": "150.5", "junk": 1}) == {"level": 150.5}
    for kind, bad in [("price_above", {}), ("price_above", {"level": -1}), ("day_up", {"pct": 0}), ("pattern", {"direction": "x"}),
                      ("rsi_cross", {"level": 130}), ("nope", {})]:
        with pytest.raises(ValueError):
            alerts.clean_params(kind, bad)
    assert alerts.clean_params("verdict_change", {}) == {"engine": "any"}
    assert alerts.describe("rsi_cross", alerts.clean_params("rsi_cross", {"level": 30, "dir": "below"})) == "RSI(14) crosses below 30"


def test_price_rule_fires_once_a_day_even_after_restart_and_goes_to_inbox_and_telegram(S):
    add_rule(S, "price_above", level=155)
    assert alerts.evaluate_quotes({SYM: quote(150)}, TODAY) == []                 # below the level: nothing
    assert len(alerts.evaluate_quotes({SYM: quote(156)}, TODAY)) == 1
    assert alerts.evaluate_quotes({SYM: quote(157)}, TODAY) == []                 # same day: not again
    assert len(inbox(S)) == 1 and inbox(S)[0].symbol == SYM and "156.00" in inbox(S)[0].text
    assert len(S.sent) == 1 and SYM in S.sent[0]                                  # the local user is the Telegram owner
    alerts._stats_cache.clear()                                                    # "restart": nothing in memory
    assert alerts.evaluate_quotes({SYM: quote(158)}, TODAY) == []
    assert len(alerts.evaluate_quotes({SYM: quote(158, day=TODAY + timedelta(days=1))}, TODAY + timedelta(days=1))) == 1   # next day: again


def test_once_rule_switches_itself_off(S):
    rid = add_rule(S, "price_below", once=True, level=148)
    alerts.evaluate_quotes({SYM: quote(147)}, TODAY)
    with S() as s:
        r = s.get(UserAlert, rid)
        assert (r.active, r.fired_count, r.last_fired is not None) == (False, 1, True)
    assert alerts.evaluate_quotes({SYM: quote(147, day=TODAY + timedelta(days=1))}, TODAY + timedelta(days=1)) == []


def test_old_or_missing_quotes_never_fire(S):
    add_rule(S, "price_above", level=100)
    assert alerts.evaluate_quotes({SYM: quote(150, day=TODAY - timedelta(days=1))}, TODAY) == []     # yesterday's close
    assert alerts.evaluate_quotes({}, TODAY) == []


def test_day_move_high_and_volume_rules(S):
    add_rule(S, "day_up", pct=3)
    add_rule(S, "day_down", pct=3)
    add_rule(S, "near_high", pct=5)
    add_rule(S, "volume_spike", mult=3)
    assert alerts.evaluate_quotes({SYM: quote(150, chg=2.9, vol=2999)}, TODAY) == []          # nothing yet; the high is 200, 25% away
    fired = alerts.evaluate_quotes({SYM: quote(190, chg=3.5, vol=3200)}, TODAY)
    texts = sorted(e["text"] for e in fired)
    assert len(texts) == 3 and "5.0% below its 52-week high 200.00" in texts[1] and texts[2].startswith("is up +3.5%") and "3.2×" in texts[0], texts
    fired = alerts.evaluate_quotes({SYM: quote(140, chg=-3.4, vol=100)}, TODAY)
    assert len(fired) == 1 and "down -3.4%" in fired[0]["text"]
    new_high = alerts.evaluate_quotes({SYM: quote(205, chg=0.1, vol=1, high=205)}, TODAY + timedelta(days=1))
    assert not new_high                                                                       # quote is dated TODAY, not tomorrow: stale


def test_a_new_52_week_high_is_named(S):
    add_rule(S, "near_high", pct=1)
    fired = alerts.evaluate_quotes({SYM: quote(205, high=205)}, TODAY)
    assert "new 52-week high" in fired[0]["text"]


def test_rules_are_per_account_and_telegram_only_for_the_owner(S):
    with S.begin() as s:
        accounts.set_telegram_owner(s, "uid-a")
    add_rule(S, "price_above", uid="uid-a", level=100)
    add_rule(S, "price_above", uid="uid-b", level=100)
    alerts.evaluate_quotes({SYM: quote(150)}, TODAY)
    assert len(inbox(S, "uid-a")) == 1 and len(inbox(S, "uid-b")) == 1
    assert len(S.sent) == 1                                                       # uid-b is not the Telegram owner


# ---------------------------------------------------------------- evening rules
def analysis(S, patterns=(), verdict="WATCH", analyst="WATCH", smc="WATCH", d=TODAY):
    with S.begin() as s:
        s.add(Analysis(symbol=SYM, date=d, verdict=verdict, score=50, verdict_analyst=analyst, verdict_smc=smc, patterns=list(patterns),
                       levels={}, liquidity={}, trend={}, recommendation={}))


def pat(i="p1", direction="bullish", tier="high", cat="chart", age=1):
    return {"id": i, "name": "Double bottom", "direction": direction, "priority_tier": tier, "category": cat, "context": {"age_bars": age}}


def test_pattern_rule_only_for_new_high_priority_patterns(S):
    add_rule(S, "pattern", direction="bullish")
    analysis(S, [pat("old"), pat("new"), pat("bear", direction="bearish"), pat("low", tier="low"), pat("lvl", cat="level")])
    prev = {SYM: {"pattern_ids": {"old"}, "verdict": "WATCH", "date": TODAY - timedelta(days=1)}}
    fired = alerts.evaluate_eod([SYM], prev, [])
    assert len(fired) == 1 and "Double bottom (bullish)" in fired[0]["text"]
    assert alerts.evaluate_eod([SYM], {SYM: {"pattern_ids": {"old", "new"}}}, []) == []          # a re-run does not repeat it


def test_verdict_and_announcement_rules(S):
    add_rule(S, "verdict_change", engine="smc")
    add_rule(S, "announcement", min_materiality=3)
    analysis(S, verdict="BUY", analyst="BUY", smc="AVOID")
    prev = {SYM: {"verdict": "WATCH", "verdict_analyst": "WATCH", "verdict_smc": "WATCH", "pattern_ids": set()}}
    anns = [{"symbol": SYM, "title": "Dividend", "category": "Dividend", "materiality": 4},
            {"symbol": SYM, "title": "Notice", "category": "Other", "materiality": 1}]
    texts = sorted(e["text"] for e in alerts.evaluate_eod([SYM], prev, anns))
    assert texts == ["announcement: Dividend (Dividend)", "verdict change: SMC WATCH → AVOID"]       # only the SMC engine, only importance 3+


def test_rsi_rule_fires_on_the_cross_only(S, monkeypatch):
    add_rule(S, "rsi_cross", level=30, dir="below")
    analysis(S)
    seq = iter([(35.0, 33.0), (31.0, 29.0)])
    monkeypatch.setattr(alerts, "_rsi_pair", lambda s, sym: next(seq))
    assert alerts.evaluate_eod([SYM], {}, []) == []                                # still above 30
    assert "crossed below 30" in alerts.evaluate_eod([SYM], {}, [])[0]["text"]


# ---------------------------------------------------------------- the engines' alerts in the same inbox
def test_engine_alerts_are_remembered_across_restarts(S):
    assert alerts.engine_event(LOCAL_UID, SYM, "stop", "stop hit", "2026-11-06") is True
    assert alerts.engine_event(LOCAL_UID, SYM, "stop", "stop hit", "2026-11-06") is False       # replaces the in-memory set
    assert alerts.engine_event(LOCAL_UID, SYM, "stop", "stop hit", "2026-11-09") is True
    evening = [{"symbol": SYM, "kind": "signal_change", "text": "[CLS] WATCH → BUY"}]
    assert alerts.record_engine_alerts(LOCAL_UID, evening, "2026-11-06") == 1
    assert alerts.record_engine_alerts(LOCAL_UID, evening, "2026-11-06") == 0                   # same-day re-run
    assert len(inbox(S)) == 3


def test_old_inbox_entries_are_pruned(S):
    with S.begin() as s:
        s.add(AlertEvent(user_id=LOCAL_UID, symbol=SYM, kind="x", text="old", at=datetime.utcnow() - timedelta(days=200)))
        s.add(AlertEvent(user_id=LOCAL_UID, symbol=SYM, kind="x", text="new"))
    assert alerts.prune() == 1 and [e.text for e in inbox(S)] == ["new"]


# ---------------------------------------------------------------- the API (called as an account)
def test_alert_api_scopes_validates_and_caps(S):
    r = alerts_api.add_alert(alerts_api.AlertIn(symbol=SYM.lower(), kind="price_below", params={"level": 140}, once=True), ME)
    assert (r["symbol"], r["text"], r["active"]) == (SYM, "price falls to 140.00 or less", True)
    with pytest.raises(HTTPException) as e:
        alerts_api.add_alert(alerts_api.AlertIn(symbol=SYM, kind="price_below", params={}), ME)
    assert e.value.status_code == 400
    with pytest.raises(HTTPException):
        alerts_api.add_alert(alerts_api.AlertIn(symbol="NOPE.N0000", kind="day_up", params={"pct": 3}), ME)
    assert alerts_api.list_alerts(OTHER)["rules"] == []                                         # not visible to others
    with pytest.raises(HTTPException) as e:
        alerts_api.delete_alert(r["id"], OTHER)
    assert e.value.status_code == 404
    assert alerts_api.edit_alert(r["id"], alerts_api.AlertPatch(active=False, params={"level": 130}), ME)["text"] == "price falls to 130.00 or less"
    alerts_api.delete_alert(r["id"], ME)
    assert alerts_api.list_alerts(ME)["rules"] == []


def test_inbox_api_counts_unread_and_marks_read(S):
    for i in range(3):
        alerts.engine_event("uid-a", SYM, f"k{i}", f"t{i}", "2026-11-06")
    alerts.engine_event("uid-b", SYM, "k", "other", "2026-11-06")
    box = alerts_api.inbox(60, False, ME)
    assert box["unread"] == 3 and [e["text"] for e in box["events"]] == ["t2", "t1", "t0"]       # newest first, only mine
    alerts_api.mark_read(alerts_api.ReadIn(ids=[box["events"][0]["id"]]), ME)
    assert alerts_api.inbox(60, True, ME)["unread"] == 2
    alerts_api.mark_read(alerts_api.ReadIn(), ME)
    assert alerts_api.inbox(60, False, ME)["unread"] == 0 and alerts_api.inbox(60, False, OTHER)["unread"] == 1
    assert alerts_api.clear_inbox(ME)["removed"] == 3 and alerts_api.inbox(60, False, ME)["events"] == []


def test_notes_crud_search_tags_and_scoping(S):
    n = alerts_api.add_note(alerts_api.NoteIn(body="  Waiting for a close above 160  ", symbol=SYM.lower(), tags=["#Plan", "plan", " Entry "]), ME)
    assert (n["body"], n["symbol"], n["tags"]) == ("Waiting for a close above 160", SYM, ["plan", "entry"])
    alerts_api.add_note(alerts_api.NoteIn(body="General thought on the market"), ME)
    alerts_api.add_note(alerts_api.NoteIn(body="other person's", symbol=SYM), OTHER)
    assert len(alerts_api.list_notes(None, None, None, None, 300, ME)) == 2
    assert [x["body"] for x in alerts_api.list_notes(SYM, None, None, None, 300, ME)] == ["Waiting for a close above 160"]
    assert len(alerts_api.list_notes(None, None, None, "market", 300, ME)) == 1
    assert len(alerts_api.list_notes(None, None, "plan", None, 300, ME)) == 1
    with pytest.raises(HTTPException):
        alerts_api.add_note(alerts_api.NoteIn(body="   "), ME)
    with pytest.raises(HTTPException) as e:
        alerts_api.edit_note(n["id"], alerts_api.NotePatch(body="hijack"), OTHER)
    assert e.value.status_code == 404
    out = alerts_api.edit_note(n["id"], alerts_api.NotePatch(body="Edited", pinned=True), ME)
    assert out["pinned"] and alerts_api.list_notes(None, None, None, None, 300, ME)[0]["body"] == "Edited"      # pinned first
    alerts_api.delete_note(n["id"], ME)
    assert len(alerts_api.list_notes(None, None, None, None, 300, ME)) == 1


def test_closing_a_position_saves_the_review_as_a_note_and_deleting_keeps_the_note(S):
    with S.begin() as s:
        p = Position(user_id="uid-a", symbol=SYM, entry_date=TODAY, entry_price=150, quantity=10)
        s.add(p)
        s.flush()
        pid = p.id
    main.close_position(pid, main.PositionClose(exit_date=TODAY, exit_price=160, review="Sold into strength; entry was early"), ME)
    notes = alerts_api.list_notes(None, pid, None, None, 300, ME)
    assert [(n["tags"], n["symbol"]) for n in notes] == [(["review"], SYM)]
    main.delete_position(pid, ME)
    kept = alerts_api.list_notes(SYM, None, None, None, 300, ME)
    assert len(kept) == 1 and kept[0]["position_id"] is None


def test_account_claim_and_delete_cover_alerts_and_notes(S):
    add_rule(S, "price_above", level=100)
    alerts.engine_event(LOCAL_UID, SYM, "k", "t", "2026-11-06")
    with S.begin() as s:
        s.add(Note(user_id=LOCAL_UID, symbol=SYM, body="mine"))
    assert {k: v for k, v in accounts.local_data_counts(S()).items() if v} == {"user_alerts": 1, "alert_events": 1, "notes": 1}
    with S.begin() as s:
        accounts.claim_local(s, "uid-a")
    assert len(inbox(S, "uid-a")) == 1 and alerts_api.list_alerts(ME)["rules"] and alerts_api.list_notes(None, None, None, None, 300, ME)
    with S.begin() as s:
        out = accounts.delete_account_data(s, "uid-a")
    assert out["user_alerts"] == 1 and out["alert_events"] == 1 and out["notes"] == 1
