"""Telegram bot (free Bot API via plain HTTPS): digest, alerts, commands, chart snapshots.

Setup: create a bot with @BotFather, put TELEGRAM_TOKEN in backend/.env, send your
bot any message, then run `python -m cse_advisor.telegram_bot --whoami` and put the
printed id in TELEGRAM_CHAT_ID. The bot only obeys that chat.

With sign-in on there can be many accounts but there is one bot and one chat: it reports on the Telegram owner's
watchlist and positions (accounts.telegram_owner; the account that claimed the pre-sign-in data, or set in SET).
"""
from __future__ import annotations

import html
import io
import logging
import sys
import threading
import time
from typing import Any

import httpx
from sqlalchemy import select

from .strategy.recommend import split_engines
from .config import env, settings
from . import accounts
from .db import Analysis, Position, Security, SessionLocal

log = logging.getLogger(__name__)
_muted = False
AGREE_ICON = {"agree": "● agree", "mixed": "◐ mixed", "differ": "○ differ"}
VERDICT_ICON = {"BUY": "🟢", "WATCH": "🟡", "AVOID": "🔴", "HOLD": "🔵", "TRIM": "🟠", "SELL": "🔴"}


def _token() -> str | None:
    return env("TELEGRAM_TOKEN")


def _chat() -> str | None:
    return env("TELEGRAM_CHAT_ID")


def enabled() -> bool:
    return bool(settings()["telegram"]["enabled"] and _token() and _chat())


def _api(method: str, data: dict | None = None, files: dict | None = None, timeout: float = 30) -> Any:
    r = httpx.post(f"https://api.telegram.org/bot{_token()}/{method}", data=data, files=files, timeout=timeout)
    r.raise_for_status()
    return r.json()


def send_text(text: str, chat: str | None = None) -> None:
    if not (_token() and (chat or _chat())) or (_muted and chat is None):
        return
    for chunk in _chunks(text, 3900):
        try:
            _api("sendMessage", {"chat_id": chat or _chat(), "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": "true"})
        except Exception as exc:
            log.warning("telegram send failed: %s", exc)


def send_photo(png: bytes, caption: str = "", chat: str | None = None) -> None:
    if not (_token() and (chat or _chat())):
        return
    try:
        _api("sendPhoto", {"chat_id": chat or _chat(), "caption": caption[:1000], "parse_mode": "HTML"},
             files={"photo": ("chart.png", png, "image/png")}, timeout=60)
    except Exception as exc:
        log.warning("telegram photo failed: %s", exc)


def _chunks(text: str, n: int) -> list[str]:
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > n:
            parts.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        parts.append(cur)
    return parts


def _f(x: Any) -> str:
    return "n/a" if x is None else f"{x:,.2f}"


# ---------------------------------------------------------------- report text
def _owner() -> str:
    with SessionLocal() as s:
        return accounts.telegram_owner(s)


