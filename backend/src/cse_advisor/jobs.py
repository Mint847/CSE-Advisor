"""End-of-day job, startup catch-up and alert generation."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update

from .strategy.recommend import engine_recs
from . import ingest, intraday
from .cse_client import now_local
from .db import Analysis, Announcement, JobRun, SessionLocal
from .engine import analyze_symbol

log = logging.getLogger(__name__)
_lock = threading.Lock()


def last_trading_day(now: datetime | None = None) -> date:
    """Most recent weekday whose session has closed (holidays are handled by the data itself)."""
    now = now or now_local()
    d = now.date()
    if now.weekday() < 5 and (now.hour, now.minute) >= (14, 45):
        return d
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def eod_done_for(d: date) -> bool:
    with SessionLocal() as s:
        return s.scalar(select(JobRun).where(JobRun.job == "eod", JobRun.run_date >= d, JobRun.ok.is_(True))) is not None


# ---------------------------------------------------------------- keeping the data current
CHECK_MINUTES = int(os.getenv("CSE_WATCHDOG_MINUTES") or 15)    # how often the watchdog looks for a missing evening run (env: for testing)
MAX_ATTEMPTS = 3                        # evening-run attempts per session before it gives up
RETRY_GAP = timedelta(minutes=60)       # ... spaced this far apart


def session_happened(d: date) -> bool | None:
    """True if the CSE traded on `d`, False if its latest trade is older (a holiday), None if it can't be told."""
    from .cse_client import client, ms_to_date
    try:
        last = max((r["lastTradedTime"] for r in client().trade_summary() if r.get("lastTradedTime")), default=None)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not tell whether the CSE traded on %s: %s", d, exc)
        return None
    return None if last is None else ms_to_date(last) >= d


def ensure_fresh(now: datetime | None = None, run: Any = None, traded: Any = None) -> str:
    """Run the evening job if the last finished session has no successful run. Called every CHECK_MINUTES and at startup,
    so a PC that slept through 15:30 (or an app that was off) catches up as soon as it is awake, instead of only at the
    next start. At most MAX_ATTEMPTS per session, RETRY_GAP apart; a session the CSE did not hold (a holiday) is recorded
    as done. Returns what it did, for the log and the tests."""
    now = now or now_local()
    run, traded = run or run_eod, traded or session_happened
    d = last_trading_day(now)
    if d == now.date() and (now.hour, now.minute) < (15, 30):
        return "waiting for the scheduled run"
    if eod_done_for(d):
        return "up to date"
    if _lock.locked():
        return "running"
    with SessionLocal() as s:
        runs = s.scalars(select(JobRun).where(JobRun.job == "eod", JobRun.run_date == d).order_by(JobRun.started_at.desc())).all()
    if len(runs) >= MAX_ATTEMPTS:
        return f"gave up after {MAX_ATTEMPTS} attempts"
    if runs and datetime.utcnow() - runs[0].started_at < RETRY_GAP:
        return "retrying later"
    if traded(d) is False:
        with SessionLocal.begin() as s:
            s.add(JobRun(job="eod", run_date=d, ok=True, message=json.dumps({"message": f"no session on {d} (the market was closed)"})))
        return f"no session on {d}"
    on_time = d == now.date() and (now.hour, now.minute) <= (16, 30)
    log.info("evening run for %s is missing; running it now%s", d, "" if on_time else " (late)")
    run(notify=True, late=None if on_time else f"recovered late, {now:%a %d %b %H:%M}")
    return "ran"


