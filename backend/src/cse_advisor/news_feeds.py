"""News headlines from free RSS feeds, matched to tracked companies, with a tone reading.

Sources (checked 6 Nov 2026, all with this app's own honest User-Agent; a site that refuses automated readers is left
out, never worked around):
  local papers   Daily FT (top stories, sectors), Daily Mirror business, LBO, Daily News, The Island, Ada Derana
  macro          Central Bank of Sri Lanka press releases (inflation, external sector, PMI ...)
  company        a Google News search per tracked company (Newswire, MarketScreener, EconomyNext articles ...)
  global         Google News searches on world themes: oil, the Sri Lankan economy, global market moves
Not used: EconomyNext's feed (answers 403 to automated readers), rongo.lk (no feed or API), a RapidAPI scraper (key,
unknown provenance). GDELT refused requests at its documented rate, so it is not relied on.

Only the title, link, date and the feed's own short summary are stored; articles are linked, never copied. The tone
comes from Claude Haiku when switched on (news_llm.py), otherwise from the finance keyword rules below. Either way a
headline counts lightly in the news factor (half its tone, the lowest materiality) and can never raise a red flag.
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
import threading
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .cse_client import TZ, now_local
from .db import Headline, Preference, Security, SessionLocal

log = logging.getLogger(__name__)

# name: a label for the health table; source: shown in the UI; kind: local | macro (company / global come from Google)
FEEDS: list[dict[str, str]] = [
    {"name": "Daily FT top stories", "source": "Daily FT", "url": "https://www.ft.lk/rss/top-story/26", "kind": "local"},
    {"name": "Daily FT sectors", "source": "Daily FT", "url": "https://www.ft.lk/rss/sectors/20", "kind": "local"},
    {"name": "Daily Mirror business", "source": "Daily Mirror", "url": "https://www.dailymirror.lk/rss/business/273", "kind": "local"},
    {"name": "LBO", "source": "LBO", "url": "https://www.lankabusinessonline.com/feed/", "kind": "local"},
    {"name": "Daily News", "source": "Daily News", "url": "https://www.dailynews.lk/feed/", "kind": "local"},
    {"name": "The Island", "source": "The Island", "url": "https://island.lk/feed/", "kind": "local"},
    {"name": "Ada Derana", "source": "Ada Derana", "url": "https://www.adaderana.lk/rss.php", "kind": "local"},
    {"name": "CBSL press releases", "source": "CBSL", "url": "https://www.cbsl.gov.lk/en/rss.xml", "kind": "macro"},
]
NOT_USED = [{"name": "EconomyNext", "note": "its feed answers 403 to automated readers, so it is not read"}]
GOOGLE = "https://news.google.com/rss/search"
_UA = {"User-Agent": "Mozilla/5.0 (personal CSE advisor; low-frequency)"}
STATUS_KEY = "news_feed_status"
_last = {"at": 0.0}
_lock = threading.Lock()

# keyword reader: finance phrases count double, single words once; "no"/"not" before a positive word flips it
POS_PHRASES = [r"profits? (?:up|rises?|rose|jumps?|jumped|surges?|surged|soars?|soared|grows?|grew|climbs?|climbed|doubles?|doubled)",
               r"record (?:profits?|revenues?|earnings|sales|turnover)", r"(?:beats?|beat) (?:estimates|expectations|forecasts?)",
               r"upgraded?", r"turns? (?:to|into) (?:a )?profit", r"back (?:in|to) (?:the )?black", r"dividend (?:raised|increased|hike)",
               r"(?:revenue|earnings|sales) (?:up|rises?|rose|grows?|grew|jumps?)", r"wins? (?:contract|order|deal)"]
NEG_PHRASES = [r"loss(?:es)? (?:widens?|widened|deepens?|deepened|swells?|up)", r"profits? (?:falls?|fell|drops?|dropped|slumps?|slumped|declines?|declined|plunges?|plunged|down|halves|halved)",
               r"downgraded?", r"(?:misses?|missed) (?:estimates|expectations|forecasts?)", r"(?:revenue|earnings|sales) (?:down|falls?|fell|drops?|declines?)",
               r"turns? (?:to|into) (?:a )?loss", r"dividend (?:cut|suspended|slashed)"]
# 'record' only counts as "record profit / revenue ..." (POS_PHRASES): alone it is usually the verb ("records a loss")
POS = r"surge|soar|jump|rise|rises|rose|grow|grew|growth|beat|upgrade|expand|gain|higher|rebound|recover|boost|strong"
NEG = r"plunge|slump|fall|falls|fell|drop|decline|loss|losses|down|probe|default|downgrade|cut|lower|warn|weak|halt|suspend|fraud|penalt"
NEGATION = r"(?:no|not|never|without|fails? to)\s+(?:\w+\s+)?"

THEMES = [("oil_energy", r"\b(?:oil|brent|crude|opec|fuel|petrol|diesel|energy|electricity|power tariff|lng|coal)\b"),
          ("rupee_fx", r"\b(?:rupee|exchange rate|dollar|forex|remittances?|reserves)\b"),
          ("rates_inflation", r"\b(?:inflation|interest rates?|policy rates?|monetary policy|t-bills?|treasury bills?|yields?|ccpi|ncpi)\b"),
          ("imf_debt", r"\b(?:imf|debt|bonds?|restructuring|eff|creditors?|default)\b"),
          ("tax_policy", r"\b(?:tax|vat|budget|tariffs?|duty|levy)\b"),
          ("tourism", r"\b(?:tourism|tourists?|arrivals|hotels?)\b"),
          ("global_markets", r"\b(?:nasdaq|wall street|s&p|dow|stock markets?|stocks|shares|equities|selloff|sell-off)\b")]


def cfg() -> dict[str, Any]:
    base = {"enabled": True, "max_age_hours": 3, "aliases": {}, "google": True, "google_max_queries": 40, "google_window_days": 7,
            "google_themes": {
                "oil": {"q": "brent OR opec OR \"oil price\" OR \"crude oil\"", "region": "US"},
                "sri_lanka_economy": {"q": "\"Sri Lanka\" (rupee OR IMF OR \"central bank\" OR inflation OR budget OR economy)", "region": "LK"},
                "global_markets": {"q": "\"stock market\" (selloff OR rally OR plunge OR surge) OR \"Wall Street\"", "region": "US"},
            }}
    return {**base, **(settings().get("news_feeds") or {})}


# ---------------------------------------------------------------- reading a feed
def tone(text: str) -> float:
    """-1..1 from finance phrases (x2) and single words ('net profit surges 213-pct' -> positive, 'losses widen' -> negative,
    'no growth' -> negative)."""
    t = text.lower()
    p = 2 * sum(len(re.findall(rf"\b{x}\b", t)) for x in POS_PHRASES)
    n = 2 * sum(len(re.findall(rf"\b{x}\b", t)) for x in NEG_PHRASES)
    negated = len(re.findall(rf"\b{NEGATION}(?:{POS})\w*", t))
    p += len(re.findall(rf"\b(?:{POS})\w*", t)) - negated
    n += len(re.findall(rf"\b(?:{NEG})\w*", t)) + negated
    if p <= 0 and n <= 0:
        return 0.0
    p, n = max(p, 0), max(n, 0)
    return round(max(-1.0, min(1.0, (p - n) / (p + n))), 2)


def macro_theme(text: str) -> str:
    t = text.lower()
    return next((k for k, pat in THEMES if re.search(pat, t)), "none")


def _strip(s: str | None) -> str:
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return re.sub(r"\s+", " ", s).strip()


def parse_date(raw: str | None) -> datetime | None:
    """Colombo wall time (naive) from the formats the feeds use: RFC 2822 ('Thu, 05 Nov 2026 18:07:25 +0530'), ISO
    ('2026-11-05 12:00:00', Daily Mirror) and 'Friday, 6 November 2026 00:36' (Daily FT). None when none fits."""
    s = re.sub(r"\s+", " ", (raw or "").strip())
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
        if d is not None:
            return d.astimezone(TZ).replace(tzinfo=None) if d.tzinfo else d
    except (TypeError, ValueError, IndexError):
        pass
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d.astimezone(TZ).replace(tzinfo=None) if d.tzinfo else d
    except ValueError:
        pass
    for fmt in ("%A, %d %B %Y %H:%M", "%A, %d %B %Y", "%d %B %Y %H:%M", "%d %b %Y %H:%M", "%B %d, %Y %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def parse_rss(xml: str, source: str) -> list[dict[str, Any]]:
    items = []
    for it in re.findall(r"<item\b.*?</item>", xml, flags=re.S | re.I):
        def tag(name: str) -> str:
            m = re.search(rf"<{name}\b[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{name}>", it, flags=re.S | re.I)
            return m.group(1).strip() if m else ""
        title, link = _strip(tag("title")).lstrip("|-–— ").strip(), _strip(tag("link"))
        if not title or not link:
            continue
        outlet = _strip(tag("source")) or None              # Google News names the publisher here
        if outlet and title.endswith(" - " + outlet):
            title = title[: -len(outlet) - 3].rstrip()
        pub = parse_date(tag("pubDate")) or parse_date(tag("dc:date")) or parse_date(tag("updated"))
        summary = "" if source == "Google News" else _strip(tag("description"))[:600]   # Google's description just repeats links
        items.append({"id": hashlib.sha1(link.encode()).hexdigest()[:40], "source": source, "title": title[:400], "link": link[:600],
                      "published": pub, "summary": summary, "outlet": (outlet or source)[:80]})
    return items


def _norm(name: str) -> str:
    n = re.sub(r"\b(PLC|LIMITED|LTD)\b\.?", " ", name or "", flags=re.I)
    return re.sub(r"\s+", " ", n).strip()


def search_name(name: str) -> str:
    """The name people write: 'L O L C HOLDINGS PLC' -> 'LOLC HOLDINGS', 'JOHN KEELLS HOLDINGS PLC' -> 'JOHN KEELLS HOLDINGS'."""
    toks, out, run = _norm(name).split(), [], ""
    for t in toks:
        if len(t) == 1 and t.isalpha():
            run += t
            continue
        if run:
            out.append(run)
            run = ""
        out.append(t)
    if run:
        out.append(run)
    return " ".join(out)


def matchers(secs: list[tuple[str, str]], aliases: bool = True) -> dict[str, list[re.Pattern]]:
    """Per symbol: full company name (case-insensitive), the ticker (case-sensitive) and any aliases from
    settings (news_feeds.aliases: {JKH: ["John Keells Holdings"]}). Word boundaries only. Google News results (from
    anywhere in the world) are matched without the aliases: 'Commercial Bank' alone also names Qatar's."""
    extra = {k.upper(): v for k, v in (cfg().get("aliases") or {}).items()} if aliases else {}
    out = {}
    for sym, name in secs:
        tick = sym.split(".")[0]
        names = {n for n in (_norm(name), search_name(name)) if n}
        pats = [re.compile(rf"(?<![\w&]){re.escape(n)}(?![\w&])", re.I) for n in sorted(names)]
        pats.append(re.compile(rf"(?<![\w.]){re.escape(tick)}(?![\w])"))
        pats += [re.compile(rf"(?<![\w&]){re.escape(a)}(?![\w&])", re.I) for a in extra.get(tick, [])]
        out[sym] = pats
    return out


