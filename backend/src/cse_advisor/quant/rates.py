"""The risk-free rate (Sri Lanka 3-month Treasury bill) and optional US series from FRED.

T-bill rate, from the Central Bank of Sri Lanka's statistical tables (Excel files, linked from its website):
- "Treasury Bill Auctions - Weekly (1996 to Latest)": the 91-day weighted average yield, before tax, each auction.
  CBSL refreshes this file only now and then.
- "Interest Rates - Monthly (2003 to Latest)": the 3-month T-bill primary-market yield at each month end, updated
  monthly. Used for the months after the weekly file ends.
The file names change with every update, so the link is found by its title on CBSL's page, not by a fixed name.
Stored as MacroDaily code `lk_tbill_3m` (close = yield in % a year). If CBSL can't be read, the last stored value is
used (Data health shows it as stale); with nothing stored yet, `quant.risk_free_manual_pct` in settings.yaml.

FRED (US Federal Reserve data, free key): read with gs-quant's own FredDataApi when FRED_API_KEY is set in .env.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import env, settings
from ..db import MacroDaily, SessionLocal

log = logging.getLogger(__name__)

TBILL = "lk_tbill_3m"
CBSL = "https://www.cbsl.gov.lk"
PAGES = {"weekly": "/en/statistics/statistical-tables/fiscal-sector", "monthly": "/en/statistics/statistical-tables/monetary-sector"}
TITLES = {"weekly": r"Treasury Bill Auctions\s*-\s*Weekly", "monthly": r"Interest Rates\s*-\s*Monthly"}
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september",
                                       "october", "november", "december"], 1)}
REFRESH_DAYS = 7          # read CBSL at most weekly
STALE_DAYS = 75           # the monthly table lags about a month; older than this is stale
# FRED series offered: FRED id -> (MacroDaily code, label). Codes are at most 12 characters (the column's size).
FRED_SERIES = {"DGS10": ("us10y", "US 10-year Treasury yield"), "DFF": ("us_fedfunds", "US Fed funds rate"),
               "VIXCLS": ("vix", "VIX (US volatility index)"), "DTWEXBGS": ("usd_index", "US dollar index (broad)"),
               "DCOILBRENTEU": ("brent_fred", "Brent crude (FRED)")}


def cfg() -> dict[str, Any]:
    return {"risk_free_manual_pct": None, **(settings().get("quant") or {})}


# ------------------------------------------------------------------ parsing (pure, tested on saved files)
def find_link(page_html: str, kind: str) -> str | None:
    """The Excel file linked under the given table title on CBSL's statistics page."""
    for m in re.finditer(r'<a[^>]+href="([^"]+\.xlsx?)"[^>]*>(.*?)</a>', page_html, re.S | re.I):
        title = re.sub(r"\s+", " ", re.sub("<[^>]+>", "", m.group(2))).strip()
        if re.search(TITLES[kind], title, re.I):
            url = m.group(1)
            return url if url.startswith("http") else CBSL + url
    return None


def parse_weekly(raw: bytes) -> pd.Series:
    """91-day weighted average yield (before tax) by auction date, from the weekly auction table."""
    df = pd.read_excel(io.BytesIO(raw), header=None)
    col = None
    for r in range(min(12, len(df))):                         # the "(Before Tax)" yield block, its 91-day column
        for c in range(df.shape[1]):
            if re.search(r"weighted average yield.*before tax", str(df.iat[r, c]), re.I | re.S):
                col = c
                break
        if col is not None:
            break
    if col is None:
        raise ValueError("weekly table: 'Weighted Average Yield Rates (Before Tax)' not found")
    out = {}
    for r in range(len(df)):
        d, v = df.iat[r, 1], df.iat[r, col]
        if isinstance(d, (datetime, pd.Timestamp)) and pd.notna(v):
            try:
                out[pd.Timestamp(d).normalize()] = float(v)
            except (TypeError, ValueError):
                continue
    if not out:
        raise ValueError("weekly table: no rows")
    return pd.Series(out).sort_index()


