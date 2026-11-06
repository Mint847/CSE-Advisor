"""Do world markets move the CSE? Brent, Nasdaq-100, S&P 500, USD/LKR, gold and the Nifty against the ASPI, 2013 onward.

    python -m cse_advisor.macro_study

  A  correlation with the ASPI: same week / next week / same month / next month, for the whole period and each half
     (2013-19, 2020-26), with the verdict "held" only when both halves agree in sign with |t| > 2
  B  events: the CSE session after a US fall of 2%+ (US day D -> first CSE session after D); the CSE week after a
     Brent week of +/-8%
  C  per stock: weekly correlation with Brent and with USD/LKR for the cached stocks (is any CSE stock an oil play?)
  D  the selection rule (uptrend, near the 52-week high, strong 120-day return) by world state: does it do better after
     a strong US month, or when the rupee is steady? (40 / 60 sessions, excess over the same-day universe, after costs)

Writes backend/data/backtests/macro_study_<date>.md and macro_links.json (the MKT page's "link to ASPI" column and
the overnight note read it). Nothing here changes a score.
"""
from __future__ import annotations

import json
import math
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from .backtest import CACHE, OUT
from .factor_study import SPLIT, clustered_t, md

START = pd.Timestamp("2013-01-01")
SERIES = ("brent", "ndx", "spx", "usdlkr", "gold", "nifty")


def r_t(r: float, n: int) -> float:
    if n < 4 or not np.isfinite(r) or abs(r) >= 1:
        return float("nan")
    return float(r * math.sqrt((n - 2) / (1 - r * r)))


def corr(x: pd.Series, y: pd.Series) -> tuple[float, int]:
    d = pd.concat([x, y], axis=1).dropna()
    return (float(d.iloc[:, 0].corr(d.iloc[:, 1])) if len(d) >= 8 else float("nan")), len(d)


def link(x: pd.Series, y: pd.Series, split: pd.Timestamp = SPLIT) -> dict[str, Any]:
    """Correlation of x with y over all, before and after `split`, with t-stats."""
    out = {}
    for tag, sel in (("all", slice(None)), ("h1", x.index < split), ("h2", x.index >= split)):
        xs = x[sel] if tag != "all" else x
        r, n = corr(xs, y)
        out[tag] = {"r": round(r, 3) if r == r else None, "n": n, "t": round(r_t(r, n), 2) if r == r else None}
    return out


def verdict(lk: dict[str, Any]) -> str:
    """held: both halves the same sign with |t| > 2. one_half: only one half significant. weak: significant overall only.
    none: nothing significant."""
    t1, t2, ta = (lk[k]["t"] for k in ("h1", "h2", "all"))
    r1, r2 = lk["h1"]["r"], lk["h2"]["r"]
    if t1 is not None and t2 is not None and abs(t1) > 2 and abs(t2) > 2 and (r1 > 0) == (r2 > 0):
        return "held"
    if (t1 is not None and abs(t1) > 2) or (t2 is not None and abs(t2) > 2):
        return "one_half"
    if ta is not None and abs(ta) > 2:
        return "weak"
    return "none"


