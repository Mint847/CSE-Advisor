"""Forward-test journal: each evening's engine calls, kept as made and scored later by what the price did.

Back-tests can be bent, knowingly or not, by every rule tweak made after seeing the results. The journal is the
honest check: a call is written down once after the close (never edited afterwards), then scored by the sessions
that follow, using the same fee model as the studies. Nothing here changes a verdict.

Recorded: every BUY and WATCH call that comes with a plan, from each of the three engines, plus whether the stock met
the selection rule on that day (SCR: uptrend, within 10% of the 52-week high, top 40% by 120-day return).

Scored (entry = the next open, only if it is inside the buy zone and above the stop, as in plan_study):
  r5 / r20 / r40 / r60   net return to the close that many sessions after the call, after both fees
  plan                   target / stop / timeout / open: the plan's stop and first target, 30-session limit
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from .backtest import _fee
from .db import Analysis, JournalEntry, ScreenerRun
from .plan_study import HOLD, exit_trade

log = logging.getLogger(__name__)
HORIZONS = (5, 20, 40, 60)
ENGINES = ("original", "analyst", "smc")
# the back-test numbers to hold the journal against (walk-forward and plan study, 14 years, 4 Nov 2026)
REFERENCE = {
    "original BUY": {"n": 98, "plan_target_first": 13.3, "plan_stop_first": 76.5, "mean_net": -4.6},
    "analyst BUY": {"n": 31, "plan_target_first": 6.5, "plan_stop_first": 61.3, "mean_net": -3.0},
    "smc BUY": {"n": 5, "plan_target_first": 0.0, "plan_stop_first": 100.0, "mean_net": -14.3},
    "original WATCH": {"n": 2790, "plan_target_first": 37.6, "plan_stop_first": 53.3, "mean_net": -2.3},
    "analyst WATCH": {"n": 3084, "plan_target_first": 42.1, "plan_stop_first": 51.5, "mean_net": -2.1},
    "smc WATCH": {"n": 2858, "plan_target_first": 37.5, "plan_stop_first": 52.4, "mean_net": -2.4},
}


def _plan(e: dict[str, Any]) -> dict[str, Any] | None:
    p = e.get("plan")
    if not p or e.get("verdict") not in ("BUY", "WATCH"):
        return None
    tg = p.get("targets") or []
    return {"zl": p["buy_zone"]["low"], "zh": p["buy_zone"]["high"], "ideal": p["ideal"], "trigger": p.get("trigger"), "stop": p["stop"],
            "t1": tg[0]["price"] if tg else None, "t2": tg[1]["price"] if len(tg) > 1 else None, "rr": p.get("reward_risk"),
            "actionable": bool(p.get("actionable")), "label": p.get("entry_label") or p.get("setup_name")}


def _rule_flags(s: Session, symbol: str) -> dict[str, Any] | None:
    """Trend / near-high / relative-strength on the evening of the call, from the stored prices and the latest SCR run."""
    from .analysis.selection import NEAR_HIGH, RS_TOP, features
    from .engine import load_adjusted, load_market
    try:
        adj, _ = load_adjusted(s, symbol)
        if len(adj) < 130:
            return None
        md = load_market(s)
        aspi = md["aspi"].dropna() if not md.empty else pd.Series(dtype=float)
        F, _, _, _ = features(adj, aspi)
        r = F.iloc[-1]
        run = s.scalar(select(ScreenerRun).order_by(ScreenerRun.date.desc()))
        rank = next((x["rs_rank"] for x in (run.rows if run else []) if x["symbol"] == symbol), None)
        trend, dist = bool(r["trendup"]), float(r["dist52"]) if r["dist52"] == r["dist52"] else None
        ok = None if (dist is None or rank is None) else bool(trend and dist > NEAR_HIGH and rank >= RS_TOP)
        return {"trendup": trend, "dist52": dist, "ret120": float(r["ret120"]) if r["ret120"] == r["ret120"] else None, "rs_rank": rank, "ok": ok}
    except Exception as exc:  # noqa: BLE001 - the journal must never break the evening job
        log.warning("journal rule flags %s: %s", symbol, exc)
        return None


def record(s: Session, symbol: str, a: Analysis | None = None) -> int:
    """Write down the latest analysis's BUY / WATCH calls. An existing entry for that stock, date and engine is kept as it is."""
    a = a or s.scalar(select(Analysis).where(Analysis.symbol == symbol).order_by(Analysis.date.desc()))
    if not a:
        return 0
    made = 0
    flags: dict[str, Any] | None = None
    for eng in ENGINES:
        e = (a.recommendation or {}).get(eng)
        plan = _plan(e) if e else None
        if not plan:
            continue
        if s.scalar(select(JournalEntry.id).where(JournalEntry.symbol == symbol, JournalEntry.date == a.date, JournalEntry.engine == eng)):
            continue
        flags = flags if flags is not None else (_rule_flags(s, symbol) or {})
        s.add(JournalEntry(symbol=symbol, date=a.date, engine=eng, verdict=e["verdict"], setup=(e["plan"].get("setup")),
                           triggered=bool(e["plan"].get("triggered")), close=float(e.get("close") or a.recommendation.get("close")),
                           plan=plan, rule=flags or None))
        made += 1
    return made


