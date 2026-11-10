"""Quant Lab strategy backtester: try a trading rule on the app's own stored CSE prices and see what it would have done.

How it works (so the numbers can be trusted):
* Rules are conditions on a stock's past prices (an average, a breakout, a dip, a dividend date...). Each is computed with
  trailing windows only, so the value on a day uses nothing from later days (a test cuts the future off and checks).
* A rule is looked at after the close (14:30). An order it creates is filled at the OPEN of the next session on which the
  stock actually traded: a stock with no trade that day can't be bought or sold.
* One class, `Runner`, holds all the decisions and the money. A fast numpy pass uses it for the random-reshuffle test and the
  half-period check; the same class feeds gs-quant's PredefinedAssetEngine, which executes the orders, books the trades and
  values the account. The two are compared on every run (`parity`).
* Costs are the CSE fees (costs.brokerage_pct_per_side) plus a slippage allowance, charged on every buy and sell; cash earns
  the CBSL T-bill rate (Actual/365). Both go through the engine as OrderCost orders.
* Prices are the app's adjusted prices; "total return" basis reinvests recorded dividends (as in QNT).

What this cannot tell you: the stocks you pick today are survivors you chose with hindsight, past results are not forecasts,
and a rule tried many times on the same data will eventually look good by luck (the reshuffle test is the check on that).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import threading
import time
import uuid
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

os.environ.setdefault("TQDM_DISABLE", "1")          # the engine would otherwise print a progress bar to the console

from ..config import settings
from ..db import Announcement, SessionLocal
from . import metrics as M
from . import rates as R
from . import series as S

OPEN_T, CLOSE_T = dt.time(9, 30), dt.time(14, 30)
BUFFER = 0.03                    # a buy is sized 3% under the money available, to cover the next open gapping up
LIQ_WINDOW = 20                  # sessions behind the liquidity ranking of entry candidates
MIN_ROWS = 250                   # a stock needs a year of stored prices to be offered
MAX_SYMBOLS = 15
MAX_DEPTH = 4
MAX_LEAVES = 12
HALF_MIN = 60                    # sessions each half needs for the stability check
PARITY_TOL = 1e-6                # relative difference allowed between the fast pass and gs-quant's engine


# =================================================================== rules
def _p(key: str, label: str, default: float, lo: float, hi: float, step: float = 1) -> dict[str, Any]:
    return {"key": key, "label": label, "default": default, "min": lo, "max": hi, "step": step}


RULES: dict[str, dict[str, Any]] = {
    "monthly": {"label": "First trading day of the month", "text": "it is the first trading day of a month", "params": [],
                "help": "A calendar schedule, not a signal: use it for the simplest test, 'buy every month'."},
    "weekly": {"label": "First trading day of the week", "text": "it is the first trading day of a week", "params": [],
               "help": "A calendar schedule: buy every week."},
    "ma_above": {"label": "Price above its average", "text": "the price is above its {n}-session average",
                 "params": [_p("n", "average of the last … sessions", 50, 2, 400)],
                 "help": "A simple moving average: the average closing price of the last n sessions. Price above it = an uptrend."},
    "ma_below": {"label": "Price below its average", "text": "the price is below its {n}-session average",
                 "params": [_p("n", "average of the last … sessions", 50, 2, 400)],
                 "help": "Price below the average = a downtrend. Often used as the exit."},
    "trend_up": {"label": "Uptrend (price > 50-day > 200-day exponential average)", "text": "it is in an uptrend (price above its 50-day, which is above its 200-day, exponential average)",
                 "params": [], "help": "The trend filter of the 14-year factor study: close above the 50-day average, which is above the 200-day average."},
    "ma_cross_up": {"label": "Short average crosses above long average", "text": "the {fast}-session average has just crossed above the {slow}-session average",
                    "params": [_p("fast", "short average (sessions)", 50, 2, 300), _p("slow", "long average (sessions)", 200, 3, 400)],
                    "help": "The 'golden cross', true only on the day the short average moves above the long one."},
    "ma_fast_above": {"label": "Short average above long average", "text": "the {fast}-session average is above the {slow}-session average",
                      "params": [_p("fast", "short average (sessions)", 50, 2, 300), _p("slow", "long average (sessions)", 200, 3, 400)],
                      "help": "True on every day the short average is above the long one (use it as 'stay in while...')."},
    "breakout": {"label": "New high", "text": "the price makes a new {n}-session high",
                 "params": [_p("n", "highest close of the last … sessions", 55, 5, 400)],
                 "help": "Today's close is above every close of the previous n sessions: a breakout."},
    "near_high": {"label": "Close to its high", "text": "the price is within {pct}% of its {n}-session high",
                  "params": [_p("pct", "within …% of the high", 10, 1, 50), _p("n", "high of the last … sessions", 250, 20, 600)],
                  "help": "Strong stocks tend to stay near their highs; weak ones are far below."},
    "rsi_below": {"label": "RSI below a level (oversold)", "text": "the {n}-session RSI is below {level}",
                  "params": [_p("level", "RSI below …", 30, 1, 99), _p("n", "RSI length (sessions)", 14, 2, 60)],
                  "help": "RSI measures recent momentum from 0 to 100. Below 30 is called oversold (it has fallen fast)."},
    "rsi_above": {"label": "RSI above a level (overbought)", "text": "the {n}-session RSI is above {level}",
                  "params": [_p("level", "RSI above …", 70, 1, 99), _p("n", "RSI length (sessions)", 14, 2, 60)],
                  "help": "Above 70 is called overbought (it has risen fast)."},
    "zscore_below": {"label": "Far below its recent average (dip)", "text": "the price is more than {z} standard deviations below its {n}-session average",
                     "params": [_p("z", "standard deviations below", 2, 0.5, 5, 0.1), _p("n", "average of the last … sessions", 60, 10, 250)],
                     "help": "The z-score says how unusual today's price is against its own recent range. Used for 'buy the dip' (mean reversion)."},
    "zscore_above": {"label": "Back at or above its recent average", "text": "the price is at least {z} standard deviations above its {n}-session average",
                     "params": [_p("z", "standard deviations above (0 = back at the average)", 0, -3, 5, 0.1), _p("n", "average of the last … sessions", 60, 10, 250)],
                     "help": "The usual exit for a dip trade: the price has come back to its average."},
    "drop_day": {"label": "Fell sharply today", "text": "the price fell at least {pct}% today", "params": [_p("pct", "fell at least …%", 5, 1, 30, 0.5)],
                 "help": "A one-day drop, for 'buy the panic'."},
    "rs_rank": {"label": "Among your strongest stocks", "text": "it is among the strongest {top_pct}% of your stocks over the last {n} sessions",
                "params": [_p("top_pct", "strongest …% of your stocks", 40, 5, 100), _p("n", "return over the last … sessions", 120, 10, 400)],
                "help": "Relative strength: ranks your chosen stocks by their recent return. Needs at least 3 stocks."},
    "beats_aspi": {"label": "Beating the ASPI", "text": "it has beaten the ASPI over the last {n} sessions", "params": [_p("n", "over the last … sessions", 120, 10, 400)],
                   "help": "The stock's return over the period is higher than the market's (ASPI)."},
    "before_xd": {"label": "Before a dividend's ex-date", "text": "a recorded dividend goes ex in {k} sessions (once announced)",
                  "params": [_p("k", "sessions before the ex-dividend date", 5, 1, 40)],
                  "help": "Uses dividends the app has recorded (from late 2023), and only from the day after they were announced. Combine with a short holding time."},
}

PRESETS: list[dict[str, Any]] = [
    {"id": "monthly", "name": "Buy every month, hold a month", "blurb": "The simplest control: no skill, just a calendar. Everything else should beat this.",
     "spec": {"entry": {"type": "monthly"}, "exit": {"hold_days": 21}, "max_positions": 5}},
    {"id": "factor", "name": "Trend + near high + strong (the factor-study rule)", "blurb": "The one rule that held up in the app's 14-year study: an uptrend, close to the high, among the strongest stocks. Exit when the price drops under its 50-day average.",
     "spec": {"entry": {"op": "all", "rules": [{"type": "trend_up"}, {"type": "near_high", "pct": 10, "n": 250}, {"type": "rs_rank", "top_pct": 40, "n": 120}]},
              "exit": {"hold_days": 90, "rule": {"type": "ma_below", "n": 50}}, "max_positions": 4}},
    {"id": "dip", "name": "Buy dips in an uptrend", "blurb": "Mean reversion: buy a stock far below its recent average while its long trend is up; sell when it recovers, or on a stop.",
     "spec": {"entry": {"op": "all", "rules": [{"type": "ma_above", "n": 200}, {"type": "zscore_below", "z": 2, "n": 60}]},
              "exit": {"hold_days": 25, "stop_pct": 10, "rule": {"type": "zscore_above", "z": 0, "n": 60}}, "max_positions": 5}},
    {"id": "breakout", "name": "Breakouts with a trailing stop", "blurb": "Buy a new 55-session high in an uptrend; let it run and sell when it falls 12% from its highest close since you bought.",
     "spec": {"entry": {"op": "all", "rules": [{"type": "breakout", "n": 55}, {"type": "ma_above", "n": 200}]},
              "exit": {"trail_pct": 12}, "max_positions": 5}},
    {"id": "cross", "name": "Golden cross", "blurb": "Buy when the 50-session average crosses above the 200-session average; sell when it crosses back down.",
     "spec": {"entry": {"type": "ma_cross_up", "fast": 50, "slow": 200},
              "exit": {"rule": {"op": "not", "rule": {"type": "ma_fast_above", "fast": 50, "slow": 200}}}, "max_positions": 5}},
    {"id": "dividend", "name": "Dividend run-up", "blurb": "Buy 5 sessions before a recorded ex-dividend date, hold a week. Only dividends the app has recorded (from late 2023) can trigger it.",
     "spec": {"entry": {"type": "before_xd", "k": 5}, "exit": {"hold_days": 7}, "max_positions": 5}},
]


def rule_text(rule: dict[str, Any]) -> str:
    """The rule in words (used in the result: exactly what was tested)."""
    if "op" in rule:
        if rule["op"] == "not":
            return f"NOT ({rule_text(rule['rule'])})"
        joiner = " AND " if rule["op"] == "all" else " OR "
        return joiner.join(f"({rule_text(r)})" if "op" in r else rule_text(r) for r in rule["rules"])
    spec = RULES[rule["type"]]
    return spec["text"].format(**{k: _fmtnum(v) for k, v in rule.items() if k != "type"})


def _fmtnum(v: Any) -> str:
    return str(int(v)) if float(v) == int(v) else f"{float(v):g}"


def check_rule(r: Any, depth: int = 0, leaves: list[int] | None = None) -> dict[str, Any]:
    """Validate and normalise a rule tree: {"type": ..., params} or {"op": "all"|"any"|"not", ...}."""
    leaves = leaves if leaves is not None else [0]
    if not isinstance(r, dict):
        raise ValueError("A rule must be a condition or a combination of conditions.")
    if depth > MAX_DEPTH:
        raise ValueError(f"Conditions can be combined at most {MAX_DEPTH} levels deep.")
    if "op" in r:
        op = r["op"]
        if op == "not":
            return {"op": "not", "rule": check_rule(r.get("rule"), depth + 1, leaves)}
        if op not in ("all", "any"):
            raise ValueError("A combination must be 'all', 'any' or 'not'.")
        subs = r.get("rules")
        if not isinstance(subs, list) or not 1 <= len(subs) <= 8:
            raise ValueError("A combination needs between 1 and 8 conditions.")
        return {"op": op, "rules": [check_rule(x, depth + 1, leaves) for x in subs]}
    t = r.get("type")
    if t not in RULES:
        raise ValueError(f"Unknown condition '{t}'.")
    leaves[0] += 1
    if leaves[0] > MAX_LEAVES:
        raise ValueError(f"At most {MAX_LEAVES} conditions in one rule.")
    out: dict[str, Any] = {"type": t}
    for p in RULES[t]["params"]:
        try:
            v = float(r.get(p["key"], p["default"]))
        except (TypeError, ValueError):
            raise ValueError(f"{RULES[t]['label']}: '{p['label']}' must be a number.") from None
        if not math.isfinite(v) or not p["min"] <= v <= p["max"]:
            raise ValueError(f"{RULES[t]['label']}: '{p['label']}' must be between {_fmtnum(p['min'])} and {_fmtnum(p['max'])}.")
        out[p["key"]] = int(v) if float(p["step"]).is_integer() and float(v).is_integer() else v
    if t in ("ma_cross_up", "ma_fast_above") and out["fast"] >= out["slow"]:
        raise ValueError("The short average must be shorter than the long one.")
    return out


def _opt(v: Any, lo: float, hi: float, label: str, integer: bool = False) -> float | None:
    if v in (None, "", 0, 0.0):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number.") from None
    if not lo <= x <= hi:
        raise ValueError(f"{label} must be between {_fmtnum(lo)} and {_fmtnum(hi)} (or empty).")
    return int(x) if integer else x


def default_slippage() -> float:
    return float(settings()["costs"].get("slippage_pct", 0.2))


def normalise(spec: Any) -> dict[str, Any]:
    """A clean copy of a strategy, or a ValueError in plain words."""
    if not isinstance(spec, dict):
        raise ValueError("The strategy must be an object.")
    syms = spec.get("symbols")
    if not isinstance(syms, list) or not syms:
        raise ValueError("Choose at least one stock.")
    syms = list(dict.fromkeys(str(x) for x in syms))
    if len(syms) > MAX_SYMBOLS:
        raise ValueError(f"At most {MAX_SYMBOLS} stocks per test.")
    per = spec.get("years", 5)
    if per != "all":
        try:
            per = int(per)
        except (TypeError, ValueError):
            raise ValueError("The period must be a number of years or 'all'.") from None
        if not 1 <= per <= 15:
            raise ValueError("The period must be between 1 and 15 years.")
    try:
        capital = float(spec.get("capital", 1_000_000))
    except (TypeError, ValueError):
        raise ValueError("Starting money must be a number.") from None
    if not 100_000 <= capital <= 1_000_000_000:
        raise ValueError("Starting money must be between LKR 100,000 and 1,000,000,000.")
    try:
        mp = int(spec.get("max_positions", min(5, len(syms))))
    except (TypeError, ValueError):
        raise ValueError("The number of positions must be a whole number.") from None
    if not 1 <= mp <= MAX_SYMBOLS:
        raise ValueError(f"Positions at once must be between 1 and {MAX_SYMBOLS}.")
    basis = spec.get("basis", "total")
    if basis not in ("total", "price"):
        raise ValueError("Basis must be 'total' or 'price'.")
    try:
        slip = float(spec.get("slippage_pct", default_slippage()))
    except (TypeError, ValueError):
        raise ValueError("Slippage must be a number.") from None
    if not 0 <= slip <= 3:
        raise ValueError("Slippage must be between 0% and 3%.")
    ex = spec.get("exit") or {}
    exit_ = {"hold_days": _opt(ex.get("hold_days"), 1, 2000, "Holding time", True), "stop_pct": _opt(ex.get("stop_pct"), 0.5, 90, "Stop loss"),
             "target_pct": _opt(ex.get("target_pct"), 0.5, 1000, "Profit target"), "trail_pct": _opt(ex.get("trail_pct"), 0.5, 90, "Trailing stop"),
             "rule": check_rule(ex["rule"]) if ex.get("rule") else None}
    entry = check_rule(spec.get("entry"))
    if "rs_rank" in json.dumps(entry) + json.dumps(exit_) and len(syms) < 3:
        raise ValueError("'Among your strongest stocks' needs at least 3 stocks to rank.")
    return {"name": str(spec.get("name") or "My strategy")[:60], "symbols": syms, "years": per, "capital": capital, "max_positions": min(mp, len(syms)),
            "basis": basis, "slippage_pct": slip, "entry": entry, "exit": exit_}


# =================================================================== indicators (vectorised; parity-tested against gs-quant)
def sma(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).mean()


def ema(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(span=n, adjust=False, min_periods=n).mean()


def zscore(x: pd.Series, n: int) -> pd.Series:
    """How many standard deviations today's price is from its n-session average (sample std, as gs-quant's zscores)."""
    return (x - x.rolling(n, min_periods=n).mean()) / x.rolling(n, min_periods=n).std()


def rsi(x: pd.Series, n: int) -> pd.Series:
    """Wilder's RSI exactly as gs-quant's relative_strength_index: smoothed averages seeded with the mean of the first n
    changes after the first, then ((n-1) x previous + today) / n."""
    v = x.to_numpy(float)
    out = np.full(len(v), np.nan)
    ok = np.flatnonzero(~np.isnan(v))
    if len(ok) < n + 2:
        return pd.Series(out, index=x.index)
    a = v[ok]
    d = np.diff(a)                                       # d[k] = a[k+1] - a[k]
    gain, loss = np.clip(d, 0, None), np.clip(-d, 0, None)
    first = n + 1                                        # position in a of the first RSI value (as gs-quant)
    g = float(gain[1:n + 1].mean())                       # changes at positions 2..n+1 of a
    l_ = float(loss[1:n + 1].mean())
    res = np.full(len(a), np.nan)
    res[first] = 100.0 if l_ == 0 else 100 - 100 / (1 + g / l_)
    for k in range(first + 1, len(a)):
        g = ((n - 1) * g + gain[k - 1]) / n
        l_ = ((n - 1) * l_ + loss[k - 1]) / n
        res[k] = 100.0 if l_ == 0 else 100 - 100 / (1 + g / l_)
    out[ok] = res
    return pd.Series(out, index=x.index)


# =================================================================== the data a test runs on
@dataclass
class Universe:
    symbols: list[str]
    labels: list[str]
    days: pd.DatetimeIndex                      # the CSE's sessions (the ASPI's dates)
    sig: pd.DataFrame                           # price the rules look at (adjusted close, carried over days without a trade)
    vol: pd.DataFrame                           # shares traded (NaN where the stock has no row that session)
    tradable: np.ndarray                        # [day, stock]: the stock traded that session
    t_open: np.ndarray                          # price a fill gets (trade basis); NaN where the stock didn't trade
    t_close: np.ndarray                         # price the account is valued at (trade basis, carried over)
    aspi: pd.Series                             # ASPI on the sessions
    xd: dict[int, list[tuple[int, int]]]        # stock -> [(ex-dividend session, first session after the announcement)]
    basis: str
    dividends_note: dict[str, Any] = field(default_factory=dict)
    nxt: np.ndarray = field(default=None)       # [day, stock]: next session > day on which the stock trades (-1 none)
    gap: np.ndarray = field(default=None)       # calendar days from the previous session
    liq: np.ndarray = field(default=None)       # 20-session average traded value (ranking of entry candidates)

    def __post_init__(self) -> None:
        n, m = self.t_close.shape
        nxt = np.full((n, m), -1, dtype=np.int64)
        for k in range(n - 2, -1, -1):
            nxt[k] = np.where(self.tradable[k + 1], k + 1, nxt[k + 1])
        self.nxt = nxt
        self.gap = np.r_[0, np.diff(self.days.values.astype("datetime64[D]").astype(np.int64))].astype(float)
        self.refresh_liq()

    def refresh_liq(self) -> None:
        tv = (self.sig * self.vol.fillna(0)).rolling(LIQ_WINDOW, min_periods=5).mean()
        self.liq = tv.fillna(0).to_numpy(float)


def load_universe(s: Session, symbols: list[str], basis: str) -> Universe:
    a = S.index_series(s, "^ASPI")
    if a is None or a.price.empty:
        raise LookupError("No ASPI history stored yet: the market's trading days are unknown.")
    days = a.price.index
    cols, labels, sig, vol, t_o, t_c, tr = [], [], {}, {}, {}, {}, {}
    xd: dict[int, list[tuple[int, int]]] = {}
    left_out: dict[str, int] = {}
    for sym in symbols:
        b = S.cse_stock(s, sym)
        if b is None or len(b.price) < MIN_ROWS or b.open is None:
            raise LookupError(f"{sym.split('.')[0]}: fewer than {MIN_ROWS} sessions of stored prices, so it can't be tested.")
        j = len(cols)
        cols.append(sym)
        labels.append(b.label)
        full = b.series(basis)
        price = b.price
        on = lambda x: x.reindex(days.union(x.index)).sort_index().ffill().reindex(days)   # noqa: E731 - carried over days without a trade
        sig[sym] = on(price)
        t_c[sym] = on(full)
        k = (full / price).reindex(price.index)
        op = (b.open * k).reindex(days)
        vv = b.volume.reindex(days)
        trade = (vv.fillna(0) > 0) & op.notna() & (op > 0)
        vol[sym] = vv
        t_o[sym] = op.where(trade)
        tr[sym] = trade.to_numpy(bool)
        left_out[b.label] = len(b.dividends_left_out)
        xd[j] = dividend_dates(s, sym, days)
    U = Universe(symbols=cols, labels=labels, days=days, sig=pd.DataFrame(sig, index=days)[cols], vol=pd.DataFrame(vol, index=days)[cols],
                 tradable=np.column_stack([tr[c] for c in cols]), t_open=np.column_stack([t_o[c].to_numpy(float) for c in cols]),
                 t_close=np.column_stack([t_c[c].to_numpy(float) for c in cols]), aspi=a.price, xd=xd, basis=basis,
                 dividends_note={"left_out": {k: v for k, v in left_out.items() if v}})
    return U


def dividend_dates(s: Session, symbol: str, days: pd.DatetimeIndex) -> list[tuple[int, int]]:
    """(ex-dividend session, first session after the announcement) for each recorded cash dividend. A rule may only use
    a dividend from the session after it was announced."""
    best: dict[str, pd.Timestamp] = {}
    for ann in s.scalars(select(Announcement).where(Announcement.symbol == symbol)):
        ev = ann.event or {}
        if ev.get("kind") != "cash_dividend" or not ev.get("xd") or ann.date is None:
            continue
        x = str(ev["xd"])
        d = pd.Timestamp(ann.date).normalize()
        if x not in best or d < best[x]:
            best[x] = d
    out = []
    for x, d in best.items():
        xi = int(days.searchsorted(pd.Timestamp(x)))
        ai = int(days.searchsorted(d, side="right"))        # strictly after the announcement day (it may come after the close)
        if xi < len(days):
            out.append((xi, ai))
    return sorted(out)


# =================================================================== evaluating rules
def _cols(df: pd.DataFrame, f: Callable[[pd.Series], pd.Series]) -> pd.DataFrame:
    return pd.DataFrame({c: f(df[c]) for c in df.columns}, index=df.index)


class _Eval:
    """Turns a rule tree into a [day, stock] True/False table, with each distinct condition computed once."""

    def __init__(self, U: Universe) -> None:
        self.U = U
        self.memo: dict[str, pd.DataFrame] = {}

    def run(self, rule: dict[str, Any]) -> np.ndarray:
        return self._tree(rule).fillna(False).to_numpy(bool)

    def _tree(self, r: dict[str, Any]) -> pd.DataFrame:
        if "op" in r:
            if r["op"] == "not":
                return ~self._tree(r["rule"]).fillna(False).astype(bool)
            parts = [self._tree(x).fillna(False).astype(bool) for x in r["rules"]]
            out = parts[0]
            for p in parts[1:]:
                out = (out & p) if r["op"] == "all" else (out | p)
            return out
        key = json.dumps(r, sort_keys=True)
        if key not in self.memo:
            self.memo[key] = self._leaf(r)
        return self.memo[key]

    def _leaf(self, r: dict[str, Any]) -> pd.DataFrame:
        U, c, t = self.U, self.U.sig, r["type"]
        n = int(r.get("n", 0))
        if t in ("monthly", "weekly"):
            per = U.days.to_period("M" if t == "monthly" else "W")
            first = np.r_[True, per[1:] != per[:-1]]
            return pd.DataFrame({k: first for k in c.columns}, index=c.index)
        if t == "ma_above":
            return c > _cols(c, lambda x: sma(x, n))
        if t == "ma_below":
            return c < _cols(c, lambda x: sma(x, n))
        if t == "trend_up":
            e50, e200 = _cols(c, lambda x: ema(x, 50)), _cols(c, lambda x: ema(x, 200))
            return (c > e50) & (e50 > e200)
        if t in ("ma_cross_up", "ma_fast_above"):
            f, s_ = _cols(c, lambda x: sma(x, int(r["fast"]))), _cols(c, lambda x: sma(x, int(r["slow"])))
            above = (f > s_) & f.notna() & s_.notna()
            return above & ~above.shift(1, fill_value=False) if t == "ma_cross_up" else above
        if t == "breakout":
            return c > c.shift(1).rolling(n, min_periods=n).max()
        if t == "near_high":
            return c >= (1 - r["pct"] / 100) * c.rolling(n, min_periods=n).max()
        if t == "rsi_below":
            return _cols(c, lambda x: rsi(x, n)) < r["level"]
        if t == "rsi_above":
            return _cols(c, lambda x: rsi(x, n)) > r["level"]
        if t == "zscore_below":
            return _cols(c, lambda x: zscore(x, n)) <= -r["z"]
        if t == "zscore_above":
            return _cols(c, lambda x: zscore(x, n)) >= r["z"]
        if t == "drop_day":
            return c.pct_change(fill_method=None) <= -r["pct"] / 100
        if t == "rs_rank":
            ret = c.pct_change(n, fill_method=None)
            cnt = ret.notna().sum(axis=1)
            rank = ret.rank(axis=1, ascending=False, method="first")
            return rank.le(np.ceil(cnt * r["top_pct"] / 100), axis=0) & ret.notna()
        if t == "beats_aspi":
            a = U.aspi.reindex(c.index).pct_change(n, fill_method=None)
            return c.pct_change(n, fill_method=None).gt(a, axis=0)
        if t == "before_xd":
            k = int(r["k"])
            out = np.zeros(c.shape, dtype=bool)
            for j, lst in U.xd.items():
                for xi, ai in lst:
                    i = xi - k
                    if 0 <= i < len(out) and i >= ai:
                        out[i, j] = True
            return pd.DataFrame(out, index=c.index, columns=c.columns)
        raise ValueError(t)


def signals(U: Universe, spec: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """(entry table, exit-rule table), each [day, stock] of True/False, from the stock's past only."""
    ev = _Eval(U)
    E = ev.run(spec["entry"])
    X = ev.run(spec["exit"]["rule"]) if spec["exit"]["rule"] else np.zeros_like(E)
    return E, X