def parse_monthly(raw: bytes) -> pd.Series:
    """3-month T-bill primary-market yield at each month end, from the monthly interest-rate table."""
    df = pd.read_excel(io.BytesIO(raw), header=None)
    col = None
    head = min(12, len(df))
    tb = next(((r, c) for r in range(head) for c in range(df.shape[1]) if re.search(r"treasury bill", str(df.iat[r, c]), re.I)), None)
    if tb is not None:                                         # the first "3-Month" column at or right of "Government Treasury Bills"
        r0, c0 = tb
        col = next((c for c in range(c0, df.shape[1]) if any(re.fullmatch(r"\s*3-?\s*Month\s*", str(df.iat[r, c]), re.I)
                                                              for r in range(r0, head))), None)
    if col is None:
        raise ValueError("monthly table: the 3-month Treasury bill column was not found")
    out, year = {}, None
    for r in range(len(df)):
        y, mname, v = df.iat[r, 1], str(df.iat[r, 2]).strip().lower(), df.iat[r, col]
        if pd.notna(y) and re.fullmatch(r"\d{4}(\.0)?", str(y).strip()):
            year = int(float(y))
        if year and mname in MONTHS and pd.notna(v):
            try:
                out[pd.Timestamp(year, MONTHS[mname], 1) + pd.offsets.MonthEnd(0)] = float(v)
            except (TypeError, ValueError):
                continue
    if not out:
        raise ValueError("monthly table: no rows")
    return pd.Series(out).sort_index()


def combine(weekly: pd.Series, monthly: pd.Series) -> pd.Series:
    """Weekly auctions where they exist; month-end yields after the weekly table ends."""
    if weekly.empty:
        return monthly
    return pd.concat([weekly, monthly[monthly.index > weekly.index[-1]]]).sort_index()