class _Steps:
    """What each step of one evening run did. A failing step is logged and recorded and the run carries on, so one
    broken data source no longer hides behind an overall OK (and never stops the analysis)."""

    def __init__(self) -> None:
        self.d: dict[str, dict[str, Any]] = {}

    @contextmanager
    def step(self, name: str):
        t0 = time.monotonic()
        try:
            yield
            self.d[name] = {"ok": True, "s": round(time.monotonic() - t0, 1)}
        except Exception as exc:  # noqa: BLE001
            log.warning("%s failed: %s", name, exc)
            self.d[name] = {"ok": False, "error": (str(exc) or type(exc).__name__)[:160], "s": round(time.monotonic() - t0, 1)}

    @contextmanager
    def each(self, name: str, sym: str):
        rec = self.d.setdefault(name, {"ok": True, "failed": {}})
        try:
            yield
        except Exception as exc:  # noqa: BLE001
            log.warning("%s %s: %s", name, sym, exc)
            rec["ok"] = False
            rec["failed"][sym] = (str(exc) or type(exc).__name__)[:120]

    def fail(self, name: str, error: str) -> None:
        self.d[name] = {"ok": False, "error": error[:160]}

    def summary(self) -> dict[str, dict[str, Any]]:
        return {k: {kk: vv for kk, vv in v.items() if vv not in ({}, None)} for k, v in self.d.items()}


