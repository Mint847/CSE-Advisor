"""Single-instrument risk and return figures, computed with gs-quant's time-series library.

Conventions (all checked by tests against gs-quant itself and against hand calculations):
- Daily simple returns (gs_quant.timeseries.returns).
- Annualised return = (end / start) ^ (365.25 / calendar days) - 1, gs-quant's own convention in get_ratio_pure.
- Volatility = standard deviation of daily returns x sqrt(sessions per year), gs-quant's volatility() with
  annualization_factor set to the CSE's observed sessions (about 239), not the 252 gs-quant infers for daily data.
- Excess return = gs-quant's excess_returns_pure against a cash index growing at the 3-month T-bill rate of each day.
- Sharpe = annualised excess return / volatility of the excess-return index: gs-quant's get_ratio_pure formula, with
  the CSE annualisation (tests show it equals gs-quant's sharpe_ratio exactly when given 252).
- Sortino = annualised excess return / annualised downside deviation (daily returns below the daily T-bill return).
- Calmar = annualised return / |max drawdown|.
- Max drawdown = gs-quant's max_drawdown (largest fall from a running peak), plus its dates and recovery.
- VaR / CVaR 95% = historical: the 5th-percentile daily return and the average of the days below it.
- Beta, correlation = gs-quant's beta() and correlation() on daily returns; weekly versions on Friday closes.
- Jensen's alpha = ann. excess return - beta x benchmark's ann. excess return. Treynor = ann. excess return / beta.
  Tracking error = sd(daily return difference) x sqrt(sessions). Information ratio = (ann. return - benchmark's) / TE.
  M2 (Modigliani) = Sharpe x benchmark volatility + average T-bill rate. Capture = average monthly return in the
  benchmark's up (down) months / the benchmark's.
A figure that needs more history than is available is None, with the reason in `missing`.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

import gs_quant.timeseries as ts
from gs_quant.timeseries import Window
from gs_quant.timeseries.econometrics import excess_returns_pure
from gs_quant.timeseries.statistics import exponential_std

from .rates import cash_index
from .series import aligned

MIN_VOL = 60          # sessions for a volatility
MIN_RATIO = 230       # about a CSE year, for annualised return and the ratios
MIN_BETA = 60         # aligned sessions for beta / correlation
MIN_WEEKS = 26        # weeks for the weekly beta
MIN_LINK = 0.3        # below this correlation, ratios that divide by beta (Treynor) or compare up/down months are noise
EWMA_BETA = 0.94      # RiskMetrics decay for the "recent volatility" (about an 11-session half-life)


def _f(x: Any, nd: int = 10) -> float | None:     # 10 decimals: no rounding error reaches a shown figure
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if math.isfinite(v) else None


def daily_returns(x: pd.Series) -> pd.Series:
    return ts.returns(x).iloc[1:]


def ann_return(x: pd.Series) -> float | None:
    """(end / start) ^ (365.25 / days) - 1."""
    if len(x) < 2 or x.iloc[0] <= 0:
        return None
    days = (x.index[-1] - x.index[0]).days
    return _f((x.iloc[-1] / x.iloc[0]) ** (365.25 / days) - 1) if days > 0 else None


def volatility(x: pd.Series, af: float) -> float | None:
    """Annualised volatility of daily returns, as a fraction (0.25 = 25%)."""
    if len(x) < MIN_VOL:
        return None
    return _f(ts.volatility(x, Window(len(x), 0), annualization_factor=af).iloc[-1] / 100)


def ewma_volatility(x: pd.Series, af: float, beta: float = EWMA_BETA) -> float | None:
    """Recent-weighted volatility: gs-quant's exponential_std of daily returns, annualised with the CSE session count."""
    if len(x) < MIN_VOL:
        return None
    return _f(exponential_std(ts.returns(x), beta).iloc[-1] * math.sqrt(af))


def drawdown_series(x: pd.Series) -> pd.Series:
    return x / x.cummax() - 1


def max_drawdown(x: pd.Series) -> dict[str, Any] | None:
    """Largest fall from a running peak (gs-quant's max_drawdown), with the peak, trough and recovery dates."""
    if len(x) < 2:
        return None
    mdd = float(ts.max_drawdown(x).iloc[-1])
    dd = drawdown_series(x)
    trough = dd.idxmin()
    peak = x.loc[:trough].idxmax()
    after = x.loc[trough:]
    rec = after[after >= x.loc[peak]]
    recovery = rec.index[0] if len(rec) else None
    return {"max_drawdown": _f(mdd), "peak": str(peak.date()), "trough": str(trough.date()),
            "recovered": str(recovery.date()) if recovery is not None else None,
            "fall_sessions": int(x.index.get_loc(trough) - x.index.get_loc(peak)),
            "recovery_sessions": int(x.index.get_loc(recovery) - x.index.get_loc(trough)) if recovery is not None else None,
            "current_drawdown": _f(dd.iloc[-1])}