def report_text(symbol: str, compact: bool = True) -> str:
    with SessionLocal() as s:
        a = accounts.latest_view(s, accounts.telegram_owner(s), symbol)
        sec = s.get(Security, symbol)
    if not a:
        return f"No analysis yet for {html.escape(symbol)}."
    ro, ra = split_engines(a.recommendation)
    rs = (a.recommendation or {}).get("smc")
    agree = (a.recommendation or {}).get("agreement") or {}
    url = env("APP_URL", "http://localhost:8000")
    close = ro.get("close") or (a.recommendation or {}).get("close")
    lines = [f"<b>{html.escape(symbol.split('.')[0])}</b> {_f(close)} · <i>{html.escape(sec.name if sec else symbol)}</i>",
             f"{VERDICT_ICON.get(a.verdict, '')} CLS <b>{a.verdict}</b> {a.score:.0f}/100"
             + (f"   {VERDICT_ICON.get(a.verdict_analyst, '')} LVL <b>{a.verdict_analyst}</b> {a.score_analyst or 0:.0f}/100" if ra else "")
             + (f"   {VERDICT_ICON.get(a.verdict_smc, '')} SMC <b>{a.verdict_smc}</b> {a.score_smc or 0:.0f}/100" if rs else "")
             + (f"   {AGREE_ICON.get(agree.get('state'), '')}" if agree else ""),
             f"Trend {a.trend['state'].replace('_', ' ')} · Liquidity {a.liquidity['grade']} ({a.liquidity['flow']['label']}, {a.liquidity['regime'].replace('_', ' ')})"]
    cat = (a.recommendation or {}).get("catalysts") or {}
    if cat:
        lines.append(f"📰 Catalysts <b>{cat['overall'].upper()}</b>" + (": " + html.escape(" · ".join(cat.get("reasons", [])[:2]), quote=False) if cat.get("reasons") else ""))
        for r in cat.get("risk_lines", [])[:2]:
            lines.append("⏰ " + html.escape(r, quote=False))
    n = 2 if compact else 6
    for tag, verdict, eng_rec in (("CLS", a.verdict, ro), ("LVL", a.verdict_analyst, ra), ("SMC", a.verdict_smc, rs)):
        if not eng_rec:
            continue
        ex = eng_rec.get("explanation") or {}
        plan = eng_rec.get("plan")
        if plan and verdict in ("BUY", "WATCH"):
            lines.append(f"<b>[{tag}]</b> {html.escape(plan.get('entry_label') or plan['setup_name'])}"
                         + (f" · trigger close &gt; <b>{_f(plan['trigger'])}</b>" if plan.get("trigger") else "")
                         + f" · zone {_f(plan['buy_zone']['low'])}–{_f(plan['buy_zone']['high'])} · limit <b>{_f(plan['ideal'])}</b>"
                         + f" · stop <b>{_f(plan['stop'])}</b> · " + " / ".join(f"{t['label']} {_f(t['price'])}" for t in plan["targets"][:2])
                         + f" · R:R {plan['reward_risk']:.2f}")
        if tag == "SMC":
            c = eng_rec.get("smc") or {}
            rg = c.get("range")
            last = c.get("last")
            lines.append(f"[SMC] weekly {c.get('htf_bias', '–')} · daily "
                         + (f"{last['kind'].upper()} {'↑' if last['dir'] == 'up' else '↓'} {_f(last['level'])}" if last else "no break")
                         + (f" · {rg['zone']} ({rg['pos'] * 100:.0f}% of leg, EQ {_f(rg['eq'])})" if rg else ""))
        if tag in ("LVL", "SMC") and not compact:
            for e in (ex.get("entries") or [])[:4]:
                lines.append(f"  • {html.escape(e['label'])}: {html.escape(e['zone'])}"
                             + (f" after close &gt; {_f(e['trigger'])}" if e.get("trigger") else "")
                             + f" · stop {_f(e['stop'])}" + (f" · TP {' / '.join(_f(t) for t in e['targets'][:2])}" if e["targets"] else "")
                             + f" · R:R {e['rr']:.2f}")
        if tag == "LVL":
            for note in (eng_rec.get("corporate") or {}).get("notes", [])[:2]:
                lines.append("📌 " + html.escape(note, quote=False))
        rv = eng_rec.get("position_review")
        if rv:
            lines.append(f"[{tag}] Position {rv['pl_pct']:+.1f}% (LKR {rv['pl_lkr']:,.0f}) · {rv['action']} · stop {_f(rv.get('suggested_stop'))}")
        if ex.get("reasons_for"):
            lines.append(f"✅ [{tag}] " + f"\n✅ [{tag}] ".join(html.escape(r["text"].split(". ")[0], quote=False) for r in ex["reasons_for"][:n]))
        if ex.get("reasons_against"):
            lines.append(f"⚠️ [{tag}] " + f"\n⚠️ [{tag}] ".join(html.escape(r["text"].split(". ")[0], quote=False) for r in ex["reasons_against"][:n]))
    lines.append(f'<a href="{url}/stock/{symbol}">Open full report</a>')
    return "\n".join(lines)


def send_digest(symbols: list[str], alerts: list[dict[str, Any]]) -> None:
    if not enabled() or not settings()["telegram"]["daily_digest"]:
        return
    head = "📊 <b>CSE Advisor: end-of-day report</b>"
    if alerts:
        head += "\n\n🔔 <b>Alerts</b>\n" + "\n".join(f"• <b>{html.escape(x['symbol'].split('.')[0])}</b>: {html.escape(x['text'])}" for x in alerts)
    order = {"BUY": 0, "SELL": 1, "TRIM": 2, "HOLD": 3, "WATCH": 4, "AVOID": 5}
    with SessionLocal() as s:
        owner = accounts.telegram_owner(s)
        latest = [(sym, accounts.latest_view(s, owner, sym)) for sym in symbols]
    soon = []
    for sym, a in latest:
        for e in (((a.recommendation or {}).get("catalysts") or {}).get("next") or []) if a else []:
            if e.get("days", 99) <= 5:
                soon.append(f"• <b>{html.escape(sym.split('.')[0])}</b> {e['date']}: {html.escape(e['title'])}")
    if soon:
        head += "\n\n📅 <b>Next 5 days</b>\n" + "\n".join(soon)
    send_text(head)
    latest.sort(key=lambda x: (order.get(x[1].verdict, 9) if x[1] else 9, -(x[1].score if x[1] else 0)))
    send_text("\n\n".join(report_text(sym) for sym, a in latest if a))


