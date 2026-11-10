"""The QNT tab: every figure, chart series and plain-English line for one stock (CSE or world), for a window, a return
basis (total or price) and a benchmark. Measurements only: nothing here feeds an engine or a verdict."""
from __future__ import annotations

import math
import threading
from collections import OrderedDict
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

import gs_quant.timeseries as ts
from gs_quant.timeseries import Window
from gs_quant.timeseries.statistics import LinearRegression

from . import metrics as M
from . import rates as R
from . import series as S

ROLL_VOL = 63            # about three months of sessions for the rolling volatility
ROLL_BETA = 120          # about six months for rolling beta and correlation
HIST_BINS = 41
WINDOWS = ("1y", "3y", "5y", "all")
_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_lock = threading.Lock()


def _pts(x: pd.Series, nd: int = 6) -> list[dict[str, Any]]:
    x = x.replace([np.inf, -np.inf], np.nan).dropna()
    return [{"time": str(t.date()), "value": round(float(v), nd)} for t, v in x.items()]


def benchmarks(b: S.Bundle, sector_code: str | None) -> list[dict[str, str]]:
    """The benchmarks offered for this instrument (the first is the default)."""
    out = [{"id": "^SPX", "label": "S&P 500"}, {"id": "^NDX", "label": "Nasdaq-100"}] if b.kind == "world" else [{"id": "^ASPI", "label": "ASPI"}]
    if b.kind != "world":
        if sector_code:
            out.append({"id": f"sector:{sector_code}", "label": f"Sector index ({sector_code})"})
        out += [{"id": "^SPX", "label": "S&P 500"}]
    return out


def load_benchmark(s: Session, bid: str) -> S.Bundle | None:
    if bid.startswith("sector:"):
        from sqlalchemy import select
        from ..db import SectorDaily
        code = bid.split(":", 1)[1]
        rows = s.execute(select(SectorDaily.date, SectorDaily.value).where(SectorDaily.code == code, SectorDaily.value.is_not(None))
                         .order_by(SectorDaily.date)).all()
        if len(rows) < 20:
            return None
        price = pd.Series({pd.Timestamp(d): float(v) for d, v in rows})
        return S.Bundle(symbol=bid, label=f"{code} sector", price=price, total=price, kind="index")
    return S.load(s, bid)


# ------------------------------------------------------------------ charts
def histogram(r: pd.Series) -> dict[str, Any]:
    """Daily returns in equal-width bins (the outer 0.5% on each side folded into the end bins so one freak day
    doesn't squash the picture), with the 5% VaR line."""
    if len(r) < 30:
        return {"bins": []}
    lo, hi = np.percentile(r, [0.5, 99.5])
    edges = np.linspace(lo, hi, HIST_BINS + 1)
    counts, _ = np.histogram(r.clip(lo, hi), bins=edges)
    return {"bins": [{"from": round(float(edges[i]), 6), "to": round(float(edges[i + 1]), 6), "count": int(c)} for i, c in enumerate(counts)],
            "var95": round(float(np.percentile(r, 5)), 6), "mean": round(float(r.mean()), 6), "n": int(len(r))}


def monthly_table(x: pd.Series) -> dict[str, Any]:
    """Return of every calendar month (rows = years) and of each year, plus the seasonal average by month."""
    m = M.monthly_returns(x)
    if m.empty:
        return {"years": [], "seasonal": []}
    years = []
    yearly = ts.bucketize(x, ts.AggregateFunction.LAST, ts.AggregatePeriod.YEAR)
    first_year_start = x.iloc[0]
    for y in sorted(set(m.index.year)):
        row = {str(mm): round(float(v), 6) for mm, v in zip(m[m.index.year == y].index.month, m[m.index.year == y].values)}
        prev = yearly[yearly.index.year == y - 1]
        base = float(prev.iloc[-1]) if len(prev) else float(first_year_start)
        end = yearly[yearly.index.year == y]
        years.append({"year": y, "months": row, "year_return": round(float(end.iloc[-1]) / base - 1, 6) if len(end) else None,
                      "partial": not len(prev) or y == x.index[-1].year})
    seasonal = []
    for mm in range(1, 13):
        v = m[m.index.month == mm]
        seasonal.append({"month": mm, "average": round(float(v.mean()), 6) if len(v) else None, "years": int(len(v)),
                         "positive": round(float((v > 0).mean()), 3) if len(v) else None,
                         "t": round(float(v.mean() / (v.std() / math.sqrt(len(v)))), 2) if len(v) >= 3 and v.std() > 0 else None})
    return {"years": years, "seasonal": seasonal}


