"""Business headlines from free RSS feeds, matched to watchlist companies, with a keyword tone.

Feeds checked 2026-11-04: EconomyNext (20 items, names CSE companies and results) and Daily Mirror business
(12 items). Daily FT's RSS was empty; Google News and CBSL timed out, so they are not used. Only the title,
link, date and the feed's own short summary are stored; articles are linked, never copied. Headlines are
lightly weighted in the news factor and can never raise a red flag.
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
import threading
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .cse_client import TZ
from .db import Headline, Security, SessionLocal

log = logging.getLogger(__name__)

FEEDS = [("EconomyNext", "https://economynext.com/feed/"), ("Daily Mirror", "https://www.dailymirror.lk/rss/business/273")]
_UA = {"User-Agent": "Mozilla/5.0 (personal CSE advisor; low-frequency)"}
POS = r"surge|soar|jump|rise|rises|rose|grow|grew|growth|record|beat|upgrade|expand|gain|higher|rebound|recover|profit up|boost|strong"
NEG = r"plunge|slump|fall|falls|fell|drop|decline|loss|losses|down|probe|default|downgrade|cut|lower|warn|weak|halt|suspend|fraud|penalt"
_last = {"at": 0.0}
_lock = threading.Lock()


def cfg() -> dict[str, Any]:
    return {"enabled": True, "max_age_hours": 3, "aliases": {}, **(settings().get("news_feeds") or {})}


def tone(text: str) -> float:
    """-1..1 from simple keyword counts ('net profit surges 213-pct' -> positive)."""
    t = text.lower()
    p = len(re.findall(rf"\b({POS})\w*", t))
    n = len(re.findall(rf"\b({NEG})\w*", t))
    if p == n == 0:
        return 0.0
    return round(max(-1.0, min(1.0, (p - n) / (p + n))), 2)


def _strip(s: str | None) -> str:
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return re.sub(r"\s+", " ", s).strip()


def parse_rss(xml: str, source: str) -> list[dict[str, Any]]:
    items = []
    for it in re.findall(r"<item\b.*?</item>", xml, flags=re.S | re.I):
        def tag(name: str) -> str:
            m = re.search(rf"<{name}\b[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{name}>", it, flags=re.S | re.I)
            return m.group(1).strip() if m else ""
        title, link = _strip(tag("title")), _strip(tag("link"))
        if not title or not link:
            continue
        try:
            pub = parsedate_to_datetime(tag("pubDate")).astimezone(TZ).replace(tzinfo=None)
        except Exception:
            pub = None
        items.append({"id": hashlib.sha1(link.encode()).hexdigest()[:40], "source": source, "title": title[:400], "link": link[:600],
                      "published": pub, "summary": _strip(tag("description"))[:600]})
    return items


def _norm(name: str) -> str:
    n = re.sub(r"\b(PLC|LIMITED|LTD)\b\.?", " ", name or "", flags=re.I)
    return re.sub(r"\s+", " ", n).strip()


def matchers(secs: list[tuple[str, str]]) -> dict[str, list[re.Pattern]]:
    """Per symbol: full company name (case-insensitive), the ticker (case-sensitive) and any aliases from
    settings (news_feeds.aliases: {JKH: ["John Keells Holdings"]}). Word boundaries only."""
    extra = {k.upper(): v for k, v in (cfg().get("aliases") or {}).items()}
    out = {}
    for sym, name in secs:
        tick = sym.split(".")[0]
        pats = [re.compile(rf"(?<![\w&]){re.escape(_norm(name))}(?![\w&])", re.I)] if _norm(name) else []
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


def fetch(s: Session, symbols: list[str]) -> dict[str, Any]:
    """Fetch every feed once, store new headlines and (re)match all recent ones to the watchlist."""
    from .cse_client import client
    secs = [(x.symbol, x.name) for x in s.scalars(select(Security).where(Security.symbol.in_(symbols)))]
    names = dict(secs)
    pats = matchers(secs)
    try:
        listed = sorted({r.get("name") or "" for r in client().trade_summary()} - {""})
    except Exception:
        listed = []
    got, new = 0, 0
    for source, url in FEEDS:
        try:
            r = httpx.get(url, headers=_UA, timeout=httpx.Timeout(12.0), follow_redirects=True)
            r.raise_for_status()
            items = parse_rss(r.text, source)
        except Exception as exc:
            log.warning("feed %s failed: %s", source, exc)
            continue
        got += len(items)
        for it in items:
            h = s.get(Headline, it["id"])
            if h is None:
                h = Headline(**it, fetched_at=datetime.utcnow())
                new += 1
            # the title only: market wraps ("Sri Lanka stocks gain ...") name many companies in their summary
            h.symbols = match(h.title, pats, names, listed)
            h.tone = tone(h.title)
            s.add(h)
    _last["at"] = time.monotonic()
    return {"items": got, "new": new}


def refresh_async(symbols: list[str]) -> None:
    """Fetch in the background when the last fetch is older than news_feeds.max_age_hours."""
    if not cfg()["enabled"] or time.monotonic() - _last["at"] < float(cfg()["max_age_hours"]) * 3600:
        return
    if not _lock.acquire(blocking=False):
        return

    def run() -> None:
        try:
            with SessionLocal.begin() as s:
                fetch(s, symbols)
        except Exception as exc:
            log.warning("headline refresh failed: %s", exc)
        finally:
            _lock.release()
    threading.Thread(target=run, daemon=True, name="headlines").start()
