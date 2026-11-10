"""EQS: your own screener. Filter every listed stock on price, size, sector, today's move and on the figures the app works out
(1-month to 6-month return, distance from the 52-week high, trend, relative strength, one-year volatility, Sharpe, beta, worst
fall). Pure logic over the rows the API assembles; nothing here changes an engine's verdict.

A stock with no value for a field you filter on is left out and counted: the candidate figures exist only for the liquid stocks
(the evening ranking), and the one-year risk figures only for those and the stocks you track."""
from __future__ import annotations

import math
from typing import Any, Callable

MAX_FILTERS = 10
MAX_ROWS = 300
NUM_OPS = (">", ">=", "<", "<=", "between", "=")
TEXT_OPS = ("is", "in", "contains")
BOOL_OPS = ("is",)


def _scr(k: str, scale: float = 1.0) -> Callable[[dict[str, Any]], Any]:
    def g(r: dict[str, Any]) -> Any:
        v = (r.get("scr") or {}).get(k)
        return None if v is None else (float(v) * scale if not isinstance(v, bool) else v)
    return g


def _q(k: str, scale: float = 1.0) -> Callable[[dict[str, Any]], Any]:
    def g(r: dict[str, Any]) -> Any:
        v = (r.get("quant") or {}).get(k)
        return None if v is None else float(v) * scale
    return g


def _base(k: str) -> Callable[[dict[str, Any]], Any]:
    return lambda r: r.get(k)


# id -> definition. `get` returns the value in the units shown to the user (percent as 12.5, not 0.125).
FIELDS: dict[str, dict[str, Any]] = {
    "price": {"label": "Price", "kind": "num", "unit": "LKR", "group": "Market today", "get": _base("price"), "help": "Last traded or closing price."},
    "change_pct": {"label": "Change today", "kind": "num", "unit": "%", "group": "Market today", "get": _base("change_pct"), "help": "Today's move in percent."},
    "turnover": {"label": "Turnover today", "kind": "num", "unit": "LKR", "group": "Market today", "get": _base("turnover"), "help": "Rupees traded today."},
    "volume": {"label": "Volume today", "kind": "num", "unit": "shares", "group": "Market today", "get": _base("volume"), "help": "Shares traded today."},
    "market_cap": {"label": "Market cap", "kind": "num", "unit": "LKR", "group": "Size and sector", "get": _base("market_cap"), "help": "Shares issued × price."},
    "sector": {"label": "Sector", "kind": "text", "unit": "", "group": "Size and sector", "get": _base("sector"), "help": "The sector the stock belongs to."},
    "industry": {"label": "Industry", "kind": "text", "unit": "", "group": "Size and sector", "get": _base("industry"), "help": "A finer split of the sector."},
    "name": {"label": "Name", "kind": "text", "unit": "", "group": "Size and sector", "get": _base("name"), "help": "The company's name."},
    "median_turnover": {"label": "Typical daily turnover", "kind": "num", "unit": "LKR", "group": "Trend and strength (liquid stocks)", "get": _scr("turn"),
                        "help": "Median rupees traded a day: how easy it is to trade."},
    "ret20": {"label": "Return, 1 month", "kind": "num", "unit": "%", "group": "Trend and strength (liquid stocks)", "get": _scr("ret20", 100), "help": "Price change over the last 20 sessions."},
    "ret60": {"label": "Return, 3 months", "kind": "num", "unit": "%", "group": "Trend and strength (liquid stocks)", "get": _scr("ret60", 100), "help": "Price change over the last 60 sessions."},
    "ret120": {"label": "Return, 6 months", "kind": "num", "unit": "%", "group": "Trend and strength (liquid stocks)", "get": _scr("ret120", 100), "help": "Price change over the last 120 sessions."},
    "rs120": {"label": "Return vs the ASPI, 6 months", "kind": "num", "unit": "pts", "group": "Trend and strength (liquid stocks)", "get": _scr("rs120", 100),
              "help": "Its 6-month return minus the ASPI's, in percentage points."},
    "rs_rank": {"label": "Strength rank", "kind": "num", "unit": "percentile", "group": "Trend and strength (liquid stocks)", "get": _scr("rs_rank", 100),
                "help": "Where its 6-month return ranks among the liquid stocks: 90 = better than 90% of them."},
    "dist52": {"label": "From the 52-week high", "kind": "num", "unit": "%", "group": "Trend and strength (liquid stocks)", "get": _scr("dist52", 100),
               "help": "How far below its 52-week high it is (0 = at the high; -10 = 10% below)."},
    "ext20": {"label": "Above its 20-day average", "kind": "num", "unit": "%", "group": "Trend and strength (liquid stocks)", "get": _scr("ext20", 100), "help": "How stretched it is above its short average."},
    "trendup": {"label": "In an uptrend", "kind": "bool", "unit": "", "group": "Trend and strength (liquid stocks)", "get": _scr("trendup"), "help": "Price above its 50-day average, which is above its 200-day."},
    "above50": {"label": "Above its 50-day average", "kind": "bool", "unit": "", "group": "Trend and strength (liquid stocks)", "get": _scr("above50"), "help": "Today's price is above its 50-day average."},
    "qualifies": {"label": "Meets the candidate rule", "kind": "bool", "unit": "", "group": "Trend and strength (liquid stocks)", "get": _scr("qualifies"),
                  "help": "Uptrend, within 10% of the 52-week high, and among the strongest 40% (the rule tested over 14 years, tested not proven)."},
    "hits": {"label": "Candidate rule parts met", "kind": "num", "unit": "of 3", "group": "Trend and strength (liquid stocks)", "get": _scr("hits"), "help": "How many of the rule's three parts it meets."},
    "vol": {"label": "Volatility, 1 year", "kind": "num", "unit": "%", "group": "One-year risk (liquid or tracked stocks)", "get": _q("vol", 100), "help": "How much it swings in a year (annualised)."},
    "sharpe": {"label": "Sharpe ratio, 1 year", "kind": "num", "unit": "", "group": "One-year risk (liquid or tracked stocks)", "get": _q("sharpe"), "help": "Return above T-bills per unit of volatility."},
    "beta": {"label": "Beta to the ASPI, 1 year", "kind": "num", "unit": "", "group": "One-year risk (liquid or tracked stocks)", "get": _q("beta"), "help": "How much it moves per 1% move of the ASPI."},
    "mdd": {"label": "Worst fall, 1 year", "kind": "num", "unit": "%", "group": "One-year risk (liquid or tracked stocks)", "get": _q("mdd", 100), "help": "Biggest drop from a high to a later low (a negative number)."},
    "mine": {"label": "On one of my lists or held", "kind": "bool", "unit": "", "group": "Mine", "get": lambda r: bool(r.get("mine")), "help": "You follow or hold it."},
}
BASE_COLUMNS = ["price", "change_pct", "turnover"]