def match(text: str, pats: dict[str, list[re.Pattern]], own: dict[str, str], listed: list[str] | None = None) -> list[str]:
    """Symbols a headline names. A hit that is only the start of another listed company's longer name
    ('John Keells' inside 'John Keells Hotels') is not a hit. `listed`: every CSE company name."""
    others = [_norm(n) for n in (listed or [])]
    hits = []
    for sym, ps in pats.items():
        mine = _norm(own.get(sym, "")).lower()
        for p in ps:
            for m in p.finditer(text):
                rest = text[m.start():].lower()
                longer = any(o.lower() != mine and len(o) > len(m.group(0)) and o.lower().startswith(m.group(0).lower() + " ")
                             and rest.startswith(o.lower()) for o in others)
                if not longer:
                    hits.append(sym)
                    break
            if sym in hits:
                break
    return hits


def subject_of(text: str, sym: str, pats: dict[str, list[re.Pattern]]) -> bool:
    """For the keyword reader: is the company the headline's subject? Its name must come before the first tone word
    ('JKH profit surges' yes; 'Banks rally as JKH ...' no)."""
    first = min((m.start() for p in pats.get(sym, []) for m in p.finditer(text)), default=None)
    if first is None:
        return False
    tone_at = [m.start() for m in re.finditer(rf"\b(?:{POS}|{NEG})\w*", text.lower())]
    return not tone_at or first < min(tone_at)


