"""Replay helper: run the full analysis on a fixture as it would have looked on a past date."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

from cse_advisor.analysis.corporate_actions import adjust_prices
from cse_advisor.engine import analyze_frame

FIX = Path(__file__).parent / "fixtures"

# Corporate actions as the app records them from CSE announcements (KZOO: 'Rights Issue (Dates)'
# gives XR 2026-03-04 and "Two (2) new ... for every One (01)"; the listing notice of 2026-04-10
# gives 614,156,734 shares and Rs 4,299,097,138 raised -> Rs 7.00).
ACTIONS: dict[str, list[dict[str, Any]]] = {
    "KZOO": [{"id": 1, "kind": "rights", "ex_date": date(2026, 3, 4), "ratio_new": 2.0, "ratio_old": 1.0, "price": 7.0,
              "listing_date": date(2026, 4, 10), "trading_date": date(2026, 3, 16), "ignore": False}],
    "JKH": [],
    "RIL": [],
}
NAMES = {"KZOO": "Ceylon Land & Equity PLC", "JKH": "John Keells Holdings PLC", "RIL": "RIL Property PLC"}


def raw_frame(tick: str) -> pd.DataFrame:
    df = pd.read_csv(FIX / f"{tick.lower()}_raw.csv", parse_dates=["date"]).set_index("date")
    df["open"] = df["close"].shift().fillna(df["close"])
    df["open"] = df[["open", "high"]].min(axis=1).combine(df["low"], max)
    df["turnover"] = df["close"] * df["volume"]
    df["trades"] = None
    df["open_estimated"] = True
    df["source"] = "history"
    return df[["open", "high", "low", "close", "volume", "turnover", "trades", "open_estimated", "source"]]


def run(tick: str, asof: str, actions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    raw = raw_frame(tick)
    acts = ACTIONS[tick] if actions is None else actions
    # actions are only known once announced; the XR adjustment applies from the XR day onward
    adj, ann = adjust_prices(raw[raw.index <= pd.Timestamp(asof)], acts)
    sec = SimpleNamespace(symbol=f"{tick}.N0000", name=NAMES[tick], info={}, shares_issued=None)
    return analyze_frame(None, sec, adj, ann, fund_rows=[], anns=[], md=pd.DataFrame(), position=None)
