"""Runs the full analysis pipeline for one stock and stores the result."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from .analysis import indicators
from .config import settings
from .analysis.candlesticks import detect_candlesticks
from .analysis.chart_patterns import detect_chart_patterns
from .analysis.fundamentals import summarize
from .analysis.confluence import candidates, cluster, zones_for_plan
from .analysis.corporate_actions import adjust_prices, phases
from .analysis.fibonacci import fib_pattern, fibonacci, robust_extremes
from .analysis.levels import anchored_profile, liquidity_pools, sr_zones, volume_profile
from .analysis.signals import compute_signals
from .analysis.smc import analyze_smc
from .analysis.structure import ma_events, swing_labels, trendlines
from .analysis.liquidity import analyze_liquidity
from .analysis.market import market_regime
from .analysis.model import score_patterns, tkey
from .analysis.news import news_score
from .analysis.pivots import zigzag
from .analysis.trend import trend_state
from .db import Analysis, Announcement, CorporateAction, Fundamental, MarketDaily, Position, PriceDaily, Security
from .strategy.analyst import recommend_analyst
from .strategy.recommend import agreement, recommend_original
from .strategy.smc import _cfg as smc_cfg, recommend_smc

log = logging.getLogger(__name__)
MIN_BARS = 40


def load_prices(s: Session, symbol: str) -> pd.DataFrame:
    rows = s.scalars(select(PriceDaily).where(PriceDaily.symbol == symbol).order_by(PriceDaily.date)).all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([{
        "date": pd.Timestamp(r.date), "open": r.open, "high": r.high, "low": r.low, "close": r.close,
        "volume": r.volume or 0.0, "turnover": r.turnover, "trades": r.trades, "open_estimated": r.open_estimated,
        "source": r.source, "tv_close": r.tv_close,
    } for r in rows]).set_index("date")
    return df


def load_actions(s: Session, symbol: str) -> list[dict[str, Any]]:
    return [_row(a) for a in s.scalars(select(CorporateAction).where(CorporateAction.symbol == symbol))]


def load_adjusted(s: Session, symbol: str) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Prices back-adjusted for rights / bonus / split (what every engine and the chart use)."""
    raw = load_prices(s, symbol)
    if raw.empty:
        return raw, []
    adj, actions = adjust_prices(raw, load_actions(s, symbol))
    return stitch_deep(adj), actions


