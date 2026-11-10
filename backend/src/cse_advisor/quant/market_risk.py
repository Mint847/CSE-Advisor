"""ASPI risk panel (MKT): how jumpy the market is now compared with its own past, how far it sits below its high, and how
closely it follows Wall Street and the rupee. Measurements only, nothing here changes a verdict."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

import gs_quant.timeseries as ts
from gs_quant.timeseries import Window

from . import correlation as C
from . import metrics as M
from . import rates as R
from . import series as S

VOL_WINDOW = 63                      # sessions (about 3 months) behind the volatility reading
CORR_WINDOW = 120
CHART_YEARS = 3
CALM, STORMY = 33, 80                # percentile of today's volatility among all past 63-session volatilities


def regime(pctile: float) -> str:
    return "calm" if pctile < CALM else "stormy" if pctile >= STORMY else "normal"


def pts(x: pd.Series, nd: int = 4) -> list[dict[str, Any]]:
    x = x.replace([np.inf, -np.inf], np.nan).dropna()
    return [{"time": str(t.date()), "value": round(float(v), nd)} for t, v in x.items()]


def build(s: Session) -> dict[str, Any]:
    a = S.index_series(s, "^ASPI")
    if a is None or len(a.price) < 300:
        return {"available": False, "reason": "Needs at least a year and a bit of ASPI history."}
    px = a.price
    af = S.cse_sessions_per_year(s)
    rates = R.tbill_series(s)
    vol = (ts.volatility(px, Window(VOL_WINDOW, VOL_WINDOW), annualization_factor=af) / 100).dropna()
    now_v = float(vol.iloc[-1])
    pct = float((vol <= now_v).mean() * 100)
    dd = M.drawdown_series(px)
    cut = px.index[-1] - pd.DateOffset(years=CHART_YEARS)
    year = S.window_slice(px, "1y")
    f1 = M.single(year, rates, af)
    ewma = M.ewma_volatility(px, af)
    days = px.index
    corr: dict[str, Any] = {}
    for key, ident, label in (("spx", "^SPX", "S&P 500 (the evening before)"), ("usd", "^USDLKR", "USD/LKR")):
        got = C.load(s, ident, days)
        if got is None:
            continue
        r_a = px.pct_change(fill_method=None)
        both = pd.concat([r_a, got[1]], axis=1).dropna()
        if len(both) < CORR_WINDOW + 20:
            continue
        rc = both.iloc[:, 0].rolling(CORR_WINDOW).corr(both.iloc[:, 1]).dropna()
        full = float(both.iloc[:, 0].corr(both.iloc[:, 1]))
        corr[key] = {"label": label, "now": round(float(rc.iloc[-1]), 3), "all": round(full, 3), "points": pts(rc[rc.index > cut], 3)}
    out = {"available": True, "as_of": str(px.index[-1].date()), "sessions_per_year": af,
           "volatility": {"now": round(now_v, 4), "percentile": round(pct, 1), "regime": regime(pct), "median": round(float(vol.median()), 4), "window": VOL_WINDOW,
                          "recent": round(float(ewma), 4) if ewma else None, "points": pts(vol[vol.index > cut])},
           "drawdown": {"now": round(float(dd.iloc[-1]), 4), "max_1y": f1.get("max_drawdown"), "points": pts(dd[dd.index > cut])},
           "year": {k: f1.get(k) for k in ("total_return", "volatility", "sharpe", "max_drawdown", "var95")},
           "correlation": corr}
    out["summary"] = summary(out)
    return out


def summary(o: dict[str, Any]) -> list[str]:
    v, d = o["volatility"], o["drawdown"]
    lines = [f"The ASPI's volatility over the last {v['window']} sessions is {v['now'] * 100:.1f}% a year, higher than {v['percentile']:.0f}% of the past readings: a {v['regime']} market "
             f"(the typical reading is {v['median'] * 100:.1f}%)."]
    lines.append("It is at its high." if d["now"] > -0.005 else f"It is {abs(d['now']) * 100:.1f}% below its previous high.")
    sp = o["correlation"].get("spx")
    if sp:
        lines.append(f"Over the last {CORR_WINDOW} sessions its daily moves matched Wall Street's of the evening before with a correlation of {sp['now']:.2f} (whole history {sp['all']:.2f}); "
                     + ("close to 0 means the CSE has mostly followed its own news." if abs(sp["now"]) < 0.2 else "the CSE is following US moves more than usual." if sp["now"] > sp["all"] + 0.1 else "about its usual link."))
    lines.append("These describe the past, not the future, and never change an engine's verdict. Not financial advice.")
    return lines
