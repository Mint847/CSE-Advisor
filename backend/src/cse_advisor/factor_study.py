"""What actually adds return after costs? Stock-selection factors, entry styles, exits for 1-3 month holds, regimes.

    python -m cse_advisor.factor_study                 # reads the cached prices from backtest.py, writes a report
    python -m cse_advisor.factor_study --step 5

Research only: nothing here changes a verdict, a score or a BUY rule. Everything is net of the settings.yaml fee
(per side), entered at the next open, with prices adjusted for rights / splits (TradingView's adjusted bars).

Parts
  A  cross-sectional factors: on every 5th session the stocks are ranked by a factor and cut into quintiles; the
     return of the top quintile minus the bottom one (and against the whole universe) over 20 / 40 / 60 / 90
     sessions. t-statistics are Newey-West on monthly means, so overlapping windows do not inflate them.
  B  one-stock conditions (trend, extension above the 20-day average, distance from the 52-week high, volume ...)
     against the same-date average of all stocks, which removes market timing.
  C  what is different about the engines' BUY calls compared with their WATCH calls.
  D  entry styles x exits for 1-3 month holds, on the recorded engine calls and on three engine-free signals, each
     against random trend-matched days with the same exit.
  E  market regime (ASPI vs its averages, breadth) and calendar years.
  F  candlestick patterns pooled, from the saved pattern-event file.

Caveats printed in the report: the 42 stocks are today's most-traded listings (survivorship), the sample is one
market with a few big regime swings, and about 100 tests are run, so only results that hold in both halves of the
history and in most years are worth acting on.
"""
from __future__ import annotations

import argparse
import math
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from .backtest import CACHE, OUT, _fee
from .analysis.selection import features
from .plan_study import enter as plan_enter

WARM = 260
MAXH = 90
HORIZONS = (20, 40, 60, 90)
SPLIT = pd.Timestamp("2020-01-01")
EXITS = ("time40", "time60", "time90", "trail3", "trail4", "ema50")
FACTORS = {                      # name: what the top quintile means
    "ret20": "best last 20 sessions", "ret60": "best last 60", "ret120": "best last 120", "mom12_1": "best 12 months excluding the last 20 sessions",
    "dist52": "closest to the 52-week high", "volr": "volume rising (20 vs 120-day average)", "turn": "most liquid (60-day median turnover)",
    "atrp": "most volatile (ATR % of price)", "ext20": "furthest above the 20-day average", "ext50": "furthest above the 50-day average",
}
CANDLES = {"hammer", "inverted_hammer", "hanging_man", "shooting_star", "bullish_engulfing", "bearish_engulfing", "bullish_harami", "bearish_harami",
           "piercing_line", "dark_cloud_cover", "morning_star", "evening_star", "three_white_soldiers", "three_black_crows", "bullish_marubozu",
           "bearish_marubozu", "dragonfly_doji", "gravestone_doji"}


# ---------------------------------------------------------------------------------------------------- statistics
def nw_t(x: Any, lag: int) -> float:
    """t-statistic of the mean with Newey-West (Bartlett) standard errors."""
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 12:
        return float("nan")
    e = x - x.mean()
    s = float(e @ e) / n
    for k in range(1, min(lag, n - 1) + 1):
        s += 2 * (1 - k / (lag + 1)) * float(e[k:] @ e[:-k]) / n
    return float(x.mean() / math.sqrt(s / n)) if s > 0 else float("nan")


def clustered_t(e: pd.Series, groups: pd.Series) -> float:
    e = e.dropna()
    if len(e) < 30:
        return float("nan")
    m = e.mean()
    se = float(np.sqrt(((e - m).groupby(groups.loc[e.index]).sum() ** 2).sum())) / len(e)
    return float(m / se) if se > 0 else float("nan")


