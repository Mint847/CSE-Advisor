"""Do the detected patterns predict anything? A causal event study over multi-year daily history.

    python -m cse_advisor.pattern_study                 # the cached universe (see backtest.py), every 3rd bar
    python -m cse_advisor.pattern_study --step 1 --workers 4
    python -m cse_advisor.pattern_study --summarize     # re-print the report from the last events file

At every `step`-th bar t the detectors see only the last `window` bars up to t (rolling, so nothing from the
future leaks in). A pattern becomes an *event* the first time it is confirmed, on its confirmation bar `e`:
the breakout / neckline-break bar for chart patterns, the last candle for candlestick patterns, the sweep,
squeeze or awakening bar otherwise. Entry is the next open (e + 1); forward returns are measured to the close
`h` bars later (h = 1, 3, 5, 10, 20) in the pattern's direction, so a bearish pattern earns when price falls.

Baseline: the same stock's directional return from the next open over *all* bars. A pattern is informative only
if its forward return beats that baseline by more than noise. For events that carry a measured-move target and an
invalidation level, the claim is also tested: P(target reached before a close beyond the invalidation, within 30
bars), against the same % distances placed on 30 random days of the same stock.
"""
from __future__ import annotations

import argparse
import logging
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from .backtest import CACHE, OUT, _fee, fetch, universe

HORIZONS = (1, 3, 5, 10, 20)
CLAIM_BARS = 30
WINDOW = 250


def _frame(sym: str) -> pd.DataFrame:
    from .analysis import indicators
    raw = pd.read_csv(CACHE / f"{sym}.csv", parse_dates=["date"]).set_index("date")
    raw = raw[~raw.index.duplicated()].sort_index()
    raw = raw[(raw[["open", "high", "low", "close"]] > 0).all(axis=1)]
    raw["turnover"] = raw["close"] * raw["volume"]
    raw["trades"] = None
    raw["open_estimated"] = False
    raw["source"] = "tv_adj"
    return indicators.add_all(raw)


def claim_outcome(h: np.ndarray, l: np.ndarray, c: np.ndarray, start: int, bull: bool, target: float, inv: float) -> str:
    """'target' if the target is touched before a close beyond the invalidation (same bar: invalidation), else
    'stop' / 'neither', over CLAIM_BARS bars from `start`."""
    for j in range(start, min(len(c), start + CLAIM_BARS)):
        if (bull and c[j] < inv) or ((not bull) and c[j] > inv):
            return "stop"
        if (bull and h[j] >= target) or ((not bull) and l[j] <= target):
            return "target"
    return "neither"


