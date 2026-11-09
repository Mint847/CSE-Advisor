"""World stocks beside the CSE: NVIDIA, Micron, SK Hynix, the big tech names and a few leaders from other sectors.

Prices: TradingView's chart feed (the same client as the CSE history), stored as daily bars in the exchange's own time
and currency; intraday bars are fetched on demand and kept a few minutes. Quotes for the list come from one TradingView
scanner request (delayed 15-20 minutes on the free feed).

Engines: the three CSE engines run on the daily bars every night. They were built and tested on CSE stocks only, so
on these they are untested; the CSE fees and the LKR position size in their plans don't apply and are removed.

News: one Google News search per stock each night, and, for every session on which the stock moved more than
`move_pct`, a search limited to the day before to the day after, so each big move shows the headlines that came out
around it. That is a list of what was published, found by name; it doesn't say which story caused the move.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import GlobalAnalysis, GlobalHeadline, GlobalMove, GlobalPrice, GlobalStock, JobRun, Preference, SessionLocal

log = logging.getLogger(__name__)

COLOMBO = ZoneInfo("Asia/Colombo")
EXCHANGE_TZ = {"NASDAQ": "America/New_York", "NYSE": "America/New_York", "AMEX": "America/New_York", "NYSEARCA": "America/New_York",
               "KRX": "Asia/Seoul", "TSE": "Asia/Tokyo", "TWSE": "Asia/Taipei", "HKEX": "Asia/Hong_Kong", "NSE": "Asia/Kolkata",
               "BSE": "Asia/Kolkata", "LSE": "Europe/London", "XETR": "Europe/Berlin", "EURONEXT": "Europe/Paris", "SIX": "Europe/Zurich",
               "SSE": "Asia/Shanghai", "SZSE": "Asia/Shanghai", "SGX": "Asia/Singapore", "ASX": "Australia/Sydney", "TSX": "America/Toronto"}
SEEDED_KEY = "global_stocks_seeded"
SCAN_URL = "https://scanner.tradingview.com/global/scan"
SCAN_COLS = ["description", "close", "change", "change_abs", "volume", "market_cap_basic", "currency", "sector", "industry",
             "price_52_week_high", "price_52_week_low", "Perf.W", "Perf.1M", "Perf.3M", "Perf.YTD", "Perf.Y", "price_earnings_ttm",
             "dividend_yield_recent", "average_volume_30d_calc", "update_mode", "exchange", "high", "low", "open"]
# how a bare ticker typed by the user is looked up (US first, then the main Asian exchanges)
TRY_EXCHANGES = ["NASDAQ", "NYSE", "AMEX", "KRX", "TSE", "TWSE", "HKEX", "NSE", "LSE", "XETR", "EURONEXT"]

PRESETS: list[dict[str, Any]] = [
    # AI & chips
    {"symbol": "NASDAQ:NVDA", "name": "NVIDIA", "group": "AI & chips", "aliases": ["Nvidia", "NVDA", "Jensen Huang"]},
    {"symbol": "NASDAQ:AMD", "name": "AMD", "group": "AI & chips", "aliases": ["AMD", "Advanced Micro Devices", "Lisa Su"]},
    {"symbol": "NASDAQ:AVGO", "name": "Broadcom", "group": "AI & chips", "aliases": ["Broadcom", "AVGO", "Hock Tan"]},
    {"symbol": "NYSE:TSM", "name": "TSMC (ADR)", "group": "AI & chips", "aliases": ["TSMC", "Taiwan Semiconductor"]},
    {"symbol": "NASDAQ:ASML", "name": "ASML (ADR)", "group": "AI & chips", "aliases": ["ASML"]},
    {"symbol": "NASDAQ:MU", "name": "Micron Technology", "group": "AI & chips", "aliases": ["Micron"]},
    {"symbol": "KRX:000660", "name": "SK Hynix", "group": "AI & chips", "aliases": ["SK Hynix", "Hynix"]},
    {"symbol": "KRX:005930", "name": "Samsung Electronics", "group": "AI & chips", "aliases": ["Samsung Electronics", "Samsung"]},
    {"symbol": "NASDAQ:INTC", "name": "Intel", "group": "AI & chips", "aliases": ["Intel", "Lip-Bu Tan"]},
    {"symbol": "NASDAQ:ARM", "name": "Arm Holdings", "group": "AI & chips", "aliases": ["Arm Holdings", "Arm Ltd", "Rene Haas"]},
    # big tech
    {"symbol": "NASDAQ:AAPL", "name": "Apple", "group": "Big tech", "aliases": ["Apple", "AAPL", "Tim Cook"]},
    {"symbol": "NASDAQ:MSFT", "name": "Microsoft", "group": "Big tech", "aliases": ["Microsoft", "MSFT", "Satya Nadella"]},
    {"symbol": "NASDAQ:GOOGL", "name": "Alphabet (Google)", "group": "Big tech", "aliases": ["Alphabet", "Google", "Sundar Pichai"]},
    {"symbol": "NASDAQ:AMZN", "name": "Amazon", "group": "Big tech", "aliases": ["Amazon", "AMZN", "Andy Jassy"]},
    {"symbol": "NASDAQ:META", "name": "Meta Platforms", "group": "Big tech", "aliases": ["Meta Platforms", "Meta", "Zuckerberg"]},
    {"symbol": "NASDAQ:TSLA", "name": "Tesla", "group": "Big tech", "aliases": ["Tesla", "TSLA"]},
    # other sectors
    {"symbol": "NYSE:JPM", "name": "JPMorgan Chase", "group": "Other sectors", "aliases": ["JPMorgan", "JP Morgan", "Jamie Dimon"]},
    {"symbol": "NYSE:XOM", "name": "Exxon Mobil", "group": "Other sectors", "aliases": ["Exxon", "ExxonMobil"]},
    {"symbol": "NYSE:LLY", "name": "Eli Lilly", "group": "Other sectors", "aliases": ["Eli Lilly", "Lilly"]},
    {"symbol": "NASDAQ:WMT", "name": "Walmart", "group": "Other sectors", "aliases": ["Walmart"]},
    {"symbol": "NYSE:BRK.B", "name": "Berkshire Hathaway", "group": "Other sectors", "aliases": ["Berkshire Hathaway", "Warren Buffett", "Greg Abel"]},
]
GROUP_ORDER = ["AI & chips", "Big tech", "Other sectors"]
FINANCE_WORDS = re.compile(r"\b(stock|stocks|shares|share price|falls?|fell|drops?|dropped|slides?|slid|plunges?|plunged|tumbles?|tumbled|"
                           r"sinks?|sank|jumps?|jumped|soars?|soared|surges?|surged|rall(?:y|ies|ied)|climbs?|climbed|gains?|rises?|rose|"
                           r"slumps?|selloff|sell-off|earnings|results|guidance|forecast|outlook|revenue|profit|downgrade|upgrade|"
                           r"price target|analyst|investors?|market value|valuation|lawsuit|probe|investigation|tariffs?|export|ban|"
                           r"ceo|resigns?|steps down|scandal|deal|acquisition|merger|record)\b", re.I)

DOWN_WORDS = re.compile(r"\b(falls?|fell|drops?|dropped|slides?|slid|plunges?|plunged|tumbles?|tumbled|sinks?|sank|slumps?|slumped|"
                        r"declines?|declined|loses|lost|selloff|sell-off|sheds?|crash(?:es|ed)?|dives?|dived|tanks?|tanked|slips?|slipped|"
                        r"down \d|lower|retreats?|worst|meltdown)\b", re.I)
UP_WORDS = re.compile(r"\b(jumps?|jumped|soars?|soared|surges?|surged|rall(?:y|ies|ied)|climbs?|climbed|gains?|gained|rises?|rose|"
                      r"spikes?|spiked|rockets?|leaps?|leapt|pops?|popped|rebounds?|rebounded|higher|record high|up \d|best)\b", re.I)


def cfg() -> dict[str, Any]:
    return {"enabled": True, "move_pct": 4.0, "history_bars": 1500, "move_news_per_night": 3, "move_news_days": 365,
            "quote_cache_seconds": 120, **(settings().get("global_stocks") or {})}


def split(symbol: str) -> tuple[str, str]:
    ex, _, t = symbol.partition(":")
    return ex.upper(), t.upper()


def tz_of(symbol: str) -> ZoneInfo:
    return ZoneInfo(EXCHANGE_TZ.get(split(symbol)[0], "America/New_York"))


# ------------------------------------------------------------------ the list
def seed(s: Session) -> int:
    """Add the preset groups the first time only, so a preset you delete stays deleted."""
    if s.get(Preference, SEEDED_KEY) is not None:
        return 0
    have = set(s.scalars(select(GlobalStock.symbol)))
    n = 0
    for p in PRESETS:
        if p["symbol"] not in have:
            s.add(GlobalStock(symbol=p["symbol"], name=p["name"], group=p["group"], aliases=p["aliases"]))
            n += 1
    s.merge(Preference(key=SEEDED_KEY, value=True))
    return n


def stocks(s: Session) -> list[GlobalStock]:
    rank = {g: i for i, g in enumerate(GROUP_ORDER)}
    return sorted(s.scalars(select(GlobalStock)), key=lambda g: (rank.get(g.group, 99), g.group, g.added_at or datetime.min, g.symbol))


def default_aliases(name: str, ticker: str) -> list[str]:
    base = re.sub(r"\b(inc|corp|corporation|co|ltd|limited|plc|holdings?|group|nv|sa|ag|se|class [a-z]|sponsored adr|adr)\b\.?,?", " ",
                  name, flags=re.I)
    base = re.sub(r"[,.]+", " ", base)
    base = re.sub(r"\s+", " ", base).strip()
    out = [base] if base else []
    if re.fullmatch(r"[A-Z]{2,5}", ticker) and ticker not in out:
        out.append(ticker)
    return out or [name]


# ------------------------------------------------------------------ quotes (one scanner request for every stock)
_quotes: dict[str, Any] = {"at": 0.0, "rows": {}}
_quote_lock = threading.Lock()


def scan(symbols: list[str], timeout: float = 20) -> dict[str, dict[str, Any]]:
    if not symbols:
        return {}
    r = httpx.post(SCAN_URL, json={"symbols": {"tickers": symbols}, "columns": SCAN_COLS}, timeout=timeout,
                   headers={"User-Agent": "Mozilla/5.0 (CSE Advisor personal tool)"})
    r.raise_for_status()
    out = {}
    for row in r.json().get("data", []):
        d = row.get("d") or []
        if len(d) != len(SCAN_COLS):
            continue
        q = dict(zip(SCAN_COLS, d))
        m = re.search(r"(\d+)$", str(q.get("update_mode") or ""))
        q["delay_min"] = int(m.group(1)) // 60 if m else None
        out[row["s"]] = q
    return out


def quotes(symbols: list[str], max_age: float | None = None) -> dict[str, dict[str, Any]]:
    """Latest (delayed) quotes for these stocks, cached for a couple of minutes for everyone."""
    max_age = float(cfg()["quote_cache_seconds"]) if max_age is None else max_age
    with _quote_lock:
        missing = [x for x in symbols if x not in _quotes["rows"]]
        if missing or time.monotonic() - _quotes["at"] > max_age:
            try:
                got = scan(sorted(set(symbols) | set(_quotes["rows"])))
                _quotes["rows"], _quotes["at"] = {**_quotes["rows"], **got}, time.monotonic()
            except Exception as exc:  # noqa: BLE001 - unofficial endpoint: keep the last quotes
                log.warning("world quotes: %s", exc)
        return {x: _quotes["rows"][x] for x in symbols if x in _quotes["rows"]}


def resolve(text: str) -> dict[str, Any]:
    """'NASDAQ:PLTR', 'PLTR' or 'KRX:000660' -> the TradingView id with its name and currency. Raises ValueError."""
    t = text.strip().upper().replace(" ", "")
    if not t:
        raise ValueError("Type a ticker, e.g. PLTR or NASDAQ:PLTR")
    cands = [t] if ":" in t else [f"{ex}:{t}" for ex in TRY_EXCHANGES]
    found = scan(cands)
    for c in cands:
        if c in found:
            q = found[c]
            return {"symbol": c, "name": q.get("description") or c, "currency": q.get("currency")}
    raise ValueError(f"TradingView has no stock '{text}'. Try EXCHANGE:TICKER, e.g. NYSE:ORCL or KRX:005930.")


# ------------------------------------------------------------------ prices
def bars(symbol: str, tf: str = "1D", n: int = 300) -> pd.DataFrame:
    """TradingView bars as a frame indexed by the exchange's own wall time (daily bars: the session date)."""
    from . import tv_client
    ex, t = split(symbol)
    raw = tv_client.fetch_bars(t, tf, n, exchange=ex)
    if not raw:
        return pd.DataFrame()
    df = pd.DataFrame(raw)
    idx = pd.DatetimeIndex(pd.to_datetime(df["ts"])).tz_localize(COLOMBO).tz_convert(tz_of(symbol)).tz_localize(None)
    df.index = idx.normalize() if tf == "1D" else idx
    df = df.drop(columns=["ts"])
    df = df[~df.index.duplicated(keep="last")]
    df["volume"] = df["volume"].fillna(0)
    df["turnover"] = df["close"] * df["volume"]
    df["open_estimated"] = False
    return df


