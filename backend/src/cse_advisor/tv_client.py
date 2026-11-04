"""Read-only client for TradingView's chart websocket (unofficial, anonymous), used only to backfill
intraday history that cse.lk does not provide.

Verified 2026-11-03: CSE stocks are listed as CSELK:<SYMBOL> (e.g. CSELK:JKH.N0000), session 0930-1430
Asia/Colombo, and an anonymous session returns ~5,000 bars per timeframe (5m ~4 months, 15m ~13 months,
1H ~5 years, 4H ~7 years). This is not an official API: it can change or stop working at any time, so
every call goes through this module, fails soft (returns []) and is rate-limited.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import string
import threading
import time
from datetime import datetime
from typing import Any

from .cse_client import TZ

log = logging.getLogger(__name__)

URL = "wss://data.tradingview.com/socket.io/websocket"
RESOLUTION = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1D": "1D"}
_lock = threading.Lock()
_last = 0.0
MIN_GAP = 2.0


def _frame(m: str, p: list[Any]) -> str:
    s = json.dumps({"m": m, "p": p}, separators=(",", ":"))
    return f"~m~{len(s)}~m~{s}"


async def _fetch(symbol: str, resolution: str, n: int, timeout: float) -> list[list[float]]:
    import websockets
    cs = "cs_" + "".join(random.choices(string.ascii_lowercase, k=12))
    async with websockets.connect(URL, origin="https://www.tradingview.com", open_timeout=timeout,
                                  additional_headers={"User-Agent": "Mozilla/5.0"}, max_size=None) as ws:
        for m, p in [("set_auth_token", ["unauthorized_user_token"]), ("chart_create_session", [cs, ""]),
                     ("resolve_symbol", [cs, "sym1", '={"symbol":"%s","adjustment":"splits"}' % symbol]),
                     ("create_series", [cs, "s1", "s1", "sym1", resolution, n, ""])]:
            await ws.send(_frame(m, p))
        bars: list[list[float]] = []
        deadline = time.monotonic() + timeout * 3
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout)
            except asyncio.TimeoutError:
                break
            for hb in re.findall(r"~m~\d+~m~(~h~\d+)", raw):
                await ws.send(f"~m~{len(hb)}~m~{hb}")
            done = False
            for part in re.split(r"~m~\d+~m~", raw):
                if not part.startswith("{"):
                    continue
                js = json.loads(part)
                kind = js.get("m")
                if kind in ("symbol_error", "series_error", "critical_error", "protocol_error"):
                    raise RuntimeError(f"{kind}: {js.get('p')}")
                if kind == "timescale_update":
                    bars = [b["v"] for b in js["p"][1].get("s1", {}).get("s", [])]
                if kind == "series_completed":
                    done = True
            if done:
                break
        return bars


def fetch_bars(symbol: str, tf: str, n: int = 5000, exchange: str = "CSELK", timeout: float = 15.0) -> list[dict[str, Any]]:
    """Bars for CSELK:<symbol> at timeframe tf (1m/5m/15m/1h/4h/1D), oldest first:
    [{ts (naive Colombo datetime), open, high, low, close, volume}]. Returns [] on any failure."""
    global _last
    res = RESOLUTION.get(tf)
    if not res:
        raise ValueError(f"unsupported timeframe {tf}")
    with _lock:
        wait = MIN_GAP - (time.monotonic() - _last)
        if wait > 0:
            time.sleep(wait)
        try:
            raw = asyncio.run(_fetch(f"{exchange}:{symbol}", res, n, timeout))
        except Exception as exc:
            log.warning("TradingView %s %s failed: %s", symbol, tf, exc)
            raw = []
        finally:
            _last = time.monotonic()
    out = []
    for v in raw:
        if len(v) < 5 or v[4] is None:
            continue
        ts = datetime.fromtimestamp(v[0], TZ).replace(tzinfo=None)
        out.append({"ts": ts, "open": float(v[1]), "high": float(v[2]), "low": float(v[3]), "close": float(v[4]),
                    "volume": float(v[5]) if len(v) > 5 and v[5] is not None else 0.0})
    return out
