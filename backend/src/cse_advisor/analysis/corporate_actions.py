"""Corporate actions: rights issues, bonus/scrip issues and share splits.

cse.lk price history is NOT adjusted, so a rights issue shows up as a crash on the XR
day (KZOO: 20.70 -> 13.80 on 4 Mar 2026). Every engine works on back-adjusted prices,
the same way TradingView charts them:

  rights   r new for every 1 held at price S:  TERP = (P + r*S) / (1 + r),  factor = TERP / P
  bonus    r new for every 1 held:             factor = 1 / (1 + r)
  split    each share becomes n:               factor = 1 / n

Bars before the ex-date are multiplied by the factor (volume divided by it).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

import pandas as pd

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
         "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "twenty five": 25, "fifty": 50, "hundred": 100}


def _num(tok: str) -> float | None:
    tok = tok.strip().lower()
    if re.fullmatch(r"\d+(\.\d+)?", tok):
        return float(tok)
    return float(WORDS[tok]) if tok in WORDS else None


def parse_proportion(text: str | None) -> tuple[float, float] | None:
    """'Two (2) new Ordinary Voting Shares for every One (01) ordinary voting share' -> (2, 1).
    Also handles '1 for 5', '1:10' and 'one (1) share for every five (5)'."""
    if not text:
        return None
    t = " ".join(text.split())
    paren = re.findall(r"\((\d+(?:\.\d+)?)\)", t)
    if len(paren) >= 2 and re.search(r"for\s+every", t, re.I):
        return float(paren[0]), float(paren[1])
    m = re.search(r"(\d+(?:\.\d+)?|[a-z]+)\s*(?:\(\d+\))?\s*(?:new\s+)?(?:ordinary\s+)?(?:voting\s+|non-voting\s+)?(?:shares?)?\s*"
                  r"(?:for\s+every|for)\s*(\d+(?:\.\d+)?|[a-z]+)", t, re.I)
    if m:
        a, b = _num(m.group(1)), _num(m.group(2))
        if a and b:
            return a, b
    m = re.search(r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", t)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None


def parse_split(text: str | None) -> float | None:
    """'subdivided into ten (10)' / 'every one share into 10 shares' / '1:10' -> 10."""
    if not text:
        return None
    t = " ".join(text.split())
    m = re.search(r"in\s*to\s+(\d+|[a-z]+)\s*(?:\((\d+)\))?", t, re.I)
    if m:
        return float(m.group(2)) if m.group(2) else _num(m.group(1))
    p = parse_proportion(t)
    if p and p[1]:
        return p[0] / p[1] if p[0] > p[1] else p[1] / p[0]
    return None


def parse_rights_price(text: str | None) -> float | None:
    """Find 'Rs. 7.00 per share' / 'at a consideration of Rs 7' in announcement text."""
    if not text:
        return None
    t = " ".join(text.split())
    m = re.search(r"(?:consideration|price|issue price|subscription price)[^.]{0,40}?Rs\.?\s*/?-?\s*([\d,]+(?:\.\d+)?)", t, re.I) \
        or re.search(r"Rs\.?\s*([\d,]+(?:\.\d+)?)\s*(?:/-)?\s*(?:per|for each|each)\s+(?:new\s+)?(?:ordinary\s+)?(?:voting\s+)?share", t, re.I)
    if m:
        try:
            v = float(m.group(1).replace(",", ""))
            return v if 0 < v < 100000 else None
        except ValueError:
            return None
    return None


def price_from_listing(text: str | None) -> tuple[float, float] | None:
    """Listing notification: shares allotted and Rs raised -> (shares, price per share)."""
    if not text:
        return None
    t = " ".join(text.split())
    nums = [float(x.replace(",", "")) for x in re.findall(r"\d{1,3}(?:,\d{3}){2,}(?:\.\d+)?", t)]
    if len(nums) < 3:
        return None
    shares = nums[0]
    amount = max(nums)
    if shares <= 0 or amount <= shares * 0.01:
        return None
    return shares, amount / shares


# ---------------------------------------------------------------- maths
def terp(cum_price: float, ratio_new: float, ratio_old: float, price: float) -> float:
    r = ratio_new / ratio_old
    return (cum_price + r * price) / (1 + r)


def pre_rights_equivalent(price_now: float, ratio_new: float, ratio_old: float, sub_price: float) -> float:
    """Value per ORIGINAL share for someone who held before XR and subscribed:
    (1 + r) * P - r * S. KZOO at 10.00 with 2-for-1 at Rs 7: 3*10 - 2*7 = 16."""
    r = ratio_new / ratio_old
    return (1 + r) * price_now - r * sub_price


def action_factor(a: dict[str, Any], cum_price: float | None) -> float | None:
    kind = a.get("kind")
    rn, ro = a.get("ratio_new"), a.get("ratio_old")
    if kind == "rights":
        if not (rn and ro and a.get("price") and cum_price):
            return None
        if a["price"] >= cum_price:          # deep-out-of-the-money rights: no theoretical dilution
            return 1.0
        return terp(cum_price, rn, ro, a["price"]) / cum_price
    if kind == "bonus":
        return ro / (ro + rn) if rn and ro else None
    if kind == "split":
        n = (rn / ro) if rn and ro else None
        return 1 / n if n and n > 0 else None
    return None


# price sources that arrive already adjusted for corporate actions (left alone by adjust_prices)
PRE_ADJUSTED = ("import_adj", "tv_adj")


def adjust_prices(raw: pd.DataFrame, actions: list[dict[str, Any]]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Back-adjust OHLC/volume for every action whose ex-date falls inside the history.

    Rows whose `source` is 'import_adj' are already adjusted (e.g. imported from TradingView)
    and are left alone. Returns (adjusted df, actions annotated with cum_price/factor/applied)."""
    df = raw.copy()
    for k in ("open", "high", "low", "close"):
        df[f"raw_{k}"] = df[k]
    df["raw_volume"] = df["volume"]
    if df.empty:
        return df, actions
    pre_adjusted = df["source"].isin(PRE_ADJUSTED) if "source" in df else pd.Series(False, index=df.index)
    out = []
    for a in sorted(actions, key=lambda x: x.get("ex_date") or date.min, reverse=True):
        a = dict(a)
        ex = a.get("ex_date")
        a["applied"], a["factor"], a["cum_price"] = False, None, None
        if a.get("ignore") or not ex:
            a["reason"] = "ignored" if a.get("ignore") else "no ex-date"
            out.append(a)
            continue
        ex_ts = pd.Timestamp(ex)
        before = df.index < ex_ts
        if not before.any() or before.all():
            a["reason"] = "ex-date outside the price history"
            out.append(a)
            continue
        cum = float(df.loc[before, "raw_close"].iloc[-1])
        a["cum_price"] = cum
        f = action_factor(a, cum)
        a["factor"] = f
        if a.get("kind") == "rights" and a.get("ratio_new") and a.get("ratio_old") and a.get("price"):
            a["terp"] = round(terp(cum, a["ratio_new"], a["ratio_old"], a["price"]), 3)
        if f is None or abs(f - 1) < 1e-6:
            a["reason"] = "subscription price unknown" if f is None and a.get("kind") == "rights" else "no adjustment needed" if f else "ratio unknown"
            out.append(a)
            continue
        m = before & ~pre_adjusted
        for k in ("open", "high", "low", "close"):
            df.loc[m, k] = df.loc[m, k] * f
        df.loc[m, "volume"] = df.loc[m, "volume"] / f
        if "turnover" in df:
            pass  # turnover (LKR) is unchanged by a pure re-scaling of price and share count
        a["applied"] = True
        out.append(a)
    if any(a.get("applied") for a in out) and "open_estimated" in df:
        # estimated opens (= previous close) must use the ADJUSTED previous close, otherwise the
        # XR-day bar would 'open' at the old cum-rights price and hide the real gap / wick.
        est = df["open_estimated"].fillna(False).astype(bool)
        prev = df["close"].shift()
        fixed = prev.clip(lower=df["low"], upper=df["high"])
        m = est & prev.notna()
        df.loc[m, "open"] = fixed[m]
    return df, sorted(out, key=lambda x: x.get("ex_date") or date.min)