# =================================================================== the decisions and the money
class Runner:
    """All decisions, fills, costs and cash, one session at a time: `open(i)` then `close(i)`.

    open(i): cash interest is credited, then the orders due this morning are filled at the open (costs charged).
    close(i): the account is valued; exits are chosen (stop, target, trailing stop, time, exit rule); entries are
    chosen for the free slots, best-traded stocks first. Orders are filled at the open of the next session on which the
    stock trades. Nothing here reads a price after session i."""

    def __init__(self, U: Universe, E: np.ndarray, X: np.ndarray, spec: dict[str, Any], rate_pct: np.ndarray, i0: int, capital: float) -> None:
        self.U, self.E, self.X, self.spec = U, E, X, spec
        self.rate = rate_pct
        self.i0, self.capital = i0, capital
        n, m = U.t_close.shape
        self.m = m
        self.cash = float(capital)
        self.shares = np.zeros(m)
        self.pos: dict[int, dict[str, Any]] = {}
        self.sched: dict[int, list[dict[str, Any]]] = defaultdict(list)
        self.pend_buy: set[int] = set()
        self.pend_sell: set[int] = set()
        self.reserved = 0.0
        self.equity = np.full(n, np.nan)
        self.equity[i0] = capital
        self.invested = np.full(n, np.nan)
        self.trades: list[dict[str, Any]] = []
        self.brokerage = self.slippage = self.interest = 0.0
        self.min_cash = float(capital)
        self.overdrawn_days = 0
        ex = spec["exit"]
        self.hold, self.stop, self.target, self.trail = ex["hold_days"], ex["stop_pct"], ex["target_pct"], ex["trail_pct"]
        self.b_rate = float(settings()["costs"]["brokerage_pct_per_side"]) / 100
        self.s_rate = spec["slippage_pct"] / 100
        self.cost_rate = self.b_rate + self.s_rate
        self.max_pos = spec["max_positions"]

    # ------------------------------------------------------------- morning
    def open(self, i: int) -> list[dict[str, Any]]:
        """Credit interest, fill what is due at today's open. Returns the cash movements (fees, interest) for the engine."""
        out: list[dict[str, Any]] = []
        if i <= self.i0:
            return out
        gap = self.U.gap[i]
        r = self.rate[i - 1] / 100
        inte = self.cash * r * gap / 365                 # on the cash held since the last close, at the rate known then (Actual/365)
        if inte:
            self.cash += inte
            self.interest += inte
            out.append({"kind": "cash", "amount": inte, "what": "interest", "day": i})
        for t in self.sched.pop(i, []):
            out.append(self._fill(t, i))
        self.min_cash = min(self.min_cash, self.cash)
        self.overdrawn_days += self.cash < -1.0              # a stock opened well above the planned price: a short overdraft
        return out

    def _fill(self, t: dict[str, Any], i: int) -> dict[str, Any]:
        j, q = t["j"], t["qty"]
        px = float(self.U.t_open[i, j])
        notional = abs(q) * px
        b, sl = notional * self.b_rate, notional * self.s_rate
        self.cash += -q * px - b - sl
        self.shares[j] += q
        self.brokerage += b
        self.slippage += sl
        if q > 0:
            self.reserved -= t["reserve"]
            self.pend_buy.discard(j)
            self.pos[j] = {"entry_idx": i, "entry_px": px, "qty": q, "peak": px, "entry_cost": b + sl}
        else:
            p = self.pos.pop(j)
            self.pend_sell.discard(j)
            pnl = p["qty"] * (px - p["entry_px"]) - p["entry_cost"] - b - sl
            self.trades.append({"j": j, "entry_idx": p["entry_idx"], "exit_idx": i, "qty": p["qty"], "entry_px": p["entry_px"], "exit_px": px,
                                "costs": p["entry_cost"] + b + sl, "pnl": pnl, "ret": pnl / (p["qty"] * p["entry_px"] + p["entry_cost"]),
                                "reason": t["reason"], "status": "closed"})
        return {"kind": "cash", "amount": -(b + sl), "what": "fees", "day": i}

    # ------------------------------------------------------------- after the close
    def close(self, i: int) -> list[dict[str, Any]]:
        """Value the account and decide. Returns the orders to place (filled at the open of day `exec`)."""
        px = self.U.t_close[i]
        held = np.flatnonzero(self.shares)
        eq = self.cash + float(np.nansum(self.shares[held] * px[held]))
        self.equity[i] = eq
        self.invested[i] = (eq - self.cash) / eq if eq else 0.0
        orders: list[dict[str, Any]] = []
        for j, p in self.pos.items():
            p["peak"] = max(p["peak"], float(px[j]))
        for j, p in list(self.pos.items()):                                  # exits
            if j in self.pend_sell:
                continue
            reason = self._exit_reason(i, j, p, float(px[j]))
            k = int(self.U.nxt[i, j])
            if reason and k > 0:
                self.sched[k].append({"j": j, "qty": -p["qty"], "reason": reason})
                self.pend_sell.add(j)
                orders.append({"kind": "trade", "j": j, "qty": -p["qty"], "exec": k})
        slots = self.max_pos - len(self.pos) - len(self.pend_buy)            # a position being sold still holds its slot until the fill
        if slots > 0:
            cand = [int(j) for j in np.flatnonzero(self.E[i]) if j not in self.pos and j not in self.pend_buy and np.isfinite(px[j]) and self.U.nxt[i, j] > 0]
            cand.sort(key=lambda j: (-self.U.liq[i, j], j))
            for j in cand:
                if slots <= 0:
                    break
                budget = min(eq / self.max_pos, self.cash - self.reserved) * (1 - BUFFER)
                q = math.floor(budget / (px[j] * (1 + self.cost_rate)))
                if q < 1:
                    continue
                k = int(self.U.nxt[i, j])
                reserve = q * px[j] * (1 + self.cost_rate)
                self.reserved += reserve
                self.sched[k].append({"j": j, "qty": q, "reserve": reserve})
                self.pend_buy.add(j)
                orders.append({"kind": "trade", "j": j, "qty": q, "exec": k})
                slots -= 1
        return orders

    def _exit_reason(self, i: int, j: int, p: dict[str, Any], c: float) -> str | None:
        if self.stop and c <= p["entry_px"] * (1 - self.stop / 100):
            return "stop loss"
        if self.trail and c <= p["peak"] * (1 - self.trail / 100):
            return "trailing stop"
        if self.target and c >= p["entry_px"] * (1 + self.target / 100):
            return "profit target"
        if self.X[i, j]:
            return "exit rule"
        if self.hold and i - p["entry_idx"] >= self.hold:
            return "holding time"
        return None

    def finish(self, i_end: int) -> None:
        """Positions still open at the end are marked at the last close (no exit cost is charged)."""
        px = self.U.t_close[i_end]
        for j, p in self.pos.items():
            gross = p["qty"] * (float(px[j]) - p["entry_px"]) - p["entry_cost"]
            self.trades.append({"j": j, "entry_idx": p["entry_idx"], "exit_idx": None, "qty": p["qty"], "entry_px": p["entry_px"],
                                "exit_px": float(px[j]), "costs": p["entry_cost"], "pnl": gross,
                                "ret": gross / (p["qty"] * p["entry_px"] + p["entry_cost"]), "reason": "still open", "status": "open"})


