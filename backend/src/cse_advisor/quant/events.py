"""Event studies: what a price did around dated events, measured against the ASPI, with the sample size and a t-statistic.

Two kinds of event:
* company events: an announcement of a given kind (dividend, rights issue, director dealing...), an ex-dividend date, or a
  results upload, for one stock or all the stocks the app tracks. The response is that stock's total return minus the
  ASPI's on the same sessions (an "abnormal" return: what the stock did beyond the market).
* world shocks: a day on which a series (S&P 500, Brent, USD/LKR, a world stock...) moved by more than a threshold; the
  response is the ASPI's (or a CSE stock's) over the next sessions. gs-quant's event_impact_analysis finds the shock days
  and maps them onto the CSE's calendar; series that close after the CSE (the US, Europe, India, commodities) react from the
  next session, series that close before it (Korea, Japan, Hong Kong...) and USD/LKR from the same one.

Day 0 is the first session that could react (an announcement after the 14:30 close counts from the next session). Events
within LOCKOUT sessions of the previous one in the same stock are dropped so windows do not overlap; events of different
stocks on the same session are averaged before the t-statistic (they share the market's mood). A t-statistic above about 2
(or below -2) is the usual bar for "probably not chance"; with fewer than MIN_N independent dates nothing is claimed.
"""
from __future__ import annotations

import math
from datetime import datetime, time as dtime
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import Announcement, FinancialReport, GlobalStock, MacroDaily
from . import series as S
from .stats_tests import nw_t

BEFORE, AFTER = 5, 20               # sessions shown before day 0 and after it
LOCKOUT = AFTER                     # events of one stock closer than this are one episode
MIN_N = 8                           # independent dates needed before the page says anything
HORIZONS = (0, 1, 4, 9, 19)         # CAR over days 0..h: 1, 2, 5, 10 and 20 sessions
CLOSE = dtime(14, 30)
SAME_DAY_EXCHANGES = {"KRX", "TSE", "TYO", "JPX", "HKEX", "SSE", "SZSE", "TWSE", "TPEX", "ASX"}   # they close before the CSE does

CATEGORY_LABELS = {
    "dividend": "Dividend announced", "rights_issue": "Rights issue", "share_split": "Share split / sub-division", "bonus_issue": "Bonus issue",
    "director_dealing": "Director dealing", "management_change": "Management change", "management_exit": "Management exit", "mna": "Merger / acquisition",
    "debt_issue": "Debt issue", "rating": "Credit rating", "red_flag": "Red-flag announcement", "non_compliance": "Non-compliance notice", "esos": "Share option scheme",
    "related_party": "Related-party transaction", "disposal": "Disposal", "dilution": "Dilution", "halt": "Trading halt", "suspension": "Suspension", "listing": "Listing",
}
EXTRA_KINDS = {"ex_dividend": "Ex-dividend date", "results": "Financial results published"}
MACRO_SOURCES = {"^SPX": "S&P 500", "^NDX": "Nasdaq-100", "^BRENT": "Brent crude", "^GOLD": "Gold", "^NIFTY": "Nifty 50 (India)", "^USDLKR": "USD/LKR"}


# ------------------------------------------------------------------ what the page offers
def catalog(s: Session, symbols: list[str]) -> dict[str, Any]:
    counts = dict(s.execute(select(Announcement.category, func.count()).where(Announcement.symbol.in_(symbols)).group_by(Announcement.category)).all()) if symbols else {}
    kinds = [{"id": k, "label": CATEGORY_LABELS.get(k, k.replace("_", " ").capitalize()), "count": int(n)} for k, n in sorted(counts.items(), key=lambda kv: -kv[1])
             if k and n >= 5 and k not in ("other", "disclosure", "meeting", "circular", "errata")]
    xd = sum(len(S.recorded_dividends(s, x)) for x in symbols)
    kinds += [{"id": "ex_dividend", "label": EXTRA_KINDS["ex_dividend"], "count": xd}]
    res = int(s.scalar(select(func.count()).select_from(FinancialReport).where(FinancialReport.symbol.in_(symbols))) or 0) if symbols else 0
    kinds += [{"id": "results", "label": EXTRA_KINDS["results"], "count": res}]
    have = {c for (c,) in s.execute(select(MacroDaily.code).distinct())}
    shocks = [{"id": k, "label": v} for k, v in MACRO_SOURCES.items() if k[1:].lower() in have]
    world = [{"id": g.symbol, "label": f"{g.symbol.split(':')[1]} · {g.name}"} for g in s.scalars(select(GlobalStock).order_by(GlobalStock.symbol))]
    return {"company_kinds": kinds, "shock_sources": shocks, "world_stocks": world, "before": BEFORE, "after": AFTER, "min_n": MIN_N}


