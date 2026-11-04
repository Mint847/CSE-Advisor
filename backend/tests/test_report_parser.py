"""Statement parsing on short excerpts of real CSE interim-statement layouts (public filings, Nov 2026)."""
from datetime import date

import pytest

from cse_advisor.analysis.report_parser import cross_check, data_row, extract_figures, period_from_title

# JKH: quarter block first, then six months; note references; Rs '000; EPS rows without a % column
JKH = """=== PAGE 9 ===
CONSOLIDATED INCOME STATEMENT
Quarter ended 30 September Six months ended 30 September
  Note 2026 2025 % 2026 2025 %
Group revenue at Rs.142.09 billion for the quarter under review is a decrease of 2%
Total revenue  142,088,699  144,755,228 (2)  283,736,159  258,908,422 10
Results from operating activities  7,150,132  10,519,221 (32)  14,518,959  15,825,111 (8)
Finance cost 7  (5,127,298)  (6,217,477) (18)  (13,251,663)  (11,923,836) 11
Profit for the period  5,823,132  4,201,492 39  6,596,357  4,918,921 34
Attributable to:
Equity holders of the parent  5,170,314  1,652,806 213  5,232,529  849,111 516
Non-controlling interest  652,818  2,548,686 (74)  1,363,828  4,069,810 (66)
 Rs.  Rs.  Rs.  Rs.
Earnings per share
Basic  0.29  0.09  0.30  0.05
Dividend per share 11  -  -  0.10  0.05
Note : All values are in Rs '000s, unless otherwise stated.
=== PAGE 11 ===
As at  30.09.2026  31.03.2026
 Total assets  943,358,863  929,123,301
 Total equity  443,493,054  439,705,535
 Net assets per share  23.60  23.38
Note : All values are in Rs '000s, unless otherwise stated.
"""

# SAMP: Bank before Group, six months before quarter, period line printed after the table
SAMP = """=== PAGE 6 ===
2026 2025  Change  2026 2025  Change  2026 2025 Change 2026 2025 Change
Rs 000  Rs 000  % Rs 000 Rs 000  % Rs 000 Rs 000 % Rs 000 Rs 000 %
Net interest income 42,797,119 38,585,118 11 22,660,772 19,459,895 16 46,429,833 41,324,930 12 24,594,175 20,868,000 18
Total operating income 63,250,388 54,273,202 17 34,781,770 26,449,715 32 68,134,271 58,040,115 17 37,332,091 28,401,400 31
Profit for the period 16,628,598 14,695,908 13 10,451,502 6,353,954 64 17,859,576 15,627,459 14 11,029,731 6,702,721 65
Attributable to:
Equity holders of the Bank 16,628,598 14,695,908 13 10,451,502 6,353,954 64 17,859,576 15,627,459 14 11,029,731 6,702,721 65
Earnings per share : Basic/Diluted (Rs) 14.18 12.53 13 8.91 5.42 64 15.23 13.33 14 9.41 5.72 65
Bank Group
INCOME STATEMENT
For the six months ended 30th June For the quarter ended 30th June For the six months ended 30th June For the quarter ended 30th June
"""

# RIL: Group then Company, three months only; owners' share follows a heading, comprehensive income below
RIL = """=== PAGE 2 ===
Three months ended 30th June,
30 Jun 2026 30 Jun 2025 30 Jun 2026 30 Jun 2025
LKR'000 LKR'000 LKR'000 LKR'000
Revenue 14,662,023             7,282,445            474,231                 381,386
Profit for the period 1,162,876               577,282               421,619                 395,461
Total comprehensive income for the period, net of tax 1,162,889               584,583               421,619                 395,461
Profit/(loss) for the period attributable to:
Owners of the parent 684,023                  410,170               421,619                 395,461
Non-controlling interest 478,853                  167,112               -                          -
Total comprehensive income for the period attributable to:
Owners of the parent 684,029                  413,894               421,619                 395,461
Earnings per share (EPS ) LKR 0.86                         0.51                      0.53                        0.49
Group Company
"""


def test_period_from_title():
    assert period_from_title("Interim Financial Statements for the Quarter ended 30th September 2026") == (date(2026, 9, 30), "Q")
    assert period_from_title("Interim Financial Statements for the Quarter ended 31st March 2026 (Extractable Version)") == (date(2026, 3, 31), "Q")


def test_commentary_is_not_a_data_row():
    assert data_row("Total operating income for the six months grew by 17.85% to Rs. 109.07 billion, and by 26.55% to Rs. 58.23") is None
    assert data_row("Dividend per share 11  -  -  0.10  0.05")[1] == ["-", "-", "0.10", "0.05"]     # note ref 11 dropped
    assert data_row("2,320,958,647  762,961,136     Total assets 2,457,499,811  2,096,013,411") is None   # uncertain column order


def test_jkh_layout_quarter_first():
    r = extract_figures(JKH, "Interim Financial Statements for the Quarter ended 30th September 2026")
    f = r["fields"]
    assert r["column"] == "Group · quarter"
    assert f["revenue"] == pytest.approx(142_088_699e3) and f["net_profit"] == pytest.approx(5_170_314e3)
    assert f["operating_profit"] == pytest.approx(7_150_132e3) and f["finance_cost"] == pytest.approx(5_127_298e3)
    assert f["eps"] == 0.29 and f["total_assets"] == pytest.approx(943_358_863e3) and f["nav_per_share"] == 23.60
    assert r["prior"]["net_profit"] == pytest.approx(1_652_806e3)          # same quarter last year: +213%
    c = cross_check(r, 17_741e6)
    assert not c["needs_review"] and all("✓" in x for x in c["checks"])


def test_samp_layout_bank_before_group():
    r = extract_figures(SAMP, "Interim Financial Statements for the Quarter ended 30th June 2026")
    assert r["bank"] and r["column"] == "Group · quarter"
    assert r["fields"]["net_profit"] == pytest.approx(11_029_731e3)       # Group quarter, not the Bank's six months
    assert r["fields"]["revenue"] == pytest.approx(37_332_091e3)          # banks: total operating income
    assert r["fields"]["eps"] == 9.41 and r["prior"]["eps"] == 5.72


def test_ril_owners_share_not_total_profit():
    r = extract_figures(RIL, "Interim Financial Statements for the Quarter ended 30th June 2026")
    assert r["fields"]["net_profit"] == pytest.approx(684_023e3)          # owners of the parent, not 1,162,876 incl. NCI
    assert r["fields"]["eps"] == 0.86 and r["fields"]["revenue"] == pytest.approx(14_662_023e3)
    assert not cross_check(r, 800e6)["needs_review"]


def test_mismatch_needs_review():
    r = extract_figures(RIL, "Interim Financial Statements for the Quarter ended 30th June 2026")
    c = cross_check(r, 2_000e6)            # wrong share count: EPS x shares no longer matches profit
    assert c["needs_review"] and any("mismatch" in x for x in c["checks"])
    assert cross_check(extract_figures("scanned page", "Quarter ended 30th June 2026"), 800e6)["needs_review"]