def rate_array(U: Universe, rates: pd.Series) -> np.ndarray:
    """T-bill rate (% a year) known at each session's close (carried forward from the last published value)."""
    if not len(rates):
        return np.zeros(len(U.days))
    return rates.reindex(rates.index.union(U.days)).sort_index().ffill().reindex(U.days).fillna(0.0).to_numpy(float)


def simulate(U: Universe, E: np.ndarray, X: np.ndarray, spec: dict[str, Any], rate_pct: np.ndarray, i0: int, i_end: int, capital: float) -> Runner:
    """The fast pass: the same Runner, no engine. Starts with `capital` at the close of session i0."""
    r = Runner(U, E, X, spec, rate_pct, i0, capital)
    for i in range(i0 + 1, i_end + 1):
        r.open(i)
        r.close(i)
    r.finish(i_end)
    return r


# =================================================================== gs-quant's engine runs the same orders
def run_engine(U: Universe, E: np.ndarray, X: np.ndarray, spec: dict[str, Any], rate_pct: np.ndarray, i0: int, i_end: int, capital: float) -> dict[str, Any]:
    """Executes, values and books the strategy's orders with gs-quant's PredefinedAssetEngine (offline, our own prices)."""
    from gs_quant.backtests.actions import Action
    from gs_quant.backtests.core import TimeWindow, ValuationFixingType, ValuationMethod
    from gs_quant.backtests.data_sources import DataManager, GenericDataSource, MissingDataStrategy
    from gs_quant.backtests.order import OrderAtMarket, OrderCost
    from gs_quant.backtests.predefined_asset_engine import PredefinedAssetEngine
    from gs_quant.backtests.strategy import Strategy
    from gs_quant.backtests.triggers import OrdersGeneratorTrigger
    from gs_quant.data import DataFrequency
    from gs_quant.target.instrument import EqStock

    days = U.days
    insts = [EqStock(name=sym) for sym in U.symbols]          # the full symbol: two share classes of one company have the same label
    dm = DataManager()
    for j, inst in enumerate(insts):
        first = int(np.argmax(np.isfinite(U.t_close[:, j])))
        pts: dict[dt.datetime, float] = {}
        for i in range(first, len(days)):
            d = days[i].date()
            o = U.t_open[i, j]
            pts[dt.datetime.combine(d, OPEN_T)] = float(o if np.isfinite(o) else U.t_close[i, j])   # not traded: the carried price
            pts[dt.datetime.combine(d, CLOSE_T)] = float(U.t_close[i, j])
        dm.add_data_source(GenericDataSource(pd.Series(pts).sort_index(), MissingDataStrategy.fill_forward), DataFrequency.REAL_TIME, inst, ValuationFixingType.PRICE)
    runner = Runner(U, E, X, spec, rate_pct, i0, capital)
    index = {days[i].date(): i for i in range(i0 + 1, i_end + 1)}

    class LabTrigger(OrdersGeneratorTrigger):
        def __init__(self) -> None:
            super().__init__(trigger_requirements=None, actions=[Action()])

        def get_trigger_times(self):
            return [OPEN_T, CLOSE_T]

        def generate_orders(self, state, backtest=None):
            i = index.get(state.date())
            if i is None:
                return []
            if state.time() == OPEN_T:                       # interest and fees: cash movements, booked in the engine as cost orders
                return [OrderCost("USD", mv["amount"], "lab", state) for mv in runner.open(i)]
            return [OrderAtMarket(instrument=insts[o["j"]], quantity=o["qty"], generation_time=state,
                                  execution_datetime=dt.datetime.combine(days[o["exec"]].date(), OPEN_T), source="lab")
                    for o in runner.close(i) if o["kind"] == "trade"]

    eng = PredefinedAssetEngine(data_mgr=dm, calendars="Weekend", tz=dt.timezone.utc,
                                valuation_method=ValuationMethod(ValuationFixingType.PRICE, TimeWindow(dt.time(14, 29), CLOSE_T)))
    bt = eng.run_backtest(Strategy(None, [LabTrigger()]), start=days[i0].date(), end=days[i_end].date(), initial_value=capital)
    perf = bt.performance.dropna()
    eq = pd.Series(perf.to_numpy(float), index=pd.DatetimeIndex([pd.Timestamp(d) for d in perf.index]))
    ledger = bt.trade_ledger()
    runner.finish(i_end)
    return {"equity": eq, "ledger": ledger, "runner": runner}


