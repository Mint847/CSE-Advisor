"""The daily series every quant figure is computed from, with the CSE accuracy rules applied in one place.

Rules (each figure on the page says which apply):
1. Adjusted prices: rights issues, bonuses and splits are taken out (engine.load_adjusted), so they don't look like crashes.
2. Total return: cash dividends are added back on the ex-dividend (XD) date. Only dividends the app has recorded can be
   added; `dividends_from` says from when the record starts, and before that the series is price-only.
3. Annualisation: the CSE trades about 240 sessions a year, not the 252 of US markets. The observed count is used.
4. Thin trading: a stock that often doesn't trade has stale prices; its volatility understates risk and its beta is
   pulled toward zero. More than THIN_SHARE of sessions without a trade flags it (weekly figures are then shown first).
5. Minimum sample: a figure needing N sessions shows "not enough history" below N (see metrics.MIN_*).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import Announcement, MarketDaily

THIN_SHARE = 0.20                 # more than this share of sessions without a trade = thinly traded
SESSIONS_FALLBACK = 240           # used when fewer than one complete calendar year of sessions is known


@dataclass
class Bundle:
    """One instrument's series for the quant figures."""
    symbol: str
    label: str
    price: pd.Series                                   # adjusted closes
    total: pd.Series                                   # total-return index (= price where no dividends are known)
    volume: pd.Series | None = None
    dividends: list[dict[str, Any]] = field(default_factory=list)   # [{xd, dps (as paid), dps_adj (in adjusted prices)}]
    dividends_from: str | None = None                  # first recorded dividend's date (total return is estimated from here)
    kind: str = "cse"                                  # cse | world | index
    currency: str = "LKR"
    adj_factor: pd.Series | None = None                # adjusted / as-traded close (NaN where the as-traded price isn't stored)
    dividends_left_out: list[dict[str, Any]] = field(default_factory=list)   # recorded but not convertible to adjusted prices
    dividends_approx: int = 0                          # kept across a rights issue before as-traded prices start (small error)
    open: pd.Series | None = None                      # adjusted opening prices (the strategy lab trades at the open)

    def series(self, basis: str = "total") -> pd.Series:
        return self.total if basis == "total" else self.price


# ------------------------------------------------------------------ sessions per year (annualisation)
def sessions_per_year(index: pd.DatetimeIndex, years: int = 5) -> float:
    """Median number of sessions in the last `years` complete calendar years of this index."""
    if len(index) == 0:
        return float(SESSIONS_FALLBACK)
    counts = pd.Series(1, index=index).groupby(index.year).sum()
    last = index[-1].year
    full = counts[(counts.index < last) & (counts.index >= index[0].year + (0 if index[0].month == 1 and index[0].day <= 7 else 1))]
    full = full.tail(years)
    return float(full.median()) if len(full) else float(SESSIONS_FALLBACK)


def cse_sessions_per_year(s: Session) -> float:
    """The CSE's own trading calendar: the dates the ASPI was recorded."""
    dates = pd.DatetimeIndex([pd.Timestamp(d) for (d,) in s.execute(select(MarketDaily.date).where(MarketDaily.aspi.is_not(None)))])
    return sessions_per_year(dates.sort_values())


# ------------------------------------------------------------------ total return
def total_return_index(price: pd.Series, dividends: list[tuple[pd.Timestamp, float]]) -> pd.Series:
    """Price index with each dividend reinvested on its XD date: r_t = (P_t + D_t) / P_(t-1). A dividend whose XD date
    is not a session counts on the next session. Same start value as the price series."""
    if price.empty:
        return price
    d = pd.Series(0.0, index=price.index)
    for xd, amt in dividends:
        pos = price.index.searchsorted(pd.Timestamp(xd))
        if 0 < pos < len(price.index):                   # the first bar has no previous close to pay against
            d.iloc[pos] += float(amt)
    gross = (price + d) / price.shift(1)
    gross.iloc[0] = 1.0
    return gross.cumprod() * float(price.iloc[0])


def recorded_dividends(s: Session, symbol: str) -> list[dict[str, Any]]:
    """Cash dividends from the stored announcements, one per (XD date, amount): the same dividend is often announced
    twice (first without dates, then with)."""
    seen: dict[tuple[str, float], dict[str, Any]] = {}
    for a in s.scalars(select(Announcement).where(Announcement.symbol == symbol)):
        ev = a.event or {}
        if ev.get("kind") != "cash_dividend" or not ev.get("dps") or not ev.get("xd"):
            continue
        try:
            xd = date.fromisoformat(str(ev["xd"]))
            dps = float(ev["dps"])
        except (TypeError, ValueError):
            continue
        seen[(str(xd), dps)] = {"xd": str(xd), "dps": dps}
    return sorted(seen.values(), key=lambda x: x["xd"])