def decomposition(x: pd.Series) -> dict[str, Any]:
    """gs-quant's seasonal decomposition (yearly cycle): the long-run trend, and the price with the yearly pattern taken
    out. gs-quant fills weekends and holidays with the previous close first; shown weekly."""
    if (x.index[-1] - x.index[0]).days < 2 * 365 + 30:
        return {"available": False, "reason": "needs at least two years of prices"}
    try:
        tr = ts.trend(x)
        adj = ts.seasonally_adjusted(x)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"could not decompose: {str(exc)[:120]}"}
    wk = lambda s: s.dropna().resample("W-FRI").last().dropna()   # noqa: E731
    return {"available": True, "trend": _pts(wk(tr), 4), "adjusted": _pts(wk(adj), 4), "price": _pts(wk(x), 4)}


def regression(s: Session, x: pd.Series, thin: bool) -> dict[str, Any]:
    """The stock's returns explained by the market (ASPI), the rupee (USD/LKR) and Wall Street (S&P 500, the previous
    US session, since New York trades after Colombo closes). Coefficients and R² from gs-quant's LinearRegression;
    t-statistics with Newey-West errors. Weekly returns for a thinly traded stock."""
    import statsmodels.api as sm
    from ..macro import series as macro_series
    facs = {"ASPI": S.index_series(s, "^ASPI")}
    usd = macro_series(s, "usdlkr")
    spx = macro_series(s, "spx")
    cols = {"stock": x}
    if facs["ASPI"] is not None:
        cols["ASPI"] = facs["ASPI"].price
    if usd is not None and len(usd):
        cols["USD/LKR"] = usd
    if spx is not None and len(spx):
        cols["S&P 500 (prev. US day)"] = spx
    if len(cols) < 2:
        return {"available": False, "reason": "no factor series stored"}
    px = pd.DataFrame({k: v for k, v in cols.items()})
    px = px.loc[px.index >= x.index[0]]
    if thin:
        rets = px.ffill().resample("W-FRI").last().pct_change(fill_method=None)
        freq = "weekly"
    else:
        rets = px.ffill().pct_change(fill_method=None)
        if "S&P 500 (prev. US day)" in rets:                       # a US close dated D reaches the CSE on the next session
            rets["S&P 500 (prev. US day)"] = rets["S&P 500 (prev. US day)"].shift(1)
        rets = rets.loc[rets.index.isin(x.index)]
        freq = "daily"
    rets = rets.dropna()
    names = [c for c in rets.columns if c != "stock"]
    if len(rets) < (40 if thin else 120):
        return {"available": False, "reason": f"needs more {freq} observations ({len(rets)})"}
    lr = LinearRegression([rets[c] for c in names], rets["stock"], fit_intercept=True)
    hac = sm.OLS(rets["stock"], sm.add_constant(rets[names])).fit(cov_type="HAC", cov_kwds={"maxlags": 5 if not thin else 2})
    per = 239 if not thin else 52
    rows = [{"factor": "Alpha (left over, a year)", "coefficient": round(float(lr.coefficient(0)) * per, 6),
             "t": round(float(hac.tvalues.iloc[0]), 2)}]
    for i, n in enumerate(names, 1):
        rows.append({"factor": n, "coefficient": round(float(lr.coefficient(i)), 4), "t": round(float(hac.tvalues.iloc[i]), 2)})
    return {"available": True, "frequency": freq, "observations": int(len(rets)), "r_squared": round(float(lr.r_squared()), 4), "rows": rows}


# ------------------------------------------------------------------ plain English (fixed rules, never advice)
def summary(f: dict[str, Any], rel: dict[str, Any], bench_label: str, thin: bool, rate: dict[str, Any]) -> list[str]:
    out = []
    v, b = f.get("volatility"), rel.get("beta_weekly" if thin else "beta")
    if v is not None:
        level = "very volatile" if v > 0.45 else "volatile" if v > 0.30 else "moderately volatile" if v > 0.18 else "fairly steady"
        out.append(f"Price swings: {level}, about {v * 100:.0f}% a year (a typical year's move is within ±{v * 100:.0f}% about two times in three).")
    if rel.get("weak_link"):
        out.append(f"Hardly moves with the {bench_label} (correlation {rel.get('correlation', 0):.2f}), so beta-based comparisons with it say little.")
    elif b is not None:
        if b > 1.2:
            out.append(f"Moves more than the {bench_label}: when the {bench_label} moves 1%, it has typically moved about {b:.1f}%.")
        elif b < 0.8:
            out.append(f"Moves less than the {bench_label}: about {b:.1f}% for each 1% move of the {bench_label}.")
        else:
            out.append(f"Moves roughly in line with the {bench_label} (beta {b:.2f}).")
    if f.get("max_drawdown") is not None:
        rec = "and has recovered" if f.get("recovered") else "and has not yet recovered"
        out.append(f"Worst fall in this period: {abs(f['max_drawdown']) * 100:.0f}% from its peak on {f['peak']} to {f['trough']}, {rec}.")
    if f.get("sharpe") is not None:
        s = f["sharpe"]
        word = "did not beat" if s < 0 else "beat, but only modestly," if s < 0.5 else "comfortably beat"
        out.append(f"After risk, it {word} the T-bill rate ({rate['rate_pct']:.2f}%): Sharpe ratio {s:.2f}.")
    dc = rel.get("down_capture")
    if dc is not None and rel.get("up_capture") is not None:
        out.append(f"In the {bench_label}'s down months it fell {dc * 100:.0f}% as much; in its up months it rose {rel['up_capture'] * 100:.0f}% as much.")
    if thin:
        out.append("Thinly traded: many sessions had no trade, so these risk figures understate the real risk and a price may not be available when you want to sell.")
    out.append("These describe the past, not the future, and are not advice.")
    return out


