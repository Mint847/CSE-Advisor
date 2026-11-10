"""Is the data current? One answer for the footer strip, the home-page banner and the Health panel.

Each stored source is compared with the last finished session (jobs.last_trading_day). A session the CSE did not hold
(a holiday, recorded by jobs.ensure_fresh) is not expected, so it never shows as stale. The last evening run's per-step
record (jobs._Steps) says which step failed; a run that finished but had a failed step is "partial", not OK.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import Analysis, Headline, JobRun, MacroDaily, MarketDaily, PriceDaily, ScreenerRun, SectorDaily

# how old a source may be before it is called stale, in days past the session (calendar days for the slower ones)
WORLD_MAX_AGE_DAYS = 3
HEADLINES_MAX_AGE_HOURS = 36


def prev_weekday(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _message(r: JobRun | None) -> dict[str, Any]:
    if r is None or not r.message:
        return {}
    try:
        m = json.loads(r.message)
        return m if isinstance(m, dict) else {}
    except ValueError:
        return {"message": r.message[:300]}            # runs recorded before the step record existed


def expected_session(s: Session, d: date) -> date:
    """The session the data should have: `d`, unless the CSE did not trade that day."""
    closed = [r for r in s.scalars(select(JobRun).where(JobRun.job == "eod", JobRun.run_date == d, JobRun.ok.is_(True)))
              if str(_message(r).get("message", "")).startswith("no session")]
    return prev_weekday(d) if closed else d


def _as_utc(t: datetime | None) -> str | None:
    return t.isoformat(timespec="seconds") + "Z" if t else None


def health(s: Session, now: datetime, session: date, running: bool = False, admin: bool = False) -> dict[str, Any]:
    """`session` = the last finished session (jobs.last_trading_day(now))."""
    want = expected_session(s, session)

    def day_source(key: str, label: str, latest: date | None, expect: date = want) -> dict[str, Any]:
        return {"key": key, "label": label, "as_of": latest.isoformat() if latest else None, "stale": latest is None or latest < expect,
                "kind": "session"}
    prices = s.scalar(select(func.max(PriceDaily.date)))
    sources = [
        day_source("prices", "Prices", prices),
        day_source("analysis", "Analysis", s.scalar(select(func.max(Analysis.date)))),
        day_source("market", "Market (ASPI)", s.scalar(select(func.max(MarketDaily.date)).where(MarketDaily.aspi.is_not(None)))),
    ]
    sec = s.scalar(select(func.max(SectorDaily.date)))
    if sec is not None:
        sources.append(day_source("sectors", "Sector indices", sec))
    run = s.scalar(select(func.max(ScreenerRun.date)))
    if run is not None:
        sources.append(day_source("screener", "Candidate ranking", run))
    from .macro import SERIES as WORLD_CODES
    world = s.scalar(select(func.max(MacroDaily.date)).where(MacroDaily.code.in_(list(WORLD_CODES))))
    if world is not None:
        sources.append({"key": "world", "label": "World markets", "as_of": world.isoformat(), "kind": "days",
                        "stale": (now.date() - world).days > WORLD_MAX_AGE_DAYS})
    from .quant.rates import STALE_DAYS, TBILL
    tbill = s.scalar(select(func.max(MacroDaily.date)).where(MacroDaily.code == TBILL))
    if tbill is not None:                                   # CBSL publishes month-end T-bill yields about a month late
        sources.append({"key": "rates", "label": "T-bill rate (CBSL)", "as_of": tbill.isoformat(), "kind": "days",
                        "stale": (now.date() - tbill).days > STALE_DAYS})
    fetched = s.scalar(select(func.max(Headline.fetched_at)))                  # UTC
    if fetched is not None:
        age = (datetime.utcnow() - fetched).total_seconds() / 3600
        sources.append({"key": "headlines", "label": "News", "as_of": _as_utc(fetched), "kind": "time", "stale": age > HEADLINES_MAX_AGE_HOURS})

    runs = s.scalars(select(JobRun).where(JobRun.job == "eod", JobRun.run_date >= want).order_by(JobRun.started_at.desc())).all()
    last_ok = next((r for r in runs if r.ok), None)
    latest = runs[0] if runs else None
    shown = last_ok or latest
    msg = _message(shown)
    steps = msg.get("steps") or {}
    failing = [k for k, v in steps.items() if not v.get("ok", True)]
    info = {
        "run_date": shown.run_date.isoformat() if shown else None, "ok": bool(shown and shown.ok), "at": _as_utc(shown.started_at) if shown else None,
        "late": bool(msg.get("late")), "attempts": len(runs), "failed_steps": failing, "analysed": msg.get("analysed"),
    }
    if admin:                                                                   # the details can name other people's stocks
        info.update({"steps": steps, "errors": msg.get("errors"), "late_note": msg.get("late"), "message": msg.get("message")})

    stale = [x["label"] for x in sources if x["stale"] and x["key"] in ("prices", "analysis", "market")]
    if stale and not last_ok:
        status = "failed" if runs and not runs[0].ok else "stale"
    elif stale:
        status = "stale"
    elif failing:
        status = "partial"
    else:
        status = "ok"
    from .quant import checks
    try:
        spikes = checks.price_spikes(s, [x for (x,) in s.execute(select(PriceDaily.symbol).distinct())])
    except Exception as exc:  # noqa: BLE001 - an extra check must never take the strip down
        logging.getLogger(__name__).warning("spike check: %s", exc)
        spikes = []
    return {"status": status, "session": session.isoformat(), "expected": want.isoformat(), "running": running, "sources": sources,
            "run": info, "stale": stale, "spikes": {"count": len(spikes), "items": spikes[:10] if admin else []}}
