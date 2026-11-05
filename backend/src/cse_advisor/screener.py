"""SCR: a daily ranking of every liquid CSE stock by the one selection rule that held up in the 14-year factor study.

    python -m cse_advisor.screener          # run now (a few minutes; ~15 minutes when the full list is re-checked)

The rule (analysis/selection.py): the stock is in an uptrend (close > 50-day average > 200-day average), within 10%
of its 52-week high, and in the top 40% by 120-day return among the liquid stocks. In the study that combination beat
the average uptrending stock by about 1.5 to 3.5 points over 40 to 90 sessions, in both halves of the history, but it
was chosen after looking at the data and the sample is one small, survivor-biased market. So the page calls it "tested,
not proven" and the forward-test journal keeps checking it on new data.

Data: TradingView's adjusted daily bars (one request per stock, ~2 s apart). Nothing is stored except the finished
ranking, and nothing here changes an engine verdict or a BUY rule.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime
from typing import Any

import pandas as pd
from sqlalchemy import select

from . import tv_client
from .analysis.selection import NEAR_HIGH, RS_TOP, features, qualifies
from .config import settings
from .cse_client import client
from .db import Preference, ScreenerRun, SessionLocal, WatchlistItem

log = logging.getLogger(__name__)
_lock = threading.Lock()
progress: dict[str, Any] = {"running": False, "done": 0, "total": 0, "phase": ""}

RULE_TEXT = (f"uptrend (close above its 50-day average, which is above its 200-day), within {abs(NEAR_HIGH) * 100:.0f}% of the 52-week high, "
             f"and in the top {100 - RS_TOP * 100:.0f}% by 120-day return among the liquid stocks")
TESTED = ("Tested on 42 stocks over 14 years: such stocks beat the average uptrending stock by roughly 1.5 to 3.5 points over 40 to 90 sessions after "
          "costs, in both halves of the history. The rule was chosen after looking at the data, so this is tested, not proven.")


def cfg() -> dict[str, Any]:
    return settings().get("screener", {})


def _bars(sym: str, n: int, upto: date) -> pd.DataFrame | None:
    raw = tv_client.fetch_bars(sym, "1D", n)
    if len(raw) < 130:
        return None
    d = pd.DataFrame(raw)
    d["date"] = pd.to_datetime(d["ts"]).dt.normalize()
    d = d.drop_duplicates("date").set_index("date").sort_index()
    d = d[d.index <= pd.Timestamp(upto)]                       # never score a half-finished session
    d = d[(d[["open", "high", "low", "close"]] > 0).all(axis=1)]
    return d[["open", "high", "low", "close", "volume"]] if len(d) >= 130 else None


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def build_rows(snap: dict[str, dict[str, Any]], names: dict[str, str], watch: set[str], min_turnover: float,
               aspi_ret120: float | None) -> list[dict[str, Any]]:
    """snap: symbol -> the last feature row (dict). Ranks 120-day return among the liquid stocks, applies the rule."""
    liquid = {k: v for k, v in snap.items() if (_num(v.get("turn")) or 0) >= min_turnover}
    ret = pd.Series({k: _num(v.get("ret120")) for k, v in liquid.items()}).dropna()
    rank = ret.rank(pct=True) if len(ret) else ret
    rows = []
    for sym, v in liquid.items():
        r120 = _num(v.get("ret120"))
        if r120 is None or _num(v.get("dist52")) is None:
            continue
        rk = float(rank.get(sym, float("nan")))
        trend, near, top = bool(v["trendup"]), v["dist52"] > NEAR_HIGH, rk >= RS_TOP
        hits = int(trend) + int(near) + int(top)
        missing = [t for ok, t in ((trend, "not in an uptrend (needs close > 50-day > 200-day average)"),
                                   (near, f"{abs(v['dist52']) * 100:.0f}% below its 52-week high (needs within {abs(NEAR_HIGH) * 100:.0f}%)"),
                                   (top, f"120-day return ranks {rk * 100:.0f}th percentile (needs {RS_TOP * 100:.0f}th or higher)")) if not ok]
        reason = ("Uptrend, " if trend else "") + f"{abs(v['dist52']) * 100:.0f}% below its 52-week high, 120-day return {r120 * 100:+.0f}%" \
            + (f" (ASPI {aspi_ret120 * 100:+.0f}%)" if aspi_ret120 is not None else "") + f", {rk * 100:.0f}th percentile of {len(ret)} liquid stocks"
        rows.append({"symbol": sym, "name": names.get(sym, sym), "close": _num(v["close"]), "chg1d": _num(v.get("chg1d")),
                     "ret20": _num(v.get("ret20")), "ret60": _num(v.get("ret60")), "ret120": r120, "rs120": _num(v.get("rs120")),
                     "dist52": _num(v["dist52"]), "ext20": _num(v.get("ext20")), "trendup": trend, "above50": bool(v.get("above50")), "turn": _num(v["turn"]), "rs_rank": _num(rk),
                     "qualifies": qualifies(trend, v["dist52"], rk), "hits": hits, "missing": missing, "reason": reason,
                     "on_watchlist": sym in watch})
    rows.sort(key=lambda r: (not r["qualifies"], -r["hits"], -(r["rs_rank"] or 0)))
    return rows


def run(force_universe: bool = False) -> dict[str, Any]:
    if not cfg().get("enabled", True):
        return {"ok": False, "message": "screener disabled in settings.yaml"}
    if not _lock.acquire(blocking=False):
        return {"ok": False, "message": "screener already running"}
    t0 = time.time()
    progress.update(running=True, done=0, total=0, phase="starting")
    try:
        from .jobs import last_trading_day
        upto = last_trading_day()
        nbars = int(cfg().get("bars", 400))
        min_turn = float(cfg().get("min_median_turnover_lkr", 2_000_000))
        with SessionLocal() as s:
            uni = s.get(Preference, "screener_universe")
            watch = {w.symbol for w in s.scalars(select(WatchlistItem)).all()}
        summary = client().trade_summary()
        names = {r["symbol"]: r.get("name") or r["symbol"] for r in summary}
        ordinary = [r["symbol"] for r in summary if r["symbol"].endswith(".N0000")]
        refresh_due = force_universe or not uni or (date.today() - date.fromisoformat(uni.value["date"])).days >= int(cfg().get("universe_refresh_days", 14))
        todo = ordinary if refresh_due else [x for x in uni.value["symbols"] if x in names]
        progress.update(total=len(todo) + 1, phase="full list (liquidity re-check)" if refresh_due else "liquid stocks")
        a = _bars("ASI", nbars, upto)
        aspi = a["close"] if a is not None else pd.Series(dtype=float)
        progress["done"] += 1
        snap: dict[str, dict[str, Any]] = {}
        failed: list[str] = []
        last_bar = None
        for sym in todo:
            try:
                d = _bars(sym, nbars, upto)
            except Exception as exc:  # noqa: BLE001
                log.warning("screener %s: %s", sym, exc)
                d = None
            progress["done"] += 1
            if d is None:
                failed.append(sym)
                continue
            F, _, _, _ = features(d, aspi)
            row = F.iloc[-1].to_dict()
            row.update(close=float(d["close"].iloc[-1]), chg1d=float(d["close"].iloc[-1] / d["close"].iloc[-2] - 1) if len(d) > 1 else None)
            snap[sym] = row
            last_bar = max(last_bar, d.index[-1]) if last_bar is not None else d.index[-1]
        if not snap or last_bar is None:
            return {"ok": False, "message": "no price data came back (TradingView may be unreachable)"}
        a120 = float(aspi.iloc[-1] / aspi.iloc[-121] - 1) if len(aspi) > 121 else None
        rows = build_rows(snap, names, watch, min_turn, a120)
        meta = {"rule": RULE_TEXT, "tested": TESTED, "checked": len(todo), "failed": failed, "liquid": len(rows), "min_median_turnover_lkr": min_turn,
                "qualifying": sum(r["qualifies"] for r in rows), "breadth_above50": round(sum(r["above50"] for r in rows) / len(rows), 3) if rows else None, "aspi_ret120": a120, "last_bar": last_bar.date().isoformat(),
                "full_recheck": refresh_due, "seconds": round(time.time() - t0)}
        with SessionLocal.begin() as s:
            run_row = s.get(ScreenerRun, last_bar.date()) or ScreenerRun(date=last_bar.date())
            run_row.rows, run_row.meta, run_row.created_at = rows, meta, datetime.utcnow()
            s.add(run_row)
            if refresh_due:
                s.merge(Preference(key="screener_universe", value={"date": date.today().isoformat(), "symbols": [r["symbol"] for r in rows]}))
        log.info("screener: %d liquid, %d qualify (%ds)", len(rows), meta["qualifying"], meta["seconds"])
        return {"ok": True, **meta}
    except Exception as exc:  # noqa: BLE001
        log.exception("screener failed")
        return {"ok": False, "message": str(exc)}
    finally:
        progress.update(running=False, phase="")
        _lock.release()


def latest() -> dict[str, Any] | None:
    with SessionLocal() as s:
        r = s.scalar(select(ScreenerRun).order_by(ScreenerRun.date.desc()))
        if not r:
            return None
        with SessionLocal() as s2:
            watch = {w.symbol for w in s2.scalars(select(WatchlistItem)).all()}
        rows = [{**x, "on_watchlist": x["symbol"] in watch} for x in r.rows]
        return {"date": r.date.isoformat(), "created_at": r.created_at.isoformat(), "rows": rows, "meta": r.meta}


def catch_up() -> None:
    """Run once if the ranking is older than the last finished session (called from startup and after the EOD job)."""
    if not cfg().get("enabled", True):
        return
    from .jobs import last_trading_day
    with SessionLocal() as s:
        r = s.scalar(select(ScreenerRun).order_by(ScreenerRun.date.desc()))
    if r is not None and (datetime.utcnow() - r.created_at).total_seconds() < 6 * 3600:
        return                                                  # just ran; do not repeat on every restart
    if r is None or r.date < last_trading_day():
        run()


def start_background() -> None:
    threading.Thread(target=catch_up, daemon=True, name="screener").start()


if __name__ == "__main__":
    from .db import init_db
    logging.basicConfig(level=logging.INFO)
    init_db()
    print(run(force_universe=False))
