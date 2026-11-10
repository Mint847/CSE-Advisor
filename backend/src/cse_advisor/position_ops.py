"""Operations on recorded positions: validated edits, partial sells, the CSE fee breakdown, and a heads-up when a split or bonus
issue happened after you bought (the share count and price you recorded may then need updating).

Fees: costs.brokerage_pct_per_side is the all-in rate charged on every buy and sell. costs.fee_breakdown splits it into the
CSE schedule's parts for display only (brokerage, CSE fee, SEC fee, CDS fee, share transaction levy); if the parts do not add up
to the all-in rate, a balancing line is shown, so the total always equals the rate the P/L figures use."""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .analysis.corporate_actions import action_factor
from .config import settings
from .db import CorporateAction, Position

DEFAULT_BREAKDOWN = [("Brokerage", 0.64), ("CSE fee", 0.084), ("SEC fee", 0.072), ("CDS fee", 0.024), ("Share transaction levy", 0.30)]


# ------------------------------------------------------------------ fees
def fee_breakdown(st: dict[str, Any] | None = None) -> dict[str, Any]:
    """The all-in rate per side and its parts (each as % of the trade value); the parts always add up to the rate."""
    st = st if st is not None else settings()
    costs = st.get("costs") or {}
    total = float(costs.get("brokerage_pct_per_side", 1.12))
    raw = costs.get("fee_breakdown")
    parts = [(str(x.get("label")), float(x.get("pct"))) for x in raw if isinstance(x, dict) and x.get("label") and x.get("pct") is not None] if raw else list(DEFAULT_BREAKDOWN)
    diff = round(total - sum(p for _, p in parts), 6)
    if abs(diff) > 1e-6:
        parts.append(("Other (the difference to your all-in rate)" if diff > 0 else "Adjustment (your all-in rate is lower)", diff))
    return {"per_side_pct": total, "slippage_pct": float(costs.get("slippage_pct", 0.2)),
            "parts": [{"label": a, "pct": round(b, 6)} for a, b in parts]}


