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
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func, select

from .strategy.recommend import plan_summary, split_engines
from . import engine as eng
from . import accounts, alerts_api, auth, eqs, globalstocks_api, market_api, position_ops, quant_api, ingest, jobs, journal, portfolio, screener, telegram_bot
from .auth import User, current_user, require_admin
from .config import FRONTEND_DIST, env, pattern_catalog, settings
from .cse_client import CDN
from .db import (
    Analysis, Announcement, CorporateAction, FinancialReport, Fundamental, JobRun, MarketDaily, Note, Position, Preference, PriceDaily,
    SessionLocal, Security, WatchlistItem, init_db,
)
from .db import LOCAL_UID
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
                  misfire_grace_time=24 * 3600, coalesce=True)
    # the watchdog: a PC that slept through the run (or an app that was off) catches up as soon as it is awake. No misfire
    # limit, so after a sleep it fires at once instead of being dropped as late.
    sched.add_job(jobs.ensure_fresh, IntervalTrigger(minutes=jobs.CHECK_MINUTES, timezone=tz), id="eod_watchdog", coalesce=True,
                  max_instances=1, misfire_grace_time=None)
    # world stocks (GLB): after the US close (about 02:30 Colombo), and caught up hourly if the PC slept through it
    from . import globalstocks
    gh, gm = map(int, str(globalstocks.cfg().get("job_time", "07:00")).split(":"))
    sched.add_job(globalstocks.run_all, CronTrigger(hour=gh, minute=gm, timezone=tz), id="world", misfire_grace_time=6 * 3600, coalesce=True)
    sched.add_job(globalstocks.catch_up, IntervalTrigger(minutes=60, timezone=tz), id="world_watchdog", coalesce=True, max_instances=1,
                  misfire_grace_time=None)
    sched.start()
    threading.Thread(target=jobs.startup_catch_up, daemon=True, name="catch-up").start()
    globalstocks.catch_up_later()
    tg_stop = telegram_bot.start_polling()
    task = asyncio.create_task(live.run())
    log.info("CSE Advisor ready; EOD job daily at %s %s", settings()["market"]["eod_job_time"], tz)
    yield
    task.cancel()
    tg_stop.set()
    sched.shutdown(wait=False)


app = FastAPI(title="CSE Advisor", lifespan=lifespan)
app.include_router(alerts_api.router)         # alerts, inbox, notes
app.include_router(market_api.router)         # heatmap, news feed, calendar, key stats, quote card
app.include_router(globalstocks_api.router)   # world stocks (GLB)
app.include_router(quant_api.router)          # quant figures (QNT, gs-quant maths)