def run_eod(notify: bool = True, only: list[str] | None = None, late: str | None = None) -> dict[str, Any]:
    """Collect today's data, analyse every watchlist stock, send the Telegram digest. `late` notes a run that came
    after its time (the PC was asleep); it is kept in the run's record."""
    if not _lock.acquire(blocking=False):
        return {"ok": False, "message": "EOD job already running"}
    started = now_local()
    out: dict[str, Any] = {"ok": True, "analysed": [], "errors": {}, "new_announcements": [], "alerts": []}
    steps = _Steps()
    try:
        with SessionLocal.begin() as s:
            syms = only or ingest.watchlist_symbols(s)
            if not syms:
                out["message"] = "watchlist empty"
                return out
            eod = ingest.record_eod(s, syms)
            out["eod"] = eod
            out["caught_up"] = ingest.catch_up(s)
        steps.d["prices"] = {"ok": True}
        # market extras (shown only) and market-wide notices matched to the watchlist
        with steps.step("sectors"):
            from . import listed_meta
            out["sectors"] = listed_meta.top_up()
        with steps.step("market extras"):
            with SessionLocal.begin() as s:
                out["market_extras"] = ingest.record_market_extras(s)
                got = ingest.sync_market_notices(s, syms)
                out["new_announcements"] += [{"symbol": a.symbol, "id": a.id, "title": a.title, "category": a.category,
                                              "materiality": a.materiality, "date": a.date.isoformat() if a.date else None}
                                             for a in got["notices"]]
                out["reports_synced"] = got["reports_synced"]
                intraday.record_aspi(s)
        with steps.step("headlines"):
            from . import news_feeds
            out["headlines"] = news_feeds.fetch(None, syms)
        if out.get("headlines", {}).get("failed") and steps.d.get("headlines", {}).get("ok"):
            steps.d["headlines"]["failed_feeds"] = out["headlines"]["failed"]     # information only: e.g. a site that refuses us
        out["headline_reader"] = read_headlines(syms)        # Claude when switched on; the keyword tone otherwise
        if "error" in out["headline_reader"]:
            steps.fail("headline reader", str(out["headline_reader"]["error"]))
        with steps.step("rates"):                       # the risk-free rate for the quant figures (CBSL T-bills, weekly at most)
            from .quant import rates
            out["rates"] = rates.top_up()
            if out["rates"].get("tbill_error"):
                raise RuntimeError("CBSL T-bill rate: " + out["rates"]["tbill_error"])
        with steps.step("world series"):
            from . import macro
            with SessionLocal.begin() as s:
                out["world"] = macro.top_up(s)
            empty = [k for k, v in out["world"].items() if not v]
            if empty:
                raise RuntimeError("no data for " + ", ".join(empty))
        prev = _previous_snapshot(syms)
        for sym in syms:
            with steps.each("announcements", sym):
                with SessionLocal.begin() as s:
                    new = ingest.sync_announcements(s, sym)
                    s.flush()
                    out.setdefault("enriched", {})[sym] = ingest.enrich_announcements(s, sym, max_requests=40)
                    if ingest.sync_reports(s, sym):
                        s.flush()
                        out.setdefault("figures", {})[sym] = ingest.sync_report_figures(s, sym)
                    out["new_announcements"] += [{"symbol": sym, "id": a.id, "title": a.title, "category": a.category,
                                                  "materiality": a.materiality, "date": a.date.isoformat() if a.date else None}
                                                 for a in new if a.date and (started.replace(tzinfo=None) - a.date).days <= 7]
            with steps.each("corporate actions", sym):
                with SessionLocal.begin() as s:
                    ingest.sync_corporate_actions(s, sym)
            vap = _today_volume_at_price(sym)   # also records today's trades as 1m intraday bars
            with steps.each("intraday", sym):
                with SessionLocal.begin() as s:
                    intraday.backfill_intraday(s, sym, full=not intraday.has_intraday(s, sym))
            try:
                with SessionLocal.begin() as s:
                    r = analyze_symbol(s, sym, intraday_vap=vap)
                if r:
                    out["analysed"].append(sym)
            except Exception as exc:
                log.exception("analysis failed for %s", sym)
                out["errors"][sym] = str(exc)
        steps.d["analysis"] = {"ok": not out["errors"], "n": len(out["analysed"]), **({"failed": dict(out["errors"])} if out["errors"] else {})}
        with steps.step("ASPI intraday"):
            with SessionLocal.begin() as s:
                intraday.backfill_intraday(s, intraday.ASPI, full=not intraday.has_intraday(s, intraday.ASPI))
        intraday._CACHE.clear()
        # forward-test journal: write down tonight's calls (kept as made), score older ones
        with steps.step("journal"):
            from . import journal
            out["journal"] = journal.record_day(out["analysed"])
        # alerts and the digest are for the Telegram owner's stocks (one bot, one chat); the analysis above covers everyone's
        with steps.step("alerts & digest"):
            from . import accounts
            with SessionLocal() as s:
                owner = accounts.telegram_owner(s)
                mine = accounts.user_symbols(s, owner)
                owner_wl = accounts.watchlist_symbols(s, owner)
            own = [x for x in syms if x in mine]
            out["alerts"] = build_alerts(own, prev, [x for x in out["new_announcements"] if x["symbol"] in mine], out.get("figures"), owner)
            from . import alerts as user_alerts
            with SessionLocal() as s:
                latest = s.scalar(select(func.max(Analysis.date)))
            day = str(latest or now_local().date())
            user_alerts.record_engine_alerts(owner, out["alerts"], day)           # the inbox keeps what the digest says
            user_alerts.evaluate_eod(syms, prev, out["new_announcements"])         # every account's own pattern / news / verdict / RSI rules
            user_alerts.prune()
            if notify:
                from . import telegram_bot
                telegram_bot.send_digest([x for x in own if x in owner_wl] + [x for x in own if x not in owner_wl], out["alerts"])
                _mark_notified([x["id"] for x in out["new_announcements"] if x["symbol"] in mine and (x.get("materiality") or 0) >= 3])
        with steps.step("backup"):
            out["backup"] = backup_db()
        with steps.step("offsite backup"):
            out["offsite"] = offsite_backup()
    except Exception as exc:
        log.exception("EOD job failed")
        out["ok"], out["message"] = False, str(exc)
        try:
            from . import telegram_bot
            telegram_bot.send_text(f"⚠️ CSE Advisor EOD job failed: {exc}")
        except Exception:
            pass
    finally:
        out["steps"] = steps.summary()
        record = {"analysed": len(out["analysed"]), "symbols": out["analysed"][:40], "errors": out["errors"], "steps": out["steps"],
                  "late": late, "message": out.get("message")}
        with SessionLocal.begin() as s:
            s.add(JobRun(job="eod", run_date=last_trading_day(), ok=out["ok"] and not out["errors"],
                         message=json.dumps({k: v for k, v in record.items() if v not in (None, {}, [])}, default=str, separators=(",", ":"))))
        _lock.release()
    if out.get("ok"):
        try:
            from . import screener
            screener.start_background()      # tonight's ranking of all liquid stocks (separate thread: it takes minutes)
        except Exception as exc:
            log.warning("screener start failed: %s", exc)
    return out