def store_bars(s: Session, symbol: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    have = {r.date: r for r in s.scalars(select(GlobalPrice).where(GlobalPrice.symbol == symbol, GlobalPrice.date >= df.index[0].date()))}
    for ts, r in df.iterrows():
        d = ts.date()
        row = have.get(d) or GlobalPrice(symbol=symbol, date=d)
        row.open, row.high, row.low, row.close, row.volume = float(r.open), float(r.high), float(r.low), float(r.close), float(r.volume or 0)
        s.add(row)
    return len(df)


def frame(s: Session, symbol: str) -> pd.DataFrame:
    rows = s.execute(select(GlobalPrice.date, GlobalPrice.open, GlobalPrice.high, GlobalPrice.low, GlobalPrice.close, GlobalPrice.volume)
                     .where(GlobalPrice.symbol == symbol).order_by(GlobalPrice.date)).all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df.index = pd.DatetimeIndex(pd.to_datetime(df.pop("date")))
    df["turnover"] = df["close"] * df["volume"]
    df["open_estimated"] = False
    return df


def update_prices(s: Session, symbol: str, retry_after: float = 5.0) -> int:
    n_have = s.scalar(select(func.count()).select_from(GlobalPrice).where(GlobalPrice.symbol == symbol)) or 0
    n = int(cfg()["history_bars"]) if n_have < 60 else 30
    df = bars(symbol, "1D", n)
    if df.empty and retry_after:                       # TradingView sometimes closes the socket at once: one more try
        time.sleep(retry_after)
        df = bars(symbol, "1D", n)
    return store_bars(s, symbol, df)


# ------------------------------------------------------------------ the engines
def _news_items(s: Session, symbol: str, asof: date) -> list[dict[str, Any]]:
    """Headlines as light news items for the engines' news factor, as for CSE stocks (materiality 1, half the tone)."""
    since = datetime.combine(asof, datetime.min.time()) - timedelta(days=180)
    out = []
    for h in s.scalars(select(GlobalHeadline).where(GlobalHeadline.symbol == symbol, GlobalHeadline.published >= since)):
        if h.tone is not None:
            out.append({"id": h.id, "title": h.title, "date": h.published, "category": "headline", "sentiment": h.tone * 0.5, "materiality": 1})
    return out


def _world_terms(res: dict[str, Any], currency: str | None) -> dict[str, Any]:
    """The plans' CSE-only parts don't apply here: drop the LKR position size, and say which currency the numbers are in."""
    cur = currency or "local currency"
    rec = res.get("recommendation") or {}
    for key in ("original", "analyst", "smc"):
        plan = (rec.get(key) or {}).get("plan")
        if isinstance(plan, dict):
            plan["size"] = None
        ex = (rec.get(key) or {}).get("explanation")
        if isinstance(ex, dict) and ex.get("sizing"):
            ex["sizing"] = None                        # an LKR position size from the CSE capital setting
    liq = res.get("liquidity") or {}
    if isinstance(liq.get("notes"), list):
        liq["notes"] = [n.replace("LKR", cur) for n in liq["notes"]]
    rec["world"] = {"currency": currency, "note": "Engines built and tested on CSE stocks; untested here. Targets net of CSE fees; no position size."}
    return res


def analyze(s: Session, gs: GlobalStock, save: bool = True) -> dict[str, Any] | None:
    from .engine import MIN_BARS, analyze_frame, history_window
    df = history_window(frame(s, gs.symbol))
    if len(df) < MIN_BARS:
        return None
    sec = SimpleNamespace(symbol=gs.symbol, name=gs.name, info=None, shares_issued=None, sector=None)
    res = analyze_frame(None, sec, df, [], fund_rows=[], anns=_news_items(s, gs.symbol, df.index[-1].date()), md=pd.DataFrame())
    res = _world_terms(res, gs.currency)
    if save:
        d = df.index[-1].date()
        row = s.scalar(select(GlobalAnalysis).where(GlobalAnalysis.symbol == gs.symbol, GlobalAnalysis.date == d)) or GlobalAnalysis(symbol=gs.symbol, date=d)
        row.verdict, row.score = res["verdict"], res["score"]
        row.verdict_analyst, row.score_analyst = res["verdict_analyst"], res["score_analyst"]
        row.verdict_smc, row.score_smc = res["verdict_smc"], res["score_smc"]
        row.result, row.created_at = res, datetime.utcnow()
        s.add(row)
    return res


def latest_analysis(s: Session, symbol: str) -> GlobalAnalysis | None:
    return s.scalar(select(GlobalAnalysis).where(GlobalAnalysis.symbol == symbol).order_by(GlobalAnalysis.date.desc()))


_intra: dict[tuple[str, str], tuple[float, Any]] = {}
_intra_lock = threading.Lock()
INTRA_TTL = 180.0
INTRA_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}


