"""Portfolio totals: P/L after fees, risk to stops against capital, concentration by stock and sector.

Fees: costs.brokerage_pct_per_side on every buy and sell (the all-in broker rate); open positions are
valued as if sold today, so unrealised P/L already includes the selling fee.
"""
from __future__ import annotations

from typing import Any


def position_figures(p: dict[str, Any], fee_pct: float) -> dict[str, Any]:
    f = fee_pct / 100
    qty, entry = float(p["quantity"]), float(p["entry_price"])
    cost = qty * entry * (1 + f)
    out: dict[str, Any] = {"cost": round(cost, 2)}
    if p.get("closed") and p.get("exit_price") is not None:
        proceeds = qty * float(p["exit_price"]) * (1 - f)
        out.update(realised_net=round(proceeds - cost, 2), realised_net_pct=round((proceeds / cost - 1) * 100, 2))
        return out
    price = p.get("price")
    if price is None:
        return out
    mv = qty * float(price)
    net = mv * (1 - f)
    out.update(market_value=round(mv, 2), unrealised_net=round(net - cost, 2), unrealised_net_pct=round((net / cost - 1) * 100, 2))
    stop = p.get("stop_used")
    if stop:
        at_stop = qty * float(stop) * (1 - f)
        out.update(stop=stop, pl_at_stop=round(at_stop - cost, 2), drop_to_stop_pct=round((float(stop) / float(price) - 1) * 100, 2))
    return out


def summarize(positions: list[dict[str, Any]], capital: float | None, fee_pct: float) -> dict[str, Any]:
    open_ = [p for p in positions if not p.get("closed")]
    closed = [p for p in positions if p.get("closed")]
    rows = {p["id"]: position_figures(p, fee_pct) for p in positions}
    mv = sum(rows[p["id"]].get("market_value") or 0 for p in open_)
    cost_open = sum(rows[p["id"]]["cost"] for p in open_)
    unreal = sum(rows[p["id"]].get("unrealised_net") or 0 for p in open_)
    real = sum(rows[p["id"]].get("realised_net") or 0 for p in closed)
    # risk: what the open book loses (from today's value) if every stop is hit; positions without a stop listed
    at_risk, no_stop = 0.0, []
    for p in open_:
        r = rows[p["id"]]
        if r.get("pl_at_stop") is not None and r.get("market_value"):
            at_risk += (r["market_value"] * (1 - fee_pct / 100)) - (r["cost"] + r["pl_at_stop"])
        else:
            no_stop.append(p["symbol"])
    by_stock = sorted(({"symbol": p["symbol"], "value": rows[p["id"]].get("market_value") or 0} for p in open_), key=lambda x: -x["value"])
    by_sector: dict[str, float] = {}
    for p in open_:
        by_sector[p.get("sector") or "unassigned"] = by_sector.get(p.get("sector") or "unassigned", 0) + (rows[p["id"]].get("market_value") or 0)
    pct = (lambda v: round(100 * v / mv, 1)) if mv else (lambda v: None)
    out = {
        "open_positions": len(open_), "invested": round(cost_open, 2), "market_value": round(mv, 2),
        "unrealised_net": round(unreal, 2), "unrealised_net_pct": round(100 * unreal / cost_open, 2) if cost_open else None,
        "realised_net": round(real, 2), "risk_to_stops": round(at_risk, 2), "positions_without_stop": no_stop,
        "largest": {**by_stock[0], "pct": pct(by_stock[0]["value"])} if by_stock else None,
        "by_stock": [{**x, "pct": pct(x["value"])} for x in by_stock],
        "by_sector": [{"sector": k, "value": round(v, 2), "pct": pct(v)} for k, v in sorted(by_sector.items(), key=lambda kv: -kv[1])],
        "fee_pct_per_side": fee_pct, "rows": rows,
    }
    if capital:
        out.update(capital=capital, invested_pct_of_capital=round(100 * cost_open / capital, 1),
                   risk_to_stops_pct_of_capital=round(100 * at_risk / capital, 2), cash_estimate=round(capital - cost_open + real, 2))
    return out
