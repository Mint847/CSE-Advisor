"""Data ingestion: securities, price history, EOD bars, market data, announcements, reports."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, time as dtime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .cse_client import client, ms_to_date, ms_to_local, now_local
from .db import (
    LOCAL_UID, Announcement, CorporateAction, FinancialReport, MarketDaily, Preference, PriceDaily, SessionLocal, Security,
    WatchlistItem,
)

log = logging.getLogger(__name__)


def _market_closed_today() -> bool:
    close_h, close_m = map(int, settings()["market"]["close"].split(":"))
    now = now_local()
    return now.weekday() >= 5 or now.time() >= dtime(close_h, close_m)


# ---------------------------------------------------------------- securities
def resolve_symbol(user_input: str) -> str:
    """Accept 'JKH', 'jkh.n0000' or a company name fragment and return the CSE symbol."""
    q = user_input.strip().upper()
    if re.fullmatch(r"[A-Z0-9]+\.[A-Z]\d{4}", q):
        return q
    rows = client().trade_summary()
    exact = [r["symbol"] for r in rows if r["symbol"].split(".")[0] == q]
    if exact:
        # prefer the ordinary voting share (N0000)
        exact.sort(key=lambda s: (not s.endswith(".N0000"), s))
        return exact[0]
    by_name = [r["symbol"] for r in rows if q in (r.get("name") or "").upper()]
    if len(by_name) == 1:
        return by_name[0]
    if by_name:
        raise ValueError(f"'{user_input}' matches several companies: {', '.join(by_name[:8])}")
    raise ValueError(f"No CSE security found for '{user_input}' (it may not have traded today; try the full symbol e.g. JKH.N0000)")


def upsert_security(s: Session, symbol: str) -> Security:
    info = client().company_info(symbol)
    si = info.get("reqSymbolInfo") or {}
    if not si:
        raise ValueError(f"CSE returned no company info for {symbol}")
    beta = info.get("reqSymbolBetaInfo") or {}
    logo = info.get("reqLogo") or {}
    sec = s.get(Security, symbol) or Security(symbol=symbol)
    sec.cse_id = si["id"]
    sec.security_id = beta.get("securityId") or logo.get("secId")
    sec.name = si.get("name") or symbol
    sec.shares_issued = si.get("quantityIssued")
    sec.beta_aspi = beta.get("triASIBetaValue")
    sec.beta_spsl = beta.get("betaValueSPSL")
    sec.logo_path = logo.get("path")
    sec.isin = si.get("isin")
    sec.info = si
    sec.updated_at = datetime.utcnow()
    s.add(sec)
    return sec


# ---------------------------------------------------------------- prices
def backfill_history(s: Session, sec: Security) -> int:
    """Merge ~1y of daily history. Real EOD bars (with true opens) are never overwritten."""
    rows = client().daily_history(sec.cse_id)
    rows.sort(key=lambda r: r["t"])
    existing = {p.date: p for p in s.scalars(select(PriceDaily).where(PriceDaily.symbol == sec.symbol))}
    today = now_local().date()
    closed = _market_closed_today()
    prev_close: float | None = None
    added = 0
    for r in rows:
        d = ms_to_date(r["t"])
        close = r.get("p")
        if close is None:
            continue
        if d == today and not closed:
            break  # today's bar is still forming
        hi = r.get("h") or close
        lo = r.get("l") or close
        op = prev_close if prev_close is not None else close
        op = min(max(op, lo), hi)  # estimated open must lie inside the bar
        vol = float(r.get("q") or 0)
        bar = existing.get(d)
        if bar is None:
            s.add(PriceDaily(symbol=sec.symbol, date=d, open=op, high=hi, low=lo, close=close,
                             volume=vol, turnover=close * vol, open_estimated=True, source="history"))
            added += 1
        elif bar.source == "history":
            bar.high, bar.low, bar.close = hi, lo, close
            if bar.tv_close is None:       # not yet put on TradingView's crossing-free volume basis
                bar.volume = vol
        prev_close = close
    return added


def backfill_deep_history(s: Session, sec: Security) -> dict[str, Any]:
    """Multi-year daily history from TradingView (back to ~2012), merged under cse.lk's one year.

    Measured 2026-11-04 on the six watchlist stocks over the overlapping year:
    - TradingView's daily bars are already adjusted for rights / bonus / splits (KZOO's pre-XR closes ~44%
      lower than cse.lk's raw ones), so days before cse.lk's history are stored as source 'tv_adj' and left
      alone by adjust_prices; engine.load_adjusted stitches them onto the app's own adjusted series.
    - Its opens are real opens (identical on every trade-summary 'eod' bar), so the estimated opens of
      cse.lk's 'history' bars are replaced (put on the raw basis first where TradingView adjusted that day).
    - Its volume excludes crossings like the trade-summary share volume; cse.lk's chart history includes
      them (JKH median ratio 0.80). History bars take TradingView's volume and keep the difference as
      crossing_volume, so the whole series is on one basis.
    """
    from .tv_client import fetch_bars
    if not (settings().get("history") or {}).get("tradingview", True):
        return {"skipped": "history.tradingview is off"}
    bars = fetch_bars(sec.symbol, "1D", int((settings().get("history") or {}).get("tv_bars", 5000)))
    if not bars:
        return {"added": 0, "error": "TradingView returned no daily bars"}
    tv = {b["ts"].date(): b for b in bars}
    existing = {p.date: p for p in s.scalars(select(PriceDaily).where(PriceDaily.symbol == sec.symbol))}
    cse_days = sorted(d for d, p in existing.items() if p.source != "tv_adj")
    first_cse = cse_days[0] if cse_days else None
    today = now_local().date()
    added = opens = vols = 0
    for d, b in tv.items():
        p = existing.get(d)
        if p is None:
            if (first_cse and d >= first_cse) or (d == today and not _market_closed_today()):
                continue   # a gap inside cse.lk's own year (or today's forming bar): don't mix sources there
            vol = float(b["volume"] or 0)
            s.add(PriceDaily(symbol=sec.symbol, date=d, open=b["open"], high=b["high"], low=b["low"], close=b["close"],
                             volume=vol, turnover=b["close"] * vol, open_estimated=False, source="tv_adj"))
            added += 1
            continue
        if p.source == "tv_adj":
            p.open, p.high, p.low, p.close, p.volume = b["open"], b["high"], b["low"], b["close"], float(b["volume"] or 0)
            p.turnover = p.close * p.volume
            continue
        p.tv_close = b["close"]
        ratio = (p.close / b["close"]) if b["close"] else 1.0
        k = ratio if abs(ratio - 1) > 0.03 else 1.0       # TradingView adjusted this day: back to the raw basis
        if p.source == "history" and p.open_estimated:
            p.open = min(max(b["open"] * k, p.low), p.high)
            p.open_estimated = False
            opens += 1
        if p.source == "history" and b["volume"]:
            tv_vol = float(b["volume"]) / k
            if p.volume and p.volume > tv_vol * 1.02:
                p.crossing_volume = p.volume - tv_vol
            p.volume, p.turnover = tv_vol, p.close * tv_vol
            vols += 1
    return {"added": added, "opens_fixed": opens, "volumes_fixed": vols,
            "first": str(min(tv)) if tv else None, "tv_bars": len(tv)}


def has_deep_history(s: Session, symbol: str) -> bool:
    return bool(s.scalar(select(func.count()).select_from(PriceDaily)
                         .where(PriceDaily.symbol == symbol, PriceDaily.source == "tv_adj")))


def backfill_market_deep(s: Session) -> int:
    """ASPI daily closes back to ~2012 from TradingView (CSELK:ASI), for days the app has no market row."""
    from .tv_client import fetch_bars
    added = 0
    have = {m.date: m for m in s.scalars(select(MarketDaily))}
    for b in fetch_bars("ASI", "1D", 5000):
        d = b["ts"].date()
        m = have.get(d)
        if m is None:
            s.add(MarketDaily(date=d, aspi=b["close"]))
            added += 1
        elif m.aspi is None:
            m.aspi = b["close"]
    return added


def record_eod(s: Session, symbols: list[str]) -> dict[str, Any]:
    """Store today's real bars (with true open) from the trade summary for watchlist symbols."""
    rows = client().trade_summary()
    by_sym = {r["symbol"]: r for r in rows}
    stored = []
    adv = sum(1 for r in rows if (r.get("change") or 0) > 0)
    dec = sum(1 for r in rows if (r.get("change") or 0) < 0)
    trade_date = None
    for sym in symbols:
        r = by_sym.get(sym)
        if not r or not r.get("price"):
            continue
        d = ms_to_date(r["lastTradedTime"])
        trade_date = trade_date or d
        close = r.get("closingPrice") or r["price"]
        bar = s.scalar(select(PriceDaily).where(PriceDaily.symbol == sym, PriceDaily.date == d))
        bar = bar or PriceDaily(symbol=sym, date=d)
        bar.open = r.get("open") or r.get("previousClose") or close
        bar.high = r.get("high") or close
        bar.low = r.get("low") or close
        bar.close = close
        bar.volume = float(r.get("sharevolume") or 0)
        bar.turnover = r.get("turnover")
        bar.trades = r.get("tradevolume")
        bar.crossing_volume = r.get("crossingVolume")
        bar.open_estimated = not bool(r.get("open"))
        bar.source = "eod"
        s.add(bar)
        stored.append(sym)
    # market-wide snapshot
    try:
        ms, aspi, sp = client().market_summary(), client().aspi(), client().spsl20()
        d = ms_to_date(aspi.get("timestamp") or ms.get("tradeDate"))
        md = s.get(MarketDaily, d) or MarketDaily(date=d)
        md.aspi, md.spsl20 = aspi.get("value"), sp.get("value")
        md.turnover, md.share_volume, md.trades = ms.get("tradeVolume"), ms.get("shareVolume"), ms.get("trades")
        md.advancers, md.decliners = adv, dec
        s.add(md)
    except Exception as exc:  # market data is nice-to-have
        log.warning("market summary failed: %s", exc)
    return {"date": str(trade_date) if trade_date else None, "stored": stored}