# =================================================================== the reshuffle test
def reshuffle(U: Universe, rng: np.random.Generator) -> Universe:
    """The same stocks with the order of their days shuffled: each day's overnight gap and daytime move stay together, so
    volatility, gaps and the average drift are kept, but any pattern in the sequence (trends, dips, breakouts) is destroyed."""
    n, m = U.t_close.shape
    t_o, t_c = U.t_open.copy(), U.t_close.copy()
    sig = np.full((n, m), np.nan)
    for j in range(m):
        valid = np.flatnonzero(np.isfinite(U.t_close[:, j]))
        f = int(valid[0])
        c0 = U.t_close[:, j]
        o = np.where(np.isfinite(U.t_open[:, j]), U.t_open[:, j], np.r_[np.nan, c0[:-1]])
        gap = o[f + 1:] / c0[f:-1] - 1
        intra = c0[f + 1:] / o[f + 1:] - 1
        gap, intra = np.nan_to_num(gap), np.nan_to_num(intra)
        perm = rng.permutation(len(gap))
        g, it = gap[perm], intra[perm]
        close = np.empty(len(g) + 1)
        close[0] = c0[f]
        close[1:] = c0[f] * np.cumprod((1 + g) * (1 + it))
        prev = close[:-1]
        t_c[f:, j] = close
        t_o[f + 1:, j] = np.where(U.tradable[f + 1:, j], prev * (1 + g), np.nan)
        sig[f:, j] = close
    V = Universe(symbols=U.symbols, labels=U.labels, days=U.days, sig=pd.DataFrame(sig, index=U.days, columns=U.symbols), vol=U.vol,
                 tradable=U.tradable, t_open=t_o, t_close=t_c, aspi=U.aspi, xd=U.xd, basis=U.basis)
    return V