def intraday_bars(symbol: str, tf: str, n: int = 700) -> pd.DataFrame:
    """Intraday bars from TradingView, kept three minutes (they are not stored)."""
    key = (symbol, tf)
    with _intra_lock:
        hit = _intra.get(key)
        if hit and time.monotonic() - hit[0] < INTRA_TTL:
            return hit[1]
    df = bars(symbol, tf, n)
    with _intra_lock:
        _intra[key] = (time.monotonic(), df)
    return df


def completed(df: pd.DataFrame, tf: str, symbol: str, now: datetime | None = None) -> pd.DataFrame:
    """Drop the bar still forming (exchange wall time)."""
    if df.empty or tf not in INTRA_MINUTES:
        return df
    now = now or datetime.now(tz_of(symbol)).replace(tzinfo=None)
    return df.iloc[:-1] if df.index[-1] + timedelta(minutes=INTRA_MINUTES[tf]) > now else df


INTRA_ANALYSIS_BARS = 500
_ia: dict[tuple[str, str], tuple[float, Any, Any]] = {}


def analyze_intraday(s: Session, gs: GlobalStock, tf: str) -> dict[str, Any]:
    """The engines on intraday bars, on demand (not stored); the same completed bar is answered from a cache."""
    from .engine import MIN_BARS, analyze_frame
    from .intraday import HTF
    daily = latest_analysis(s, gs.symbol)
    df = completed(intraday_bars(gs.symbol, tf), tf, gs.symbol).tail(INTRA_ANALYSIS_BARS)
    if len(df) < MIN_BARS:
        return {"symbol": gs.symbol, "tf": tf, "error": f"not enough {tf} bars from TradingView ({len(df)}; the engines need {MIN_BARS})"}
    hit = _ia.get((gs.symbol, tf))
    if hit and hit[1] == df.index[-1] and time.monotonic() - hit[0] < 600:
        return hit[2]
    h = HTF[tf]
    htf = frame(s, gs.symbol) if h == "1D" else completed(intraday_bars(gs.symbol, h, 300), h, gs.symbol)
    shared = {"liquidity": (daily.result.get("liquidity") if daily else None) or {}, "fundamentals": {}}
    sec = SimpleNamespace(symbol=gs.symbol, name=gs.name, info=None, shares_issued=None, sector=None)
    res = analyze_frame(None, sec, df, [], tf=tf, shared=shared, htf_df=htf[["open", "high", "low", "close"]] if not htf.empty else None,
                        anns=[], md=pd.DataFrame(), fund_rows=[])
    res = _world_terms(res, gs.currency)
    res["daily"] = {"date": str(daily.date) if daily else None,
                    "verdicts": {"original": daily.verdict, "analyst": daily.verdict_analyst, "smc": daily.verdict_smc} if daily else None}
    _ia[(gs.symbol, tf)] = (time.monotonic(), df.index[-1], res)
    return res


