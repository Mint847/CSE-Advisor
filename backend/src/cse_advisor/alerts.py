"""Your own alerts, and the inbox every alert lands in.

A rule (UserAlert) watches one stock. Price rules are checked on every live quote while the market is open; pattern,
announcement, verdict and RSI rules are checked once each evening, after the analysis. Every hit becomes an AlertEvent
(the inbox) and, for the Telegram owner, a Telegram message. An event is made once per day per rule: the `key` column
is the memory, so a restart does not resend it.

The engines' own alerts (stop and target hit, entry trigger, buy zone, the evening's signal changes) are written to the
same inbox through `engine_event` / `record_engine_alerts`, so the inbox is the one place that shows everything."""
from __future__ import annotations

import hashlib
import html
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from .db import AlertEvent, PriceDaily, SessionLocal, UserAlert

log = logging.getLogger(__name__)

# kind -> label, the fields it needs, and where it is checked ("live" on each quote, "eod" after the evening analysis)
KINDS: dict[str, dict[str, Any]] = {
    "price_above": {"label": "Price rises to", "when": "live", "fields": {"level": "number"}},
    "price_below": {"label": "Price falls to", "when": "live", "fields": {"level": "number"}},
    "day_up": {"label": "Up on the day by", "when": "live", "fields": {"pct": "percent"}},
    "day_down": {"label": "Down on the day by", "when": "live", "fields": {"pct": "percent"}},
    "near_high": {"label": "Within % of the 52-week high", "when": "live", "fields": {"pct": "percent"}},
    "volume_spike": {"label": "Volume reaches × the 20-day average", "when": "live", "fields": {"mult": "multiple"}},
    "pattern": {"label": "A new high-priority pattern", "when": "eod", "fields": {"direction": "any|bullish|bearish"}},
    "announcement": {"label": "A new announcement", "when": "eod", "fields": {"min_materiality": "1-5"}},
    "verdict_change": {"label": "An engine changes its verdict", "when": "eod", "fields": {"engine": "any|original|analyst|smc"}},
    "rsi_cross": {"label": "RSI(14) crosses", "when": "eod", "fields": {"level": "number", "dir": "above|below"}},
}
LIVE = {k for k, v in KINDS.items() if v["when"] == "live"}
EOD = {k for k, v in KINDS.items() if v["when"] == "eod"}
ENGINES = {"original": "CLS", "analyst": "LVL", "smc": "SMC"}
KEEP_DAYS = 90                      # inbox history


# ------------------------------------------------------------------ rules
def _num(params: dict[str, Any], key: str, lo: float, hi: float) -> float:
    try:
        v = float(params[key])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"'{key}' must be a number") from None
    if not lo < v <= hi:
        raise ValueError(f"'{key}' must be between {lo:g} and {hi:g}")
    return v


def clean_params(kind: str, params: dict[str, Any] | None) -> dict[str, Any]:
    """Check a rule's settings and return only the fields that belong to its kind."""
    if kind not in KINDS:
        raise ValueError("unknown alert kind")
    p = params or {}
    if kind in ("price_above", "price_below"):
        return {"level": round(_num(p, "level", 0, 1e7), 4)}
    if kind in ("day_up", "day_down"):
        return {"pct": round(_num(p, "pct", 0, 100), 2)}
    if kind == "near_high":
        return {"pct": round(_num(p, "pct", 0, 50), 2)}
    if kind == "volume_spike":
        return {"mult": round(_num(p, "mult", 1, 1000), 2)}
    if kind == "pattern":
        d = p.get("direction", "any")
        if d not in ("any", "bullish", "bearish"):
            raise ValueError("direction must be any, bullish or bearish")
        return {"direction": d}
    if kind == "announcement":
        return {"min_materiality": int(_num(p, "min_materiality", 0, 5))}
    if kind == "verdict_change":
        e = p.get("engine", "any")
        if e != "any" and e not in ENGINES:
            raise ValueError("engine must be any, original, analyst or smc")
        return {"engine": e}
    d = p.get("dir", "below")                                   # rsi_cross
    if d not in ("above", "below"):
        raise ValueError("dir must be above or below")
    return {"level": round(_num(p, "level", 0, 100), 1), "dir": d}


