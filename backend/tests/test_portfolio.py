"""Portfolio totals after fees, risk to stops, concentration; relative strength vs the ASPI."""
import pandas as pd
import pytest

from cse_advisor.analysis.market import relative_strength
from cse_advisor.portfolio import position_figures, summarize

FEE = 1.12


def test_fee_inclusive_pl():
    open_ = {"id": 1, "symbol": "JKH.N0000", "quantity": 1000, "entry_price": 20.0, "price": 22.0, "closed": False, "stop_used": 19.0}
    f = position_figures(open_, FEE)
    assert f["cost"] == pytest.approx(20_224.0)
    assert f["unrealised_net"] == pytest.approx(22_000 * (1 - 0.0112) - 20_224.0, abs=0.01)    # selling fee included
    assert f["pl_at_stop"] == pytest.approx(19_000 * (1 - 0.0112) - 20_224.0, abs=0.01)
    closed = {"id": 2, "symbol": "COMB.N0000", "quantity": 100, "entry_price": 200.0, "exit_price": 200.0, "closed": True}
    assert position_figures(closed, FEE)["realised_net"] == pytest.approx(-448.0)                # flat trade still loses two fees


def test_summary_risk_and_concentration():
    rows = [
        {"id": 1, "symbol": "JKH.N0000", "quantity": 1000, "entry_price": 20.0, "price": 22.0, "closed": False, "stop_used": 19.0, "sector": "CG"},
        {"id": 2, "symbol": "SAMP.N0000", "quantity": 100, "entry_price": 140.0, "price": 150.0, "closed": False, "stop_used": None, "sector": "BNK"},
        {"id": 3, "symbol": "COMB.N0000", "quantity": 100, "entry_price": 200.0, "exit_price": 210.0, "closed": True},
    ]
    s = summarize(rows, 1_000_000, FEE)
    assert s["open_positions"] == 2 and s["market_value"] == pytest.approx(37_000)
    assert s["largest"]["symbol"] == "JKH.N0000" and s["largest"]["pct"] == pytest.approx(59.5, abs=0.1)
    assert s["positions_without_stop"] == ["SAMP.N0000"]
    # from today's value down to the stop: 1000 x (22 - 19) less the selling fee on that difference
    assert s["risk_to_stops"] == pytest.approx(3000 * (1 - 0.0112), abs=0.01)
    assert s["risk_to_stops_pct_of_capital"] == pytest.approx(0.30, abs=0.01)
    assert [x["sector"] for x in s["by_sector"]] == ["CG", "BNK"]
    assert s["realised_net"] == pytest.approx(100 * 210 * (1 - 0.0112) - 100 * 200 * 1.0112, abs=0.01)


def test_relative_strength():
    idx = pd.bdate_range("2026-01-01", periods=130)
    stock = pd.Series([100 * (1.002 ** i) for i in range(130)], index=idx)
    aspi = pd.Series(1000.0, index=idx)
    rs = relative_strength(stock, aspi)
    assert rs["label"] == "leader" and rs["vs_aspi"]["60"] == pytest.approx(12.7, abs=0.1)
    assert relative_strength(stock.iloc[:10], aspi) is None