def backfill_market(s: Session) -> int:
    added = 0
    for r in client().aspi_history():
        d = ms_to_date(r["d"])
        md = s.get(MarketDaily, d)
        if md is None:
            s.add(MarketDaily(date=d, aspi=r.get("v")))
            added += 1
        elif md.aspi is None:
            md.aspi = r.get("v")
    return added


# ---------------------------------------------------------------- market extras (shown only, not scored)
MARKET_INDICES = {"ASI", "S&P SL20"}   # allSectors also lists the two market indices; they are not sectors


def record_market_extras(s: Session) -> dict[str, Any]:
    """Foreign flow + market valuation (dailyMarketSummery, last ~2 sessions) and today's sector indices."""
    from .db import SectorDaily
    out: dict[str, Any] = {"market_days": 0, "sectors": 0}
    try:
        for r in client().daily_market_summary():
            if not r.get("tradeDate"):
                continue
            d = ms_to_date(r["tradeDate"])
            md = s.get(MarketDaily, d) or MarketDaily(date=d)
            md.foreign_buy, md.foreign_sell = r.get("equityForeignPurchase"), r.get("equityForeignSales")
            md.foreign_trades = int(r["tradesNoForeign"]) if r.get("tradesNoForeign") is not None else None
            md.per, md.pbv, md.dy, md.market_cap = r.get("per"), r.get("pbv"), r.get("dy"), r.get("marketCap")
            md.turnover = md.turnover or r.get("marketTurnover")
            md.trades = md.trades or (int(r["marketTrades"]) if r.get("marketTrades") else None)
            md.aspi = md.aspi or r.get("asi")
            s.add(md)
            out["market_days"] += 1
    except Exception as exc:
        log.warning("daily market summary failed: %s", exc)
    try:
        for r in client().all_sectors():
            if not r.get("symbol") or not r.get("transactionTime") or r["symbol"] in MARKET_INDICES:
                continue
            d = ms_to_date(r["transactionTime"])
            row = s.scalar(select(SectorDaily).where(SectorDaily.code == r["symbol"], SectorDaily.date == d)) \
                or SectorDaily(code=r["symbol"], date=d, name=r.get("name") or r["symbol"])
            row.name, row.index_name = r.get("name") or r["symbol"], r.get("indexName")
            row.value, row.change, row.change_pct = r.get("indexValue"), r.get("change"), r.get("percentage")
            row.turnover, row.volume = r.get("sectorTurnoverToday"), r.get("sectorVolumeToday")
            row.trades = int(r["sectorTradeToday"]) if r.get("sectorTradeToday") is not None else None
            s.add(row)
            out["sectors"] += 1
    except Exception as exc:
        log.warning("sector indices failed: %s", exc)
    return out