def backup_db() -> dict[str, Any]:
    """Nightly copy of the SQLite database to data/backups/ (SQLite's online backup, safe while the app runs); keeps the newest N."""
    import sqlite3
    from .config import DATA_DIR, settings
    from .db import engine
    if engine.dialect.name != "sqlite" or not engine.url.database:
        return {"skipped": "not a sqlite database"}
    dest_dir = DATA_DIR / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"cse_advisor_{last_trading_day().isoformat()}.db"
    src, dst = sqlite3.connect(engine.url.database), sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    keep = int((settings().get("backup") or {}).get("keep", 14))
    old = sorted(dest_dir.glob("cse_advisor_*.db"))[:-keep] if keep > 0 else []
    for f in old:
        f.unlink(missing_ok=True)
    return {"file": dest.name, "kept": min(len(list(dest_dir.glob('cse_advisor_*.db'))), keep)}


def _mark_notified(ids: list[int]) -> int:
    """Announcements that went out in a digest are marked, so the flag says what was told to the user."""
    if not ids:
        return 0
    with SessionLocal.begin() as s:
        return s.execute(update(Announcement).where(Announcement.id.in_(ids)).values(notified=True)).rowcount or 0


def offsite_dir() -> Path | None:
    """Where the weekly zip of the newest backup goes: settings backup.offsite_dir; empty = a CSE-Advisor-backups folder in
    OneDrive if there is one (finished zip files are safe to sync, a live database is not); "off" = never."""
    from .config import settings
    raw = str((settings().get("backup") or {}).get("offsite_dir") or "").strip()
    if raw.lower() == "off":
        return None
    if raw:
        return Path(os.path.expandvars(raw)).expanduser()
    od = Path.home() / "OneDrive"
    return od / "CSE-Advisor-backups" if od.exists() else None


def offsite_backup(dest: Path | None = None, keep: int = 8, today: date | None = None) -> dict[str, Any]:
    """Once a week: the newest nightly backup, zipped, into the off-site folder; the newest `keep` zips are kept.
    Needs the nightly backup (backup_db) to have run."""
    from .config import DATA_DIR
    dest = dest if dest is not None else offsite_dir()
    if dest is None:
        return {"skipped": "no off-site folder (set backup.offsite_dir in settings.yaml)"}
    newest = max((DATA_DIR / "backups").glob("cse_advisor_*.db"), key=lambda f: f.name, default=None)
    if newest is None:
        return {"skipped": "no nightly backup yet"}
    year, week, _ = (today or last_trading_day()).isocalendar()
    target = dest / f"cse_advisor_{year}-W{week:02d}.zip"
    if target.exists():
        return {"skipped": f"this week's copy exists ({target.name})"}
    dest.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(newest, newest.name)
    tmp.replace(target)
    for old in sorted(dest.glob("cse_advisor_*.zip"), key=lambda f: f.name)[:-keep]:
        old.unlink(missing_ok=True)
    return {"file": str(target), "mb": round(target.stat().st_size / 1e6, 1)}


def _today_volume_at_price(sym: str) -> dict[str, Any] | None:
    """Group today's individual trades by price (only meaningful on the trading day itself)."""
    from .analysis.signals import intraday_volume_at_price
    from .cse_client import client, ms_to_date
    from .db import Security
    try:
        with SessionLocal() as s:
            sec = s.get(Security, sym)
        if not sec or not sec.cse_id:
            return None
        trades = client().intraday_trades(sec.cse_id)
        if not trades or ms_to_date(max(t["t"] for t in trades)) != last_trading_day():
            return None
        try:
            with SessionLocal.begin() as s:
                intraday.record_intraday(s, sym, trades)
        except Exception as exc:
            log.warning("recording intraday trades %s: %s", sym, exc)
        out = intraday_volume_at_price(trades)
        if out:
            out["date"] = str(last_trading_day())
            out["rows"] = out["rows"][-60:]
        return out
    except Exception as exc:
        log.debug("intraday trades %s: %s", sym, exc)
        return None