def describe(kind: str, params: dict[str, Any]) -> str:
    """A rule in words, for the list and the inbox."""
    p = params or {}
    if kind == "price_above":
        return f"price rises to {p['level']:,.2f} or more"
    if kind == "price_below":
        return f"price falls to {p['level']:,.2f} or less"
    if kind == "day_up":
        return f"up {p['pct']:g}% or more on the day"
    if kind == "day_down":
        return f"down {p['pct']:g}% or more on the day"
    if kind == "near_high":
        return f"within {p['pct']:g}% of the 52-week high"
    if kind == "volume_spike":
        return f"volume reaches {p['mult']:g}× the 20-day average"
    if kind == "pattern":
        return "a new high-priority pattern" + ("" if p.get("direction", "any") == "any" else f" ({p['direction']})")
    if kind == "announcement":
        m = p.get("min_materiality", 0)
        return "a new announcement" + (f" (importance {m}+)" if m else "")
    if kind == "verdict_change":
        e = p.get("engine", "any")
        return "any engine changes its verdict" if e == "any" else f"{ENGINES[e]} changes its verdict"
    return f"RSI(14) crosses {p['dir']} {p['level']:g}"


_live_cache: dict[str, float] = {"at": 0.0, "n": 0}


def has_live_rules() -> bool:
    """Is any live price rule switched on? (cached for 20 s: the live loop asks every few seconds)"""
    if time.monotonic() - _live_cache["at"] > 20:
        with SessionLocal() as s:
            _live_cache["n"] = s.scalar(select(func.count()).select_from(UserAlert).where(UserAlert.active.is_(True), UserAlert.kind.in_(LIVE))) or 0
        _live_cache["at"] = time.monotonic()
    return bool(_live_cache["n"])


def forget_cache() -> None:
    _live_cache["at"] = 0.0


# ------------------------------------------------------------------ the inbox
def _emit(s, uid: str, symbol: str | None, kind: str, text: str, key: str, alert_id: int | None = None) -> dict[str, Any] | None:
    """Write one inbox entry unless this key already exists (the same thing today). Returns it, or None."""
    if s.scalar(select(AlertEvent.id).where(AlertEvent.user_id == uid, AlertEvent.key == key)) is not None:
        return None
    ev = AlertEvent(user_id=uid, alert_id=alert_id, symbol=symbol, kind=kind, text=text, key=key)
    s.add(ev)
    s.flush()
    return {"id": ev.id, "user_id": uid, "symbol": symbol, "kind": kind, "text": text}


def _fire(s, rule: UserAlert, text: str, key: str) -> dict[str, Any] | None:
    ev = _emit(s, rule.user_id, rule.symbol, rule.kind, text, key, rule.id)
    if ev is None:
        return None
    rule.last_fired = datetime.utcnow()
    rule.fired_count = (rule.fired_count or 0) + 1
    if rule.once:
        rule.active = False
    return ev


def deliver(events: list[dict[str, Any]]) -> None:
    """Telegram has one chat: the owner's. Everyone else gets the inbox only."""
    if not events:
        return
    from . import accounts, telegram_bot
    if not telegram_bot.enabled():
        return
    with SessionLocal() as s:
        owner = accounts.telegram_owner(s)
    for ev in events:
        if ev["user_id"] == owner:
            telegram_bot.send_text(f"🔔 <b>{html.escape(ev['symbol'] or '')}</b> {html.escape(ev['text'])}".replace("  ", " ").strip())


def engine_event(uid: str, symbol: str, kind: str, text: str, day: str) -> bool:
    """An engine alert (stop hit, trigger, buy zone...) for the inbox, once a day. True when it is new."""
    with SessionLocal.begin() as s:
        return _emit(s, uid, symbol, kind, text, f"eng:{symbol}:{kind}:{day}") is not None


