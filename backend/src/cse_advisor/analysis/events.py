"""Upcoming events and event risk: results due, ex-dividend, AGM, rights dates.

Results timing is estimated from the company's own history: how many days after each quarter-end its
interim statements were uploaded (period end from the report title, because CSE's `manualDate` is not
always the period end). CSE's listing rules ask for interim statements within 45 days of the quarter end;
that is quoted for context, but the estimate follows what this company actually did.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from statistics import median
from typing import Any

RESULTS_RISK_DAYS = 7      # results this close = event risk for a new entry
XD_RISK_SESSIONS = 5       # ex-dividend this close: the price will drop by about the dividend
AGM_RISK_DAYS = 7


def _quarter_end_after(d: date) -> date:
    """The first calendar quarter-end (31 Mar, 30 Jun, 30 Sep, 31 Dec) after d."""
    for y in (d.year, d.year + 1):
        for m, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            if date(y, m, day) > d:
                return date(y, m, day)
    raise ValueError(d)


def results_due(reports: list[tuple[date, date]], today: date) -> dict[str, Any] | None:
    """reports: (period_end, uploaded) pairs. -> the next quarter's expected upload date and window."""
    rs = sorted({(p, u) for p, u in reports if p and u and 0 < (u - p).days < 120})
    if len(rs) < 2:
        return None
    last_pe = rs[-1][0]
    nxt = _quarter_end_after(last_pe)
    if nxt <= last_pe:
        return None
    lags = [(u - p).days for p, u in rs[-8:]]
    same = [(u - p).days for p, u in rs if p.month == nxt.month]          # e.g. year-end quarters take longer
    lag = int(median(same[-3:])) if len(same) >= 2 else int(median(lags))
    lo, hi = min(same or lags), max(same or lags)
    exp = nxt + timedelta(days=lag)
    if exp < today and rs[-1][0] < nxt:          # overdue: the expected day has passed with nothing uploaded
        status = "overdue"
    else:
        status = "expected"
    return {"period_end": nxt.isoformat(), "expected": exp.isoformat(), "window": [(nxt + timedelta(days=lo)).isoformat(),
            (nxt + timedelta(days=hi)).isoformat()], "days": (exp - today).days, "lag_days": lag, "history_lags": lags,
            "status": status, "basis": f"median of its last {len(same[-3:]) if len(same) >= 2 else len(lags)} "
                                         f"{'same-quarter ' if len(same) >= 2 else ''}reports ({lag} days after the quarter end); "
                                         "CSE rules ask for interim statements within 45 days"}


SKIP_ANNUAL = re.compile(r"errata|amended|amendment|corrigend|circular|notice|audited financial statements", re.I)
QUARTER_ENDS = {(3, 31), (6, 30), (9, 30), (12, 31)}


def annual_year_end(title: str, period_date: date | None, uploaded: date | None) -> date | None:
    """The financial year an annual-report row covers. CSE's own period date is the year end for most
    companies but is the upload day for some ('Annual Report as at 31st March 2026' filed 26 May), so a date
    written in the title wins, then CSE's date when it is a quarter end shortly before the upload."""
    from .report_parser import period_from_title
    d, _ = period_from_title(title)
    if d and (uploaded is None or 0 < (uploaded - d).days < 330):
        return d
    if period_date and (period_date.month, period_date.day) in QUARTER_ENDS and (uploaded is None or 0 < (uploaded - period_date).days < 330):
        return period_date
    return None


