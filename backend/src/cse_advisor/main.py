"""FastAPI app: JSON API + SSE live quotes + scheduler + serves the built React frontend.

Run:  python -m cse_advisor.main     (or: uvicorn cse_advisor.main:app)
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select

from .strategy.recommend import plan_summary, split_engines
from . import engine as eng
from . import ingest, jobs, journal, portfolio, screener, telegram_bot
from .config import FRONTEND_DIST, pattern_catalog, settings
from .cse_client import CDN
from .db import (
    Analysis, Announcement, CorporateAction, FinancialReport, Fundamental, JobRun, MarketDaily, Position, Preference, PriceDaily,
    SessionLocal, Security, WatchlistItem, init_db,
)
from .live import live, market_open

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("cse_advisor")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    tz = settings()["market"]["timezone"]
    hh, mm = map(int, settings()["market"]["eod_job_time"].split(":"))
    sched = BackgroundScheduler(timezone=tz)
    sched.add_job(jobs.run_eod, CronTrigger(day_of_week="mon-fri", hour=hh, minute=mm, timezone=tz), id="eod",
                  misfire_grace_time=3 * 3600, coalesce=True)
    sched.start()
    threading.Thread(target=jobs.startup_catch_up, daemon=True, name="catch-up").start()
    tg_stop = telegram_bot.start_polling()
    task = asyncio.create_task(live.run())
    log.info("CSE Advisor ready; EOD job daily at %s %s", settings()["market"]["eod_job_time"], tz)
    yield
    task.cancel()
    tg_stop.set()
    sched.shutdown(wait=False)


app = FastAPI(title="CSE Advisor", lifespan=lifespan)


def _latest(s, symbol: str) -> Analysis | None:
    return s.scalar(select(Analysis).where(Analysis.symbol == symbol).order_by(Analysis.date.desc()))


# ============================================================================ watchlist
class AddReq(BaseModel):
    query: str


@app.get("/api/watchlist")
def get_watchlist() -> list[dict[str, Any]]:
    out = []
    with SessionLocal() as s:
        for item in s.scalars(select(WatchlistItem).order_by(WatchlistItem.added_at)):
            sec = s.get(Security, item.symbol)
            a = _latest(s, item.symbol)
            closes = s.scalars(select(PriceDaily.close).where(PriceDaily.symbol == item.symbol)
                               .order_by(PriceDaily.date.desc()).limit(60)).all()[::-1]
            q = live.quotes.get(item.symbol)
            ro, ra = split_engines(a.recommendation if a else None)
            rs = (a.recommendation or {}).get("smc") if a else None
            agree = (a.recommendation or {}).get("agreement") if a else None
            last = q["close"] if q else (closes[-1] if closes else None)
            top = next((p for p in (a.patterns if a else []) if p["status"] in ("confirmed", "forming") and p.get("category") != "level"), None)
            out.append({
                "symbol": item.symbol, "name": sec.name if sec else item.symbol, "sector": sec.sector if sec else None,
                "logo": f"{CDN}cmt/{sec.logo_path}" if sec and sec.logo_path else None,
                "price": last, "change_pct": q["change_pct"] if q else (((closes[-1] / closes[-2]) - 1) * 100 if len(closes) > 1 else None),
                "sparkline": closes, "verdict": a.verdict if a else None, "score": a.score if a else None,
                "analysis_date": a.date.isoformat() if a else None,
                "trend": a.trend["state"] if a else None,
                "liquidity_grade": a.liquidity["grade"] if a else None, "liquidity_regime": a.liquidity["regime"] if a else None,
                "top_pattern": {"name": top["name"], "direction": top["direction"], "status": top["status"], "priority": top["priority"]} if top else None,
                "plan": plan_summary(ro.get("plan")),
                "summary": ro.get("explanation", {}).get("summary") if ro else None,
                "verdict_analyst": a.verdict_analyst if a else None, "score_analyst": a.score_analyst if a else None,
                "plan_analyst": plan_summary((ra or {}).get("plan")),
                "zone1": ((a.levels or {}).get("confluence") or {}).get("zone1") if a else None,
                "summary_analyst": (ra or {}).get("explanation", {}).get("summary") if ra else None,
                "verdict_smc": a.verdict_smc if a else None, "score_smc": a.score_smc if a else None,
                "plan_smc": plan_summary((rs or {}).get("plan")),
                "summary_smc": (rs or {}).get("explanation", {}).get("summary") if rs else None,
                "agreement": agree,
            })
    return out


@app.post("/api/watchlist")
def add_watchlist(req: AddReq) -> dict[str, Any]:
    try:
        sym = ingest.add_to_watchlist(req.query)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    with SessionLocal.begin() as s:
        r = eng.analyze_symbol(s, sym)
    return {"symbol": sym, "analysed": r is not None}


@app.delete("/api/watchlist/{symbol}")
def del_watchlist(symbol: str) -> dict[str, str]:
    ingest.remove_from_watchlist(symbol)
    return {"removed": symbol}


# ============================================================================ stock data
class CorporateActionIn(BaseModel):
    kind: str = "rights"
    ex_date: date | None = None
    ratio_new: float | None = None
    ratio_old: float | None = None
    price: float | None = None
    listing_date: date | None = None
    ignore: bool = False


class ImportIn(BaseModel):
    csv: str
    already_adjusted: bool = False


@app.get("/api/stocks/{symbol}/corporate-actions")
def corporate_actions(symbol: str) -> list[dict[str, Any]]:
    from .engine import load_adjusted
    with SessionLocal() as s:
        _, acts = load_adjusted(s, symbol)
    return _jsonable_list(acts)


@app.post("/api/stocks/{symbol}/corporate-actions")
def add_corporate_action(symbol: str, a: CorporateActionIn) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        row = CorporateAction(symbol=symbol, manual=True, **a.model_dump())
        s.add(row)
        s.flush()
        rid = row.id
    _reanalyze_quiet(symbol)
    return {"ok": True, "id": rid}


@app.put("/api/stocks/{symbol}/corporate-actions/{aid}")
def edit_corporate_action(symbol: str, aid: int, a: CorporateActionIn) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        row = s.get(CorporateAction, aid)
        if not row or row.symbol != symbol:
            raise HTTPException(404, "not found")
        for k, v in a.model_dump().items():
            setattr(row, k, v)
        row.manual = True
    _reanalyze_quiet(symbol)
    return {"ok": True}


@app.delete("/api/stocks/{symbol}/corporate-actions/{aid}")
def delete_corporate_action(symbol: str, aid: int) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        row = s.get(CorporateAction, aid)
        if row and row.symbol == symbol:
            s.delete(row)
    _reanalyze_quiet(symbol)
    return {"ok": True}


@app.post("/api/stocks/{symbol}/import")
def import_history(symbol: str, body: ImportIn) -> dict[str, Any]:
    if len(body.csv) > 5_000_000:
        raise HTTPException(413, "CSV too large")
    try:
        with SessionLocal.begin() as s:
            res = ingest.import_history_csv(s, symbol, body.csv, body.already_adjusted)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _reanalyze_quiet(symbol)
    return res


def _reanalyze_quiet(symbol: str) -> None:
    try:
        with SessionLocal.begin() as s:
            eng.analyze_symbol(s, symbol)
    except Exception as exc:  # pragma: no cover - best effort
        log.warning("re-analysis failed for %s: %s", symbol, exc)


def _jsonable_list(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from .engine import _jsonable
    return _jsonable(rows)


def _tf(tf: str) -> str:
    t = (tf or "1D").strip()
    low = t.lower()
    if low in ("1m", "5m", "15m", "1h", "4h"):
        return low
    if low in ("1", "1min"):
        return "1m"
    if low in ("60", "60m"):
        return "1h"
    if low in ("240", "240m"):
        return "4h"
    return "1W" if low == "1w" else "1D"


@app.get("/api/stocks/{symbol}/candles")
def candles(symbol: str, tf: str = "1D", raw: bool = False) -> dict[str, Any]:
    with SessionLocal() as s:
        return eng._jsonable(eng.chart_data(s, symbol, _tf(tf), adjusted=not raw))


@app.get("/api/stocks/{symbol}/analysis")
def analysis(symbol: str, tf: str = "1D") -> dict[str, Any]:
    t = _tf(tf)
    if t not in ("1D", "1W"):
        with SessionLocal.begin() as s:
            sec = s.get(Security, symbol)
            if not sec:
                raise HTTPException(404, "unknown symbol")
            r = eng.analyze_intraday(s, symbol, t)
        if r.get("error"):
            return {"symbol": symbol, "name": sec.name, "tf": t, "error": r["error"], "coverage": r.get("coverage")}
        rec = r["recommendation"]
        return eng._jsonable({**{k: r[k] for k in ("patterns", "levels", "liquidity", "trend", "recommendation", "coverage", "daily", "elapsed_ms", "time")},
                              "symbol": symbol, "name": sec.name, "tf": t, "date": str(r["date"]), "created_at": None,
                              "verdict": rec["original"]["verdict"], "score": rec["original"]["score"],
                              "verdict_analyst": rec["analyst"]["verdict"], "score_analyst": rec["analyst"]["score"],
                              "verdict_smc": rec["smc"]["verdict"], "score_smc": rec["smc"]["score"],
                              "logo": f"{CDN}cmt/{sec.logo_path}" if sec.logo_path else None, "sector": _sector_today(s, sec)})
    with SessionLocal() as s:
        a = _latest(s, symbol)
        sec = s.get(Security, symbol)
        if not a:
            raise HTTPException(404, "No analysis yet; run the EOD job or re-add the stock")
        return {"symbol": symbol, "name": sec.name if sec else symbol, "tf": "1D", "sector": _sector_today(s, sec),
                "date": a.date.isoformat(), "created_at": a.created_at.isoformat(),
                "verdict": a.verdict, "score": a.score, "verdict_analyst": a.verdict_analyst, "score_analyst": a.score_analyst,
                "verdict_smc": a.verdict_smc, "score_smc": a.score_smc,
                "patterns": a.patterns, "levels": a.levels, "liquidity": a.liquidity,
                "trend": a.trend, "recommendation": a.recommendation,
                "logo": f"{CDN}cmt/{sec.logo_path}" if sec and sec.logo_path else None}


@app.get("/api/stocks/{symbol}/live")
def live_check(symbol: str) -> dict[str, Any]:
    """The live price against the stored daily analysis' levels: which patterns and plans the session has already
    invalidated, hit or overtaken. {} when there is no live quote from a session after the analysis."""
    from .live_status import live_overlay
    with SessionLocal() as s:
        a = _latest(s, symbol)
        if not a:
            return {}
        quote = live.quotes.get(symbol)
        out = live_overlay({"date": a.date.isoformat(), "recommendation": a.recommendation, "patterns": a.patterns}, quote)
    return out or {}


def _sector_today(s, sec: Security | None) -> dict[str, Any] | None:
    from .db import SectorDaily
    if not sec or not sec.sector:
        return None
    r = s.scalar(select(SectorDaily).where(SectorDaily.code == sec.sector).order_by(SectorDaily.date.desc()))
    return {"code": sec.sector, "name": r.name if r else sec.sector, "date": r.date.isoformat() if r else None,
            "value": r.value if r else None, "change_pct": r.change_pct if r else None}


class SectorIn(BaseModel):
    sector: str | None = None


@app.put("/api/stocks/{symbol}/sector")
def set_sector(symbol: str, body: SectorIn) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        sec = s.get(Security, symbol)
        if not sec:
            raise HTTPException(404, "unknown symbol")
        sec.sector = (body.sector or "").strip().upper() or None
    return {"ok": True, "sector": body.sector}


@app.post("/api/stocks/{symbol}/intraday/backfill")
def intraday_backfill(symbol: str) -> dict[str, Any]:
    from . import intraday
    with SessionLocal.begin() as s:
        sec = s.get(Security, symbol)
        if not sec:
            raise HTTPException(404, "unknown symbol")
        daily = ingest.backfill_deep_history(s, sec)
        got = intraday.backfill_intraday(s, symbol, full=True)
    intraday.refresh_today(symbol, force=True)
    intraday._CACHE.clear()
    if daily.get("tv_bars"):
        with SessionLocal.begin() as s:
            eng.analyze_symbol(s, symbol)
    if not any(got.values()) and not daily.get("tv_bars"):
        raise HTTPException(502, "TradingView returned no bars (it may be switched off in settings, or unavailable)")
    return {"ok": True, "bars": got, "daily": daily}


@app.post("/api/stocks/{symbol}/analyze")
def reanalyze(symbol: str) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        r = eng.analyze_symbol(s, symbol)
    if not r:
        raise HTTPException(400, "Not enough data to analyse")
    return {"ok": True, "verdict": r["verdict"], "score": r["score"], "verdict_analyst": r["verdict_analyst"], "score_analyst": r["score_analyst"],
            "verdict_smc": r["verdict_smc"], "score_smc": r["score_smc"]}


@app.get("/api/stocks/{symbol}/history")
def history(symbol: str, limit: int = 60) -> list[dict[str, Any]]:
    with SessionLocal() as s:
        rows = s.scalars(select(Analysis).where(Analysis.symbol == symbol).order_by(Analysis.date.desc()).limit(limit)).all()
        return [{"date": r.date.isoformat(), "verdict": r.verdict, "score": r.score,
                 "verdict_analyst": r.verdict_analyst, "score_analyst": r.score_analyst,
                 "verdict_smc": r.verdict_smc, "score_smc": r.score_smc} for r in rows]


# ---------------------------------------------------------------- fundamentals
class FundamentalIn(BaseModel):
    period_end: date
    period_type: str = "Q"
    revenue: float | None = None
    gross_profit: float | None = None
    operating_profit: float | None = None
    net_profit: float | None = None
    eps: float | None = None
    total_assets: float | None = None
    total_liabilities: float | None = None
    total_equity: float | None = None
    current_assets: float | None = None
    current_liabilities: float | None = None
    total_debt: float | None = None
    finance_cost: float | None = None
    operating_cash_flow: float | None = None
    dividend_per_share: float | None = None
    shares_outstanding: float | None = None
    one_off_income: float | None = None


def _report_period(r: FinancialReport) -> str | None:
    """The period a report covers (its own title first: CSE's date is sometimes the upload day)."""
    from .analysis.events import annual_year_end
    from .analysis.report_parser import period_from_title
    up = r.uploaded.date() if r.uploaded else None
    d = annual_year_end(r.title, r.period_date.date() if r.period_date else None, up) if r.kind == "annual" else period_from_title(r.title)[0]
    return (d or (r.period_date.date() if r.period_date else None)).isoformat() if (d or r.period_date) else None


def _next_reports(reports: list[FinancialReport]) -> dict[str, Any]:
    """When the next quarterly statements and the next annual report are expected (from this company's own history)."""
    from .analysis.events import annual_due, results_due
    from .analysis.report_parser import period_from_title
    today = date.today()
    pairs = [(pe, r.uploaded.date()) for r in reports if r.kind == "quarterly" and r.uploaded
             for pe in [period_from_title(r.title)[0]] if pe]
    ann = [(r.title, r.period_date.date() if r.period_date else None, r.uploaded.date() if r.uploaded else None)
           for r in reports if r.kind == "annual"]
    return {"quarterly": results_due(pairs, today), "annual": annual_due(ann, today)}


@app.get("/api/stocks/{symbol}/fundamentals")
def fundamentals(symbol: str) -> dict[str, Any]:
    from .analysis.fundamentals import summarize
    with SessionLocal() as s:
        sec = s.get(Security, symbol)
        if not sec:
            raise HTTPException(404, "unknown symbol")
        all_rows = [{c.name: getattr(r, c.name) for c in r.__table__.columns}
                    for r in s.scalars(select(Fundamental).where(Fundamental.symbol == symbol).order_by(Fundamental.period_end))]
        rows = [r for r in all_rows if not r.get("needs_review")]
        last = s.scalar(select(PriceDaily.close).where(PriceDaily.symbol == symbol).order_by(PriceDaily.date.desc()))
        summ = summarize(rows, last or 0, sec.info, sec.shares_issued, eng.market_valuation(s))
        # auto-extracted rows: their evidence, and the ones waiting for a check (not used in any score)
        summ["review"] = [r for r in all_rows if r.get("needs_review")][-12:]
        summ["auto"] = {str(r["period_end"]): {"source": r["source"], "detail": r.get("detail")} for r in all_rows if r["source"] != "manual"}
        summ["market_valuation"] = eng.market_valuation(s)
        reports = s.scalars(select(FinancialReport).where(FinancialReport.symbol == symbol).order_by(FinancialReport.period_date.desc())).all()
        summ["reports"] = [{"id": r.id, "kind": r.kind, "title": r.title, "url": CDN + r.path,
                            "period": _report_period(r), "filed": r.uploaded.date().isoformat() if r.uploaded else None}
                           for r in reports[:24]]
        summ["next_reports"] = _next_reports(reports)
        summ["beta"] = {"aspi": sec.beta_aspi, "spsl20": sec.beta_spsl}
        summ["name"] = sec.name
        return eng._jsonable(summ)


@app.post("/api/stocks/{symbol}/fundamentals")
def add_fundamentals(symbol: str, f: FundamentalIn) -> dict[str, Any]:
    if f.period_type not in ("Q", "FY"):
        raise HTTPException(400, "period_type must be Q or FY")
    with SessionLocal.begin() as s:
        row = s.scalar(select(Fundamental).where(Fundamental.symbol == symbol, Fundamental.period_end == f.period_end,
                                                 Fundamental.period_type == f.period_type)) or Fundamental(symbol=symbol)
        for k, v in f.model_dump().items():
            setattr(row, k, v)
        row.source = "manual"
        s.add(row)
    with SessionLocal.begin() as s:
        eng.analyze_symbol(s, symbol)
    return {"ok": True}


@app.post("/api/stocks/{symbol}/fundamentals/{fid}/confirm")
def confirm_fundamental(symbol: str, fid: int) -> dict[str, Any]:
    """Accept an auto-extracted row after checking it against the PDF: it is then used in the scores."""
    with SessionLocal.begin() as s:
        row = s.get(Fundamental, fid)
        if not row or row.symbol != symbol:
            raise HTTPException(404, "not found")
        row.needs_review = False
        row.detail = {**(row.detail or {}), "confirmed": True}
    with SessionLocal.begin() as s:
        eng.analyze_symbol(s, symbol)
    return {"ok": True}


@app.post("/api/stocks/{symbol}/fundamentals/extract")
def extract_fundamentals(symbol: str) -> dict[str, Any]:
    """Re-read the newest quarterly statements (downloads each PDF again)."""
    with SessionLocal.begin() as s:
        if not s.get(Security, symbol):
            raise HTTPException(404, "unknown symbol")
        ingest.sync_reports(s, symbol)
        s.flush()
        got = ingest.sync_report_figures(s, symbol, force=True)
    with SessionLocal.begin() as s:
        eng.analyze_symbol(s, symbol)
    return {"ok": True, **got}


@app.delete("/api/stocks/{symbol}/fundamentals/{fid}")
def del_fundamental(symbol: str, fid: int) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        row = s.get(Fundamental, fid)
        if row and row.symbol == symbol:
            s.delete(row)
    return {"ok": True}


@app.get("/api/stocks/{symbol}/catalysts")
def catalysts_view(symbol: str) -> dict[str, Any]:
    """News, results and events for one stock, with the scorecard (evidence beside the engines)."""
    from . import catalysts, news_feeds
    with SessionLocal() as s:
        if not s.get(Security, symbol):
            raise HTTPException(404, "unknown symbol")
        news_feeds.refresh_async(ingest.watchlist_symbols(s))
        adj, _ = eng.load_adjusted(s, symbol)
        md = eng.load_market(s)
        c = catalysts.build(s, symbol, adj["close"] if not adj.empty else adj, md["aspi"] if "aspi" in md else None)
    return eng._jsonable(c)


@app.get("/api/market/headlines")
def market_headlines(limit: int = 40) -> list[dict[str, Any]]:
    from . import news_feeds
    from .db import Headline
    with SessionLocal() as s:
        news_feeds.refresh_async(ingest.watchlist_symbols(s))
        rows = s.scalars(select(Headline).order_by(Headline.published.desc()).limit(limit)).all()
        return eng._jsonable([{"source": h.source, "title": h.title, "link": h.link, "published": h.published, "symbols": h.symbols or [],
                               "tone": h.tone} for h in rows])


@app.get("/api/stocks/{symbol}/announcements")
def announcements(symbol: str, limit: int = 40) -> list[dict[str, Any]]:
    with SessionLocal() as s:
        rows = s.scalars(select(Announcement).where(Announcement.symbol == symbol).order_by(Announcement.date.desc()).limit(limit)).all()
        return [{"id": a.id, "date": a.date.isoformat() if a.date else None, "title": a.title, "body": a.body,
                 "category": a.category, "sentiment": a.sentiment, "materiality": a.materiality} for a in rows]


# ============================================================================ live quotes (SSE)
@app.get("/api/live")
async def live_stream(request: Request, symbols: str = "") -> StreamingResponse:
    wanted = {x for x in symbols.split(",") if x}

    async def gen():
        live.subscribers += 1
        try:
            last_v = -1
            while not await request.is_disconnected():
                if live.version != last_v and live.quotes:
                    last_v = live.version
                    data = {k: v for k, v in live.quotes.items() if not wanted or k in wanted}
                    payload = {"open": market_open(cached_only=True), "updated": live.updated.isoformat() if live.updated else None, "quotes": data}
                    yield f"data: {json.dumps(payload)}\n\n"
                if not await live.wait_update(20):
                    yield ": keepalive\n\n"
        finally:
            live.subscribers -= 1

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ============================================================================ market, positions, prefs, jobs
@app.get("/api/market")
def market() -> dict[str, Any]:
    from .analysis.market import market_regime
    with SessionLocal() as s:
        rows = s.scalars(select(MarketDaily).order_by(MarketDaily.date)).all()
        md = eng.load_market(s)
        from .db import SectorDaily
        last_d = s.scalar(select(SectorDaily.date).order_by(SectorDaily.date.desc()))
        sectors = s.scalars(select(SectorDaily).where(SectorDaily.date == last_d).order_by(SectorDaily.change_pct.desc())).all() if last_d else []
        mine: dict[str, list[str]] = {}
        wl = set(ingest.watchlist_symbols(s))
        for sec in s.scalars(select(Security).where(Security.sector.is_not(None))):
            if sec.symbol in wl:
                mine.setdefault(sec.sector, []).append(sec.symbol)
    return eng._jsonable({"regime": market_regime(md), "open": market_open(),
                          "series": [{"time": r.date.isoformat(), "aspi": r.aspi, "spsl20": r.spsl20, "turnover": r.turnover,
                                      "advancers": r.advancers, "decliners": r.decliners, "foreign_buy": r.foreign_buy,
                                      "foreign_sell": r.foreign_sell, "foreign_trades": r.foreign_trades,
                                      "per": r.per, "pbv": r.pbv, "dy": r.dy, "market_cap": r.market_cap} for r in rows],
                          "sectors": {"date": last_d.isoformat() if last_d else None,
                                      "rows": [{"code": x.code, "name": x.name, "index_name": x.index_name, "value": x.value, "change": x.change,
                                                "change_pct": x.change_pct, "turnover": x.turnover, "trades": x.trades,
                                                "watchlist": mine.get(x.code, [])}
                                               for x in sectors]},
                          "movers": live.refresh_movers()})


@app.get("/api/regime")
def regime() -> dict[str, Any]:
    """Market banner: ASPI vs its 100 / 200-day averages and breadth (from the SCR ranking). Description only."""
    from .analysis.market import regime_banner
    with SessionLocal() as s:
        md = eng.load_market(s)
    run = screener.latest()
    return eng._jsonable(regime_banner(md, (run or {}).get("meta", {}).get("breadth_above50")))


@app.get("/api/screener")
def screener_view() -> dict[str, Any]:
    return eng._jsonable({"run": screener.latest(), "progress": dict(screener.progress), "enabled": screener.cfg().get("enabled", True)})


@app.post("/api/screener/run")
def screener_run(full: bool = False) -> dict[str, Any]:
    if screener.progress["running"]:
        return {"started": False, "message": "already running"}
    threading.Thread(target=screener.run, kwargs={"force_universe": full}, daemon=True, name="screener").start()
    return {"started": True}


@app.get("/api/journal")
def journal_view() -> dict[str, Any]:
    with SessionLocal() as s:
        return eng._jsonable(journal.summary(s))


class PositionIn(BaseModel):
    symbol: str
    entry_date: date
    entry_price: float
    quantity: int
    stop: float | None = None
    target: float | None = None
    notes: str | None = None


class PositionClose(BaseModel):
    exit_date: date
    exit_price: float


@app.get("/api/positions")
def positions() -> list[dict[str, Any]]:
    return _positions()


@app.get("/api/portfolio")
def portfolio_view() -> dict[str, Any]:
    """Totals for the Positions page: P/L after fees, risk to stops vs capital, concentration."""
    from . import portfolio
    rows = _positions()
    with SessionLocal() as s:
        sectors = {x.symbol: x.sector for x in s.scalars(select(Security))}
    for p in rows:
        p["sector"] = sectors.get(p["symbol"])
        p["stop_used"] = p.get("stop") or ((p.get("review") or {}).get("suggested_stop"))
    return eng._jsonable(portfolio.summarize(rows, settings()["risk"].get("capital_lkr"), settings()["costs"]["brokerage_pct_per_side"]))


@app.post("/api/telegram/test")
def telegram_test() -> dict[str, Any]:
    if not telegram_bot.enabled():
        raise HTTPException(400, "Telegram isn't set up: add TELEGRAM_TOKEN and TELEGRAM_CHAT_ID to backend/.env (see the README), then restart.")
    telegram_bot.send_text("✅ CSE Advisor test message: alerts and the evening digest will arrive here.")
    return {"ok": True}


def _exit_warnings(s, symbol: str, a: Analysis | None) -> list[dict[str, Any]]:
    """'Consider trimming' notes for a held stock (information only; see portfolio.exit_warnings)."""
    try:
        adj, _ = eng.load_adjusted(s, symbol)
        cl = adj["close"]
        te = portfolio.trend_exit(cl.to_numpy()[-2:], cl.ewm(span=50, adjust=False, min_periods=50).mean().to_numpy()[-2:])
        return portfolio.exit_warnings(a.patterns if a else [], te)
    except Exception as exc:  # noqa: BLE001
        log.warning("exit warnings %s: %s", symbol, exc)
        return []


def _positions() -> list[dict[str, Any]]:
    with SessionLocal() as s:
        out = []
        for p in s.scalars(select(Position).order_by(Position.closed, Position.entry_date.desc())):
            a = _latest(s, p.symbol)
            q = live.quotes.get(p.symbol)
            ro, ra = split_engines(a.recommendation if a else None)
            price = q["close"] if q else (ro.get("close") if a else None)
            out.append({**{c.name: getattr(p, c.name) for c in p.__table__.columns},
                        "price": price, "review": ro.get("position_review") if a else None,
                        "review_analyst": (ra or {}).get("position_review"),
                        "review_smc": ((a.recommendation or {}).get("smc") or {}).get("position_review") if a else None,
                        "verdict": a.verdict if a else None, "verdict_analyst": a.verdict_analyst if a else None,
                        "verdict_smc": a.verdict_smc if a else None,
                        "warnings": [] if p.closed else _exit_warnings(s, p.symbol, a)})
        return eng._jsonable(out)


@app.post("/api/positions")
def add_position(p: PositionIn) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        if not s.get(Security, p.symbol):
            raise HTTPException(400, "Add the stock to the watchlist first")
        s.add(Position(**p.model_dump()))
    with SessionLocal.begin() as s:
        eng.analyze_symbol(s, p.symbol)
    return {"ok": True}


@app.post("/api/positions/{pid}/close")
def close_position(pid: int, c: PositionClose) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        p = s.get(Position, pid)
        if not p:
            raise HTTPException(404, "not found")
        p.closed, p.exit_date, p.exit_price = True, c.exit_date, c.exit_price
        sym = p.symbol
    with SessionLocal.begin() as s:
        eng.analyze_symbol(s, sym)
    return {"ok": True}


@app.delete("/api/positions/{pid}")
def delete_position(pid: int) -> dict[str, Any]:
    sym = None
    with SessionLocal.begin() as s:
        p = s.get(Position, pid)
        if p:
            sym = p.symbol
            s.delete(p)
    if sym:   # drop the position review from the stock's analysis now, not at the next evening run
        with SessionLocal.begin() as s:
            eng.analyze_symbol(s, sym)
    return {"ok": True}


@app.get("/api/catalog")
def catalog() -> dict[str, Any]:
    return {"patterns": pattern_catalog(), "default_display": settings()["patterns"]["default_display"]}


@app.get("/api/prefs/{key}")
def get_pref(key: str) -> Any:
    with SessionLocal() as s:
        p = s.get(Preference, key)
        return p.value if p else None


@app.put("/api/prefs/{key}")
async def put_pref(key: str, request: Request) -> dict[str, Any]:
    value = await request.json()
    with SessionLocal.begin() as s:
        p = s.get(Preference, key) or Preference(key=key)
        p.value = value
        s.add(p)
    return {"ok": True}


@app.post("/api/jobs/eod")
def trigger_eod() -> dict[str, Any]:
    threading.Thread(target=jobs.run_eod, kwargs={"notify": True}, daemon=True).start()
    return {"started": True}


@app.get("/api/status")
def status() -> dict[str, Any]:
    with SessionLocal() as s:
        runs = s.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(5)).all()
    return {"market_open": market_open(), "telegram": telegram_bot.enabled(),
            "live_updated": live.updated.isoformat() if live.updated else None,
            "jobs": [{"job": r.job, "date": r.run_date.isoformat(), "ok": r.ok, "at": r.started_at.isoformat(), "message": r.message} for r in runs],
            "settings": {"capital": settings()["risk"]["capital_lkr"], "risk_pct": settings()["risk"]["risk_per_trade_pct"],
                         "fees_pct": settings()["costs"]["brokerage_pct_per_side"], "weights": settings()["weights"]}}


# ============================================================================ frontend
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = FRONTEND_DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(FRONTEND_DIST / "index.html")


def run() -> None:
    import uvicorn
    from .config import env
    # The app has no login, so it listens on this PC only by default. Set HOST=0.0.0.0 in
    # backend/.env to reach it from your phone on home Wi-Fi (or use Tailscale instead).
    uvicorn.run("cse_advisor.main:app", host=env("HOST", "127.0.0.1"), port=int(env("PORT", "8000")), reload=False)


if __name__ == "__main__":
    run()
