"""Candlestick pattern detection (single and multi-bar)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .model import Pattern, round_price, tkey

BULL = {"bullish_engulfing", "morning_star", "three_white_soldiers", "piercing_line", "bullish_harami",
        "hammer", "inverted_hammer", "dragonfly_doji", "bullish_marubozu"}
BEAR = {"bearish_engulfing", "evening_star", "three_black_crows", "dark_cloud_cover", "bearish_harami",
        "hanging_man", "shooting_star", "gravestone_doji", "bearish_marubozu"}
LABELS = {
    "bullish_engulfing": "Bull Engulf", "bearish_engulfing": "Bear Engulf", "morning_star": "Morning Star",
    "evening_star": "Evening Star", "three_white_soldiers": "3 Soldiers", "three_black_crows": "3 Crows",
    "piercing_line": "Piercing", "dark_cloud_cover": "Dark Cloud", "bullish_harami": "Bull Harami",
    "bearish_harami": "Bear Harami", "hammer": "Hammer", "hanging_man": "Hanging Man",
    "shooting_star": "Shooting Star", "inverted_hammer": "Inv Hammer", "doji": "Doji",
    "dragonfly_doji": "Dragonfly", "gravestone_doji": "Gravestone", "bullish_marubozu": "Marubozu ▲",
    "bearish_marubozu": "Marubozu ▼",
}
DESCRIPTIONS = {
    "bullish_engulfing": "a bullish body fully engulfed the prior bearish body after a decline, meaning buyers overwhelmed sellers",
    "bearish_engulfing": "a bearish body fully engulfed the prior bullish body after an advance, meaning sellers took control",
    "morning_star": "a large down candle, a small indecision candle, then a strong up candle closing above the first candle's midpoint, a three-bar bottom reversal",
    "evening_star": "a large up candle, a small indecision candle, then a strong down candle closing below the first candle's midpoint, a three-bar top reversal",
    "three_white_soldiers": "three consecutive strong up candles closing near their highs, showing sustained buying",
    "three_black_crows": "three consecutive strong down candles closing near their lows, showing sustained selling",
    "piercing_line": "after a strong down candle, the next candle closed above the midpoint of its body, so buyers absorbed the selling",
    "dark_cloud_cover": "after a strong up candle, the next candle closed below the midpoint of its body, so sellers absorbed the buying",
    "bullish_harami": "a small up body inside the prior large down body, so selling momentum stalled",
    "bearish_harami": "a small down body inside the prior large up body, so buying momentum stalled",
    "hammer": "a long lower shadow after a decline: sellers pushed price down but buyers drove it back up",
    "hanging_man": "a long lower shadow after an advance, an early warning that selling pressure is appearing",
    "shooting_star": "a long upper shadow after an advance: buyers pushed up but sellers rejected the highs",
    "inverted_hammer": "a long upper shadow after a decline, an early sign buyers are testing higher prices",
    "doji": "open and close almost equal, signalling indecision",
    "dragonfly_doji": "a doji with a long lower shadow: lows were rejected",
    "gravestone_doji": "a doji with a long upper shadow: highs were rejected",
    "bullish_marubozu": "a full-bodied up candle with almost no shadows, showing strong buying conviction",
    "bearish_marubozu": "a full-bodied down candle with almost no shadows, showing strong selling conviction",
}


def detect_candlesticks(df: pd.DataFrame, lookback: int = 150) -> list[Pattern]:
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    rel_vol = df["rel_vol"].to_numpy(float) if "rel_vol" in df else np.full(len(df), np.nan)
    body = np.abs(c - o)
    rng = np.maximum(h - l, 1e-9)
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    avg_body = pd.Series(body).rolling(20, min_periods=5).mean().to_numpy()
    bull, bear = c > o, c < o
    dates = [tkey(d) for d in df.index]
    n = len(df)
    out: list[Pattern] = []

    def prior_trend(i: int) -> int:
        if i < 6:
            return 0
        chg = (c[i - 1] - c[i - 6]) / c[i - 6]
        return 1 if chg > 0.02 else -1 if chg < -0.02 else 0

    def add(ptype: str, i: int, nbars: int, conf: float) -> None:
        first = i - nbars + 1
        direction = "bullish" if ptype in BULL else "bearish" if ptype in BEAR else "neutral"
        lo_, hi_ = float(l[first:i + 1].min()), float(h[first:i + 1].max())
        marker = {
            "time": dates[i], "text": LABELS[ptype],
            "position": "belowBar" if direction == "bullish" else "aboveBar" if direction == "bearish" else "aboveBar",
            "shape": "arrowUp" if direction == "bullish" else "arrowDown" if direction == "bearish" else "circle",
        }
        levels = {"level": round_price(c[i])}
        if direction == "bullish":
            levels["invalidation"] = round_price(lo_)
        elif direction == "bearish":
            levels["invalidation"] = round_price(hi_)
        vc = bool(np.isfinite(rel_vol[i]) and rel_vol[i] >= 1.3)
        out.append(Pattern(
            type=ptype, direction=direction, status="confirmed", start=dates[first], end=dates[i], end_index=i,
            anchors=[{"time": dates[i], "price": round_price(c[i]), "label": LABELS[ptype]}],
            zones=[{"t1": dates[first], "t2": dates[i], "low": round_price(lo_), "high": round_price(hi_), "role": "candle_box"}],
            markers=[marker], levels=levels, confidence=float(np.clip(conf + (0.1 if vc else 0), 0.2, 0.95)),
            context={"volume_confirmed": vc, "rel_vol": None if not np.isfinite(rel_vol[i]) else round(float(rel_vol[i]), 2)},
            bars=dates[first:i + 1],
            explanation=f"{LABELS[ptype]} on {dates[i]}: {DESCRIPTIONS[ptype]}."
                        + (f" Volume was {rel_vol[i]:.1f}× the 20-day average." if vc else ""),
        ))

    for i in range(max(2, n - lookback), n):
        ab = avg_body[i] if np.isfinite(avg_body[i]) and avg_body[i] > 0 else body[max(0, i - 20):i + 1].mean() or 1e-9
        t = prior_trend(i)
        small = body[i] <= 0.1 * rng[i]
        # ---- three-bar patterns
        if i >= 2:
            a, b = i - 2, i - 1
            mid_a = (o[a] + c[a]) / 2
            if bear[a] and body[a] >= ab and body[b] <= 0.35 * body[a] and bull[i] and c[i] > mid_a and t <= 0:
                add("morning_star", i, 3, 0.65); continue
            if bull[a] and body[a] >= ab and body[b] <= 0.35 * body[a] and bear[i] and c[i] < mid_a and t >= 0:
                add("evening_star", i, 3, 0.65); continue
            if all(bull[j] and body[j] >= 0.6 * ab and upper[j] <= 0.35 * body[j] for j in (a, b, i)) and c[a] < c[b] < c[i]:
                add("three_white_soldiers", i, 3, 0.6); continue
            if all(bear[j] and body[j] >= 0.6 * ab and lower[j] <= 0.35 * body[j] for j in (a, b, i)) and c[a] > c[b] > c[i]:
                add("three_black_crows", i, 3, 0.6); continue
        # ---- two-bar patterns
        p = i - 1
        if bear[p] and bull[i] and o[i] <= c[p] and c[i] >= o[p] and body[i] > body[p] and t <= 0:
            add("bullish_engulfing", i, 2, 0.6); continue
        if bull[p] and bear[i] and o[i] >= c[p] and c[i] <= o[p] and body[i] > body[p] and t >= 0:
            add("bearish_engulfing", i, 2, 0.6); continue
        mid_p = (o[p] + c[p]) / 2
        if bear[p] and body[p] >= ab and bull[i] and o[i] <= c[p] and mid_p < c[i] < o[p] and t <= 0:
            add("piercing_line", i, 2, 0.55); continue
        if bull[p] and body[p] >= ab and bear[i] and o[i] >= c[p] and o[p] < c[i] < mid_p and t >= 0:
            add("dark_cloud_cover", i, 2, 0.55); continue
        if bear[p] and body[p] >= ab and bull[i] and body[i] <= 0.6 * body[p] and o[i] >= c[p] and c[i] <= o[p] and t < 0:
            add("bullish_harami", i, 2, 0.45); continue
        if bull[p] and body[p] >= ab and bear[i] and body[i] <= 0.6 * body[p] and o[i] <= c[p] and c[i] >= o[p] and t > 0:
            add("bearish_harami", i, 2, 0.45); continue
        # ---- single-bar patterns
        if small and rng[i] >= 0.5 * ab:
            if upper[i] <= 0.1 * rng[i] and lower[i] >= 0.6 * rng[i]:
                add("dragonfly_doji", i, 1, 0.45)
            elif lower[i] <= 0.1 * rng[i] and upper[i] >= 0.6 * rng[i]:
                add("gravestone_doji", i, 1, 0.45)
            else:
                add("doji", i, 1, 0.35)
            continue
        if body[i] > 0.1 * rng[i] and lower[i] >= 2 * body[i] and upper[i] <= max(0.3 * body[i], 0.1 * rng[i]):
            if t < 0:
                add("hammer", i, 1, 0.5); continue
            if t > 0:
                add("hanging_man", i, 1, 0.45); continue
        if body[i] > 0.1 * rng[i] and upper[i] >= 2 * body[i] and lower[i] <= max(0.3 * body[i], 0.1 * rng[i]):
            if t > 0:
                add("shooting_star", i, 1, 0.5); continue
            if t < 0:
                add("inverted_hammer", i, 1, 0.45); continue
        if body[i] >= 0.9 * rng[i] and body[i] >= 1.3 * ab:
            add("bullish_marubozu" if bull[i] else "bearish_marubozu", i, 1, 0.45)
    return out
