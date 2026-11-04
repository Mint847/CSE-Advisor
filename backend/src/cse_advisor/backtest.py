"""Walk-forward test of the three engines on multi-year daily history.

    python -m cse_advisor.backtest                  # the 40-stock universe, 2012 onwards
    python -m cse_advisor.backtest --step 10 --workers 4

At every `step`-th session t the full pipeline sees only the last `window` bars up to t. Each engine's
BUY or WATCH plan becomes a limit order at its 'ideal' price, valid for 5 sessions (a trigger must close
first). After a fill: stop vs T1, whichever is hit first, else exit at the close 30 sessions later.
Costs: brokerage + slippage per side from settings (1.32% by default). The same stock + engine is not
re-entered within 10 sessions. History comes from TradingView's daily bars (already adjusted for rights,
bonus and splits; real opens), cached under data/backtests/cache. Fundamentals and news are not used
(their history isn't available), so the Classic engine's score is technical only, as in the earlier study.
Results are split by year and by market regime (ASPI above / below its 200-day average).
"""
from __future__ import annotations

import argparse
import logging
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

from .config import DATA_DIR, settings

OUT = DATA_DIR / "backtests"
CACHE = OUT / "cache"
# the 40 most-traded stocks used by the 2026-11-02 one-year study, so the results compare directly
UNIVERSE = [f"{t}.N0000" for t in (
    "AAIC ACL ACME AEL ASCO ASPH BFL BIL CBNK CIC CITH CLND COCR COMB CWL DFCC DIAL DIPD DOCK ELPL HAYC HNB HNBF JKH "
    "LLUB MULL NTB OSEA PABC PAP PKME PLC PLR SAMP SEYB SINS SUN TAP WATA WIND").split()]


def universe() -> list[str]:
    f = OUT / "universe.txt"
    if f.exists():
        return [x.strip() for x in f.read_text().splitlines() if x.strip()]
    return UNIVERSE


def fetch(symbols: list[str], refresh: bool = False) -> dict[str, int]:
    from .tv_client import fetch_bars
    CACHE.mkdir(parents=True, exist_ok=True)
    got = {}
    for sym in symbols:
        f = CACHE / f"{sym}.csv"
        if f.exists() and not refresh:
            got[sym] = -1
            continue
        bars = fetch_bars(sym, "1D", 5000)
        if bars:
            df = pd.DataFrame(bars)
            df["date"] = pd.to_datetime(df["ts"]).dt.normalize()
            df[["date", "open", "high", "low", "close", "volume"]].to_csv(f, index=False)
        got[sym] = len(bars)
    if refresh or not (CACHE / "ASPI.csv").exists():
        bars = fetch_bars("ASI", "1D", 5000)
        if bars:
            a = pd.DataFrame(bars)
            a["date"] = pd.to_datetime(a["ts"]).dt.normalize()
            a[["date", "close"]].rename(columns={"close": "aspi"}).to_csv(CACHE / "ASPI.csv", index=False)
    return got


def _fee() -> float:
    c = settings()["costs"]
    return (c["brokerage_pct_per_side"] + c["slippage_pct"]) / 100


def simulate(plan: dict[str, Any], t: int, n: int, h: Any, l: Any, c: Any, fee: float) -> tuple[str, float, int] | None:
    ideal, stop, trig = plan["ideal"], plan["stop"], plan.get("trigger")
    t1 = plan["targets"][0]["price"] if plan["targets"] else None
    if t1 is None:
        return None
    armed, fill = trig is None, None
    for j in range(t + 1, min(n, t + 6)):
        if not armed:
            armed = c[j] > trig
            continue
        if l[j] <= ideal:
            fill = j
            break
    if fill is None:
        return None
    res, px = "timeout", None
    for k in range(fill, min(n, fill + 30)):
        if l[k] <= stop:
            res, px = "stop", stop
            break
        if h[k] >= t1:
            res, px = "target", t1
            break
    if px is None:
        k = min(n - 1, fill + 29)
        px = c[k]
        if fill + 29 >= n:
            res = "open"
    return res, px * (1 - fee) / (ideal * (1 + fee)) - 1, fill