def record_engine_alerts(uid: str, alerts: list[dict[str, Any]], day: str) -> int:
    """The evening's alerts (signal changes, new patterns, results, position warnings) into the inbox. Telegram already
    gets them in the digest, so nothing is sent from here."""
    n = 0
    with SessionLocal.begin() as s:
        for a in alerts:
            h = hashlib.md5(a["text"].encode("utf-8")).hexdigest()[:10]
            if _emit(s, uid, a.get("symbol"), a["kind"], a["text"], f"eve:{a.get('symbol')}:{a['kind']}:{h}:{day}"):
                n += 1
    return n


def prune(keep_days: int = KEEP_DAYS) -> int:
    with SessionLocal.begin() as s:
        rows = s.scalars(select(AlertEvent).where(AlertEvent.at < datetime.utcnow() - timedelta(days=keep_days))).all()
        for r in rows:
            s.delete(r)
        return len(rows)


# ------------------------------------------------------------------ live rules (each quote while the market is open)
_stats_cache: dict[str, tuple[date, float | None, float | None]] = {}


def _stats(s, symbol: str, today: date) -> tuple[float | None, float | None]:
    """(52-week high, average volume of the last 20 sessions) from stored daily bars before today; cached for the day."""
    hit = _stats_cache.get(symbol)
    if hit and hit[0] == today:
        return hit[1], hit[2]
    rows = s.execute(select(PriceDaily.date, PriceDaily.high, PriceDaily.volume).where(
        PriceDaily.symbol == symbol, PriceDaily.date < today, PriceDaily.date >= today - timedelta(days=365)
    ).order_by(PriceDaily.date.desc())).all()
    hi = max((r.high for r in rows if r.high), default=None)
    vols = [r.volume for r in rows[:20] if r.volume]
    avg = sum(vols) / len(vols) if vols else None
    _stats_cache[symbol] = (today, hi, avg)
    return hi, avg


def _live_text(s, rule: UserAlert, q: dict[str, Any], today: date) -> str | None:
    price = q.get("close")
    if not price:
        return None
    p = rule.params or {}
    if rule.kind == "price_above" and price >= p["level"]:
        return f"is {price:,.2f}, at or above your {p['level']:,.2f}"
    if rule.kind == "price_below" and price <= p["level"]:
        return f"is {price:,.2f}, at or below your {p['level']:,.2f}"
    chg = q.get("change_pct")
    if rule.kind == "day_up" and chg is not None and chg >= p["pct"]:
        return f"is up {chg:+.1f}% on the day at {price:,.2f}"
    if rule.kind == "day_down" and chg is not None and chg <= -p["pct"]:
        return f"is down {chg:+.1f}% on the day at {price:,.2f}"
    if rule.kind == "near_high":
        hi, _ = _stats(s, rule.symbol, today)
        hi = max(hi or 0, q.get("high") or 0) or None
        if hi and price >= hi * (1 - p["pct"] / 100):
            return (f"is at a new 52-week high, {price:,.2f}" if price >= hi else
                    f"is {price:,.2f}, {100 * (1 - price / hi):.1f}% below its 52-week high {hi:,.2f}")
    if rule.kind == "volume_spike":
        _, avg = _stats(s, rule.symbol, today)
        vol = q.get("volume")
        if avg and vol and vol >= p["mult"] * avg:
            return f"has traded {vol:,.0f} shares today, {vol / avg:.1f}× its 20-day average ({avg:,.0f})"
    return None