def quick_figures(equity: np.ndarray, days: pd.DatetimeIndex, rates: pd.Series, af: float) -> tuple[float, float | None]:
    x = pd.Series(equity, index=days)
    return float(x.iloc[-1] / x.iloc[0] - 1), M.sharpe(x, rates, af)


# =================================================================== a whole run
Progress = Callable[[float, str], None]


def _noop(frac: float, msg: str) -> None:
    pass


def hold_curve(U: Universe, i0: int, i_end: int, capital: float, cost_rate: float) -> tuple[pd.Series, int]:
    """Equal amounts in every stock that exists at the start, bought once at that day's close (fees paid), never sold."""
    ok = np.isfinite(U.t_close[i0])
    n = int(ok.sum())
    if n == 0:
        return pd.Series(dtype=float), 0
    units = np.where(ok, (capital / n) * (1 - cost_rate) / np.where(ok, U.t_close[i0], 1), 0.0)
    val = np.where(ok, U.t_close[i0:i_end + 1], 0.0) @ units
    out = pd.Series(val, index=U.days[i0:i_end + 1])
    out.iloc[0] = capital                                   # the account starts at its full value; the purchase fee shows on day one
    return out, n


def run_lab(s: Session, spec_in: dict[str, Any], progress: Progress = _noop, mc_runs: int | None = None, seed: int = 1) -> dict[str, Any]:
    t0 = time.time()
    spec = normalise(spec_in)
    cfg = (settings().get("quant") or {}).get("lab") or {}
    mc_runs = int(cfg.get("reshuffles", 100)) if mc_runs is None else mc_runs
    progress(0.02, "Loading prices")
    U = load_universe(s, spec["symbols"], spec["basis"])
    rates = R.tbill_series(s)
    af = S.cse_sessions_per_year(s)
    rate_pct = rate_array(U, rates)
    n = len(U.days)
    i_end = n - 1
    if spec["years"] == "all":
        i0 = int(np.argmax(np.isfinite(U.t_close).any(axis=1)))
    else:
        i0 = int(U.days.searchsorted(U.days[-1] - pd.DateOffset(years=spec["years"]))) if spec["years"] else 0
    i0 = max(i0, int(np.argmax(np.isfinite(U.t_close).any(axis=1))))
    if i_end - i0 < 120:
        raise ValueError("Fewer than 120 sessions in the period: too short to test anything.")
    progress(0.08, "Working out the rules")
    E, X = signals(U, spec)
    capital = spec["capital"]
    progress(0.12, "Running the strategy")
    sim = simulate(U, E, X, spec, rate_pct, i0, i_end, capital)
    progress(0.2, "Running it through gs-quant's engine")
    eng = run_engine(U, E, X, spec, rate_pct, i0, i_end, capital)
    sess = U.days[i0:i_end + 1]
    sim_eq = pd.Series(sim.equity[i0:i_end + 1], index=sess)
    eng_eq = eng["equity"].reindex(sess)
    both = pd.concat([sim_eq, eng_eq], axis=1, keys=["sim", "eng"]).dropna()
    diff = float(((both["sim"] - both["eng"]).abs() / both["sim"]).max()) if len(both) else float("nan")
    n_eng, n_bad = ledger_check(U, sim, eng["ledger"])
    parity = {"ok": bool(diff <= PARITY_TOL and n_bad == 0 and n_eng == len(sim.trades)), "max_rel_diff": diff, "sessions_compared": int(len(both)),
              "engine_trades": n_eng, "own_trades": len(sim.trades), "fills_differing": n_bad}
    equity = eng_eq if parity["ok"] and eng_eq.notna().sum() == len(sess) else sim_eq
    progress(0.3, "Buying and holding for comparison")
    hold, n_hold = hold_curve(U, i0, i_end, capital, sim.cost_rate)
    aspi = U.aspi.reindex(sess)
    aspi_idx = aspi / aspi.iloc[0] * capital
    cash = R.cash_index(sess, rates) * capital
    fig = M.single(equity, rates, af)
    rel = M.relative(equity, aspi_idx, rates, af)
    fig_hold, fig_aspi = M.single(hold, rates, af), M.single(aspi_idx, rates, af)
    stats = trade_stats(sim, U, i0, i_end)
    stats.update(brokerage=round(float(sim.brokerage), 2), slippage=round(float(sim.slippage), 2), interest=round(float(sim.interest), 2), min_cash=round(float(sim.min_cash), 2), overdrawn_days=int(sim.overdrawn_days))

    progress(0.38, "Checking the two halves of the period")
    mid = i0 + (i_end - i0) // 2
    halves = []
    if mid - i0 >= HALF_MIN and i_end - mid >= HALF_MIN:
        for name, a, b in (("First half", i0, mid), ("Second half", mid, i_end)):
            r = simulate(U, E, X, spec, rate_pct, a, b, capital)
            ds = U.days[a:b + 1]
            tot, sh = quick_figures(r.equity[a:b + 1], ds, rates, af)
            hh, _ = hold_curve(U, a, b, capital, sim.cost_rate)
            ht, hs = quick_figures(hh.to_numpy(), ds, rates, af) if len(hh) else (None, None)
            halves.append({"name": name, "from": str(ds[0].date()), "to": str(ds[-1].date()), "total_return": tot, "sharpe": sh,
                           "hold_total_return": ht, "hold_sharpe": hs, "trades": sum(1 for t in r.trades if t["status"] == "closed")})

    luck = None
    if mc_runs > 0:
        rng = np.random.default_rng(seed)
        res = []
        t_mc = time.time()
        limit = float(cfg.get("reshuffle_seconds", 120))
        for k in range(mc_runs):
            V = reshuffle(U, rng)
            Ev, Xv = signals(V, spec)
            r = simulate(V, Ev, Xv, spec, rate_pct, i0, i_end, capital)
            res.append(quick_figures(r.equity[i0:i_end + 1], sess, rates, af))
            progress(0.45 + 0.5 * (k + 1) / mc_runs, f"Reshuffling the days: {k + 1} of {mc_runs}")
            if time.time() - t_mc > limit:
                break
        luck = luck_summary(res, fig.get("total_return"), fig.get("sharpe"))
    progress(0.97, "Writing up")

    out = {
        "spec": spec, "meta": {"universe": U.labels, "start": str(sess[0].date()), "end": str(sess[-1].date()), "sessions": len(sess),
                               "sessions_per_year": af, "rate": R.rate_info(s), "hold_stocks": n_hold, "of_stocks": len(U.labels),
                               "brokerage_pct": round(sim.b_rate * 100, 4), "slippage_pct": spec["slippage_pct"],
                               "entry_text": rule_text(spec["entry"]), "exit_text": exit_text(spec["exit"]), "seconds": round(time.time() - t0, 1),
                               "gs_quant": "gs-quant 2.2.5 (Goldman Sachs, Apache-2.0): PredefinedAssetEngine, offline"},
        "parity": parity,
        "charts": {"strategy": pts(equity / capital * 100), "hold": pts(hold / capital * 100), "aspi": pts(aspi_idx / capital * 100),
                   "cash": pts(cash / capital * 100), "drawdown": pts(M.drawdown_series(equity)),
                   "invested": pts(pd.Series(sim.invested[i0:i_end + 1], index=sess))},
        "figures": fig, "hold_figures": fig_hold, "aspi_figures": fig_aspi, "relative": rel, "stats": stats,
        "trades": trade_rows(sim, U), "halves": halves, "luck": luck, "dividends": U.dividends_note,
    }
    out["summary"] = summary(out)
    return out


