"""Market-overview routes: heatmap, the news & announcements feed, the calendar, key stats and the quote card for any
listed stock. Display only: nothing here feeds an engine."""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from . import accounts, ingest, listed_meta
from .auth import User, current_user
from .cse_client import client
from .db import Announcement, Headline, ListedMeta, Security, SectorDaily, SessionLocal

log = logging.getLogger(__name__)
router = APIRouter()


def _listed() -> list[dict[str, Any]]:
    from .main import _all_listed            # one cached CSE request shared with the All-stocks tab
    try:
        return _all_listed()
    except Exception as exc:  # noqa: BLE001 - unofficial endpoint
        log.warning("listed stocks: %s", exc)
        raise HTTPException(502, "Couldn't reach the CSE for prices. Try again in a minute.") from exc


# ------------------------------------------------------------------ heatmap
@router.get("/api/heatmap")
def heatmap(u: User = Depends(current_user)) -> dict[str, Any]:
    """Every listed stock with its day change, market cap and sector, for the treemap; plus the S&P/CSE sector indices."""
    rows = _listed()
    with SessionLocal() as s:
        meta = listed_meta.lookup(s)
        mine = accounts.user_symbols(s, u.uid)
        last = s.scalar(select(SectorDaily.date).order_by(SectorDaily.date.desc()).limit(1))
        idx = s.scalars(select(SectorDaily).where(SectorDaily.date == last)).all() if last else []
        age = listed_meta.age_days(s)
    out = []
    for r in rows:
        m = meta.get(r["symbol"], {})
        cap = r.get("market_cap") or m.get("market_cap")
        if not r.get("price") or not cap:
            continue
        out.append({"symbol": r["symbol"], "name": r["name"], "price": r["price"], "change_pct": r.get("change_pct") or 0.0,
                    "turnover": r.get("turnover") or 0.0, "market_cap": cap, "sector": m.get("sector") or "Other",
                    "industry": m.get("industry"), "mine": r["symbol"] in mine})
    return {"stocks": out, "meta_age_days": round(age, 1) if age is not None else None,
            "indices": [{"code": x.code, "name": x.name, "change_pct": x.change_pct, "value": x.value} for x in idx],
            "indices_date": last.isoformat() if last else None}


# ------------------------------------------------------------------ news & announcements feed
def _tone_ok(tone: float | None, want: str) -> bool:
    if want == "all":
        return True
    if tone is None:
        return want == "neutral"
    return tone > 0.15 if want == "positive" else tone < -0.15 if want == "negative" else abs(tone) <= 0.15