def _previous_snapshot(syms: list[str]) -> dict[str, dict[str, Any]]:
    snap = {}
    with SessionLocal() as s:
        for sym in syms:
            a = s.scalar(select(Analysis).where(Analysis.symbol == sym).order_by(Analysis.date.desc()))
            if a:
                snap[sym] = {"verdict": a.verdict, "score": a.score, "date": a.date,
                             "verdict_analyst": a.verdict_analyst, "score_analyst": a.score_analyst,
                             "verdict_smc": a.verdict_smc, "score_smc": a.score_smc,
                             "pattern_ids": {p["id"] for p in a.patterns}}
    return snap


def build_alerts(syms: list[str], prev: dict[str, dict[str, Any]], new_anns: list[dict[str, Any]],
                 figures: dict[str, Any] | None = None, user_id: str | None = None) -> list[dict[str, Any]]:
    """Alerts for one account's stocks (the Telegram owner's): signal changes of the shared analysis, new patterns and
    results, and this account's position reviews."""
    from . import accounts
    from .db import LOCAL_UID
    uid = user_id or LOCAL_UID
    alerts: list[dict[str, Any]] = []
    with SessionLocal() as s:
        for sym in syms:
            a = s.scalar(select(Analysis).where(Analysis.symbol == sym).order_by(Analysis.date.desc()))
            if not a:
                continue
            p = prev.get(sym)
            known = (p or {}).get("pattern_ids", set())        # patterns the previous analysis already had
            if p and p["date"] == a.date and p["verdict"] == a.verdict and p.get("verdict_analyst") == a.verdict_analyst \
                    and p.get("verdict_smc") == a.verdict_smc:
                p = None                                       # re-run on the same day: only verdict changes
            if p and p["verdict"] != a.verdict:
                alerts.append({"symbol": sym, "kind": "signal_change", "text": f"[CLS] {p['verdict']} → {a.verdict} (score {a.score:.0f})"})
            if p and a.verdict_analyst and p.get("verdict_analyst") and p["verdict_analyst"] != a.verdict_analyst:
                alerts.append({"symbol": sym, "kind": "signal_change",
                               "text": f"[LVL] {p['verdict_analyst']} → {a.verdict_analyst} (level score {a.score_analyst or 0:.0f})"})
            if p and a.verdict_smc and p.get("verdict_smc") and p["verdict_smc"] != a.verdict_smc:
                alerts.append({"symbol": sym, "kind": "signal_change",
                               "text": f"[SMC] {p['verdict_smc']} → {a.verdict_smc} (SMC score {a.score_smc or 0:.0f})"})
            old_ids = known                                    # (kept on a same-day re-run, so patterns are not announced twice)
            for pat in a.patterns:
                if pat.get("category") not in ("level", "smc", "candlestick") and pat["priority_tier"] == "high" and pat["id"] not in old_ids and pat["context"].get("age_bars", 99) <= 2:
                    alerts.append({"symbol": sym, "kind": "new_pattern",
                                   "text": f"New high-priority pattern: {pat['name']} ({pat['direction']}, {pat['status']})"})
            # results just read from a new statement PDF
            if (figures or {}).get(sym, {}).get("parsed"):
                lr = ((a.recommendation or {}).get("catalysts") or {}).get("latest_results") or {}
                if lr.get("net_profit") is not None:
                    y = lr.get("profit_yoy_pct")
                    alerts.append({"symbol": sym, "kind": "results",
                                   "text": f"New results, quarter to {lr['period_end']}: profit LKR {lr['net_profit'] / 1e6:,.0f}M"
                                           + (f" ({y:+.0f}% YoY)" if y is not None else "") + (f", EPS {lr['eps']:.2f}" if lr.get("eps") is not None else "")})
            pos = accounts.open_position(s, uid, sym)
            if pos:
                try:
                    from . import portfolio
                    from .engine import load_adjusted
                    adj, _ = load_adjusted(s, sym)
                    cl = adj["close"]
                    te = portfolio.trend_exit(cl.to_numpy()[-2:], cl.ewm(span=50, adjust=False, min_periods=50).mean().to_numpy()[-2:])
                    for w in portfolio.exit_warnings([pt for pt in a.patterns if pt["id"] not in old_ids], te if te and te["crossed_today"] else None):
                        alerts.append({"symbol": sym, "kind": "position", "text": "Consider trimming: " + w["text"]})
                except Exception as exc:
                    log.warning("exit warnings %s: %s", sym, exc)
                for tag, eng_rec in engine_recs(accounts.effective(s, uid, a)["recommendation"]):
                    rv = (eng_rec or {}).get("position_review") or {}
                    if rv.get("action") in ("SELL", "TRIM"):
                        alerts.append({"symbol": sym, "kind": "position", "text": f"[{tag}] Position: {rv['action']} because " + "; ".join(rv.get("reasons", []))})
    for ann in new_anns:
        if (ann.get("materiality") or 0) >= 3:
            alerts.append({"symbol": ann["symbol"], "kind": "announcement", "text": f"Announcement: {ann['title']} ({ann['category']})"})
    return alerts


