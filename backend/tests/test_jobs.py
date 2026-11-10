"""Keeping the data current: the watchdog that recovers a missed evening run, and the per-step record of a run."""
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cse_advisor import jobs
from cse_advisor.db import Base, JobRun

FRI, THU = date(2026, 11, 6), date(2026, 11, 5)


@pytest.fixture()
def Session(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    monkeypatch.setattr(jobs, "SessionLocal", S)
    from cse_advisor import alerts
    monkeypatch.setattr(alerts, "SessionLocal", S)         # the alert step must never reach the real database
    return S


def _run(S, d, ok=True, ago_min=0):
    with S.begin() as s:
        s.add(JobRun(job="eod", run_date=d, ok=ok, started_at=datetime.utcnow() - timedelta(minutes=ago_min), message="{}"))


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, **kw):
        self.calls.append(kw)


def fresh(now, run=None, traded=lambda d: True):
    return jobs.ensure_fresh(now, run=run, traded=traded)


# ---------------------------------------------------------------- the watchdog
def test_a_run_missed_while_the_pc_slept_is_recovered_late(Session):
    rec = Recorder()
    _run(Session, THU)                                         # Thursday ran; Friday's 15:30 run was slept through
    assert fresh(datetime(2026, 11, 7, 10, 0), rec) == "ran"   # the PC is awake on Saturday morning
    assert len(rec.calls) == 1 and rec.calls[0]["notify"] is True and "recovered late" in rec.calls[0]["late"]


def test_nothing_to_do_before_the_scheduled_time_or_when_it_ran(Session):
    rec = Recorder()
    _run(Session, THU)
    assert fresh(datetime(2026, 11, 6, 14, 0), rec) == "up to date"             # session = Thursday, which ran
    assert fresh(datetime(2026, 11, 6, 15, 0), rec) == "waiting for the scheduled run"   # today's session, before 15:30
    _run(Session, FRI)
    assert fresh(datetime(2026, 11, 7, 10, 0), rec) == "up to date"
    assert rec.calls == []


def test_a_run_a_little_after_the_time_is_not_called_late(Session):
    rec = Recorder()
    _run(Session, THU)
    assert fresh(datetime(2026, 11, 6, 16, 0), rec) == "ran"
    assert rec.calls[0]["late"] is None


def test_retries_are_spaced_and_capped(Session):
    rec = Recorder()
    _run(Session, THU)
    _run(Session, FRI, ok=False, ago_min=10)                   # tonight's run failed 10 minutes ago
    assert fresh(datetime(2026, 11, 6, 16, 0), rec) == "retrying later"
    assert rec.calls == []
    with Session.begin() as s:                                 # that attempt is now old enough
        s.execute(JobRun.__table__.update().values(started_at=datetime.utcnow() - timedelta(hours=2)))
    assert fresh(datetime(2026, 11, 6, 16, 0), rec) == "ran"
    _run(Session, FRI, ok=False, ago_min=300)
    _run(Session, FRI, ok=False, ago_min=300)                  # three failed attempts so far
    assert "gave up" in fresh(datetime(2026, 11, 6, 20, 0), Recorder())


def test_a_holiday_is_recorded_as_done_not_run(Session):
    rec = Recorder()
    _run(Session, THU)
    assert fresh(datetime(2026, 11, 7, 10, 0), rec, traded=lambda d: False) == f"no session on {FRI}"
    assert rec.calls == []
    assert jobs.eod_done_for(FRI)
    assert fresh(datetime(2026, 11, 7, 11, 0), rec, traded=lambda d: False) == "up to date"
    with Session() as s:
        assert "market was closed" in json.loads(s.scalar(select(JobRun.message).where(JobRun.run_date == FRI)))["message"]


def test_a_run_in_progress_is_left_alone(Session):
    rec = Recorder()
    _run(Session, THU)
    assert jobs._lock.acquire(blocking=False)
    try:
        assert fresh(datetime(2026, 11, 7, 10, 0), rec) == "running"
    finally:
        jobs._lock.release()
    assert rec.calls == []


def test_unknown_session_state_still_runs(Session):
    rec = Recorder()
    _run(Session, THU)
    assert fresh(datetime(2026, 11, 7, 10, 0), rec, traded=lambda d: None) == "ran"      # the CSE could not be asked: run anyway


