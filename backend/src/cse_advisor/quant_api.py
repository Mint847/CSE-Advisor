"""Routes for the quant pages (QNT tab). Computation runs in FastAPI's thread pool (plain `def` routes)."""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from .auth import User, current_user
from .db import SessionLocal

router = APIRouter()


def _report(symbol: str, window: str, basis: str, bench: str | None) -> dict[str, Any]:
    from .engine import _jsonable
    from .quant import report
    with SessionLocal() as s:
        try:
            return _jsonable(report.build(s, symbol, window, basis, bench))
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None


@router.get("/api/stocks/{symbol}/quant")
def stock_quant(symbol: str, window: str = Query("3y", pattern="^(1y|3y|5y|all)$"), basis: str = Query("total", pattern="^(total|price)$"),
                bench: str | None = Query(None, max_length=40), _: User = Depends(current_user)) -> dict[str, Any]:
    return _report(symbol, window, basis, bench)


@router.get("/api/global/{symbol}/quant")
def world_quant(symbol: str, window: str = Query("3y", pattern="^(1y|3y|5y|all)$"), bench: str | None = Query(None, max_length=40),
                _: User = Depends(current_user)) -> dict[str, Any]:
    return _report(symbol, window, "price", bench)


# ------------------------------------------------------------------ PORT: portfolio risk and performance (Q2)
def _portfolio(uid: str) -> dict[str, Any]:
    from .engine import _jsonable
    from .quant import metrics as M
    from .quant import portfolio_risk as P
    from .quant import rates as R
    from .quant import series as S
    from .quant.report import _pts, monthly_table
    with SessionLocal() as s:
        rates = R.tbill_series(s)
        af = S.cse_sessions_per_year(s)
        c = P.equity_curve(s, uid)
        if c.get("available"):
            x, a = c.pop("curve"), c.pop("aspi")
            f = M.single(x, rates, af)
            rel = M.relative(x, a, rates, af)
            c.update(figures=f, relative=rel, aspi_figures=M.single(a, rates, af),
                     charts={"portfolio": _pts(x, 4), "aspi": _pts(a, 4), "drawdown": _pts(M.drawdown_series(x)),
                             "pnl": _pts(c.pop("pnl"), 2), "value": _pts(c.pop("value"), 2), "monthly": monthly_table(x)})
        return _jsonable({"curve": c, "risk": P.risk_now(s, uid), "rate": R.rate_info(s), "sessions_per_year": af,
                          "gs_quant": "gs-quant 2.2.5 (Goldman Sachs, Apache-2.0): offline maths only"})


@router.get("/api/portfolio/quant")
def portfolio_quant(u: User = Depends(current_user)) -> dict[str, Any]:
    return _portfolio(u.uid)


@router.get("/api/portfolio/baskets")
def portfolio_baskets(window: str = Query("3y", pattern="^(1y|3y|5y)$"), u: User = Depends(current_user)) -> list[dict[str, Any]]:
    from .engine import _jsonable
    from .quant import portfolio_risk as P
    from .quant.report import _pts
    keep_f = ("total_return", "cagr", "volatility", "max_drawdown", "sharpe", "sessions", "missing")
    keep_r = ("beta", "correlation", "information_ratio", "up_capture", "down_capture", "missing")
    with SessionLocal() as s:
        out = []
        for b in P.list_baskets(s, u.uid, window):
            if b.get("available"):
                lvl, a, hold = b.pop("level"), b.pop("aspi"), b.pop("hold")
                for k in ("figures", "aspi_figures", "hold_figures"):
                    b[k] = {x: v for x, v in b[k].items() if x in keep_f}
                b["relative"] = {k: v for k, v in b["relative"].items() if k in keep_r}
                b["charts"] = {"basket": _pts(lvl, 4), "hold": _pts(hold, 4), "aspi": _pts(a, 4)}
            out.append(b)
        return _jsonable(out)


# ------------------------------------------------------------------ Quant Lab: strategy backtester (Q3)
SAVED_KEY = "lab_strategies"
MAX_SAVED = 30


def _lab_spec(body: dict[str, Any], uid: str) -> dict[str, Any]:
    """The strategy as the page sent it; a `list_id` is turned into that list's stocks."""
    from . import accounts
    spec = dict(body or {})
    lid = spec.pop("list_id", None)
    if lid is not None:
        with SessionLocal() as s:
            try:
                spec["symbols"] = accounts.group_symbols(s, uid, int(lid))
            except (accounts.ListError, ValueError) as exc:
                raise HTTPException(400, str(exc)) from None
    return spec


def _run_view(run: Any, with_result: bool = True) -> dict[str, Any]:
    from .engine import _jsonable
    out: dict[str, Any] = {"id": run.id, "status": run.status, "progress": round(run.progress, 3), "stage": run.stage, "cached": run.cached,
                           "elapsed": round(time.time() - run.started, 1)}
    if run.status == "error":
        out["error"] = run.error
    if run.status == "done" and with_result:
        out["result"] = _jsonable(run.result)
    return out


@router.get("/api/lab/catalog")
def lab_catalog(_: User = Depends(current_user)) -> dict[str, Any]:
    from .quant import strategy_lab
    with SessionLocal() as s:
        return strategy_lab.catalog(s)


@router.post("/api/lab/run")
def lab_run(body: dict[str, Any], u: User = Depends(current_user)) -> dict[str, Any]:
    from .quant import strategy_lab
    try:
        run = strategy_lab.start(u.uid, _lab_spec(body, u.uid))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return _run_view(run, with_result=False)


@router.get("/api/lab/run/{run_id}")
def lab_run_status(run_id: str, u: User = Depends(current_user)) -> dict[str, Any]:
    from .quant import strategy_lab
    run = strategy_lab.get(run_id)
    if run is None or run.uid != u.uid:
        raise HTTPException(404, "That test is no longer kept (the app was restarted, or many newer tests were run). Run it again.")
    return _run_view(run)


