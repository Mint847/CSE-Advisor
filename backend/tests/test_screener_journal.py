"""SCR ranking rule, forward-test journal, exit warnings, regime banner and the nightly backup."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from cse_advisor import journal, portfolio, screener
from cse_advisor.analysis.market import regime_banner
from cse_advisor.analysis.selection import features, qualifies
from cse_advisor.db import Analysis, Base, JournalEntry, PriceDaily


# ----------------------------------------------------------------------------------------------- selection rule
def _frame(closes: list[float]) -> pd.DataFrame:
    idx = pd.bdate_range("2023-01-02", periods=len(closes))
    c = pd.Series(closes, index=idx)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 100_000.0})


def test_uptrend_near_high_vs_downtrend():
    up = _frame(list(np.linspace(10, 20, 400)))
    down = _frame(list(np.linspace(20, 10, 400)))
    aspi = pd.Series(np.linspace(1000, 1100, 400), index=up.index)
    fu, _, _, _ = features(up, aspi)
    fd, _, _, _ = features(down, aspi)
    assert bool(fu["trendup"].iloc[-1]) and fu["dist52"].iloc[-1] > -0.02 and fu["ret120"].iloc[-1] > 0.15
    assert not bool(fd["trendup"].iloc[-1]) and fd["dist52"].iloc[-1] < -0.3
    assert fu["rs120"].iloc[-1] > 0 > fd["rs120"].iloc[-1]


def test_rule_needs_all_three():
    assert qualifies(True, -0.05, 0.9)
    assert not qualifies(False, -0.05, 0.9)          # no uptrend
    assert not qualifies(True, -0.12, 0.9)           # more than 10% below the high
    assert not qualifies(True, -0.05, 0.5)           # not in the top 40%
    assert not qualifies(True, -0.10, 0.9)           # exactly 10% below is not "within"


def _snap(**kw):
    base = {"close": 10.0, "chg1d": 0.01, "ret20": 0.05, "ret60": 0.1, "ret120": 0.2, "rs120": 0.1, "dist52": -0.03, "ext20": 0.02,
            "trendup": True, "above50": True, "turn": 5_000_000.0}
    base.update(kw)
    return base


def test_build_rows_ranks_and_filters():
    snap = {"AAA.N0000": _snap(ret120=0.60), "BBB.N0000": _snap(ret120=0.30), "CCC.N0000": _snap(ret120=0.05, dist52=-0.30, trendup=False),
            "DDD.N0000": _snap(ret120=0.50, turn=100_000.0),                                  # too illiquid to rank
            "EEE.N0000": _snap(ret120=0.20, dist52=-0.02)}
    rows = screener.build_rows(snap, {"AAA.N0000": "Alpha"}, {"BBB.N0000"}, 2_000_000, 0.08)
    syms = [r["symbol"] for r in rows]
    assert "DDD.N0000" not in syms                                       # liquidity cut
    by = {r["symbol"]: r for r in rows}
    assert by["AAA.N0000"]["qualifies"] and by["AAA.N0000"]["name"] == "Alpha"
    assert by["BBB.N0000"]["on_watchlist"] and not by["AAA.N0000"]["on_watchlist"]
    assert not by["CCC.N0000"]["qualifies"] and len(by["CCC.N0000"]["missing"]) == 3 - by["CCC.N0000"]["hits"]
    assert syms[0] == "AAA.N0000"                                         # qualifiers first, strongest first
    assert rows[-1]["symbol"] == "CCC.N0000"
    assert all(r["qualifies"] for r in rows[:2]) or rows[0]["qualifies"]


def test_build_rows_skips_stocks_without_enough_history():
    snap = {"AAA.N0000": _snap(), "NEW.N0000": _snap(ret120=float("nan"))}
    rows = screener.build_rows(snap, {}, set(), 1_000_000, None)
    assert [r["symbol"] for r in rows] == ["AAA.N0000"]
    assert rows[0]["rs120"] == pytest.approx(0.1)                          # NaN-free JSON values


# ----------------------------------------------------------------------------------------------- journal
@pytest.fixture()
def db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    with S() as s:
        yield s


def _prices(s, symbol: str, closes: list[float], start=date(2026, 1, 5)):
    d, rows = start, []
    for c in closes:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        rows.append(PriceDaily(symbol=symbol, date=d, open=c, high=c * 1.005, low=c * 0.995, close=c, volume=1e5, source="eod"))
        d += timedelta(days=1)
    s.add_all(rows)
    s.flush()
    return [r.date for r in rows]


def _analysis(s, symbol: str, d: date, verdict="BUY", zh=10.2, stop=9.5, t1=11.0):
    plan = {"setup": "pullback", "triggered": True, "actionable": True, "reward_risk": 2.1, "buy_zone": {"low": 9.9, "high": zh}, "ideal": 10.0,
            "trigger": None, "stop": stop, "targets": [{"price": t1}, {"price": t1 + 0.5}]}
    rec = {"original": {"verdict": verdict, "plan": plan, "close": 10.0}, "analyst": {"verdict": "AVOID", "plan": None},
           "smc": {"verdict": "WATCH", "plan": {**plan, "setup": "order_block", "triggered": False}, "close": 10.0}, "close": 10.0}
    a = Analysis(symbol=symbol, date=d, verdict=verdict, score=70, patterns=[], levels={}, liquidity={}, trend={}, recommendation=rec)
    s.add(a)
    s.flush()
    return a


def test_record_once_and_never_overwrite(db):
    ds = _prices(db, "AAA.N0000", [10.0] * 80)
    a = _analysis(db, "AAA.N0000", ds[10])
    assert journal.record(db, "AAA.N0000", a) == 2                       # BUY (original) and WATCH (smc); AVOID has no entry
    a.recommendation["original"]["verdict"] = "WATCH"                      # a later re-analysis must not rewrite history
    assert journal.record(db, "AAA.N0000", a) == 0
    rows = db.scalars(select(JournalEntry)).all()
    assert {(r.engine, r.verdict) for r in rows} == {("original", "BUY"), ("smc", "WATCH")}


def test_score_target_hit_and_horizons(db):
    closes = [10.0] * 11 + [10.0, 10.3, 10.6, 11.2, 11.3] + [11.0] * 70      # the target (11.0) is touched on day 4 after the call
    ds = _prices(db, "AAA.N0000", closes)
    a = _analysis(db, "AAA.N0000", ds[10])
    journal.record(db, "AAA.N0000", a)
    assert journal.score_pending(db) == 2
    je = db.scalar(select(JournalEntry).where(JournalEntry.engine == "original"))
    o = je.outcome
    assert o["entered"] and o["entry_px"] == pytest.approx(10.0)
    assert o["plan"] == "target" and o["plan_net"] > 0
    assert o["r5"] == pytest.approx((11.3 * (1 - 0.0132) / (10.0 * 1.0132) - 1) * 100, abs=0.05)
    assert o["r20"] is not None and o["r60"] is not None and je.scored_at is not None


def test_score_skips_entry_above_the_zone_and_keeps_partial_open(db):
    closes = [10.0] * 11 + [10.8] * 12                                          # next open 10.8 is above the zone (10.2)
    ds = _prices(db, "AAA.N0000", closes)
    a = _analysis(db, "AAA.N0000", ds[10])
    journal.record(db, "AAA.N0000", a)
    journal.score_pending(db)
    je = db.scalar(select(JournalEntry).where(JournalEntry.engine == "original"))
    assert je.outcome["entered"] is False and je.scored_at is not None
    # a call with too little history after it stays pending
    ds2 = _prices(db, "BBB.N0000", [10.0] * 15)
    b = _analysis(db, "BBB.N0000", ds2[12])
    journal.record(db, "BBB.N0000", b)
    journal.score_pending(db)
    jb = db.scalar(select(JournalEntry).where(JournalEntry.symbol == "BBB.N0000", JournalEntry.engine == "original"))
    assert jb.outcome is None or jb.scored_at is None


def test_summary_groups_and_reference(db):
    closes = [10.0] * 11 + [10.0, 10.3, 10.6, 11.2, 11.3] + [11.0] * 70
    ds = _prices(db, "AAA.N0000", closes)
    journal.record(db, "AAA.N0000", _analysis(db, "AAA.N0000", ds[10]))
    journal.score_pending(db)
    out = journal.summary(db)
    g = {x["group"]: x for x in out["groups"]}
    assert g["original BUY"]["entered"] == 1 and g["original BUY"]["target_first"] == 100.0
    assert g["original BUY"]["reference"]["n"] == 98                          # the back-test number to compare against
    assert out["total"] == 2 and out["recent"][0]["symbol"] == "AAA.N0000"


# ----------------------------------------------------------------------------------------------- exit warnings
def _pat(typ, direction="bearish", status="confirmed", age=2, cat="chart"):
    return {"type": typ, "direction": direction, "status": status, "category": cat, "name": typ.replace("_", " ").title(), "context": {"age_bars": age}, "id": typ}


def test_exit_warning_only_for_patterns_with_a_measured_bearish_edge():
    w = portfolio.exit_warnings([_pat("descending_triangle"), _pat("double_top"), _pat("bearish_engulfing", cat="candlestick"),
                                 _pat("descending_triangle", age=30), _pat("rising_wedge", direction="bullish")], None)
    assert [x["id"] for x in w] == ["descending_triangle"]                      # weak edge yes; no-edge / candlestick / stale / bullish no
    assert "Track record" in w[0]["text"]


def test_trend_exit_warning():
    te = portfolio.trend_exit([10.0, 9.5], [9.8, 9.9])
    assert te["below"] and te["crossed_today"]
    assert not portfolio.trend_exit([10.0, 10.5], [9.8, 9.9])["below"]
    assert portfolio.exit_warnings([], None) == []
    assert portfolio.exit_warnings([], te)[0]["kind"] == "trend"
    assert portfolio.trend_exit([1.0], [1.0]) is None


# ----------------------------------------------------------------------------------------------- regime, backup
def test_regime_banner_states():
    up = pd.DataFrame({"aspi": np.linspace(8000, 12000, 300)})
    dn = pd.DataFrame({"aspi": np.linspace(12000, 8000, 300)})
    assert regime_banner(up, 0.7)["state"] == "risk_on"
    assert regime_banner(dn, 0.2)["state"] == "risk_off"
    assert regime_banner(up, 0.3)["state"] == "mixed"
    assert regime_banner(pd.DataFrame({"aspi": [1.0] * 50}))["state"] == "unknown"
    assert "no engine uses this" in regime_banner(up, 0.7)["note"]


def test_backup_copies_and_prunes(tmp_path, monkeypatch):
    import sqlite3
    from cse_advisor import config, db, jobs
    src = tmp_path / "live.db"
    c = sqlite3.connect(src)
    c.execute("create table t (x integer)")
    c.execute("insert into t values (42)")
    c.commit()
    c.close()
    monkeypatch.setattr(db, "engine", create_engine(f"sqlite:///{src.as_posix()}"))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    bdir = tmp_path / "backups"
    bdir.mkdir()
    for i in range(20):
        (bdir / f"cse_advisor_2020-01-{i + 1:02d}.db").write_bytes(b"x")
    out = jobs.backup_db()
    files = sorted(bdir.glob("cse_advisor_*.db"))
    assert len(files) == 14 and out["file"] in [f.name for f in files]
    got = sqlite3.connect(bdir / out["file"]).execute("select x from t").fetchone()
    assert got == (42,)