@router.get("/api/feed")
def feed(scope: str = Query("mine", pattern="^(mine|all)$"), kind: str = Query("all", pattern="^(all|announcement|headline)$"),
         tone: str = Query("all", pattern="^(all|positive|negative|neutral)$"), q: str = "", days: int = Query(14, ge=1, le=365),
         symbol: str | None = None, limit: int = Query(150, ge=1, le=500), u: User = Depends(current_user)) -> dict[str, Any]:
    """Announcements and headlines in one list, newest first. 'mine' = stocks on your lists or held (plus market-wide and
    macro headlines, which concern everyone)."""
    since = datetime.now() - timedelta(days=days)
    needle = q.strip().lower()
    items: list[dict[str, Any]] = []
    with SessionLocal() as s:
        mine = accounts.user_symbols(s, u.uid)
        names = {x.symbol: x.name for x in s.scalars(select(Security))}
        if kind in ("all", "announcement"):
            sel = select(Announcement).where(Announcement.date >= since)
            if symbol:
                sel = sel.where(Announcement.symbol == symbol)
            elif scope == "mine":
                sel = sel.where(Announcement.symbol.in_(mine or {""}))
            for a in s.scalars(sel.order_by(Announcement.date.desc()).limit(1500)):
                items.append({"type": "announcement", "id": f"a{a.id}", "at": a.date.isoformat(timespec="minutes") if a.date else None,
                              "title": a.title, "source": "CSE", "symbols": [a.symbol], "category": a.category, "tone": a.sentiment,
                              "importance": a.materiality, "link": None, "detail": a.attachment})
        if kind in ("all", "headline"):
            sel = select(Headline).where(Headline.published >= since)
            seen_cluster: set[str] = set()
            for h in s.scalars(sel.order_by(Headline.published.desc()).limit(2500)):
                llm = h.llm or {}
                about = [c["symbol"] for c in llm.get("companies") or [] if c.get("about") == "primary"] if llm.get("by") else (h.symbols or [])
                if symbol and symbol not in about:
                    continue
                if scope == "mine" and not symbol and about and not (set(about) & mine):
                    continue                     # a headline about somebody else's stock
                if scope == "mine" and not symbol and not about and (h.kind or "local") == "company":
                    continue
                key = h.cluster or h.id          # the same story from several outlets once
                if key in seen_cluster:
                    continue
                seen_cluster.add(key)
                items.append({"type": "headline", "id": f"h{h.id}", "at": h.published.isoformat(timespec="minutes") if h.published else None,
                              "title": h.title, "source": h.outlet or h.source, "symbols": about, "category": llm.get("category") or h.kind,
                              "tone": h.tone, "importance": None, "link": h.link, "detail": llm.get("reason")})
    out = []
    for it in items:
        if not _tone_ok(it["tone"], tone):
            continue
        if needle and needle not in (it["title"] or "").lower() and not any(needle in x.lower() or needle in (names.get(x) or "").lower() for x in it["symbols"]) \
                and needle not in (it["category"] or "").lower():
            continue
        out.append(it)
    out.sort(key=lambda x: x["at"] or "", reverse=True)
    return {"count": len(out), "items": out[:limit], "names": {x: names.get(x) for it in out[:limit] for x in it["symbols"] if names.get(x)}}


# ------------------------------------------------------------------ calendar
_CAL: dict[str, Any] = {"at": 0.0, "rows": None}
_CAL_LOCK = threading.Lock()
CAL_TTL = 900.0


def _calendar_rows(symbols: list[str]) -> list[dict[str, Any]]:
    """Upcoming dated events (results due, ex-dividend, payment, AGM, corporate actions) for these stocks, nearest first.
    Built the same way as each stock's CN tab, so the two always agree; cached for 15 minutes."""
    from . import catalysts, engine as eng
    rows: list[dict[str, Any]] = []
    with SessionLocal() as s:
        md = eng.load_market(s)
        for sym in symbols:
            try:
                adj, _ = eng.load_adjusted(s, sym)
                c = catalysts.build(s, sym, adj["close"] if not adj.empty else adj, md["aspi"] if "aspi" in md else None)
            except Exception as exc:  # noqa: BLE001
                log.warning("calendar %s: %s", sym, exc)
                continue
            rows += [{"symbol": sym, **e} for e in c.get("calendar", [])]
    return sorted(rows, key=lambda e: (e["date"], e["symbol"]))


@router.get("/api/calendar")
def calendar(scope: str = Query("mine", pattern="^(mine|all)$"), days: int = Query(90, ge=7, le=120), u: User = Depends(current_user)) -> dict[str, Any]:
    with SessionLocal() as s:
        mine = accounts.user_symbols(s, u.uid)
        tracked = ingest.watchlist_symbols(s)
        names = {x.symbol: x.name for x in s.scalars(select(Security))}
    with _CAL_LOCK:
        if _CAL["rows"] is None or time.monotonic() - _CAL["at"] > CAL_TTL or set(_CAL.get("syms", [])) != set(tracked):
            _CAL["rows"], _CAL["at"], _CAL["syms"] = _calendar_rows(sorted(tracked)), time.monotonic(), sorted(tracked)
        rows = _CAL["rows"]
    end = (date.today() + timedelta(days=days)).isoformat()
    shown = [{**r, "mine": r["symbol"] in mine} for r in rows if r["date"] <= end and (scope == "all" or r["symbol"] in mine)]
    return {"today": date.today().isoformat(), "events": shown, "names": {r["symbol"]: names.get(r["symbol"]) for r in shown}}