@router.get("/api/lab/saved")
def lab_saved(u: User = Depends(current_user)) -> list[dict[str, Any]]:
    from . import accounts
    with SessionLocal() as s:
        return accounts.get_pref(s, u.uid, SAVED_KEY) or []


@router.put("/api/lab/saved")
def lab_save(body: dict[str, Any], u: User = Depends(current_user)) -> list[dict[str, Any]]:
    from . import accounts
    from .quant import strategy_lab
    try:
        spec = strategy_lab.normalise(_lab_spec(body.get("spec") or {}, u.uid))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    name = " ".join(str(body.get("name") or spec["name"]).split())[:60] or "My strategy"
    spec["name"] = name
    with SessionLocal.begin() as s:
        cur = [x for x in (accounts.get_pref(s, u.uid, SAVED_KEY) or []) if x.get("name") != name]
        if len(cur) >= MAX_SAVED:
            raise HTTPException(400, f"You can keep at most {MAX_SAVED} strategies: delete one first.")
        cur.append({"name": name, "spec": spec, "saved_at": datetime.utcnow().isoformat(timespec="seconds")})
        accounts.put_pref(s, u.uid, SAVED_KEY, cur)
        return cur


@router.delete("/api/lab/saved")
def lab_delete(name: str = Query(..., max_length=60), u: User = Depends(current_user)) -> list[dict[str, Any]]:
    from . import accounts
    with SessionLocal.begin() as s:
        cur = [x for x in (accounts.get_pref(s, u.uid, SAVED_KEY) or []) if x.get("name") != name]
        accounts.put_pref(s, u.uid, SAVED_KEY, cur)
        return cur


# ------------------------------------------------------------------ Quant Lab extras (Q4): event studies, correlations, baskets, coverage
def _mine(uid: str) -> list[str]:
    from . import accounts
    with SessionLocal() as s:
        return sorted(accounts.user_symbols(s, uid))


def _guard(fn: Any, *a: Any, **kw: Any) -> Any:
    try:
        return fn(*a, **kw)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None


@router.get("/api/lab/events/catalog")
def lab_events_catalog(u: User = Depends(current_user)) -> dict[str, Any]:
    from .quant import events
    syms = _mine(u.uid)
    with SessionLocal() as s:
        return {**events.catalog(s, syms), "stocks": [{"id": x, "label": x.split(".")[0]} for x in syms]}


@router.post("/api/lab/events")
def lab_events(body: dict[str, Any], u: User = Depends(current_user)) -> dict[str, Any]:
    from .engine import _jsonable
    from .quant import events
    spec = dict(body or {})
    if spec.get("kind") == "company":
        mine = set(_mine(u.uid))
        spec["symbols"] = [x for x in (spec.get("symbols") or []) if x in mine][:30]
    with SessionLocal() as s:
        return _jsonable(_guard(events.run, s, spec))


@router.get("/api/lab/corr/options")
def lab_corr_options(u: User = Depends(current_user)) -> dict[str, Any]:
    from .quant import correlation
    with SessionLocal() as s:
        return correlation.options(s, _mine(u.uid))


@router.post("/api/lab/corr")
def lab_corr(body: dict[str, Any], u: User = Depends(current_user)) -> dict[str, Any]:
    from .engine import _jsonable
    from .quant import correlation
    pair = body.get("pair")
    pr = (int(pair[0]), int(pair[1])) if isinstance(pair, list) and len(pair) == 2 else None
    mine = set(_mine(u.uid))
    ids = [x for x in (str(i) for i in (body.get("ids") or [])) if x.startswith("^") or x.startswith("sector:") or ":" in x or x in mine]
    with SessionLocal() as s:
        return _jsonable(_guard(correlation.compute, s, ids, str(body.get("window", "3y")), str(body.get("freq", "daily")), pr))


@router.post("/api/lab/basket")
def lab_basket(body: dict[str, Any], u: User = Depends(current_user)) -> dict[str, Any]:
    from .engine import _jsonable
    from .quant import portfolio_risk as P
    from .quant.report import _pts
    mine = set(_mine(u.uid))
    syms = [x for x in (body.get("symbols") or []) if x in mine][:15]
    if len(syms) < 2:
        raise HTTPException(400, "Choose at least two of your stocks.")
    weights = body.get("weights", "equal")
    if isinstance(weights, dict):                                    # {symbol: weight} from the page
        weights = [float(weights.get(x, 0)) for x in syms]
    window = str(body.get("window", "3y"))
    if window not in ("1y", "3y", "5y"):
        raise HTTPException(400, "Window must be 1y, 3y or 5y.")
    with SessionLocal() as s:
        b = _guard(P.basket, s, syms, window, str(body.get("name") or "Your basket"), weights, str(body.get("rebalance", "monthly")))
    if b.get("available"):
        lvl, a, hold = b.pop("level"), b.pop("aspi"), b.pop("hold")
        b["charts"] = {"basket": _pts(lvl, 4), "hold": _pts(hold, 4), "aspi": _pts(a, 4)}
    return _jsonable(b)


@router.get("/api/lab/coverage")
def lab_coverage(_: User = Depends(current_user)) -> dict[str, Any]:
    from .quant import coverage
    return {"version": coverage.GS_QUANT_VERSION, "marquee": coverage.MARQUEE, "entries": coverage.table()}


@router.get("/api/market/risk")
def market_risk(_: User = Depends(current_user)) -> dict[str, Any]:
    from .engine import _jsonable
    from .quant import market_risk as MR
    with SessionLocal() as s:
        return _jsonable(MR.build(s))
