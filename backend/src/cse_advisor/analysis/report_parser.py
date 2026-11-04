"""Headline figures from CSE interim financial statements (text PDFs), by rules: no LLM.

Layouts differ by company (checked on JKH, COMB, SAMP, LOLC, KZOO, RIL in Nov 2026):
- column blocks: JKH "Quarter ended | Six months ended", COMB/SAMP "six months | quarter", RIL/LOLC
  "Group | Company" each "three months"; each block is (current, prior-year[, % change]);
- entities: SAMP prints Bank before Group, others Group before Company, some on separate pages;
- units: Rs '000 almost everywhere; EPS and NAV per share are in rupees;
- pypdf sometimes puts numbers before the label (LOLC's balance sheet): only numbers after it are used.

The parser picks the Group / current-quarter column, also returns the same quarter last year from the
comparative column, and cross-checks EPS x shares against profit and NAV x shares against equity.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
NUM = re.compile(r"(?<![\w.])\(?\d[\d,]*(?:\.\d+)?\)?(?![\w])|(?<!\S)-(?!\S)(?=\s*(?:[-(\d>]|$))|>100")
YEARISH = re.compile(r"\b20\d\d\b|\b\d{2}[./-]\d{2}[./-]20\d\d\b|\b\d{1,2}-[A-Za-z]{3}-\d{2}\b")
UNIT = re.compile(r"(?:Rs|LKR)\.?\s*'?\s*000|Rs\s000|'000s?", re.I)
PCT = re.compile(r"(?<!\S)(%|Change|Variance)(?!\S)", re.I)
Q_WORDS = re.compile(r"quarter\s+ended|three\s+months|3\s+months", re.I)
C_WORDS = re.compile(r"(six|nine|twelve)\s+months|half[\s-]+year|year\s+ended|cumulative", re.I)
ENTITY = re.compile(r"\b(Group|Consolidated|Company|Bank)\b")


def period_from_title(title: str) -> tuple[date | None, str]:
    """('Quarter ended 30th June 2026') -> (2026-06-30, 'Q'); annual reports -> 'FY'."""
    t = title or ""
    d = None
    # "30th June 2026" / "31st March, 2026" / "June 30,2025" / "March 31, 2025"
    for rx, order in ((r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+),?\s*(20\d\d)", "dmy"), (r"([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(20\d\d)", "mdy")):
        m = re.search(rx, t)
        if not m:
            continue
        day, mon = (m.group(1), m.group(2)) if order == "dmy" else (m.group(2), m.group(1))
        if mon.lower() in MONTHS:
            try:
                d = date(int(m.group(3)), MONTHS[mon.lower()], int(day))
                break
            except ValueError:
                d = None
    kind = "FY" if re.search(r"annual report|year ended", t, re.I) and not re.search(r"quarter", t, re.I) else "Q"
    return d, kind


def to_number(tok: str) -> float | None:
    if tok == "-":
        return 0.0
    if tok.startswith(">"):
        return None
    neg = tok.startswith("(") and tok.endswith(")")
    try:
        v = float(tok.strip("()").replace(",", ""))
    except ValueError:
        return None
    return -v if neg else v


def split_line(line: str) -> tuple[str, list[str]]:
    """(label, number tokens after the label). Numbers printed before the label are ignored."""
    first = None
    for m in NUM.finditer(line):
        before = line[:m.start()]
        if re.search(r"[A-Za-z]{3,}", before):
            first = m.start()
            break
    if first is None:
        return line.strip(), []
    label = line[:first].strip()
    return label, [m.group(0) for m in NUM.finditer(line[first:])]


def data_row(line: str) -> tuple[str, list[str]] | None:
    """A statement row: a label followed by numbers only. Commentary ("grew by 17.85% to Rs. 109 billion")
    and rows whose numbers were printed partly before the label (uncertain column order) are rejected."""
    label, toks = split_line(line)
    if len(toks) < 2 or len(label) > 110:
        return None
    if NUM.match(label):
        return None                                   # numbers printed before the label
    m = re.search(re.escape(label), line)
    rest = line[m.end():] if m else ""
    if re.search(r"[A-Za-z]{2,}", NUM.sub(" ", rest).replace("%", " ")):
        return None
    # a leading 1-2 digit integer before decimals / thousands is a note reference ("Dividend per share 11 0.10 0.05")
    if len(toks) >= 3 and re.fullmatch(r"\d{1,2}", toks[0]) and any(("," in t or "." in t) for t in toks[1:]):
        toks = toks[1:]
    return label, toks


class Table:
    """Column template of one statement: blocks of (current, prior[, %]) with period and entity."""

    def __init__(self, lines: list[str], at: int):
        self.n_cols, self.scale, self.blocks = 0, 1.0, []
        hdr = None
        for j in range(at, max(-1, at - 40), -1):                      # nearest header above the row
            ln = lines[j]
            if ln.startswith("=== PAGE"):
                break
            cols = max(len(YEARISH.findall(ln)), len(UNIT.findall(ln))) + len(PCT.findall(ln))
            if cols >= 2 and len(split_line(ln)[1]) < 2 or (cols >= 2 and YEARISH.search(ln) and not re.search(r"[a-z]{4,}\s+\d", ln)):
                hdr = j
                self.n_cols = cols
                self.has_pct = bool(PCT.search(ln))
                break
        lo, hi = max(0, at - 45), min(len(lines), at + 45)
        page = [x for x in lines[lo:hi]]
        if any(UNIT.search(x) for x in page):
            self.scale = 1000.0
        elif any(re.search(r"\b(Rs\.?|LKR)\s*(Mn|Million)\b", x, re.I) for x in page):
            self.scale = 1e6
        # period and entity order, from the best header / footer line of the same page
        self.periods: list[str] = []
        self.entities: list[str] = []
        for x in page:
            if x.startswith("=== PAGE"):
                continue
            seq = sorted([(m.start(), "Q") for m in Q_WORDS.finditer(x)] + [(m.start(), "C") for m in C_WORDS.finditer(x)])
            if len(seq) > len(self.periods):
                self.periods = [p for _, p in seq]
            ents = [("G" if m.group(1) in ("Group", "Consolidated") else "S") for m in ENTITY.finditer(x)]
            if len(ents) >= 2 and len(set(ents)) == 2 and len(x.split()) <= 6 and len(ents) > len(self.entities):
                self.entities = ents
        self.header = hdr

    def pick(self, n: int) -> tuple[int, int, str] | None:
        """For a row with n numbers: (index of current value, index of prior-year value, description)."""
        if n < 2:
            return None
        blocks = None
        for size in ((3, 2) if self.n_cols % 3 == 0 and getattr(self, "has_pct", False) else (2, 3)):
            if n % size == 0 and (self.n_cols == 0 or n in (self.n_cols, self.n_cols // 3 * 2 if size == 2 else self.n_cols)):
                blocks = n // size
                break
        if blocks is None:
            size = 3 if n % 3 == 0 and n >= 6 and getattr(self, "has_pct", False) else 2
            if n % size:
                return None
            blocks = n // size
        per = self.periods if len(self.periods) == blocks else (self.periods * blocks)[:blocks] if len(self.periods) == 1 else \
            (self.periods * (blocks // len(self.periods))) if self.periods and blocks % len(self.periods) == 0 else ["?"] * blocks
        ents = self.entities or ["G"]
        per_ent = max(1, blocks // len(ents)) if blocks % len(ents) == 0 else blocks
        ent = [ents[min(i // per_ent, len(ents) - 1)] for i in range(blocks)]
        order = sorted(range(blocks), key=lambda i: (ent[i] != "G", per[i] != "Q", i))
        b = order[0]
        desc = f"{'Group' if ent[b] == 'G' else 'Company/Bank'} · {'quarter' if per[b] == 'Q' else 'cumulative' if per[b] == 'C' else 'period'}"
        return b * size, b * size + 1, desc


FIELDS_PL = [
    # (field, label regex, kind) ; kind: 'money' scaled by units, 'per_share' not scaled
    ("revenue_bank", r"^total operating income\b", "money"),
    ("revenue", r"^(total revenue|revenue|turnover)\b(?!\s*(reserve|from contracts|from insurance))", "money"),
    ("operating_profit", r"^(results from operating activities|profit\s*/?\s*\(?loss\)?\s*from operat\w*|profit from operating activities|operating profit)\b", "money"),
    ("finance_cost", r"^(finance (cost|costs|expenses?))\b", "money"),
    ("profit_period", r"^profit\s*/?\s*(\(loss\))?\s*for the (period|quarter)\b", "money"),
    ("dividend_per_share", r"^dividend per share\b", "per_share"),
]
ATTRIB = re.compile(r"^(equity holders of the (parent|bank|company)|owners of the (parent|company)|shareholders of the (parent|company)|"
                    r"profit attributable to equity share ?holders)", re.I)
EPS_HDR = re.compile(r"(earnings|loss)\s*(/\s*\(?(loss|earnings)\)?)?\s*per\s*(ordinary\s*)?share", re.I)
FIELDS_BS = [
    ("total_assets", r"^total assets\b", "money"),
    ("equity_attrib", r"^(total )?equity attributable to (the )?(equity holders|owners|shareholders)", "money"),
    ("total_equity", r"^total equity\b(?! and)", "money"),
    ("total_liabilities", r"^total liabilities\b(?! and)", "money"),
    ("nav_per_share", r"^net assets? (value )?per share\b", "per_share"),
]


def _value(lines: list[str], i: int, kind: str, tables: dict[int, Table]) -> tuple[float, float | None, str] | None:
    row = data_row(lines[i].strip())
    if row is None:
        return None
    label, toks = row
    nums = [to_number(t) for t in toks]
    t = tables.setdefault(i, Table(lines, i))
    p = t.pick(len(nums))
    if p is None:
        return None
    cur, prior, desc = p
    sc = t.scale if kind == "money" else 1.0
    c = nums[cur]
    if c is None:
        return None
    pv = nums[prior] if prior < len(nums) else None
    return c * sc, (pv * sc if pv is not None else None), desc


def extract_figures(text: str, title: str = "") -> dict[str, Any]:
    lines = [ln.rstrip() for ln in text.splitlines()]
    period_end, ptype = period_from_title(title)
    fields: dict[str, float] = {}
    prior: dict[str, float] = {}
    evidence: dict[str, str] = {}
    desc_used: dict[str, str] = {}
    tables: dict[int, Table] = {}
    found_pl = None

    def take(name: str, i: int, kind: str) -> bool:
        if name in fields:
            return True
        got = _value(lines, i, kind, tables)
        if not got:
            return False
        fields[name], pv, desc_used[name] = got
        if pv is not None:
            prior[name] = pv
        evidence[name] = lines[i].strip()[:200]
        return True

    stripped = [ln.strip() for ln in lines]
    for i, ln in enumerate(stripped):
        low = ln.lower()
        for name, rx, kind in FIELDS_PL:
            if re.match(rx, low) and split_line(ln)[1]:
                if take(name, i, kind) and found_pl is None:
                    found_pl = i
        # profit attributable: the line after an "attributable to" heading, or a one-line form
        if ATTRIB.match(ln) and split_line(ln)[1] and "net_profit" not in fields:
            one_line = "attributable" in low
            heading = next((stripped[j].lower() for j in range(i - 1, max(-1, i - 4), -1) if "attributable" in stripped[j].lower()), None)
            if one_line or (heading is not None and "comprehensive" not in heading):
                take("net_profit", i, "money")
        if EPS_HDR.search(ln) and "eps" not in fields:
            for j in range(i, min(len(stripped), i + 4)):
                lab, toks = split_line(stripped[j])
                if len(toks) >= 2 and all(re.match(r"^\(?\d{1,4}(\.\d+)?\)?$|^-$", t) for t in toks[:2]):
                    if take("eps", j, "per_share"):
                        break
        for name, rx, kind in FIELDS_BS:
            if re.match(rx, low) and split_line(ln)[1]:
                take(name, i, kind)

    bank = "revenue_bank" in fields
    out_fields: dict[str, float | None] = {
        "revenue": fields.get("revenue_bank") if bank else fields.get("revenue"),
        "operating_profit": fields.get("operating_profit"),
        "finance_cost": abs(fields["finance_cost"]) if "finance_cost" in fields and not bank else None,
        # no subsidiaries yet: "Profit attributable to equity holders  -" while the period's profit is the figure
        "net_profit": fields.get("net_profit") if fields.get("net_profit") or not fields.get("profit_period") else fields.get("profit_period"),
        "eps": fields.get("eps"),
        "dividend_per_share": fields.get("dividend_per_share") or None,
        "total_assets": fields.get("total_assets"),
        "total_equity": fields.get("equity_attrib") or fields.get("total_equity"),
        "total_liabilities": fields.get("total_liabilities"),
        "nav_per_share": fields.get("nav_per_share"),
    }
    prior_fields = {
        "revenue": prior.get("revenue_bank") if bank else prior.get("revenue"),
        "operating_profit": prior.get("operating_profit"),
        "net_profit": prior.get("net_profit") if prior.get("net_profit") or not prior.get("profit_period") else prior.get("profit_period"),
        "eps": prior.get("eps"),
    }
    ev = {k: evidence.get(src) for k, src in (("revenue", "revenue_bank" if bank else "revenue"), ("operating_profit", "operating_profit"),
                                               ("finance_cost", "finance_cost"),
                                               ("net_profit", "net_profit" if "net_profit" in fields else "profit_period"),
                                               ("eps", "eps"), ("dividend_per_share", "dividend_per_share"), ("total_assets", "total_assets"),
                                               ("total_equity", "equity_attrib" if "equity_attrib" in fields else "total_equity"),
                                               ("total_liabilities", "total_liabilities"), ("nav_per_share", "nav_per_share")) if evidence.get(src)}
    column = desc_used.get("net_profit") or desc_used.get("profit_period") or desc_used.get("eps")
    return {"period_end": period_end, "period_type": ptype, "bank": bank, "fields": out_fields,
            "prior": {k: v for k, v in prior_fields.items() if v is not None}, "evidence": ev, "column": column,
            "text_chars": len(text)}


def cross_check(res: dict[str, Any], shares: float | None) -> dict[str, Any]:
    """EPS x shares vs profit attributable, NAV x shares vs equity; decides needs_review."""
    f = res["fields"]
    checks, ok = [], True
    if not f.get("net_profit") and not f.get("eps"):
        return {"checks": ["no profit or EPS line found"], "needs_review": True}
    if res.get("column") and "Group" not in res["column"]:
        checks.append(f"column used: {res['column']} (no Group column identified)")
    if res.get("column") and "cumulative" in res["column"]:
        checks.append("only a cumulative column was identified")
        ok = False
    if shares and f.get("eps") and f.get("net_profit"):
        implied = f["eps"] * shares
        r = implied / f["net_profit"] if f["net_profit"] else None
        good = r is not None and 0.75 <= r <= 1.33
        checks.append(f"EPS {f['eps']:.2f} × {shares / 1e6:,.0f}M shares = {implied / 1e6:,.0f}M vs profit {f['net_profit'] / 1e6:,.0f}M "
                      + ("✓" if good else "✗ mismatch"))
        ok &= good
    elif not f.get("eps"):
        checks.append("no EPS line found")
        ok = False
    if shares and f.get("nav_per_share") and f.get("total_equity"):
        implied = f["nav_per_share"] * shares
        r = implied / f["total_equity"]
        good = 0.8 <= r <= 1.25
        then = f["total_equity"] / f["nav_per_share"] if f["nav_per_share"] else 0
        checks.append(f"NAV/share {f['nav_per_share']:.2f} × shares = {implied / 1e9:,.2f}bn vs equity {f['total_equity'] / 1e9:,.2f}bn "
                      + ("✓" if good else f"✗ mismatch: implies {then / 1e6:,.0f}M shares then vs {shares / 1e6:,.0f}M today "
                                          "(a rights / bonus issue since, or the wrong column); check against the PDF"))
        ok &= good
    if not f.get("revenue"):
        checks.append("no revenue line found")
    if not res.get("period_end"):
        checks.append("period end not found in the title")
        ok = False
    return {"checks": checks, "needs_review": not ok}