PRESETS: list[dict[str, Any]] = [
    {"id": "rule", "name": "Meets the candidate rule", "blurb": "Uptrend, near its high, among the strongest: the rule the 14-year study favoured (tested, not proven).",
     "spec": {"filters": [{"field": "qualifies", "op": "is", "value": True}], "sort": {"field": "rs_rank", "dir": "desc"}}},
    {"id": "steady", "name": "Steady and strong", "blurb": "Calmer than most (volatility under 30%), paid for its risk (Sharpe above 0.5) and rising over 6 months.",
     "spec": {"filters": [{"field": "vol", "op": "<", "value": 30}, {"field": "sharpe", "op": ">", "value": 0.5}, {"field": "ret120", "op": ">", "value": 0}], "sort": {"field": "sharpe", "dir": "desc"}}},
    {"id": "high", "name": "Uptrend, close to the high", "blurb": "In an uptrend and within 10% of its 52-week high.",
     "spec": {"filters": [{"field": "trendup", "op": "is", "value": True}, {"field": "dist52", "op": ">=", "value": -10}], "sort": {"field": "dist52", "dir": "desc"}}},
    {"id": "movers", "name": "Big movers on real volume", "blurb": "Up 5% or more today with at least LKR 5 million traded.",
     "spec": {"filters": [{"field": "change_pct", "op": ">=", "value": 5}, {"field": "turnover", "op": ">=", "value": 5_000_000}], "sort": {"field": "change_pct", "dir": "desc"}}},
    {"id": "low_beta", "name": "Defensive: low beta", "blurb": "Moves less than the market (beta under 0.7) with a worst fall milder than -25%.",
     "spec": {"filters": [{"field": "beta", "op": "<", "value": 0.7}, {"field": "mdd", "op": ">", "value": -25}], "sort": {"field": "beta", "dir": "asc"}}},
]


def availability(field: str) -> str:
    g = FIELDS[field]["group"]
    if g.startswith("Trend"):
        return "this figure exists only for the liquid stocks in the evening candidate ranking"
    if g.startswith("One-year"):
        return "this figure exists only for the liquid stocks and the stocks you track"
    return "the CSE gave no value for them"


def catalog() -> dict[str, Any]:
    return {"fields": [{"id": k, **{x: v[x] for x in ("label", "kind", "unit", "group", "help")}, "ops": {"num": NUM_OPS, "text": TEXT_OPS, "bool": BOOL_OPS}[v["kind"]]} for k, v in FIELDS.items()],
            "presets": PRESETS, "max_filters": MAX_FILTERS, "max_rows": MAX_ROWS}