def ledger_check(U: Universe, r: Runner, ledger: pd.DataFrame) -> tuple[int, int]:
    """Every stock trade in gs-quant's ledger against ours: same stock, same day, same prices. (The ledger also lists the
    cash movements for fees and interest; those are skipped.) Returns (engine trades, trades that differ)."""
    stock = ledger[~ledger["Instrument"].astype(str).str.startswith("Cash")]
    mine = {(U.symbols[t["j"]], U.days[t["entry_idx"]].date()): t for t in r.trades}
    bad = 0
    for ins, o, c, ov, cv, status in zip(stock["Instrument"], stock["Open"], stock["Close"], stock["Open Value"], stock["Close Value"], stock["Status"]):
        sym = str(ins)[len("EqStock("):-1]
        t = mine.get((sym, pd.Timestamp(o).date()))
        if t is None or abs(t["entry_px"] - ov) > 1e-9 * max(1.0, abs(ov)):
            bad += 1
        elif status == "closed" and (t["exit_idx"] is None or U.days[t["exit_idx"]].date() != pd.Timestamp(c).date() or abs(t["exit_px"] - cv) > 1e-9 * max(1.0, abs(cv))):
            bad += 1
    return len(stock), bad


def pts(x: pd.Series, nd: int = 4) -> list[dict[str, Any]]:
    x = x.replace([np.inf, -np.inf], np.nan).dropna()
    return [{"time": str(t.date()), "value": round(float(v), nd)} for t, v in x.items()]