def score(s: Session, symbol: str, entries: list[JournalEntry]) -> int:
    """Fill in the outcome of entries for one stock from the daily prices we hold."""
    from .engine import load_adjusted
    adj, _ = load_adjusted(s, symbol)
    if adj.empty:
        return 0
    o, h, l, c = (adj[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    idx = {t.date(): i for i, t in enumerate(adj.index)}
    fee, n, done = _fee(), len(adj), 0
    for je in entries:
        t = idx.get(je.date)
        if t is None or t + 1 >= n:
            continue
        p = je.plan
        px = float(o[t + 1])
        entered = 0 < px <= p["zh"] and px > p["stop"]
        out: dict[str, Any] = {"entered": entered, "entry_date": adj.index[t + 1].date().isoformat(), "entry_px": px if entered else None}
        if entered:
            for H in HORIZONS:
                out[f"r{H}"] = round((c[t + H] * (1 - fee) / (px * (1 + fee)) - 1) * 100, 2) if t + H < n else None
            if p.get("t1"):
                res, net, k = exit_trade(t + 1, px, p["stop"], p["t1"], h, l, c, fee)
                out["plan"], out["plan_net"], out["plan_days"] = res, round(net * 100, 2), k - t
            complete = out.get("r60") is not None and out.get("plan") in ("target", "stop", "timeout")
        else:
            complete = t + 6 < n                                   # a skipped call is final once the buy window has passed
        je.outcome = out
        je.scored_at = datetime.utcnow() if complete else None
        done += 1
    return done


def score_pending(s: Session) -> int:
    rows = s.scalars(select(JournalEntry).where(JournalEntry.scored_at.is_(None))).all()
    by: dict[str, list[JournalEntry]] = {}
    for je in rows:
        by.setdefault(je.symbol, []).append(je)
    return sum(score(s, sym, es) for sym, es in by.items())


def record_day(symbols: list[str]) -> dict[str, int]:
    """Called by the evening job after the analyses: write the calls, then score what is old enough."""
    from .db import SessionLocal
    out = {"recorded": 0, "scored": 0}
    for sym in symbols:
        try:
            with SessionLocal.begin() as s:
                out["recorded"] += record(s, sym)
        except Exception as exc:  # noqa: BLE001
            log.warning("journal record %s: %s", sym, exc)
    try:
        with SessionLocal.begin() as s:
            out["scored"] = score_pending(s)
    except Exception as exc:  # noqa: BLE001
        log.warning("journal scoring: %s", exc)
    return out


# ------------------------------------------------------------------------------------------------ the page
def _stats(rows: list[JournalEntry]) -> dict[str, Any]:
    ent = [r for r in rows if (r.outcome or {}).get("entered")]
    d: dict[str, Any] = {"calls": len(rows), "entered": len(ent)}
    for H in (20, 60):
        v = np.array([r.outcome[f"r{H}"] for r in ent if r.outcome.get(f"r{H}") is not None], float)
        d[f"n{H}"] = int(len(v))
        d[f"mean{H}"] = round(float(v.mean()), 2) if len(v) else None
        d[f"win{H}"] = round(float((v > 0).mean() * 100), 1) if len(v) else None
    pl = [r.outcome.get("plan") for r in ent if r.outcome.get("plan") in ("target", "stop", "timeout")]
    d["plan_n"] = len(pl)
    d["target_first"] = round(100 * pl.count("target") / len(pl), 1) if pl else None
    d["stop_first"] = round(100 * pl.count("stop") / len(pl), 1) if pl else None
    pn = np.array([r.outcome["plan_net"] for r in ent if r.outcome.get("plan") in ("target", "stop", "timeout")], float)
    d["plan_mean_net"] = round(float(pn.mean()), 2) if len(pn) else None
    return d


def summary(s: Session, limit: int = 60) -> dict[str, Any]:
    rows = s.scalars(select(JournalEntry).order_by(JournalEntry.date.desc(), JournalEntry.symbol)).all()
    groups: dict[str, list[JournalEntry]] = {}
    for r in rows:
        key = f"{r.engine} {'BUY' if r.verdict == 'BUY' else 'WATCH'}"
        groups.setdefault(key, []).append(r)
        if (r.rule or {}).get("ok") is True:
            groups.setdefault("rule met (any engine)", []).append(r)
        elif (r.rule or {}).get("ok") is False:
            groups.setdefault("rule not met (any engine)", []).append(r)
    by_group = [{"group": k, **_stats(v), "reference": REFERENCE.get(k)} for k, v in sorted(groups.items())]
    first = min((r.date for r in rows), default=None)
    return {"total": len(rows), "scored": sum(1 for r in rows if r.scored_at), "since": first.isoformat() if first else None, "groups": by_group,
            "recent": [{"id": r.id, "symbol": r.symbol, "date": r.date.isoformat(), "engine": r.engine, "verdict": r.verdict, "setup": r.setup,
                        "triggered": r.triggered, "close": r.close, "plan": r.plan, "rule_ok": (r.rule or {}).get("ok"), "outcome": r.outcome}
                       for r in rows[:limit]]}