def source_info(source: str) -> tuple[str, bool]:
    """(label, reacts the same CSE session): a source's close vs the CSE's 14:30 close."""
    if source in MACRO_SOURCES:
        return MACRO_SOURCES[source], source == "^USDLKR"
    if ":" in source:
        return source.split(":")[1], source.split(":")[0].upper() in SAME_DAY_EXCHANGES
    return source, False


# ------------------------------------------------------------------ the framing
def frame(ar: np.ndarray, idxs: list[int], before: int = BEFORE, after: int = AFTER) -> np.ndarray:
    """[event, offset -before..+after] of daily abnormal returns around session indices; NaN where the data does not reach."""
    n = len(ar)
    out = np.full((len(idxs), before + after + 1), np.nan)
    for r, i in enumerate(idxs):
        lo, hi = max(i - before, 0), min(i + after, n - 1)
        out[r, lo - (i - before):hi - (i - before) + 1] = ar[lo:hi + 1]
    return out


def lockout(idxs: list[int], gap: int = LOCKOUT) -> list[int]:
    """Keep the first of any events closer than `gap` sessions (one episode, counted once)."""
    keep: list[int] = []
    for i in sorted(set(idxs)):
        if not keep or i - keep[-1] >= gap:
            keep.append(i)
    return keep


def aggregate(rows: list[dict[str, Any]], mats: np.ndarray) -> dict[str, Any]:
    """Path and statistics from the event x offset matrix; `rows[i]["date"]` is the reaction session. Events on the same session are averaged
    first, so the t-statistic counts independent dates."""
    dates = np.array([r["date"] for r in rows])
    uniq = sorted(set(dates))
    by_date = np.array([np.nanmean(mats[dates == d], axis=0) if np.isfinite(mats[dates == d]).any() else np.full(mats.shape[1], np.nan) for d in uniq])
    zero = BEFORE                                                         # column of day 0
    # path: cumulative abnormal return from the first shown day; NaN while the data doesn't reach
    csum = np.cumsum(by_date, axis=1)
    path = []
    for k in range(by_date.shape[1]):
        col = csum[:, k]
        ok = np.isfinite(col)
        n = int(ok.sum())
        m = float(col[ok].mean()) if n else float("nan")
        se = float(col[ok].std(ddof=1) / math.sqrt(n)) if n > 1 else float("nan")
        path.append({"day": k - BEFORE, "mean": m, "se": se, "n": n})
    horizons = []
    for h in HORIZONS:
        seg = by_date[:, zero:zero + h + 1]
        car = np.where(np.isfinite(seg).all(axis=1), np.nansum(seg, axis=1), np.nan)
        ok = np.isfinite(car)
        n = int(ok.sum())
        m = float(car[ok].mean()) if n else float("nan")
        sd = float(car[ok].std(ddof=1)) if n > 1 else float("nan")
        t = m / (sd / math.sqrt(n)) if n > 1 and sd > 0 else float("nan")
        horizons.append({"sessions": h + 1, "mean": m, "median": float(np.median(car[ok])) if n else float("nan"), "positive": float((car[ok] > 0).mean()) if n else float("nan"),
                         "t": t, "n": n})
    pre = by_date[:, :zero]
    pre_car = np.where(np.isfinite(pre).all(axis=1), np.nansum(pre, axis=1), np.nan)
    ok = np.isfinite(pre_car)
    pre_t = float(np.nanmean(pre_car) / (np.nanstd(pre_car, ddof=1) / math.sqrt(ok.sum()))) if ok.sum() > 2 and np.nanstd(pre_car) > 0 else float("nan")
    return {"path": path, "horizons": horizons, "dates": len(uniq), "events": len(rows),
            "before": {"mean": float(np.nanmean(pre_car)) if ok.any() else float("nan"), "t": pre_t, "n": int(ok.sum())}}


# ------------------------------------------------------------------ events
def first_session(days: pd.DatetimeIndex, when: datetime | pd.Timestamp | str, can_be_after_close: bool = True) -> int | None:
    """First session that could react to something published at `when` (a date alone counts from that date)."""
    t = pd.Timestamp(when)
    d = t.normalize()
    i = int(days.searchsorted(d))
    if i < len(days) and days[i] == d and can_be_after_close and (t.hour, t.minute) >= (CLOSE.hour, CLOSE.minute):
        i += 1
    return i if i < len(days) else None


def company_events(s: Session, symbol: str, kind: str, days: pd.DatetimeIndex) -> list[int]:
    if kind == "ex_dividend":
        return [i for d in S.recorded_dividends(s, symbol) if (i := first_session(days, d["xd"], False)) is not None]
    if kind == "results":
        rows = s.scalars(select(FinancialReport.uploaded).where(FinancialReport.symbol == symbol, FinancialReport.uploaded.is_not(None))).all()
        return [i for t in rows if (i := first_session(days, t)) is not None]
    out = []
    for t in s.scalars(select(Announcement.date).where(Announcement.symbol == symbol, Announcement.category == kind, Announcement.date.is_not(None))):
        i = first_session(days, t)
        if i is not None:
            out.append(i)
    return out