# ---------------------------------------------------------------- the same story from several outlets
_STOP = set("a an the of to in on for and or as at by with from is are be its it's this that after over into up down vs "
            "sri lanka lankan plc ltd rs mn bn per cent pct".split())


def _tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in _STOP and len(w) > 1}


def same_story(a: str, b: str, threshold: float = 0.6) -> bool:
    x, y = _tokens(a), _tokens(b)
    if not x or not y:
        return False
    return len(x & y) / min(len(x), len(y)) >= threshold


def assign_clusters(s: Session, rows: list[Headline], days: int = 3) -> None:
    """Give each headline without a cluster the cluster of an earlier headline telling the same story (within `days`),
    or its own id."""
    pending = [h for h in rows if not h.cluster]
    if not pending:
        return
    since = min((h.published or datetime.now()) for h in pending) - timedelta(days=days)
    known = [h for h in s.scalars(select(Headline).where(Headline.published >= since, Headline.cluster.is_not(None)))]
    for h in sorted(pending, key=lambda x: x.published or datetime.now()):
        near = [k for k in known if k.published and h.published and abs((k.published - h.published).days) <= days]
        hit = next((k for k in near if same_story(k.title, h.title)), None)
        h.cluster = hit.cluster if hit else h.id
        known.append(h)


# ---------------------------------------------------------------- reading a headline for one stock
def stock_reading(h: Headline, symbol: str, pats: dict[str, list[re.Pattern]] | None = None) -> float | None:
    """The headline's tone for this stock, or None when it does not count for it. Claude's reading wins when present:
    only a headline that is *about* the company counts (a passing mention does not). Without it, the keyword tone counts
    when the company is the headline's subject."""
    llm = h.llm or {}
    if llm.get("by"):
        c = next((c for c in llm.get("companies") or [] if c.get("symbol") == symbol), None)
        return float(c["sentiment"]) if c and c.get("about") == "primary" else None
    if symbol not in (h.symbols or []):
        return None
    if pats is not None and not subject_of(h.title, symbol, pats):
        return None
    return float(h.tone or 0.0)