def study(args: tuple[str, int, int]) -> list[dict[str, Any]]:
    sym, step, seed = args
    warnings.filterwarnings("ignore")
    logging.disable(logging.WARNING)
    from .analysis.candlesticks import detect_candlesticks
    from .analysis.chart_patterns import detect_chart_patterns
    from .analysis.liquidity import analyze_liquidity
    from .analysis.pivots import zigzag
    df = _frame(sym)
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    n = len(df)
    ema50 = df["ema50"].to_numpy(float) if "ema50" in df else np.full(n, np.nan)
    atrv = df["atr"].bfill().to_numpy(float)
    rng = np.random.default_rng(seed)
    seen: set[tuple] = set()
    events: list[dict[str, Any]] = []
    for t in range(WINDOW + 10, n - 2, step):
        w = df.iloc[t - WINDOW + 1: t + 1]
        base = t - WINDOW + 1
        piv = zigzag(w, w["atr"], k=1.5)
        pats = detect_chart_patterns(w, piv) + [p for p in detect_candlesticks(w, lookback=step + 2)]
        try:
            pats += [p for p in analyze_liquidity(w, None).get("_patterns", [])]
        except Exception:  # noqa: BLE001
            pass
        for p in pats:
            if p.type in ("doji",):
                continue
            if p.markers:
                mt = p.markers[-1]["time"]
                try:
                    e_local = int(w.index.get_loc(pd.Timestamp(mt)))
                except KeyError:
                    continue
            elif p.type.startswith("volume_"):
                e_local = p.end_index
            else:
                continue
            e = base + e_local
            if e < t - step - 1 or e >= n - 1:
                continue
            key = (p.type, p.start, e)
            if key in seen:
                continue
            seen.add(key)
            sign = 1 if p.direction == "bullish" else -1 if p.direction == "bearish" else 0
            ent = o[e + 1]
            row: dict[str, Any] = {"sym": sym, "type": p.type, "direction": p.direction, "status0": p.status, "e": e,
                                   "date": df.index[e].date().isoformat(), "year": df.index[e].year, "price": c[e],
                                   "atr_pct": atrv[e] / c[e] * 100, "vol_ok": bool(p.context.get("volume_confirmed")),
                                   "with_trend": (1 if c[e] > ema50[e] else -1) == sign if np.isfinite(ema50[e]) and sign else None,
                                   "above": bool(c[e] > ema50[e]) if np.isfinite(ema50[e]) else None,
                                   "tick_pct": (0.1 if c[e] < 100 else 0.25) / c[e] * 100}
            for hh in HORIZONS:
                row[f"r{hh}"] = (c[e + hh] / ent - 1) * 100 if e + hh < n else np.nan
            lv = p.levels
            row["geometry_ok"] = None
            if sign and lv.get("target") and lv.get("invalidation") and lv.get("breakout"):
                tg, inv = float(lv["target"]), float(lv["invalidation"])
                ok = (tg > c[e] > inv) if sign > 0 else (tg < c[e] < inv)
                row["geometry_ok"] = bool(ok)
                row["tgt_pct"], row["inv_pct"] = (tg / c[e] - 1) * 100, (inv / c[e] - 1) * 100
                if ok and e + 1 < n:
                    row["claim"] = claim_outcome(h, l, c, e + 1, sign > 0, tg, inv)
                    picks = rng.integers(WINDOW, n - CLAIM_BARS - 2, size=30)
                    outs = [claim_outcome(h, l, c, int(j) + 1, sign > 0, c[j] * (1 + row["tgt_pct"] / 100), c[j] * (1 + row["inv_pct"] / 100)) for j in picks]
                    row["claim_base_target"] = float(np.mean([x == "target" for x in outs]))
                    row["claim_base_stop"] = float(np.mean([x == "stop" for x in outs]))
            events.append(row)
    # baselines for this stock: directional mean / hit rate over all bars from the next open
    # Two baselines: all bars, and trend-matched (only bars on the same side of the 50-day EMA as the event). The
    # second one separates a pattern's value from plain momentum (stocks above their EMA50 keep drifting up).
    for hh in HORIZONS:
        js = np.arange(WINDOW, n - hh - 1)
        r = (c[js + hh] / o[js + 1] - 1) * 100
        ab = c[js] > ema50[js]
        m_all, h_all = float(r.mean()), float((r > 0).mean() * 100)
        m_ab, m_be = (float(r[ab].mean()) if ab.any() else m_all), (float(r[~ab].mean()) if (~ab).any() else m_all)
        h_ab, h_be = (float((r[ab] > 0).mean() * 100) if ab.any() else h_all), (float((r[~ab] > 0).mean() * 100) if (~ab).any() else h_all)
        for ev in events:
            ev[f"b{hh}_up"], ev[f"b{hh}_up_hit"] = m_all, h_all
            up = ev.get("above")
            ev[f"b{hh}_tm"], ev[f"b{hh}_tm_hit"] = (m_ab, h_ab) if up else (m_be, h_be) if up is not None else (m_all, h_all)
    return events


