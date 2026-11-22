"""Researcher identifiers and self-reported metrics found in free text (CV, proposal, links)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ORCID_RE = re.compile(r"\b(\d{4}-\d{4}-\d{4}-\d{3}[\dX])\b")
SCHOLAR_RE = re.compile(r"[?&]user=([\w-]{12})")
SCOPUS_URL_RE = re.compile(r"authorId=(\d{8,12})")
SCOPUS_TEXT_RE = re.compile(r"Scopus\s*(?:Author)?\s*ID\s*[:\-]?\s*(\d{8,12})", re.I)
RESEARCHERID_RE = re.compile(r"Researcher\s*ID\s*[:\-]?\s*([A-Z]{1,3}-\d{4}-\d{4})", re.I)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>,;]+[^\s\"<>,;.)\]])", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def orcid_from(text: str) -> str:
    m = ORCID_RE.search(text or "")
    return m.group(1) if m else ""


def scholar_id_from(text: str) -> str:
    m = SCHOLAR_RE.search(text or "")
    return m.group(1) if m else ""


def scopus_id_from(text: str) -> str:
    text = text or ""
    if re.fullmatch(r"\s*\d{8,12}\s*", text):
        return text.strip()
    m = SCOPUS_URL_RE.search(text) or SCOPUS_TEXT_RE.search(text)
    return m.group(1) if m else ""


@dataclass
class Claim:
    metric: str  # h_index | citations | publications
    value: int
    source: str  # Scopus | Google Scholar | Web of Science | unspecified
    snippet: str


@dataclass
class TextFindings:
    orcid: str = ""
    scholar_id: str = ""
    scopus_id: str = ""
    researcherid: str = ""
    dois: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)


def _source_near(text: str, start: int, end: int) -> str:
    window = text[max(0, start - 60): end + 60].lower()
    if "scholar" in window:
        return "Google Scholar"
    if "scopus" in window:
        return "Scopus"
    if "web of science" in window or "wos" in window:
        return "Web of Science"
    return "источник не указан"


def _to_int(s: str) -> int | None:
    digits = re.sub(r"[^\d]", "", s)
    return int(digits) if digits else None


def scan_text(text: str) -> TextFindings:
    f = TextFindings()
    f.orcid = orcid_from(text)
    f.scholar_id = scholar_id_from(text)
    f.scopus_id = scopus_id_from(text)
    m = RESEARCHERID_RE.search(text)
    f.researcherid = m.group(1) if m else ""
    f.dois = sorted({d.rstrip(".").lower() for d in DOI_RE.findall(text)})
    f.emails = sorted(set(EMAIL_RE.findall(text)))

    flat = re.sub(r"\s+", " ", text)
    seen: set[tuple[str, int, str]] = set()

    def add(metric: str, value: int | None, m: re.Match) -> None:
        if value is None:
            return
        src = _source_near(flat, m.start(), m.end())
        key = (metric, value, src)
        if key in seen:
            return
        seen.add(key)
        snippet = flat[max(0, m.start() - 40): m.end() + 40].strip()
        f.claims.append(Claim(metric, value, src, snippet))

    sep = r"\s*(?:\([^)]{0,40}\))?\s*(?:[:=\-–—]|is|of|равен|составляет)?\s*"
    for m in re.finditer(r"(?:h[\s-]?index|hirsch[\s-]index|индекс\s+хирша)" + sep + r"(?<![iI])(\d{1,3})\b(?![.,]\d)", flat, re.I):
        add("h_index", _to_int(m.group(1)), m)
    for m in re.finditer(r"citations?" + sep + r"(\d{1,3}(?:[ ,]\d{3})+|\d{2,6})\b", flat, re.I):
        if "[IF" in flat[max(0, m.start() - 20): m.start()]:
            continue  # per-paper citation badges like "[IF: 8.5 | Citations: 1600+]"
        add("citations", _to_int(m.group(1)), m)
    for m in re.finditer(r"(\d{1,3}(?:,\d{3})+|\d{2,6})\+?\s+citations", flat, re.I):
        add("citations", _to_int(m.group(1)), m)
    return f
