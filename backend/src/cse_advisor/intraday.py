"""Intraday bars (1m / 5m / 15m / 1H / 4H): recording cse.lk trades, TradingView backfill, and loading a
timeframe as an adjusted OHLCV frame.

Sources:
- cse.lk `companyChartDataByStock` period 1 = today's individual trades only (no history). Recorded as
  1m bars every evening (and on demand during the session), so every timeframe can be built exactly.
- TradingView (unofficial, see tv_client.py) for history: ~5,000 bars per timeframe.
Per day the loader uses whichever source has the fuller day (cse normally, TradingView for days that
were never recorded, e.g. before the app existed or when the PC was off).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import settings
from .cse_client import client, ms_to_local, now_local
from .db import PriceIntraday, SessionLocal, Security

log = logging.getLogger(__name__)

INTRADAY = ("1m", "5m", "15m", "1h", "4h")
MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}
LABEL = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1D": "daily", "1W": "weekly"}
# the SMC engine's higher timeframe for each chart timeframe
HTF = {"1m": "5m", "5m": "15m", "15m": "1h", "1h": "4h", "4h": "1D", "1D": "1W"}
ASPI = "^ASPI"
TV_SYMBOL = {ASPI: "ASI"}


def cfg() -> dict[str, Any]:
    return {"tradingview": True, "tv_bars": 5000, "tv_refresh_bars": 300, "analysis_bars": 1500,
            "live_refresh_seconds": 60, **(settings().get("intraday") or {})}


def session() -> tuple[int, int]:
    """(open, close) in minutes after midnight, from settings.market (09:30-14:30)."""
    oh, om = map(int, settings()["market"]["open"].split(":"))
    ch, cm = map(int, settings()["market"]["close"].split(":"))
    return oh * 60 + om, ch * 60 + cm


def is_intraday(tf: str) -> bool:
    return tf in MINUTES


def bucket(ts: pd.Series | pd.DatetimeIndex, tf: str) -> pd.DatetimeIndex:
    """Start of the session-anchored bucket each timestamp falls in. Buckets start at the session open
    (09:30, 10:30 ... for 1H; 09:30 and 13:30 for 4H, as TradingView draws them). Prints after the close
    (closing auction, index final value) fold into the last bucket; pre-open prints into the first."""
    t = pd.DatetimeIndex(ts)
    o, c = session()
    m = (t.hour * 60 + t.minute).to_numpy()
    rel = (m - o).clip(0, c - o - 1)
    step = MINUTES[tf]
    start = (rel // step) * step + o
    day = t.normalize()
    return day + pd.to_timedelta(start, unit="m")


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """OHLCV frame indexed by bar start -> the same at timeframe tf (only buckets that traded)."""
    if df.empty:
        return df
    g = df.groupby(bucket(df.index, tf))
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(), "volume": g["volume"].sum()})
    if "trades" in df:
        out["trades"] = g["trades"].sum(min_count=1)
    return out


def ticks_to_1m(trades: list[dict[str, Any]], value_key: str = "p", qty_key: str = "q", time_key: str = "t") -> pd.DataFrame:
    rows = [(ms_to_local(r[time_key]).replace(tzinfo=None), float(r[value_key]), float(r.get(qty_key) or 0))
            for r in trades if r.get(value_key) is not None and r.get(time_key)]
    if not rows:
        return pd.DataFrame()
    t = pd.DataFrame(rows, columns=["ts", "price", "qty"]).sort_values("ts", kind="stable").set_index("ts")
    g = t.groupby(bucket(t.index, "1m"))
    return pd.DataFrame({"open": g["price"].first(), "high": g["price"].max(), "low": g["price"].min(),
                         "close": g["price"].last(), "volume": g["qty"].sum(), "trades": g["price"].count()})


# ---------------------------------------------------------------- storage
def _replace(s: Session, symbol: str, interval: str, source: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    lo, hi = df.index.min().to_pydatetime(), df.index.max().to_pydatetime()
    s.execute(delete(PriceIntraday).where(PriceIntraday.symbol == symbol, PriceIntraday.interval == interval,
                                          PriceIntraday.source == source, PriceIntraday.ts >= lo, PriceIntraday.ts <= hi))
    s.bulk_insert_mappings(PriceIntraday, [
        {"symbol": symbol, "interval": interval, "ts": ts.to_pydatetime(), "open": float(r.open), "high": float(r.high),
         "low": float(r.low), "close": float(r.close), "volume": float(r.volume),
         "trades": int(r.trades) if "trades" in df and pd.notna(r.trades) else None, "source": source}
        for ts, r in zip(df.index, df.itertuples())])
    return len(df)


def record_intraday(s: Session, symbol: str, trades: list[dict[str, Any]]) -> int:
    """Store today's cse.lk trades (one row each) as 1m bars, replacing that day's earlier recording."""
    bars = ticks_to_1m(trades)
    if bars.empty:
        return 0
    for d, day in bars.groupby(bars.index.normalize()):
        s.execute(delete(PriceIntraday).where(PriceIntraday.symbol == symbol, PriceIntraday.interval == "1m",
                                              PriceIntraday.source == "cse", PriceIntraday.ts >= d.to_pydatetime(),
                                              PriceIntraday.ts < (d + pd.Timedelta(days=1)).to_pydatetime()))
        _replace(s, symbol, "1m", "cse", day)
    return len(bars)


def record_aspi(s: Session) -> int:
    pts = client().aspi_intraday()
    return record_intraday(s, ASPI, [{"t": p["d"], "p": p["v"], "q": 0} for p in pts if p.get("v")])


def backfill_intraday(s: Session, symbol: str, full: bool = True, tfs: tuple[str, ...] | None = None) -> dict[str, int]:
    """Fetch TradingView bars for every intraday timeframe (or just `tfs`) and store them (source 'tv')."""
    from .tv_client import fetch_bars
    c = cfg()
    if not c["tradingview"]:
        return {}
    out = {}
    for tf in tfs or INTRADAY:
        # a 300-bar top-up is a single 1m session: take a week of minutes so a missed night is filled
        n = int(c["tv_bars"] if full else c["tv_refresh_bars"])
        if tf == "1m" and not full:
            n = max(n, 1500)
        bars = fetch_bars(TV_SYMBOL.get(symbol, symbol), tf, n)
        if not bars:
            out[tf] = 0
            continue
        df = pd.DataFrame(bars).set_index("ts")
        df = resample(df, tf)            # normalises any post-close prints into the last session bucket
        out[tf] = _replace(s, symbol, tf, "tv", df)
    return out


def has_intraday(s: Session, symbol: str) -> bool:
    """True once a TradingView backfill has stored bars for the symbol."""
    return bool(s.scalar(select(func.count()).select_from(PriceIntraday)
                         .where(PriceIntraday.symbol == symbol, PriceIntraday.source == "tv")))


def missing_timeframes(s: Session, symbol: str) -> tuple[str, ...]:
    """Timeframes with no TradingView bars stored yet for a symbol that already has others (1m was added later)."""
    have = set(s.scalars(select(PriceIntraday.interval).where(PriceIntraday.symbol == symbol, PriceIntraday.source == "tv").distinct()))
    return tuple(tf for tf in INTRADAY if tf not in have) if have else ()


def backfill_async(symbol: str, full: bool = True) -> threading.Thread:
    def run() -> None:
        try:
            with SessionLocal.begin() as s:
                got = backfill_intraday(s, symbol, full)
            log.info("intraday backfill %s: %s", symbol, got)
            _CACHE.clear()
        except Exception as exc:
            log.warning("intraday backfill %s failed: %s", symbol, exc)
    th = threading.Thread(target=run, daemon=True, name=f"tv-backfill-{symbol}")
    th.start()
    return th


# ---------------------------------------------------------------- live refresh (today's bars)
_live_last: dict[str, float] = {}
_live_lock = threading.Lock()


def refresh_today(symbol: str, force: bool = False) -> bool:
    """During the session, re-record today's trades for one symbol (at most once a minute)."""
    from .live import market_open
    if not force and not market_open():
        return False
    with _live_lock:
        if not force and time.monotonic() - _live_last.get(symbol, 0) < float(cfg()["live_refresh_seconds"]):
            return False
        _live_last[symbol] = time.monotonic()
    try:
        with SessionLocal.begin() as s:
            if symbol == ASPI:
                record_aspi(s)
            else:
                sec = s.get(Security, symbol)
                if not sec or not sec.cse_id:
                    return False
                record_intraday(s, symbol, client().intraday_trades(sec.cse_id))
        return True
    except Exception as exc:
        log.debug("live intraday %s: %s", symbol, exc)
        return False