def annual_due(reports: list[tuple[str, date | None, date | None]], today: date) -> dict[str, Any] | None:
    """reports: (title, CSE period date, uploaded) of annual reports -> when the next one is expected: the next
    year end plus how long this company took after its recent year ends (corrections and errata are ignored)."""
    by_year: dict[date, date] = {}
    for title, pd_, up in reports:
        if not up or SKIP_ANNUAL.search(title or ""):
            continue
        fy = annual_year_end(title, pd_, up)
        if fy and 0 < (up - fy).days < 330:
            by_year[fy] = min(by_year.get(fy, up), up)           # a re-filed report doesn't count
    ends = sorted(by_year)
    if not ends:
        return None
    last = ends[-1]
    ends = [e for e in ends if (e.month, e.day) == (last.month, last.day)]      # a company that moved its year end starts over
    try:
        nxt = last.replace(year=last.year + 1)
    except ValueError:
        nxt = last.replace(year=last.year + 1, day=28)
    lags = [(by_year[e] - e).days for e in ends[-4:]]
    lag = int(median(lags))
    exp = nxt + timedelta(days=lag)
    return {"period_end": nxt.isoformat(), "expected": exp.isoformat(),
            "window": [(nxt + timedelta(days=min(lags))).isoformat(), (nxt + timedelta(days=max(lags))).isoformat()],
            "days": (exp - today).days, "lag_days": lag, "history_lags": lags,
            "status": "overdue" if exp < today else "expected", "year_ended": nxt <= today,
            "basis": (f"median of its last {len(lags)} annual reports ({lag} days after the year end)" if len(lags) > 1
                      else f"its last annual report came {lag} days after the year end (only one on this year end, so a rough guide)")}


def sessions_until(today: date, d: date) -> int:
    n, x = 0, today
    while x < d:
        x += timedelta(days=1)
        if x.weekday() < 5:
            n += 1
    return n


def upcoming(today: date, dividends: list[dict[str, Any]], agms: list[dict[str, Any]], corporate: list[dict[str, Any]],
             due: dict[str, Any] | None, close: float | None, horizon_days: int = 120) -> list[dict[str, Any]]:
    """Calendar rows {date, kind, title, detail, days} from today on, nearest first."""
    out: list[dict[str, Any]] = []
    end = today + timedelta(days=horizon_days)

    def add(d: str | date | None, kind: str, title: str, detail: str = "") -> None:
        if not d:
            return
        dd = date.fromisoformat(d) if isinstance(d, str) else d
        if today <= dd <= end:
            out.append({"date": dd.isoformat(), "kind": kind, "title": title, "detail": detail, "days": (dd - today).days})

    for v in dividends:
        dps = v.get("dps")
        y = f" (yield {100 * dps / close:.1f}% at {close:,.2f})" if dps and close else ""
        add(v.get("xd"), "ex_dividend", f"Ex-dividend Rs {dps:g}" if dps else "Ex-dividend", f"FY {v.get('fy') or '?'}{y}; buy before this date to receive it")
        add(v.get("payment"), "dividend_payment", f"Dividend payment Rs {dps:g}" if dps else "Dividend payment")
    for g in agms:
        add(g.get("date"), "agm", "Annual general meeting", g.get("venue") or "")
    for a in corporate:
        if a.get("ignore"):
            continue
        k = a.get("kind")
        add(a.get("ex_date"), "corporate", f"{'XR' if k == 'rights' else 'Ex-date'} ({k})", "price adjusts for the corporate action")
        add(a.get("trading_date"), "corporate", "Rights entitlements start trading")
        add(a.get("listing_date"), "corporate", "New shares listed", "possible supply overhang")
    if due:
        add(due["expected"], "results_due", f"Results for the quarter to {due['period_end']} (expected)", due["basis"])
    return sorted(out, key=lambda x: x["date"])


def risk_lines(today: date, calendar: list[dict[str, Any]], due: dict[str, Any] | None) -> list[str]:
    """Plain-text event-risk notes for the engines' 'watch next' lists (text only, not scored)."""
    lines = []
    if due and due["status"] == "overdue":
        lines.append(f"Results for the quarter to {due['period_end']} look overdue (expected about {due['expected']}): they can land any day.")
    for e in calendar:
        if e["kind"] == "results_due" and e["days"] <= RESULTS_RISK_DAYS:
            lines.append(f"Results expected around {e['date']} ({e['days']} days): an entry before them carries event risk.")
        elif e["kind"] == "ex_dividend" and sessions_until(today, date.fromisoformat(e["date"])) <= XD_RISK_SESSIONS:
            lines.append(f"{e['title']} on {e['date']}: the price drops by about the dividend that day; that is not weakness.")
        elif e["kind"] == "agm" and e["days"] <= AGM_RISK_DAYS:
            lines.append(f"AGM on {e['date']}.")
        elif e["kind"] == "corporate" and e["days"] <= 10:
            lines.append(f"{e['title']} on {e['date']}: {e['detail']}.")
    return lines
