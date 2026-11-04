"""Client for the (unofficial, undocumented) JSON endpoints used by www.cse.lk.

Verified 2026-11-02. These endpoints can change without notice, so every call
goes through this one module: if CSE changes something, only this file needs
fixing. Requests are rate-limited and kept to a minimum (personal use).
"""
from __future__ import annotations

import threading
import time
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

BASE = "https://www.cse.lk/api/"
CDN = "https://cdn.cse.lk/"
TZ = ZoneInfo("Asia/Colombo")
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (personal CSE advisor; low-frequency)",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json",
}


class CSEError(RuntimeError):
    pass


class CSEClient:
    def __init__(self, min_interval: float = 1.0, timeout: float = 30.0):
        self._client = httpx.Client(headers=_HEADERS, timeout=timeout)
        self._min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()

    def _post(self, endpoint: str, data: dict[str, Any] | None = None, retries: int = 3) -> Any:
        last_exc: Exception | None = None
        for attempt in range(retries):
            with self._lock:
                wait = self._min_interval - (time.monotonic() - self._last)
                if wait > 0:
                    time.sleep(wait)
                self._last = time.monotonic()
            try:
                r = self._client.post(BASE + endpoint, data=data or {})
                r.raise_for_status()
                payload = r.json()
                if isinstance(payload, dict) and "apierror" in payload:
                    raise CSEError(f"{endpoint}: {payload['apierror'].get('message')}")
                return payload
            except (httpx.HTTPError, ValueError) as exc:
                last_exc = exc
                time.sleep(2 * (attempt + 1))
        raise CSEError(f"{endpoint} failed after {retries} attempts: {last_exc}")

    # ---- market-wide -------------------------------------------------------
    def trade_summary(self) -> list[dict[str, Any]]:
        """Today's OHLC, volume, turnover, trades for every traded security."""
        return self._post("tradeSummary").get("reqTradeSummery", [])

    def market_summary(self) -> dict[str, Any]:
        return self._post("marketSummery")

    def aspi(self) -> dict[str, Any]:
        return self._post("aspiData")

    def spsl20(self) -> dict[str, Any]:
        return self._post("snpData")

    # Chart `period` codes (verified 2026-11-03), same for chartData and companyChartDataByStock:
    #   1 = today intraday, 2 = 1 week, 3 = 1 month, 4 = 3 months, 5 = 1 year (daily). There is no
    #   intraday history: period 1 is today's session only (until the next session starts).
    def aspi_history(self) -> list[dict[str, Any]]:
        """~1 year of daily ASPI closes: [{d: ms, v: value, pc: %chg}]."""
        return self._post("chartData", {"chartId": 1, "period": 5})

    def aspi_intraday(self) -> list[dict[str, Any]]:
        """Today's ASPI about once a minute: [{d: ms, v: value}]."""
        return self._post("chartData", {"chartId": 1, "period": 1}) or []

    def market_status(self) -> str:
        """'Market Open' / 'Market Closed' (also covers Poya days and other closures)."""
        return str(self._post("marketStatus", retries=1).get("status") or "")

    def daily_market_summary(self) -> list[dict[str, Any]]:
        """Last ~2 sessions: foreign purchases/sales, trades, market PER/PBV/DY, market cap."""
        rows = self._post("dailyMarketSummery") or []
        out = []
        for r in rows:   # returned as [[{...}], [{...}]]
            out += r if isinstance(r, list) else [r]
        return out

    def all_sectors(self) -> list[dict[str, Any]]:
        """Today's S&P/CSE sector indices (22)."""
        return self._post("allSectors") or []

    def non_compliance(self) -> list[dict[str, Any]]:
        """Market-wide non-compliance / modified-audit-opinion notices (company name, not symbol)."""
        return self._post("getNonComplianceAnnouncements").get("nonComplianceAnnouncements", [])

    def financial_announcements(self) -> list[dict[str, Any]]:
        """Market-wide list of recently uploaded financial statements (short symbol, e.g. 'KVAL')."""
        return self._post("getFinancialAnnouncement").get("reqFinancialAnnouncemnets", [])

    def movers(self) -> dict[str, list[dict[str, Any]]]:
        """Top 10 gainers, losers and most active (by trades) today."""
        return {"gainers": self._post("topGainers") or [], "losers": self._post("topLooses") or [],
                "active": self._post("mostActiveTrades") or []}

    # ---- per company -------------------------------------------------------
    def company_info(self, symbol: str) -> dict[str, Any]:
        return self._post("companyInfoSummery", {"symbol": symbol})

    def daily_history(self, cse_id: int) -> list[dict[str, Any]]:
        """~1 year of daily bars (period 5). Fields: t (ms), h, l, p (close), q (volume). No open."""
        return self._post("companyChartDataByStock", {"stockId": cse_id, "period": 5}).get("chartData", [])

    def intraday_trades(self, cse_id: int) -> list[dict[str, Any]]:
        """Today's individual trades (period 1; every trade incl. crossings): t (ms), p (price), q (qty)."""
        return self._post("companyChartDataByStock", {"stockId": cse_id, "period": 1}).get("chartData", [])

    def announcements(self, symbol: str) -> list[dict[str, Any]]:
        """Recent corporate announcements (category, createdDate ms, remarks, xd/payment dates)."""
        return self._post("getAnnouncementByCompany", {"symbol": symbol}).get("reqCompanyAnnouncement", [])

    def announcement_typed(self, announcement_id: int) -> dict[str, Any]:
        """Structured announcement (found in cse.lk's own site code, 2026-11-04). `reqBaseAnnouncement.dType`
        says what it is, with typed fields:
        - CashDividendWithDates: votingDivPerShare, xd, payment, agm, recordDate (ms), financialYear, firstAndFinal
        - ScripDividendWithDates: votingPropotion, xd, payment, agm
        - DealingsByDirectors: dircetorsName [sic], natureOfDir, directorTransactions[{transType 'Acquisition' |
          'Disposal', transactionDate, quantity, price}]
        - AppointmentOfDirectors: dirList[{dirName, natureOfDir, effectiveDate}]; AgmInitial: dateOfAgm
        Generic 'Corporate Disclosure' notices come back empty here: use announcement_detail for those."""
        try:
            return self._post("getAnnouncementById", {"announcementId": announcement_id}, retries=2) or {}
        except CSEError:
            return {}

    def announcement_detail(self, announcement_id: int) -> dict[str, Any]:
        """Full announcement: XR/XD dates, proportion text, record/allotment dates and attached documents."""
        try:
            return self._post("getGeneralAnnouncementById", {"announcementId": announcement_id}, retries=1) or {}
        except CSEError:
            return {}

    def document_text(self, file_url: str, max_bytes: int = 3_000_000, max_pages: int = 6) -> str:
        """Download a CSE PDF (announcement attachment or financial report) from the CDN and return the text
        of its first `max_pages` pages ('' if unreadable, too big, or a scanned image)."""
        import io
        try:
            from pypdf import PdfReader
        except ImportError:  # optional dependency
            return ""
        with self._lock:
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
        try:
            r = self._client.get("https://cdn.cse.lk/" + file_url.lstrip("/"))
            r.raise_for_status()
        except httpx.HTTPError:
            return ""
        if len(r.content) > max_bytes:
            return ""
        try:
            return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(r.content)).pages[:max_pages])
        except Exception:
            return ""

    def financials(self, symbol: str) -> dict[str, Any]:
        return self._post("financials", {"symbol": symbol})


def ms_to_local(ms: int | float) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=TZ)


def ms_to_date(ms: int | float) -> date:
    return ms_to_local(ms).date()


def now_local() -> datetime:
    return datetime.now(TZ)


_client: CSEClient | None = None


def client() -> CSEClient:
    global _client
    if _client is None:
        _client = CSEClient()
    return _client
