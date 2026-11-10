"""Portfolio risk and performance (PORT): the equity curve against the ASPI with the full figure set on it, the risk of
what is held now (each holding's share of the risk, correlations, value-at-risk in rupees, stress tests), and "what if
you had held your lists" baskets (gs-quant's basket_series).

Equity curve: a time-weighted return, so putting money in or taking it out doesn't count as performance. Each session:
    r_t = (V_t + D_t + S_t) / (V_(t-1) + B_t) - 1
V = market value of the open positions at the close, B = money put in that day (buys at cost incl. the fee), S = money
taken out (sales net of the fee), D = estimated dividends received (dividend x shares held before the XD date).
A position is valued with the adjusted price scaled back to its entry day, so a later bonus issue or split doesn't show
as a loss (and dividends are counted on the extra shares). Fees as on PORT: costs.brokerage_pct_per_side each way.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

import gs_quant.timeseries as ts

from ..config import settings
from ..db import Position
from . import metrics as M
from . import rates as R
from . import series as S

RISK_WINDOW = 250          # sessions of daily returns behind the covariance, VaR and correlations (about a CSE year)
MIN_RISK = 60
MIN_BASKET = 60
STRESS_HORIZONS = {"1 week": 5, "1 month": 21, "3 months": 63}     # sessions; no 1-day replays: a one-day date slip
                                                                     # between the index and a stock's history flips them


def fee() -> float:
    return float(settings()["costs"]["brokerage_pct_per_side"]) / 100


def _positions(s: Session, uid: str) -> list[Position]:
    return list(s.scalars(select(Position).where(Position.user_id == uid).order_by(Position.entry_date, Position.id)))


def _aspi(s: Session) -> pd.Series:
    a = S.index_series(s, "^ASPI")
    return a.price if a is not None else pd.Series(dtype=float)


def on_calendar(x: pd.Series, cal: pd.DatetimeIndex) -> pd.Series:
    """A series on the CSE calendar: a session without a trade keeps the last price (no change that day)."""
    return x.reindex(cal.union(x.index)).sort_index().ffill().reindex(cal)


def _session_on_or_after(cal: pd.DatetimeIndex, d: Any) -> pd.Timestamp | None:
    i = cal.searchsorted(pd.Timestamp(d))
    return cal[i] if i < len(cal) else None


def entry_scale(b: S.Bundle, entry_date: Any, entry_price: float) -> tuple[float, bool] | None:
    """Converts the adjusted price into rupees per share bought: value = quantity x adjusted price x scale.
    With the as-traded close stored for the entry day: scale = as-traded / adjusted that day (exact; a later bonus or
    split is absorbed). Before as-traded prices are stored (TradingView's adjusted history): scale = price paid /
    adjusted close that day, exact from the next session on, but the move from the price paid to that day's close is
    lost (estimated=True)."""
    t = pd.Timestamp(entry_date)
    if t < b.price.index[0]:
        return None
    adj_e = float(b.price.asof(t))
    if b.adj_factor is None:
        return 1.0, False
    first = b.adj_factor.first_valid_index()
    if first is not None and t >= first:
        fac = b.adj_factor.asof(t)
        if pd.notna(fac) and fac > 0:
            return 1.0 / float(fac), False
    return (float(entry_price) / adj_e, True) if adj_e > 0 else None


# ------------------------------------------------------------------ equity curve
def equity_curve(s: Session, uid: str) -> dict[str, Any]:
    pos = _positions(s, uid)
    if not pos:
        return {"available": False, "reason": "No positions recorded yet. Add your buys on this page to see your performance."}
    aspi = _aspi(s)
    cal = aspi.index
    first = _session_on_or_after(cal, min(p.entry_date for p in pos))
    if first is None:
        return {"available": False, "reason": "Your first position is newer than the latest market close."}
    i0 = cal.get_loc(first)
    days = cal[max(0, i0 - 1):]                              # the session before the first buy is the start (100)
    f = fee()
    value = pd.Series(0.0, index=days)
    buys = pd.Series(0.0, index=days)
    sales = pd.Series(0.0, index=days)
    divs = pd.Series(0.0, index=days)
    bundles: dict[str, S.Bundle | None] = {}
    skipped: list[str] = []
    estimated: list[str] = []
    for p in pos:
        if p.symbol not in bundles:
            bundles[p.symbol] = S.cse_stock(s, p.symbol)
        b = bundles[p.symbol]
        e = _session_on_or_after(days, p.entry_date)
        if b is None or e is None:
            skipped.append(p.symbol)
            continue
        px = on_calendar(b.price, days)
        sc = entry_scale(b, e, p.entry_price)
        if sc is None or pd.isna(px.loc[e]):
            skipped.append(p.symbol)
            continue
        scale, est = sc                                      # adjusted price -> rupees per share bought
        if est:
            estimated.append(p.symbol)
        q = float(p.quantity)
        x = _session_on_or_after(days, p.exit_date) if p.closed and p.exit_date else None
        held = (days >= e) & ((days < x) if x is not None else np.ones(len(days), dtype=bool))
        value[held] += q * px[held].fillna(0.0) * scale
        buys[e] += q * float(p.entry_price) * (1 + f)
        if x is not None and p.exit_price is not None:
            sales[x] += q * float(p.exit_price) * (1 - f)
        for d in b.dividends:                                # paid on shares held at the close before the XD date
            xd = _session_on_or_after(days, d["xd"])
            if xd is not None and e < xd and (x is None or xd <= x):
                divs[xd] += q * float(d["dps_adj"]) * scale
    if buys.sum() <= 0:
        return {"available": False, "reason": "No prices for the stocks in your positions yet.", "skipped": sorted(set(skipped))}
    prev = value.shift(1).fillna(0.0)
    base = prev + buys
    r = ((value + divs + sales) / base.where(base > 0) - 1).fillna(0.0)
    curve = (1 + r).cumprod() * 100
    a = on_calendar(aspi, days)
    a_idx = a / a.iloc[0] * 100
    pnl = value + (sales + divs - buys).cumsum()
    return {"available": True, "curve": curve, "aspi": a_idx, "value": value, "pnl": pnl,
            "dividends": round(float(divs.sum()), 2), "money_in": round(float(buys.sum()), 2), "money_out": round(float(sales.sum()), 2),
            "start": str(days[0].date()), "skipped": sorted(set(skipped)), "entry_estimated": sorted(set(x.split(".")[0] for x in estimated)),
            "fee_pct_per_side": round(f * 100, 4)}


# ------------------------------------------------------------------ risk of what is held now
def holdings(s: Session, uid: str) -> list[dict[str, Any]]:
    """Open positions merged per stock, valued at the latest close (in rupees, counting shares from later bonuses/splits)."""
    merged: dict[str, dict[str, Any]] = {}
    for p in _positions(s, uid):
        if p.closed:
            continue
        b = merged[p.symbol]["bundle"] if p.symbol in merged else S.cse_stock(s, p.symbol)
        if b is None or b.price.empty:
            continue
        sc = entry_scale(b, p.entry_date, p.entry_price)
        if sc is None:
            continue
        m = merged.setdefault(p.symbol, {"symbol": p.symbol, "label": b.label, "bundle": b, "value": 0.0})
        m["value"] += float(p.quantity) * float(b.price.iloc[-1]) * sc[0]     # counts shares from later bonuses/splits
    return list(merged.values())


def returns_matrix(bundles: dict[str, S.Bundle], cal: pd.DatetimeIndex, n: int = RISK_WINDOW) -> pd.DataFrame:
    """Daily returns of each holding on the CSE calendar (a day without a trade = no change), the last n sessions all
    of them have."""
    px = pd.DataFrame({k: on_calendar(b.price, cal) for k, b in bundles.items()})
    return px.pct_change(fill_method=None).iloc[1:].dropna(how="any").tail(n)


def risk_figures(rets: pd.DataFrame, values: pd.Series, market: pd.Series | None, af: float) -> dict[str, Any]:
    """The maths on its own (tested by hand): covariance-based volatility and each holding's share of it,
    correlations, diversification ratio, historical VaR/CVaR in rupees, beta to the market."""
    total = float(values.sum())
    w = (values / total).reindex(rets.columns)
    cov = rets.cov() * af
    sig = np.sqrt(np.diag(cov.values))
    var_p = float(w.values @ cov.values @ w.values)
    vol = math.sqrt(var_p) if var_p > 0 else 0.0
    share = (w.values * (cov.values @ w.values)) / var_p if var_p > 0 else np.zeros(len(w))   # sums to 1
    pnl = rets.values @ (w.values * total)           # each past day's moves applied to today's holdings, in rupees
    var95 = float(np.percentile(pnl, 5))
    tail = pnl[pnl <= var95]
    beta = None
    if market is not None:
        m = market.reindex(rets.index)
        pr = pd.Series(rets.values @ w.values, index=rets.index)
        ok = m.notna()
        if ok.sum() >= MIN_RISK and m[ok].var() > 0:
            beta = float(np.cov(pr[ok], m[ok])[0, 1] / np.var(m[ok], ddof=1))
    return {"total": total, "weights": w, "vol_each": pd.Series(sig, index=rets.columns), "volatility": vol,
            "risk_share": pd.Series(share, index=rets.columns), "correlation": rets.corr(),
            "diversification_ratio": float(w.values @ sig) / vol if vol > 0 else None,
            "var95": var95, "cvar95": float(tail.mean()) if len(tail) else var95, "beta": beta}


def risk_now(s: Session, uid: str) -> dict[str, Any]:
    hs = holdings(s, uid)
    if not hs:
        return {"available": False, "reason": "No open positions."}
    aspi = _aspi(s)
    cal = aspi.index
    af = S.cse_sessions_per_year(s)
    rets = returns_matrix({h["symbol"]: h["bundle"] for h in hs}, cal)
    if len(rets) < MIN_RISK:
        newest = max(hs, key=lambda h: h["bundle"].price.index[0])["label"]
        return {"available": False, "reason": f"Needs {MIN_RISK} sessions with prices for every holding; has {len(rets)} ({newest} is the newest)."}
    values = pd.Series({h["symbol"]: h["value"] for h in hs})
    fig = risk_figures(rets, values, aspi.pct_change(fill_method=None), af)
    thin = {h["symbol"]: S.thin_trading(h["bundle"].volume, RISK_WINDOW, cal) for h in hs}
    label = {h["symbol"]: h["label"] for h in hs}
    rows = [{"symbol": k, "label": label[k], "value": round(float(values[k]), 2), "weight": round(float(fig["weights"][k]), 6),
             "volatility": round(float(fig["vol_each"][k]), 6), "risk_share": round(float(fig["risk_share"][k]), 6),
             "thin": bool(thin[k]["thin"]), "share_no_trade": thin[k]["share_no_trade"]} for k in rets.columns]
    total = fig["total"]
    return {"available": True, "total_value": round(total, 2), "sessions": int(len(rets)), "from": str(rets.index[0].date()),
            "to": str(rets.index[-1].date()), "sessions_per_year": af,
            "volatility": round(fig["volatility"], 6), "beta": None if fig["beta"] is None else round(fig["beta"], 4),
            "diversification_ratio": None if fig["diversification_ratio"] is None else round(fig["diversification_ratio"], 4),
            "var95_lkr": round(fig["var95"], 2), "cvar95_lkr": round(fig["cvar95"], 2),
            "var95_pct": round(fig["var95"] / total, 6), "cvar95_pct": round(fig["cvar95"] / total, 6),
            "any_thin": any(r["thin"] for r in rows),
            "rows": sorted(rows, key=lambda r: -r["risk_share"]),
            "correlation": {"labels": [label[k] for k in rets.columns],
                            "matrix": [[round(float(v), 3) for v in row] for row in fig["correlation"].values]},
            "stress": stress(hs, values, aspi, rets)}


# ------------------------------------------------------------------ stress tests (the ASPI's worst episodes, replayed)
def worst_episodes(index: pd.Series, horizon: int, k: int = 2) -> list[tuple[pd.Timestamp, pd.Timestamp, float]]:
    """The k worst non-overlapping changes of `index` over `horizon` sessions: (start, end, change)."""
    x = index.dropna()
    ch = (x / x.shift(horizon) - 1).dropna().sort_values()
    out: list[tuple[pd.Timestamp, pd.Timestamp, float]] = []
    for end, r in ch.items():
        st = x.index[x.index.get_loc(end) - horizon]
        if any(not (end < a or st > b) for a, b, _ in out):
            continue
        out.append((st, end, float(r)))
        if len(out) >= k:
            break
    return out


def stress(hs: list[dict[str, Any]], values: pd.Series, aspi: pd.Series, rets: pd.DataFrame) -> list[dict[str, Any]]:
    """Today's holdings put through the ASPI's worst episodes: each holding's own move then if it was listed through
    the episode, otherwise its beta to the ASPI x the ASPI's move (marked as estimated)."""
    ar = aspi.pct_change(fill_method=None).reindex(rets.index)
    ok = ar.notna()
    mv = float(ar[ok].var()) if ok.sum() > 1 else 0.0
    betas = {k: float(np.cov(rets.loc[ok, k], ar[ok])[0, 1] / mv) if mv > 0 else 1.0 for k in rets.columns}
    total = float(values.sum())
    out = []
    for label, h in STRESS_HORIZONS.items():
        for st, en, ra in worst_episodes(aspi, h, 2):
            loss, by_beta, est = 0.0, 0.0, []
            for x in hs:
                p = x["bundle"].price
                b_move = betas.get(x["symbol"], 1.0) * ra
                if p.index[0] <= st and en <= p.index[-1]:
                    m = float(p.asof(en) / p.asof(st) - 1)
                else:
                    m = b_move
                    est.append(x["label"])
                loss += float(values[x["symbol"]]) * m
                by_beta += float(values[x["symbol"]]) * b_move
            out.append({"horizon": label, "sessions": h, "from": str(st.date()), "to": str(en.date()), "aspi": round(ra, 6),
                        "portfolio_pct": round(loss / total, 6), "portfolio_lkr": round(loss, 2),
                        "beta_pct": round(by_beta / total, 6), "beta_lkr": round(by_beta, 2), "estimated": est})
    return out


# ------------------------------------------------------------------ "what if you had held your lists"
def basket(s: Session, symbols: list[str], window: str = "3y", name: str = "", weights: Any = None, rebalance: str = "monthly") -> dict[str, Any]:
    """A basket of CSE stocks (gs-quant's basket_series): the CSE fee on every rebalance (on the share of the basket
    traded) and on the first purchase, total return; against the ASPI over the same sessions.
    weights: None / "equal", "inverse_vol" (each stock's share is proportional to 1 / its volatility in the year before the
    start), or a list of numbers in the order of `symbols` (normalised to 100%). rebalance: "monthly", "weekly" or "none"
    (bought once, never rebalanced)."""
    if rebalance not in ("monthly", "weekly", "none"):
        raise ValueError("Rebalance must be monthly, weekly or none.")
    if isinstance(weights, list):
        if len(weights) != len(symbols) or any((not isinstance(x, (int, float))) or x < 0 for x in weights) or sum(weights) <= 0:
            raise ValueError("Give one weight (0 or more) for each stock, with at least one above 0.")
    aspi = _aspi(s)
    cal = aspi.index
    bs = {sym: S.cse_stock(s, sym) for sym in symbols}
    missing = [k.split(".")[0] for k, v in bs.items() if v is None or len(v.total) < 2]
    bs = {k: v for k, v in bs.items() if v is not None and len(v.total) >= 2}
    if len(bs) < 2:
        return {"available": False, "name": name, "reason": "Needs at least two stocks with prices on the list."}
    wanted = S.window_slice(aspi, window).index[0]
    too_new: list[str] = []
    if window != "all":                     # a stock listed after the window starts would cut every stock's history short
        old_enough = {k: v for k, v in bs.items() if v.total.index[0] <= wanted + pd.Timedelta(days=10)}
        if len(old_enough) >= 2:
            too_new = [v.label for k, v in bs.items() if k not in old_enough]
            bs = old_enough
    start = max(b.total.index[0] for b in bs.values())
    newest = max(bs.values(), key=lambda b: b.total.index[0]).label
    series = [S.window_slice(on_calendar(b.total, cal).loc[start:].dropna(), window) for b in bs.values()]
    if min(len(x) for x in series) < MIN_BASKET:
        return {"available": False, "name": name, "reason": f"Fewer than {MIN_BASKET} sessions that every stock has ({newest} is the newest)."}
    f = fee()
    n = len(series)
    w, wnote = _weights(weights, list(bs), symbols, cal, bs, series[0].index[0])
    freq = {"monthly": ts.RebalFreq.MONTHLY, "weekly": ts.RebalFreq.WEEKLY}.get(rebalance)
    px = pd.concat(series, axis=1, join="inner")
    hold = (px / px.iloc[0] * w).sum(axis=1) * 100 * (1 - f)      # bought once in these weights, never rebalanced
    lvl = hold if freq is None else ts.basket_series(series, list(w), costs=[f] * n, rebal_freq=freq) * (1 - f)
    px = px.reindex(lvl.index)
    hold = hold.reindex(lvl.index)
    ends = [rebalanced_end(px, monthly_on_day(px.index, d), f, w) * (1 - f) for d in range(1, 29)] if rebalance == "monthly" else None
    a = aspi.reindex(lvl.index)
    a_idx = a / a.iloc[0] * 100
    rates = R.tbill_series(s)
    af = S.cse_sessions_per_year(s)
    return {"available": True, "name": name, "symbols": [b.label for b in bs.values()], "missing": missing, "too_new": too_new,
            "start": str(lvl.index[0].date()), "end": str(lvl.index[-1].date()),
            "limited_by": newest if start > wanted + pd.Timedelta(days=10) else None,
            "level": lvl, "aspi": a_idx, "hold": hold, "figures": M.single(lvl, rates, af), "aspi_figures": M.single(a_idx, rates, af),
            "hold_figures": M.single(hold, rates, af),
            "rebalance_day_range": [round(min(ends) / 100 - 1, 6), round(max(ends) / 100 - 1, 6)] if ends else None,
            "weights": [{"symbol": b.label, "weight": round(float(x), 6)} for b, x in zip(bs.values(), w)], "weights_note": wnote, "rebalance": rebalance,
            "relative": M.relative(lvl, a_idx, rates, af)}


def monthly_on_day(cal: pd.DatetimeIndex, day: int) -> set[pd.Timestamp]:
    """The first session on or after `day` of each month (after the first session)."""
    out = set()
    for y, m in sorted({(d.year, d.month) for d in cal}):
        i = cal.searchsorted(pd.Timestamp(y, m, day))
        if 0 < i < len(cal):
            out.add(cal[i])
    return out


def _weights(spec: Any, keys: list[str], symbols: list[str], cal: pd.DatetimeIndex, bs: dict[str, S.Bundle], start: pd.Timestamp) -> tuple[np.ndarray, str | None]:
    """Weights for the stocks that made it into the basket (in order), summing to 1, and a note if they changed."""
    n = len(keys)
    if isinstance(spec, list):
        by = dict(zip(symbols, spec))
        w = np.array([float(by[k]) for k in keys])
        if w.sum() <= 0:
            return np.full(n, 1 / n), "The stocks with weight above 0 have too little history: equal weights used."
        note = "Some stocks were left out, so the other weights were scaled up to 100%." if len(keys) != len(symbols) else None
        return w / w.sum(), note
    if spec == "inverse_vol":
        vols = []
        for k in keys:
            r = on_calendar(bs[k].total, cal).loc[:start].pct_change(fill_method=None).dropna().tail(250)
            vols.append(float(r.std()) if len(r) >= 120 else float("nan"))
        v = np.array(vols)
        if np.isfinite(v).all() and (v > 0).all():
            w = 1 / v
            return w / w.sum(), None
        return np.full(n, 1 / n), "Not enough history before the start to measure each stock's volatility: equal weights used."
    return np.full(n, 1 / n), None


def rebalanced_end(px: pd.DataFrame, rebal: set[pd.Timestamp], cost: float, weights: np.ndarray | None = None) -> float:
    """End value (start 100) of an equal-weight basket rebalanced on `rebal`, with gs-quant's backtest_basket rules:
    the fee is charged on the share of the basket traded at each rebalance (cost x |target - actual weight|).
    Identical to gs-quant on its own rebalance dates (tested); used to show how much the result depends on the day."""
    v = px.to_numpy(dtype=float)
    n = v.shape[1]
    w = np.full(n, 1 / n) if weights is None else np.asarray(weights, dtype=float)
    units = 100 * w / v[0]
    out = 100.0
    for i in range(1, len(v)):
        out = out + float(units @ (v[i] - v[i - 1]))
        if px.index[i] in rebal:
            actual = units * v[i] / out
            out -= cost * float(np.abs(w - actual).sum()) * out
            units = out * w / v[i]
    return out


def list_baskets(s: Session, uid: str, window: str = "3y") -> list[dict[str, Any]]:
    from ..accounts import group_symbols, lists_overview
    return [{"id": g["id"], **basket(s, group_symbols(s, uid, g["id"]), window, g["name"])} for g in lists_overview(s, uid)]
