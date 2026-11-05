"""End-of-day job, startup catch-up and alert generation."""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select

from .strategy.recommend import engine_recs
from . import ingest, intraday
from .cse_client import now_local
from .db import Analysis, Announcement, JobRun, Position, SessionLocal
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


def run_eod(notify: bool = True, only: list[str] | None = None) -> dict[str, Any]:
    """Collect today's data, analyse every watchlist stock, send the Telegram digest."""
    if not _lock.acquire(blocking=False):
        return {"ok": False, "message": "EOD job already running"}
    started = now_local()
    out: dict[str, Any] = {"ok": True, "analysed": [], "errors": {}, "new_announcements": []}
    try:
        with SessionLocal.begin() as s:
            syms = only or ingest.watchlist_symbols(s)
            if not syms:
                out["message"] = "watchlist empty"
                return out
            eod = ingest.record_eod(s, syms)
            out["eod"] = eod
            out["caught_up"] = ingest.catch_up(s)
        # market extras (shown only) and market-wide notices matched to the watchlist
        try:
            with SessionLocal.begin() as s:
                out["market_extras"] = ingest.record_market_extras(s)
                got = ingest.sync_market_notices(s, syms)
                out["new_announcements"] += [{"symbol": a.symbol, "id": a.id, "title": a.title, "category": a.category,
                                              "materiality": a.materiality, "date": a.date.isoformat() if a.date else None}
                                             for a in got["notices"]]
                out["reports_synced"] = got["reports_synced"]
                intraday.record_aspi(s)
        except Exception as exc:
            log.warning("market extras failed: %s", exc)
        try:
            from . import news_feeds
            with SessionLocal.begin() as s:
                out["headlines"] = news_feeds.fetch(s, syms)
        except Exception as exc:
            log.warning("headlines failed: %s", exc)
        prev = _previous_snapshot(syms)
        for sym in syms:
            try:
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
            except Exception as exc:
                log.warning("announcements %s: %s", sym, exc)
            try:
                with SessionLocal.begin() as s:
                    ingest.sync_corporate_actions(s, sym)
            except Exception as exc:
                log.warning("corporate actions %s: %s", sym, exc)
            vap = _today_volume_at_price(sym)   # also records today's trades as 1m intraday bars
            try:
                with SessionLocal.begin() as s:
                    intraday.backfill_intraday(s, sym, full=not intraday.has_intraday(s, sym))
            except Exception as exc:
                log.warning("intraday top-up %s: %s", sym, exc)
            try:
                with SessionLocal.begin() as s:
                    r = analyze_symbol(s, sym, intraday_vap=vap)
                if r:
                    out["analysed"].append(sym)
            except Exception as exc:
                log.exception("analysis failed for %s", sym)
                out["errors"][sym] = str(exc)
        try:
            with SessionLocal.begin() as s:
                intraday.backfill_intraday(s, intraday.ASPI, full=not intraday.has_intraday(s, intraday.ASPI))
        except Exception as exc:
            log.warning("ASPI intraday top-up: %s", exc)
        intraday._CACHE.clear()
        # forward-test journal: write down tonight's calls (kept as made), score older ones
        try:
            from . import journal
            out["journal"] = journal.record_day(out["analysed"])
        except Exception as exc:
            log.warning("journal failed: %s", exc)
        out["alerts"] = build_alerts(syms, prev, out["new_announcements"], out.get("figures"))
        if notify:
            from . import telegram_bot
            telegram_bot.send_digest(syms, out["alerts"])
        try:
            out["backup"] = backup_db()
        except Exception as exc:
            log.warning("database backup failed: %s", exc)
    except Exception as exc:
        log.exception("EOD job failed")
        out["ok"], out["message"] = False, str(exc)
        try:
            from . import telegram_bot
            telegram_bot.send_text(f"⚠️ CSE Advisor EOD job failed: {exc}")
        except Exception:
            pass
    finally:
        with SessionLocal.begin() as s:
            s.add(JobRun(job="eod", run_date=last_trading_day(), ok=out["ok"] and not out["errors"],
                         message=str({k: v for k, v in out.items() if k in ("message", "errors", "analysed")})[:2000]))
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
                 figures: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    with SessionLocal() as s:
        for sym in syms:
            a = s.scalar(select(Analysis).where(Analysis.symbol == sym).order_by(Analysis.date.desc()))
            if not a:
                continue
            p = prev.get(sym)
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
            old_ids = p["pattern_ids"] if p else set()
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
            pos = s.scalar(select(Position).where(Position.symbol == sym, Position.closed.is_(False)))
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
                for tag, eng_rec in engine_recs(a.recommendation):
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
    d = last_trading_day()
    if not eod_done_for(d):
        log.info("EOD for %s not done yet; running catch-up", d)
        run_eod(notify=True)
    history_catch_up()
    fundamentals_catch_up()
    catalysts_catch_up()
    intraday_catch_up()
    try:
        from . import screener
        screener.catch_up()
    except Exception as exc:
        log.warning("screener catch-up failed: %s", exc)


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
        with SessionLocal.begin() as s:
            out["headlines"] = news_feeds.fetch(s, syms)
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