# ---------------------------------------------------------------- fetching
_http: httpx.Client | None = None


def _get(url: str) -> str:
    """IPv4 only: on a PC without a working IPv6 route each IPv6 address would first wait out the whole timeout."""
    global _http
    if _http is None:
        _http = httpx.Client(headers=_UA, timeout=httpx.Timeout(12.0, connect=6.0), follow_redirects=True,
                             transport=httpx.HTTPTransport(local_address="0.0.0.0", retries=1))
    r = _http.get(url)
    r.raise_for_status()
    try:                                  # some feeds declare UTF-8 but send Windows-1252 quotes
        return r.content.decode("utf-8")
    except UnicodeDecodeError:
        return r.content.decode("cp1252", errors="replace")


def google_url(q: str, region: str = "LK") -> str:
    hl = {"LK": ("en-LK", "LK", "LK:en"), "US": ("en-US", "US", "US:en")}.get(region, ("en-LK", "LK", "LK:en"))
    return GOOGLE + "?" + urlencode({"q": q, "hl": hl[0], "gl": hl[1], "ceid": hl[2]})


def _status(s: Session) -> dict[str, Any]:
    p = s.get(Preference, STATUS_KEY)
    return dict(p.value) if p and isinstance(p.value, dict) else {}


def feed_status(s: Session) -> dict[str, Any]:
    """Health of each source: last success, items, last error (shown on the market page)."""
    return {"feeds": _status(s), "not_used": NOT_USED}


def _note(st: dict[str, Any], name: str, ok: bool, items: int = 0, error: str | None = None) -> None:
    now = now_local().replace(tzinfo=None).isoformat(timespec="minutes")
    prev = st.get(name) or {}
    st[name] = {**prev, "checked": now, "ok": ok, "items": items if ok else prev.get("items", 0),
                "last_ok": now if ok else prev.get("last_ok"), "error": None if ok else (error or "failed")[:160]}


def _store(s: Session, items: list[dict[str, Any]], kind: str, pats, names, listed, fetched: datetime, strict=None) -> list[Headline]:
    rows = []
    for it in items:
        use = strict if (strict is not None and it.get("source") == "Google News") else pats
        h = s.get(Headline, it["id"])
        if h is None:
            h = Headline(**{k: v for k, v in it.items() if k != "published"}, published=it["published"] or fetched,
                         fetched_at=datetime.utcnow(), kind=kind)
        elif kind == "company" and h.kind in (None, "local"):
            h.kind = "company"
        # the title only: market wraps ("Sri Lanka stocks gain ...") name many companies in their summary
        h.symbols = match(h.title, use, names, listed)
        h.tone = tone(h.title)
        s.add(h)
        rows.append(h)
    return rows


def sources(secs: list[tuple[str, str]], google: bool = True) -> list[dict[str, str]]:
    """Every feed to read this time: the fixed feeds, then (nightly) one Google News search per tracked company and the
    world-theme searches."""
    c = cfg()
    out = [dict(f) for f in FEEDS]
    if google and c.get("google", True):
        win = int(c.get("google_window_days", 7))
        for sym, name in secs[: int(c.get("google_max_queries", 40))]:
            # the full name only: a short alias ("Commercial Bank") finds other countries' companies worldwide
            out.append({"name": f"Google News: {sym.split('.')[0]}", "source": "Google News", "kind": "company",
                        "url": google_url(f'"{search_name(name).title()}" when:{win}d', "LK")})
        for key, th in (c.get("google_themes") or {}).items():
            out.append({"name": f"Google News: {key.replace('_', ' ')}", "source": "Google News", "kind": "global",
                        "url": google_url(f"{th['q']} when:2d", th.get("region", "US")), "limit": str(th.get("limit", 15))})
    return out


