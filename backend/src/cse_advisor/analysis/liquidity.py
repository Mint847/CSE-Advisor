"""Liquidity engine: tradability grade, money-flow (accumulation/distribution), and
liquidity regime (dormant -> awakening -> active -> frenzy -> drying up)."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .model import Pattern, round_price, tkey

GRADE_THRESHOLDS = [("A", 50e6), ("B", 10e6), ("C", 2e6), ("D", 0.5e6)]   # median daily turnover, LKR
GRADES = ["A", "B", "C", "D", "E"]


def _calendar_fill(df: pd.DataFrame, calendar: list[pd.Timestamp] | None) -> pd.DataFrame:
    """Reindex to the market's trading calendar so days with no trades count as zero volume."""
    if not calendar:
        return df
    cal = pd.DatetimeIndex(sorted(set(calendar))).normalize()
    cal = cal[(cal >= df.index[0]) & (cal <= df.index[-1])]
    full = df.reindex(df.index.union(cal))
    full["volume"] = full["volume"].fillna(0)
    full["turnover"] = full["turnover"].fillna(0)
    full["close"] = full["close"].ffill()
    full["traded"] = df["volume"].reindex(full.index).fillna(0) > 0
    return full


def corwin_schultz(df: pd.DataFrame) -> pd.Series:
    h, l = df["high"].astype(float), df["low"].astype(float)
    beta = np.log(h / l) ** 2 + np.log(h.shift(-1) / l.shift(-1)) ** 2
    gamma = np.log(np.maximum(h, h.shift(-1)) / np.minimum(l, l.shift(-1))) ** 2
    k = 3 - 2 * np.sqrt(2)
    alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / k - np.sqrt(gamma / k)
    s = 2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))
    return s.clip(lower=0)


def _regime_timeline(full: pd.DataFrame) -> pd.Series:
    t = full["turnover"].astype(float)
    t5, t20 = t.rolling(5, min_periods=3).mean(), t.rolling(20, min_periods=10).mean()
    prior60 = t.shift(20).rolling(60, min_periods=20).mean()
    base = t20.rolling(250, min_periods=60).median().fillna(t20.expanding(20).median())
    ret10 = full["close"].pct_change(10)
    states: list[str] = []
    prev = "normal"
    for i in range(len(full)):
        b, p60 = base.iloc[i], prior60.iloc[i]
        if not np.isfinite(b) or not np.isfinite(p60) or b <= 0:
            states.append("normal"); continue
        ref = max(p60, 1e-9)
        if t5.iloc[i] >= 4 * max(b, ref) and abs(ret10.iloc[i]) >= 0.2:
            s = "frenzy"
        elif t5.iloc[i] >= 2 * ref and (prev in ("dormant", "drying_up", "normal") and t20.shift(5).iloc[i] <= 1.1 * b):
            s = "awakening"
        elif prev in ("active", "frenzy", "awakening") and t5.iloc[i] < 0.6 * t20.iloc[i]:
            s = "drying_up"
        elif t20.iloc[i] >= 1.3 * b:
            s = "active"
        elif t20.iloc[i] < 0.6 * b:
            s = "dormant"
        elif prev == "awakening" and t5.iloc[i] >= ref:
            s = "active"
        else:
            s = "normal"
        states.append(s)
        prev = s
    return pd.Series(states, index=full.index)


REGIME_TEXT = {
    "dormant": "dormant: turnover well below its normal level, little interest",
    "awakening": "awakening: turnover has jumped off a quiet base, new money is arriving",
    "active": "active: sustained above-normal turnover",
    "frenzy": "frenzy: extreme turnover with a sharp price move, late-stage retail chasing risk",
    "drying_up": "drying up: turnover fading after an active phase, momentum may be exhausting",
    "normal": "normal: turnover around its usual level",
}