# ------------------------------------------------------------------ big moves
def find_moves(s: Session, symbol: str, threshold: float | None = None, days: int | None = None) -> list[GlobalMove]:
    """Sessions with a close-to-close move of at least `threshold` %, over the last `days`."""
    threshold = float(cfg()["move_pct"]) if threshold is None else threshold
    days = int(cfg()["move_news_days"]) if days is None else days
    df = frame(s, symbol)
    if len(df) < 2:
        return []
    ret = df["close"].pct_change() * 100
    since = df.index[-1] - pd.Timedelta(days=days)
    have = {m.date: m for m in s.scalars(select(GlobalMove).where(GlobalMove.symbol == symbol))}
    out = []
    for ts, r in ret.items():
        if ts < since or pd.isna(r) or abs(r) < threshold:
            continue
        m = have.get(ts.date()) or GlobalMove(symbol=symbol, date=ts.date(), news_checked=False)
        m.pct = round(float(r), 2)
        s.add(m)
        out.append(m)
    return out


# ------------------------------------------------------------------ news
def _patterns(gs: GlobalStock) -> list[re.Pattern]:
    """Whole-word matches. Long or multi-word names ignore case; short ones ("Meta", "Intel", "AMD") must be written
    as a name (Meta / META), so the everyday word doesn't count."""
    out = []
    for a in (gs.aliases or [gs.name]):
        if not a:
            continue
        if len(a) >= 6 or " " in a:
            out.append(re.compile(r"(?<![\w-])" + re.escape(a) + r"(?![\w-])", re.I))
        else:
            out.append(re.compile(r"(?<![\w-])(?:" + re.escape(a) + "|" + re.escape(a.upper()) + r")(?![\w-])"))
    return out