def excess_index(x: pd.Series, rates: pd.Series) -> pd.Series:
    return excess_returns_pure(x, cash_index(x.index, rates))


def sharpe(x: pd.Series, rates: pd.Series, af: float) -> float | None:
    if len(x) < MIN_RATIO:
        return None
    er = excess_index(x, rates)
    r, v = ann_return(er), volatility(er, af)
    return _f(r / v) if r is not None and v else None


def _rf_daily(index: pd.DatetimeIndex, rates: pd.Series) -> pd.Series:
    """The T-bill return earned between consecutive sessions (Actual/365)."""
    c = cash_index(index, rates)
    return c.pct_change().iloc[1:]


def downside_deviation(x: pd.Series, rates: pd.Series, af: float) -> float | None:
    if len(x) < MIN_VOL:
        return None
    r = daily_returns(x)
    shortfall = (r - _rf_daily(x.index, rates).reindex(r.index).fillna(0)).clip(upper=0)
    return _f(math.sqrt(float((shortfall ** 2).mean())) * math.sqrt(af))


def monthly_returns(x: pd.Series) -> pd.Series:
    """Month-end to month-end returns (gs-quant's bucketize, last value of each month)."""
    m = ts.bucketize(x, ts.AggregateFunction.LAST, ts.AggregatePeriod.MONTH)
    return m.pct_change().dropna()


def streaks(x: pd.Series) -> dict[str, int]:
    """Longest runs of consecutive up and down sessions (gs-quant's consecutive)."""
    r = daily_returns(x)
    up = ts.consecutive(r, ts.SignDirection.POSITIVE) if len(r) else pd.Series(dtype=float)
    down = ts.consecutive(r, ts.SignDirection.NEGATIVE) if len(r) else pd.Series(dtype=float)
    return {"longest_up": int(up.max()) if len(up) else 0, "longest_down": int(down.max()) if len(down) else 0,
            "current": int(up.iloc[-1]) if len(up) and up.iloc[-1] > 0 else -int(down.iloc[-1]) if len(down) else 0}


def single(x: pd.Series, rates: pd.Series, af: float) -> dict[str, Any]:
    """Every single-instrument figure for one price (or total-return) series."""
    out: dict[str, Any] = {}
    missing: dict[str, str] = {}
    n = len(x)
    r = daily_returns(x) if n > 1 else pd.Series(dtype=float)
    out["sessions"] = n
    out["start"], out["end"] = (str(x.index[0].date()), str(x.index[-1].date())) if n else (None, None)
    out["total_return"] = _f(x.iloc[-1] / x.iloc[0] - 1) if n > 1 else None
    if n >= MIN_RATIO:
        out["cagr"] = ann_return(x)
    else:
        out["cagr"] = None
        missing["cagr"] = f"needs about a year of sessions ({MIN_RATIO}); has {n}"
    out["volatility"] = volatility(x, af)
    out["ewma_volatility"] = ewma_volatility(x, af)
    if out["volatility"] is None:
        missing["volatility"] = f"needs {MIN_VOL} sessions; has {n}"
    mdd = max_drawdown(x)
    out.update(mdd or {})
    out["sharpe"] = sharpe(x, rates, af)
    out["downside_deviation"] = downside_deviation(x, rates, af)
    er_ann = ann_return(excess_index(x, rates)) if n >= MIN_RATIO else None
    out["excess_return"] = er_ann
    out["sortino"] = _f(er_ann / out["downside_deviation"]) if er_ann is not None and out["downside_deviation"] else None
    out["calmar"] = _f(out["cagr"] / abs(out["max_drawdown"])) if out["cagr"] is not None and out.get("max_drawdown") else None
    for k in ("sharpe", "sortino", "calmar"):
        if out[k] is None:
            missing[k] = f"needs about a year of sessions ({MIN_RATIO}); has {n}"
    if len(r) >= MIN_VOL:
        q = float(np.percentile(r, 5))
        out["var95"] = _f(q)
        out["cvar95"] = _f(r[r <= q].mean())
        out["skew"] = _f(r.skew())
        out["kurtosis"] = _f(r.kurt())                 # excess kurtosis: 0 for a normal distribution
        semi = (r - r.mean()).clip(upper=0)
        out["semi_variance"] = _f(float((semi ** 2).mean()) * af)
        out["positive_days"] = _f((r > 0).mean())
    m = monthly_returns(x) if n > 40 else pd.Series(dtype=float)
    if len(m):
        out["best_month"] = {"month": str(m.idxmax().date())[:7], "return": _f(m.max())}
        out["worst_month"] = {"month": str(m.idxmin().date())[:7], "return": _f(m.min())}
        out["positive_months"] = _f((m > 0).mean())
        out["months"] = int(len(m))
    out.update(streaks(x) if n > 2 else {})
    out["sessions_per_year"] = af
    out["missing"] = missing
    return out


