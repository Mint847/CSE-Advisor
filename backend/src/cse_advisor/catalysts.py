"""The Catalysts view: everything news-, results- and event-driven about one stock, as evidence.

Built from data the app already stores: classified announcements (with typed dividend / dealing / AGM
facts), the statement figures read from the quarterly PDFs, the reporting history, corporate actions,
matched headlines and the price history. The scorecard rolls the signals up to Supportive / Neutral /
Cautious; it is shown next to the engines, never blended into their scores.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from .analysis.corporate_actions import phases
from .analysis.events import results_due, risk_lines, upcoming
from .analysis.news import event_text
from .analysis.reactions import MIN_N, reaction, stats, xd_drop
from .analysis.report_parser import period_from_title
from .db import Analysis, Announcement, CorporateAction, FinancialReport, Fundamental, Headline, Security

MATERIAL = {"mna", "disposal", "investment", "rating", "rights_issue", "bonus_issue", "share_split", "buyback", "dilution",
            "management_exit", "red_flag", "suspension", "litigation", "non_compliance"}


def _row(o: Any) -> dict[str, Any]:
    return {c.name: getattr(o, c.name) for c in o.__table__.columns}


def build(s: Session, symbol: str, closes: pd.Series, aspi: pd.Series | None, today: date | None = None,
          analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    today = today or date.today()
    close = float(closes.iloc[-1]) if len(closes) else None
    anns = list(s.scalars(select(Announcement).where(Announcement.symbol == symbol).order_by(Announcement.date.desc())))
    reports = list(s.scalars(select(FinancialReport).where(FinancialReport.symbol == symbol, FinancialReport.kind == "quarterly")))
    funds = [_row(f) for f in s.scalars(select(Fundamental).where(Fundamental.symbol == symbol, Fundamental.period_type == "Q")
                                        .order_by(Fundamental.period_end))]
    actions = [_row(a) for a in s.scalars(select(CorporateAction).where(CorporateAction.symbol == symbol))]
    if analysis is None:
        a = s.scalar(select(Analysis).where(Analysis.symbol == symbol).order_by(Analysis.date.desc()))
        analysis = {"recommendation": a.recommendation, "fundamentals": a.fundamentals} if a else {}
    rec = (analysis or {}).get("recommendation") or {}

    # ---- events with their price reaction
    events: list[dict[str, Any]] = []
    fig_by_report = {f["report_id"]: f for f in funds if f.get("report_id") and f["source"] == "pdf"}
    for r in reports:
        pe, _ = period_from_title(r.title)
        if not r.uploaded:
            continue
        f = fig_by_report.get(r.id)
        yoy = _yoy(funds, f) if f else None
        events.append({"kind": "results", "when": r.uploaded, "title": r.title, "period_end": str(pe) if pe else None,
                       "detail": (f"profit {f['net_profit'] / 1e6:,.0f}M" + (f" ({yoy:+.0f}% YoY)" if yoy is not None else "")) if f and f.get("net_profit") is not None else ""})
    for a in anns:
        ev = a.event or {}
        if not a.date:
            continue
        if ev.get("kind") == "cash_dividend":
            events.append({"kind": "dividend declared", "when": a.date, "title": event_text(ev) or a.title, "aid": a.id})
        elif ev.get("kind") == "dealing" and ev.get("net_value"):
            events.append({"kind": "director buy" if ev["net_value"] > 0 else "director sell", "when": a.date, "title": event_text(ev), "aid": a.id})
        elif (a.category or "") in MATERIAL:
            events.append({"kind": a.category, "when": a.date, "title": a.attachment or a.title, "aid": a.id})
    for e in events:
        e["reaction"] = reaction(closes, aspi, e["when"]) if len(closes) > 2 else None
        e["when"] = e["when"].isoformat(timespec="minutes")
    rstats = stats(events)

    # ---- dividends
    divs = [a.event for a in anns if (a.event or {}).get("kind") == "cash_dividend" and a.event.get("dps")]
    past = [d for d in divs if d.get("xd") and date.fromisoformat(d["xd"]) <= today]
    ttm = sum(d["dps"] for d in past if date.fromisoformat(d["xd"]) > today - timedelta(days=365))
    # the XD-day drop only means something when the dividend is well above the price tick (yield >= 1%)
    drops = [x for x in (xd_drop(closes, d["xd"], d["dps"]) for d in past[:8]) if x and x["yield_pct"] >= 1.0]
    dividends = {"ttm_dps": round(ttm, 2) if ttm else None, "ttm_yield_pct": round(100 * ttm / close, 2) if ttm and close else None,
                 "history": [{k: d.get(k) for k in ("dps", "xd", "payment", "fy", "final")} for d in divs[:10]], "xd_drops": drops}

    # ---- insiders (director dealings with direction, from the typed records)
    def window(days: int) -> dict[str, Any]:
        rows = [a for a in anns if a.date and (a.event or {}).get("kind") == "dealing" and (datetime.now() - a.date).days <= days]
        buys = [a for a in rows if (a.event.get("net_value") or 0) > 0]
        sells = [a for a in rows if (a.event.get("net_value") or 0) < 0]
        return {"days": days, "notices": len(rows), "buys": len(buys), "sells": len(sells),
                "net_value": round(sum(a.event.get("net_value") or 0 for a in rows), 0)}
    dealings = [a for a in anns if (a.event or {}).get("kind") == "dealing"]
    insiders = {"d90": window(90), "d365": window(365),
                # dealings in the last 3 years whose detail hasn't been read yet (older ones are not read)
                "unknown_direction": sum(1 for a in anns if a.category == "director_dealing" and not (a.event or {}).get("kind")
                                         and a.date and (datetime.now() - a.date).days <= 3 * 365),
                "recent": [{"date": a.date.date().isoformat(), "director": a.event.get("director"), "role": a.event.get("role"),
                            "net_value": a.event.get("net_value"), "trades": a.event.get("trades")} for a in dealings[:8]]}

    # ---- calendar
    pairs = []
    for r in reports:
        pe, _ = period_from_title(r.title)
        if pe and r.uploaded:
            pairs.append((pe, r.uploaded.date()))
    due = results_due(pairs, today)
    agms = [a.event for a in anns if (a.event or {}).get("kind") == "agm"]
    cal = upcoming(today, [d for d in divs if (d.get("xd") or d.get("payment"))], agms, actions, due, close)
    risks = risk_lines(today, cal, due)
    corp = phases(actions, today, close) if close else {"phase": None, "notes": []}

    # ---- results table (newest first) with YoY
    results = []
    for f in reversed(funds[-8:]):
        results.append({"period_end": str(f["period_end"]), "revenue": f.get("revenue"), "net_profit": f.get("net_profit"), "eps": f.get("eps"),
                        "profit_yoy_pct": _yoy(funds, f), "source": f["source"], "needs_review": bool(f.get("needs_review")),
                        "checks": (f.get("detail") or {}).get("checks"), "url": (f.get("detail") or {}).get("url")})

    # ---- headlines
    from .news_feeds import matchers, stock_reading
    cutoff = datetime.now() - timedelta(days=60)
    sec = s.get(Security, symbol)
    pats = matchers([(symbol, sec.name if sec else symbol)])
    hl, counted, seen = [], [], set()
    for h in s.scalars(select(Headline).where(Headline.published >= cutoff).order_by(Headline.published.desc())):
        llm_syms = {c.get("symbol") for c in (h.llm or {}).get("companies") or []}
        if symbol not in (h.symbols or []) and symbol not in llm_syms:
            continue
        v = stock_reading(h, symbol, pats)
        hl.append((h, v))
        if v is not None and (h.cluster or h.id) not in seen:     # the same story from several outlets counts once
            seen.add(h.cluster or h.id)
            counted.append((h, v))
    h30 = [(h, v) for h, v in counted if h.published and h.published >= datetime.now() - timedelta(days=30)]

    def _item(h: Headline, v: float | None) -> dict[str, Any]:
        llm = h.llm or {}
        return {"source": h.outlet or h.source, "title": h.title, "link": h.link,
                "published": h.published.isoformat(timespec="minutes") if h.published else None,
                "tone": v if v is not None else h.tone, "counted": v is not None,
                "read_by": "Claude" if llm.get("by") else "keywords", "reason": llm.get("reason"), "category": llm.get("category")}
    headlines = {"n30": len(h30), "tone30": round(sum(v for _, v in h30) / len(h30), 2) if h30 else None,
                 "items": [_item(h, v) for h, v in hl[:15]]}

    # ---- announcement list
    react = {e["aid"]: e.get("reaction") for e in events if e.get("aid")}
    alist = []
    for a in anns[:80]:
        ev = a.event or {}
        alist.append({"id": a.id, "date": a.date.isoformat(timespec="minutes") if a.date else None, "title": a.title, "subject": a.attachment,
                      "summary": event_text(ev) or None, "category": a.category, "sentiment": a.sentiment, "materiality": a.materiality,
                      "reaction": react.get(a.id)})

    out = {"symbol": symbol, "asof": today.isoformat(), "close": close, "calendar": cal, "results_due": due, "risk_lines": risks,
           "results": results, "dividends": dividends, "insiders": insiders, "headlines": headlines, "announcements": alist,
           "reaction_stats": rstats, "events": sorted(events, key=lambda e: e["when"], reverse=True)[:40],
           "corporate": {"phase": corp.get("phase"), "notes": corp.get("notes", [])}}
    out["scorecard"] = scorecard(out, rec, (analysis or {}).get("fundamentals") or {})
    return out


def _yoy(funds: list[dict[str, Any]], f: dict[str, Any] | None) -> float | None:
    if not f or f.get("net_profit") is None:
        return None
    pe = f["period_end"]
    try:
        prev_pe = pe.replace(year=pe.year - 1)
    except ValueError:
        prev_pe = pe.replace(year=pe.year - 1, day=28)
    prev = next((x for x in funds if x["period_end"] == prev_pe and x.get("net_profit")), None)
    # a % change against a loss (e.g. -804M -> +62M) is not meaningful: no YoY figure then
    if not prev or not prev["net_profit"] or prev["net_profit"] <= 0:
        return None
    return round((f["net_profit"] / prev["net_profit"] - 1) * 100, 1)


def scorecard(c: dict[str, Any], rec: dict[str, Any], fundamentals: dict[str, Any]) -> dict[str, Any]:
    """Each signal: reading, direction (+1 supportive / 0 / -1 cautious) and why."""
    rows: list[dict[str, Any]] = []

    def add(signal: str, reading: str, direction: int, why: str = "", weight: int = 1) -> None:
        rows.append({"signal": signal, "reading": reading, "direction": direction, "why": why, "weight": weight})

    # latest results
    res = next((r for r in c["results"] if r["source"] in ("pdf", "manual") and not r["needs_review"]), None)
    if res and res.get("net_profit") is not None:
        y = res.get("profit_yoy_pct")
        d = 0 if y is None else 1 if y >= 10 else -1 if y <= -10 else 0
        add("Latest results", f"quarter to {res['period_end']}: profit {res['net_profit'] / 1e6:,.0f}M" + (f", {y:+.0f}% YoY" if y is not None else ""),
            d, "growing profits support the price" if d > 0 else "falling profits weigh on it" if d < 0 else "", 2)
    else:
        add("Latest results", "no checked figures yet", 0)
    # valuation vs the market
    rr = fundamentals.get("ratios") or {}
    rel = fundamentals.get("relative") or {}
    if rr.get("pe"):
        pv = rel.get("pe_vs_market_pct")
        d = 0 if pv is None else 1 if pv <= -25 else -1 if pv >= 40 else 0
        add("Valuation", f"P/E {rr['pe']:.1f}" + (f" ({pv:+.0f}% vs market)" if pv is not None else "") + (f", P/BV {rr['pbv']:.2f}" if rr.get("pbv") else ""),
            d, "cheap against the market" if d > 0 else "expensive against the market" if d < 0 else "")
    # results timing
    due = c.get("results_due")
    if due:
        soon = due["days"] <= 7 or due["status"] == "overdue"
        add("Results timing", f"next expected {due['expected']} ({'overdue' if due['status'] == 'overdue' else str(due['days']) + ' days'})", 0,
            "event risk: a new entry before results can gap either way" if soon else "", 0)
    # dividend
    dv = c["dividends"]
    nxt = next((e for e in c["calendar"] if e["kind"] == "ex_dividend"), None)
    if nxt or dv.get("ttm_yield_pct"):
        y = dv.get("ttm_yield_pct")
        add("Dividend", (f"trailing yield {y:.1f}%" if y else "") + (f"; next XD {nxt['date']}" if nxt else ""),
            1 if (y or 0) >= 5 else 0, "a high cash yield supports the price" if (y or 0) >= 5 else "")
    # insiders
    i90 = c["insiders"]["d90"]
    if i90["notices"]:
        nv = i90["net_value"]
        d = 1 if nv > 0 else -1 if nv <= -1_000_000 else 0
        add("Director dealings (90 days)", f"{i90['buys']} buying, {i90['sells']} selling notices; net LKR {nv:+,.0f}", d,
            "directors are buying with their own money" if d > 0 else "directors are net sellers" if d < 0 else "")
    # announcement tone
    news = rec.get("news") or {}
    if news.get("score") is not None:
        sc = news["score"]
        add("Announcement tone", f"news score {sc:.0f}/100 ({news.get('detail', '')})", 1 if sc >= 62 else -1 if sc <= 38 else 0)
    # headlines (light)
    h = c["headlines"]
    if h["n30"]:
        t = h["tone30"] or 0
        add("Headlines (30 days)", f"{h['n30']} matched, average tone {t:+.2f}", 1 if t >= 0.3 else -1 if t <= -0.3 else 0,
            "tone of headlines about the company (Claude when switched on, else keywords); shown, not counted", 0)
    # reaction history
    rs = next((x for x in c["reaction_stats"] if x["kind"] == "results"), None)
    if rs:
        if rs["readable"] and rs.get("median_5d_excess_pct") is not None:
            m = rs["median_5d_excess_pct"]
            add("Reaction to results", f"median {m:+.1f}% vs ASPI over 5 sessions (n={rs['n']})", 1 if m >= 1 else -1 if m <= -1 else 0,
                "how this stock has traded after its own results")
        else:
            add("Reaction to results", rs.get("note") or "too few to read", 0)
    # corporate phase
    if c["corporate"].get("phase"):
        add("Corporate action", c["corporate"]["phase"].replace("_", " "), -1, "; ".join(c["corporate"].get("notes", [])[:1]))
    # red flags (from the engines' news reading)
    flags = (rec.get("original") or {}).get("red_flags") or []
    if flags:
        add("Red flags", "; ".join(f"{f['category'].replace('_', ' ')} {f['date']}" for f in flags[:3]), -1, "hard red flag in the last months", 3)

    total = sum(r["direction"] * r["weight"] for r in rows)
    overall = "Cautious" if flags or total <= -2 else "Supportive" if total >= 2 else "Neutral"
    reasons = [r for r in sorted(rows, key=lambda r: -abs(r["direction"] * r["weight"])) if r["direction"]][:3]
    return {"overall": overall, "total": total, "rows": rows,
            "reasons": [f"{r['signal']}: {r['reading']}" for r in reasons],
            "note": "Evidence beside the engines, not part of their scores. Supportive ≥ +2, Cautious ≤ −2 or any hard red flag."}


def compact(c: dict[str, Any]) -> dict[str, Any]:
    """What is stored with the nightly analysis (watchlist, Telegram, Compare view)."""
    return {"overall": c["scorecard"]["overall"], "total": c["scorecard"]["total"], "reasons": c["scorecard"]["reasons"],
            "risk_lines": c["risk_lines"], "next": c["calendar"][:3], "results_due": c.get("results_due"),
            "latest_results": next((r for r in c["results"] if r["source"] in ("pdf", "manual") and not r["needs_review"]), None)}
