"""Is the entry-and-exit plan sound? Entry and exit variants on exactly the same recorded signals, plus a control.

    python -m cse_advisor.backtest --calls --step 5     # records every BUY / WATCH call with its plan
    python -m cse_advisor.plan_study                    # compares entries and exits on the latest recording

Entries compared (all use the signal bar t, known at its close):
  limit        the plan as it stands: a limit buy at `ideal` for 5 sessions (a trigger must close first), filled when
               the low reaches it
  next_open    buy at the next open (after a trigger close, if the plan has a trigger)
Exits compared: stop and T1 (the plan), stop and T2, no stop (T1 or time), a stop twice as far. Time exit 30 sessions.
Control: the same stop / T1 distances (in %) applied at the next open of 20 random days of the same stock. If the
recorded signals do not beat that, the signal is adding nothing over random timing.
Costs: brokerage + slippage per side from settings (1.32% by default). Same-bar stop and target: stop first.
"""
from __future__ import annotations

import argparse
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from .backtest import CACHE, OUT, _fee

HOLD = 30
FILL_DAYS = 5
CONTROL_DRAWS = 20


def _prices(sym: str) -> tuple[np.ndarray, ...]:
    d = pd.read_csv(CACHE / f"{sym}.csv", parse_dates=["date"]).set_index("date")
    d = d[~d.index.duplicated()].sort_index()
    return tuple(d[k].to_numpy(float) for k in ("open", "high", "low", "close"))


def enter(kind: str, call: dict[str, Any], o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray) -> tuple[int, float] | None:
    t, n = int(call["t"]), len(c)
    trig = call["trigger"] if call["trigger"] == call["trigger"] else None      # NaN -> None
    if kind == "limit":
        armed = trig is None
        for j in range(t + 1, min(n, t + 1 + FILL_DAYS)):
            if not armed:
                armed = c[j] > trig
                continue
            if l[j] <= call["ideal"]:
                return j, float(call["ideal"])
        return None
    if kind == "next_open":
        # only while price is still inside (or below) the plan's buy zone: above it the plan's targets and stop no
        # longer fit, and a market buy above T1 would book an instant 'target' at a loss
        def ok(px: float) -> bool:
            return 0 < px <= call["zh"] and px > call["stop"]
        if trig is None:
            return (t + 1, float(o[t + 1])) if t + 1 < n and ok(o[t + 1]) else None
        for j in range(t + 1, min(n - 1, t + 1 + FILL_DAYS)):
            if c[j] > trig:
                return (j + 1, float(o[j + 1])) if ok(o[j + 1]) else None
        return None
    raise ValueError(kind)


def exit_trade(i: int, px: float, stop: float | None, target: float | None, h: np.ndarray, l: np.ndarray, c: np.ndarray, fee: float) -> tuple[str, float, int]:
    n = len(c)
    last = min(n - 1, i + HOLD - 1)
    for k in range(i, last + 1):
        if stop is not None and l[k] <= stop:
            return "stop", stop * (1 - fee) / (px * (1 + fee)) - 1, k
        if target is not None and h[k] >= target:
            return "target", target * (1 - fee) / (px * (1 + fee)) - 1, k
    res = "open" if i + HOLD - 1 >= n else "timeout"
    return res, c[last] * (1 - fee) / (px * (1 + fee)) - 1, last


def exits(call: dict[str, Any], px: float) -> dict[str, tuple[float | None, float | None]]:
    stop, t1, t2 = call["stop"], call["t1"], call["t2"] if call["t2"] == call["t2"] else call["t1"]
    return {"stop+T1 (plan)": (stop, t1), "stop+T2": (stop, t2), "no stop, T1": (None, t1), "stop 2x as far, T1": (px - 2 * (px - stop), t1)}


def _agg(rows: list[tuple[str, float]], filled: int, total: int) -> dict[str, float]:
    if not rows:
        return {"calls": total, "filled %": 0.0}
    net = np.array([r[1] for r in rows]) * 100
    res = [r[0] for r in rows]
    rt = 100 * (1 - (1 - _fee()) / (1 + _fee()))       # round-trip cost as % of the entry
    return {"calls": total, "filled %": 100 * filled / total, "trades": len(rows), "target first %": 100 * res.count("target") / len(rows),
            "stop first %": 100 * res.count("stop") / len(rows), "win %": 100 * float((net > 0).mean()), "mean net %": float(net.mean()),
            "mean before costs %": float(net.mean()) + rt, "median net %": float(np.median(net))}


