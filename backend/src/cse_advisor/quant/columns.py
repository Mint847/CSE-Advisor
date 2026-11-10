"""One-year volatility, Sharpe, beta and worst fall for the CANDIDATES and All-stocks tables. The same figures, from the same
code, as the QNT tab's 1-year window (total price series, CBSL T-bill rate, the CSE's session count), so a number in a table
and the number on the stock's own page agree."""
from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from . import metrics as M
from . import rates as R
from . import series as S

MIN_BARS = 200                      # fewer than this many sessions in the year: no figures (a half-year of data misleads)
_cache: dict[tuple[str, str], dict[str, Any] | None] = {}


def figures(close: pd.Series, aspi: pd.Series, rates: pd.Series, af: float) -> dict[str, Any] | None:
    """{vol, sharpe, beta, mdd} over the last year of `close` (a price series on the stock's own dates) against `aspi`."""
    x = S.window_slice(close.dropna(), "1y")
    if len(x) < MIN_BARS:
        return None
    f = M.single(x, rates, af)
    b = aspi.reindex(x.index.union(aspi.index)).sort_index().ffill().reindex(x.index).dropna()
    rel = M.relative(x.loc[b.index], b, rates, af) if len(b) >= MIN_BARS else {}
    r = lambda v: None if v is None else round(float(v), 4)   # noqa: E731
    return {"vol": r(f.get("volatility")), "sharpe": r(f.get("sharpe")), "beta": r(rel.get("beta")), "mdd": r(f.get("max_drawdown"))}


def for_symbols(s: Session, symbols: list[str]) -> dict[str, dict[str, Any]]:
    """The figures for stocks the app stores prices for (cached until a new session's price arrives)."""
    a = S.index_series(s, "^ASPI")
    if a is None:
        return {}
    rates = R.tbill_series(s)
    af = S.cse_sessions_per_year(s)
    out: dict[str, dict[str, Any]] = {}
    for sym in symbols:
        b = S.cse_stock(s, sym)
        if b is None:
            continue
        key = (sym, str(b.total.index[-1].date()))
        if key not in _cache:
            _cache[key] = figures(b.total, a.price, rates, af)
        if _cache[key]:
            out[sym] = _cache[key]
    return out