# ------------------------------------------------------------------ fetching and storing
def _get(url: str, binary: bool = False) -> Any:
    # IPv4 only: CBSL's site (on a CDN) also lists IPv6 addresses that many Sri Lankan connections can't reach, and
    # httpx would wait on each in turn (over 10 minutes) where curl falls back at once. Over IPv4 a page takes ~1 s.
    with httpx.Client(transport=httpx.HTTPTransport(local_address="0.0.0.0"), timeout=30, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (CSE Advisor personal tool)"}) as c:
        r = c.get(url)
    r.raise_for_status()
    return r.content if binary else r.text


def fetch_tbill(get: Any = None) -> pd.Series:
    get = get or _get
    parts = {}
    for kind in ("weekly", "monthly"):
        url = find_link(get(CBSL + PAGES[kind]), kind)
        if not url:
            raise ValueError(f"CBSL page no longer links the {kind} table")
        parts[kind] = (parse_weekly if kind == "weekly" else parse_monthly)(get(url, binary=True))
    return combine(parts["weekly"], parts["monthly"])


def store(s: Session, code: str, series: pd.Series) -> int:
    have = {r.date: r for r in s.scalars(select(MacroDaily).where(MacroDaily.code == code))}
    n = 0
    for ts, v in series.items():
        d = ts.date()
        row = have.get(d)
        if row is None:
            s.add(MacroDaily(code=code, date=d, close=float(v)))
            n += 1
        elif abs(row.close - float(v)) > 1e-9:
            row.close = float(v)
    return n


def latest(s: Session, code: str = TBILL) -> tuple[date, float] | None:
    r = s.scalar(select(MacroDaily).where(MacroDaily.code == code).order_by(MacroDaily.date.desc()))
    return (r.date, r.close) if r else None


def top_up(get: Any = None, force: bool = False) -> dict[str, Any]:
    """Read CBSL at most once a week (and FRED when a key is set). Never raises: a failure is reported, the old values stay."""
    from ..db import Preference
    out: dict[str, Any] = {}
    with SessionLocal() as s:
        lt = latest(s)
        p = s.get(Preference, "rates_fetched_at")
        fetched_at = datetime.fromisoformat(p.value) if p and p.value else None
    if force or lt is None or fetched_at is None or datetime.utcnow() - fetched_at > timedelta(days=REFRESH_DAYS):
        try:
            ser = fetch_tbill(get)
            with SessionLocal.begin() as s:
                out["tbill_new"] = store(s, TBILL, ser)
                s.merge(Preference(key="rates_fetched_at", value=datetime.utcnow().isoformat()))
            out["tbill_latest"] = [str(ser.index[-1].date()), float(ser.iloc[-1])]
        except Exception as exc:  # noqa: BLE001 - unofficial source
            log.warning("CBSL T-bill rates: %s", exc)
            out["tbill_error"] = str(exc)[:200]
            out["tbill_latest"] = [str(lt[0]), lt[1]] if lt else None
    else:
        out["tbill_skipped"] = "read within the last week"
    if fred_key():
        out["fred"] = fred_top_up()
    return out


# ------------------------------------------------------------------ the rate the figures use
def tbill_series(s: Session) -> pd.Series:
    """The stored T-bill yields (% a year) by date; with none stored, the manual setting as a flat line (or empty)."""
    rows = s.execute(select(MacroDaily.date, MacroDaily.close).where(MacroDaily.code == TBILL).order_by(MacroDaily.date)).all()
    if rows:
        return pd.Series({pd.Timestamp(d): v for d, v in rows}).sort_index()
    manual = cfg().get("risk_free_manual_pct")
    return pd.Series({pd.Timestamp("1990-01-01"): float(manual)}) if manual is not None else pd.Series(dtype=float)


def rate_info(s: Session) -> dict[str, Any]:
    """What to show beside every ratio: the rate used, its date and where it came from."""
    lt = latest(s)
    if lt:
        stale = (date.today() - lt[0]).days > STALE_DAYS
        return {"rate_pct": lt[1], "as_of": str(lt[0]), "source": "CBSL 3-month Treasury bill", "stale": stale}
    manual = cfg().get("risk_free_manual_pct")
    if manual is not None:
        return {"rate_pct": float(manual), "as_of": None, "source": "manual setting (quant.risk_free_manual_pct)", "stale": False}
    return {"rate_pct": 0.0, "as_of": None, "source": "none yet: 0% used until the CBSL rate is read", "stale": True}


def cash_index(dates: pd.DatetimeIndex, rates: pd.Series) -> pd.Series:
    """Growth of 1 rupee kept in 3-month T-bills over these dates (Actual/365, the rate known on the previous date).
    The 'spot curve' gs-quant's excess_returns_pure takes, so the excess return uses the rate of each period."""
    if len(dates) == 0:
        return pd.Series(dtype=float)
    r = rates.reindex(rates.index.union(dates)).sort_index().ffill().reindex(dates).fillna(0.0) if len(rates) else pd.Series(0.0, index=dates)
    out = [1.0]
    for i in range(1, len(dates)):
        days = (dates[i] - dates[i - 1]).days
        out.append(out[-1] * (1 + float(r.iloc[i - 1]) / 100 * days / 365))
    return pd.Series(out, index=dates)


# ------------------------------------------------------------------ FRED (optional)
def fred_key() -> str | None:
    k = (env("FRED_API_KEY") or "").strip()
    return k or None


def fred_fetch(series_id: str, start: date | None = None, api: Any = None) -> pd.Series:
    from gs_quant.api.fred.data import FredDataApi
    api = api or FredDataApi(api_key=fred_key())
    return api.query_data(api.build_query(start=start), series_id)


def fred_top_up(api: Any = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for sid, (code, _label) in FRED_SERIES.items():
        try:
            with SessionLocal() as s:
                lt = latest(s, code)
            ser = fred_fetch(sid, (lt[0] - timedelta(days=10)) if lt else date(2010, 1, 1), api)
            with SessionLocal.begin() as s:
                out[sid] = store(s, code, ser)
        except Exception as exc:  # noqa: BLE001
            log.warning("FRED %s: %s", sid, exc)
            out[sid] = f"error: {str(exc)[:120]}"
    return out