def returns(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Log returns over calendar weeks (Fri) or months, from closes forward-filled across holidays."""
    r = np.log(df.ffill().resample(rule).last()).diff()
    return r[r.index >= START]


def next_session(cse: pd.Series, dates: pd.DatetimeIndex) -> pd.Series:
    """For each world-session date D, the CSE daily return of the first session after D (NaN if none yet)."""
    idx = cse.index
    pos = idx.searchsorted(dates, side="right")
    vals = [cse.iloc[p] if p < len(idx) else np.nan for p in pos]
    return pd.Series(vals, index=dates)


# ---------------------------------------------------------------------------------------------------- parts
def part_a(df: pd.DataFrame) -> tuple[list[str], dict[str, Any]]:
    w, m = returns(df, "W-FRI"), returns(df, "ME")
    rows, links = [], {}
    for k in [c for c in SERIES if c in df]:
        tests = {"same week": link(w[k], w["aspi"]), "next week": link(w[k], w["aspi"].shift(-1)),
                 "same month": link(m[k], m["aspi"]), "next month": link(m[k], m["aspi"].shift(-1))}
        vs = {name: verdict(t) for name, t in tests.items()}
        order = {"held": 3, "one_half": 2, "weak": 1, "none": 0}

        def pick(names: tuple[str, str]) -> tuple[str, str]:
            best = max(names, key=lambda n: (order[vs[n]], abs(tests[n]["all"]["r"] or 0)))
            b, v = tests[best], vs[best]
            half = "2020-26" if b["h2"]["t"] is not None and abs(b["h2"]["t"]) > 2 else "2013-19"
            return v, {"held": f"yes, both halves ({best}, r {b['all']['r']:+.2f})", "one_half": f"only {half} ({best}, r {b['all']['r']:+.2f})",
                       "weak": f"weak ({best}, r {b['all']['r']:+.2f})", "none": f"no (largest r {b['all']['r']:+.2f})"}[v]
        v_tog, t_tog = pick(("same week", "same month"))        # moves at the same time as the ASPI
        v_ahead, t_ahead = pick(("next week", "next month"))     # this period's move vs the ASPI's next period
        text = f"moves with the ASPI: {t_tog}; ahead of it: {t_ahead}"
        links[k] = {"verdict": v_ahead, "verdict_together": v_tog, "verdict_text": text, "together": t_tog, "ahead": t_ahead, "tests": tests}
        for name, t in tests.items():
            rows.append({"series": k, "test": name, "r": t["all"]["r"], "t": t["all"]["t"], "n": t["all"]["n"],
                         "r 2013-19": t["h1"]["r"], "r 2020-26": t["h2"]["r"], "verdict": vs[name]})
    out = ["## A. Correlation with the ASPI", "", "Weekly and monthly log returns. 'next' = this period's world move against the ASPI's "
           "following period. Verdict 'held' needs both halves to agree with |t| > 2.", ""]
    out += md(pd.DataFrame(rows).set_index(["series", "test"]))
    out += ["", "**Summary:** " + "; ".join(f"{k}: {v['verdict_text']}" for k, v in links.items()), ""]
    return out, links


def part_b(daily: pd.DataFrame) -> tuple[list[str], dict[str, Any]]:
    cse = np.log(daily["aspi"].dropna()).diff().dropna()
    cse = cse[cse.index >= START]
    base_mean, base_down = float(cse.mean() * 100), float((cse < 0).mean() * 100)
    events: dict[str, Any] = {}
    rows = []
    for code in ("ndx", "spx"):
        if code not in daily:
            continue
        us = np.log(daily[code].dropna()).diff().dropna()
        us = us[us.index >= START]
        for lim in (-0.02, -0.03):
            hit = us[us <= lim]
            nx = next_session(cse, hit.index).dropna()
            if len(nx):
                h1, h2 = nx[nx.index < SPLIT], nx[nx.index >= SPLIT]
                rows.append({"event": f"{code} down {abs(lim) * 100:.0f}%+", "n": len(nx), "next CSE session mean %": round(nx.mean() * 100, 2),
                             "next session down %": round((nx < 0).mean() * 100, 0), "t": round(nx.mean() / (nx.std(ddof=1) / math.sqrt(len(nx))), 2) if len(nx) > 2 else None,
                             "mean 2013-19 %": round(h1.mean() * 100, 2) if len(h1) else None, "n 2013-19": len(h1),
                             "mean 2020-26 %": round(h2.mean() * 100, 2) if len(h2) else None, "n 2020-26": len(h2)})
    us_any = pd.concat([np.log(daily[c].dropna()).diff() for c in ("ndx", "spx") if c in daily], axis=1).min(axis=1)
    us_any = us_any[us_any.index >= START]
    nx = next_session(cse, us_any[us_any <= -0.02].index).dropna()
    if len(nx):
        h1, h2 = nx[nx.index < SPLIT], nx[nx.index >= SPLIT]
        events["us_down_2"] = {"n": int(len(nx)), "next_mean": round(float(nx.mean() * 100), 2), "next_down_pct": round(float((nx < 0).mean() * 100), 0),
                               "base_mean": round(base_mean, 3), "base_down_pct": round(base_down, 0), "period": f"{START.year}-{cse.index[-1].year}",
                               "mean_2013_19": round(float(h1.mean() * 100), 2) if len(h1) else None, "n_2013_19": int(len(h1)),
                               "mean_2020_26": round(float(h2.mean() * 100), 2) if len(h2) else None, "n_2020_26": int(len(h2)),
                               "next5_mean": None}
        # does it last? the CSE's five sessions after the fall (a one-day dip that recovers is noise for 1-3 month holds)
        lv = np.log(daily["aspi"].dropna())
        lv = lv[lv.index >= START]
        pos = lv.index.searchsorted(nx.index, side="left")      # the reacting session's position
        five = [lv.iloc[min(p + 4, len(lv) - 1)] - lv.iloc[p - 1] for p in pos if 0 < p < len(lv) - 4]
        if five:
            events["us_down_2"]["next5_mean"] = round(float(np.mean(five) * 100), 2)
            base5 = float((lv.shift(-4) - lv.shift(1)).dropna().mean() * 100)
            events["us_down_2"]["base5_mean"] = round(base5, 2)
    if "brent" in daily:
        w = returns(daily[["aspi", "brent"]], "W-FRI")
        for sign, lab in ((1, "up"), (-1, "down")):
            sel = w["brent"] * sign >= 0.08
            same, nxt = w["aspi"][sel].dropna(), w["aspi"].shift(-1)[sel].dropna()
            rows.append({"event": f"Brent week {lab} 8%+", "n": len(nxt), "next CSE session mean %": None, "next session down %": None, "t": None,
                         "same-week ASPI %": round(same.mean() * 100, 2) if len(same) else None, "next-week ASPI %": round(nxt.mean() * 100, 2) if len(nxt) else None})
            events[f"brent_week_{lab}_8"] = {"n": int(len(nxt)), "same_week": round(float(same.mean() * 100), 2) if len(same) else None,
                                             "next_week": round(float(nxt.mean() * 100), 2) if len(nxt) else None}
        events["avg_week"] = round(float(w["aspi"].mean() * 100), 2)
    out = ["## B. Events", "", f"US day D is followed by the first CSE session after D. All CSE sessions: mean {base_mean:+.3f}%, down {base_down:.0f}% of days.", ""]
    out += md(pd.DataFrame(rows).set_index("event")) if rows else ["(no data)"]
    return out + [""], events


def _cached_stocks() -> dict[str, pd.Series]:
    out = {}
    for f in sorted(CACHE.glob("*.N0000.csv")):
        d = pd.read_csv(f, parse_dates=["date"]).set_index("date")
        d = d[~d.index.duplicated()].sort_index()
        if len(d) > 500:
            out[f.stem.split(".")[0]] = d["close"]
    return out


def part_c(daily: pd.DataFrame, stocks: dict[str, pd.Series]) -> tuple[list[str], list[dict[str, Any]]]:
    rows = []
    for sym, c in stocks.items():
        df = pd.concat({"s": c, **{k: daily[k] for k in ("brent", "usdlkr", "aspi") if k in daily}}, axis=1, sort=True)
        w = returns(df, "W-FRI")
        rb, nb = corr(w["s"], w["brent"]) if "brent" in w else (float("nan"), 0)
        rf, _ = corr(w["s"], w["usdlkr"]) if "usdlkr" in w else (float("nan"), 0)
        rows.append({"stock": sym, "r brent": round(rb, 3), "t brent": round(r_t(rb, nb), 2), "r usdlkr": round(rf, 3), "weeks": nb})
    t = pd.DataFrame(rows)
    if t.empty:
        return ["## C. Per stock", "", "(no cached stocks)", ""], []
    t = t.reindex(t["r brent"].abs().sort_values(ascending=False).index)
    out = ["## C. Per stock: weekly correlation with Brent and USD/LKR", "",
           f"{len(t)} cached stocks. Largest |r| with Brent: {t.iloc[0]['stock']} {t.iloc[0]['r brent']:+.2f}. "
           f"Stocks with |t| > 2 against Brent: {int((t['t brent'].abs() > 2).sum())} (about {len(t) * 0.05:.0f} expected by chance).", ""]
    out += md(t.head(15).set_index("stock"), ints=("weeks",))
    return out + [""], t.to_dict("records")


def part_d(daily: pd.DataFrame) -> list[str]:
    """The selection rule by world state, on the factor study's panel."""
    from .analysis.selection import NEAR_HIGH, RS_TOP
    from .factor_study import load
    try:
        L = load(5)
    except Exception as exc:  # noqa: BLE001
        return ["## D. Selection rule by world state", "", f"(skipped: {exc})", ""]
    long = L["long"].copy()
    rank = L["rank120"].stack().rename("rs").reset_index().rename(columns={"level_0": "date", "level_1": "sym"})
    long = long.merge(rank, on=["date", "sym"], how="left")
    long = long[long["trendup"].astype(bool) & (long["dist52"] > NEAR_HIGH) & (long["rs"] >= RS_TOP)]
    states = pd.DataFrame(index=pd.DatetimeIndex(sorted(long["date"].unique())))
    for code in ("ndx", "usdlkr"):
        if code in daily:
            x = daily[code].dropna()
            m1 = (x / x.shift(21) - 1).reindex(states.index, method="ffill")
            states[code] = m1
    rows = []
    for H in (40, 60):
        long[f"ex{H}"] = long[f"f{H}"] - long[f"dm{H}"]
        for code, labels in (("ndx", ("weak US month (bottom third)", "middle", "strong US month (top third)")),
                             ("usdlkr", ("rupee firmer (bottom third)", "middle", "rupee weaker (top third)"))):
            if code not in states:
                continue
            q = pd.qcut(states[code], 3, labels=labels)
            g = long.assign(state=long["date"].map(q)).dropna(subset=["state", f"ex{H}"])
            for lab in labels:
                e = g[g["state"] == lab]
                if len(e) < 20:
                    continue
                rows.append({"state": lab, "H": H, "n": len(e), "excess pts": round(e[f"ex{H}"].mean() * 100, 2),
                             "t (month-clustered)": round(clustered_t(e[f"ex{H}"].reset_index(drop=True), e["date"].dt.to_period("M").reset_index(drop=True)), 2)})
    out = ["## D. The selection rule by world state", "", "Excess return of rule-qualifying stocks over the same-day universe, after costs. "
           "World state = the series' change over the prior 21 sessions, in thirds.", ""]
    out += md(pd.DataFrame(rows).set_index(["state", "H"])) if rows else ["(no data)"]
    return out + [""]


# ---------------------------------------------------------------------------------------------------- run
def load_daily() -> pd.DataFrame:
    from .db import SessionLocal, init_db
    from .engine import load_market
    from . import macro
    init_db()
    with SessionLocal.begin() as s:
        if not s.query(macro.MacroDaily).count():
            macro.top_up(s)
    with SessionLocal() as s:
        md_ = load_market(s)
        cols = {"aspi": md_["aspi"].dropna()} if not md_.empty else {}
        for code in SERIES:
            x = macro.series(s, code)
            if not x.empty:
                cols[code] = x
    if "aspi" not in cols and (CACHE / "ASPI.csv").exists():
        cols["aspi"] = pd.read_csv(CACHE / "ASPI.csv", parse_dates=["date"]).set_index("date")["aspi"]
    return pd.concat(cols, axis=1, sort=True)


def main() -> None:
    daily = load_daily()
    a, links = part_a(daily)
    b, events = part_b(daily)
    c, per_stock = part_c(daily, _cached_stocks())
    d = part_d(daily)
    end = daily["aspi"].dropna().index[-1].date()
    head = [f"# Macro study {date.today()}", "", f"ASPI and world series, {START.date()} to {end}. Weekly and monthly log returns; "
            "halves split at 2020-01-01. Nothing here changes a score.", ""]
    OUT.mkdir(parents=True, exist_ok=True)
    rep = OUT / f"macro_study_{date.today()}.md"
    rep.write_text("\n".join(head + a + b + c + d), encoding="utf-8")
    from .macro import LINKS_FILE
    LINKS_FILE.write_text(json.dumps({"date": date.today().isoformat(), "period": f"{START.year}-{end.year}", "series": links, "events": events,
                                      "per_stock_top": per_stock[:10]}, indent=1, default=str), encoding="utf-8")
    print("\n".join(a[-2:] + b))
    print(f"report: {rep}\nlinks: {LINKS_FILE}")


if __name__ == "__main__":
    main()