def startup_catch_up() -> None:
    """If the PC was off at the scheduled time, run the missed EOD job now; then make sure every watchlist
    stock (and the ASPI) has intraday history and the sector table has some history."""
    log.info("startup: %s", ensure_fresh())
    try:
        from .quant import rates
        log.info("rates: %s", rates.top_up())
    except Exception as exc:  # noqa: BLE001
        log.warning("rates refresh failed: %s", exc)
    try:
        from . import listed_meta
        log.info("sectors: %s", listed_meta.top_up())
    except Exception as exc:  # noqa: BLE001
        log.warning("sector refresh failed: %s", exc)
    history_catch_up()
    fundamentals_catch_up()
    catalysts_catch_up()
    intraday_catch_up()
    world_catch_up()
    try:
        from . import screener
        screener.catch_up()
    except Exception as exc:
        log.warning("screener catch-up failed: %s", exc)


def read_headlines(syms: list[str]) -> dict[str, Any]:
    """Claude reads the new headlines (news_llm.py) when switched on; never fails the job."""
    try:
        from . import news_llm
        with SessionLocal.begin() as s:
            return news_llm.classify_pending(s, syms)
    except Exception as exc:  # noqa: BLE001
        log.warning("headline reader failed: %s", exc)
        return {"error": str(exc)[:200]}


def world_catch_up() -> dict[str, Any]:
    """World series (Brent, Nasdaq-100, ...): the full history once, and the macro study once so the MKT page can show
    each series' measured link to the ASPI."""
    out: dict[str, Any] = {}
    try:
        from . import macro
        with SessionLocal.begin() as s:
            out["series"] = macro.top_up(s)
        if not macro.LINKS_FILE.exists():
            from . import macro_study
            macro_study.main()
            out["study"] = "written"
    except Exception as exc:  # noqa: BLE001
        log.warning("world catch-up failed: %s", exc)
    return out


def history_catch_up(force: bool = False) -> dict[str, Any]:
    """Older daily history (TradingView, back to ~2012) for watchlist stocks that don't have it yet, plus
    the ASPI; then re-analyse those stocks so the engines use it."""
    from sqlalchemy import func, select
    from .db import MarketDaily, Security
    out: dict[str, Any] = {}
    try:
        with SessionLocal() as s:
            todo = [x for x in ingest.watchlist_symbols(s) if force or not ingest.has_deep_history(s, x)]
            n_market = s.scalar(select(func.count()).select_from(MarketDaily)) or 0
        if n_market < 1000 or force:
            with SessionLocal.begin() as s:
                out["ASPI"] = ingest.backfill_market_deep(s)
        for sym in todo:
            with SessionLocal.begin() as s:
                sec = s.get(Security, sym)
                out[sym] = ingest.backfill_deep_history(s, sec) if sec else {"error": "unknown"}
            with SessionLocal.begin() as s:
                analyze_symbol(s, sym)
        if todo:
            intraday._CACHE.clear()
            log.info("history catch-up: %s", out)
    except Exception as exc:
        log.warning("history catch-up failed: %s", exc)
    return out