# ---------------------------------------------------------------- chart snapshot
def chart_png(symbol: str, bars: int = 120) -> bytes | None:
    import matplotlib
    matplotlib.use("Agg")
    import mplfinance as mpf

    from .engine import load_adjusted
    with SessionLocal() as s:
        df, _ = load_adjusted(s, symbol)
        a = accounts.latest_view(s, accounts.telegram_owner(s), symbol)
    if df.empty:
        return None
    df = df.tail(bars).rename(columns=str.capitalize)
    start = df.index[0]
    hlines, colors, alines, acolors = [], [], [], []
    if a:
        for z in a.levels.get("zones", []):
            hlines.append(z["price"]); colors.append("#3b82f6" if z["kind"] == "support" else "#f97316")
        fib = a.levels.get("fib")
        lo_v, hi_v = float(df["Low"].min()), float(df["High"].max())
        if fib:
            for lv in fib["levels"]:
                if lo_v * 0.9 <= lv["price"] <= hi_v * 1.1:
                    hlines.append(lv["price"]); colors.append("#2a78d6")
        for key in ("zone1", "zone2"):
            z = (a.levels.get("confluence") or {}).get(key)
            if z:
                hlines += [z["low"], z["high"]]; colors += ["#6b7280", "#6b7280"]
        plan = split_engines(a.recommendation)[0].get("plan")
        if plan and a.verdict in ("BUY", "WATCH", "HOLD", "TRIM"):
            hlines += [plan["ideal"], plan["stop"]] + [t["price"] for t in plan["targets"]]
            colors += ["#16a34a", "#dc2626"] + ["#7c3aed"] * len(plan["targets"])
        for p in [p for p in a.patterns if p["category"] in ("chart_pattern",) and p["priority"] >= 30][:3]:
            for ln in p["lines"]:
                if ln.get("pane"):
                    continue
                t0, t1 = ln["from"]["time"], ln["to"]["time"]
                import pandas as pd
                if pd.Timestamp(t0) < start or pd.Timestamp(t1) > df.index[-1]:
                    continue
                alines.append([(pd.Timestamp(t0), ln["from"]["price"]), (pd.Timestamp(t1), ln["to"]["price"])])
                acolors.append("#16a34a" if p["direction"] == "bullish" else "#dc2626" if p["direction"] == "bearish" else "#6b7280")
    kw: dict[str, Any] = {}
    if hlines:
        kw["hlines"] = {"hlines": hlines, "colors": colors, "linewidths": 0.8, "alpha": 0.7}
    if alines:
        kw["alines"] = {"alines": alines, "colors": acolors, "linewidths": 1.4}
    buf = io.BytesIO()
    mpf.plot(df, type="candle", volume=True, style="yahoo", mav=(20, 50), title=symbol, figsize=(10, 6),
             savefig={"fname": buf, "dpi": 110, "bbox_inches": "tight"}, **kw)
    return buf.getvalue()


# ---------------------------------------------------------------- commands
HELP = ("<b>CSE Advisor commands</b>\n/watchlist: verdicts for your stocks\n/report SYMBOL: report + chart\n"
        "/levels SYMBOL: support/resistance & plan levels\n/add SYMBOL, /remove SYMBOL\n/positions\n/run: run the analysis now\n/mute, /unmute")