def fee_lines(notional: float, st: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The fee parts for one trade in rupees."""
    return [{"label": p["label"], "pct": p["pct"], "lkr": round(notional * p["pct"] / 100, 2)} for p in fee_breakdown(st)["parts"]]


# ------------------------------------------------------------------ edit
def apply_edit(p: Position, e: dict[str, Any], today: date | None = None) -> None:
    """Change a recorded position. Only the fields present in `e` change. Raises ValueError with a plain message."""
    today = today or date.today()
    entry_date = e.get("entry_date", p.entry_date)
    entry_price = e.get("entry_price", p.entry_price)
    qty = e.get("quantity", p.quantity)
    if entry_date > today:
        raise ValueError("The buy date can't be in the future.")
    if entry_price is None or entry_price <= 0:
        raise ValueError("The buy price must be above 0.")
    if qty is None or int(qty) < 1:
        raise ValueError("The quantity must be at least 1 share.")
    for k in ("stop", "target"):
        if e.get(k) is not None and e[k] <= 0:
            raise ValueError(f"The {k} must be above 0 (or empty).")
    if p.closed:
        exit_date = e.get("exit_date", p.exit_date)
        exit_price = e.get("exit_price", p.exit_price)
        if exit_price is None or exit_price <= 0:
            raise ValueError("The sell price must be above 0.")
        if exit_date is None or exit_date < entry_date:
            raise ValueError("The sell date can't be before the buy date.")
        if exit_date > today:
            raise ValueError("The sell date can't be in the future.")
        p.exit_date, p.exit_price = exit_date, exit_price
    p.entry_date, p.entry_price, p.quantity = entry_date, float(entry_price), int(qty)
    for k in ("stop", "target", "notes"):
        if k in e:
            setattr(p, k, e[k])


# ------------------------------------------------------------------ partial sells
def close_position(s: Session, p: Position, exit_date: date, exit_price: float, quantity: int | None = None) -> Position:
    """Sell all of a position, or part of it. A part sale leaves the position open with fewer shares and records the sold shares as
    a closed position of their own (same buy date and price), so realised and unrealised P/L stay separate. Returns the closed row."""
    if p.closed:
        raise ValueError("That position is already closed.")
    if exit_price is None or exit_price <= 0:
        raise ValueError("The sell price must be above 0.")
    if exit_date < p.entry_date:
        raise ValueError("The sell date can't be before the buy date.")
    q = p.quantity if quantity is None else int(quantity)
    if q < 1:
        raise ValueError("Sell at least 1 share.")
    if q > p.quantity:
        raise ValueError(f"You hold {p.quantity} shares; you can't sell {q}.")
    if q == p.quantity:
        p.closed, p.exit_date, p.exit_price = True, exit_date, exit_price
        return p
    part = Position(user_id=p.user_id, symbol=p.symbol, entry_date=p.entry_date, entry_price=p.entry_price, quantity=q, stop=p.stop, target=p.target,
                    closed=True, exit_date=exit_date, exit_price=exit_price, notes=f"Part of position #{p.id}: sold {q} of {p.quantity} shares")
    p.quantity -= q
    s.add(part)
    s.flush()
    return part


# ------------------------------------------------------------------ split / bonus since the purchase
def share_multiplier(a: CorporateAction) -> float | None:
    """How many shares one share became (a 10-for-1 sub-division: 10; a 1-for-2 bonus issue: 1.5). None for rights issues."""
    if a.kind not in ("split", "bonus"):
        return None
    f = action_factor({"kind": a.kind, "ratio_new": a.ratio_new, "ratio_old": a.ratio_old}, None)
    return 1 / f if f else None


ACK_KEY = "corp_ack"           # UserPref: {position id: [corporate action ids the user has dealt with]}


def corp_hints(s: Session, p: Position, acked: Any = ()) -> list[dict[str, Any]]:
    """Corporate actions the app has recorded for this stock after the buy date, apart from those already acknowledged (you edited the
    shares or price, or dismissed the note). A split or bonus issue changes the share count, so the position as recorded may be out of
    date: a suggested quantity and price (in today's shares) are included for those."""
    if p.closed:
        return []
    acked = set(acked)
    acts = s.scalars(select(CorporateAction).where(CorporateAction.symbol == p.symbol, CorporateAction.ignore.is_(False),
                                                   CorporateAction.ex_date.is_not(None), CorporateAction.ex_date > p.entry_date)
                     .order_by(CorporateAction.ex_date)).all()
    out, mult = [], 1.0
    for a in acts:
        if a.id in acked:
            continue
        m = share_multiplier(a)
        ratio = f"{a.ratio_new:g} new for {a.ratio_old:g} held" if a.ratio_new and a.ratio_old else None
        kind = {"split": "Share sub-division", "bonus": "Bonus issue", "rights": "Rights issue"}.get(a.kind, a.kind)
        if m:
            mult *= m
        out.append({"id": a.id, "kind": a.kind, "ex_date": str(a.ex_date), "ratio": ratio, "share_multiplier": round(m, 6) if m else None,
                    "text": f"{kind} on {a.ex_date}" + (f" ({ratio})" if ratio else "") + ("." if m else ": your shares are unchanged unless you subscribed, but the price adjusted.")})
    if out and mult != 1.0:
        sug = {"quantity": int(round(p.quantity * mult)), "entry_price": round(p.entry_price / mult, 4)}
        out[-1]["suggest"] = sug
        out[-1]["text"] += f" If your broker credited the new shares, record {sug['quantity']:,} shares bought at {sug['entry_price']:g} (today's terms)."
    return out


def acked_for(s: Session, uid: str, pid: int) -> set[int]:
    from . import accounts
    return {int(x) for x in ((accounts.get_pref(s, uid, ACK_KEY) or {}).get(str(pid)) or [])}


def acknowledge(s: Session, p: Position) -> int:
    """Mark every corporate action currently shown for this position as dealt with. Returns how many were acknowledged."""
    from . import accounts
    ids = [h["id"] for h in corp_hints(s, p, acked_for(s, p.user_id, p.id))]
    if ids:
        ack = dict(accounts.get_pref(s, p.user_id, ACK_KEY) or {})
        ack[str(p.id)] = sorted(set(ack.get(str(p.id), [])) | set(ids))
        accounts.put_pref(s, p.user_id, ACK_KEY, ack)
    return len(ids)
