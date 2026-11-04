"""Catalysts: typed announcement facts, price reactions, the results calendar, headlines and the scorecard."""
from datetime import date, datetime

import pandas as pd
import pytest

from cse_advisor.analysis.events import results_due, risk_lines, upcoming
from cse_advisor.analysis.news import attachment_subject, classify_announcement, event_text, news_score, parse_typed
from cse_advisor.analysis.reactions import first_session, reaction, stats
from cse_advisor.catalysts import scorecard
from cse_advisor.news_feeds import match, matchers, parse_rss, tone

# shapes as returned by cse.lk getAnnouncementById (SAMP, 2026)
DIV = {"dType": "CashDividendWithDates", "votingDivPerShare": 10.3, "xd": "31 Mar 2026", "payment": "23 Apr 2026",
       "agm": "30 Mar 2026", "recordDate": 1775068200000, "financialYear": "2025", "firstAndFinal": True}
DEAL = {"dType": "DealingsByDirectors", "dircetorsName": "Mr.D A De Vas Gunasekara", "natureOfDir": "Executive Director / CFO",
        "accTypeOwnAcc": True, "directorTransactions": [{"transType": "Acquisition", "transactionDate": "25 Feb 2026", "quantity": 10000.0, "price": 161}]}


def test_typed_records():
    d = parse_typed(DIV)
    assert d["kind"] == "cash_dividend" and d["dps"] == 10.3 and d["xd"] == "2026-03-31" and d["payment"] == "2026-04-23"
    assert "Rs 10.3 per share" in event_text(d)
    x = parse_typed(DEAL)
    assert x["kind"] == "dealing" and x["net_value"] == 1_610_000 and x["trades"][0]["side"] == "buy"
    assert event_text(x).startswith("Acquisition by Mr.D A De Vas Gunasekara")
    a = parse_typed({"dType": "AppointmentOfDirectors", "dirList": [{"dirName": "Mr. A Silva ", "natureOfDir": "Non-Executive", "effectiveDate": "28 Oct 2026"}]})
    assert a["directors"][0] == {"name": "Mr. A Silva", "role": "Non-Executive", "date": "2026-10-28"}


def test_attachment_names_the_real_subject():
    s = attachment_subject("cmt/announcement_portal_prod/Letter to CSE - Appointment of Duputy Chairman - Dilip Wijeyeratne_11906678805790032.pdf")
    assert s == "Letter to CSE - Appointment of Duputy Chairman - Dilip Wijeyeratne"
    assert classify_announcement("Corporate Disclosure", s)[0] == "management_change"
    assert classify_announcement("Corporate Disclosure", None)[0] == "disclosure"
    assert classify_announcement("Corporate Disclosure", "Disposal of a subsidiary")[0] == "disposal"


def test_director_selling_needs_a_real_net_sale():
    asof = datetime(2026, 3, 10)
    small = {"date": datetime(2026, 3, 1), "title": "Dealings By Directors", "category": "director_dealing", "sentiment": -0.4,
             "materiality": 3, "event": {"net_value": -200_000}}
    big = {**small, "event": {"net_value": -5_000_000}}
    assert not [f for f in news_score([small], asof)["red_flags"] if f["category"] == "director_selling"]
    assert [f for f in news_score([big], asof)["red_flags"] if f["category"] == "director_selling"]


def _closes():
    idx = pd.bdate_range("2026-03-02", periods=12)
    return pd.Series([100, 100, 100, 104, 105, 106, 107, 108, 108, 108, 108, 108], index=idx, dtype=float)


def test_reaction_counts_from_the_next_session_after_the_close():
    c = _closes()
    assert c.index[first_session(c, datetime(2026, 3, 5, 10, 0))].date() == date(2026, 3, 5)     # during the session
    assert c.index[first_session(c, datetime(2026, 3, 4, 16, 30))].date() == date(2026, 3, 5)    # after 14:30
    aspi = pd.Series(1000.0, index=c.index)
    r = reaction(c, aspi, datetime(2026, 3, 4, 16, 30))
    assert r["r0"] == 4.0 and r["x0"] == 4.0 and r["r5"] == 8.0 and r["complete"]


