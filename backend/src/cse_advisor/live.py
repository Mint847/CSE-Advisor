"""Live quotes: polls the CSE trade summary only while someone has a chart open during
market hours (one request covers every stock), and checks intraday level alerts."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time as dtime
from typing import Any

from sqlalchemy import select

from .strategy.recommend import split_engines
from .config import settings
from .cse_client import client, ms_to_local, now_local
from .db import SessionLocal

log = logging.getLogger(__name__)


_status: dict[str, Any] = {"at": 0.0, "open": None, "open_seen": None}


def parse_status(text: str) -> bool | None:
    t = (text or "").lower()
    return False if "close" in t else True if "open" in t else None


def market_open(now: datetime | None = None, fetch: Any = None, cached_only: bool = False) -> bool:
    """CSE's own marketStatus (cached 60 s), so Poya days and other closures read as closed; the clock rule
    is the fallback when the call fails or returns something unexpected. Passing `now` (tests, replays)
    uses the clock rule only."""
    import time as _t
    if now is not None:
        return clock_open(now)
    t = now_local()
    if not cached_only and _t.monotonic() - _status["at"] > 60:
        _status["at"] = _t.monotonic()
        try:
            _status["open"] = parse_status((fetch or client().market_status)())
        except Exception as exc:
            log.debug("marketStatus failed: %s", exc)
            _status["open"] = None
        if _status["open"]:
            _status["open_seen"] = t.date()
    if _status["open"] is None:
        return clock_open(t)
    if _status["open"]:
        return True
    # CSE says closed as soon as trading stops: keep the 10-minute closing-print window, but only on a day
    # the market actually opened (not on a holiday)
    return _status["open_seen"] == t.date() and clock_open(t)


def clock_open(now: datetime, grace: int = 10) -> bool:
    oh, om = map(int, settings()["market"]["open"].split(":"))
    ch, cm = map(int, settings()["market"]["close"].split(":"))
    minutes = now.hour * 60 + now.minute
    return now.weekday() < 5 and oh * 60 + om <= minutes <= ch * 60 + cm + grace   # +grace for the closing print


class LiveQuotes:
    def __init__(self) -> None:
        self.quotes: dict[str, dict[str, Any]] = {}
        self.version = 0
        self.updated: datetime | None = None
        self.subscribers = 0
        self._event = asyncio.Event()
        self.movers: dict[str, Any] = {}
        self._movers_at = 0.0

    def refresh_movers(self, max_age: float = 300) -> dict[str, Any]:
        """Top gainers / losers / most active (3 requests), at most every `max_age` seconds."""
        import time as _t
        if self.movers and _t.monotonic() - self._movers_at < max_age:
            return self.movers
        try:
            m = client().movers()
            self.movers = {
                "gainers": [{"symbol": r["symbol"], "price": r.get("price"), "change_pct": r.get("changePercentage")} for r in m["gainers"]],
                "losers": [{"symbol": r["symbol"], "price": r.get("price"), "change_pct": r.get("changePercentage")} for r in m["losers"]],
                "active": [{"symbol": r["symbol"], "trades": r.get("tradeVolume"), "volume": r.get("shareVolume"),
                            "turnover": r.get("turnover"), "change_pct": r.get("percentageShareVolume")} for r in m["active"]],
                "updated": now_local().isoformat(timespec="seconds")}
            self._movers_at = _t.monotonic()
        except Exception as exc:
            log.debug("movers failed: %s", exc)
        return self.movers

    def _fetch(self) -> None:
        rows = client().trade_summary()
        q = {}
        for r in rows:
            if not r.get("price"):
                continue
            t = ms_to_local(r["lastTradedTime"])
            q[r["symbol"]] = {
                "symbol": r["symbol"], "time": str(t.date()), "last_trade": t.strftime("%H:%M:%S"),
                "open": r.get("open") or r.get("previousClose") or r["price"], "high": r.get("high") or r["price"],
                "low": r.get("low") or r["price"], "close": r["price"], "prev_close": r.get("previousClose"),
                "change": r.get("change"), "change_pct": r.get("percentageChange"),
                "volume": r.get("sharevolume"), "turnover": r.get("turnover"), "trades": r.get("tradevolume"),
            }
        self.quotes = q
        self.version += 1
        self.updated = now_local()

    async def run(self) -> None:
        from . import alerts, telegram_bot
        poll = settings()["market"]["live_poll_seconds"]
        while True:
            try:
                stale = self.updated is None or (now_local() - self.updated).total_seconds() > poll
                alerts_on = bool(settings()["telegram"]["intraday_level_alerts"] and telegram_bot.enabled())                     or await asyncio.to_thread(alerts.has_live_rules)
                is_open = await asyncio.to_thread(market_open)
                if (self.subscribers > 0 or alerts_on) and stale and (is_open or self.updated is None):
                    await asyncio.to_thread(self._fetch)
                    self._event.set()
                    self._event = asyncio.Event()
                    if is_open:
                        await asyncio.to_thread(self.check_level_alerts)
            except Exception as exc:
                log.warning("live poll failed: %s", exc)
            await asyncio.sleep(5)

    async def wait_update(self, timeout: float = 25) -> bool:
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def check_level_alerts(self) -> None:
        """After each quote refresh: the accounts' own price rules, then the engines' levels for the Telegram owner's
        stocks (stop, target, trigger, buy zone). Both go to the inbox once a day (remembered in the database, so a
        restart doesn't repeat them); the owner's also go to Telegram when it is set up."""
        from . import accounts, alerts, telegram_bot
        today = now_local().date()
        try:
            alerts.evaluate_quotes(self.quotes, today)
        except Exception as exc:  # noqa: BLE001
            log.warning("user alerts failed: %s", exc)
        if not settings()["telegram"]["intraday_level_alerts"]:
            return
        day = str(today)
        with SessionLocal() as s:
            owner = accounts.telegram_owner(s)            # Telegram has one chat: its owner's stocks
            for sym in sorted(accounts.user_symbols(s, owner)):
                q = self.quotes.get(sym)
                a = accounts.latest_view(s, owner, sym)
                if not q or not a or q["time"] != day:
                    continue
                price = q["close"]
                ro, ra = split_engines(a.recommendation)
                msgs = []
                pos = accounts.open_position(s, owner, sym)
                if pos:
                    rv = ro.get("position_review") or {}
                    stop = rv.get("suggested_stop") or pos.stop
                    if stop and price <= stop:
                        msgs.append(("stop", f"🛑 {sym} at {price:,.2f} is at/below your stop {stop:,.2f}"))
                    tgt = pos.target or rv.get("target")
                    if tgt and price >= tgt:
                        msgs.append(("target", f"🎯 {sym} at {price:,.2f} reached target {tgt:,.2f}"))
                else:
                    rs = (a.recommendation or {}).get("smc") or {}
                    for tag, verdict, plan in (("CLS", a.verdict, ro.get("plan")), ("LVL", a.verdict_analyst, (ra or {}).get("plan")),
                                               ("SMC", a.verdict_smc, rs.get("plan"))):
                        if not plan or verdict not in ("BUY", "WATCH"):
                            continue
                        if plan.get("trigger") and price > plan["trigger"]:
                            msgs.append((f"trigger_{tag}", f"⚡ [{tag}] {sym} at {price:,.2f} is above trigger {plan['trigger']:,.2f}. Confirm on the close with volume."))
                        zl, zh = plan["buy_zone"]["low"], plan["buy_zone"]["high"]
                        if verdict == "BUY" and zl <= price <= zh:
                            msgs.append((f"zone_{tag}", f"🟢 [{tag}] {sym} at {price:,.2f} is inside the buy zone {zl:,.2f}–{zh:,.2f} (limit {plan['ideal']:,.2f})"))
                for kind, text in msgs:
                    if alerts.engine_event(owner, sym, kind, text, day) and telegram_bot.enabled():
                        telegram_bot.send_text(text)


live = LiveQuotes()