def stitch_deep(adj: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """Join TradingView's older, already-adjusted bars (source 'tv_adj') onto the app's adjusted series.

    TradingView and the app adjust for the same corporate actions but compute the factors a little
    differently (e.g. KZOO's 2026 rights: TERP-based 0.5588 here), so the older bars are scaled by
    k = median(app adjusted close / TradingView close) over the first `window` days both sources have.
    k is ~1.0 when nothing was adjusted. Older bars have no raw (as-traded) prices, so their raw_* columns
    equal the stitched values and `deep` marks them."""
    adj = adj.copy()
    deep = (adj["source"] == "tv_adj") if "source" in adj else pd.Series(False, index=adj.index)
    adj["deep"] = deep
    if not deep.any():
        return adj
    ref = adj["tv_close"] if "tv_close" in adj else pd.Series(np.nan, index=adj.index)
    both = (~deep) & ref.notna() & (ref > 0)
    ratios = (adj.loc[both, "close"] / ref[both]).head(window)
    k = float(ratios.median()) if len(ratios) else 1.0
    for c in ("open", "high", "low", "close"):
        adj.loc[deep, c] = adj.loc[deep, c] * k
        adj.loc[deep, f"raw_{c}"] = adj.loc[deep, c]
    adj.loc[deep, "volume"] = adj.loc[deep, "volume"] / k
    adj.loc[deep, "raw_volume"] = adj.loc[deep, "volume"]
    adj.attrs["stitch_k"] = k
    return adj


def market_valuation(s: Session | None) -> dict[str, Any] | None:
    """The latest market P/E and P/BV recorded from CSE's daily market summary."""
    if s is None:
        return None
    m = s.scalar(select(MarketDaily).where(MarketDaily.per.is_not(None)).order_by(MarketDaily.date.desc()))
    return {"per": m.per, "pbv": m.pbv, "date": str(m.date)} if m else None


def history_window(adj: pd.DataFrame) -> pd.DataFrame:
    """The most recent `history.analysis_bars` daily bars (default 2000, about 8 years) for the engines."""
    n = int((settings().get("history") or {}).get("analysis_bars", 2000))
    return adj.iloc[-n:] if n and len(adj) > n else adj


def load_market(s: Session) -> pd.DataFrame:
    rows = s.scalars(select(MarketDaily).order_by(MarketDaily.date)).all()
    return pd.DataFrame([{"date": pd.Timestamp(r.date), "aspi": r.aspi, "turnover": r.turnover,
                          "advancers": r.advancers, "decliners": r.decliners} for r in rows]).set_index("date") if rows else pd.DataFrame()


def _row(o: Any) -> dict[str, Any]:
    return {c.name: getattr(o, c.name) for c in o.__table__.columns}


def analyze_symbol(s: Session, symbol: str, save: bool = True, intraday_vap: dict[str, Any] | None = None,
                   asof: Any = None) -> dict[str, Any] | None:
    sec = s.get(Security, symbol)
    adj, actions = load_adjusted(s, symbol)
    if asof is not None:   # replay: only data up to and including `asof`
        adj = adj[adj.index <= pd.Timestamp(asof)]
    adj = history_window(adj)
    if sec is None or len(adj) < MIN_BARS:
        log.warning("%s: not enough history (%d bars)", symbol, len(adj))
        return None
    return analyze_frame(s, sec, adj, actions, save=save and asof is None, intraday_vap=intraday_vap)


def analyze_frame(s: Session | None, sec: Any, adj: pd.DataFrame, actions: list[dict[str, Any]], save: bool = False,
                  intraday_vap: dict[str, Any] | None = None, fund_rows: list[dict[str, Any]] | None = None,
                  anns: list[dict[str, Any]] | None = None, md: pd.DataFrame | None = None,
                  position: dict[str, Any] | None = None, tf: str = "1D", shared: dict[str, Any] | None = None,
                  htf_df: pd.DataFrame | None = None) -> dict[str, Any]:
    """Full pipeline on an (adjusted) price frame. Used by the EOD job, replay tests and (with tf != '1D')
    the on-demand intraday analysis. `shared` carries the stock-level inputs an intraday run takes from
    the latest daily analysis instead of recomputing them on intraday bars: liquidity, fundamentals and
    (optionally) the market regime."""
    symbol = sec.symbol
    intraday = tf != "1D"
    if intraday:
        save, intraday_vap, position = False, None, None
    # Original engine: works on the adjusted bars exactly as built originally (real wicks).
    df_u = indicators.add_all(adj)
    # Analyst engine: freak single-bar wicks (e.g. an XR-day spike that hit the price breakers) are
    # capped so they don't become swing points, Fibonacci anchors or "the peak".
    df = df_u.copy()
    df["high"], df["low"] = robust_extremes(df)
    if md is None:
        md = load_market(s) if s is not None else pd.DataFrame()
    atr_now = float(df_u["atr"].iloc[-1])
    close = float(df_u["close"].iloc[-1])
    calendar = list(md.index) if not md.empty else None

    # ---- shared / Original-engine inputs
    piv_o = zigzag(df_u, df_u["atr"], k=1.5)
    trend = trend_state(df_u, piv_o)
    profile = volume_profile(df_u, lookback=min(120, len(df_u)))
    zones_o = sr_zones(df_u, piv_o, atr_now, profile, max_zones=6)
    pools = liquidity_pools(df_u, piv_o)
    if intraday:   # liquidity describes the stock (daily turnover), not the bar size
        liq = {**(shared or {}).get("liquidity", {}), "_patterns": []}
        liq.setdefault("grade", "C")
    else:
        liq = analyze_liquidity(df_u, calendar)

    # ---- Analyst-engine inputs
    pivots = zigzag(df, df["atr"], k=1.5)
    trend_a = trend_state(df, pivots)
    zones = sr_zones(df, pivots, atr_now, profile)
    pools_a = liquidity_pools(df, pivots)
    fib = fibonacci(df)
    anchored = anchored_profile(df, fib["start_index"]) if fib else anchored_profile(df, max(0, len(df) - 120))
    tls = trendlines(df, pivots)
    swing = swing_labels(df, pivots)
    ma = ma_events(df)
    sig = compute_signals(df_u, intraday_vap)       # volatility checks need the real wicks
    # SMC engine: the capped frame, so a freak XR-day wick is not read as a liquidity sweep
    if intraday:
        from .intraday import HTF, LABEL
        smc = analyze_smc(df, k=int(smc_cfg()["swing_len"]), htf_df=htf_df, ltf_label=LABEL[tf], htf_label=LABEL[HTF[tf]],
                          htf_short=LABEL[HTF[tf]].upper() if HTF[tf] != "1D" else "D")
    else:
        smc = analyze_smc(df, k=int(smc_cfg()["swing_len"]))

    raw_patterns = detect_chart_patterns(df_u, piv_o) + detect_candlesticks(df_u) + liq.pop("_patterns") + tls + smc["patterns"]
    if fib:
        raw_patterns.append(fib_pattern(df, fib))
    if swing:
        raw_patterns.append(swing)
    key_levels = [z["price"] for z in zones_o] + [p["price"] for p in pools if p["source"] == "swing"]
    est_dates = {tkey(d) for d in df.index[df["open_estimated"].fillna(False).astype(bool)]}
    pats = score_patterns(raw_patterns, len(df), trend["direction"], key_levels, atr_now, liq["grade"], est_dates,
                          track_record=not intraday)
    patterns = [p.to_dict() for p in pats]
    if intraday:   # catalog names say "Weekly ..." for the SMC higher-timeframe blocks
        hl = smc.get("labels", {}).get("htf", "weekly")
        for p in patterns:
            if p["type"].startswith("weekly_"):
                p["name"] = p["name"].replace("Weekly", hl[:1].upper() + hl[1:])
    tl_dicts = [p for p in patterns if p["type"] in ("trendline_resistance", "trendline_support")]

    clusters = cluster(candidates(close, atr_now, zones, fib, anchored, profile, ma, tl_dicts), close, atr_now)
    conf = zones_for_plan(clusters, close, atr_now)
    corp = phases(actions, df.index[-1].date(), close)
    corp["actions"] = [{k: a.get(k) for k in ("id", "kind", "ex_date", "ratio_new", "ratio_old", "price", "listing_date",
                                               "trading_date", "factor", "terp", "cum_price", "applied", "reason", "manual", "ignore")}
                       for a in actions]

    if intraday and shared and shared.get("fundamentals") is not None:
        fund_rows = []
    elif fund_rows is None:
        # auto-extracted rows that failed their cross-checks are shown for review but not used
        fund_rows = [_row(r) for r in s.scalars(select(Fundamental).where(Fundamental.symbol == symbol, Fundamental.needs_review.is_not(True)))]             if s is not None else []
    if intraday and shared and shared.get("fundamentals") is not None:
        fundamentals = shared["fundamentals"]
    else:
        fundamentals = summarize(fund_rows, close, sec.info, sec.shares_issued, market_valuation(s))
    if anns is None:
        anns = [{"id": a.id, "title": a.title, "date": a.date, "category": a.category, "sentiment": a.sentiment,
                 "materiality": a.materiality, "event": a.event} for a in s.scalars(select(Announcement).where(Announcement.symbol == symbol))] if s is not None else []
        # matched business headlines count lightly (materiality 1, half their keyword tone) and never flag
        if s is not None:
            from .db import Headline
            anns = anns + [{"id": h.id, "title": h.title, "date": h.published, "category": "headline", "sentiment": (h.tone or 0) * 0.5,
                            "materiality": 1} for h in s.scalars(select(Headline).where(Headline.published.is_not(None)))
                           if symbol in (h.symbols or [])]
    asof_dt = datetime.combine(df.index[-1].date(), datetime.min.time())
    recent_anns = [a for a in anns if a.get("date") and a["date"] <= asof_dt + pd.Timedelta(days=1)]
    news = news_score(recent_anns, asof_dt)
    news_o = news_score(recent_anns, asof_dt, legacy=True)
    if intraday and shared and shared.get("market"):
        market = shared["market"]
    else:
        market = market_regime(md[md.index <= df.index[-1]] if not md.empty else md)

    prev_o = prev_a = prev_s = None
    if s is not None and position is None and not intraday:
        pos = s.scalar(select(Position).where(Position.symbol == symbol, Position.closed.is_(False)).order_by(Position.entry_date.desc()))
        position = _row(pos) if pos else None
        prev = s.scalar(select(Analysis).where(Analysis.symbol == symbol, Analysis.date < df.index[-1].date()).order_by(Analysis.date.desc()))
        if prev:
            prev_o = {"verdict": prev.verdict, "score": prev.score, "pattern_ids": [p["id"] for p in prev.patterns]}
            prev_a = {"verdict": prev.verdict_analyst, "score": prev.score_analyst}
            prev_s = {"verdict": prev.verdict_smc, "score": prev.score_smc}

    ctx = {"fib": fib, "confluence": conf, "signals": sig, "trendlines": tl_dicts, "ma": ma, "corporate": corp, "zones": zones,
           "anchored_profile": anchored}
    rec_o = recommend_original(symbol, sec.name, df_u, trend, patterns, zones_o, profile, pools, liq, fundamentals, news_o,
                               market, position, prev_o)
    rec_a = recommend_analyst(symbol, sec.name, df, trend_a, patterns, zones, profile, pools_a, liq, fundamentals, news,
                              position, prev_a, ctx)
    if intraday:
        rec_o, rec_a = bar_words(rec_o), bar_words(rec_a)
    rec_s = recommend_smc(symbol, sec.name, df, smc, patterns, liq, news, position, prev_s)
    rec = {"original": rec_o, "analyst": rec_a, "smc": rec_s,
           "agreement": agreement(rec_o["verdict"], rec_a["verdict"], rec_s["verdict"]),
           "close": close, "date": str(df.index[-1].date()), "tf": tf, "time": tkey(df.index[-1]),
           "news": {k: v for k, v in news.items() if k != "items"}, "market": market}
    # relative strength vs the ASPI and (if set in SET) the stock's sector index: shown only
    if not intraday and "aspi" in md:
        sector = None
        if s is not None and getattr(sec, "sector", None):
            from .db import SectorDaily
            rows = s.execute(select(SectorDaily.date, SectorDaily.value).where(SectorDaily.code == sec.sector)).all()
            if len(rows) > 20:
                sector = pd.Series({pd.Timestamp(d): v for d, v in rows}).sort_index()
        from .analysis.market import relative_strength
        rec["relative_strength"] = relative_strength(adj["close"], md["aspi"], sector)
    # catalysts (news, results, events) as evidence beside the engines; event-risk lines go into each
    # engine's "watch next" list as text only (they change no score or verdict)
    if s is not None and not intraday and (datetime.now().date() - df.index[-1].date()).days <= 7:
        try:
            from . import catalysts
            cat = catalysts.build(s, symbol, adj["close"], md["aspi"] if "aspi" in md else None, datetime.now().date(),
                                  {"recommendation": rec, "fundamentals": fundamentals})
            rec["catalysts"] = catalysts.compact(cat)
            for r in (rec_o, rec_a, rec_s):
                wn = r.setdefault("explanation", {}).setdefault("watch_next", [])
                r["explanation"]["watch_next"] = list(cat["risk_lines"]) + [x for x in wn if x not in cat["risk_lines"]]
        except Exception as exc:
            log.warning("catalysts for %s failed: %s", symbol, exc)

    levels = {"zones": zones, "zones_original": zones_o, "volume_profile": profile, "pools": pools, "fib": fib,
              "anchored_profile": anchored,
              "confluence": {"zone1": conf["zone1"], "zone2": conf["zone2"], "sell_zones": conf["sell_zones"]},
              "ma": ma, "events": corp.get("events", []), "smc": rec_s["smc"],
              "pivots": [{"time": tkey(df.index[p.idx]), "price": round(p.price, 2), "kind": p.kind, "confirmed": p.confirmed} for p in pivots[-40:]]}
    trend_out = {**trend, "atr": round(atr_now, 3), "rsi": round(float(df["rsi"].iloc[-1]), 1) if pd.notna(df["rsi"].iloc[-1]) else None}
    result = {"symbol": symbol, "date": df.index[-1].date(), "tf": tf, "time": tkey(df.index[-1]),
              "verdict": rec_o["verdict"], "score": rec_o["score"],
              "verdict_analyst": rec_a["verdict"], "score_analyst": rec_a["score"],
              "verdict_smc": rec_s["verdict"], "score_smc": rec_s["score"],
              "patterns": patterns, "levels": levels, "liquidity": liq, "trend": trend_out,
              "fundamentals": fundamentals, "recommendation": rec}
    result = _jsonable(result)
    if save and s is not None:
        row = s.scalar(select(Analysis).where(Analysis.symbol == symbol, Analysis.date == df.index[-1].date())) \
            or Analysis(symbol=symbol, date=df.index[-1].date())
        row.verdict, row.score = rec_o["verdict"], rec_o["score"]
        row.verdict_analyst, row.score_analyst = rec_a["verdict"], rec_a["score"]
        row.verdict_smc, row.score_smc = rec_s["verdict"], rec_s["score"]
        row.patterns, row.levels, row.liquidity = result["patterns"], result["levels"], result["liquidity"]
        row.trend, row.fundamentals, row.recommendation = result["trend"], result["fundamentals"], result["recommendation"]
        row.created_at = datetime.utcnow()
        s.add(row)
    return result


def bar_words(obj: Any) -> Any:
    """Intraday wording for the Original/Analyst text: 'session(s)' -> 'bar(s)', '20-day' -> '20-bar'."""
    import re
    if isinstance(obj, dict):
        return {k: (v if k in ("kind", "engine", "verdict", "pattern_id", "type", "id") else bar_words(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [bar_words(v) for v in obj]
    if isinstance(obj, str):
        t = re.sub(r"\bsessions\b", "bars", obj)
        t = re.sub(r"\bsession\b", "bar", t)
        t = re.sub(r"\b(\d+)-day\b", r"\1-bar", t)
        t = re.sub(r"\b(up|down)-day\b", r"\1-bar", t)
        t = re.sub(r"\b(up|down) days\b", r"\1 bars", t)
        return t
    return obj


def _shared_daily(s: Session, symbol: str) -> dict[str, Any] | None:
    a = s.scalar(select(Analysis).where(Analysis.symbol == symbol).order_by(Analysis.date.desc()))
    if a is None:
        return None
    return {"liquidity": a.liquidity or {}, "fundamentals": a.fundamentals, "date": str(a.date),
            "verdicts": {"original": a.verdict, "analyst": a.verdict_analyst, "smc": a.verdict_smc}}


def _intraday_market(s: Session, tf: str) -> dict[str, Any] | None:
    """Market regime on ASPI bars of the chart's own timeframe (None if there are too few)."""
    from .intraday import ASPI, LABEL, completed, load_intraday
    aspi, _ = load_intraday(s, ASPI, tf, daily=None, adjusted=False, refresh=False)
    aspi = completed(aspi, tf)
    if len(aspi) < 40:
        return None
    m = market_regime(pd.DataFrame({"aspi": aspi["close"], "turnover": np.nan, "advancers": np.nan, "decliners": np.nan},
                                   index=aspi.index))
    m = bar_words(m)
    m["detail"] = f"{LABEL[tf]} ASPI: " + m.get("detail", "")
    m["timeframe"] = tf
    return m


def analyze_intraday(s: Session, symbol: str, tf: str) -> dict[str, Any]:
    """All three engines on intraday bars, computed on demand (never stored). Liquidity, fundamentals and
    news describe the stock, so they come from the latest daily analysis."""
    import time as _time
    from .intraday import HTF, cache_get, cache_put, cfg, completed, load_intraday
    t0 = _time.perf_counter()
    sec = s.get(Security, symbol)
    adj, actions = load_adjusted(s, symbol)
    if sec is None or adj.empty:
        return {"symbol": symbol, "tf": tf, "error": "unknown symbol or no daily history"}
    full, cov = load_intraday(s, symbol, tf, daily=adj)
    df = completed(full, tf).tail(int(cfg()["analysis_bars"]))
    if len(df) < MIN_BARS:
        return {"symbol": symbol, "tf": tf, "coverage": cov,
                "error": f"not enough {tf} history yet ({len(df)} bars; the engines need {MIN_BARS})"}
    last = df.index[-1]
    hit = cache_get(symbol, tf, last)
    if hit is not None:
        return hit
    shared = _shared_daily(s, symbol)
    if shared is None:
        analyze_symbol(s, symbol)
        s.flush()
        shared = _shared_daily(s, symbol) or {}
    market = _intraday_market(s, tf)
    if market:
        shared = {**shared, "market": market}
    h = HTF[tf]
    if h == "1D":
        htf_df = adj[["open", "high", "low", "close"]]
    else:
        htf_df, _ = load_intraday(s, symbol, h, daily=adj, refresh=False)
        htf_df = completed(htf_df, h)
    res = analyze_frame(s, sec, df.drop(columns=["source"], errors="ignore"), actions, save=False, tf=tf, shared=shared, htf_df=htf_df)
    res["coverage"] = cov
    res["daily"] = {"date": shared.get("date"), "verdicts": shared.get("verdicts")}
    res["elapsed_ms"] = round((_time.perf_counter() - t0) * 1000)
    cache_put(symbol, tf, last, res)
    return res


def _jsonable(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.floating,)):
        v = float(o)
        return None if not np.isfinite(v) else v
    if isinstance(o, float) and not np.isfinite(o):
        return None
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return o


def chart_data(s: Session, symbol: str, timeframe: str = "1D", adjusted: bool = True) -> dict[str, Any]:
    adj, actions = load_adjusted(s, symbol)
    if adj.empty:
        return {"bars": [], "indicators": {}}
    if not adjusted and "deep" in adj:      # TradingView's older bars exist only adjusted: the raw view starts at cse.lk's year
        adj = adj[~adj["deep"].astype(bool)]
    raw = adj if adjusted else adj.assign(**{k: adj[f"raw_{k}"] for k in ("open", "high", "low", "close")}, volume=adj["raw_volume"])
    events = [{"time": str(a["ex_date"]), "kind": a["kind"], "applied": a.get("applied"), "factor": a.get("factor"),
               "text": {"rights": "XR", "bonus": "Bonus", "split": "Split"}.get(a["kind"], a["kind"])}
              for a in actions if a.get("ex_date") and not a.get("ignore")]
    coverage = None
    if timeframe != "1D" and timeframe != "1W":
        from .intraday import load_intraday
        raw, coverage = load_intraday(s, symbol, timeframe, daily=adj, adjusted=adjusted)
        if raw.empty:
            return {"symbol": symbol, "timeframe": timeframe, "bars": [], "indicators": {}, "events": [], "coverage": coverage,
                    "adjusted": adjusted}
        first = raw.index[0].normalize()
        events = [{**e, "time": tkey(raw.index[raw.index.searchsorted(pd.Timestamp(e["time"]))])}
                  for e in events if pd.Timestamp(e["time"]) >= first and raw.index.searchsorted(pd.Timestamp(e["time"])) < len(raw)]
    elif timeframe == "1W":
        g = raw.resample("W-FRI")
        wk = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                           "close": g["close"].last(), "volume": g["volume"].sum(), "turnover": g["turnover"].sum(min_count=1)}).dropna(subset=["close"])
        # label each weekly bar with its first trading day so daily-dated drawings snap into it
        first_day = raw.index.to_series().resample("W-FRI").first().dropna()
        wk.index = pd.DatetimeIndex(first_day.loc[wk.index].values)
        wk["open_estimated"] = False
        raw = wk
    df = indicators.add_all(raw)
    t = [tkey(d) for d in df.index]

    def series(col: str, nd: int = 3) -> list[dict[str, Any]]:
        return [{"time": ti, "value": round(float(v), nd)} for ti, v in zip(t, df[col]) if pd.notna(v)]

    bars = [{"time": ti, "open": round(float(r.open), 2), "high": round(float(r.high), 2), "low": round(float(r.low), 2),
             "close": round(float(r.close), 2), "volume": float(r.volume), "est": bool(r.open_estimated)}
            for ti, r in zip(t, df.itertuples())]
    return {
        "symbol": symbol, "timeframe": timeframe, "bars": bars, "adjusted": adjusted, "events": events, "coverage": coverage,
        "indicators": {
            "ema20": series("ema20"), "ema21": series("ema21"), "ema50": series("ema50"), "ema200": series("ema200"),
            "sma200": series("sma200"), "rsi_ma": series("rsi_ma", 1),
            "bb_up": series("bb_up"), "bb_mid": series("bb_mid"), "bb_lo": series("bb_lo"),
            "rsi": series("rsi", 1), "macd": series("macd", 4), "macd_signal": series("macd_signal", 4),
            "macd_hist": series("macd_hist", 4),
        },
    }
