"""World series beside the CSE: Brent crude, Nasdaq-100, S&P 500, USD/LKR, gold and India's Nifty 50.

Daily closes come from TradingView's chart feed (the same client as the CSE history, one request per series, ~2 s
apart): 5,000 bars once, then a small top-up each evening. They are shown on the MKT page with the link each one has
had to the ASPI (macro_study.py measures it), written into the forward-test journal, and used for the overnight note.
No engine score uses them: in the 14-year study none held up well enough to.

Dates: a US or oil session dated D is followed by the CSE session after D (Colombo closes before New York opens).
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import DATA_DIR, settings
from .db import Headline, MacroDaily, MarketDaily

log = logging.getLogger(__name__)

SERIES: dict[str, dict[str, str]] = {
    "brent": {"symbol": "UKOIL", "exchange": "TVC", "name": "Brent crude", "unit": "USD/bbl"},
    "ndx": {"symbol": "NDX", "exchange": "NASDAQ", "name": "Nasdaq-100", "unit": ""},
    "spx": {"symbol": "SPX", "exchange": "SP", "name": "S&P 500", "unit": ""},
    "usdlkr": {"symbol": "USDLKR", "exchange": "FX_IDC", "name": "USD/LKR", "unit": "LKR"},
    "gold": {"symbol": "GOLD", "exchange": "TVC", "name": "Gold", "unit": "USD/oz"},
    "nifty": {"symbol": "NIFTY", "exchange": "NSE", "name": "Nifty 50 (India)", "unit": ""},
}
LINKS_FILE = DATA_DIR / "backtests" / "macro_links.json"


def cfg() -> dict[str, Any]:
    c = {"enabled": True, "series": SERIES, "shock_us_pct": -2.0, "shock_oil_pct": 5.0, **(settings().get("macro") or {})}
    c["series"] = {**SERIES, **(c.get("series") or {})}
    return c


# ---------------------------------------------------------------- storing
def _bars(code: str, n: int) -> list[dict[str, Any]]:
    from . import tv_client
    sp = cfg()["series"][code]
    return tv_client.fetch_bars(sp["symbol"], "1D", n, exchange=sp["exchange"])


def store(s: Session, code: str, bars: list[dict[str, Any]]) -> int:
    """Upsert daily closes (the last bar of a day wins; a bar still forming today is overwritten tomorrow)."""
    by: dict[date, float] = {}
    for b in bars:
        try:
            d, c = pd.Timestamp(b["ts"]).date(), float(b["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if c > 0:
            by[d] = c
    if not by:
        return 0
    have = {r.date: r for r in s.scalars(select(MacroDaily).where(MacroDaily.code == code, MacroDaily.date >= min(by)))}
    for d, c in by.items():
        r = have.get(d)
        if r is None:
            s.add(MacroDaily(code=code, date=d, close=c))
        else:
            r.close = c
    return len(by)


def top_up(s: Session, n: int = 15, fetch: Any = None) -> dict[str, int]:
    """Each series: the full history the first time (fewer than 1,000 rows stored), else the last `n` bars."""
    if not cfg()["enabled"]:
        return {}
    fetch = fetch or _bars
    codes = list(cfg()["series"])
    have = {c: s.scalar(select(func.count()).select_from(MacroDaily).where(MacroDaily.code == c)) or 0 for c in codes}
    got: dict[str, list[dict[str, Any]]] = {}
    for code in codes:                          # network first: nothing is written while waiting on TradingView
        try:
            got[code] = fetch(code, 5000 if have[code] < 1000 else n)
        except Exception as exc:  # noqa: BLE001 - one series failing must not stop the others
            log.warning("macro %s: %s", code, exc)
            got[code] = []
    return {code: store(s, code, bars) for code, bars in got.items()}


# ---------------------------------------------------------------- reading
def series(s: Session, code: str, upto: date | None = None) -> pd.Series:
    q = select(MacroDaily.date, MacroDaily.close).where(MacroDaily.code == code)
    if upto is not None:
        q = q.where(MacroDaily.date <= upto)
    rows = s.execute(q.order_by(MacroDaily.date)).all()
    return pd.Series({pd.Timestamp(d): c for d, c in rows}, dtype=float).sort_index()


def _chg(x: pd.Series, days: int) -> float | None:
    if len(x) < 2:
        return None
    ref = x[x.index <= x.index[-1] - pd.Timedelta(days=days)]
    if ref.empty:
        return None
    return round((x.iloc[-1] / ref.iloc[-1] - 1) * 100, 2)


def snapshot(s: Session, upto: date | None = None, spark: int = 0) -> dict[str, dict[str, Any]]:
    """Per series: last close and date, % change over 1 session, 1 week and 1 month (calendar), optional sparkline."""
    out = {}
    for code, sp in cfg()["series"].items():
        x = series(s, code, upto)
        if x.empty:
            continue
        d1 = round((x.iloc[-1] / x.iloc[-2] - 1) * 100, 2) if len(x) > 1 else None
        row = {"name": sp["name"], "unit": sp.get("unit", ""), "last": round(float(x.iloc[-1]), 4), "date": x.index[-1].date().isoformat(),
               "chg1d": d1, "chg1w": _chg(x, 7), "chg1m": _chg(x, 30)}
        if spark:
            row["spark"] = [round(float(v), 4) for v in x.tail(spark).to_numpy()]
        out[code] = row
    return out


def links() -> dict[str, Any]:
    """The measured links to the ASPI (written by macro_study.py); {} until the study has run."""
    try:
        return json.loads(LINKS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def overnight_note(s: Session, snap: dict[str, dict[str, Any]] | None = None, lk: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """A note when the US market (Nasdaq-100 or S&P 500) closed down at least 2%, or Brent moved 5%+, in a session the
    CSE has not traded after yet. It quotes what followed such days in the study. Description only."""
    c = cfg()
    snap = snap if snap is not None else snapshot(s)
    lk = lk if lk is not None else links()
    last_cse = s.scalar(select(func.max(MarketDaily.date)).where(MarketDaily.aspi.is_not(None)))
    notes = []
    for code in ("ndx", "spx"):
        r = snap.get(code)
        if r and r["chg1d"] is not None and r["chg1d"] <= c["shock_us_pct"] and (last_cse is None or date.fromisoformat(r["date"]) >= last_cse):
            ev = (lk.get("events") or {}).get("us_down_2")
            txt = f"{r['name']} {r['chg1d']:+.1f}% in its {r['date']} session, after the CSE's last close."
            if ev and ev.get("n"):
                txt += (f" In {ev['period']}, the CSE session after a US fall of 2% or more averaged {ev['next_mean']:+.2f}% and fell on "
                        f"{ev['next_down_pct']:.0f}% of {ev['n']} such days (vs {ev['base_down_pct']:.0f}% on all days)")
                if ev.get("next5_mean") is not None:
                    txt += f"; over the next five sessions it averaged {ev['next5_mean']:+.2f}% (all weeks {ev.get('base5_mean', 0):+.2f}%)"
                txt += "."
            notes.append({"series": code, "text": txt})
            break
    r = snap.get("brent")
    if r and r["chg1d"] is not None and abs(r["chg1d"]) >= c["shock_oil_pct"] and (last_cse is None or date.fromisoformat(r["date"]) >= last_cse):
        txt = f"Brent crude {r['chg1d']:+.1f}% ({r['date']})."
        b = (lk.get("series") or {}).get("brent")
        if b:
            txt += f" Oil's measured link to the ASPI: {b.get('verdict_text', 'none')}."
        notes.append({"series": "brent", "text": txt})
    if not notes:
        return None
    return {"notes": notes, "note": "Description only, not a signal: no engine uses world markets."}


def news_tone(s: Session, upto: date | None = None, days: int = 3) -> dict[str, Any]:
    """World and economy headlines of the last `days` (Claude's reading where present): count, mean tone for Sri Lankan
    shares, and the themes. Recorded in the journal; no score uses it."""
    end = pd.Timestamp(upto or date.today()) + pd.Timedelta(days=1)
    rows = s.scalars(select(Headline).where(Headline.kind.in_(("global", "macro")), Headline.published >= end - pd.Timedelta(days=days + 1),
                                            Headline.published < end)).all()
    read = [h.llm for h in rows if (h.llm or {}).get("by")]
    themes: dict[str, int] = {}
    for l in read:
        t = l.get("macro_theme") or "none"
        themes[t] = themes.get(t, 0) + 1
    tones = [float(l.get("sl_market_tone") or 0) for l in read]
    return {"n": len(rows), "read": len(read), "tone": round(float(np.mean(tones)), 3) if tones else None, "themes": themes}


def journal_context(s: Session, upto: date) -> dict[str, Any]:
    """What the world looked like on the evening of a call, for the journal: world sessions dated before the call's day
    only (a US session dated D starts after the CSE's evening run on D, so including it would be hindsight)."""
    snap = snapshot(s, upto - timedelta(days=1))
    return {"world": {k: {"chg1d": v["chg1d"], "chg1w": v["chg1w"], "chg1m": v["chg1m"], "date": v["date"]} for k, v in snap.items()},
            "world_news": news_tone(s, upto)}