def backfill_sectors(s: Session, n: int = 300) -> int:
    """One-off: ~n daily closes per S&P/CSE sector index from TradingView (allSectors' indexCodeSp is the
    TradingView symbol, e.g. SPCSEBP), so the sector table has history from the first day."""
    from .db import SectorDaily
    from .tv_client import fetch_bars
    added = 0
    for r in client().all_sectors():
        code, tv = r.get("symbol"), r.get("indexCodeSp")
        if not code or not tv or code in MARKET_INDICES:
            continue
        have = set(s.scalars(select(SectorDaily.date).where(SectorDaily.code == code)))
        prev = None
        for b in fetch_bars(tv, "1D", n):
            d = b["ts"].date()
            if d not in have:
                s.add(SectorDaily(code=code, date=d, name=r.get("name") or code, index_name=r.get("indexName"), value=b["close"],
                                  change=(b["close"] - prev) if prev else None,
                                  change_pct=((b["close"] / prev - 1) * 100) if prev else None))
                added += 1
            prev = b["close"]
    return added


def _norm_company(name: str) -> str:
    n = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper().replace("&", " AND "))
    n = re.sub(r"\b(PLC|LIMITED|LTD|COMPANY|THE)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def match_company(company: str, securities: list[tuple[str, str]]) -> str | None:
    """Watchlist symbol whose company name equals `company` (case, punctuation and PLC/LIMITED ignored).
    Exact only: 'JOHN KEELLS PLC' must not match 'JOHN KEELLS HOLDINGS PLC'."""
    key = _norm_company(company)
    if not key:
        return None
    hits = [sym for sym, name in securities if _norm_company(name) == key]
    hits.sort(key=lambda x: (not x.endswith(".N0000"), x))
    return hits[0] if hits else None


def _parse_cse_dt(v: Any) -> datetime | None:
    if isinstance(v, (int, float)):
        return ms_to_local(v).replace(tzinfo=None)
    try:
        return datetime.strptime(str(v), "%d %b %Y %I:%M:%S %p")
    except (TypeError, ValueError):
        return None


def sync_market_notices(s: Session, symbols: list[str]) -> dict[str, Any]:
    """Market-wide lists matched to the watchlist: non-compliance / modified-audit-opinion notices are stored
    as announcements (so the engines' existing red-flag rules see them), and a new financial statement
    triggers an early report sync for that stock."""
    from .analysis.news import classify_announcement
    secs = [(sym, (s.get(Security, sym).name if s.get(Security, sym) else sym)) for sym in symbols]
    new: list[Announcement] = []
    try:
        for r in client().non_compliance():
            aid = r.get("announcementId") or r.get("id")
            sym = match_company(r.get("company") or r.get("companyName") or "", secs)
            if not sym or aid is None or s.get(Announcement, aid):
                continue
            title = (r.get("announcementCategory") or r.get("title") or "Non-compliance notice").strip().title()
            cat, sentiment, materiality = classify_announcement(title, None)
            a = Announcement(id=aid, symbol=sym, title=title, body="(market-wide non-compliance list)", date=_parse_cse_dt(r.get("createdDate")),
                             category=cat, sentiment=sentiment, materiality=materiality)
            s.add(a)
            new.append(a)
    except Exception as exc:
        log.warning("non-compliance list failed: %s", exc)
    synced = []
    try:
        short = {sym.split(".")[0]: sym for sym in sorted(symbols, key=lambda x: not x.endswith(".N0000"))[::-1]}
        for r in client().financial_announcements():
            sym = short.get((r.get("symbol") or "").upper())
            if sym and sym not in synced and r.get("id") and not s.get(FinancialReport, r["id"]):
                sync_reports(s, sym)
                synced.append(sym)
    except Exception as exc:
        log.warning("financial announcements list failed: %s", exc)
    return {"notices": new, "reports_synced": synced}


# ---------------------------------------------------------------- text & reports
def sync_announcements(s: Session, symbol: str) -> list[Announcement]:
    from .analysis.news import classify_announcement

    new: list[Announcement] = []
    for a in client().announcements(symbol):
        aid = a.get("announcementId")
        if aid is None or s.get(Announcement, aid):
            continue
        title = (a.get("announcementCategory") or "").strip().title()
        extras = {k: a[k] for k in ("xd", "xr", "recordDate", "paymentDate", "agmDate", "allotment") if a.get(k)}
        body = re.sub(r"<br\s*/?>", "\n", a.get("remarks") or "").strip()
        if extras:
            body = (body + "\n" if body else "") + "; ".join(f"{k}: {v}" for k, v in extras.items())
        cat, sentiment, materiality = classify_announcement(title, body or None)
        ann = Announcement(id=aid, symbol=symbol, title=title, body=body or None,
                           date=ms_to_local(a["createdDate"]).replace(tzinfo=None) if a.get("createdDate") else None,
                           file_path=None, category=cat, sentiment=sentiment, materiality=materiality)
        s.add(ann)
        new.append(ann)
    return new


CA_PATTERNS = {
    "rights_dates": re.compile(r"^RIGHTS ISSUE \(DATES\)", re.I),
    "rights": re.compile(r"^RIGHTS ISSUE$|RIGHTS ISSUE OF SHARES", re.I),
    "listing": re.compile(r"LISTING OF SHARES", re.I),
    "split": re.compile(r"SUB-?DIVISION|SPLIT", re.I),
    "bonus": re.compile(r"CAPITALI[SZ]ATION OF RESERVES|BONUS ISSUE", re.I),
}


def _ms_date(v: Any) -> date | None:
    try:
        return ms_to_date(v) if isinstance(v, (int, float)) and v > 0 else None
    except Exception:
        return None


def _detail(aid: int) -> tuple[dict[str, Any], str]:
    """(base announcement fields, text of the first attached PDF)."""
    d = client().announcement_detail(aid)
    base = d.get("reqBaseAnnouncement") or {}
    docs = d.get("reqAnnouncementDocs") or []
    text = ""
    for doc in docs[:1]:
        if (doc.get("contentType") or "").endswith("pdf") and doc.get("fileUrl"):
            try:
                text = client().document_text(doc["fileUrl"])
            except Exception as exc:
                log.debug("doc text failed: %s", exc)
    return base, text


def sync_corporate_actions(s: Session, symbol: str, max_age_days: int = 420) -> list[CorporateAction]:
    """Build rights / bonus / split records from CSE announcements (one detail request per new announcement)."""
    from .analysis.corporate_actions import parse_proportion, parse_rights_price, parse_split, price_from_listing

    # TradingView's older bars (tv_adj) arrive adjusted, so only cse.lk's own history needs action records
    first = s.scalar(select(func.min(PriceDaily.date)).where(PriceDaily.symbol == symbol, PriceDaily.source != "tv_adj"))
    horizon = min(now_local().date() - timedelta(days=max_age_days), first or now_local().date())
    anns = s.scalars(select(Announcement).where(Announcement.symbol == symbol).order_by(Announcement.date)).all()
    known = {a.announcement_id for a in s.scalars(select(CorporateAction).where(CorporateAction.symbol == symbol))}
    seen_skip = set((s.get(Preference, f"ca_seen:{symbol}") or Preference(key="", value=[])).value or [])
    changed: list[CorporateAction] = []
    actions = list(s.scalars(select(CorporateAction).where(CorporateAction.symbol == symbol)))

    def _ref(a: CorporateAction) -> date | None:
        if a.ex_date:
            return a.ex_date
        ann_d = (a.detail or {}).get("announced")
        return date.fromisoformat(ann_d) if ann_d else None

    def rights_near(d: date | None) -> CorporateAction | None:
        cands = [a for a in actions if a.kind == "rights" and _ref(a) and d and abs((_ref(a) - d).days) <= 120]
        return min(cands, key=lambda a: abs((_ref(a) - d).days)) if cands else None

    for ann in anns:
        if not ann.date or ann.date.date() < horizon or ann.id in known or ann.id in seen_skip:
            continue
        title = (ann.title or "").upper()
        kind = next((k for k, rx in CA_PATTERNS.items() if rx.search(title)), None)
        if not kind:
            continue
        base, text = _detail(ann.id)
        seen_skip.add(ann.id)
        remarks = " ".join(str(base.get(k) or "") for k in ("remarks", "votingProportion", "description"))
        full = f"{remarks}\n{ann.body or ''}\n{text}"
        if kind == "rights_dates":
            prop = parse_proportion(base.get("votingProportion") or full)
            ex = _ms_date(base.get("xr")) or _ms_date(base.get("xd"))
            a = rights_near(ex) or CorporateAction(symbol=symbol, kind="rights")
            if not a.manual:
                a.ex_date = ex or a.ex_date
                if prop:
                    a.ratio_new, a.ratio_old = prop
                a.trading_date = _ms_date(base.get("tradingCommencement")) or a.trading_date
                a.price = a.price or parse_rights_price(full)
            a.announcement_id = a.announcement_id or ann.id
            a.detail = {**(a.detail or {}), "dates": {k: base.get(k) for k in ("xr", "recordDate", "allotment", "tradingCommencement", "egm")},
                        "proportion": base.get("votingProportion")}
        elif kind == "rights":
            a = rights_near(ann.date.date())
            price = parse_rights_price(full)
            prop = parse_proportion(full)
            if a is None:
                a = CorporateAction(symbol=symbol, kind="rights", announcement_id=ann.id,
                                    detail={"announced": ann.date.date().isoformat()})
            if not a.manual:
                a.price = a.price or price
                if prop and not a.ratio_new:
                    a.ratio_new, a.ratio_old = prop
        elif kind == "listing":
            if "RIGHT" not in full.upper():
                continue
            a = rights_near(ann.date.date())
            if a is None:
                continue
            got = price_from_listing(text)
            if not a.manual:
                a.listing_date = a.listing_date or ann.date.date()
                if got and not a.price:
                    a.price = round(got[1], 2)
                    a.detail = {**(a.detail or {}), "price_source": f"listing notice: {got[0]:,.0f} shares, Rs {got[0] * got[1]:,.0f} raised"}
        else:  # split / bonus
            ex = _ms_date(base.get("xd")) or _ms_date(base.get("xr")) or _ms_date(base.get("tradingCommencement"))
            if kind == "split":
                n = parse_split(full)
                ratio = (n, 1.0) if n else None
            else:
                ratio = parse_proportion(full)
            a = CorporateAction(symbol=symbol, kind=kind, announcement_id=ann.id, ex_date=ex,
                                ratio_new=ratio[0] if ratio else None, ratio_old=ratio[1] if ratio else None,
                                detail={"proportion": base.get("votingProportion"), "remarks": (base.get("remarks") or "")[:300]})
        s.add(a)
        if a not in actions:
            actions.append(a)
        changed.append(a)
    pref = s.get(Preference, f"ca_seen:{symbol}") or Preference(key=f"ca_seen:{symbol}")
    pref.value = sorted(seen_skip)
    s.add(pref)
    return changed


def import_history_csv(s: Session, symbol: str, csv_text: str, already_adjusted: bool) -> dict[str, Any]:
    """Merge a CSV of daily bars (date,open,high,low,close,volume) for multi-year history.
    Existing cse.lk rows are never overwritten."""
    import io
    import pandas as pd
    df = pd.read_csv(io.StringIO(csv_text))
    df.columns = [c.strip().lower() for c in df.columns]
    if "time" in df.columns and "date" not in df.columns:
        df = df.rename(columns={"time": "date"})
    need = {"date", "high", "low", "close"}
    if not need.issubset(df.columns):
        raise ValueError(f"CSV needs columns {sorted(need)} (+ open, volume); got {list(df.columns)}")
    d = pd.to_datetime(df["date"], errors="coerce", utc=False)
    if d.isna().all():
        d = pd.to_datetime(pd.to_numeric(df["date"], errors="coerce"), unit="s")
    df["date"] = d.dt.date
    df = df.dropna(subset=["date", "close"])
    existing = set(s.scalars(select(PriceDaily.date).where(PriceDaily.symbol == symbol)))
    added = 0
    for r in df.itertuples():
        if r.date in existing:
            continue
        o = getattr(r, "open", None)
        vol = float(getattr(r, "volume", 0) or 0)
        s.add(PriceDaily(symbol=symbol, date=r.date, open=float(o) if o == o and o is not None else float(r.close),
                         high=float(r.high), low=float(r.low), close=float(r.close), volume=vol,
                         turnover=float(r.close) * vol, open_estimated=o is None or o != o,
                         source="import_adj" if already_adjusted else "import"))
        existing.add(r.date)
        added += 1
    return {"added": added, "rows": len(df), "first": str(min(df["date"])) if len(df) else None}


def sync_reports(s: Session, symbol: str) -> int:
    data = client().financials(symbol)
    added = 0
    for kind, key in (("annual", "infoAnnualData"), ("quarterly", "infoQuarterlyData")):
        for r in data.get(key) or []:
            if s.get(FinancialReport, r["id"]):
                continue
            s.add(FinancialReport(
                id=r["id"], symbol=symbol, kind=kind, title=r.get("fileText") or kind, path=r["path"],
                period_date=ms_to_local(r["manualDate"]).replace(tzinfo=None) if r.get("manualDate") else None,
                uploaded=ms_to_local(r["uploadedDate"]).replace(tzinfo=None) if isinstance(r.get("uploadedDate"), (int, float)) else None,
            ))
            added += 1
    return added


def enrich_announcements(s: Session, symbol: str, max_requests: int = 250) -> dict[str, Any]:
    """Read what announcements are actually about (one request each, once per announcement).

    getAnnouncementById gives typed facts for dividends (DPS, XD, payment), director dealings (buy / sell,
    quantity, price), appointments and AGMs; generic 'Corporate Disclosure' notices fall back to
    getGeneralAnnouncementById, whose attachment file name carries the real subject. Every stored
    announcement is then re-classified from its title plus that text (no request needed for that)."""
    from .analysis.news import attachment_subject, classify_announcement, event_text, parse_typed
    now = datetime.now()
    rows = s.scalars(select(Announcement).where(Announcement.symbol == symbol).order_by(Announcement.date.desc())).all()
    todo = []
    for a in rows:
        if a.event is not None or not a.date:
            continue
        age = (now - a.date).days
        cat = a.category or "other"
        if (cat in ("director_dealing", "dividend", "bonus_issue") and age <= 3 * 365) or \
                (cat in ("other", "disclosure", "meeting", "management_change", "management_exit", "results", "circular") and age <= 400):
            todo.append(a)
    done = changed = 0
    for a in todo[:max_requests]:
        r = client().announcement_typed(a.id)
        base = r.get("reqBaseAnnouncement") or {}
        docs = r.get("reqAnnouncementDocs") or []
        if not base:
            d = client().announcement_detail(a.id)
            base = d.get("reqBaseAnnouncement") or {}
            docs = d.get("reqAnnouncementDocs") or []
        ev = parse_typed(base) if base else {"kind": "none"}
        a.attachment = attachment_subject(docs[0].get("fileUrl")) if docs else None
        a.event = ev
        text = " ".join(x for x in ((base.get("remarks") or "").strip(), a.attachment or "", event_text(ev)) if x)
        if text and text not in (a.body or ""):
            a.body = ((a.body + "\n") if a.body else "") + text
        done += 1
    # re-classify everything from title + whatever text is known (the rules changed, and new text arrived)
    for a in rows:
        cat, sent, mat = classify_announcement(a.title, a.body)
        ev = a.event or {}
        if ev.get("kind") == "dealing":
            net = ev.get("net_value") or 0
            cat, sent = "director_dealing", (0.4 if net > 0 else -0.4 if net < 0 else 0.0)
        elif ev.get("kind") == "cash_dividend":
            cat = "dividend"
        if (cat, sent, mat) != (a.category, a.sentiment, a.materiality):
            a.category, a.sentiment, a.materiality = cat, sent, mat
            changed += 1
    return {"fetched": done, "pending": max(0, len(todo) - max_requests), "reclassified": changed}


PL_FIELDS = ("revenue", "operating_profit", "finance_cost", "net_profit", "eps", "dividend_per_share")
BS_FIELDS = ("total_assets", "total_equity", "total_liabilities")


def _report_choice(reports: list[FinancialReport], n: int) -> list[tuple[date, FinancialReport]]:
    """The newest `n` quarter-ends, one report each: the '(Extractable Version)' when CSE has two."""
    from .analysis.report_parser import period_from_title
    by_period: dict[date, list[FinancialReport]] = {}
    for r in reports:                # every report in CSE's quarterly list is an interim statement with a quarter column
        d, _ = period_from_title(r.title)
        if d:
            by_period.setdefault(d, []).append(r)
    out = []
    for d in sorted(by_period, reverse=True)[:n]:
        rs = sorted(by_period[d], key=lambda r: ("extractable" not in (r.title or "").lower(), -(r.uploaded or datetime.min).timestamp()))
        out.append((d, rs[0]))
    return out


def sync_report_figures(s: Session, symbol: str, n: int | None = None, force: bool = False) -> dict[str, Any]:
    """Read the newest quarterly statements (text PDFs) into the fundamentals table, by rules.

    Each report gives its quarter and, from the comparative column, the same quarter a year earlier
    (stored as 'pdf_prior' unless a direct row exists). A row whose EPS x shares or NAV x shares doesn't
    match the figures stays needs_review (shown, not used in scores). Manually entered rows are never
    overwritten. Report ids already read are remembered, so each PDF is downloaded once."""
    from .analysis.report_parser import cross_check, extract_figures
    from .db import Fundamental
    n = n or int((settings().get("fundamentals") or {}).get("auto_reports", 6))
    sec = s.get(Security, symbol)
    reports = list(s.scalars(select(FinancialReport).where(FinancialReport.symbol == symbol, FinancialReport.kind == "quarterly")))
    key = f"report_parsed:{symbol}"
    pref = s.get(Preference, key) or Preference(key=key, value=[])
    seen = set(pref.value or [])
    out: dict[str, Any] = {"parsed": [], "review": [], "unreadable": [], "skipped": 0}
    for d, rep in _report_choice(reports, n):
        if rep.id in seen and not force:
            out["skipped"] += 1
            continue
        text = client().document_text(rep.path, max_bytes=8_000_000, max_pages=16)
        seen.add(rep.id)
        if len(text) < 2000:
            out["unreadable"].append(rep.title)
            continue
        res = extract_figures(text, rep.title)
        chk = cross_check(res, sec.shares_issued if sec else None)
        detail = {"report_id": rep.id, "title": rep.title, "url": "https://cdn.cse.lk/" + rep.path.lstrip("/"),
                  "column": res["column"], "evidence": res["evidence"], "checks": chk["checks"], "bank": res["bank"],
                  "nav_per_share": res["fields"].get("nav_per_share")}
        row = s.scalar(select(Fundamental).where(Fundamental.symbol == symbol, Fundamental.period_end == d, Fundamental.period_type == "Q"))
        if row is not None and row.source == "manual":
            continue
        # strongest check: a newer report already gave this quarter in its comparative column
        if row is not None and row.source == "pdf_prior" and row.net_profit and res["fields"].get("net_profit"):
            same = abs(res["fields"]["net_profit"] / row.net_profit - 1) <= 0.02
            note = f"profit matches the comparative column of {(row.detail or {}).get('title', 'a newer report')}"
            chk["checks"].append(note + (" ✓" if same else f" ✗ ({row.net_profit / 1e6:,.0f}M there; restated?)"))
            if same:
                chk["needs_review"] = False
            detail["checks"] = chk["checks"]
        row = row or Fundamental(symbol=symbol, period_end=d, period_type="Q")
        for f in PL_FIELDS + BS_FIELDS:
            setattr(row, f, res["fields"].get(f))
        row.source, row.needs_review, row.report_id, row.detail = "pdf", chk["needs_review"], rep.id, detail
        s.add(row)
        (out["review"] if chk["needs_review"] else out["parsed"]).append(str(d))
        # the same quarter last year, from the comparative column
        if res["prior"]:
            try:
                pd_ = d.replace(year=d.year - 1)
            except ValueError:
                pd_ = d.replace(year=d.year - 1, day=28)
            prev = s.scalar(select(Fundamental).where(Fundamental.symbol == symbol, Fundamental.period_end == pd_, Fundamental.period_type == "Q"))
            if prev is None or prev.source == "pdf_prior":
                prev = prev or Fundamental(symbol=symbol, period_end=pd_, period_type="Q")
                for f in PL_FIELDS:
                    setattr(prev, f, res["prior"].get(f))
                prev.source, prev.needs_review, prev.report_id = "pdf_prior", chk["needs_review"], rep.id
                prev.detail = {**detail, "comparative": True, "checks": [f"comparative column of {rep.title}"] + chk["checks"]}
                s.add(prev)
        s.flush()
    pref.value = sorted(seen)
    s.add(pref)
    return out


# ---------------------------------------------------------------- orchestration
def watchlist_symbols(s: Session, user_id: str | None = None) -> list[str]:
    """Stocks the data jobs keep up to date: every account's watchlist and open positions (each once). With
    `user_id`, just that account's watchlist."""
    from .accounts import tracked_symbols, watchlist_symbols as wl
    return wl(s, user_id) if user_id is not None else tracked_symbols(s)


def add_to_watchlist(user_input: str, user_id: str = LOCAL_UID, max_items: int | None = None, group_id: int | None = None) -> str:
    """Track a stock for this account and put it on a list (`group_id`, or the first list)."""
    from . import accounts
    symbol = resolve_symbol(user_input)
    with SessionLocal.begin() as s:
        have = s.scalar(select(WatchlistItem).where(WatchlistItem.user_id == user_id, WatchlistItem.symbol == symbol))
        if have is None and max_items is not None:
            n = s.scalar(select(func.count()).select_from(WatchlistItem).where(WatchlistItem.user_id == user_id)) or 0
            if n >= max_items:
                raise ValueError(f"Your watchlist is full ({max_items} stocks). Remove one first.")
        accounts.add_to_group(s, user_id, group_id, symbol)      # first: checks the list, before any slow fetching
        sec = upsert_security(s, symbol)
        if have is None:
            s.add(WatchlistItem(user_id=user_id, symbol=symbol))
        s.flush()
        backfill_history(s, sec)
        if s.scalar(select(func.count()).select_from(MarketDaily)) < 100:
            backfill_market(s)
        try:
            sync_announcements(s, symbol)
            sync_reports(s, symbol)
            s.flush()
            sync_corporate_actions(s, symbol)
        except Exception as exc:
            log.warning("text sync failed for %s: %s", symbol, exc)
    # older daily history (TradingView, one ~2 s request) before the first analysis, so it uses the years
    try:
        with SessionLocal.begin() as s:
            sec = s.get(Security, symbol)
            if sec and not has_deep_history(s, symbol):
                backfill_deep_history(s, sec)
    except Exception as exc:
        log.warning("deep history failed for %s: %s", symbol, exc)
    # intraday history (TradingView, ~15 s) in the background so adding a stock stays fast
    from .intraday import backfill_async, has_intraday
    with SessionLocal() as s:
        need = not has_intraday(s, symbol)
    if need:
        backfill_async(symbol, full=True)
    return symbol


def remove_from_watchlist(symbol: str, user_id: str = LOCAL_UID, group_id: int | None = None) -> bool:
    """Take a stock off one list (or all lists when `group_id` is None). Off its last list it stops being tracked.
    Returns True when it is no longer tracked."""
    from . import accounts
    with SessionLocal.begin() as s:
        return accounts.remove_from_group(s, user_id, group_id, symbol)


def catch_up(s: Session) -> list[str]:
    """Backfill any symbols whose stored history has gaps (e.g. the PC was off for days)."""
    fixed = []
    for sym in watchlist_symbols(s):
        last = s.scalar(select(func.max(PriceDaily.date)).where(PriceDaily.symbol == sym))
        if last is None or (now_local().date() - last).days > 3:
            sec = s.get(Security, sym) or upsert_security(s, sym)
            backfill_history(s, sec)
            fixed.append(sym)
    return fixed