def run(calls: pd.DataFrame) -> str:
    fee = _fee()
    rng = np.random.default_rng(7)
    cache: dict[str, tuple[np.ndarray, ...]] = {}
    out_rows, ctl_rows, adverse = [], [], []
    for call in calls.to_dict("records"):
        if call["t1"] != call["t1"]:
            continue
        o, h, l, c = cache.setdefault(call["sym"], _prices(call["sym"]))
        n = len(c)
        mkt = enter("next_open", call, o, h, l, c)
        lim = enter("limit", call, o, h, l, c)
        for ekind, got in (("limit", lim), ("next_open", mkt)):
            for xname, (stop, tgt) in (exits(call, got[1]) if got else exits(call, call["ideal"])).items():
                res = exit_trade(got[0], got[1], stop, tgt, h, l, c, fee) if got else None
                out_rows.append({**{k: call[k] for k in ("sym", "t", "engine", "verdict", "triggered", "actionable", "setup")}, "entry": ekind, "exit": xname,
                                 "filled": got is not None, "res": res[0] if res else None, "net": res[1] if res else np.nan})
        # adverse selection: the next-open outcome of calls whose limit order did / did not fill
        if mkt:
            r = exit_trade(mkt[0], mkt[1], call["stop"], call["t1"], h, l, c, fee)
            adverse.append({"engine": call["engine"], "limit_filled": lim is not None, "net": r[1] * 100, "res": r[0]})
        # control: same % stop / T1 distance on random days, market entry at the next open
        if mkt:
            sp, tp = call["stop"] / call["ideal"] - 1, call["t1"] / call["ideal"] - 1
            for j in rng.integers(260, n - HOLD - 3, size=CONTROL_DRAWS):
                if abs(int(j) - int(call["t"])) < 10:
                    continue
                px = float(o[j + 1])
                if px <= 0:      # a bad (zero) price in the history
                    continue
                r = exit_trade(j + 1, px, px * (1 + sp), px * (1 + tp), h, l, c, fee)
                ctl_rows.append({"engine": call["engine"], "verdict": call["verdict"], "triggered": call["triggered"], "res": r[0], "net": r[1] * 100})
    df = pd.DataFrame(out_rows)
    L = [f"# Entry and exit variants on the recorded calls", "",
         f"{len(calls):,} calls ({calls.sym.nunique()} stocks, {calls.date.min()} to {calls.date.max()}), costs {fee * 100:.2f}% per side, 30-session time exit. "
         "Same-bar stop and target: stop first.", ""]

    def md(rows: list[dict[str, Any]], key: str) -> list[str]:
        t = pd.DataFrame(rows).set_index(key)
        cols = list(t.columns)
        o_ = ["| | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
        for i, r in t.iterrows():
            o_.append(f"| {i} | " + " | ".join("–" if isinstance(x, float) and x != x else f"{x:.0f}" if col in ("calls", "trades", "n") else f"{x:.1f}" for col, x in zip(cols, r.values)) + " |")
        return o_

    for title, mask in (("All calls", df.verdict.notna()), ("BUY calls only", df.verdict == "BUY"), ("Plans marked triggered", df.triggered == True),  # noqa: E712
                        ("Plans marked actionable (reward:risk ≥ 2 after costs)", df.actionable == True)):  # noqa: E712
        sub = df[mask]
        rows = []
        for (eng, ent, ex), g in sub.groupby(["engine", "entry", "exit"]):
            if ex not in ("stop+T1 (plan)", "no stop, T1") and ent == "limit":
                continue
            rows.append({"variant": f"{eng} · {ent} · {ex}", **_agg([(r, v) for r, v in zip(g[g.filled].res, g[g.filled].net)], int(g.filled.sum()), len(g))})
        L += [f"## {title}", "", *md(rows, "variant"), ""]
    geo = calls[calls.t1.notna()].copy()
    geo["stop_pct"] = (geo.ideal - geo.stop) / geo.ideal * 100
    geo["t1_pct"] = (geo.t1 - geo.ideal) / geo.ideal * 100
    geo["stop_atr"] = (geo.ideal - geo.stop) / geo.atr
    rows = []
    for eng, g in geo.groupby("engine"):
        rows.append({"engine": eng, "calls": len(g), "median stop %": g.stop_pct.median(), "median T1 %": g.t1_pct.median(),
                     "median stop in ATR": g.stop_atr.median(), "gross T1:stop": g.t1_pct.median() / g.stop_pct.median(),
                     "random-walk P(T1 first)": 100 * g.stop_pct.median() / (g.stop_pct.median() + g.t1_pct.median())})
    L += ["## Plan geometry (before costs)", "",
          "On a random walk the chance of reaching T1 before the stop is about stop / (stop + T1). Round-trip costs are "
          f"{100 * (1 - (1 - fee) / (1 + fee)):.2f}% of the entry on every trade, win or lose.", "", *md(rows, "engine"), ""]
    # control
    ctl = pd.DataFrame(ctl_rows)
    plan_mkt = df[(df.entry == "next_open") & (df.exit == "stop+T1 (plan)") & df.filled]
    rows = []
    for eng in ("original", "analyst", "smc"):
        a, b = plan_mkt[plan_mkt.engine == eng], ctl[ctl.engine == eng]
        if len(a):
            rows.append({"engine": eng, "signals: trades": len(a), "signals: mean net %": a.net.mean() * 100, "signals: target first %": 100 * (a.res == "target").mean(),
                         "random days: trades": len(b), "random: mean net %": b.net.mean(), "random: target first %": 100 * (b.res == "target").mean(),
                         "signal minus random (pts)": a.net.mean() * 100 - b.net.mean()})
    L += ["## Control: do the signals beat random timing with the same stop and target distances? (next-open entry inside the buy zone, plan exit)", "",
          *md(rows, "engine"), ""]
    adv = pd.DataFrame(adverse)
    rows = []
    for (eng, f), g in adv.groupby(["engine", "limit_filled"]):
        rows.append({"engine / limit order": f"{eng} · limit {'filled' if f else 'NOT filled'}", "n": len(g), "next-open mean net %": g.net.mean(),
                     "target first %": 100 * (g.res == "target").mean(), "stop first %": 100 * (g.res == "stop").mean()})
    L += ["## Adverse selection: the same calls bought at the next open (only inside the buy zone), split by whether the plan's limit order would have filled", "",
          "If the calls where the limit did not fill did much better than the ones where it did, the limit-order entry is skipping the winners.", "",
          *md(rows, "engine / limit order"), ""]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--file")
    a = ap.parse_args()
    files = sorted(OUT.glob("calls_*.csv"))
    f = a.file or (files[-1] if files else None)
    if not f:
        raise SystemExit("No recording: run  python -m cse_advisor.backtest --calls  first")
    calls = pd.read_csv(f)
    md = run(calls)
    (OUT / f"plan_study_{date.today().isoformat()}.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