def about(title: str, pats: list[re.Pattern]) -> bool:
    return any(p.search(title) for p in pats)


def _query(gs: GlobalStock) -> str:
    names = [a for a in (gs.aliases or [gs.name]) if a][:3]
    return "(" + " OR ".join(f'"{a}"' for a in names) + ")"


def _get(url: str) -> str:
    r = httpx.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (CSE Advisor personal tool)"}, follow_redirects=True)
    r.raise_for_status()
    return r.text


def headline_tone(title: str) -> float:
    """The keyword tone, overruled by a price verb: "Nvidia stock sinks 5%" reads as negative whatever else it says."""
    from .news_feeds import tone
    t = tone(title)
    down, up = bool(DOWN_WORDS.search(title)), bool(UP_WORDS.search(title))
    if down and not up:
        return min(t, -0.4)
    if up and not down:
        return max(t, 0.4)
    return t


def store_news(s: Session, gs: GlobalStock, items: list[dict[str, Any]], move_date: date | None = None) -> int:
    tone = headline_tone
    pats = _patterns(gs)
    n = 0
    for it in items:
        if not about(it["title"], pats):
            continue                                   # Google also returns pages that only mention the name in passing
        h = s.get(GlobalHeadline, (it["id"], gs.symbol))
        if h is None:
            h = GlobalHeadline(id=it["id"], symbol=gs.symbol, title=it["title"], link=it["link"], outlet=it.get("outlet"),
                               published=it.get("published"), tone=tone(it["title"]), move_date=move_date)
            s.add(h)
            n += 1
        elif move_date and h.move_date is None:
            h.move_date = move_date
    return n