def handle_command(text: str, chat: str) -> None:
    global _muted
    from . import ingest, jobs
    parts = text.strip().split()
    cmd, arg = parts[0].split("@")[0].lower(), (parts[1] if len(parts) > 1 else "")
    try:
        if cmd in ("/start", "/help"):
            send_text(HELP, chat)
        elif cmd == "/watchlist":
            with SessionLocal() as s:
                owner = accounts.telegram_owner(s)
                syms = ingest.watchlist_symbols(s, owner)
                rows = [accounts.latest_view(s, owner, x) for x in syms]
            send_text("\n".join(f"<b>{x.split('.')[0]}</b> {_f(split_engines(a.recommendation)[0].get('close'))} · CLS {VERDICT_ICON.get(a.verdict, '')}{a.verdict} {a.score:.0f}"
                                f" · LVL {VERDICT_ICON.get(a.verdict_analyst or '', '')}{a.verdict_analyst or '–'} {a.score_analyst or 0:.0f}"
                                f" · SMC {VERDICT_ICON.get(a.verdict_smc or '', '')}{a.verdict_smc or '–'} {a.score_smc or 0:.0f}"
                                if a else f"• {x}: not analysed yet" for x, a in zip(syms, rows)) or "Watchlist is empty.", chat)
        elif cmd == "/report" and arg:
            sym = ingest.resolve_symbol(arg)
            png = chart_png(sym)
            if png:
                send_photo(png, sym, chat)
            send_text(report_text(sym, compact=False), chat)
        elif cmd == "/levels" and arg:
            sym = ingest.resolve_symbol(arg)
            with SessionLocal() as s:
                a = s.scalar(select(Analysis).where(Analysis.symbol == sym).order_by(Analysis.date.desc()))
            if not a:
                send_text("Not analysed yet.", chat); return
            z = "\n".join(f"{'🟦' if x['kind'] == 'support' else '🟧'} {x['low']:.2f}–{x['high']:.2f} ({x['touches']} touches)" for x in reversed(a.levels["zones"]))
            send_text(f"<b>{sym}</b> levels (close {_f(split_engines(a.recommendation)[0].get('close'))})\n{z}", chat)
        elif cmd == "/add" and arg:
            sym = ingest.add_to_watchlist(arg, _owner())
            jobs.run_eod(notify=False, only=[sym])
            send_text(f"Added {sym}.\n\n" + report_text(sym), chat)
        elif cmd == "/remove" and arg:
            ingest.remove_from_watchlist(ingest.resolve_symbol(arg), _owner())
            send_text(f"Removed {arg.upper()}.", chat)
        elif cmd == "/positions":
            with SessionLocal() as s:
                ps = s.scalars(select(Position).where(Position.user_id == accounts.telegram_owner(s), Position.closed.is_(False))).all()
            send_text("\n".join(f"<b>{p.symbol}</b> {p.quantity:,} @ {_f(p.entry_price)} · stop {_f(p.stop)}" for p in ps) or "No open positions.", chat)
        elif cmd == "/run":
            send_text("Running analysis…", chat)
            threading.Thread(target=jobs.run_eod, kwargs={"notify": True}, daemon=True).start()
        elif cmd == "/mute":
            _muted = True; send_text("Muted scheduled messages.", chat)
        elif cmd == "/unmute":
            _muted = False; send_text("Unmuted.", chat)
        else:
            send_text(HELP, chat)
    except Exception as exc:
        send_text(f"Error: {html.escape(str(exc))}", chat)


def poll_forever(stop: threading.Event) -> None:
    if not _token():
        return
    offset = None
    while not stop.is_set():
        try:
            data = {"timeout": 50}
            if offset:
                data["offset"] = offset
            res = _api("getUpdates", data, timeout=60)
            for u in res.get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                chat = str((msg.get("chat") or {}).get("id", ""))
                text = msg.get("text") or ""
                if not text.startswith("/"):
                    continue
                if not _chat():
                    send_text(f"Your chat id is <code>{chat}</code>. Put it in TELEGRAM_CHAT_ID in backend/.env and restart.", chat)
                elif chat == _chat():
                    handle_command(text, chat)
        except Exception as exc:
            log.debug("telegram poll: %s", exc)
            stop.wait(10)


def start_polling() -> threading.Event:
    stop = threading.Event()
    threading.Thread(target=poll_forever, args=(stop,), daemon=True, name="telegram-poll").start()
    return stop


if __name__ == "__main__":
    if "--whoami" in sys.argv:
        if not _token():
            print("Set TELEGRAM_TOKEN in backend/.env first."); sys.exit(1)
        res = _api("getUpdates", {"timeout": 0})
        ids = {str(u.get("message", {}).get("chat", {}).get("id")) for u in res.get("result", []) if u.get("message")}
        print("Chat ids that messaged the bot:", ", ".join(i for i in ids if i != "None") or "none: send your bot a message first")
    elif "--test" in sys.argv:
        send_text("✅ CSE Advisor test message")
        print("sent" if enabled() else "telegram not configured")
