"""Fundamental ratios, health checks and a 0-100 fundamental score."""
from __future__ import annotations

from typing import Any

import numpy as np

from ..config import settings


def _f(x: Any) -> float | None:
    try:
        return None if x is None or not np.isfinite(float(x)) else float(x)
    except (TypeError, ValueError):
        return None


def _div(a: float | None, b: float | None) -> float | None:
    return None if a is None or b in (None, 0) else a / b


def summarize(rows: list[dict[str, Any]], price: float, info: dict[str, Any] | None,
              shares_issued: float | None, market_val: dict[str, Any] | None = None) -> dict[str, Any]:
    """rows: fundamentals table rows (dicts), newest first not required. `market_val` ({per, pbv, date})
    is CSE's market P/E and P/BV from the daily market summary, used when settings don't set them."""
    info = info or {}
    shares = _f(shares_issued) or _f(info.get("quantityIssued"))
    market = {
        "price": price, "market_cap": _f(info.get("marketCap")) or (price * shares if shares else None),
        "shares": shares, "high_52w": _f(info.get("p12HiPrice")), "low_52w": _f(info.get("p12LowPrice")),
        "ytd_high": _f(info.get("ytdHiPrice")), "ytd_low": _f(info.get("ytdLowPrice")),
        "all_time_high": _f(info.get("allHiPrice")), "ytd_turnover": _f(info.get("ytdTurnover")),
        "foreign_pct": _f(info.get("foreignPercentage")),
    }
    rows = sorted(rows, key=lambda r: r["period_end"])
    q = [r for r in rows if r["period_type"] == "Q"]
    fy = [r for r in rows if r["period_type"] == "FY"]
    out: dict[str, Any] = {"market": market, "periods": rows[-12:], "ratios": {}, "health": {}, "score": None,
                           "flags": [], "basis": None, "has_data": bool(rows)}
    if not rows:
        out["detail"] = "No financial figures entered yet. Add the latest quarterly/annual figures from the CSE report PDFs."
        return out

    # trailing twelve months
    if len(q) >= 4:
        last4 = q[-4:]
        ttm = {k: sum(_f(r.get(k)) or 0 for r in last4) for k in ("revenue", "net_profit", "operating_profit", "finance_cost", "operating_cash_flow", "dividend_per_share", "one_off_income")}
        bs = q[-1]
        basis = f"TTM to {bs['period_end']}"
        prev_q = q[-8:-4] if len(q) >= 8 else None
    elif fy:
        bs = fy[-1]
        ttm = {k: _f(bs.get(k)) for k in ("revenue", "net_profit", "operating_profit", "finance_cost", "operating_cash_flow", "dividend_per_share", "one_off_income")}
        basis = f"FY ending {bs['period_end']}"
        prev_q = None
    else:
        bs = q[-1]
        ttm = {k: (_f(bs.get(k)) or 0) * 4 for k in ("revenue", "net_profit", "operating_profit", "finance_cost", "operating_cash_flow")}
        ttm["one_off_income"] = _f(bs.get("one_off_income"))
        ttm["dividend_per_share"] = _f(bs.get("dividend_per_share"))
        basis = f"latest quarter ×4 (annualised) to {bs['period_end']}"
        prev_q = None
    out["basis"] = basis
    # balance-sheet items from the most recent period that has them (a statement can omit its balance sheet)
    bs = dict(bs)
    for k in ("total_equity", "total_assets", "total_liabilities", "total_debt", "current_assets", "current_liabilities"):
        if _f(bs.get(k)) is None:
            have = [r for r in rows if _f(r.get(k)) is not None]
            if have:
                bs[k] = have[-1][k]
    sh = _f(bs.get("shares_outstanding")) or shares
    equity = _f(bs.get("total_equity"))
    eps = _div(ttm.get("net_profit"), sh)
    one_off = ttm.get("one_off_income") or 0.0
    core_profit = (ttm.get("net_profit") or 0) - one_off if ttm.get("net_profit") is not None else None
    core_eps = _div(core_profit, sh) if core_profit is not None else None
    bvps = _div(equity, sh)
    r = {
        "eps_ttm": eps, "pe": _div(price, eps) if eps and eps > 0 else None, "bvps": bvps,
        "pbv": _div(price, bvps) if bvps and bvps > 0 else None,
        "roe_pct": (_div(ttm.get("net_profit"), equity) or 0) * 100 if equity else None,
        "net_margin_pct": (_div(ttm.get("net_profit"), ttm.get("revenue")) or 0) * 100 if ttm.get("revenue") else None,
        "debt_to_equity": _div(_f(bs.get("total_debt")), equity),
        "liabilities_to_equity": _div(_f(bs.get("total_liabilities")), equity),
        "current_ratio": _div(_f(bs.get("current_assets")), _f(bs.get("current_liabilities"))),
        "interest_cover": _div(ttm.get("operating_profit"), ttm.get("finance_cost")) if ttm.get("finance_cost") else None,
        "dividend_yield_pct": (_div(ttm.get("dividend_per_share"), price) or 0) * 100 if ttm.get("dividend_per_share") else None,
        "cfo_to_profit": _div(ttm.get("operating_cash_flow"), ttm.get("net_profit")) if ttm.get("operating_cash_flow") else None,
        "core_eps": core_eps if one_off else None,
        "core_pe": _div(price, core_eps) if one_off and core_eps and core_eps > 0 else None,
        "one_off_share_pct": (_div(one_off, ttm.get("net_profit")) or 0) * 100 if one_off and ttm.get("net_profit") else None,
        "nav_per_share": bvps,
        "price_to_nav_discount_pct": (price / bvps - 1) * 100 if bvps and bvps > 0 else None,
    }
    if prev_q:
        prev_np = sum(_f(x.get("net_profit")) or 0 for x in prev_q)
        prev_rev = sum(_f(x.get("revenue")) or 0 for x in prev_q)
        r["profit_growth_pct"] = (_div(ttm["net_profit"] - prev_np, abs(prev_np)) or 0) * 100 if prev_np else None
        r["revenue_growth_pct"] = (_div(ttm["revenue"] - prev_rev, abs(prev_rev)) or 0) * 100 if prev_rev else None
    elif len(fy) >= 2:
        a, b = fy[-2], fy[-1]
        r["profit_growth_pct"] = (_div((_f(b.get("net_profit")) or 0) - (_f(a.get("net_profit")) or 0), abs(_f(a.get("net_profit")) or 0)) or 0) * 100 if _f(a.get("net_profit")) else None
        r["revenue_growth_pct"] = (_div((_f(b.get("revenue")) or 0) - (_f(a.get("revenue")) or 0), abs(_f(a.get("revenue")) or 0)) or 0) * 100 if _f(a.get("revenue")) else None
    if len(q) >= 5:
        last, yago = q[-1], q[-5]
        if _f(yago.get("net_profit")):
            r["latest_q_profit_yoy_pct"] = ((_f(last.get("net_profit")) or 0) / _f(yago.get("net_profit")) - 1) * 100
    out["ratios"] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()}

    # ---- score components (each 0-100) with plain-language reasons
    parts: list[tuple[str, float, str]] = []
    pe = r["pe"]
    if eps is not None and eps <= 0:
        parts.append(("valuation", 20, "loss-making on a trailing basis (no meaningful P/E)"))
        out["flags"].append("Loss-making (trailing)")
    elif pe:
        v = 90 if pe < 6 else 75 if pe < 10 else 60 if pe < 15 else 45 if pe < 22 else 30
        parts.append(("valuation", v, f"P/E {pe:.1f}"))
    if r["pbv"]:
        v = 85 if r["pbv"] < 0.8 else 70 if r["pbv"] < 1.2 else 55 if r["pbv"] < 2 else 35
        parts.append(("book value", v, f"P/BV {r['pbv']:.2f}"))
    if r["roe_pct"] is not None:
        v = 90 if r["roe_pct"] >= 20 else 75 if r["roe_pct"] >= 15 else 55 if r["roe_pct"] >= 10 else 35 if r["roe_pct"] >= 0 else 15
        parts.append(("quality", v, f"ROE {r['roe_pct']:.1f}%"))
    g = r.get("profit_growth_pct")
    if g is not None:
        v = 85 if g >= 25 else 70 if g >= 10 else 55 if g >= 0 else 35 if g >= -20 else 20
        parts.append(("growth", v, f"profit growth {g:+.0f}% year-on-year"))
    if r["debt_to_equity"] is not None or r["current_ratio"] is not None or r["interest_cover"] is not None:
        hv, hr = 60.0, []
        if r["debt_to_equity"] is not None:
            hv += 15 if r["debt_to_equity"] < 0.5 else 0 if r["debt_to_equity"] < 1.5 else -20
            hr.append(f"debt/equity {r['debt_to_equity']:.2f}")
            if r["debt_to_equity"] > 2:
                out["flags"].append(f"High leverage (D/E {r['debt_to_equity']:.1f})")
        if r["current_ratio"] is not None:
            hv += 10 if r["current_ratio"] >= 1.5 else 0 if r["current_ratio"] >= 1 else -15
            hr.append(f"current ratio {r['current_ratio']:.2f}")
        if r["interest_cover"] is not None:
            hv += 10 if r["interest_cover"] >= 5 else 0 if r["interest_cover"] >= 2 else -20
            hr.append(f"interest cover {r['interest_cover']:.1f}×")
            if r["interest_cover"] < 1.5:
                out["flags"].append(f"Weak interest cover ({r['interest_cover']:.1f}×)")
        parts.append(("financial health", float(np.clip(hv, 0, 100)), ", ".join(hr)))
    if r.get("cfo_to_profit") is not None and r["cfo_to_profit"] < 0.5 and (ttm.get("net_profit") or 0) > 0:
        out["flags"].append("Operating cash flow well below reported profit (earnings quality)")
    out["components"] = [{"name": n, "score": round(s, 1), "reason": why} for n, s, why in parts]
    if parts:
        out["score"] = round(float(np.mean([s for _, s, _ in parts])), 1)
    if one_off and r.get("one_off_share_pct") and r["one_off_share_pct"] > 50:
        out["flags"].append(f"{r['one_off_share_pct']:.0f}% of profit is one-off (fair-value / bargain-purchase / non-operating gains); core P/E "
                            + (f"{r['core_pe']:.1f}" if r.get("core_pe") else "n/a"))
    vcfg = settings().get("valuation", {})
    mv = market_val or {}
    mpe, mpbv = vcfg.get("market_pe") or mv.get("per"), vcfg.get("market_pbv") or mv.get("pbv")
    rel = {}
    if mpe and pe:
        rel["pe_vs_market_pct"] = round((pe / mpe - 1) * 100, 1)
    if mpbv and r["pbv"]:
        rel["pbv_vs_market_pct"] = round((r["pbv"] / mpbv - 1) * 100, 1)
    if rel:
        rel["market_pe"], rel["market_pbv"] = mpe, mpbv
        out["relative"] = rel
    # value range: P/E band on core EPS blended with a NAV band (analyst-style low / mid / high)
    lo_pe, mid_pe, hi_pe = vcfg.get("pe_band", [8, 11, 14])
    lo_nv, mid_nv, hi_nv = vcfg.get("nav_band", [0.5, 0.75, 1.0])
    e = core_eps if one_off and core_eps else eps
    band = {}
    if e and e > 0:
        band["pe"] = {"low": round(e * lo_pe, 2), "mid": round(e * mid_pe, 2), "high": round(e * hi_pe, 2)}
    if bvps and bvps > 0:
        band["nav"] = {"low": round(bvps * lo_nv, 2), "mid": round(bvps * mid_nv, 2), "high": round(bvps * hi_nv, 2)}
    if band:
        lows = [b["low"] for b in band.values()]
        highs = [b["high"] for b in band.values()]
        low, high = min(lows), max(highs)
        method = []
        if "pe" in band:
            method.append(f"{'core ' if one_off and core_eps else ''}EPS × P/E {lo_pe}-{mid_pe}-{hi_pe}")
        if "nav" in band:
            method.append(f"NAV/share × {lo_nv}-{mid_nv}-{hi_nv}")
        out["valuation_band"] = {"low": low, "mid": round((low + high) / 2, 2), "high": high,
                                 "method": " and ".join(method) + " (edit the bands in settings.yaml)", "parts": band}
    return out