def phases(actions: list[dict[str, Any]], asof: date, close: float) -> dict[str, Any]:
    """Event phase for today's analysis: pre-XR warning or post-XR supply overhang."""
    res: dict[str, Any] = {"phase": None, "notes": [], "score_adjustment": 0, "events": []}
    for a in actions:
        if a.get("ignore") or not a.get("ex_date"):
            continue
        ex = a["ex_date"]
        res["events"].append({"kind": a["kind"], "date": str(ex), "label": {"rights": "XR", "bonus": "XB", "split": "Split"}.get(a["kind"], a["kind"])})
        if a.get("listing_date"):
            res["events"].append({"kind": "listing", "date": str(a["listing_date"]), "label": "New shares listed"})
        if a["kind"] != "rights":
            continue
        rn, ro, s = a.get("ratio_new"), a.get("ratio_old"), a.get("price")
        if asof < ex and (ex - asof).days <= 30:
            res["phase"] = "pre_xr"
            txt = f"Rights issue goes ex-rights (XR) on {ex}"
            if rn and ro and s:
                t = terp(close, rn, ro, s)
                res["terp_today"] = round(t, 3)
                txt += (f": {rn:g} new for every {ro:g} at Rs {s:g}. Theoretical ex-rights price at today's close = "
                        f"({close:.2f} + {rn / ro:g} × {s:g}) / {1 + rn / ro:g} = {t:.2f}. Expect the price to open near it")
            res["notes"].append(txt + ".")
            res["score_adjustment"] -= 5
        else:
            end = (a.get("listing_date") or (ex + timedelta(days=45))) + timedelta(days=21)
            if ex <= asof <= end:
                res["phase"] = "overhang"
                when = f"new shares listed {a['listing_date']}" if a.get("listing_date") else "new shares expected in CDS accounts"
                res["notes"].append(
                    f"Rights-issue supply overhang: XR {ex}, {when}. Subscribers who want cash may sell, so "
                    f"expect selling pressure until about {end}.")
                res["score_adjustment"] -= 10
            if ex <= asof and (asof - ex).days <= 180 and rn and ro and s:
                res["pre_rights_equivalent"] = round(pre_rights_equivalent(close, rn, ro, s), 2)
                res["notes"].append(
                    f"In pre-rights terms today's {close:.2f} equals Rs {res['pre_rights_equivalent']:.2f} per original share "
                    f"((1 + {rn / ro:g}) × {close:.2f} − {rn / ro:g} × {s:g}) for holders who subscribed.")
    return res
