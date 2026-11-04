"""The 1-minute timeframe and the 'next report expected' dates shown beside the report links."""
from datetime import date

import pandas as pd

from cse_advisor import intraday as it
from cse_advisor.analysis.events import annual_due, annual_year_end, results_due
from cse_advisor.tv_client import RESOLUTION


# ---------------------------------------------------------------- 1 minute
def test_one_minute_is_a_chart_timeframe_with_its_own_higher_timeframe():
    assert it.is_intraday("1m") and "1m" in it.INTRADAY and RESOLUTION["1m"] == "1"
    assert it.HTF["1m"] == "5m" and it.LABEL["1m"] == "1m"


def test_one_minute_buckets_are_the_minutes_themselves():
    ts = pd.DatetimeIndex(["2026-11-03 09:30", "2026-11-03 09:31", "2026-11-03 14:29", "2026-11-03 14:45"])
    assert [str(x)[11:16] for x in it.bucket(ts, "1m")] == ["09:30", "09:31", "14:29", "14:29"]   # the post-close print folds in
    df = pd.DataFrame({"open": [10.0, 10.1], "high": [10.2, 10.3], "low": [9.9, 10.0], "close": [10.1, 10.2], "volume": [5.0, 7.0]},
                      index=pd.DatetimeIndex(["2026-11-03 09:30", "2026-11-03 09:31"]))
    assert it.resample(df, "1m").equals(it.resample(df, "1m")) and len(it.resample(df, "1m")) == 2


def test_the_forming_one_minute_bar_is_dropped_during_the_session():
    from datetime import datetime
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
                      index=pd.DatetimeIndex(["2026-11-03 10:00", "2026-11-03 10:01"]))
    assert len(it.completed(df, "1m", now=datetime(2026, 11, 3, 10, 1, 30))) == 1
    assert len(it.completed(df, "1m", now=datetime(2026, 11, 3, 10, 5))) == 2


# ---------------------------------------------------------------- report dates
def test_annual_year_end_comes_from_the_title_when_cse_dates_it_by_upload_day():
    # JKH: "Annual Report as at 31st March 2026" is dated 26 May (the upload day) by CSE
    assert annual_year_end("Annual Report as at 31st March 2026", date(2026, 5, 26), date(2026, 5, 26)) == date(2026, 3, 31)
    # no date in the title: CSE's own date is the year end
    assert annual_year_end("Annual Report 2024/25", date(2025, 3, 31), date(2025, 5, 27)) == date(2025, 3, 31)
    assert annual_year_end("Annual Report 2024", date(2024, 12, 31), date(2025, 3, 5)) == date(2024, 12, 31)


def test_next_annual_report_follows_the_companys_own_lag():
    rows = [("Annual Report 2022/23", date(2023, 3, 31), date(2023, 5, 23)), ("Annual Report 2023/24", date(2024, 3, 31), date(2024, 5, 21)),
            ("Annual Report 2024/25", date(2025, 3, 31), date(2025, 5, 27)), ("Annual Report as at 31st March 2026", date(2026, 5, 26), date(2026, 5, 26))]
    d = annual_due(rows, date(2026, 11, 4))
    assert d["period_end"] == "2027-03-31" and d["status"] == "expected" and d["year_ended"] is False
    assert d["history_lags"] == [53, 51, 57, 56] and d["lag_days"] == 54 and d["expected"] == "2027-05-24"


def test_errata_and_audited_statements_do_not_count_as_the_annual_report():
    rows = [("Annual Report 2023/24", date(2024, 3, 31), date(2024, 8, 27)), ("Annual Report 2024/25", date(2025, 3, 31), date(2025, 8, 29)),
            ("Errata to the Annual Report 2024/25", date(2025, 3, 31), date(2025, 9, 11)),
            ("Audited Financial Statements for the Period ended 31st March 2026", date(2026, 3, 31), date(2026, 4, 30))]
    d = annual_due(rows, date(2025, 12, 1))
    assert d["period_end"] == "2026-03-31" and d["history_lags"] == [149, 151]


def test_a_changed_year_end_starts_over_and_says_it_is_rough():
    rows = [("Annual Report 2023", date(2023, 12, 31), date(2024, 6, 3)), ("Annual Report 2024", date(2024, 12, 31), date(2025, 6, 6)),
            ("Annual Report as at 31st March 2026", date(2026, 3, 31), date(2026, 6, 5))]
    d = annual_due(rows, date(2026, 11, 4))
    assert d["period_end"] == "2027-03-31" and d["history_lags"] == [66] and "rough guide" in d["basis"]


def test_an_annual_report_past_its_date_is_overdue_and_no_history_gives_nothing():
    rows = [("Annual Report 2022", date(2022, 12, 31), date(2023, 3, 6)), ("Annual Report 2023", date(2023, 12, 31), date(2024, 3, 5))]
    assert annual_due(rows, date(2025, 4, 1))["status"] == "overdue"
    assert annual_due([], date(2025, 4, 1)) is None


def test_next_quarterly_report_estimate_is_unchanged():
    pairs = [(date(2025, 12, 31), date(2026, 1, 28)), (date(2026, 3, 31), date(2026, 5, 26)), (date(2026, 6, 30), date(2026, 7, 28)),
             (date(2026, 9, 30), date(2026, 11, 3))]
    d = results_due(pairs, date(2026, 11, 4))
    assert d["period_end"] == "2026-12-31" and d["lag_days"] == 31 and d["expected"] == "2027-01-31"   # median of 28, 56, 28, 34 days


def test_cse_and_tradingview_can_both_hold_the_same_minute():
    from datetime import datetime
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from cse_advisor.db import Base, PriceIntraday
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    row = dict(symbol="JKH.N0000", interval="1m", ts=datetime(2026, 11, 4, 10, 0), open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0)
    with Session(eng) as s:
        s.add_all([PriceIntraday(**row, source="cse"), PriceIntraday(**row, source="tv")])
        s.commit()
        assert s.query(PriceIntraday).count() == 2