def report(df: pd.DataFrame, meta: dict[str, Any]) -> str:
    L = [f"# Pattern event study, {meta['first']} to {meta['last']}", "",
         f"{meta['stocks']} stocks, detection every {meta['step']} bar(s) on a rolling {WINDOW}-bar window, {len(df):,} events. "
         "Entry = next open after the confirmation bar; returns in % to the close h bars later, in the pattern's direction "
         "(a bearish pattern earns when price falls). Baseline = the same stocks' return over all bars. "
         "Excess = pattern mean minus baseline mean; t = excess / (std / sqrt(n)), rough because events cluster.", ""]
    d = df[df.direction.isin(["bullish", "bearish"])].copy()
    d["month"] = d.date.astype(str).str[:7]
    d["half"] = np.where(d.year <= 2019, "2013-19", "2020-26")
    rt = 100 * (1 - (1 - _fee()) / (1 + _fee()))

    def clustered_t(e: pd.Series, groups: pd.Series) -> float:
        """t of the mean of e with standard errors clustered by calendar month (events in one month share the
        market's moves, and neighbouring events in one stock have overlapping forward windows)."""
        e = e.dropna()
        if len(e) < 30:
            return float("nan")
        g = groups.loc[e.index]
        m = e.mean()
        se = float(np.sqrt(((e - m).groupby(g).sum() ** 2).sum())) / len(e)
        return float(m / se) if se > 0 else float("nan")

    rows = []
    for (typ, dirn), g in d.groupby(["type", "direction"]):
        if len(g) < 60:
            continue
        sgn = 1 if dirn == "bullish" else -1
        row: dict[str, Any] = {"pattern": f"{typ} ({dirn[:4]})", "n": len(g), "months": g.month.nunique()}
        for hh in (10, 20):
            r = sgn * g[f"r{hh}"]
            ex = r - sgn * g[f"b{hh}_tm"]                # excess over the trend-matched baseline
            ok = r.notna()
            row[f"{hh}d mean"] = r[ok].mean()
            row[f"{hh}d median"] = r[ok].median()
            row[f"{hh}d excess"] = ex[ok].mean()
            row[f"{hh}d t (clustered)"] = clustered_t(ex[ok], g.month)
        ex20 = sgn * g["r20"] - sgn * g["b20_tm"]
        for h_, name in ((g.half == "2013-19", "excess 2013-19"), (g.half == "2020-26", "excess 2020-26")):
            row[f"20d {name}"] = ex20[h_ & ex20.notna()].mean()
        row["20d hit %"] = ((sgn * g["r20"]) > 0)[g.r20.notna()].mean() * 100
        row["trend-matched hit %"] = (g["b20_tm_hit"] if sgn > 0 else 100 - g["b20_tm_hit"]).mean()
        if sgn > 0:
            row["20d net of costs %"] = row["20d mean"] - rt
        rows.append(row)
    t = pd.DataFrame(rows).sort_values("20d t (clustered)", ascending=False).set_index("pattern")

    def md(tbl: pd.DataFrame) -> list[str]:
        cols = list(tbl.columns)
        out = ["| | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
        for i, r in tbl.iterrows():
            cells = []
            for col, x in zip(cols, r.values):
                cells.append("–" if isinstance(x, float) and x != x else f"{x:.0f}" if col in ("n", "months") else f"{x:.2f}" if isinstance(x, float) else str(x))
            out.append(f"| {i} | " + " | ".join(cells) + " |")
        return out
    L += ["## Forward returns by pattern, against a trend-matched baseline, sorted by clustered t at 20 bars", "",
          "Excess = pattern mean minus the mean of random days of the same stock on the same side of its 50-day EMA. "
          "t is clustered by calendar month. About 45 patterns are tested at two horizons, so by chance one or two will show |t| ≈ 2.5; "
          "treat |t| < 3 as noise. 'Net of costs' only for bullish patterns (there is no short selling on the CSE).", "",
          *md(t.round(2)), ""]
    sig = t[(t["20d t (clustered)"].abs() >= 3) | (t["10d t (clustered)"].abs() >= 3)]
    stable = sig[(np.sign(sig["20d excess 2013-19"]) == np.sign(sig["20d excess 2020-26"]))]
    L += ["## Patterns with clustered |t| ≥ 3, and the same sign of excess in both halves of the sample", "", *(md(stable.round(2)) if len(stable) else ["None."]), ""]
    # claims
    cl = d[d["claim"].notna()] if "claim" in d else d.iloc[0:0]
    if len(cl):
        rows = []
        for typ, g in cl.groupby("type"):
            rows.append({"pattern": typ, "n": len(g), "target first %": (g.claim == "target").mean() * 100,
                         "random-day baseline %": g.claim_base_target.mean() * 100, "stopped first %": (g.claim == "stop").mean() * 100,
                         "baseline stop %": g.claim_base_stop.mean() * 100, "neither %": (g.claim == "neither").mean() * 100})
        L += ["## The pattern's own claim: measured-move target before a close beyond the invalidation (30 bars)", "",
              "Baseline = the same % distances on 30 random days of the same stock.", "",
              *md(pd.DataFrame(rows).sort_values("n", ascending=False).set_index("pattern").round(1)), ""]
    bad = d[d.geometry_ok == False]  # noqa: E712
    if len(bad):
        L += ["## Confirmed patterns whose own levels are in the wrong order (target / invalidation on the wrong side of the close)", "",
              *md(bad.groupby("type").size().rename("n").to_frame()), ""]
    # context splits
    ctx = []
    for name, col in (("volume-confirmed", "vol_ok"), ("with the trend (close vs EMA50)", "with_trend")):
        for val in (True, False):
            g = d[d[col] == val]
            if len(g) > 30:
                sg = np.where(g.direction == "bullish", 1, -1)
                r10 = (sg * g["r10"]).dropna()
                b10 = (sg * g["b10_up"]).loc[r10.index]
                ctx.append({"split": f"{name} = {val}", "n": len(g), "10d mean": r10.mean(), "10d excess": r10.mean() - b10.mean(), "10d hit %": (r10 > 0).mean() * 100})
    if ctx:
        L += ["## Does context help? (all patterns pooled, 10 bars)", "", *md(pd.DataFrame(ctx).set_index("split").round(2)), ""]
    # tick size
    d["tick_bucket"] = pd.cut(d.tick_pct, [0, 0.5, 1.0, 100], labels=["tick < 0.5% of price", "0.5–1%", "> 1% of price"])
    tk = []
    for b, g in d.groupby("tick_bucket", observed=True):
        sg = np.where(g.direction == "bullish", 1, -1)
        r10 = (sg * g["r10"]).dropna()
        tk.append({"tick bucket": b, "n": len(g), "10d mean": r10.mean(), "10d excess": r10.mean() - (sg * g["b10_up"]).loc[r10.index].mean()})
    L += ["## Coarse prices (one tick as a share of the price), all patterns pooled", "", *md(pd.DataFrame(tk).set_index("tick bucket").round(2)), ""]
    return "\n".join(L)


def track_record(df: pd.DataFrame) -> dict[str, Any]:
    """Per (pattern type, direction): what followed it, and whether that is distinguishable from random days of the
    same stock on the same side of its 50-day EMA. The label is deliberately conservative:
      edge       clustered |t| >= 3 at 20 sessions, excess in the pattern's direction, the same sign in both halves
                 of the sample (2013-19, 2020-26) and at 10 sessions
      wrong way  the mirror image (significantly the opposite way, in both halves)
      weak       |t| >= 2 with a consistent sign in both halves
      no edge    everything else
    The returns are measured from the next open after confirmation, so they are what you could have captured."""
    d = df[df.direction.isin(["bullish", "bearish"])].copy()
    d["month"] = d.date.astype(str).str[:7]
    out: dict[str, Any] = {}

    def cl_t(e: pd.Series, g: pd.Series) -> float:
        e = e.dropna()
        if len(e) < 30:
            return float("nan")
        m = e.mean()
        se = float(np.sqrt(((e - m).groupby(g.loc[e.index]).sum() ** 2).sum())) / len(e)
        return float(m / se) if se > 0 else float("nan")

    for (typ, dirn), g in d.groupby(["type", "direction"]):
        if len(g) < 60 or g.month.nunique() < 24:
            continue
        sgn = 1 if dirn == "bullish" else -1
        ex = {h: sgn * g[f"r{h}"] - sgn * g[f"b{h}_tm"] for h in (10, 20)}
        t = {h: cl_t(ex[h], g.month) for h in (10, 20)}
        half = {name: ex[20][(g.year <= 2019) if name == "a" else (g.year >= 2020)].mean() for name in ("a", "b")}
        same = bool(np.sign(half["a"]) == np.sign(half["b"]) and np.sign(half["a"]) == np.sign(ex[20].mean()))
        t20, t10 = t[20], t[10]
        if same and t20 >= 3 and t10 > 0:
            label = "edge"
        elif same and t20 <= -3 and t10 < 0:
            label = "wrong way"
        elif same and abs(t20) >= 2 and np.sign(t10) == np.sign(t20):
            label = "weak" if t20 > 0 else "wrong way"
        else:
            label = "no edge"
        base_med = float((g["b20_tm"]).median())
        out[f"{typ}|{dirn}"] = {
            "n": int(len(g)), "months": int(g.month.nunique()), "label": label,
            "median_20d": round(float(g["r20"].median()), 2), "mean_20d": round(float(g["r20"].mean()), 2),
            "baseline_median_20d": round(base_med, 2), "baseline_mean_20d": round(float(g["b20_tm"].mean()), 2),
            "excess_20d": round(float(ex[20].mean()), 2), "t_20d": round(t20, 2), "excess_10d": round(float(ex[10].mean()), 2), "t_10d": round(t10, 2),
            "excess_20d_2013_19": round(float(half["a"]), 2), "excess_20d_2020_26": round(float(half["b"]), 2),
            "hit_20d": round(float(((sgn * g["r20"]) > 0)[g.r20.notna()].mean() * 100), 1),
            "baseline_hit_20d": round(float((g["b20_tm_hit"] if sgn > 0 else 100 - g["b20_tm_hit"]).mean()), 1)}
    return {"span": f"{d.date.min()[:4]}–{d.date.max()[:4]}", "stocks": int(d.sym.nunique()), "generated": date.today().isoformat(), "patterns": out}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--step", type=int, default=3)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--summarize", action="store_true")
    ap.add_argument("--write-track-record", action="store_true", help="write config/pattern_track_record.json (what the app shows in each pattern's explanation)")
    a = ap.parse_args()
    stamp = date.today().isoformat()
    f = OUT / f"pattern_events_{stamp}.csv"
    if a.summarize and f.exists():
        df = pd.read_csv(f)
    else:
        syms = a.symbols or list(dict.fromkeys(universe() + ["LOLC.N0000", "KZOO.N0000", "RIL.N0000", "DOCK.N0000"]))
        t0 = time.time()
        fetch(syms, False)
        syms = [x for x in syms if (CACHE / f"{x}.csv").exists()]
        print(f"{len(syms)} stocks", flush=True)
        with ProcessPoolExecutor(max_workers=a.workers) as ex:
            rows = [x for r in ex.map(study, [(s, a.step, i) for i, s in enumerate(syms)]) for x in r]
        df = pd.DataFrame(rows)
        OUT.mkdir(parents=True, exist_ok=True)
        df.to_csv(f, index=False)
        print(f"{len(df):,} events in {(time.time() - t0) / 60:.0f} min -> {f}", flush=True)
    meta = {"first": df.date.min(), "last": df.date.max(), "stocks": df.sym.nunique(), "step": a.step}
    md = report(df, meta)
    (OUT / f"pattern_events_{stamp}.md").write_text(md, encoding="utf-8")
    if a.write_track_record:
        import json
        from .analysis.track_record import PATH
        tr = track_record(df)
        PATH.write_text(json.dumps(tr, indent=1), encoding="utf-8")
        print(f"track record for {len(tr['patterns'])} pattern types -> {PATH}")
        print("labels:", pd.Series([v["label"] for v in tr["patterns"].values()]).value_counts().to_dict())
    print(md)


if __name__ == "__main__":
    main()