# ------------------------------------------------------------------ the whole report
def build(s: Session, symbol: str, window: str = "3y", basis: str = "total", bench: str | None = None) -> dict[str, Any]:
    if window not in WINDOWS:
        raise ValueError("window must be 1y, 3y, 5y or all")
    b = S.load(s, symbol)
    if b is None:
        raise LookupError("no prices for this stock")
    sector = None
    if b.kind == "cse":
        from ..db import Security
        sec = s.get(Security, symbol)
        sector = sec.sector if sec else None
    opts = benchmarks(b, sector)
    bench = bench or opts[0]["id"]
    key = (symbol, window, basis, bench, str(b.price.index[-1].date()), len(b.dividends))
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    af = S.cse_sessions_per_year(s) if b.kind != "world" else S.sessions_per_year(b.price.index)
    rates = R.tbill_series(s)
    rate = R.rate_info(s)
    full = b.series(basis)
    x = S.window_slice(full, window)
    bm = load_benchmark(s, bench)
    bx = S.window_slice(bm.price, window) if bm is not None else None
    cal = None
    if b.kind == "cse":
        a = S.index_series(s, "^ASPI")
        cal = a.price.index if a is not None else None
    thin = S.thin_trading(b.volume.loc[x.index[0]:] if b.volume is not None else None, len(x), cal)
    f = M.single(x, rates if b.kind != "world" else pd.Series(dtype=float), af)
    rel = M.relative(x, bx, rates if b.kind != "world" else pd.Series(dtype=float), af) if bx is not None else {"missing": {"beta": "benchmark has no data"}}
    r = M.daily_returns(x)
    charts: dict[str, Any] = {"drawdown": _pts(M.drawdown_series(x)),
                              "histogram": histogram(r),
                              "monthly": monthly_table(x)}
    if len(x) > ROLL_VOL + 5:
        charts["rolling_vol"] = _pts(ts.volatility(x, Window(ROLL_VOL, ROLL_VOL), annualization_factor=af) / 100)
    if bx is not None:
        d = S.aligned(x.rename("x"), bx.rename("b"))
        if len(d) > ROLL_BETA + 5:
            charts["rolling_beta"] = _pts(ts.beta(d["x"], d["b"], Window(ROLL_BETA, ROLL_BETA)), 4)
            charts["rolling_corr"] = _pts(ts.correlation(d["x"], d["b"], Window(ROLL_BETA, ROLL_BETA)), 4)
    yr = int(round(af))
    pz = b.price.loc[:x.index[-1]].tail(len(x) + yr + 5)               # the window plus the year each z-score looks back on
    if len(pz) > yr + 5:
        z = ts.zscores(pz, Window(yr, yr))
        charts["zscore"] = _pts(z.loc[x.index[0]:], 3)
        f["zscore_now"] = M._f(z.iloc[-1])
    if len(r) > yr:
        pct = ts.percentiles(r.tail(yr + 1), w=Window(yr, 0))          # only today's value is shown: the last year is enough
        f["today_move"] = M._f(r.iloc[-1])
        f["today_move_percentile"] = M._f(pct.iloc[-1])
    charts["decomposition"] = decomposition(x)
    if b.kind != "world":
        charts["regression"] = regression(s, x, thin["thin"])
    meta = {"symbol": symbol, "label": b.label, "kind": b.kind, "currency": b.currency, "window": window, "basis": basis,
            "bench": {"id": bench, "label": bm.label if bm is not None else bench}, "benchmarks": opts,
            "sessions_per_year": af, "rate": rate if b.kind != "world" else {"rate_pct": 0.0, "source": "not used for world stocks (US rate not stored)", "as_of": None, "stale": False},
            "dividends": {"count": len([d for d in b.dividends if pd.Timestamp(d["xd"]) >= x.index[0]]), "from": b.dividends_from,
                          "applies": basis == "total" and b.kind == "cse",
                          "left_out": len([d for d in b.dividends_left_out if pd.Timestamp(d["xd"]) >= x.index[0]]),
                          "approx": b.dividends_approx},
            "thin": thin, "start": f.get("start"), "end": f.get("end"),
            "gs_quant": "gs-quant 2.2.5 (Goldman Sachs, Apache-2.0): offline maths only"}
    out = {"meta": meta, "figures": f, "relative": rel, "charts": charts,
           "summary": summary(f, rel, meta["bench"]["label"], thin["thin"], meta["rate"])}
    with _lock:
        _cache[key] = out
        while len(_cache) > 64:
            _cache.popitem(last=False)
    return out