def shock_events(s: Session, source: str, response_days: pd.DatetimeIndex, direction: str, threshold_pct: float) -> list[int]:
    """CSE session indices on which `source` had moved by the threshold, found by gs-quant's event_impact_analysis and
    mapped onto the CSE's calendar (the next session for sources that close after the CSE, the same one otherwise)."""
    from gs_quant.timeseries.event_study import event_impact_analysis
    from gs_quant.timeseries._event_study.event_study_impact import CalendarAlignment, EventDirection
    b = S.load(s, source)
    if b is None or len(b.price) < 30:
        raise LookupError(f"No stored prices for {source_info(source)[0]} yet.")
    price = b.price
    same = source_info(source)[1]
    if not same:                                                         # its close comes after the CSE's: it can only act next session
        price = price.copy()
        price.index = price.index + pd.Timedelta(days=1)
    a = pd.Series(np.arange(1, len(response_days) + 1, dtype=float), index=response_days)   # only its calendar matters here
    try:
        # horizon=1 (one observation back on the series' own calendar). gs-quant's default "1d" is a calendar-day offset that has
        # no previous point after a weekend or holiday, so every Monday's move would silently be missed.
        res = event_impact_analysis(a, price, window=1, threshold=abs(threshold_pct) / 100, horizon=1,
                                    direction={"down": EventDirection.DOWN, "up": EventDirection.UP, "abs": EventDirection.ABS}[direction],
                                    calendar_alignment=CalendarAlignment.NEXT)
    except ValueError as exc:
        if "non-empty" in str(exc):                                  # gs-quant's way of saying no day moved that much
            return []
        raise
    mapped = res.attrs.get("mapped_event_dates") or []
    idx = response_days.get_indexer(pd.DatetimeIndex([pd.Timestamp(x) for x in mapped]))
    return sorted({int(i) for i in idx if i >= 0})