def fetch_news(s: Session, gs: GlobalStock, get: Any = None) -> int:
    """Last two days' headlines about this stock."""
    from .news_feeds import google_url, parse_rss
    xml = (get or _get)(google_url(f"{_query(gs)} when:2d", "US"))
    return store_news(s, gs, parse_rss(xml, "Google News")[:40])


def fetch_move_news(s: Session, gs: GlobalStock, mv: GlobalMove, get: Any = None) -> int:
    """Headlines from the day before a big move to the day after, biased to market news."""
    from .news_feeds import google_url, parse_rss
    q = f"{_query(gs)} (stock OR shares) after:{mv.date - timedelta(days=1)} before:{mv.date + timedelta(days=2)}"
    n = store_news(s, gs, parse_rss((get or _get)(google_url(q, "US")), "Google News")[:40], move_date=mv.date)
    mv.news_checked = True
    return n


def move_headlines(s: Session, symbol: str, d: date, limit: int = 8) -> list[dict[str, Any]]:
    """Headlines published from the day before to the day after a move (Colombo time covers the US session of `d`,
    which ends the next morning here), market news first."""
    start, end = datetime.combine(d - timedelta(days=1), datetime.min.time()), datetime.combine(d + timedelta(days=2), datetime.min.time())
    rows = s.scalars(select(GlobalHeadline).where(GlobalHeadline.symbol == symbol, GlobalHeadline.published >= start,
                                                  GlobalHeadline.published < end)).all()
    seen, out = set(), []
    mv = s.get(GlobalMove, (symbol, d))
    want = (DOWN_WORDS if mv.pct < 0 else UP_WORDS) if mv else None

    def rank(h: GlobalHeadline) -> tuple[int, float]:
        r = 2 if want is not None and want.search(h.title) else 1 if FINANCE_WORDS.search(h.title) else 0
        when = h.published or datetime.combine(d, datetime.min.time())
        return (-r, abs((when - datetime.combine(d, datetime.min.time()) - timedelta(hours=20)).total_seconds()))   # ~ the US session
    for h in sorted(rows, key=rank):
        k = re.sub(r"\W+", " ", h.title.lower())[:60]
        if k in seen:
            continue
        seen.add(k)
        out.append(headline_row(h))
    return out[:limit]