# ------------------------------------------------------------------ key stats and the quote card
_INFO: dict[str, tuple[float, dict[str, Any]]] = {}


def _company_info(symbol: str) -> dict[str, Any] | None:
    """The CSE's company summary for any listed stock (one request, cached 5 min); the stored copy if the CSE is unreachable."""
    hit = _INFO.get(symbol)
    if hit and time.monotonic() - hit[0] < 300:
        return hit[1]
    try:
        si = (client().company_info(symbol).get("reqSymbolInfo")) or None
        if si:
            _INFO[symbol] = (time.monotonic(), si)
            return si
    except Exception as exc:  # noqa: BLE001
        log.warning("company info %s: %s", symbol, exc)
    with SessionLocal() as s:
        sec = s.get(Security, symbol)
        return sec.info if sec else None


def key_stats(info: dict[str, Any] | None, meta: dict[str, Any] | None, price: float | None) -> dict[str, Any]:
    i, m = info or {}, meta or {}

    def pos(lo: float | None, hi: float | None) -> float | None:
        return round(100 * (price - lo) / (hi - lo), 0) if price and lo is not None and hi and hi > lo else None
    hi12, lo12 = i.get("p12HiPrice"), i.get("p12LowPrice")
    return {"price": price, "prev_close": i.get("previousClose"), "day_low": i.get("lowTrade"), "day_high": i.get("hiTrade"),
            "hi_52w": hi12, "lo_52w": lo12, "pos_52w": pos(lo12, hi12), "pct_from_high": round(100 * (price / hi12 - 1), 1) if price and hi12 else None,
            "hi_ytd": i.get("ytdHiPrice"), "lo_ytd": i.get("ytdLowPrice"), "hi_all": i.get("allHiPrice"), "lo_all": i.get("allLowPrice"),
            "volume_ytd": i.get("ytdShareVolume"), "volume_12m": i.get("p12ShareVolume"), "turnover_ytd": i.get("ytdTurnover"),
            "market_cap": i.get("marketCap") or m.get("market_cap"), "market_cap_pct": i.get("marketCapPercentage"),
            "foreign_pct": i.get("foreignPercentage"), "shares_issued": i.get("quantityIssued"), "par": i.get("parValue"),
            "sector": m.get("sector"), "industry": m.get("industry"), "isin": i.get("isin")}


@router.get("/api/stocks/{symbol}/stats")
def stats(symbol: str, price: float | None = None, _: User = Depends(current_user)) -> dict[str, Any]:
    with SessionLocal() as s:
        meta = s.get(ListedMeta, symbol)
        m = {"sector": meta.sector, "industry": meta.industry, "market_cap": meta.market_cap} if meta else {}
    return key_stats(_company_info(symbol), m, price)


@router.get("/api/quote/{symbol}")
def quote_card(symbol: str, u: User = Depends(current_user)) -> dict[str, Any]:
    """Any listed stock, tracked or not: today's quote, key stats, sector, and where it sits for this account."""
    row = next((r for r in _listed() if r["symbol"] == symbol), None)
    if row is None:
        raise HTTPException(404, "Not a listed security")
    with SessionLocal() as s:
        meta = s.get(ListedMeta, symbol)
        m = {"sector": meta.sector, "industry": meta.industry, "market_cap": meta.market_cap} if meta else {}
        lists = accounts.symbol_lists(s, u.uid).get(symbol, [])
        held = symbol in set(accounts.held_symbols(s, u.uid))
        tracked = s.get(Security, symbol) is not None
    return {"quote": row, "stats": key_stats(_company_info(symbol), m, row.get("price")), "lists": lists, "held": held, "tracked": tracked}


@router.get("/api/listed")
def listed_symbols(_: User = Depends(current_user)) -> list[dict[str, str]]:
    """Symbol and name of every listed stock (for the command line to open any of them)."""
    return [{"symbol": r["symbol"], "name": r["name"]} for r in _listed()]
