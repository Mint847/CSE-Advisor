"""Technical indicators implemented with pandas (no TA-Lib dependency).

All functions take a DataFrame with columns open/high/low/close/volume and a
DatetimeIndex (or any index) and return Series aligned to it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def _wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    gain = _wilder(d.clip(lower=0), n)
    loss = _wilder(-d.clip(upper=0), n)
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out = out.where(loss != 0, 100.0)  # no losses in window -> RSI 100
    return out.where(gain.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    up, lo = mid + k * sd, mid - k * sd
    return pd.DataFrame({"bb_mid": mid, "bb_up": up, "bb_lo": lo, "bb_width": (up - lo) / mid})


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift()
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return _wilder(true_range(df), n)


def adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    tr = _wilder(true_range(df), n)
    pdi = 100 * _wilder(plus_dm, n) / tr
    mdi = 100 * _wilder(minus_dm, n) / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return pd.DataFrame({"adx": _wilder(dx, n), "plus_di": pdi, "minus_di": mdi})


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff()).fillna(0)
    return (direction * df["volume"]).cumsum()


def ad_line(df: pd.DataFrame) -> pd.Series:
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng
    return (mfm.fillna(0) * df["volume"]).cumsum()


def mfi(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    flow = tp * df["volume"]
    pos = flow.where(tp > tp.shift(), 0.0).rolling(n).sum()
    neg = flow.where(tp < tp.shift(), 0.0).rolling(n).sum()
    return 100 - 100 / (1 + pos / neg.replace(0, np.nan))


def slope(s: pd.Series, n: int) -> pd.Series:
    """Rolling least-squares slope per bar."""
    x = np.arange(n, dtype=float)
    x -= x.mean()
    denom = (x ** 2).sum()
    return s.rolling(n, min_periods=n).apply(lambda y: float(np.dot(x, y - y.mean()) / denom), raw=True)


def add_all(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of df with every indicator the engines and chart need."""
    out = df.copy()
    c = out["close"]
    for n in (20, 21, 50, 200):
        out[f"ema{n}"] = ema(c, n)
    for n in (20, 50, 200):
        out[f"sma{n}"] = c.rolling(n, min_periods=n).mean()
    out["rsi"] = rsi(c)
    out["rsi_ma"] = out["rsi"].rolling(14, min_periods=5).mean()
    out = out.join(macd(c)).join(bollinger(c)).join(adx(out))
    out["atr"] = atr(out)
    out["obv"] = obv(out)
    out["ad"] = ad_line(out)
    out["mfi"] = mfi(out)
    out["vol_avg20"] = out["volume"].rolling(20, min_periods=5).mean()
    out["rel_vol"] = out["volume"] / out["vol_avg20"].shift().replace(0, np.nan)
    turnover = out["turnover"] if "turnover" in out else c * out["volume"]
    out["turnover"] = turnover.fillna(c * out["volume"])
    bw = out["bb_width"]
    out["bb_width_pct"] = bw.rolling(120, min_periods=40).rank(pct=True)
    return out