def headline_row(h: GlobalHeadline) -> dict[str, Any]:
    return {"title": h.title, "link": h.link, "outlet": h.outlet, "published": h.published.isoformat(timespec="minutes") if h.published else None,
            "tone": h.tone, "market": bool(FINANCE_WORDS.search(h.title))}


_backfilling: set[str] = set()


def backfill_move_news_async(symbol: str, limit: int = 12) -> bool:
    """Search the headlines around this stock's big moves that haven't been searched yet (in the background)."""
    if symbol in _backfilling:
        return True
    with SessionLocal() as s:
        todo = s.scalar(select(func.count()).select_from(GlobalMove).where(GlobalMove.symbol == symbol, GlobalMove.news_checked.is_(False))) or 0
    if not todo:
        return False
    _backfilling.add(symbol)

    def work() -> None:
        try:
            with SessionLocal() as s:
                ids = [m.date for m in s.scalars(select(GlobalMove).where(GlobalMove.symbol == symbol, GlobalMove.news_checked.is_(False))
                                                 .order_by(GlobalMove.date.desc()).limit(limit))]
            for d in ids:
                with SessionLocal.begin() as s:
                    gs, mv = s.get(GlobalStock, symbol), s.get(GlobalMove, (symbol, d))
                    if gs and mv:
                        try:
                            fetch_move_news(s, gs, mv)
                        except Exception as exc:  # noqa: BLE001
                            log.warning("move news %s %s: %s", symbol, d, exc)
                            break
                time.sleep(1.5)                          # polite to Google
        finally:
            _backfilling.discard(symbol)
    threading.Thread(target=work, daemon=True, name=f"move-news-{symbol}").start()
    return True


def backfilling(symbol: str) -> bool:
    return symbol in _backfilling