def test_stats_say_when_there_are_too_few():
    ev = [{"kind": "results", "reaction": {"r0": 1.0, "r5": 2.0, "x5": 1.5, "complete": True}}] * 3
    row = stats(ev)[0]
    assert row["n"] == 3 and not row["readable"] and "too few" in row["note"]
    many = stats([{"kind": "results", "reaction": {"r0": 1.0, "r5": 2.0, "x5": 1.5, "complete": True}}] * 9)[0]
    assert many["readable"] and many["median_5d_excess_pct"] == 1.5 and many["pct_positive_5d"] == 100


def test_results_due_uses_the_company_lag_and_ignores_bad_dates():
    reps = [(date(2025, 9, 30), date(2025, 11, 12)), (date(2025, 12, 31), date(2026, 2, 26)), (date(2026, 3, 31), date(2026, 5, 14)),
            (date(2026, 6, 30), date(2026, 8, 13)), (date(2025, 6, 30), date(2025, 6, 30))]          # last one: 0-day lag, ignored
    due = results_due(reps, date(2026, 11, 4))
    assert due["period_end"] == "2026-09-30" and due["history_lags"] == [43, 57, 44, 44]
    # only one earlier September report, so the median of all four lags (44 days) is used
    assert due["lag_days"] == 44 and due["expected"] == "2026-11-13" and due["days"] == 9 and due["status"] == "expected"
    late = results_due(reps, date(2026, 11, 20))
    assert late["status"] == "overdue"


def test_calendar_and_risk_lines():
    today = date(2026, 3, 27)
    cal = upcoming(today, [parse_typed(DIV)], [], [], None, close=150.0)
    assert [e["kind"] for e in cal] == ["ex_dividend", "dividend_payment"]
    assert "yield 6.9%" in cal[0]["detail"]
    lines = risk_lines(today, cal, None)
    assert any("drops by about the dividend" in x for x in lines)


RSS = """<rss><channel><title>EconomyNext</title>
<item><title>John Keells Holdings Sept quarter net profit surges 213-pct to Rs5.2bn</title><link>https://economynext.com/a</link>
<pubDate>Tue, 03 Nov 2026 13:10:00 +0000</pubDate><description><![CDATA[<p>JKH profit</p>]]></description></item>
<item><title>John Keells Hotels occupancy falls</title><link>https://economynext.com/b</link><pubDate>Tue, 03 Nov 2026 10:00:00 +0000</pubDate></item>
<item><title>Combined bank deposits grow</title><link>https://economynext.com/c</link><pubDate>Tue, 03 Nov 2026 09:00:00 +0000</pubDate></item>
</channel></rss>"""


def test_headlines_match_full_names_and_tickers_only():
    items = parse_rss(RSS, "EconomyNext")
    assert len(items) == 3 and items[0]["published"] == datetime(2026, 11, 3, 18, 40)          # Colombo time
    own = {"JKH.N0000": "JOHN KEELLS HOLDINGS PLC", "COMB.N0000": "COMMERCIAL BANK OF CEYLON PLC"}
    pats = matchers(list(own.items()))
    listed = ["JOHN KEELLS HOLDINGS PLC", "JOHN KEELLS HOTELS PLC", "COMMERCIAL BANK OF CEYLON PLC"]
    assert match(items[0]["title"], pats, own, listed) == ["JKH.N0000"]
    assert match(items[1]["title"], pats, own, listed) == []            # John Keells Hotels is another company
    assert match(items[2]["title"], pats, own, listed) == []            # 'Combined' is not the ticker COMB
    assert tone(items[0]["title"]) > 0 and tone(items[1]["title"]) < 0


def test_scorecard_rollup():
    base = {"results": [{"period_end": "2026-09-30", "net_profit": 5.17e9, "profit_yoy_pct": 213.0, "source": "pdf", "needs_review": False}],
            "results_due": None, "dividends": {"ttm_yield_pct": 6.0}, "calendar": [], "insiders": {"d90": {"notices": 2, "buys": 2, "sells": 0, "net_value": 3e6}},
            "headlines": {"n30": 0}, "reaction_stats": [], "corporate": {}}
    sc = scorecard(base, {"news": {"score": 55}}, {})
    assert sc["overall"] == "Supportive" and sc["total"] >= 2
    flagged = scorecard(base, {"original": {"red_flags": [{"category": "red_flag", "date": "2026-10-01"}]}}, {})
    assert flagged["overall"] == "Cautious"                             # any hard red flag