PUBLIC_API = {"/api/auth/config"}


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    """Every /api call needs a valid Firebase ID token when sign-in is on (except the public sign-in config). Checked
    here once for all routes, so no route can forget it; the routes read the caller from Depends(current_user)."""
    path = request.url.path
    if path.startswith("/api/") and path not in PUBLIC_API:
        try:
            request.state.user = await asyncio.to_thread(current_user, request)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                headers={"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None)
    response = await call_next(request)
    if not path.startswith("/__/"):          # Firebase's own sign-in helper pages keep Firebase's headers
        for k, v in security_headers().items():
            response.headers.setdefault(k, v)
    return response


def security_headers() -> dict[str, str]:
    """Headers that don't break sign-in: framing only by our own pages (the auth iframe), no COOP (it breaks the
    Google pop-up), and a CSP listing exactly what Firebase sign-in loads."""
    pid = auth.project_id()
    fb = f" https://{pid}.firebaseapp.com" if pid else ""
    emu = f" http://{auth.emulator_host()}" if auth.emulator_host() else ""     # local testing only (demo-* projects)
    csp = ("default-src 'self'; script-src 'self' https://apis.google.com; style-src 'self' 'unsafe-inline'; "
           "img-src 'self' data: blob: https:; font-src 'self' data:; "
           f"connect-src 'self' https://*.googleapis.com https://apis.google.com{emu}; "
           f"frame-src 'self'{fb} https://apis.google.com https://accounts.google.com{emu}; "
           "frame-ancestors 'self'; object-src 'none'; base-uri 'self'; form-action 'self'")
    return {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "SAMEORIGIN", "Content-Security-Policy": csp,
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()"}


def _latest(s, symbol: str) -> Analysis | None:
    return s.scalar(select(Analysis).where(Analysis.symbol == symbol).order_by(Analysis.date.desc()))


def _require_tracked(s, u: User, symbol: str) -> None:
    """Refreshing a stock's data is for stocks you watch or hold (or the admin): it costs requests to cse.lk / TradingView."""
    if not u.admin and symbol not in accounts.user_symbols(s, u.uid):
        raise HTTPException(403, "Add the stock to your watchlist first.")


def _cap(name: str, default: int) -> int:
    return int((settings().get("accounts") or {}).get(name, default))


# ============================================================================ account
class ProfileIn(BaseModel):
    first_name: str = ""
    last_name: str = ""


@app.get("/api/auth/config")
def auth_config() -> dict[str, Any]:
    """Public: whether sign-in is on, and the Firebase web config (public values that identify the project)."""
    return auth.public_config()


def _me(s, u: User, row) -> dict[str, Any]:
    out = accounts.profile_dict(row, u) | {"auth": auth.enabled()}
    if u.admin:
        out["local_data"] = accounts.local_data_counts(s) if u.uid != LOCAL_UID else {}
        out["telegram_owner"] = accounts.telegram_owner(s) == u.uid
    return out


@app.get("/api/me")
def me(u: User = Depends(current_user)) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        return _me(s, u, accounts.ensure_profile(s, u))


@app.post("/api/me")
def me_signed_in(body: ProfileIn, u: User = Depends(current_user)) -> dict[str, Any]:
    """Called once after each sign-in with the name typed at sign-up (if any): creates the profile on the first sign-in
    and only ever fills empty fields."""
    with SessionLocal.begin() as s:
        return _me(s, u, accounts.ensure_profile(s, u, body.first_name, body.last_name))


@app.put("/api/me")
def me_rename(body: ProfileIn, u: User = Depends(current_user)) -> dict[str, Any]:
    if not body.first_name.strip():
        raise HTTPException(400, "Enter your first name.")
    with SessionLocal.begin() as s:
        return _me(s, u, accounts.set_name(s, u, body.first_name, body.last_name))


@app.delete("/api/me")
def me_delete(u: User = Depends(current_user)) -> dict[str, Any]:
    """Delete this account's data (watchlist, positions, preferences, profile). The browser then deletes the Firebase
    account itself, after re-authenticating."""
    if u.uid == LOCAL_UID:
        raise HTTPException(400, "Sign-in is off: there is no account to delete.")
    with SessionLocal.begin() as s:
        return {"ok": True, "deleted": accounts.delete_account_data(s, u.uid)}


@app.post("/api/me/claim-local")
def me_claim_local(u: User = Depends(require_admin)) -> dict[str, Any]:
    """Move the data made before sign-in was switched on (watchlist, positions, preferences) into this account."""
    with SessionLocal.begin() as s:
        return {"ok": True, "moved": accounts.claim_local(s, u.uid)}


@app.post("/api/me/telegram-owner")
def me_telegram_owner(u: User = Depends(require_admin)) -> dict[str, Any]:
    """Make the Telegram digest and alerts report on this account's watchlist and positions."""
    with SessionLocal.begin() as s:
        accounts.set_telegram_owner(s, u.uid)
    return {"ok": True}


# ============================================================================ watchlist
class AddReq(BaseModel):
    query: str
    list_id: int | None = None          # the list to put it on (the first list when omitted)


class ListIn(BaseModel):
    name: str


def _watch_row(s, uid: str, symbol: str) -> dict[str, Any]:
    sec = s.get(Security, symbol)
    a = accounts.view(s, uid, _latest(s, symbol))
    closes = s.scalars(select(PriceDaily.close).where(PriceDaily.symbol == symbol).order_by(PriceDaily.date.desc()).limit(60)).all()[::-1]
    q = live.quotes.get(symbol)
    ro, ra = split_engines(a.recommendation if a else None)
    rs = (a.recommendation or {}).get("smc") if a else None
    agree = (a.recommendation or {}).get("agreement") if a else None
    last = q["close"] if q else (closes[-1] if closes else None)
    top = next((p for p in (a.patterns if a else []) if p["status"] in ("confirmed", "forming") and p.get("category") != "level"), None)
    return {
        "symbol": symbol, "name": sec.name if sec else symbol, "sector": sec.sector if sec else None,
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
    }


@app.get("/api/watchlist")
def get_watchlist(list_: str | None = Query(None, alias="list"), u: User = Depends(current_user)) -> list[dict[str, Any]]:
    """The caller's stocks with the engines' verdicts. `list` = a list id, or "portfolio" (stocks with an open position);
    without it, every stock the account tracks."""
    with SessionLocal.begin() as s:                 # begin(): the first call makes "Watchlist 1" for a new account
        try:
            if list_ is None:
                syms = accounts.watchlist_symbols(s, u.uid)
            elif list_ == "portfolio":
                syms = accounts.held_symbols(s, u.uid)
            else:
                syms = accounts.group_symbols(s, u.uid, int(list_))
        except accounts.ListError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, "list must be a list id or 'portfolio'") from exc
        return [_watch_row(s, u.uid, sym) for sym in syms]


@app.post("/api/watchlist")
def add_watchlist(req: AddReq, u: User = Depends(current_user)) -> dict[str, Any]:
    try:
        sym = ingest.add_to_watchlist(req.query, u.uid, None if u.admin else _cap("max_watchlist", 30), req.list_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    with SessionLocal.begin() as s:
        r = eng.analyze_symbol(s, sym)
    return {"symbol": sym, "analysed": r is not None}


@app.delete("/api/watchlist/{symbol}")
def del_watchlist(symbol: str, list_: int | None = Query(None, alias="list"), u: User = Depends(current_user)) -> dict[str, Any]:
    """Take a stock off one list (`list`), or off every list. Off its last list it stops being tracked."""
    try:
        untracked = ingest.remove_from_watchlist(symbol, u.uid, list_)
    except accounts.ListError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"removed": symbol, "untracked": untracked}


# ---------------------------------------------------------------- named lists (home page tabs)
@app.get("/api/lists")
def get_lists(u: User = Depends(current_user)) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        return {"lists": accounts.lists_overview(s, u.uid), "portfolio": len(accounts.held_symbols(s, u.uid)),
                "max_lists": 50 if u.admin else _cap("max_lists", 10)}


@app.post("/api/lists")
def create_list(body: ListIn, u: User = Depends(current_user)) -> dict[str, Any]:
    try:
        with SessionLocal.begin() as s:
            g = accounts.create_group(s, u.uid, body.name, 50 if u.admin else _cap("max_lists", 10))
            return {"id": g.id, "name": g.name}
    except accounts.ListError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.put("/api/lists/{gid}")
def rename_list(gid: int, body: ListIn, u: User = Depends(current_user)) -> dict[str, Any]:
    try:
        with SessionLocal.begin() as s:
            g = accounts.rename_group(s, u.uid, gid, body.name)
            return {"id": g.id, "name": g.name}
    except accounts.ListError as exc:
        raise HTTPException(404 if "not found" in str(exc) else 400, str(exc)) from exc


@app.delete("/api/lists/{gid}")
def delete_list(gid: int, u: User = Depends(current_user)) -> dict[str, Any]:
    """Delete a list. Stocks that were only on it stop being tracked."""
    try:
        with SessionLocal.begin() as s:
            return {"ok": True, "untracked": accounts.delete_group(s, u.uid, gid)}
    except accounts.ListError as exc:
        raise HTTPException(404 if "not found" in str(exc) else 400, str(exc)) from exc


# ---------------------------------------------------------------- every listed stock (the "All stocks" tab)
_ALL: dict[str, Any] = {"at": 0.0, "rows": []}


def _all_listed() -> list[dict[str, Any]]:
    """Every security in CSE's latest trade summary (one request, cached for a minute)."""
    import time as _t
    from .cse_client import client, ms_to_date
    if _ALL["rows"] and _t.monotonic() - _ALL["at"] < 60:
        return _ALL["rows"]
    rows = []
    for r in client().trade_summary():
        sym = r.get("symbol")
        if not sym:
            continue
        price = r.get("price") or r.get("closingPrice") or None
        rows.append({"symbol": sym, "name": r.get("name") or sym, "price": price, "change_pct": r.get("percentageChange") if price else None,
                     "volume": r.get("sharevolume"), "turnover": r.get("turnover"), "trades": r.get("tradevolume"),
                     "market_cap": r.get("marketCap"),
                     "traded": str(ms_to_date(r["lastTradedTime"])) if r.get("lastTradedTime") else None})
    _ALL["rows"], _ALL["at"] = rows, _t.monotonic()
    return rows


def _stock_rows(u: User) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Every listed security with its latest price and where it sits for this account; each row also carries `scr` (its row in the
    latest candidate ranking, if it is liquid enough to be in it) and `quant` (its one-year risk figures, when known)."""
    try:
        listed = _all_listed()
    except Exception as exc:  # noqa: BLE001 - the CSE endpoints are unofficial
        log.warning("all stocks: %s", exc)
        raise HTTPException(502, "Couldn't reach the CSE for the list of stocks. Try again in a minute.") from exc
    run = screener.latest(u.uid)
    cand = {r["symbol"]: r for r in (run or {}).get("rows", [])}
    with SessionLocal.begin() as s:
        lists = accounts.symbol_lists(s, u.uid)
        held = set(accounts.held_symbols(s, u.uid))
        overview = accounts.lists_overview(s, u.uid)
    with SessionLocal() as s:
        from . import listed_meta
        kinds = listed_meta.lookup(s)
    from .quant import columns as qcols
    with SessionLocal() as s:
        stored = qcols.for_symbols(s, list(ingest.watchlist_symbols(s)))      # the tracked stocks, from stored prices (exactly the QNT figures)
    quant = {k: r["quant"] for k, r in cand.items() if r.get("quant")}        # the liquid ones, from the last candidate run
    quant.update(stored)
    rows = [{**r, "quant": quant.get(r["symbol"]), "sector": kinds.get(r["symbol"], {}).get("sector"), "industry": kinds.get(r["symbol"], {}).get("industry"),
             "lists": lists.get(r["symbol"], []), "held": r["symbol"] in held, "mine": bool(lists.get(r["symbol"])) or r["symbol"] in held, "scr": cand.get(r["symbol"]),
             "candidate": ({"qualifies": cand[r["symbol"]]["qualifies"], "hits": cand[r["symbol"]]["hits"], "rs_rank": cand[r["symbol"]]["rs_rank"]}
                           if r["symbol"] in cand else None)} for r in listed]
    return rows, overview


@app.get("/api/stocks/all")
def all_stocks(u: User = Depends(current_user)) -> dict[str, Any]:
    """Every listed security with its latest price, and where it sits for this account: on which lists, held, and whether it
    meets the candidate rule (SCR)."""
    rows, overview = _stock_rows(u)
    return {"lists": overview, "count": len(rows), "rows": [{k: v for k, v in r.items() if k not in ("scr", "mine")} for r in rows]}


@app.get("/api/eqs/catalog")
def eqs_catalog(_: User = Depends(current_user)) -> dict[str, Any]:
    return eqs.catalog()


@app.post("/api/eqs/run")
def eqs_run(body: dict[str, Any], u: User = Depends(current_user)) -> dict[str, Any]:
    """Your own screen over every listed stock (see eqs.py)."""
    rows, _ = _stock_rows(u)
    try:
        return eng._jsonable(eqs.run(rows, body))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


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
def add_corporate_action(symbol: str, a: CorporateActionIn, _: User = Depends(require_admin)) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        row = CorporateAction(symbol=symbol, manual=True, **a.model_dump())
        s.add(row)
        s.flush()
        rid = row.id
    _reanalyze_quiet(symbol)
    return {"ok": True, "id": rid}


@app.put("/api/stocks/{symbol}/corporate-actions/{aid}")
def edit_corporate_action(symbol: str, aid: int, a: CorporateActionIn, _: User = Depends(require_admin)) -> dict[str, Any]:
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
def delete_corporate_action(symbol: str, aid: int, _: User = Depends(require_admin)) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        row = s.get(CorporateAction, aid)
        if row and row.symbol == symbol:
            s.delete(row)
    _reanalyze_quiet(symbol)
    return {"ok": True}


@app.post("/api/stocks/{symbol}/import")
def import_history(symbol: str, body: ImportIn, _: User = Depends(require_admin)) -> dict[str, Any]:
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
def analysis(symbol: str, tf: str = "1D", u: User = Depends(current_user)) -> dict[str, Any]:
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
        a = accounts.view(s, u.uid, _latest(s, symbol))
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
def live_check(symbol: str, u: User = Depends(current_user)) -> dict[str, Any]:
    """The live price against the stored daily analysis' levels: which patterns and plans the session has already
    invalidated, hit or overtaken. {} when there is no live quote from a session after the analysis."""
    from .live_status import live_overlay
    with SessionLocal() as s:
        a = accounts.view(s, u.uid, _latest(s, symbol))
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
def set_sector(symbol: str, body: SectorIn, _: User = Depends(require_admin)) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        sec = s.get(Security, symbol)
        if not sec:
            raise HTTPException(404, "unknown symbol")
        sec.sector = (body.sector or "").strip().upper() or None
    return {"ok": True, "sector": body.sector}


@app.post("/api/stocks/{symbol}/intraday/backfill")
def intraday_backfill(symbol: str, u: User = Depends(current_user)) -> dict[str, Any]:
    from . import intraday
    with SessionLocal.begin() as s:
        sec = s.get(Security, symbol)
        if not sec:
            raise HTTPException(404, "unknown symbol")
        _require_tracked(s, u, symbol)
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
def reanalyze(symbol: str, u: User = Depends(current_user)) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        _require_tracked(s, u, symbol)
        r = eng.analyze_symbol(s, symbol)
        if not r:
            raise HTTPException(400, "Not enough data to analyse")
        s.flush()
        v = accounts.latest_view(s, u.uid, symbol)
    return {"ok": True, **{k: getattr(v, k) for k in accounts.VERDICT_FIELDS}}


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
def add_fundamentals(symbol: str, f: FundamentalIn, _: User = Depends(require_admin)) -> dict[str, Any]:
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
def confirm_fundamental(symbol: str, fid: int, _: User = Depends(require_admin)) -> dict[str, Any]:
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
def extract_fundamentals(symbol: str, _: User = Depends(require_admin)) -> dict[str, Any]:
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
def del_fundamental(symbol: str, fid: int, _: User = Depends(require_admin)) -> dict[str, Any]:
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
def market_headlines(limit: int = 40, u: User = Depends(current_user)) -> list[dict[str, Any]]:
    from . import news_feeds
    from .db import Headline
    with SessionLocal() as s:
        news_feeds.refresh_async(ingest.watchlist_symbols(s))
        mine = accounts.user_symbols(s, u.uid)
        rows = s.scalars(select(Headline).where(Headline.kind.is_(None) | Headline.kind.in_(("local", "company")))
                         .order_by(Headline.published.desc()).limit(limit)).all()
        return eng._jsonable([_headline_row(h, mine) for h in rows])


def _headline_row(h, mine: set[str]) -> dict[str, Any]:
    llm = h.llm or {}
    about = [c["symbol"] for c in llm.get("companies") or [] if c.get("about") == "primary"] if llm.get("by") else (h.symbols or [])
    return {"source": h.outlet or h.source, "title": h.title, "link": h.link, "published": h.published, "kind": h.kind or "local",
            "symbols": [x for x in about if x in mine], "tone": h.tone, "read_by": "Claude" if llm.get("by") else "keywords",
            "category": llm.get("category"), "theme": llm.get("macro_theme"), "sl_market_tone": llm.get("sl_market_tone"),
            "reason": llm.get("reason")}


@app.get("/api/stocks/{symbol}/announcements")
def announcements(symbol: str, limit: int = 40) -> list[dict[str, Any]]:
    with SessionLocal() as s:
        rows = s.scalars(select(Announcement).where(Announcement.symbol == symbol).order_by(Announcement.date.desc()).limit(limit)).all()
        return [{"id": a.id, "date": a.date.isoformat() if a.date else None, "title": a.title, "body": a.body,
                 "category": a.category, "sentiment": a.sentiment, "materiality": a.materiality} for a in rows]


# ============================================================================ live quotes (SSE)
@app.get("/api/live")
async def live_stream(request: Request, symbols: str = "", _: User = Depends(current_user)) -> StreamingResponse:
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
def market(u: User = Depends(current_user)) -> dict[str, Any]:
    from .analysis.market import market_regime
    with SessionLocal() as s:
        rows = s.scalars(select(MarketDaily).order_by(MarketDaily.date)).all()
        md = eng.load_market(s)
        from .db import SectorDaily
        last_d = s.scalar(select(SectorDaily.date).order_by(SectorDaily.date.desc()))
        sectors = s.scalars(select(SectorDaily).where(SectorDaily.date == last_d).order_by(SectorDaily.change_pct.desc())).all() if last_d else []
        mine: dict[str, list[str]] = {}
        wl = set(ingest.watchlist_symbols(s, u.uid))
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
    from . import macro
    from .analysis.market import regime_banner
    with SessionLocal() as s:
        md = eng.load_market(s)
        try:
            overnight = macro.overnight_note(s)
        except Exception as exc:  # noqa: BLE001 - the banner must load without world data
            log.warning("overnight note: %s", exc)
            overnight = None
    run = screener.latest()
    return eng._jsonable({**regime_banner(md, (run or {}).get("meta", {}).get("breadth_above50")), "overnight": overnight})


@app.get("/api/world")
def world(u: User = Depends(current_user)) -> dict[str, Any]:
    """World series with their measured link to the ASPI, the overnight note, world / economy headlines, the state of
    each news source, and the headline reader's usage. Description only: no score uses any of it."""
    from . import macro, news_feeds, news_llm
    from .db import Headline, Preference
    lk = macro.links()
    with SessionLocal() as s:
        snap = macro.snapshot(s, spark=60)
        rows = s.scalars(select(Headline).where(Headline.kind.in_(("global", "macro"))).order_by(Headline.published.desc()).limit(30)).all()
        seen, news = set(), []
        for h in rows:                                   # the same story from several outlets once
            if (h.cluster or h.id) in seen:
                continue
            seen.add(h.cluster or h.id)
            news.append(_headline_row(h, set()) | {"theme": (h.llm or {}).get("macro_theme") or news_feeds.macro_theme(h.title)})
        usage = s.get(Preference, news_llm.USAGE_KEY)
        series = [{"code": k, **v, "link": (lk.get("series") or {}).get(k, {}).get("verdict_text"),
                   "verdict": (lk.get("series") or {}).get(k, {}).get("verdict")} for k, v in snap.items()]
        return eng._jsonable({"series": series, "overnight": macro.overnight_note(s, snap, lk), "news": news[:20],
                              "study": {"date": lk.get("date"), "period": lk.get("period"), "events": lk.get("events")},
                              "feeds": news_feeds.feed_status(s), "reader": {"enabled": bool(news_llm.cfg()["enabled"]), "model": news_llm.cfg()["model"],
                                                                              "usage": usage.value if usage else None},
                              "news_tone": macro.news_tone(s)})


@app.get("/api/screener")
def screener_view(u: User = Depends(current_user)) -> dict[str, Any]:
    return eng._jsonable({"run": screener.latest(u.uid), "progress": dict(screener.progress), "enabled": screener.cfg().get("enabled", True)})


@app.post("/api/screener/run")
def screener_run(full: bool = False, _: User = Depends(require_admin)) -> dict[str, Any]:
    if screener.progress["running"]:
        return {"started": False, "message": "already running"}
    threading.Thread(target=screener.run, kwargs={"force_universe": full}, daemon=True, name="screener").start()
    return {"started": True}


@app.get("/api/journal")
def journal_view(u: User = Depends(current_user)) -> dict[str, Any]:
    """The calls the engines made on the caller's stocks (watchlist and holdings)."""
    with SessionLocal() as s:
        return eng._jsonable(journal.summary(s, symbols=accounts.user_symbols(s, u.uid)))


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
    quantity: int | None = None        # fewer than you hold = a partial sell (the rest stays open)
    review: str | None = None          # optional "what went right / wrong", saved as a note tagged review


class PositionEdit(BaseModel):
    """Fields left out are unchanged; stop / target / notes can be set to null to clear them."""
    entry_date: date | None = None
    entry_price: float | None = None
    quantity: int | None = None
    stop: float | None = None
    target: float | None = None
    notes: str | None = None
    exit_date: date | None = None      # closed positions only
    exit_price: float | None = None


@app.get("/api/positions")
def positions(u: User = Depends(current_user)) -> list[dict[str, Any]]:
    return _positions(u.uid)


@app.get("/api/portfolio")
def portfolio_view(u: User = Depends(current_user)) -> dict[str, Any]:
    """Totals for the Positions page: P/L after fees, risk to stops vs capital, concentration."""
    from . import portfolio
    rows = _positions(u.uid)
    with SessionLocal() as s:
        sectors = {x.symbol: x.sector for x in s.scalars(select(Security))}
        from . import listed_meta
        kinds = listed_meta.lookup(s)
    for p in rows:
        p["sector"] = sectors.get(p["symbol"]) or kinds.get(p["symbol"], {}).get("sector")      # CSE index code, else TradingView's sector
        p["stop_used"] = p.get("stop") or ((p.get("review") or {}).get("suggested_stop"))
    return eng._jsonable(portfolio.summarize(rows, settings()["risk"].get("capital_lkr"), settings()["costs"]["brokerage_pct_per_side"]))


@app.post("/api/telegram/test")
def telegram_test(_: User = Depends(require_admin)) -> dict[str, Any]:
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


def _positions(uid: str) -> list[dict[str, Any]]:
    with SessionLocal() as s:
        acked = accounts.get_pref(s, uid, position_ops.ACK_KEY) or {}
        out = []
        for p in s.scalars(select(Position).where(Position.user_id == uid).order_by(Position.closed, Position.entry_date.desc())):
            a = accounts.view(s, uid, _latest(s, p.symbol))
            q = live.quotes.get(p.symbol)
            ro, ra = split_engines(a.recommendation if a else None)
            price = q["close"] if q else (ro.get("close") if a else None)
            out.append({**{c.name: getattr(p, c.name) for c in p.__table__.columns if c.name != "user_id"},
                        "price": price, "review": ro.get("position_review") if a else None,
                        "review_analyst": (ra or {}).get("position_review"),
                        "review_smc": ((a.recommendation or {}).get("smc") or {}).get("position_review") if a else None,
                        "verdict": a.verdict if a else None, "verdict_analyst": a.verdict_analyst if a else None,
                        "verdict_smc": a.verdict_smc if a else None,
                        "warnings": [] if p.closed else _exit_warnings(s, p.symbol, a),
                        "corp_since_entry": position_ops.corp_hints(s, p, acked.get(str(p.id), []))})
        return eng._jsonable(out)


@app.post("/api/positions")
def add_position(p: PositionIn, u: User = Depends(current_user)) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        if not s.get(Security, p.symbol):
            raise HTTPException(400, "Add the stock to the watchlist first")
        n = s.scalar(select(func.count()).select_from(Position).where(Position.user_id == u.uid)) or 0
        if not u.admin and n >= _cap("max_positions", 200):
            raise HTTPException(400, "You have too many positions recorded. Delete old closed ones first.")
        s.add(Position(user_id=u.uid, **p.model_dump()))
    with SessionLocal.begin() as s:
        accounts.refresh_review(s, u.uid, p.symbol)
    return {"ok": True}


def _own_position(s, u: User, pid: int) -> Position:
    p = s.get(Position, pid)
    if not p or p.user_id != u.uid:
        raise HTTPException(404, "not found")
    return p


@app.get("/api/costs")
def costs(_: User = Depends(current_user)) -> dict[str, Any]:
    """The all-in fee per side and how it splits (for the close dialog and the add form); the parts always add up to the rate."""
    return position_ops.fee_breakdown()


@app.put("/api/positions/{pid}")
def edit_position(pid: int, e: PositionEdit, u: User = Depends(current_user)) -> dict[str, Any]:
    """Correct a recorded position (open or closed). Only the fields sent change; stop / target / notes may be cleared with null."""
    sent = e.model_fields_set
    changes = {k: getattr(e, k) for k in sent}
    for k in ("entry_date", "entry_price", "quantity"):          # these can't be cleared
        if k in changes and changes[k] is None:
            del changes[k]
    with SessionLocal.begin() as s:
        p = _own_position(s, u, pid)
        try:
            position_ops.apply_edit(p, changes)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        if "quantity" in changes or "entry_price" in changes:       # you have dealt with any split / bonus note for it
            position_ops.acknowledge(s, p)
        sym = p.symbol
    with SessionLocal.begin() as s:
        accounts.refresh_review(s, u.uid, sym)
    return {"ok": True}


@app.post("/api/positions/{pid}/corp-ack")
def acknowledge_corp(pid: int, u: User = Depends(current_user)) -> dict[str, Any]:
    """Dismiss the "a split / bonus / rights issue happened after you bought" note for a position."""
    with SessionLocal.begin() as s:
        n = position_ops.acknowledge(s, _own_position(s, u, pid))
    return {"ok": True, "dismissed": n}


@app.post("/api/positions/{pid}/close")
def close_position(pid: int, c: PositionClose, u: User = Depends(current_user)) -> dict[str, Any]:
    """Sell a position, or part of it (`quantity` below what you hold: the sold shares become a closed position of their own)."""
    with SessionLocal.begin() as s:
        p = _own_position(s, u, pid)
        before = p.quantity
        try:
            done = position_ops.close_position(s, p, c.exit_date, c.exit_price, c.quantity)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        sym, remaining = p.symbol, (0 if p.closed else p.quantity)
        if c.review and c.review.strip():            # "what went right / wrong" becomes a note tagged review
            s.add(Note(user_id=u.uid, symbol=sym, position_id=done.id, body=c.review.strip()[:alerts_api.MAX_BODY], tags=["review"]))
    with SessionLocal.begin() as s:
        accounts.refresh_review(s, u.uid, sym)
    return {"ok": True, "sold": before - remaining, "remaining": remaining}


@app.delete("/api/positions/{pid}")
def delete_position(pid: int, u: User = Depends(current_user)) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        p = _own_position(s, u, pid)
        sym = p.symbol
        for n in s.scalars(select(Note).where(Note.user_id == u.uid, Note.position_id == p.id)):
            n.position_id = None                     # the notes stay, on the stock
        s.delete(p)
    with SessionLocal.begin() as s:   # drop (or redo) the position review now, not at the next evening run
        accounts.refresh_review(s, u.uid, sym)
    return {"ok": True}


@app.get("/api/catalog")
def catalog() -> dict[str, Any]:
    return {"patterns": pattern_catalog(), "default_display": settings()["patterns"]["default_display"]}


@app.get("/api/prefs/{key}")
def get_pref(key: str, u: User = Depends(current_user)) -> Any:
    with SessionLocal() as s:
        return accounts.get_pref(s, u.uid, key)


@app.put("/api/prefs/{key}")
async def put_pref(key: str, request: Request, u: User = Depends(current_user)) -> dict[str, Any]:
    if len(key) > 100:
        raise HTTPException(400, "key too long")
    body = await request.body()
    if len(body) > 200_000:
        raise HTTPException(413, "preference too large")
    value = json.loads(body or b"null")

    def save() -> None:
        with SessionLocal.begin() as s:
            accounts.put_pref(s, u.uid, key, value)
    await asyncio.to_thread(save)
    return {"ok": True}


@app.post("/api/jobs/eod")
def trigger_eod(_: User = Depends(require_admin)) -> dict[str, Any]:
    threading.Thread(target=jobs.run_eod, kwargs={"notify": True}, daemon=True).start()
    return {"started": True}


@app.get("/api/health")
def data_health(u: User = Depends(current_user)) -> dict[str, Any]:
    """Is the data current? As-of dates per source against the last finished session, the last evening run and which of its
    steps failed. Anyone signed in sees the state; only the admin sees the details."""
    from . import health
    from .cse_client import now_local
    now = now_local().replace(tzinfo=None)
    with SessionLocal() as s:
        return eng._jsonable(health.health(s, now, jobs.last_trading_day(now_local()), running=jobs._lock.locked(), admin=u.admin))


@app.get("/api/status")
def status(u: User = Depends(current_user)) -> dict[str, Any]:
    with SessionLocal() as s:
        runs = s.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(5)).all()
    # job messages list every account's stocks: only the admin sees them
    return {"market_open": market_open(), "telegram": telegram_bot.enabled(), "admin": u.admin, "auth": auth.enabled(),
            "live_updated": live.updated.isoformat() if live.updated else None,
            "jobs": [{"job": r.job, "date": r.run_date.isoformat(), "ok": r.ok, "at": r.started_at.isoformat(),
                      "message": r.message if u.admin else None} for r in runs],
            "settings": {"capital": settings()["risk"]["capital_lkr"], "risk_pct": settings()["risk"]["risk_per_trade_pct"],
                         "fees_pct": settings()["costs"]["brokerage_pct_per_side"], "weights": settings()["weights"]}}


