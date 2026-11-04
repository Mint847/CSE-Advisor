"""Refresh the replay fixtures from cse.lk (public daily price data, ~1 year).

    .venv\\Scripts\\python tests\\fixtures\\fetch_fixtures.py

Prices are stored RAW (unadjusted), exactly as cse.lk returns them; open is not
provided by cse.lk, so it is estimated as the previous close (as the app does).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parents[1] / "src"))

from cse_advisor.cse_client import client, ms_to_date  # noqa: E402

SYMBOLS = ["KZOO.N0000", "JKH.N0000", "RIL.N0000"]


def main() -> None:
    c = client()
    for sym in SYMBOLS:
        info = c.company_info(sym)["reqSymbolInfo"]
        rows = sorted(c.daily_history(info["id"]), key=lambda r: r["t"])
        recs = [{"date": ms_to_date(r["t"]), "high": r.get("h") or r["p"], "low": r.get("l") or r["p"], "close": r["p"],
                 "volume": float(r.get("q") or 0)} for r in rows if r.get("p") is not None]
        df = pd.DataFrame(recs)
        df.to_csv(HERE / f"{sym.split('.')[0].lower()}_raw.csv", index=False)
        print(sym, len(df), df["date"].min(), df["date"].max())


if __name__ == "__main__":
    main()