def md(tbl: pd.DataFrame, ints: tuple[str, ...] = ("n", "trades", "calls")) -> list[str]:
    cols = list(tbl.columns)
    out = ["| | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for i, r in tbl.iterrows():
        cells = []
        for col, x in zip(cols, r.values):
            cells.append("–" if isinstance(x, float) and x != x else f"{x:.0f}" if col in ints else f"{x:.2f}" if isinstance(x, (float, np.floating)) else str(x))
        out.append(f"| {i} | " + " | ".join(cells) + " |")
    return out


# ---------------------------------------------------------------------------------------------------- data
class Stock:
    __slots__ = ("sym", "dates", "o", "h", "l", "c", "atr", "ema50", "n", "pos")

    def __init__(self, sym: str, d: pd.DataFrame, atr: pd.Series, ema50: pd.Series):
        self.sym, self.dates = sym, d.index
        self.o, self.h, self.l, self.c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        self.atr, self.ema50 = atr.to_numpy(float), ema50.to_numpy(float)
        self.n = len(d)
        self.pos = {t: i for i, t in enumerate(d.index)}


_features = features


def load(step: int) -> dict[str, Any]:
    fee = _fee()
    aspi = pd.read_csv(CACHE / "ASPI.csv", parse_dates=["date"]).set_index("date")["aspi"]
    aspi = aspi[~aspi.index.duplicated()].sort_index()
    stocks: dict[str, Stock] = {}
    feats: dict[str, pd.DataFrame] = {}
    for f in sorted(CACHE.glob("*.N0000.csv")):
        d = pd.read_csv(f, parse_dates=["date"]).set_index("date")
        d = d[~d.index.duplicated()].sort_index()
        d = d[(d[["open", "high", "low", "close"]] > 0).all(axis=1)]
        if len(d) < WARM + MAXH + 60:
            continue
        F, atr, e50, _ = _features(d, aspi, fee)
        stocks[f.stem] = Stock(f.stem, d, atr, e50)
        feats[f.stem] = F
    cal = sorted(set().union(*[set(s.dates) for s in stocks.values()]))
    sample = set(cal[WARM::step])
    # cross-sectional momentum rank on every date, for the engine-free "top relative strength" signal
    rank120 = pd.concat({k: v["ret120"] for k, v in feats.items()}, axis=1).rank(axis=1, pct=True)
    above = pd.concat({k: v["above50"].astype(float).where(v["ret20"].notna()) for k, v in feats.items()}, axis=1)
    breadth = above.mean(axis=1).where(above.count(axis=1) >= 15)
    reg = pd.DataFrame({"above100": aspi > aspi.rolling(100).mean(), "above200": aspi > aspi.rolling(200).mean()}).reindex(pd.DatetimeIndex(cal), method="ffill")
    reg["breadth"] = breadth.reindex(reg.index)
    longs = []
    for k, F in feats.items():
        g = F.copy()
        g["sym"] = k
        g["i"] = np.arange(len(g))
        g = g[(g["i"] >= WARM) & g.index.isin(sample)]
        longs.append(g)
    long = pd.concat(longs).rename_axis("date").reset_index()
    long = long[long.groupby("date")["sym"].transform("size") >= 15]
    for H in HORIZONS:
        long[f"dm{H}"] = long.groupby("date")[f"f{H}"].transform("mean")
    return {"stocks": stocks, "feats": feats, "long": long, "reg": reg, "rank120": rank120, "fee": fee, "step": step, "aspi": aspi}


# ---------------------------------------------------------------------------------------------------- A, B
def part_a(long: pd.DataFrame) -> tuple[list[str], pd.DataFrame]:
    rows = []
    for name, meaning in FACTORS.items():
        r = long.groupby("date")[name].rank(pct=True)
        q = np.ceil(r * 5).clip(1, 5)
        for H in HORIZONS:
            sub = pd.DataFrame({"date": long["date"], "q": q, "f": long[f"f{H}"]}).dropna()
            by = sub.groupby(["date", "q"])["f"].mean().unstack()
            if 1.0 not in by or 5.0 not in by:
                continue
            allm = sub.groupby("date")["f"].mean()
            spread = (by[5.0] - by[1.0]).dropna()
            top_ex = (by[5.0] - allm).dropna()
            mon = spread.groupby(spread.index.to_period("M")).mean()
            monx = top_ex.groupby(top_ex.index.to_period("M")).mean()
            yr = spread.groupby(spread.index.year).mean()
            rows.append({"factor": name, "top quintile = ": meaning, "H": H, "bottom Q1 net %": by[1.0].mean() * 100, "top Q5 net %": by[5.0].mean() * 100,
                         "universe net %": allm.mean() * 100, "Q5 - Q1 pts": spread.mean() * 100, "t (NW)": nw_t(mon.values, math.ceil(H / 21)),
                         "Q5 - universe pts": top_ex.mean() * 100, "t Q5 vs all": nw_t(monx.values, math.ceil(H / 21)),
                         "Q5-Q1 2013-19": spread[spread.index < SPLIT].mean() * 100, "Q5-Q1 2020-26": spread[spread.index >= SPLIT].mean() * 100,
                         "years Q5>Q1 %": (yr > 0).mean() * 100})
    t = pd.DataFrame(rows)
    L = ["## A. Which stocks to hold: cross-sectional factors", "",
         "Every 5th session the 42 stocks are ranked on the factor and cut into quintiles; returns are from the next open to the close H sessions "
         "later, **after round-trip costs**. 'Q5 - universe' is the top quintile minus the average stock on the same dates, which is what a ranking screen "
         "would have to add to be worth using. t-statistics are Newey-West on monthly means (lag = H / 21 months). About 40 rows are tested, so |t| below 3 "
         "is likely chance; look for the same sign in both halves and in most years.", ""]
    show = t.set_index(["factor"]).drop(columns=["top quintile = "])
    for H in HORIZONS:
        s = show[show["H"] == H].drop(columns=["H"]).round(2)
        L += [f"### {H} sessions", "", *md(s), ""]
    L += ["Factor definitions: " + "; ".join(f"**{k}** top = {v}" for k, v in FACTORS.items()) + ".", ""]
    return L, t


def part_b(long: pd.DataFrame) -> list[str]:
    L = ["## B. One-stock conditions, against the average stock on the same date", "",
         "Each row: all stock-dates (every 5th session) meeting the condition. 'Net %' is the 40-session return after costs; 'vs same-date average' removes "
         "market timing; t is clustered by calendar month.", ""]
    H = 40
    d = long.dropna(subset=[f"f{H}"]).copy()
    d["month"] = d["date"].dt.to_period("M").astype(str)
    d["ex"] = d[f"f{H}"] - d[f"dm{H}"]
    conds = {
        "trend up (close > EMA50 > EMA200)": d["trendup"], "not trend up": ~d["trendup"],
        "close > EMA50": d["above50"], "close < EMA50": ~d["above50"],
        "within 5% of 52-week high": d["dist52"] > -0.05, "more than 25% below 52-week high": d["dist52"] < -0.25,
        "ext20 < 0 (below the 20-day average)": d["ext20"] < 0, "ext20 0 to 3%": d["ext20"].between(0, 0.03), "ext20 3 to 8%": d["ext20"].between(0.03, 0.08),
        "ext20 > 8% (stretched)": d["ext20"] > 0.08,
        "20-day return > +15%": d["ret20"] > 0.15, "20-day return < -10%": d["ret20"] < -0.10,
        "stronger than ASPI over 120 days": d["rs120"] > 0, "weaker than ASPI over 120 days": d["rs120"] < 0,
        "volume 20d > 1.5x the 120d average": d["volr"] > 1.5, "volume 20d < 0.7x": d["volr"] < 0.7,
        "55-day breakout today": d["brk55"], "bounce off the 20-day average in an uptrend": d["pb"],
    }
    rows = []
    for name, m in conds.items():
        g = d[m.fillna(False)]
        if len(g) < 200:
            continue
        rows.append({"condition": name, "n": len(g), "net %": g[f"f{H}"].mean() * 100, "win %": (g[f"f{H}"] > 0).mean() * 100,
                     "vs same-date average (pts)": g["ex"].mean() * 100, "t (month-clustered)": clustered_t(g["ex"], g["month"]),
                     "2013-19": g.loc[g["date"] < SPLIT, "ex"].mean() * 100, "2020-26": g.loc[g["date"] >= SPLIT, "ex"].mean() * 100})
    L += md(pd.DataFrame(rows).set_index("condition").round(2)) + [""]
    return L


# ---------------------------------------------------------------------------------------------------- C
def part_c(data: dict[str, Any]) -> list[str]:
    files = sorted((OUT).glob("calls_*.csv"))
    L = ["## C. What is different about the BUY calls?", ""]
    if not files:
        return L + ["No recorded calls found (run `python -m cse_advisor.backtest --calls`).", ""]
    calls = pd.read_csv(files[-1], parse_dates=["date"])
    feats = data["feats"]
    rows = []
    for c in calls[calls.verdict.isin(["BUY", "WATCH"])].itertuples():
        F = feats.get(c.sym)
        if F is None or c.date not in F.index:
            continue
        r = F.loc[c.date]
        rows.append({"engine": c.engine, "group": "BUY" if c.verdict == "BUY" else "WATCH triggered" if c.triggered else "WATCH not triggered",
                     "ext20": r.ext20, "ret20": r.ret20, "dist52": r.dist52, "volr": r.volr, "trendup": float(r.trendup), "f40": r.f40})
    df = pd.DataFrame(rows)
    # universe average 40-session return on the same dates, for "excess"
    dm = pd.concat({k: v["f40"] for k, v in feats.items()}, axis=1).mean(axis=1)
    L += [f"From {files[-1].name} ({len(calls):,} calls). Forward = 40-session net return from the next open; 'vs universe' = minus the average stock the same day.", ""]
    g = df.groupby(["engine", "group"]).agg(n=("f40", "size"), ext20_med=("ext20", "median"), ret20_med=("ret20", "median"), dist52_med=("dist52", "median"),
                                              volr_med=("volr", "median"), trendup_pct=("trendup", "mean"), f40_mean=("f40", "mean"))
    g["trendup_pct"] *= 100
    for c in ("ext20_med", "ret20_med", "dist52_med", "f40_mean"):
        g[c] *= 100
    L += md(g.round(2)) + [""]
    b = df[df.group == "BUY"].copy()
    if len(b) > 30:
        sp = []
        for name, m in (("BUY, 20-day return > +10%", b.ret20 > 0.10), ("BUY, 20-day return <= +10%", b.ret20 <= 0.10),
                        ("BUY, stretched (ext20 > 5%)", b.ext20 > 0.05), ("BUY, not stretched", b.ext20 <= 0.05),
                        ("BUY, within 10% of the 52-week high", b.dist52 > -0.10), ("BUY, further below the high", b.dist52 <= -0.10)):
            x = b[m]
            if len(x) >= 10:
                sp.append({"BUY subset": name, "n": len(x), "40d net %": x.f40.mean() * 100, "win %": (x.f40 > 0).mean() * 100})
        L += ["### Inside the BUY calls (all three engines pooled)", "", *md(pd.DataFrame(sp).set_index("BUY subset").round(2)), ""]
    return L


# ---------------------------------------------------------------------------------------------------- D
def sim_exit(rule: str, i: int, px: float, A: Stock, fee: float) -> tuple[float, int] | None:
    n = A.n
    last = min(n - 1, i + MAXH - 1)
    if i + MAXH - 1 > n - 1:
        return None                         # the trade is not fully observed
    k_ = 1 - fee
    k0 = 1 / (px * (1 + fee))
    if rule.startswith("time"):
        j = min(last, i + int(rule[4:]) - 1)
        return A.c[j] * k_ * k0 - 1, j - i + 1
    a0 = A.atr[i - 1]
    if not np.isfinite(a0):
        return None
    if rule.startswith("trail"):
        k = float(rule[5:])
        stop, hh = px - k * a0, px
        for j in range(i, last + 1):
            if j > i and A.o[j] <= stop:
                return A.o[j] * k_ * k0 - 1, j - i + 1
            if A.l[j] <= stop:
                return stop * k_ * k0 - 1, j - i + 1
            hh = max(hh, A.h[j])
            stop = max(stop, hh - k * A.atr[j])
        return A.c[last] * k_ * k0 - 1, last - i + 1
    if rule == "ema50":
        stop = px - 3 * a0
        for j in range(i, last + 1):
            if j > i and A.o[j] <= stop:
                return A.o[j] * k_ * k0 - 1, j - i + 1
            if A.l[j] <= stop:
                return stop * k_ * k0 - 1, j - i + 1
            if j > i and A.c[j] < A.ema50[j]:
                return A.c[j] * k_ * k0 - 1, j - i + 1
        return A.c[last] * k_ * k0 - 1, last - i + 1
    raise ValueError(rule)


def bounce_entry(call: dict[str, Any], A: Stock) -> tuple[int, float] | None:
    """Wait for price to touch the buy zone, then buy the open after the first up-close above the prior day's high."""
    t, touched = int(call["t"]), False
    for j in range(t + 1, min(A.n - 1, t + 6)):
        if A.l[j] <= call["zh"]:
            touched = True
        if touched and A.c[j] > A.h[j - 1] and A.c[j] > A.o[j]:
            px = float(A.o[j + 1])
            return (j + 1, px) if call["stop"] < px < call["t1"] else None
    return None


def part_d_e(data: dict[str, Any]) -> list[str]:
    stocks: dict[str, Stock] = data["stocks"]
    feats, fee, reg, rank120, step = data["feats"], data["fee"], data["reg"], data["rank120"], data["step"]
    trades: list[dict[str, Any]] = []

    def add(signal: str, entry: str, sym: str, i: int, px: float, sig_i: int) -> None:
        A = stocks[sym]
        sd = A.dates[sig_i]
        for ex in EXITS:
            r = sim_exit(ex, i, px, A, fee)
            if r:
                trades.append({"signal": signal, "entry": entry, "exit": ex, "sym": sym, "date": sd, "net": r[0] * 100, "bars": r[1]})

    # controls: every 5th session, trend up, next open
    for sym, A in stocks.items():
        F = feats[sym]
        tu = F["trendup"].to_numpy(bool)
        for i in range(WARM, A.n - MAXH - 2, step):
            if tu[i]:
                add("control: random trend-up days", "next_open", sym, i + 1, float(A.o[i + 1]), i)
            add("control: random days (any trend)", "next_open", sym, i + 1, float(A.o[i + 1]), i)
    # engine-free signals
    for name, col in (("55-day breakout above the 200-day average", "brk55"), ("bounce off the 20-day average, uptrend", "pb"),
                      ("55-day breakout and top 40% relative strength", None)):
        for sym, A in stocks.items():
            F = feats[sym]
            s = F["brk55"].to_numpy(bool) if col is None else F[col].to_numpy(bool)
            if col is None:
                rk = rank120[sym].reindex(F.index).to_numpy(float)
                s = s & (rk >= 0.6)
            last = -99
            for i in np.flatnonzero(s):
                if i < WARM or i > A.n - MAXH - 3 or i - last < 10:
                    continue
                last = i
                add("signal: " + name, "next_open", sym, i + 1, float(A.o[i + 1]), i)
    # recorded engine calls
    files = sorted(OUT.glob("calls_*.csv"))
    if files:
        calls = pd.read_csv(files[-1], parse_dates=["date"])
        calls = calls[(calls.verdict == "BUY") | ((calls.verdict == "WATCH") & (calls.triggered == True))]  # noqa: E712
        for c in calls.to_dict("records"):
            A = stocks.get(c["sym"])
            if A is None or c["date"] not in A.pos or c["t1"] != c["t1"]:
                continue
            t = A.pos[c["date"]]
            c = {**c, "t": t}
            # plan_enter reads o, h, l, c arrays and the call's t / ideal / zh / stop / trigger
            for kind in ("limit", "next_open"):
                got = plan_enter(kind, c, A.o, A.h, A.l, A.c)
                if got:
                    add(f"{c['engine']} {c['verdict']}{'' if c['verdict'] == 'BUY' else ' (triggered)'}", kind, c["sym"], got[0], got[1], t)
            b = bounce_entry(c, A)
            if b:
                add(f"{c['engine']} {c['verdict']}{'' if c['verdict'] == 'BUY' else ' (triggered)'}", "bounce", c["sym"], b[0], b[1], t)
    T = pd.DataFrame(trades)
    T["ym"] = T["date"].dt.to_period("M")
    T["year"] = T["date"].dt.year
    R = reg.reindex(pd.DatetimeIndex(T["date"]), method="ffill")
    T["above200"], T["breadth"] = R["above200"].to_numpy(), R["breadth"].to_numpy()

    ctl = T[T.signal == "control: random trend-up days"]
    ctl_m = {ex: ctl[ctl.exit == ex].groupby("ym")["net"].mean() for ex in EXITS}
    L = ["## D. Entries and exits for 1-3 month holds", "",
         f"Costs {fee * 100:.2f}% per side already deducted. Exits: **time40/60/90** = sell at the close of that session; **trail3 / trail4** = ATR chandelier "
         "stop 3 / 4 x ATR(14) below the highest high since entry (gap-down exits at the open), max 90 sessions; **ema50** = sell at the first close below the "
         "50-day average, with a 3 x ATR hard stop. Entries: **next_open** = the open after the signal; **limit** = the engine's resting limit at its ideal price "
         "(5 sessions, as in the plan study); **bounce** = wait for price to touch the buy zone, then buy the open after the first up-close above the previous "
         "day's high. 'vs control' = mean monthly difference from random trend-up days with the same exit (t is Newey-West, lag 3).", ""]

    def summary(g: pd.DataFrame, ex: str) -> dict[str, Any]:
        gm = g.groupby("ym")["net"].mean()
        cm = ctl_m[ex]
        common = gm.index.intersection(cm.index)
        d = gm[common] - cm[common]
        w, lo = g.net[g.net > 0], g.net[g.net <= 0]
        return {"trades": len(g), "mean net %": g.net.mean(), "median %": g.net.median(), "win %": (g.net > 0).mean() * 100,
                "avg days": g.bars.mean(), "avg win / avg loss": (w.mean() / abs(lo.mean())) if len(w) and len(lo) else float("nan"),
                "vs control (pts)": d.mean() if len(d) else float("nan"), "t": nw_t(d.values, 3) if len(d) else float("nan"),
                "2013-19": g.loc[g.date < SPLIT, "net"].mean(), "2020-26": g.loc[g.date >= SPLIT, "net"].mean()}

    sig_names = sorted(T.signal.unique(), key=lambda s: (not s.startswith("control"), not s.startswith("signal"), s))
    for sname in sig_names:
        sub = T[T.signal == sname]
        rows = {}
        for (en, ex), g in sub.groupby(["entry", "exit"]):
            if len(g) >= 20:
                rows[f"{en} · {ex}"] = summary(g, ex)
        if rows:
            L += [f"### {sname}", "", *md(pd.DataFrame(rows).T.round(2)), ""]

    # E. regimes and years: controls and the engine-free signals, one exit each
    L += ["## E. Does the market regime or the year change the picture?", "",
          "Entries bucketed by what the ASPI and breadth (share of the 42 stocks above their 50-day average) said on the signal date. Mean net % per trade, "
          "exit = trail3 (an ATR trailing stop). 'n' is the number of trades.", ""]
    ex = "trail3"
    base = T[T.exit == ex].copy()
    base["ASPI vs 200-day"] = np.where(base.above200.astype(float) == 1, "above", np.where(base.above200.astype(float) == 0, "below", "n/a"))
    base["breadth"] = pd.cut(base["breadth"], [-0.01, 0.4, 0.6, 1.01], labels=["breadth < 40%", "40-60%", "> 60%"])
    reg_rows = []
    for sname in [s for s in sig_names if s.startswith("control: random trend") or s.startswith("signal")]:
        s = base[base.signal == sname]
        for col in ("ASPI vs 200-day", "breadth"):
            for val, g in s.groupby(col, observed=True):
                if len(g) >= 30:
                    reg_rows.append({"signal": sname[:46], "regime": f"{col}: {val}", "n": len(g), "mean net %": g.net.mean(), "win %": (g.net > 0).mean() * 100})
    L += [*md(pd.DataFrame(reg_rows).set_index("signal").round(2)), ""]
    yr = base[base.signal.str.startswith("control: random trend") | base.signal.str.startswith("signal")]
    ytab = yr.pivot_table(index="year", columns="signal", values="net", aggfunc="mean").round(2)
    ytab.columns = [c[:30] for c in ytab.columns]
    L += ["### By year (trail3, mean net % per trade)", "", *md(ytab), ""]
    return L


# ---------------------------------------------------------------------------------------------------- G
def part_g(data: dict[str, Any]) -> list[str]:
    """Candidate stock-selection rules built from what B / D suggested, each against all trend-up days, same exit."""
    stocks, reg, fee = data["stocks"], data["reg"], data["fee"]
    long = data["long"].copy()
    long["rs_rank"] = long.groupby("date")["ret120"].rank(pct=True)
    long["breadth"] = reg["breadth"].reindex(pd.DatetimeIndex(long["date"]), method="ffill").to_numpy()
    base = long[long["trendup"].astype(bool)].copy()
    exits = ("time40", "time60", "time90", "trail4")
    rec = []
    for r in base.itertuples():
        A = stocks[r.sym]
        i = int(r.i) + 1
        if i >= A.n:
            continue
        for ex in exits:
            s = sim_exit(ex, i, float(A.o[i]), A, fee)
            if s:
                rec.append((r.Index, ex, s[0] * 100))
    sim = pd.DataFrame(rec, columns=["row", "exit", "net"]).merge(base[["dist52", "rs_rank", "breadth", "date"]], left_on="row", right_index=True)
    sim["ym"] = sim["date"].dt.to_period("M")
    sim["year"] = sim["date"].dt.year
    near = sim["dist52"] > -0.10
    strong = sim["rs_rank"] >= 0.6
    wide = sim["breadth"] > 0.6
    cands = {"all trend-up days (the control)": pd.Series(True, index=sim.index),
             "near 52-week high (within 10%)": near, "top 40% relative strength (120d)": strong,
             "near high and top 40% RS": near & strong, "breadth > 60% only": wide, "breadth < 40% only": sim["breadth"] < 0.4,
             "near high and breadth > 60%": near & wide, "near high, top 40% RS and breadth > 60%": near & strong & wide}
    L = ["## G. Candidate stock-selection rules, each against all trend-up days with the same exit", "",
         "Trend up = close > EMA50 > EMA200. Every 5th session, entry at the next open, costs deducted. 'vs control' is the mean monthly difference from the "
         "control row of the same exit; t is Newey-West; 'years better' is the share of calendar years in which the rule beat the control. Rules were chosen "
         "after looking at parts B and D, so treat even good numbers here as a hypothesis to confirm on new data (the journal), not as proof.", ""]
    rows = {}
    for ex in exits:
        e = sim[sim.exit == ex]
        ctl_m = e.groupby("ym")["net"].mean()
        ctl_y = e.groupby("year")["net"].mean()
        for name, m in cands.items():
            g = e[m.loc[e.index]]
            if len(g) < 60:
                continue
            gm = g.groupby("ym")["net"].mean()
            common = gm.index.intersection(ctl_m.index)
            d = gm[common] - ctl_m[common]
            gy = g.groupby("year")["net"].mean()
            dy = (gy - ctl_y.reindex(gy.index)).dropna()
            dd = g.assign(dm=g["ym"].map(ctl_m))
            rows[f"{ex} · {name}"] = {"trades": len(g), "mean net %": g.net.mean(), "median %": g.net.median(), "win %": (g.net > 0).mean() * 100,
                                      "vs control (pts)": d.mean(), "t": nw_t(d.values, 3), "years better %": (dy > 0).mean() * 100,
                                      "2013-19 vs ctl": (dd[dd.date < SPLIT].net - dd[dd.date < SPLIT].dm).mean(),
                                      "2020-26 vs ctl": (dd[dd.date >= SPLIT].net - dd[dd.date >= SPLIT].dm).mean()}
    L += md(pd.DataFrame(rows).T.round(2)) + [""]
    return L


# ---------------------------------------------------------------------------------------------------- F
def part_f() -> list[str]:
    files = sorted(OUT.glob("pattern_events_*.csv"))
    L = ["## F. Candlestick patterns, pooled", ""]
    if not files:
        return L + ["No saved pattern events.", ""]
    ev = pd.read_csv(files[-1])
    ev = ev[ev["type"].isin(CANDLES) & ev["direction"].isin(["bullish", "bearish"])].copy()
    ev["month"] = ev["date"].astype(str).str[:7]
    rt = 100 * (1 - (1 - _fee()) / (1 + _fee()))
    rows = []
    for dirn, g in ev.groupby("direction"):
        sg = 1 if dirn == "bullish" else -1
        row = {"group": f"all candlesticks ({dirn})", "n": len(g)}
        for hh in (10, 20):
            ex = sg * g[f"r{hh}"] - sg * g[f"b{hh}_tm"]
            row[f"{hh}d excess"], row[f"{hh}d t"] = ex.mean(), clustered_t(ex, g["month"])
        row["20d mean"] = (sg * g["r20"]).mean()
        row["20d net of costs"] = row["20d mean"] - rt if sg > 0 else float("nan")
        rows.append(row)
    L += ["Directional return after the pattern minus a trend-matched baseline (random days of the same stock on the same side of its 50-day average); "
          "all 18 candlestick types pooled, from " + files[-1].name + ". t is clustered by month.", "", *md(pd.DataFrame(rows).set_index("group").round(2)), ""]
    return L


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=5)
    a = ap.parse_args()
    data = load(a.step)
    long = data["long"]
    first, last = long["date"].min().date(), long["date"].max().date()
    L = [f"# Factor, entry and exit study, {first} to {last}", "",
         f"{len(data['stocks'])} stocks (today's most-traded listings, so survivorship flatters the results), adjusted prices from TradingView, every {a.step}th "
         f"session sampled, costs {data['fee'] * 100:.2f}% per side. Research only: no rule in the app uses these results yet.", ""]
    la, table = part_a(long)
    L += la + part_b(long) + part_c(data) + part_d_e(data) + part_g(data) + part_f()
    out = OUT / f"factor_study_{date.today().isoformat()}.md"
    out.write_text("\n".join(L), encoding="utf-8")
    table.to_csv(OUT / f"factor_quintiles_{date.today().isoformat()}.csv", index=False)
    print("wrote", out)


if __name__ == "__main__":
    main()