# ------------------------------------------------------------------ the whole study
def run(s: Session, spec: dict[str, Any]) -> dict[str, Any]:
    kind = spec.get("kind")
    a = S.index_series(s, "^ASPI")
    if a is None or a.price.empty:
        raise LookupError("No ASPI history stored yet.")
    days = a.price.index
    aspi_ret = a.price.pct_change(fill_method=None).fillna(0.0).to_numpy()
    rows: list[dict[str, Any]] = []
    mats: list[np.ndarray] = []
    notes: list[str] = []

    def add(label: str, ar: np.ndarray, idxs: list[int], dated: bool = True) -> None:
        kept = lockout(idxs)
        kept = [i for i in kept if i >= 1]
        if len(idxs) != len(kept):
            notes.append(f"{label}: {len(set(idxs)) - len(kept)} events left out (within {LOCKOUT} sessions of an earlier one, or before the data starts)")
        M = frame(ar, kept)
        for i, m in zip(kept, M):
            rows.append({"symbol": label, "date": str(days[i].date()), "idx": i})
            mats.append(m)

    if kind == "company":
        k = str(spec.get("event"))
        if k not in CATEGORY_LABELS and k not in EXTRA_KINDS:
            raise ValueError("Unknown kind of event.")
        syms = spec.get("symbols") or []
        if not syms:
            raise ValueError("Choose at least one stock.")
        adjust = spec.get("adjust", "market")
        for sym in syms:
            b = S.cse_stock(s, sym)
            if b is None:
                notes.append(f"{sym.split('.')[0]}: no stored prices")
                continue
            px = b.total.reindex(days.union(b.total.index)).sort_index().ffill().reindex(days)
            ret = px.pct_change(fill_method=None).fillna(0.0).to_numpy()
            ar = ret - aspi_ret if adjust == "market" else ret
            add(b.label, ar, company_events(s, sym, k, days))
        title = f"{CATEGORY_LABELS.get(k) or EXTRA_KINDS[k]}: the stock's return beyond the ASPI" if adjust == "market" else f"{CATEGORY_LABELS.get(k) or EXTRA_KINDS[k]}: the stock's return"
        meta = {"title": title, "response": "stocks" if len(syms) > 1 else syms[0].split(".")[0], "adjusted": adjust == "market"}
    elif kind == "shock":
        src = str(spec.get("source"))
        direction = spec.get("direction", "down")
        if direction not in ("down", "up", "abs"):
            raise ValueError("Direction must be down, up or abs.")
        try:
            thr = float(spec.get("threshold_pct", 2))
        except (TypeError, ValueError):
            raise ValueError("The size of the move must be a number.") from None
        if not 0.2 <= thr <= 50:
            raise ValueError("The size of the move must be between 0.2% and 50%.")
        resp = str(spec.get("response", "^ASPI"))
        label, same = source_info(src)
        idxs = shock_events(s, src, days, direction, thr)
        if resp == "^ASPI":
            ar, rlabel = aspi_ret, "ASPI"
        else:
            b = S.cse_stock(s, resp)
            if b is None:
                raise LookupError(f"No stored prices for {resp.split('.')[0]}.")
            px = b.total.reindex(days.union(b.total.index)).sort_index().ffill().reindex(days)
            ret = px.pct_change(fill_method=None).fillna(0.0).to_numpy()
            ar = ret - aspi_ret if spec.get("adjust", "market") == "market" else ret
            rlabel = b.label + (" beyond the ASPI" if spec.get("adjust", "market") == "market" else "")
        add(rlabel, ar, idxs)
        mv = {"down": "fell", "up": "rose", "abs": "moved"}[direction]
        meta = {"title": f"{rlabel} after {label} {mv} {thr:g}% in a day", "response": rlabel, "adjusted": resp != "^ASPI" and spec.get("adjust", "market") == "market",
                "reacts": "the same CSE session" if same else "the next CSE session"}
    else:
        raise ValueError("Choose company events or a world shock.")

    if not rows:
        return {"available": False, "reason": "No such events fall inside the stored prices.", "notes": notes, "meta": meta}
    agg = aggregate(rows, np.array(mats))
    table = []
    for r, m in sorted(zip(rows, mats), key=lambda x: x[0]["idx"], reverse=True)[:150]:
        seg = m[BEFORE:]
        table.append({"stock": r["symbol"], "date": r["date"], "day0": _f(seg[0]), "car5": _f(np.nansum(seg[:5])) if np.isfinite(seg[:5]).all() else None,
                      "car20": _f(np.nansum(seg[:20])) if np.isfinite(seg[:20]).all() else None})
    out = {"available": True, "meta": {**meta, "n_events": agg["events"], "n_dates": agg["dates"], "before": BEFORE, "after": AFTER, "min_n": MIN_N,
                                       "start": str(days[0].date()), "end": str(days[-1].date())},
           "path": [{k: _f(v) if isinstance(v, float) else v for k, v in p.items()} for p in agg["path"]],
           "horizons": [{k: _f(v) if isinstance(v, float) else v for k, v in h.items()} for h in agg["horizons"]],
           "pre": {k: _f(v) if isinstance(v, float) else v for k, v in agg["before"].items()}, "table": table, "notes": notes}
    out["summary"] = summary(out)
    return out


def _f(x: Any) -> float | None:
    x = float(x)
    return round(x, 6) if math.isfinite(x) else None


def pct(v: float | None, nd: int = 2) -> str:
    return "–" if v is None else f"{'+' if v > 0 else ''}{v * 100:.{nd}f}%"


def summary(o: dict[str, Any]) -> list[str]:
    """Plain words from fixed rules over the numbers. An association in the past, not a cause and not a forecast."""
    m, hz = o["meta"], {h["sessions"]: h for h in o["horizons"]}
    lines = [f"{m['n_events']} events on {m['n_dates']} different sessions, {m['start']} to {m['end']}."]
    if m["n_dates"] < m["min_n"]:
        lines.append(f"That is too few to say anything: with fewer than {m['min_n']} independent dates a result is mostly noise. The figures below are shown for completeness only.")
    for n in (1, 5, 20):
        h = hz.get(n)
        if h and h["n"] and h["mean"] is not None:
            sig = "" if h["t"] is None else (" (probably not chance)" if abs(h["t"]) >= 2 else " (could easily be chance)")
            what = "the reaction day" if n == 1 else f"the first {n} sessions"
            lines.append(f"Over {what} the average was {pct(h['mean'])}, up in {h['positive'] * 100:.0f}% of the {h['n']} cases, t = {('–' if h['t'] is None else f'{h['t']:.1f}')}{sig}.")
    pre = o["pre"]
    if pre.get("mean") is not None and pre.get("t") is not None and abs(pre["t"]) >= 2 and pre["n"] >= m["min_n"]:
        lines.append(f"The 5 sessions before day 0 already moved {pct(pre['mean'])} on average (t = {pre['t']:.1f}): something may have been known or guessed before the event.")
    if m.get("adjusted"):
        lines.append("Returns are measured beyond the ASPI on the same sessions, so general market moves are taken out. Dividends are included (total return).")
    lines.append("This shows what tended to happen in the past around these events. It does not show the event caused it, and it is not a forecast.")
    return lines