def collect(feeds: list[dict[str, str]], pause: float = 1.5, budget: float = 180.0, get: Any = None) -> list[dict[str, Any]]:
    """Network only (no database): read each feed, politely spaced for Google. One result per feed: items or error.
    Stops Google searches after two failures in a row (throttled or unreachable) and everything after `budget` seconds,
    so a network problem can never stall the evening job."""
    get = get or _get
    out, start, google_fails = [], time.monotonic(), 0
    for i, f in enumerate(feeds):
        google = f["source"] == "Google News"
        if time.monotonic() - start > budget or (google and google_fails >= 2):
            out.append({**f, "items": None, "error": "skipped: time budget used" if time.monotonic() - start > budget
                        else "skipped: Google News did not answer twice in a row"})
            continue
        if i and google:
            time.sleep(pause)
        try:
            items = parse_rss(get(f["url"]), f["source"])
            if f.get("limit"):                     # world-theme searches return ~100 items: keep the newest few
                items = sorted(items, key=lambda x: x["published"] or datetime.min, reverse=True)[: int(f["limit"])]
            out.append({**f, "items": items})
            google_fails = 0 if google else google_fails
        except Exception as exc:  # noqa: BLE001
            code = getattr(getattr(exc, "response", None), "status_code", None)
            log.warning("feed %s failed: %s", f["name"], exc)
            out.append({**f, "items": None, "error": f"HTTP {code}" if code else (str(exc) or type(exc).__name__)[:160]})
            google_fails += 1 if google else 0
    return out


def _tracked(symbols: list[str]) -> list[tuple[str, str]]:
    with SessionLocal() as s:
        return [(x.symbol, x.name) for x in s.scalars(select(Security).where(Security.symbol.in_(symbols)))]


def store_results(s: Session, symbols: list[str], results: list[dict[str, Any]]) -> dict[str, Any]:
    """Write what collect() read: new headlines, (re)matched stocks, same-story groups, each source's health."""
    from .cse_client import client
    secs = [(x.symbol, x.name) for x in s.scalars(select(Security).where(Security.symbol.in_(symbols)))]
    names, pats, strict = dict(secs), matchers(secs), matchers(secs, aliases=False)
    try:
        listed = sorted({r.get("name") or "" for r in client().trade_summary()} - {""})
    except Exception:
        listed = []
    st = _status(s)
    fetched = now_local().replace(tzinfo=None)
    got, new, rows = 0, 0, []
    for r in results:
        if r["items"] is None:
            _note(st, r["name"], False, error=r.get("error"))
            continue
        _note(st, r["name"], True, len(r["items"]))
        got += len(r["items"])
        new += sum(1 for it in r["items"] if s.get(Headline, it["id"]) is None)
        rows.extend(_store(s, r["items"], r["kind"], pats, names, listed, fetched, strict))
    s.flush()
    assign_clusters(s, rows)
    s.merge(Preference(key=STATUS_KEY, value=st))
    _last["at"] = time.monotonic()
    return {"items": got, "new": new, "sources": len(results), "failed": [r["name"] for r in results if r["items"] is None]}


def fetch(s: Session | None, symbols: list[str], google: bool = True) -> dict[str, Any]:
    """Read every source, then store. The reading happens before any write, so a slow feed never holds the database's
    write lock. `s` is the caller's session for the writes (one is opened when None)."""
    results = collect(sources(_tracked(symbols), google))
    if s is not None:
        return store_results(s, symbols, results)
    with SessionLocal.begin() as s2:
        return store_results(s2, symbols, results)


def refresh_async(symbols: list[str]) -> None:
    """On a page visit, when the last fetch is older than news_feeds.max_age_hours: the fixed feeds only, in the
    background (the per-company Google searches run with the evening job)."""
    if not cfg()["enabled"] or time.monotonic() - _last["at"] < float(cfg()["max_age_hours"]) * 3600:
        return
    if not _lock.acquire(blocking=False):
        return

    def run() -> None:
        try:
            fetch(None, symbols, google=False)
        except Exception as exc:
            log.warning("headline refresh failed: %s", exc)
        finally:
            _lock.release()
    threading.Thread(target=run, daemon=True, name="headlines").start()