def relative(x: pd.Series, b: pd.Series, rates: pd.Series, af: float) -> dict[str, Any]:
    """Figures against a benchmark (both series are cut to their common dates)."""
    out: dict[str, Any] = {}
    missing: dict[str, str] = {}
    d = aligned(x.rename("x"), b.rename("b"))
    n = len(d)
    out["aligned_sessions"] = n
    if n < MIN_BETA:
        missing["beta"] = f"needs {MIN_BETA} sessions with both prices; has {n}"
        out["missing"] = missing
        return out
    xs, bs = d["x"], d["b"]
    out["beta"] = _f(ts.beta(xs, bs, Window(n, 0)).iloc[-1])
    out["correlation"] = _f(ts.correlation(xs, bs, Window(n, 0)).iloc[-1])
    out["r_squared"] = _f(out["correlation"] ** 2) if out["correlation"] is not None else None
    wk = d.resample("W-FRI").last().dropna()
    if len(wk) >= MIN_WEEKS:
        out["beta_weekly"] = _f(ts.beta(wk["x"], wk["b"], Window(len(wk), 0)).iloc[-1])
        out["correlation_weekly"] = _f(ts.correlation(wk["x"], wk["b"], Window(len(wk), 0)).iloc[-1])
    else:
        missing["beta_weekly"] = f"needs {MIN_WEEKS} weeks; has {len(wk)}"
    rx, rb = daily_returns(xs), daily_returns(bs)
    te = float((rx - rb).std()) * math.sqrt(af)
    out["tracking_error"] = _f(te)
    if n >= MIN_RATIO:
        cx, cb = ann_return(xs), ann_return(bs)
        ex, eb = ann_return(excess_index(xs, rates)), ann_return(excess_index(bs, rates))
        beta = out["beta"]
        out["benchmark_cagr"] = cb
        out["information_ratio"] = _f((cx - cb) / te) if cx is not None and cb is not None and te > 0 else None
        out["jensen_alpha"] = _f(ex - beta * eb) if ex is not None and eb is not None and beta is not None else None
        out["treynor"] = _f(ex / beta) if ex is not None and beta else None
        vb = volatility(bs, af)
        sh = sharpe(xs, rates, af)
        rf_avg = float(rates.reindex(rates.index.union(xs.index)).sort_index().ffill().reindex(xs.index).mean()) / 100 if len(rates) else 0.0
        out["m2"] = _f(sh * vb + rf_avg) if sh is not None and vb is not None else None
        mx, mb = monthly_returns(xs), monthly_returns(bs)
        mm = aligned(mx.rename("x"), mb.rename("b"))
        up, down = mm[mm["b"] > 0], mm[mm["b"] < 0]
        out["up_capture"] = _f(up["x"].mean() / up["b"].mean()) if len(up) >= 3 else None
        out["down_capture"] = _f(down["x"].mean() / down["b"].mean()) if len(down) >= 3 else None
        for tag, part in (("bull", up), ("bear", down)):          # in the benchmark's up and down months
            if len(part) >= 3:
                diff = part["x"] - part["b"]
                te_m = float(diff.std()) * math.sqrt(12)
                out[f"tracking_error_{tag}"] = _f(te_m)
                out[f"information_ratio_{tag}"] = _f(diff.mean() * 12 / te_m) if te_m > 0 else None
                rf_m = rf_avg / 12                       # the average T-bill return per month over the window
                out[f"jensen_alpha_{tag}"] = (_f(((part["x"] - rf_m) - beta * (part["b"] - rf_m)).mean() * 12)
                                              if beta is not None else None)
    else:
        for k in ("information_ratio", "jensen_alpha", "treynor", "m2", "up_capture", "down_capture"):
            missing[k] = f"needs about a year of shared sessions ({MIN_RATIO}); has {n}"
    corr = out.get("correlation")
    if corr is not None and abs(corr) < MIN_LINK:
        why = f"not meaningful: the stock hardly moves with this benchmark (correlation {corr:.2f}, below {MIN_LINK})"
        for k in ("treynor", "up_capture", "down_capture", "jensen_alpha_bull", "jensen_alpha_bear"):
            if out.get(k) is not None:
                out[k] = None
                missing[k] = why
        out["weak_link"] = True
    out["missing"] = missing
    return out
