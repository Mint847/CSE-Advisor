"""Routes for world stocks (GLB). The list and its prices are shared data, like the CSE's: anyone signed in reads them,
the admin adds and removes stocks."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from . import globalstocks as gx
from .auth import User, current_user, require_admin
from .db import GlobalHeadline, GlobalMove, GlobalPrice, GlobalStock, SessionLocal

router = APIRouter()


def _jsonable(o: Any) -> Any:
    from .engine import _jsonable as j
    return j(o)


def _own(s, symbol: str) -> GlobalStock:
    gs = s.get(GlobalStock, symbol)
    if gs is None:
        raise HTTPException(404, "Not on the world list")
    return gs


def _quote_row(q: dict[str, Any] | None) -> dict[str, Any] | None:
    if not q:
        return None
    return {"close": q.get("close"), "change_pct": q.get("change"), "change": q.get("change_abs"), "open": q.get("open"),
            "high": q.get("high"), "low": q.get("low"), "volume": q.get("volume"), "avg_volume_30d": q.get("average_volume_30d_calc"),
            "market_cap": q.get("market_cap_basic"), "currency": q.get("currency"), "sector": q.get("sector"), "industry": q.get("industry"),
            "hi_52w": q.get("price_52_week_high"), "lo_52w": q.get("price_52_week_low"), "perf_w": q.get("Perf.W"), "perf_1m": q.get("Perf.1M"),
            "perf_3m": q.get("Perf.3M"), "perf_ytd": q.get("Perf.YTD"), "perf_1y": q.get("Perf.Y"), "pe": q.get("price_earnings_ttm"),
            "div_yield": q.get("dividend_yield_recent"), "delay_min": q.get("delay_min"), "name": q.get("description")}


@router.get("/api/global")
def world_list(_: User = Depends(current_user)) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        seeded = gx.seed(s)
    if seeded:                                          # first visit: the preset groups, prices filled in the background
        threading.Thread(target=gx.run_all, daemon=True, name="world-first-run").start()
    with SessionLocal() as s:
        rows = gx.stocks(s)
        syms = [g.symbol for g in rows]
        qs = gx.quotes(syms)
        since = datetime.now() - timedelta(days=30)
        out = []
        for g in rows:
            closes = [c for (c,) in s.execute(select(GlobalPrice.close).where(GlobalPrice.symbol == g.symbol)
                                              .order_by(GlobalPrice.date.desc()).limit(60)).all()][::-1]
            a = gx.latest_analysis(s, g.symbol)
            mv = s.scalar(select(GlobalMove).where(GlobalMove.symbol == g.symbol, GlobalMove.date >= since.date()).order_by(GlobalMove.date.desc()))
            mv_head = gx.move_headlines(s, g.symbol, mv.date, 1) if mv else []
            last_h = s.scalar(select(GlobalHeadline).where(GlobalHeadline.symbol == g.symbol).order_by(GlobalHeadline.published.desc()))
            q = _quote_row(qs.get(g.symbol))
            if q is None and len(closes) >= 2:              # the scanner is down: the stored close
                q = {"close": closes[-1], "change_pct": (closes[-1] / closes[-2] - 1) * 100, "currency": g.currency, "stored": True}
            out.append({"symbol": g.symbol, "ticker": gx.split(g.symbol)[1], "exchange": gx.split(g.symbol)[0], "name": g.name, "group": g.group,
                        "currency": g.currency or (q or {}).get("currency"), "quote": q, "spark": closes,
                        "analysis": {"date": str(a.date), "verdict": a.verdict, "score": a.score, "verdict_analyst": a.verdict_analyst,
                                     "score_analyst": a.score_analyst, "verdict_smc": a.verdict_smc, "score_smc": a.score_smc} if a else None,
                        "move": {"date": str(mv.date), "pct": mv.pct, "headline": mv_head[0] if mv_head else None} if mv else None,
                        "headline": gx.headline_row(last_h) if last_h else None})
        run = gx.last_run(s)
        groups = list(dict.fromkeys([*gx.GROUP_ORDER, *(g.group for g in rows)]))
        return _jsonable({"rows": out, "groups": groups, "move_pct": gx.cfg()["move_pct"],
                          "run": {"at": run.started_at.isoformat(timespec="seconds") + "Z", "ok": run.ok} if run else None,
                          "running": gx._run_lock.locked()})


class AddIn(BaseModel):
    query: str
    group: str = "Other"
    aliases: list[str] | None = None


@router.post("/api/global")
def world_add(body: AddIn, _: User = Depends(require_admin)) -> dict[str, Any]:
    try:
        found = gx.resolve(body.query)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Couldn't reach TradingView: {exc}") from None
    with SessionLocal.begin() as s:
        if s.get(GlobalStock, found["symbol"]):
            raise HTTPException(400, f"{found['symbol']} is already on the list")
        if (s.query(GlobalStock).count()) >= 80:
            raise HTTPException(400, "The world list holds at most 80 stocks")
        ex, t = gx.split(found["symbol"])
        aliases = [a.strip() for a in (body.aliases or []) if a.strip()] or gx.default_aliases(found["name"], t)
        s.add(GlobalStock(symbol=found["symbol"], name=found["name"][:200], group=(body.group or "Other").strip()[:60] or "Other",
                          aliases=aliases[:8], currency=found.get("currency")))
    res = gx.refresh_one(found["symbol"], news=True, move_news=0)
    gx.backfill_move_news_async(found["symbol"])
    if not res.get("bars"):
        raise HTTPException(502, f"Added {found['symbol']}, but TradingView returned no price history yet. It is tried again tonight.")
    return {"ok": True, "symbol": found["symbol"], "name": found["name"], **res}


class EditIn(BaseModel):
    group: str | None = None
    aliases: list[str] | None = None
    name: str | None = None


@router.put("/api/global/{symbol}")
def world_edit(symbol: str, body: EditIn, _: User = Depends(require_admin)) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        g = _own(s, symbol)
        if body.group is not None and body.group.strip():
            g.group = body.group.strip()[:60]
        if body.name is not None and body.name.strip():
            g.name = body.name.strip()[:200]
        if body.aliases is not None:
            g.aliases = [a.strip() for a in body.aliases if a.strip()][:8] or [g.name]
        return {"ok": True, "group": g.group, "aliases": g.aliases}


@router.delete("/api/global/{symbol}")
def world_delete(symbol: str, _: User = Depends(require_admin)) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        _own(s, symbol)
        gx.remove(s, symbol)
    return {"ok": True}


@router.post("/api/global/run")
def world_run(_: User = Depends(require_admin)) -> dict[str, Any]:
    if gx._run_lock.locked():
        return {"started": False, "message": "already running"}
    threading.Thread(target=gx.run_all, daemon=True, name="world-run").start()
    return {"started": True}


@router.get("/api/global/{symbol}/analysis")
def world_analysis(symbol: str, tf: str = "1D", _: User = Depends(current_user)) -> dict[str, Any]:
    from .intraday import HTF
    with SessionLocal() as s:
        g = _own(s, symbol)
        if tf in HTF and tf not in ("1D", "1W"):
            res = gx.analyze_intraday(s, g, tf)
            res.update(name=g.name, created_at=None, logo=None)
            return _jsonable(res)
        a = gx.latest_analysis(s, symbol)
        if a is None:
            raise HTTPException(404, "No analysis yet: prices are still being fetched. Try again in a minute.")
        return _jsonable({**a.result, "symbol": symbol, "name": g.name, "date": str(a.date), "created_at": a.created_at.isoformat() + "Z",
                          "logo": None, "currency": g.currency})


@router.get("/api/global/{symbol}/candles")
def world_candles(symbol: str, tf: str = "1D", _: User = Depends(current_user)) -> dict[str, Any]:
    from .engine import frame_chart, weekly_bars
    with SessionLocal() as s:
        _own(s, symbol)
        daily = gx.frame(s, symbol)
    if tf == "1D":
        raw = daily
    elif tf == "1W":
        raw = weekly_bars(daily) if not daily.empty else daily
    elif tf in gx.INTRA_MINUTES:
        raw = gx.intraday_bars(symbol, tf)
    else:
        raise HTTPException(400, "unknown timeframe")
    if raw.empty:
        return {"symbol": symbol, "timeframe": tf, "bars": [], "indicators": {}, "events": [], "adjusted": True}
    events = []
    if tf in ("1D", "1W"):                              # big moves as markers (the NEWS tab has their headlines)
        with SessionLocal() as s:
            first = raw.index[0].date()
            for m in s.scalars(select(GlobalMove).where(GlobalMove.symbol == symbol, GlobalMove.date >= first).order_by(GlobalMove.date)):
                events.append({"time": str(m.date), "kind": "move", "applied": False, "factor": None, "text": f"{m.pct:+.1f}%"})
    return frame_chart(symbol, tf, raw, events=events)


@router.get("/api/global/{symbol}/quote")
def world_quote(symbol: str, _: User = Depends(current_user)) -> dict[str, Any]:
    with SessionLocal() as s:
        g = _own(s, symbol)
    return {"symbol": symbol, "name": g.name, "group": g.group, "aliases": g.aliases, "quote": _quote_row(gx.quotes([symbol]).get(symbol))}


@router.get("/api/global/{symbol}/news")
def world_news(symbol: str, days: int = Query(365, ge=30, le=2000), _: User = Depends(current_user)) -> dict[str, Any]:
    """Each big move with the headlines from the day before to the day after, and the latest headlines."""
    with SessionLocal() as s:
        g = _own(s, symbol)
        since = datetime.now().date() - timedelta(days=days)
        moves = s.scalars(select(GlobalMove).where(GlobalMove.symbol == symbol, GlobalMove.date >= since).order_by(GlobalMove.date.desc())).all()
        mv = [{"date": str(m.date), "pct": m.pct, "checked": m.news_checked, "headlines": gx.move_headlines(s, symbol, m.date)} for m in moves]
        recent = s.scalars(select(GlobalHeadline).where(GlobalHeadline.symbol == symbol).order_by(GlobalHeadline.published.desc()).limit(40)).all()
        pending = any(not m.news_checked for m in moves)
    busy = gx.backfill_move_news_async(symbol) if pending else gx.backfilling(symbol)
    return _jsonable({"symbol": symbol, "name": g.name, "aliases": g.aliases, "move_pct": gx.cfg()["move_pct"], "moves": mv,
                      "recent": [gx.headline_row(h) for h in recent], "searching": busy})