# ============================================================================ Firebase sign-in helper
# The sign-in pop-up / redirect runs on the app's own domain (authDomain = this host) so it works in Safari and in
# home-screen apps, which block the third-party storage the default <project>.firebaseapp.com helper needs. Firebase
# Hosting serves /__/auth/* by itself; anywhere else (this server) it is proxied to the project's firebaseapp.com.
_AUTH_PATHS = {"auth/handler", "auth/handler.js", "auth/iframe", "auth/iframe.js", "auth/experiments.js", "firebase/init.json"}


@app.get("/__/{path:path}")
def firebase_helper(path: str, request: Request) -> Response:
    pid = auth.project_id()
    if not pid or path not in _AUTH_PATHS:
        raise HTTPException(404, "not found")
    import httpx
    try:
        r = httpx.get(f"https://{pid}.firebaseapp.com/__/{path}", params=dict(request.query_params), timeout=15,
                      headers={"user-agent": request.headers.get("user-agent", "cse-advisor")})
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Sign-in helper unavailable") from exc
    keep = {k: v for k, v in r.headers.items() if k.lower() in ("content-type", "cache-control", "content-security-policy",
                                                                 "x-frame-options", "cross-origin-opener-policy")}
    return Response(content=r.content, status_code=r.status_code, headers=keep)


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
    host = env("HOST", "127.0.0.1")
    # Without sign-in (no FIREBASE_PROJECT_ID) anyone who can reach the server can use and change everything, so it
    # listens on this PC only. To open it to a network, switch sign-in on (README: Accounts), or set ALLOW_NO_AUTH=1
    # for a trusted home network.
    if not auth.enabled() and host not in ("127.0.0.1", "localhost", "::1") and env("ALLOW_NO_AUTH") != "1":
        raise SystemExit(f"HOST={host} without sign-in would expose the app to the network. Set FIREBASE_PROJECT_ID "
                         "(README: Accounts) or ALLOW_NO_AUTH=1 on a trusted network.")
    if auth.enabled():
        log.info("sign-in ON (Firebase project %s); admins: %s", auth.project_id(), ", ".join(sorted(auth.admin_emails())) or "none set")
    uvicorn.run("cse_advisor.main:app", host=host, port=int(env("PORT", "8000")), reload=False,
                proxy_headers=True, forwarded_allow_ips=env("FORWARDED_ALLOW_IPS", "127.0.0.1"))


if __name__ == "__main__":
    run()