def study(args: tuple[str, int, int, int]) -> list[dict[str, Any]]:
    sym, step, window, start = args
    logging.disable(logging.WARNING)
    from .engine import analyze_frame
    raw = pd.read_csv(CACHE / f"{sym}.csv", parse_dates=["date"]).set_index("date")
    raw = raw[~raw.index.duplicated()].sort_index()
    raw["turnover"] = raw["close"] * raw["volume"]
    raw["trades"] = None
    raw["open_estimated"] = False
    raw["source"] = "tv_adj"
    aspi = pd.read_csv(CACHE / "ASPI.csv", parse_dates=["date"]).set_index("date")["aspi"] if (CACHE / "ASPI.csv").exists() else None
    regime = None
    if aspi is not None:
        a = aspi.reindex(raw.index).ffill()
        regime = (a > a.rolling(200, min_periods=150).mean()).map({True: "ASPI up-trend", False: "ASPI down-trend"})
    h, l, c = raw["high"].to_numpy(float), raw["low"].to_numpy(float), raw["close"].to_numpy(float)
    n = len(raw)
    fee = _fee()
    sec = SimpleNamespace(symbol=sym, name=sym, info={}, shares_issued=None)
    out, last = [], {}
    for t in range(max(start, 120), n - 6, step):
        frame = raw.iloc[max(0, t + 1 - window): t + 1]
        try:
            r = analyze_frame(None, sec, frame, [], fund_rows=[], anns=[], md=pd.DataFrame(), position=None)
        except Exception:  # noqa: BLE001 - a failing bar shouldn't stop the study
            continue
        rec = r["recommendation"]
        d = raw.index[t]
        reg = regime.iloc[t] if regime is not None and pd.notna(regime.iloc[t]) else "unknown"
        out.append({"sym": sym, "date": d.date().isoformat(), "year": d.year, "regime": reg, "kind": "verdicts",
                    "orig": rec["original"]["verdict"], "anly": rec["analyst"]["verdict"], "smc": rec["smc"]["verdict"],
                    "agree": rec["agreement"]["state"]})
        for eng in ("original", "analyst", "smc"):
            e = rec[eng]
            plan = e["plan"]
            if not plan or e["verdict"] not in ("BUY", "WATCH") or t - last.get(eng, -99) < 10:
                continue
            sim = simulate(plan, t, n, h, l, c, fee)
            if not sim:
                continue
            last[eng] = t
            out.append({"sym": sym, "date": d.date().isoformat(), "year": d.year, "regime": reg, "kind": "trade", "engine": eng,
                        "verdict": e["verdict"], "setup": plan["setup"], "actionable": plan["actionable"], "rr": plan.get("reward_risk"),
                        "res": sim[0], "net": sim[1]})
    return out


def study_calls(args: tuple[str, int, int, int]) -> list[dict[str, Any]]:
    """Every BUY / WATCH call with its full plan (no fill or exit simulated), so entry and exit variants can be
    compared offline on exactly the same signals. Same stock + engine at least 10 bars apart."""
    sym, step, window, start = args
    logging.disable(logging.WARNING)
    from .engine import analyze_frame
    raw = pd.read_csv(CACHE / f"{sym}.csv", parse_dates=["date"]).set_index("date")
    raw = raw[~raw.index.duplicated()].sort_index()
    raw["turnover"] = raw["close"] * raw["volume"]
    raw["trades"] = None
    raw["open_estimated"] = False
    raw["source"] = "tv_adj"
    n = len(raw)
    sec = SimpleNamespace(symbol=sym, name=sym, info={}, shares_issued=None)
    out, last = [], {}
    for t in range(max(start, 120), n - 6, step):
        frame = raw.iloc[max(0, t + 1 - window): t + 1]
        try:
            r = analyze_frame(None, sec, frame, [], fund_rows=[], anns=[], md=pd.DataFrame(), position=None)
        except Exception:  # noqa: BLE001
            continue
        rec = r["recommendation"]
        for eng in ("original", "analyst", "smc"):
            e = rec[eng]
            plan = e["plan"]
            if not plan or e["verdict"] not in ("BUY", "WATCH") or t - last.get(eng, -99) < 10:
                continue
            last[eng] = t
            tg = plan["targets"]
            out.append({"sym": sym, "t": t, "date": raw.index[t].date().isoformat(), "engine": eng, "verdict": e["verdict"],
                        "setup": plan["setup"], "triggered": plan["triggered"], "actionable": plan["actionable"], "rr": plan.get("reward_risk"),
                        "zl": plan["buy_zone"]["low"], "zh": plan["buy_zone"]["high"], "ideal": plan["ideal"], "trigger": plan.get("trigger"),
                        "stop": plan["stop"], "t1": tg[0]["price"] if tg else None, "t2": tg[1]["price"] if len(tg) > 1 else None,
                        "atr": plan.get("atr"), "close_t": float(raw["close"].iloc[t])})
    return out


def calls_main(a: argparse.Namespace) -> None:
    syms = a.symbols or universe()
    t0 = time.time()
    fetch(syms, False)
    syms = [x for x in syms if (CACHE / f"{x}.csv").exists()]
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        rows = [x for r in ex.map(study_calls, [(s, a.step, a.window, a.start) for s in syms]) for x in r]
    df = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / f"calls_{date.today().isoformat()}.csv"
    df.to_csv(f, index=False)
    print(f"{len(df):,} calls from {len(syms)} stocks in {(time.time() - t0) / 60:.0f} min -> {f}")


