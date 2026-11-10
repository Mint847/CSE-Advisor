"""Correlation explorer: how closely different CSE stocks, indices, world stocks and world series move together.

Every series is turned into returns on the CSE's own sessions. A series that closes after the CSE (the US, Europe, India,
oil, gold) is matched to the CSE session it can actually affect, the next one; one that closes before it (Korea, Japan, Hong
Kong...) and USD/LKR to the same session. A CSE stock on a day it did not trade has a return of 0 (its price is carried over),
which pulls the correlation of thinly traded stocks toward zero: the weekly option, and the "thin" flag, are there for that.
A pair needs MIN_PAIR shared days to be shown, and |t| below about 2 is marked as probably not chance.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from . import report
from . import series as S
from .events import source_info
from .stats_tests import r_t

MAX_SERIES = 12
MIN_PAIR = 30
ROLL = 120
WINDOWS = {"1y": 1, "3y": 3, "5y": 5}


def to_cse_returns(price: pd.Series, days: pd.DatetimeIndex, same_day: bool) -> pd.Series:
    """Daily returns of a series on the CSE's sessions. Each return goes to the first session on or after its date (same_day) or
    strictly after it; two returns landing on one session (a CSE holiday in between) are compounded; a session with none is NaN."""
    r = price.pct_change(fill_method=None).dropna()
    pos = days.searchsorted(r.index, side="left" if same_day else "right")
    ok = pos < len(days)
    g = (1 + r[ok]).groupby(pos[ok]).prod() - 1
    out = pd.Series(np.nan, index=days)
    out.iloc[g.index.to_numpy()] = g.to_numpy()
    return out


def load(s: Session, ident: str, days: pd.DatetimeIndex) -> tuple[str, pd.Series, bool, bool] | None:
    """(label, returns on CSE sessions, thin, is a CSE stock) for an id: a CSE stock, ^ASPI / ^SPX..., sector:CODE or EXCHANGE:TICKER."""
    b = report.load_benchmark(s, ident)
    if b is None or len(b.price) < 30:
        return None
    if b.kind == "cse":
        px = b.total.reindex(days.union(b.total.index)).sort_index().ffill().reindex(days)
        r = px.pct_change(fill_method=None)
        thin = bool(S.thin_trading(b.volume, 250, days)["thin"])
        return b.label, r, thin, True
    if ident == "^ASPI" or ident.startswith("sector:"):
        same = True
    else:
        same = source_info(ident)[1]
    label = {"^ASPI": "ASPI"}.get(ident) or (source_info(ident)[0] if ident in ("^SPX", "^NDX", "^BRENT", "^GOLD", "^NIFTY", "^USDLKR") else b.label)
    return label, to_cse_returns(b.price, days, same), False, False


def compute(s: Session, ids: list[str], window: str = "3y", freq: str = "daily", pair: tuple[int, int] | None = None, roll: int = ROLL) -> dict[str, Any]:
    ids = list(dict.fromkeys(ids))
    if not 2 <= len(ids) <= MAX_SERIES:
        raise ValueError(f"Choose between 2 and {MAX_SERIES} series.")
    if window not in WINDOWS and window != "all":
        raise ValueError("Window must be 1y, 3y, 5y or all.")
    if freq not in ("daily", "weekly"):
        raise ValueError("Frequency must be daily or weekly.")
    a = S.index_series(s, "^ASPI")
    if a is None or a.price.empty:
        raise LookupError("No ASPI history stored yet.")
    days = a.price.index
    cols: list[tuple[str, str, pd.Series, bool, bool]] = []
    missing: list[str] = []
    for i in ids:
        got = load(s, i, days)
        if got is None:
            missing.append(i)
            continue
        cols.append((i, *got))
    if len(cols) < 2:
        raise LookupError("Fewer than two of those series have stored prices." + (f" Missing: {', '.join(missing)}." if missing else ""))
    df = pd.DataFrame({c[0]: c[2] for c in cols}, index=days)
    if window != "all":
        df = df[df.index > days[-1] - pd.DateOffset(years=WINDOWS[window])]
    if freq == "weekly":
        wk = df.groupby(df.index.to_period("W")).apply(lambda g: (1 + g.fillna(0)).prod() - 1)
        # a week in which a series has no return at all stays NaN
        has = df.groupby(df.index.to_period("W")).apply(lambda g: g.notna().any())
        df = wk.where(has)
    k = len(cols)
    mat = [[None] * k for _ in range(k)]
    nn = [[0] * k for _ in range(k)]
    sig = [[False] * k for _ in range(k)]
    pairs = []
    for i in range(k):
        for j in range(k):
            d = df[[cols[i][0], cols[j][0]]].dropna() if i != j else df[[cols[i][0]]].dropna()
            nn[i][j] = int(len(d))
            if i == j:
                mat[i][j] = 1.0
                continue
            if len(d) < MIN_PAIR:
                continue
            r = float(d.iloc[:, 0].corr(d.iloc[:, 1]))
            mat[i][j] = round(r, 4)
            t = r_t(r, len(d))
            sig[i][j] = bool(np.isfinite(t) and abs(t) >= 2)
            if j > i:
                pairs.append({"a": cols[i][1], "b": cols[j][1], "r": round(r, 4), "n": int(len(d)), "t": round(t, 2) if np.isfinite(t) else None, "significant": sig[i][j]})
    pairs.sort(key=lambda p: -abs(p["r"]))
    out: dict[str, Any] = {"ids": [c[0] for c in cols], "labels": [c[1] for c in cols], "thin": [c[3] for c in cols], "cse": [c[4] for c in cols], "matrix": mat, "n": nn,
                           "significant": sig, "pairs": pairs[:10], "missing": missing, "window": window, "freq": freq,
                           "start": str(df.index[0].start_time.date() if freq == "weekly" else df.index[0].date()), "end": str(df.index[-1].end_time.date() if freq == "weekly" else df.index[-1].date()),
                           "notes": notes_for(cols, freq)}
    if pair is not None and 0 <= pair[0] < k and 0 <= pair[1] < k and pair[0] != pair[1]:
        x, y = df[cols[pair[0]][0]], df[cols[pair[1]][0]]
        full = pd.concat([x, y], axis=1).dropna()
        w = roll if freq == "daily" else max(13, roll // 5)
        rc = full.iloc[:, 0].rolling(w).corr(full.iloc[:, 1]).dropna()
        out["rolling"] = {"a": cols[pair[0]][1], "b": cols[pair[1]][1], "window": w,
                          "points": [{"time": str(t.date() if hasattr(t, "date") else t.start_time.date()), "value": round(float(v), 4)} for t, v in rc.items()]}
    return out


def notes_for(cols: list[tuple[str, str, pd.Series, bool, bool]], freq: str) -> list[str]:
    notes = []
    thin = [c[1] for c in cols if c[3]]
    if thin and freq == "daily":
        notes.append(f"{', '.join(thin)} often did not trade, so their daily correlations are pulled toward zero: switch to weekly.")
    late = [c[1] for c in cols if not c[4] and c[0] not in ("^ASPI",) and not c[0].startswith("sector:") and not source_info(c[0])[1]]
    if late:
        notes.append(f"{', '.join(late)} close after the CSE does, so each of their days is matched to the next CSE session.")
    return notes


def options(s: Session, symbols: list[str]) -> dict[str, Any]:
    """What the explorer can compare: indices and world series stored here, sector indices, the user's stocks, world stocks."""
    from sqlalchemy import func, select
    from ..db import GlobalStock, MacroDaily, PriceDaily, SectorDaily
    have = {c for (c,) in s.execute(select(MacroDaily.code).distinct())}
    named = {"^SPX": "S&P 500", "^NDX": "Nasdaq-100", "^BRENT": "Brent crude", "^GOLD": "Gold", "^NIFTY": "Nifty 50 (India)", "^USDLKR": "USD/LKR"}
    idx = [{"id": "^ASPI", "label": "ASPI"}] + [{"id": k, "label": v} for k, v in named.items() if k[1:].lower() in have]
    sectors = [{"id": f"sector:{c}", "label": f"{c} · {n}"} for c, n in s.execute(select(SectorDaily.code, func.max(SectorDaily.name)).group_by(SectorDaily.code)).all()]
    stored = {sym for (sym,) in s.execute(select(PriceDaily.symbol).distinct())}
    cse = [{"id": x, "label": x.split(".")[0]} for x in symbols if x in stored]
    world = [{"id": g.symbol, "label": f"{g.symbol.split(':')[1]} · {g.name}"} for g in s.scalars(select(GlobalStock).order_by(GlobalStock.symbol))]
    return {"indices": idx, "sectors": sectors, "cse": cse, "world": world, "max": MAX_SERIES}