def fundamentals_catch_up() -> dict[str, Any]:
    """Read the newest quarterly statements for watchlist stocks that have no figures from them yet."""
    from sqlalchemy import func, select
    from .db import Fundamental
    out: dict[str, Any] = {}
    try:
        with SessionLocal() as s:
            todo = [x for x in ingest.watchlist_symbols(s)
                    if not s.scalar(select(func.count()).select_from(Fundamental).where(Fundamental.symbol == x))]
        for sym in todo:
            with SessionLocal.begin() as s:
                out[sym] = ingest.sync_report_figures(s, sym)
            with SessionLocal.begin() as s:
                analyze_symbol(s, sym)
        if todo:
            log.info("fundamentals catch-up: %s", out)
    except Exception as exc:
        log.warning("fundamentals catch-up failed: %s", exc)
    return out


def catalysts_catch_up() -> dict[str, Any]:
    """One-off and resumable: read the typed details of past dividend / dealing / disclosure notices for every
    watchlist stock (one request each, ~1 s apart), fetch headlines, then re-analyse what changed."""
    out: dict[str, Any] = {}
    try:
        from . import news_feeds
        with SessionLocal() as s:
            syms = ingest.watchlist_symbols(s)
        out["headlines"] = news_feeds.fetch(None, syms, google=False)     # the Google searches run with the evening job
        out["headline_reader"] = read_headlines(syms)
        for sym in syms:
            with SessionLocal.begin() as s:
                r = ingest.enrich_announcements(s, sym)
            out[sym] = r
            if r["fetched"] or r["reclassified"] or out["headlines"].get("new"):
                with SessionLocal.begin() as s:
                    analyze_symbol(s, sym)
        log.info("catalysts catch-up: %s", out)
    except Exception as exc:
        log.warning("catalysts catch-up failed: %s", exc)
    return out


def intraday_catch_up() -> dict[str, Any]:
    from sqlalchemy import func, select
    from .db import SectorDaily
    out: dict[str, Any] = {}
    try:
        with SessionLocal() as s:
            ingest_syms = ingest.watchlist_symbols(s) + [intraday.ASPI]
            todo = [x for x in ingest_syms if not intraday.has_intraday(s, x)]
            n_sector = s.scalar(select(func.count()).select_from(SectorDaily)) or 0
        for sym in todo:
            with SessionLocal.begin() as s:
                out[sym] = intraday.backfill_intraday(s, sym, full=True)
        # timeframes added after a stock was first backfilled (1m): fetch just those
        for sym in ingest_syms:
            with SessionLocal.begin() as s:
                tfs = intraday.missing_timeframes(s, sym)
                if tfs and sym not in todo:
                    out[sym] = intraday.backfill_intraday(s, sym, full=True, tfs=tfs)
                    todo.append(sym)
        # one attempt only: most S&P/CSE sector indices are licensed ('permission denied' for anonymous
        # TradingView sessions), so their history builds from the nightly allSectors recording instead
        from .db import Preference
        with SessionLocal() as s:
            tried = s.get(Preference, "sector_backfill_tried") is not None
        if n_sector < 200 and not tried and intraday.cfg()["tradingview"]:
            with SessionLocal.begin() as s:
                out["sectors"] = ingest.backfill_sectors(s)
                ingest.record_market_extras(s)
                s.merge(Preference(key="sector_backfill_tried", value=True))
        if todo:
            intraday._CACHE.clear()
            log.info("intraday catch-up: %s", out)
    except Exception as exc:
        log.warning("intraday catch-up failed: %s", exc)
    return out
