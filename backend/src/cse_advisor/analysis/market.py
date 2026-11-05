"""Market regime from ASPI trend, breadth and market turnover (macro proxy until the
full macro module, which adds CBSL rates / T-bills / LKR, is built)."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .indicators import ema


def relative_strength(closes: pd.Series, aspi: pd.Series | None, sector: pd.Series | None = None,
                      spans: tuple[int, ...] = (20, 60, 120)) -> dict[str, Any] | None:
    """The stock's move minus the ASPI's (and its sector index's) over 20 / 60 / 120 sessions, in points.
    Leader: ahead over 60 sessions by 5+ points and still ahead over 20; laggard: the mirror image."""
    if closes is None or len(closes) < 25 or aspi is None or aspi.dropna().empty:
        return None
    a = aspi.reindex(closes.index).ffill()
    sec = sector.reindex(closes.index).ffill() if sector is not None and not sector.dropna().empty else None
    out: dict[str, Any] = {"vs_aspi": {}, "vs_sector": {}}
    for n in spans:
        if len(closes) <= n:
            continue
        stock = float(closes.iloc[-1] / closes.iloc[-1 - n] - 1) * 100
        if pd.notna(a.iloc[-1 - n]) and a.iloc[-1 - n]:
            out["vs_aspi"][str(n)] = round(stock - float(a.iloc[-1] / a.iloc[-1 - n] - 1) * 100, 1)
        if sec is not None and pd.notna(sec.iloc[-1 - n]) and sec.iloc[-1 - n]:
            out["vs_sector"][str(n)] = round(stock - float(sec.iloc[-1] / sec.iloc[-1 - n] - 1) * 100, 1)
    r20, r60 = out["vs_aspi"].get("20"), out["vs_aspi"].get("60")
    if r20 is None or r60 is None:
        out["label"] = "n/a"
    else:
        out["label"] = "leader" if r60 >= 5 and r20 > 0 else "laggard" if r60 <= -5 and r20 < 0 else "in line"
    out["text"] = (f"{out['label']}: " + ", ".join(f"{v:+.1f} pts vs ASPI over {k} sessions" for k, v in out["vs_aspi"].items())
                   + ("; vs sector " + ", ".join(f"{v:+.1f} ({k})" for k, v in out["vs_sector"].items()) if out["vs_sector"] else ""))
    return out


def market_regime(md: pd.DataFrame) -> dict[str, Any]:
    if md is None or md.empty or md["aspi"].dropna().shape[0] < 30:
        return {"score": None, "regime": "unknown", "detail": "not enough ASPI history yet", "factors": []}
    aspi = md["aspi"].dropna()
    e20, e50 = ema(aspi, 20), ema(aspi, 50)
    last = float(aspi.iloc[-1])
    factors = []
    score = 50.0
    if pd.notna(e50.iloc[-1]):
        above = last > e50.iloc[-1]
        rising = e50.iloc[-1] > e50.iloc[-6]
        score += (12 if above else -12) + (8 if rising else -8)
        factors.append(f"ASPI {last:,.0f} is {'above' if above else 'below'} its 50-day EMA ({e50.iloc[-1]:,.0f}), which is {'rising' if rising else 'falling'}")
    chg20 = last / float(aspi.iloc[-21]) - 1 if len(aspi) > 21 else 0
    score += float(np.clip(chg20 * 200, -15, 15))
    factors.append(f"ASPI {chg20 * 100:+.1f}% over 20 sessions")
    t = md["turnover"].dropna()
    if len(t) >= 30:
        r = t.tail(10).mean() / t.tail(60).mean()
        score += float(np.clip((r - 1) * 20, -10, 10))
        factors.append(f"market turnover (10d vs 60d avg) {r:.2f}×")
    b = md.dropna(subset=["advancers", "decliners"]).tail(10)
    if len(b) >= 3:
        adv, dec = b["advancers"].sum(), b["decliners"].sum()
        ratio = adv / max(dec, 1)
        score += float(np.clip((ratio - 1) * 10, -8, 8))
        factors.append(f"breadth: {adv} advancers vs {dec} decliners over the last {len(b)} sessions")
    score = float(np.clip(score, 0, 100))
    regime = "risk_on" if score >= 60 else "risk_off" if score <= 40 else "neutral"
    return {"score": round(score, 1), "regime": regime, "detail": "; ".join(factors), "factors": factors,
            "aspi": last, "aspi_chg20_pct": round(chg20 * 100, 2)}


def regime_banner(md: pd.DataFrame, breadth: float | None = None) -> dict[str, Any]:
    """A plain description of the market for the banner: ASPI against its 100- and 200-day averages and, when the SCR ranking
    exists, the share of liquid stocks above their own 50-day average. Display only: the 14-year study found that staying out
    of weak markets did not improve results, so nothing in the engines uses this."""
    if md is None or md.empty or md["aspi"].dropna().shape[0] < 200:
        return {"state": "unknown", "text": "Not enough ASPI history for a market read.", "facts": []}
    a = md["aspi"].dropna()
    last = float(a.iloc[-1])
    m100, m200 = float(a.rolling(100).mean().iloc[-1]), float(a.rolling(200).mean().iloc[-1])
    above200, above100 = last > m200, last > m100
    facts = [f"ASPI {last:,.0f} is {abs(last / m200 - 1) * 100:.1f}% {'above' if above200 else 'below'} its 200-day average",
             f"{abs(last / m100 - 1) * 100:.1f}% {'above' if above100 else 'below'} its 100-day average"]
    if breadth is not None:
        facts.append(f"{breadth * 100:.0f}% of liquid stocks are above their 50-day average")
    strong = above200 and (breadth is None or breadth >= 0.6)
    weak = (not above200) and (breadth is None or breadth <= 0.4)
    state = "risk_on" if strong else "risk_off" if weak else "mixed"
    label = {"risk_on": "Risk-on", "risk_off": "Risk-off", "mixed": "Mixed"}[state]
    return {"state": state, "label": label, "facts": facts, "aspi": last, "above200": above200, "above100": above100, "breadth": breadth,
            "text": f"{label}: " + "; ".join(facts) + ".",
            "note": "Description only. In the 14-year test, staying out of weak markets did not improve results, so no engine uses this."}