# ------------------------------------------------------------------ one stock, all stocks
def refresh_one(symbol: str, news: bool = True, move_news: int = 0) -> dict[str, Any]:
    out: dict[str, Any] = {}
    with SessionLocal.begin() as s:
        gs = s.get(GlobalStock, symbol)
        if gs is None:
            return {"error": "unknown"}
        out["bars"] = update_prices(s, symbol)
    with SessionLocal.begin() as s:
        gs = s.get(GlobalStock, symbol)
        find_moves(s, symbol)
        if news:
            try:
                out["news"] = fetch_news(s, gs)
            except Exception as exc:  # noqa: BLE001
                out["news_error"] = str(exc)[:200]
        for mv in s.scalars(select(GlobalMove).where(GlobalMove.symbol == symbol, GlobalMove.news_checked.is_(False))
                            .order_by(GlobalMove.date.desc()).limit(move_news)):
            try:
                fetch_move_news(s, gs, mv)
                time.sleep(1.5)
            except Exception as exc:  # noqa: BLE001
                out["move_news_error"] = str(exc)[:200]
                break
    with SessionLocal.begin() as s:
        gs = s.get(GlobalStock, symbol)
        r = analyze(s, gs) if gs else None
        out["verdicts"] = [r["verdict"], r["verdict_analyst"], r["verdict_smc"]] if r else None
    return out


_run_lock = threading.Lock()


def run_all() -> dict[str, Any]:
    """The nightly world-stock job: prices, big moves, headlines, the engines. Recorded as JobRun 'global'."""
    if not cfg()["enabled"] or not _run_lock.acquire(blocking=False):
        return {"skipped": "disabled or already running"}
    started = datetime.utcnow()
    res: dict[str, Any] = {}
    try:
        with SessionLocal.begin() as s:
            seed(s)
            syms = [g.symbol for g in stocks(s)]
        try:
            with SessionLocal.begin() as s:
                cur = {k: v.get("currency") for k, v in scan(syms).items()}
                for g in s.scalars(select(GlobalStock)):
                    if cur.get(g.symbol) and not g.currency:
                        g.currency = cur[g.symbol]
        except Exception as exc:  # noqa: BLE001
            log.warning("world currencies: %s", exc)
        for sym in syms:
            try:
                res[sym] = refresh_one(sym, news=True, move_news=int(cfg()["move_news_per_night"]))
            except Exception as exc:  # noqa: BLE001
                log.warning("world stock %s: %s", sym, exc)
                res[sym] = {"error": str(exc)[:200]}
        # TradingView drops the odd request: one more pass, half a minute later, for any stock whose prices didn't come
        again = [k for k, v in res.items() if "error" not in v and not v.get("bars")]
        if again:
            time.sleep(30)
            for sym in again:
                try:
                    res[sym] = {**res[sym], **refresh_one(sym, news=False, move_news=0), "second_try": True}
                except Exception as exc:  # noqa: BLE001
                    log.warning("world stock %s (second try): %s", sym, exc)
        ok = not any("error" in v for v in res.values())
        with SessionLocal.begin() as s:
            import json
            s.add(JobRun(job="global", run_date=date.today(), ok=ok, started_at=started,
                         message=json.dumps({"stocks": len(syms), "failed": [k for k, v in res.items() if "error" in v]})[:2000]))
        return res
    finally:
        _run_lock.release()


def last_run(s: Session) -> JobRun | None:
    return s.scalar(select(JobRun).where(JobRun.job == "global").order_by(JobRun.started_at.desc()))


def catch_up_later(delay: float = 90.0) -> None:
    """At start-up: wait for the CSE catch-up (which also uses TradingView) to get going first."""
    t = threading.Timer(delay, catch_up)
    t.daemon = True
    t.start()


def catch_up(max_age_hours: float = 26) -> None:
    """At start-up and hourly: run in the background if the last world run is older than a day (the PC slept through it)."""
    if not cfg()["enabled"] or _run_lock.locked():
        return
    with SessionLocal() as s:
        r = last_run(s)
    if r is None or (datetime.utcnow() - r.started_at).total_seconds() > max_age_hours * 3600:
        threading.Thread(target=run_all, daemon=True, name="world-catch-up").start()


def remove(s: Session, symbol: str) -> None:
    for m in (GlobalPrice, GlobalAnalysis, GlobalMove, GlobalHeadline):
        s.execute(delete(m).where(m.symbol == symbol))
    s.execute(delete(GlobalStock).where(GlobalStock.symbol == symbol))