# ------------------------------------------------------------------ builders
def cse_stock(s: Session, symbol: str) -> Bundle | None:
    from ..engine import load_adjusted
    adj, _ = load_adjusted(s, symbol)
    if adj.empty:
        return None
    adj = adj[~adj.index.duplicated(keep="last")].sort_index()
    price = adj["close"].astype(float)
    # A dividend paid in rupees per share at the time is scaled like the price that day (adjusted ÷ as-traded close).
    # Before the first as-traded bar (TradingView's already-adjusted history) the as-traded price is unknown: the factor
    # of the first as-traded bar carries back (the app's own recorded actions), and a split or bonus issue between the
    # dividend and that bar can't be converted, so that dividend is left out (see share_changes).
    known = (adj["close"] / adj["raw_close"]).replace([np.inf, -np.inf], np.nan)
    if "deep" in adj:
        known = known.mask(adj["deep"].astype(bool))      # stitch_deep copies the adjusted price into raw_* there
    first_raw = known.first_valid_index()
    factor = known.bfill().fillna(1.0)
    changes = share_changes(s, symbol)
    divs, left_out, approx = [], [], 0
    for dv in recorded_dividends(s, symbol):
        ts = pd.Timestamp(dv["xd"])
        pos = price.index.searchsorted(ts)
        if pos >= len(price.index):
            continue                                   # XD date still ahead
        if first_raw is None or ts < first_raw:
            after = [c for c in changes if ts - pd.Timedelta(days=CHANGE_LOOKBACK_DAYS) <= c["date"] and (first_raw is None or c["date"] <= first_raw)]
            if any(c["kind"] in SIZE_CHANGES for c in after):
                left_out.append({**dv, "reason": "a share split or bonus issue since then (amount can't be converted)"})
                continue
            approx += any(c["kind"] == "rights_issue" for c in after)
        f = float(factor.iloc[pos])
        divs.append({**dv, "dps_adj": round(dv["dps"] * f, 6)})
    total = total_return_index(price, [(pd.Timestamp(d["xd"]), d["dps_adj"]) for d in divs])
    return Bundle(symbol=symbol, label=symbol.split(".")[0], price=price, total=total, volume=adj["volume"].astype(float),
                  dividends=divs, dividends_from=divs[0]["xd"] if divs else None, kind="cse", currency="LKR",
                  adj_factor=known.astype(float), dividends_left_out=left_out, dividends_approx=int(approx),
                  open=adj["open"].astype(float) if "open" in adj else None)


SIZE_CHANGES = {"share_split", "bonus_issue"}
CHANGE_LOOKBACK_DAYS = 45           # a split announced shortly before a dividend's XD date may still take effect after it


def share_changes(s: Session, symbol: str) -> list[dict[str, Any]]:
    """Announcements of splits, bonus issues and rights issues (date, kind), from the stored announcement categories."""
    rows = s.execute(select(Announcement.date, Announcement.category).where(
        Announcement.symbol == symbol, Announcement.category.in_(SIZE_CHANGES | {"rights_issue"}))).all()
    return [{"date": pd.Timestamp(d).normalize(), "kind": k} for d, k in rows if d is not None]


def world_stock(s: Session, symbol: str) -> Bundle | None:
    from ..db import GlobalStock
    from ..globalstocks import frame
    df = frame(s, symbol)
    if df.empty:
        return None
    g = s.get(GlobalStock, symbol)
    price = df["close"].astype(float)
    return Bundle(symbol=symbol, label=symbol.split(":")[-1], price=price, total=price, volume=df["volume"].astype(float),
                  kind="world", currency=(g.currency if g and g.currency else "USD"))


def index_series(s: Session, code: str) -> Bundle | None:
    """^ASPI, ^SPX, ^NDX... or any id the compare route knows (indices are price-only)."""
    from ..market_api import INDEX_SERIES, _compare_points
    if code != "^ASPI" and code not in INDEX_SERIES:            # another stored world series (^USDLKR, FRED codes...): never a network lookup
        from ..db import MacroDaily
        rows = s.execute(select(MacroDaily.date, MacroDaily.close).where(MacroDaily.code == code[1:].lower()).order_by(MacroDaily.date)).all()
        if not rows:
            return None
        price = pd.Series({pd.Timestamp(d): float(v) for d, v in rows}).sort_index()
        return Bundle(symbol=code, label=code[1:].upper(), price=price, total=price, kind="index")
    got = _compare_points(s, code)
    if not got or not got[1]:
        return None
    price = pd.Series({pd.Timestamp(p["time"]): float(p["value"]) for p in got[1]}).sort_index()
    return Bundle(symbol=code, label=got[0], price=price, total=price, kind="index")


def load(s: Session, symbol: str) -> Bundle | None:
    """Any instrument by id: an index (^ASPI), a world stock (EXCHANGE:TICKER) or a CSE stock."""
    if symbol.startswith("^"):
        return index_series(s, symbol)
    if ":" in symbol:
        return world_stock(s, symbol)
    return cse_stock(s, symbol)


# ------------------------------------------------------------------ liquidity and alignment
def thin_trading(volume: pd.Series | None, window: int, sessions: pd.DatetimeIndex | None = None) -> dict[str, Any]:
    """Share of the last `window` sessions without a trade. With `sessions` (the market's own calendar, e.g. the ASPI's
    dates) a session on which the stock has no row at all counts as a day without a trade too."""
    if volume is None or volume.empty:
        return {"share_no_trade": None, "thin": False}
    from ..config import settings
    limit = float((settings().get("quant") or {}).get("thin_trading_share", THIN_SHARE))
    v = volume.tail(window)
    if sessions is not None and len(v):
        cal = sessions[sessions >= v.index[0]][-window:] if len(sessions) else sessions
        if len(cal):
            v = v.reindex(cal)                         # a missing session becomes NaN = no trade
    share = float((v.fillna(0) <= 0).mean()) if len(v) else 0.0
    return {"share_no_trade": round(share, 3), "thin": share > limit, "limit": limit}


def aligned(*series: pd.Series) -> pd.DataFrame:
    """Series side by side on the dates they all have (no filling: a missing day is a day without a price)."""
    return pd.concat(series, axis=1, join="inner").dropna()


def window_slice(x: pd.Series, window: str) -> pd.Series:
    """'1y', '3y', '5y' or 'all', counted back from the last date."""
    if window == "all" or x.empty:
        return x
    years = {"1y": 1, "3y": 3, "5y": 5, "10y": 10}[window]
    return x[x.index > x.index[-1] - pd.DateOffset(years=years)]
