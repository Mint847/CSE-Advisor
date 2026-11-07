"""Sector, industry and market cap for every listed stock, from TradingView's public scanner (one request).

The CSE's own feed has no sector. TradingView's scanner for the Sri Lanka market lists all ~295 securities with a sector
(Finance, Consumer Services...), an industry and a market cap. It is unofficial and can change, so a failure is only
recorded (the heatmap then shows what it already has) and never stops the evening run. Refreshed weekly: sectors rarely
change. This is display-only: nothing here feeds an engine score."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import ListedMeta, SessionLocal

log = logging.getLogger(__name__)

URL = "https://scanner.tradingview.com/srilanka/scan"
COLUMNS = ["name", "description", "sector", "industry", "market_cap_basic"]
MAX_AGE_DAYS = 6


def fetch(timeout: float = 30) -> list[dict[str, Any]]:
    r = httpx.post(URL, json={"columns": COLUMNS, "range": [0, 600], "markets": ["srilanka"]}, timeout=timeout,
                   headers={"User-Agent": "Mozilla/5.0 (CSE Advisor personal tool)"})
    r.raise_for_status()
    out = []
    for row in r.json().get("data", []):
        d = row.get("d") or []
        if len(d) < len(COLUMNS) or not d[0]:
            continue
        out.append({"symbol": str(d[0]), "name": d[1], "sector": d[2] or None, "industry": d[3] or None,
                    "market_cap": float(d[4]) if d[4] else None})
    return out


def refresh(s: Session, rows: list[dict[str, Any]] | None = None) -> dict[str, int]:
    rows = fetch() if rows is None else rows
    if not rows:
        raise RuntimeError("TradingView returned no securities")
    have = {m.symbol: m for m in s.scalars(select(ListedMeta))}
    now = datetime.utcnow()
    added = 0
    for r in rows:
        m = have.get(r["symbol"])
        if m is None:
            m = ListedMeta(symbol=r["symbol"])
            s.add(m)
            added += 1
        m.name, m.sector, m.industry, m.market_cap, m.updated_at = r["name"], r["sector"], r["industry"], r["market_cap"], now
    return {"stocks": len(rows), "new": added, "with_sector": sum(1 for r in rows if r["sector"])}


def age_days(s: Session) -> float | None:
    t = s.scalar(select(func.max(ListedMeta.updated_at)))
    return (datetime.utcnow() - t).total_seconds() / 86400 if t else None


def top_up(max_age_days: float = MAX_AGE_DAYS) -> dict[str, Any]:
    """Refresh when empty or older than a week; otherwise do nothing."""
    with SessionLocal() as s:
        age = age_days(s)
    if age is not None and age < max_age_days:
        return {"skipped": f"fresh ({age:.1f} days old)"}
    with SessionLocal.begin() as s:
        return refresh(s)


def lookup(s: Session) -> dict[str, dict[str, Any]]:
    return {m.symbol: {"sector": m.sector, "industry": m.industry, "market_cap": m.market_cap} for m in s.scalars(select(ListedMeta))}