# ---------------------------------------------------------------- loading
def _rows(s: Session, symbol: str, interval: str, source: str) -> pd.DataFrame:
    rows = s.execute(select(PriceIntraday.ts, PriceIntraday.open, PriceIntraday.high, PriceIntraday.low, PriceIntraday.close,
                            PriceIntraday.volume, PriceIntraday.trades)
                     .where(PriceIntraday.symbol == symbol, PriceIntraday.interval == interval, PriceIntraday.source == source)
                     .order_by(PriceIntraday.ts)).all()
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "trades"], index=pd.DatetimeIndex([], name="ts"))
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "trades"]).set_index("ts")


def merge_sources(cse: pd.DataFrame, tv: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Per day, prefer TradingView's bars: like the daily chart (trade-summary share volume) they exclude
    crossings, while cse.lk's trade list includes them (JKH 2026-11-03: 3.97M shares in the ticks vs 1.89M).
    cse.lk is used for days TradingView doesn't have or has only partly (e.g. today during the session).
    Returns (frame, per-row source label)."""
    parts, labels = [], []
    days = sorted(set(cse.index.normalize()) | set(tv.index.normalize()))
    cse_g = {d: g for d, g in cse.groupby(cse.index.normalize())} if not cse.empty else {}
    tv_g = {d: g for d, g in tv.groupby(tv.index.normalize())} if not tv.empty else {}
    for d in days:
        a, b = cse_g.get(d), tv_g.get(d)
        if a is not None and b is not None:
            # TradingView's day is complete when its last bar is no earlier than cse.lk's
            pick, src = (b, "tv") if b.index[-1] >= a.index[-1] else (a, "cse")
        else:
            pick, src = (a, "cse") if a is not None else (b, "tv")
        parts.append(pick)
        labels += [src] * len(pick)
    if not parts:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]), pd.Series(dtype=str)
    df = pd.concat(parts).sort_index()
    return df, pd.Series(labels, index=pd.concat(parts).index).sort_index()


def piecewise_constant(r: pd.Series, jump: float = 0.03) -> pd.Series:
    """Split a noisy ratio series into runs wherever it jumps by more than `jump`, and replace each run
    by its median. Used for adjustment factors, which are constant between corporate actions."""
    if r.empty:
        return r
    out = pd.Series(index=r.index, dtype=float)
    start, vals = 0, [float(r.iloc[0])]
    for i in range(1, len(r)):
        med = float(np.median(vals))
        v = float(r.iloc[i])
        if med and abs(v / med - 1) > jump:
            out.iloc[start:i] = med
            start, vals = i, [v]
        else:
            vals.append(v)
    out.iloc[start:] = float(np.median(vals))
    return out


def adjust_intraday(df: pd.DataFrame, src: pd.Series, daily: pd.DataFrame | None, adjusted: bool = True) -> pd.DataFrame:
    """Put intraday bars on the same price basis as the (rights/split-adjusted) daily chart.
    `daily` is engine.load_adjusted()'s frame (close + raw_close per day)."""
    if df.empty or daily is None or daily.empty:
        return df
    df = df.copy()
    day = df.index.normalize()
    raw_close = daily["raw_close"]
    deep = daily["deep"].astype(bool) if "deep" in daily else pd.Series(False, index=daily.index)
    deep_days = set(deep.index[deep])
    # TradingView may already have adjusted older bars for its own corporate-action rules: put each TV day
    # back on cse.lk's raw basis first when its last close is >3% away from the raw daily close.
    # (Days covered only by TradingView's adjusted daily bars have no raw close: handled below.)
    tv_days = sorted(set(day[src.reindex(df.index).to_numpy() == "tv"]) - deep_days)
    if tv_days:
        last_close = df["close"].groupby(day).last()
        for d in tv_days:
            rc = raw_close.get(d)
            lc = last_close.get(d)
            if rc and lc and abs(lc / rc - 1) > 0.03:
                m = day == d
                k = rc / lc
                for c in ("open", "high", "low", "close"):
                    df.loc[m, c] = df.loc[m, c] * k
                df.loc[m, "volume"] = df.loc[m, "volume"] / k
    if adjusted:
        pf = (daily["close"] / daily["raw_close"]).replace([float("inf")], 1.0)
        vf = (daily["volume"] / daily["raw_volume"]).where(daily["raw_volume"] > 0, 1.0)
        idx = pd.DatetimeIndex(sorted(set(day)))
        pf = pf.reindex(pf.index.union(idx)).bfill().fillna(1.0).reindex(idx)
        vf = vf.reindex(vf.index.union(idx)).bfill().fillna(1.0).reindex(idx)
        dd = [d for d in idx if d in deep_days]
        if dd:
            # tie each intraday day to TradingView's adjusted daily close with a factor that only changes at a
            # corporate action: per-day ratio, split where it jumps >3%, median inside each run (no daily jitter
            # from the last-trade vs official-close difference)
            last_close = df["close"].groupby(day).last()
            ratio = (daily["close"].reindex(dd) / last_close.reindex(dd)).dropna()
            step = piecewise_constant(ratio)
            pf.loc[step.index] = step
            vf.loc[step.index] = 1 / step
        f = pf.reindex(day).to_numpy()
        for c in ("open", "high", "low", "close"):
            df[c] = df[c] * f
        df["volume"] = df["volume"] * vf.reindex(day).to_numpy()
    return df


def load_intraday(s: Session, symbol: str, tf: str, daily: pd.DataFrame | None = None, adjusted: bool = True,
                  refresh: bool = True) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Adjusted OHLCV frame for timeframe tf plus a coverage summary."""
    if not is_intraday(tf):
        raise ValueError(f"not an intraday timeframe: {tf}")
    if refresh:
        refresh_today(symbol)
    cse = resample(_rows(s, symbol, "1m", "cse"), tf)
    tv = _rows(s, symbol, tf, "tv")
    df, src = merge_sources(cse, tv)
    df = adjust_intraday(df, src, daily, adjusted)
    cov: dict[str, Any] = {"bars": int(len(df)), "tf": tf}
    for name in ("tv", "cse"):
        idx = src.index[src == name] if len(src) else []
        if len(idx):
            cov[name] = {"from": str(idx.min().date()), "to": str(idx.max().date()), "bars": int(len(idx))}
    if not df.empty:
        df["open_estimated"] = False
        df["source"] = src.reindex(df.index).to_numpy()
        df["turnover"] = df["close"] * df["volume"]
        if "trades" not in df:
            df["trades"] = None
    return df, cov


# results of analyze_intraday, keyed on (symbol, tf, last bar time)
_CACHE: dict[tuple[str, str], tuple[Any, dict[str, Any]]] = {}


def cache_get(symbol: str, tf: str, last: Any) -> dict[str, Any] | None:
    hit = _CACHE.get((symbol, tf))
    return hit[1] if hit and hit[0] == last else None


def cache_put(symbol: str, tf: str, last: Any, result: dict[str, Any]) -> None:
    _CACHE[(symbol, tf)] = (last, result)


def completed(df: pd.DataFrame, tf: str, now: datetime | None = None) -> pd.DataFrame:
    """Drop the bar that is still forming (only during today's session)."""
    if df.empty:
        return df
    now = now or now_local().replace(tzinfo=None)
    last = df.index[-1]
    end = last + timedelta(minutes=MINUTES[tf])
    o, c = session()
    close_t = last.normalize() + timedelta(minutes=c)
    if last.date() == now.date() and now < min(end, close_t) + timedelta(minutes=1):
        return df.iloc[:-1]
    return df