def _agg(g: pd.DataFrame) -> pd.Series:
    return pd.Series({"trades": len(g), "target first %": (g.res == "target").mean() * 100, "stop first %": (g.res == "stop").mean() * 100,
                      "avg net %": g.net.mean() * 100, "median net %": g.net.median() * 100, "win %": (g.net > 0).mean() * 100})


def report(df: pd.DataFrame, meta: dict[str, Any]) -> str:
    v = df[df.kind == "verdicts"]
    tr = df[(df.kind == "trade") & (df.res != "open")]
    lines = [f"# Walk-forward test, three engines, {meta['first']} to {meta['last']}", "",
             f"{meta['stocks']} stocks, a call every {meta['step']} sessions on the last {meta['window']} bars, "
             f"{len(v):,} engine calls, {len(tr):,} simulated trades. Costs {meta['fee'] * 100:.2f}% per side. "
             f"Run {meta['run']} in {meta['seconds'] / 60:.0f} min.", "",
             "Trade rule: limit at the plan's ideal price for 5 sessions (trigger must close first); stop vs T1 first, else exit after 30 sessions.", ""]

    def table(t: pd.DataFrame) -> list[str]:
        t = t.round(1)
        cols = list(t.columns)
        idx = [str(x) if not isinstance(x, tuple) else " · ".join(map(str, x)) for x in t.index]
        out = ["| | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
        for i, (_, row) in zip(idx, t.iterrows()):
            out.append(f"| {i} | " + " | ".join("–" if isinstance(x, float) and x != x else f"{x:g}" if isinstance(x, (int, float)) else str(x)
                                               for x in row.values) + " |")
        return out

    lines += ["## Verdicts", ""]
    vc = pd.DataFrame({e: v[k].value_counts() for e, k in (("Classic", "orig"), ("Levels", "anly"), ("SMC", "smc"))}).fillna(0).astype(int)
    lines += table(vc) + ["", f"Agreement: " + ", ".join(f"{k} {x * 100:.1f}%" for k, x in v.agree.value_counts(normalize=True).items()), ""]
    if tr.empty:
        return "\n".join(lines + ["No trades were simulated."])
    lines += ["## Trades by engine and verdict", ""] + table(tr.groupby(["engine", "verdict"]).apply(_agg, include_groups=False)) + [""]
    lines += ["## By market regime", ""] + table(tr.groupby(["engine", "regime"]).apply(_agg, include_groups=False)) + [""]
    yr = tr.groupby(["year", "engine"]).apply(_agg, include_groups=False)[["trades", "target first %", "avg net %"]].unstack("engine")
    yr.columns = [f"{e} {m}" for m, e in yr.columns]
    lines += ["## By year (target-first % and average net %)", ""] + table(yr) + [""]
    lines += ["## Actionable plans only (reward:risk ≥ 2 after costs)", ""] + table(tr[tr.actionable].groupby("engine").apply(_agg, include_groups=False)) + [""]
    lines += ["## By setup (top 12 by trades)", ""]
    st = tr.groupby(["engine", "setup"]).apply(_agg, include_groups=False).sort_values("trades", ascending=False).head(12)
    lines += table(st) + [""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--window", type=int, default=750)
    ap.add_argument("--start", type=int, default=250, help="first bar index to evaluate (warm-up)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--refresh", action="store_true", help="re-download the price history")
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--calls", action="store_true", help="only record every call with its plan (for entry/exit variant tests)")
    a = ap.parse_args()
    if a.calls:
        return calls_main(a)
    syms = a.symbols or universe()
    t0 = time.time()
    got = fetch(syms, a.refresh)
    syms = [x for x in syms if (CACHE / f"{x}.csv").exists()]
    print(f"history for {len(syms)} stocks ({sum(1 for v in got.values() if v > 0)} downloaded)", flush=True)
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        rows = [x for r in ex.map(study, [(s, a.step, a.window, a.start) for s in syms]) for x in r]
    df = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    df.to_csv(OUT / f"walkforward_{stamp}.csv", index=False)
    v = df[df.kind == "verdicts"]
    meta = {"first": v.date.min() if len(v) else "?", "last": v.date.max() if len(v) else "?", "stocks": len(syms), "step": a.step,
            "window": a.window, "fee": _fee(), "run": stamp, "seconds": time.time() - t0}
    md = report(df, meta)
    (OUT / f"walkforward_{stamp}.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
