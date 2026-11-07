"""The data-freshness check: which source is behind, what a holiday does, and partial vs failed runs."""
import json
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cse_advisor import health
from cse_advisor.db import Analysis, Base, Headline, JobRun, MacroDaily, MarketDaily, PriceDaily

FRI, THU, WED = date(2026, 11, 6), date(2026, 11, 5), date(2026, 11, 4)
NOW = datetime(2026, 11, 7, 10, 0)


@pytest.fixture()
def S():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)


def load(S, upto, headlines_ago_h=5):
    with S.begin() as s:
        s.add(PriceDaily(symbol="JKH.N0000", date=upto, open=1, high=1, low=1, close=1))
        s.add(Analysis(symbol="JKH.N0000", date=upto, verdict="WATCH", score=50, patterns=[], levels={}, liquidity={}, trend={}, recommendation={}))
        s.add(MarketDaily(date=upto, aspi=21000.0))
        s.add(MacroDaily(code="brent", date=upto, close=100.0))
        s.add(Headline(id="h", source="x", title="t", link="l", fetched_at=datetime.utcnow() - timedelta(hours=headlines_ago_h)))


def run(S, d, ok=True, steps=None, late=None, message=None):
    rec = {"analysed": 8, "steps": steps or {"backup": {"ok": True}}, "late": late, "message": message}
    with S.begin() as s:
        s.add(JobRun(job="eod", run_date=d, ok=ok, started_at=datetime.utcnow(), message=json.dumps({k: v for k, v in rec.items() if v})))


def get(S, admin=False, running=False):
    with S() as s:
        return health.health(s, NOW, FRI, running=running, admin=admin)


def test_current_data_is_ok(S):
    load(S, FRI)
    run(S, FRI)
    h = get(S)
    assert h["status"] == "ok" and h["stale"] == [] and h["expected"] == FRI.isoformat()
    assert {x["key"] for x in h["sources"]} == {"prices", "analysis", "market", "world", "headlines"}


def test_data_that_stops_at_thursday_is_stale_when_friday_was_missed(S):
    load(S, THU)
    run(S, THU)                                              # Thursday's run exists, Friday's does not
    h = get(S)
    assert h["status"] == "stale" and set(h["stale"]) == {"Prices", "Analysis", "Market (ASPI)"}
    assert next(x for x in h["sources"] if x["key"] == "prices")["as_of"] == THU.isoformat()


def test_a_holiday_is_not_stale(S):
    load(S, THU)
    run(S, THU)
    run(S, FRI, message="no session on 2026-11-06 (the market was closed)")
    h = get(S)
    assert h["status"] == "ok" and h["expected"] == THU.isoformat()


def test_a_failed_attempt_with_no_good_run_is_failed(S):
    load(S, THU)
    run(S, FRI, ok=False)
    assert get(S)["status"] == "failed"


def test_a_finished_run_with_a_broken_step_is_partial_and_only_the_admin_sees_why(S):
    load(S, FRI)
    run(S, FRI, steps={"world series": {"ok": False, "error": "no data for brent"}, "analysis": {"ok": True}}, late="recovered late, Sat 07 Nov 10:00")
    h = get(S)
    assert h["status"] == "partial" and h["run"]["failed_steps"] == ["world series"] and h["run"]["late"] is True
    assert "steps" not in h["run"] and "late_note" not in h["run"]
    a = get(S, admin=True)
    assert a["run"]["steps"]["world series"]["error"] == "no data for brent" and a["run"]["late_note"].startswith("recovered late")


def test_old_news_and_world_data_are_flagged_but_do_not_make_the_data_stale(S):
    load(S, FRI, headlines_ago_h=60)
    run(S, FRI)
    with S.begin() as s:
        s.query(MacroDaily).delete()
        s.add(MacroDaily(code="brent", date=date(2026, 10, 30), close=1.0))
    h = get(S)
    stale = {x["key"] for x in h["sources"] if x["stale"]}
    assert stale == {"headlines", "world"} and h["status"] == "ok"


def test_runs_recorded_before_the_step_record_still_read(S):
    load(S, FRI)
    with S.begin() as s:
        s.add(JobRun(job="eod", run_date=FRI, ok=True, started_at=datetime.utcnow(), message="{'analysed': ['JKH.N0000'], 'errors': {}}"))
    h = get(S, admin=True)
    assert h["status"] == "ok" and h["run"]["message"].startswith("{'analysed'")