def exit_text(ex: dict[str, Any]) -> str:
    bits = []
    if ex.get("stop_pct"):
        bits.append(f"it falls {_fmtnum(ex['stop_pct'])}% below the price paid (stop loss)")
    if ex.get("trail_pct"):
        bits.append(f"it falls {_fmtnum(ex['trail_pct'])}% below its highest close since buying (trailing stop)")
    if ex.get("target_pct"):
        bits.append(f"it gains {_fmtnum(ex['target_pct'])}% (profit target)")
    if ex.get("rule"):
        bits.append(rule_text(ex["rule"]))
    if ex.get("hold_days"):
        bits.append(f"{ex['hold_days']} sessions have passed")
    return "; or ".join(bits) if bits else "never (positions are kept to the end)"


def trade_stats(r: Runner, U: Universe, i0: int, i_end: int) -> dict[str, Any]:
    closed = [t for t in r.trades if t["status"] == "closed"]
    wins = [t for t in closed if t["pnl"] > 0]
    loss = [t for t in closed if t["pnl"] <= 0]
    gross_w, gross_l = sum(t["pnl"] for t in wins), -sum(t["pnl"] for t in loss)
    eq = r.equity[i0:i_end + 1]
    years = max((U.days[i_end] - U.days[i0]).days / 365.25, 1e-9)
    traded = sum(abs(t["qty"]) * t["entry_px"] for t in r.trades) + sum(abs(t["qty"]) * t["exit_px"] for t in closed)
    return {"trades": len(closed), "open_at_end": len(r.trades) - len(closed), "wins": len(wins),
            "win_rate": len(wins) / len(closed) if closed else None,
            "avg_win": float(np.mean([t["ret"] for t in wins])) if wins else None, "avg_loss": float(np.mean([t["ret"] for t in loss])) if loss else None,
            "profit_factor": gross_w / gross_l if gross_l > 0 else None,
            "avg_sessions": float(np.mean([t["exit_idx"] - t["entry_idx"] for t in closed])) if closed else None,
            "best": max((t["ret"] for t in closed), default=None), "worst": min((t["ret"] for t in closed), default=None),
            "exposure": float(np.nanmean(r.invested[i0 + 1:i_end + 1])) if i_end > i0 else None,
            "turnover_per_year": traded / float(np.mean(eq)) / years if len(eq) else None}


def trade_rows(r: Runner, U: Universe, limit: int = 400) -> dict[str, Any]:
    rows = []
    for t in sorted(r.trades, key=lambda t: t["entry_idx"], reverse=True)[:limit]:
        rows.append({"stock": U.labels[t["j"]], "in": str(U.days[t["entry_idx"]].date()), "out": str(U.days[t["exit_idx"]].date()) if t["exit_idx"] is not None else None,
                     "sessions": (t["exit_idx"] - t["entry_idx"]) if t["exit_idx"] is not None else None, "qty": int(t["qty"]), "ret": round(t["ret"], 6),
                     "pnl": round(t["pnl"], 2), "costs": round(t["costs"], 2), "why": t["reason"]})
    return {"rows": rows, "total": len(r.trades), "shown": len(rows)}


def luck_summary(res: list[tuple[float, float | None]], actual_total: Any, actual_sharpe: Any) -> dict[str, Any]:
    tot = np.array([a for a, _ in res])
    sh = np.array([b for _, b in res if b is not None])
    lo, hi = float(np.min(tot)), float(np.max(tot))
    bins = np.linspace(lo, hi, 21) if hi > lo else np.array([lo - 0.01, lo + 0.01])
    counts, edges = np.histogram(tot, bins=bins)
    out: dict[str, Any] = {"runs": len(res), "total": {"median": float(np.median(tot)), "p5": float(np.percentile(tot, 5)), "p95": float(np.percentile(tot, 95)),
                                                     "beat": float(np.mean(tot < actual_total)) if actual_total is not None else None},
                           "bins": [{"from": float(edges[i]), "to": float(edges[i + 1]), "count": int(counts[i])} for i in range(len(counts))]}
    if len(sh) and actual_sharpe is not None:
        out["sharpe"] = {"median": float(np.median(sh)), "p5": float(np.percentile(sh, 5)), "p95": float(np.percentile(sh, 95)), "beat": float(np.mean(sh < actual_sharpe)), "runs": int(len(sh))}
    return out


def pct(v: Any, nd: int = 1, sign: bool = True) -> str:
    return "–" if v is None else f"{'+' if sign and v > 0 else ''}{v * 100:.{nd}f}%"


def bottom_line(f: dict[str, Any], h: dict[str, Any]) -> str:
    """The first thing a reader should see: did the rule beat simply holding the same stocks?"""
    tr, ht, sh, hs = f.get("total_return"), h.get("total_return"), f.get("sharpe"), h.get("sharpe")
    more = tr is not None and ht is not None and tr > ht
    steadier = sh is not None and hs is not None and sh > hs
    if more and steadier:
        return "Bottom line: it beat buying and holding the same stocks, both in return and in return for the risk."
    if steadier:
        return "Bottom line: it earned less than buying and holding the same stocks, but gave a better return for the risk."
    if more:
        return "Bottom line: it earned more than buying and holding the same stocks, but not more for the risk it took."
    return "Bottom line: it did not beat simply buying and holding the same stocks."