def analyze_liquidity(df: pd.DataFrame, calendar: list[pd.Timestamp] | None = None) -> dict[str, Any]:
    full = _calendar_fill(df, calendar)
    if "traded" not in full:
        full["traded"] = full["volume"] > 0
    last60 = full.tail(60)
    days_traded = float(last60["traded"].mean()) if len(last60) else 0.0
    med_turn = float(last60["turnover"].median()) if len(last60) else 0.0
    avg_turn20 = float(full["turnover"].tail(20).mean())
    adv_vol20 = float(full["volume"].tail(20).mean())
    traded = df.tail(60)
    ret = traded["close"].pct_change().abs()
    amihud = float((ret / traded["turnover"].replace(0, np.nan)).mean() * 1e6) if len(traded) > 5 else None
    spread = float(corwin_schultz(df.tail(61)).mean()) if len(df) > 5 else None

    grade_i = next((i for i, (_, th) in enumerate(GRADE_THRESHOLDS) if med_turn >= th), 4)
    notes = [f"median daily turnover LKR {med_turn / 1e6:.2f}M over the last 60 sessions"]
    if days_traded < 0.8:
        grade_i = min(4, grade_i + 1)
        notes.append(f"traded on only {days_traded * 100:.0f}% of sessions (downgraded)")
    if spread is not None and spread > 0.03:
        grade_i = min(4, grade_i + 1)
        notes.append(f"estimated bid-ask spread {spread * 100:.1f}% (downgraded)")
    grade = GRADES[grade_i]

    # ---- money flow
    d20 = df.tail(20)
    up_vol = float(d20["volume"][d20["close"].diff() > 0].sum())
    dn_vol = float(d20["volume"][d20["close"].diff() < 0].sum())
    ud_ratio = up_vol / dn_vol if dn_vol > 0 else (3.0 if up_vol > 0 else 1.0)
    vol_mean = float(d20["volume"].mean()) or 1.0

    def _slope(s: pd.Series) -> float:
        y = s.tail(20).to_numpy(float)
        return float(np.polyfit(np.arange(len(y)), y, 1)[0]) if len(y) >= 5 else 0.0

    obv_slope = _slope(df["obv"]) / vol_mean if "obv" in df else 0.0
    ad_slope = _slope(df["ad"]) / vol_mean if "ad" in df else 0.0
    t5 = float(full["turnover"].tail(5).mean())
    t60 = float(full["turnover"].tail(60).mean()) or 1.0
    chg5 = float(df["close"].iloc[-1] / df["close"].iloc[-6] - 1) if len(df) > 6 else 0.0
    chg20 = float(df["close"].iloc[-1] / df["close"].iloc[-21] - 1) if len(df) > 21 else 0.0
    trade_size = None
    if "trades" in df and df["trades"].notna().sum() >= 10:
        ts = (df["turnover"] / df["trades"].replace(0, np.nan)).dropna()
        trade_size = {"last5": float(ts.tail(5).mean()), "last20": float(ts.tail(20).mean())}

    acc = 50.0
    acc += float(np.clip((ud_ratio - 1) * 15, -20, 20))
    acc += 10 * np.sign(obv_slope) if abs(obv_slope) > 0.02 else 0
    acc += 5 * np.sign(ad_slope) if abs(ad_slope) > 0.02 else 0
    acc += float(np.clip((t5 / max(avg_turn20, 1) - 1) * 10, -10, 10)) * (1 if chg5 >= 0 else -1)
    if trade_size and trade_size["last20"] > 0:
        acc += float(np.clip((trade_size["last5"] / trade_size["last20"] - 1) * 10, -5, 5)) * (1 if chg5 >= 0 else -1)
    acc = float(np.clip(acc, 0, 100))
    flow_label = "accumulation" if acc >= 62 else "distribution" if acc <= 38 else "neutral"

    # ---- regime timeline
    states = _regime_timeline(full)
    segs: list[dict[str, Any]] = []
    for dt, s in states.items():
        if segs and segs[-1]["state"] == s:
            segs[-1]["end"] = tkey(dt)
        else:
            segs.append({"state": s, "start": tkey(dt), "end": tkey(dt)})
    regime = states.iloc[-1] if len(states) else "normal"

    # ---- awakening markers as patterns
    awak: list[Pattern] = []
    idx_map = {d: i for i, d in enumerate(df.index)}
    for seg in segs:
        if seg["state"] != "awakening":
            continue
        ts = pd.Timestamp(seg["start"])
        i = idx_map.get(ts)
        if i is None or i < 5:
            continue
        up = df["close"].iloc[i] >= df["close"].iloc[i - 5]
        awak.append(Pattern(
            type="volume_awakening", direction="bullish" if up else "bearish", status="confirmed",
            start=seg["start"], end=seg["end"], end_index=i,
            markers=[{"time": seg["start"], "position": "belowBar" if up else "aboveBar", "shape": "square", "text": "Vol↑"}],
            levels={"level": round_price(float(df["close"].iloc[i]))}, confidence=0.55,
            context={"volume_confirmed": True},
            explanation=(f"Liquidity awakening on {seg['start']}: 5-day turnover jumped to at least 2× its prior 60-day base "
                         f"after a quiet period, with price {'rising' if up else 'falling'}. On the CSE this is often the first sign "
                         f"of {'accumulation before a retail-driven move' if up else 'distribution / exit by holders'}."),
        ))

    return {
        "grade": grade, "grade_notes": notes,
        "median_turnover_60": med_turn, "avg_turnover_20": avg_turn20, "adv_volume_20": adv_vol20,
        "days_traded_pct": round(days_traded * 100, 1),
        "amihud": None if amihud is None or not np.isfinite(amihud) else round(amihud, 4),
        "spread_est_pct": None if spread is None or not np.isfinite(spread) else round(spread * 100, 2),
        "flow": {
            "score": round(acc, 1), "label": flow_label, "up_down_volume_ratio": round(ud_ratio, 2),
            "obv_slope_norm": round(obv_slope, 3), "ad_slope_norm": round(ad_slope, 3),
            "turnover_5_vs_20": round(t5 / max(avg_turn20, 1), 2), "turnover_20_vs_60": round(avg_turn20 / t60, 2),
            "price_change_5d_pct": round(chg5 * 100, 2), "price_change_20d_pct": round(chg20 * 100, 2),
            "avg_trade_size": trade_size,
        },
        "regime": regime, "regime_text": REGIME_TEXT.get(regime, regime),
        "timeline": segs[-30:],
        "turnover_series": [{"time": tkey(d), "value": float(v)} for d, v in full["turnover"].tail(250).items()],
        "_patterns": awak,
    }


def days_to_exit(qty: float, adv_volume: float, participation: float) -> float | None:
    if adv_volume <= 0:
        return None
    return qty / (participation * adv_volume)