# ---------------------------------------------------------------- one run, step by step
def _stub_run(monkeypatch, failing=()):
    """Stub everything run_eod touches; names in `failing` raise."""
    from cse_advisor import accounts, ingest, intraday, journal, listed_meta, macro, news_feeds, screener

    def ok(name, value=None):
        def f(*a, **k):
            if name in failing:
                raise RuntimeError(f"{name} broke")
            return value
        return f
    monkeypatch.setattr(ingest, "record_eod", ok("record_eod", {}))
    monkeypatch.setattr(ingest, "catch_up", ok("catch_up", []))
    monkeypatch.setattr(ingest, "record_market_extras", ok("extras", {}))
    monkeypatch.setattr(ingest, "sync_market_notices", ok("notices", {"notices": [], "reports_synced": 0}))
    monkeypatch.setattr(ingest, "sync_announcements", ok("announcements", []))
    monkeypatch.setattr(ingest, "enrich_announcements", ok("enrich", {}))
    monkeypatch.setattr(ingest, "sync_reports", ok("reports", False))
    monkeypatch.setattr(ingest, "sync_corporate_actions", ok("cacts", None))
    monkeypatch.setattr(intraday, "record_aspi", ok("aspi", None))
    monkeypatch.setattr(intraday, "backfill_intraday", ok("intraday", {}))
    monkeypatch.setattr(intraday, "has_intraday", lambda *a, **k: True)
    monkeypatch.setattr(news_feeds, "fetch", ok("headlines", {"items": 5, "new": 1, "sources": 8, "failed": ["Daily News"]}))
    monkeypatch.setattr(macro, "top_up", ok("world", {"brent": 15, "ndx": 15}))
    monkeypatch.setattr(listed_meta, "top_up", ok("sectors", {"skipped": "test"}))     # no network, no real database
    from cse_advisor.quant import rates
    monkeypatch.setattr(rates, "top_up", ok("rates", {"tbill_skipped": "test"}))       # no network, no real database
    monkeypatch.setattr(journal, "record_day", ok("journal", {"recorded": 0, "scored": 0}))
    monkeypatch.setattr(screener, "start_background", lambda: None)
    monkeypatch.setattr(jobs, "read_headlines", lambda syms: {"skipped": "llm.enabled is false"})
    monkeypatch.setattr(jobs, "_previous_snapshot", lambda syms: {})
    monkeypatch.setattr(jobs, "_today_volume_at_price", lambda sym: None)
    monkeypatch.setattr(jobs, "analyze_symbol", ok("analysis", {"verdict": "WATCH"}))
    monkeypatch.setattr(jobs, "build_alerts", ok("alerts", []))
    monkeypatch.setattr(jobs, "backup_db", ok("backup", {"file": "x.db"}))
    monkeypatch.setattr(jobs, "offsite_backup", ok("offsite", {"skipped": "test"}))     # never write into the real OneDrive
    monkeypatch.setattr(accounts, "telegram_owner", lambda s: "local")


def _last_record(S):
    with S() as s:
        r = s.scalars(select(JobRun).order_by(JobRun.id.desc())).first()
        return r.ok, json.loads(r.message)


def test_a_clean_run_records_every_step(Session, monkeypatch):
    _stub_run(monkeypatch)
    out = jobs.run_eod(notify=False, only=["JKH.N0000"])
    assert out["ok"] and out["analysed"] == ["JKH.N0000"]
    ok, rec = _last_record(Session)
    assert ok and rec["analysed"] == 1
    steps = rec["steps"]
    for name in ("market extras", "headlines", "world series", "announcements", "analysis", "journal", "alerts & digest", "backup"):
        assert steps[name]["ok"], name
    assert steps["headlines"]["failed_feeds"] == ["Daily News"]               # information only: still ok


def test_one_broken_source_is_recorded_and_the_analysis_still_runs(Session, monkeypatch):
    _stub_run(monkeypatch, failing=("extras", "world", "announcements", "backup"))
    out = jobs.run_eod(notify=False, only=["JKH.N0000", "COMB.N0000"], late="recovered late, Sat 07 Nov 10:00")
    assert out["ok"] and sorted(out["analysed"]) == ["COMB.N0000", "JKH.N0000"]        # analysis was not stopped
    ok, rec = _last_record(Session)
    assert ok                                                                         # the job as a whole still counts as done
    st = rec["steps"]
    assert st["market extras"]["ok"] is False and "extras broke" in st["market extras"]["error"]
    assert st["world series"]["ok"] is False and st["backup"]["ok"] is False
    assert st["announcements"]["ok"] is False and set(st["announcements"]["failed"]) == {"JKH.N0000", "COMB.N0000"}
    assert st["analysis"]["ok"] and st["journal"]["ok"]
    assert rec["late"].startswith("recovered late")


def test_an_analysis_error_marks_the_run_for_a_retry(Session, monkeypatch):
    _stub_run(monkeypatch, failing=("analysis",))
    out = jobs.run_eod(notify=False, only=["JKH.N0000"])
    assert out["errors"] and not out["analysed"]
    ok, rec = _last_record(Session)
    assert ok is False                                                                # so the watchdog retries it
    assert rec["steps"]["analysis"]["ok"] is False and "JKH.N0000" in rec["steps"]["analysis"]["failed"]