def summary(o: dict[str, Any]) -> list[str]:
    """Plain-English reading of the result, from fixed rules over the numbers. Not advice."""
    f, h, a, st, m = o["figures"], o["hold_figures"], o["aspi_figures"], o["stats"], o["meta"]
    cap = o["spec"]["capital"]
    lines = []
    if st["trades"]:
        lines.append(bottom_line(f, h))
    tr = f.get("total_return")
    lines.append(f"From {m['start']} to {m['end']} LKR {cap:,.0f} would have become LKR {cap * (1 + (tr or 0)):,.0f} ({pct(tr)}), after fees and slippage. "
                 f"Buying the same {m['hold_stocks']} stocks equally and holding gave {pct(h.get('total_return'))}; the ASPI {pct(a.get('total_return'))}.")
    if f.get("max_drawdown") is not None:
        lines.append(f"Its worst fall from a high was {pct(f['max_drawdown'])} (buy-and-hold {pct(h.get('max_drawdown'))}). It was invested {pct(st.get('exposure'), 0, False)} of the time"
                     + (f", so the rest earned the T-bill rate ({m['rate']['rate_pct']:.2f}%)." if st.get("exposure") is not None and st["exposure"] < 0.9 else "."))
    if f.get("sharpe") is not None and h.get("sharpe") is not None:
        better = f["sharpe"] > h["sharpe"]
        lines.append(f"Return for the risk (Sharpe) {f['sharpe']:.2f} against {h['sharpe']:.2f} for buy-and-hold: it {'did better' if better else 'did not do better'} per unit of risk.")
    nt = st["trades"]
    if nt:
        small = " That is too few trades to say much: a handful of lucky or unlucky trades decides the result." if nt < 30 else ""
        lines.append(f"{nt} closed trades; {pct(st['win_rate'], 0, False)} made money (average win {pct(st['avg_win'])}, average loss {pct(st['avg_loss'])}).{small}")
    else:
        lines.append("The rule never bought anything in this period: loosen it or lengthen the period.")
    costs = st["brokerage"] + st["slippage"]
    lines.append(f"Fees and slippage took LKR {costs:,.0f} ({costs / cap * 100:.1f}% of the starting money); interest on idle cash added LKR {st['interest']:,.0f}.")
    hv = o.get("halves") or []
    if len(hv) == 2:
        same = (hv[0]["total_return"] > 0) == (hv[1]["total_return"] > 0)
        lines.append(f"First half {pct(hv[0]['total_return'])}, second half {pct(hv[1]['total_return'])}: "
                     + ("it made (or lost) money in both halves, which is more reassuring than one lucky stretch." if same else "it worked in only one of the halves, so the overall result may come from one good period."))
    lk = o.get("luck")
    if lk and lk.get("sharpe"):
        b = lk["sharpe"]["beat"]
        verdict = ("better than almost all of them: the real price history had patterns this rule used, which is unlikely to be luck alone (not proof, and the stocks were picked with hindsight)" if b >= 0.95
                   else "within the range that pure luck produces: the rule has not shown it adds anything" if b >= 0.5
                   else "worse than the typical shuffled result")
        lines.append(f"Reshuffle test: with each stock's days put in random order (same volatility, gaps and overall rise, but no trends or dips), the same rule beat the "
                     f"reshuffled versions {b * 100:.0f}% of the time on return for risk: {verdict}.")
    if st.get("overdrawn_days"):
        lines.append(f"Cash dipped below zero on {st['overdrawn_days']} days (by up to LKR {-st['min_cash']:,.0f}) because a stock opened well above the price the order was sized on; it is treated as a short overdraft at the T-bill rate.")
    if not o["parity"]["ok"]:
        lines.append(f"Warning: the fast calculation and gs-quant's engine differ by {o['parity']['max_rel_diff']:.2e}; the fast calculation is shown.")
    lines.append("The stocks are ones you track today, chosen with hindsight, so past results look better than they could have in real time. They describe the past, not the future, and never change an engine's verdict. Not financial advice.")
    return lines


# =================================================================== running in the background
@dataclass
class Run:
    id: str
    uid: str
    spec: dict[str, Any]
    status: str = "running"
    progress: float = 0.0
    stage: str = "Starting"
    result: dict[str, Any] | None = None
    error: str | None = None
    started: float = field(default_factory=time.time)
    cached: bool = False


_runs: "OrderedDict[str, Run]" = OrderedDict()
_cache: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
_lock = threading.Lock()
_global_slots = threading.Semaphore(2)


def fingerprint(s: Session, spec: dict[str, Any]) -> str:
    a = S.index_series(s, "^ASPI")
    last = str(a.price.index[-1].date()) if a is not None and len(a.price) else ""
    return hashlib.sha1((json.dumps(spec, sort_keys=True) + last).encode()).hexdigest()


def start(uid: str, spec_in: dict[str, Any]) -> Run:
    """Start a test in the background (one at a time per account). Identical tests on unchanged data return at once."""
    spec = normalise(spec_in)
    with SessionLocal() as s:
        key = fingerprint(s, spec)
    with _lock:
        for r in _runs.values():
            if r.uid == uid and r.status == "running":
                return r
        run = Run(id=uuid.uuid4().hex[:12], uid=uid, spec=spec)
        if key in _cache:
            run.status, run.progress, run.stage, run.result, run.cached = "done", 1.0, "Done (kept from an earlier identical run)", _cache[key], True
            _runs[run.id] = run
            _trim()
            return run
        _runs[run.id] = run
        _trim()
    threading.Thread(target=_work, args=(run, key), daemon=True, name=f"lab-{run.id}").start()
    return run


def _trim() -> None:
    while len(_runs) > 40:
        _runs.popitem(last=False)


def _work(run: Run, key: str) -> None:
    def prog(frac: float, msg: str) -> None:
        run.progress, run.stage = frac, msg
    try:
        with _global_slots, SessionLocal() as s:
            res = run_lab(s, run.spec, prog)
        with _lock:
            _cache[key] = res
            while len(_cache) > 30:
                _cache.popitem(last=False)
        run.result, run.status, run.progress, run.stage = res, "done", 1.0, "Done"
    except (ValueError, LookupError) as exc:
        run.status, run.error, run.stage = "error", str(exc), "Stopped"
    except Exception as exc:  # noqa: BLE001 - the page shows the message; the log has the trace
        import logging
        logging.getLogger("cse_advisor.lab").exception("lab run failed")
        run.status, run.error, run.stage = "error", f"Something went wrong: {exc}", "Stopped"


def get(run_id: str) -> Run | None:
    return _runs.get(run_id)


# =================================================================== what the page offers
def available_stocks(s: Session) -> list[dict[str, Any]]:
    """Stocks with enough stored prices to test, with their names."""
    from sqlalchemy import func
    from ..db import PriceDaily, Security
    counts = dict(s.execute(select(PriceDaily.symbol, func.count()).group_by(PriceDaily.symbol)).all())
    names = {x.symbol: x.name for x in s.scalars(select(Security))}
    return sorted(({"symbol": k, "label": k.split(".")[0], "name": names.get(k), "sessions": v} for k, v in counts.items() if v >= MIN_ROWS), key=lambda x: x["label"])


def catalog(s: Session) -> dict[str, Any]:
    return {"rules": [{"type": k, **{x: v[x] for x in ("label", "help", "params")}} for k, v in RULES.items()], "presets": PRESETS,
            "defaults": {"capital": 1_000_000, "years": 5, "basis": "total", "max_positions": 5, "slippage_pct": default_slippage(),
                         "brokerage_pct": float(settings()["costs"]["brokerage_pct_per_side"])},
            "stocks": available_stocks(s), "max_symbols": MAX_SYMBOLS}