def evaluate_quotes(quotes: dict[str, dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """Check every live price rule against the latest quotes; returns (and delivers) the events that fired."""
    fired: list[dict[str, Any]] = []
    iso = str(today)
    with SessionLocal.begin() as s:
        rules = s.scalars(select(UserAlert).where(UserAlert.active.is_(True), UserAlert.kind.in_(LIVE))).all()
        for r in rules:
            q = quotes.get(r.symbol)
            if not q or q.get("time") != iso:
                continue
            try:
                text = _live_text(s, r, q, today)
            except Exception as exc:  # noqa: BLE001
                log.warning("alert %s: %s", r.id, exc)
                continue
            if text:
                ev = _fire(s, r, text, f"a{r.id}:{iso}")
                if ev:
                    fired.append(ev)
    deliver(fired)
    return fired


# ------------------------------------------------------------------ evening rules (after the analysis)
def _rsi_pair(s, symbol: str) -> tuple[float, float] | None:
    from .analysis.indicators import rsi
    from .engine import load_adjusted
    adj, _ = load_adjusted(s, symbol)
    if adj is None or len(adj) < 20:
        return None
    r = rsi(adj["close"]).dropna()
    return (float(r.iloc[-2]), float(r.iloc[-1])) if len(r) >= 2 else None


def evaluate_eod(symbols: list[str], prev: dict[str, dict[str, Any]], new_anns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check the pattern / announcement / verdict / RSI rules after tonight's analysis. `prev` is each stock's analysis
    from before the run (jobs._previous_snapshot); `new_anns` the announcements the run just fetched."""
    from .db import Analysis
    fired: list[dict[str, Any]] = []
    wanted = set(symbols)
    with SessionLocal.begin() as s:
        rules = s.scalars(select(UserAlert).where(UserAlert.active.is_(True), UserAlert.kind.in_(EOD),
                                                  UserAlert.symbol.in_(wanted))).all()
        by_sym: dict[str, list[UserAlert]] = {}
        for r in rules:
            by_sym.setdefault(r.symbol, []).append(r)
        for sym, rs in by_sym.items():
            a = s.scalar(select(Analysis).where(Analysis.symbol == sym).order_by(Analysis.date.desc()))
            if not a:
                continue
            day = str(a.date)
            p = prev.get(sym) or {}
            for r in rs:
                par = r.params or {}
                text = None
                if r.kind == "pattern":
                    known = p.get("pattern_ids", set())
                    new = [x for x in a.patterns if x.get("category") not in ("level", "smc", "candlestick")
                           and x.get("priority_tier") == "high" and x["id"] not in known
                           and (x.get("context") or {}).get("age_bars", 99) <= 2
                           and (par.get("direction", "any") == "any" or x.get("direction") == par["direction"])]
                    if new:
                        text = "new pattern: " + ", ".join(f"{x['name']} ({x['direction']})" for x in new[:3])
                elif r.kind == "announcement":
                    hits = [x for x in new_anns if x["symbol"] == sym and (x.get("materiality") or 0) >= par.get("min_materiality", 0)]
                    if hits:
                        text = "announcement: " + "; ".join(f"{x['title']} ({x['category']})" for x in hits[:3])
                elif r.kind == "verdict_change" and p:
                    pairs = {"original": (p.get("verdict"), a.verdict), "analyst": (p.get("verdict_analyst"), a.verdict_analyst),
                             "smc": (p.get("verdict_smc"), a.verdict_smc)}
                    eng = par.get("engine", "any")
                    ch = [f"{ENGINES[e]} {o} → {n}" for e, (o, n) in pairs.items() if (eng in ("any", e)) and o and n and o != n]
                    if ch:
                        text = "verdict change: " + ", ".join(ch)
                elif r.kind == "rsi_cross":
                    try:
                        pr = _rsi_pair(s, sym)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("rsi alert %s: %s", r.id, exc)
                        pr = None
                    if pr:
                        before, now = pr
                        lv = par["level"]
                        if (par["dir"] == "above" and before < lv <= now) or (par["dir"] == "below" and before > lv >= now):
                            text = f"RSI(14) crossed {par['dir']} {lv:g} (now {now:.0f})"
                if text:
                    ev = _fire(s, r, text, f"a{r.id}:{day}")
                    if ev:
                        fired.append(ev)
    deliver(fired)
    return fired