def normalise(spec: Any) -> dict[str, Any]:
    """A clean copy of a screen, or a ValueError in plain words."""
    if not isinstance(spec, dict):
        raise ValueError("The screen must be an object.")
    flt = spec.get("filters") or []
    if not isinstance(flt, list) or len(flt) > MAX_FILTERS:
        raise ValueError(f"Use at most {MAX_FILTERS} conditions.")
    out = []
    for f in flt:
        if not isinstance(f, dict) or f.get("field") not in FIELDS:
            raise ValueError("Unknown field in a condition.")
        d, op, v = FIELDS[f["field"]], f.get("op"), f.get("value")
        kind = d["kind"]
        if op not in {"num": NUM_OPS, "text": TEXT_OPS, "bool": BOOL_OPS}[kind]:
            raise ValueError(f"'{d['label']}': that comparison does not apply.")
        if kind == "num":
            try:
                v = [float(x) for x in v] if op == "between" else float(v)
            except (TypeError, ValueError):
                raise ValueError(f"'{d['label']}': enter a number" + (" for each end of the range." if op == "between" else ".")) from None
            vals = v if op == "between" else [v]
            if op == "between" and len(v) != 2:
                raise ValueError(f"'{d['label']}': a range needs two numbers.")
            if any(not math.isfinite(x) for x in vals):
                raise ValueError(f"'{d['label']}': that number is not valid.")
            if op == "between" and v[0] > v[1]:
                v = [v[1], v[0]]
        elif kind == "bool":
            if not isinstance(v, bool):
                raise ValueError(f"'{d['label']}': choose yes or no.")
        else:
            if op == "in":
                v = [str(x).strip() for x in (v if isinstance(v, list) else [v]) if str(x).strip()]
                if not v:
                    raise ValueError(f"'{d['label']}': choose at least one.")
            else:
                v = str(v or "").strip()
                if not v:
                    raise ValueError(f"'{d['label']}': type something to look for.")
        out.append({"field": f["field"], "op": op, "value": v})
    srt = spec.get("sort") or {"field": "turnover", "dir": "desc"}
    if srt.get("field") not in FIELDS or srt.get("dir") not in ("asc", "desc"):
        raise ValueError("Sort by a known field, ascending or descending.")
    try:
        limit = max(1, min(int(spec.get("limit", 100)), MAX_ROWS))
    except (TypeError, ValueError):
        raise ValueError("The number of rows must be a number.") from None
    return {"name": str(spec.get("name") or "")[:60], "filters": out, "sort": {"field": srt["field"], "dir": srt["dir"]}, "limit": limit}


def _test(val: Any, f: dict[str, Any]) -> bool:
    op, v = f["op"], f["value"]
    if FIELDS[f["field"]]["kind"] == "num":
        x = float(val)
        return {">": x > v, ">=": x >= v, "<": x < v, "<=": x <= v, "=": abs(x - v) < 1e-9}.get(op, False) if op != "between" else v[0] <= x <= v[1]
    if FIELDS[f["field"]]["kind"] == "bool":
        return bool(val) is v
    s = str(val).lower()
    return s in [x.lower() for x in v] if op == "in" else (v.lower() == s if op == "is" else v.lower() in s)


def run(rows: list[dict[str, Any]], spec_in: Any) -> dict[str, Any]:
    spec = normalise(spec_in)
    used = list(dict.fromkeys(f["field"] for f in spec["filters"]))
    cols = list(dict.fromkeys(BASE_COLUMNS + used + [spec["sort"]["field"]]))
    unknown = {k: 0 for k in used}
    matched = []
    for r in rows:
        ok = True
        for f in spec["filters"]:
            val = FIELDS[f["field"]]["get"](r)
            if val is None or (isinstance(val, float) and not math.isfinite(val)):
                unknown[f["field"]] += 1
                ok = False
                break
            if not _test(val, f):
                ok = False
                break
        if ok:
            matched.append(r)
    sf, rev = spec["sort"]["field"], spec["sort"]["dir"] == "desc"

    def key(r: dict[str, Any]) -> tuple[int, Any]:
        v = FIELDS[sf]["get"](r)
        if v is None:
            return (1, 0)                                              # a stock with no value always sorts last
        return (0, (-v if rev else v) if isinstance(v, (int, float)) and not isinstance(v, bool) else (str(v).lower() if not rev else "".join(chr(0x10FFFF - ord(c)) for c in str(v).lower())))
    matched.sort(key=key)
    shown = matched[:spec["limit"]]
    notes = []
    for k, n in unknown.items():
        if n:
            notes.append(f"{n} stocks have no value for “{FIELDS[k]['label']}” and were left out: {availability(k)}.")
    return {"spec": spec, "total": len(rows), "matched": len(matched), "shown": len(shown), "columns": cols, "notes": notes,
            "rows": [{"symbol": r["symbol"], "name": r.get("name"), "sector": r.get("sector"), "mine": bool(r.get("mine")), "held": bool(r.get("held")),
                      "values": {c: FIELDS[c]["get"](r) for c in cols}} for r in shown]}
