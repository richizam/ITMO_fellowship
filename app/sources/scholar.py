"""Google Scholar public profile (citations?user=...).

Google Scholar has no API. We read the public profile page the same way a browser
does, one page of up to 100 publications sorted by date, with caching and pacing.
If Scholar answers with a CAPTCHA the source is reported as unavailable and the rest
of the dossier is still built.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from app.sources.http import FetchError, http

BASE = "https://scholar.google.com/citations"


@dataclass
class ScholarPub:
    title: str
    authors: str
    venue: str
    year: int | None
    citations: int | None
    url: str


@dataclass
class ScholarProfile:
    scholar_id: str
    name: str = ""
    affiliation: str = ""
    interests: list[str] = field(default_factory=list)
    citations_all: int | None = None
    citations_recent: int | None = None
    h_all: int | None = None
    h_recent: int | None = None
    i10_all: int | None = None
    i10_recent: int | None = None
    recent_label: str = ""  # "Since 2021"
    citations_by_year: dict[int, int] = field(default_factory=dict)
    publications: list[ScholarPub] = field(default_factory=list)
    complete_for_window: bool = True

    @property
    def url(self) -> str:
        return f"{BASE}?user={self.scholar_id}&hl=en"


class ScholarBlocked(FetchError):
    pass


def _int(s: str | None) -> int | None:
    if not s:
        return None
    d = re.sub(r"[^\d]", "", s)
    return int(d) if d else None


def fetch_profile(scholar_id: str, from_year: int, max_pages: int = 8) -> ScholarProfile:
    prof = ScholarProfile(scholar_id=scholar_id)
    for page in range(max_pages):
        params = {"user": scholar_id, "hl": "en", "cstart": page * 100, "pagesize": 100, "sortby": "pubdate"}
        status, body = http().get(BASE, params=params, browser=True)
        if status == 404:
            raise FetchError("Google Scholar profile not found", 404)
        if status != 200 or "gsc_prf_in" not in body:
            raise ScholarBlocked(f"Google Scholar refused the request (HTTP {status}, probably CAPTCHA)", status)
        soup = BeautifulSoup(body, "lxml")
        if page == 0:
            _parse_header(soup, prof)
        rows = _parse_rows(soup)
        prof.publications.extend(rows)
        years = [r.year for r in rows if r.year]
        # sorted by date: stop once we are past the window or the list is exhausted
        if len(rows) < 100 or (years and min(years) < from_year):
            break
    else:
        prof.complete_for_window = False
    return prof


def _parse_header(soup: BeautifulSoup, prof: ScholarProfile) -> None:
    el = soup.find(id="gsc_prf_in")
    prof.name = el.get_text(" ", strip=True) if el else ""
    aff = soup.select_one("#gsc_prf_i .gsc_prf_il")
    prof.affiliation = aff.get_text(" ", strip=True) if aff else ""
    prof.interests = [a.get_text(strip=True) for a in soup.select("#gsc_prf_int a.gsc_prf_inta")]

    table = soup.find(id="gsc_rsb_st")
    if table:
        hdr = [th.get_text(strip=True) for th in table.select("thead th")]
        prof.recent_label = hdr[2] if len(hdr) > 2 else ""
        for tr in table.select("tbody tr"):
            label = tr.select_one(".gsc_rsb_sc1")
            vals = [_int(td.get_text()) for td in tr.select("td.gsc_rsb_std")]
            name = label.get_text(strip=True).lower() if label else ""
            if len(vals) < 2:
                continue
            if name.startswith("citations"):
                prof.citations_all, prof.citations_recent = vals[0], vals[1]
            elif name.startswith("h-index"):
                prof.h_all, prof.h_recent = vals[0], vals[1]
            elif name.startswith("i10"):
                prof.i10_all, prof.i10_recent = vals[0], vals[1]

    years = [_int(s.get_text()) for s in soup.select(".gsc_md_hist_b .gsc_g_t, #gsc_rsb_cit .gsc_g_t")]
    counts = [_int(s.get_text()) for s in soup.select(".gsc_md_hist_b .gsc_g_al, #gsc_rsb_cit .gsc_g_al")]
    for y, c in zip(years, counts):
        if y:
            prof.citations_by_year[y] = c or 0


def _parse_rows(soup: BeautifulSoup) -> list[ScholarPub]:
    out = []
    for tr in soup.select("tr.gsc_a_tr"):
        a = tr.select_one("a.gsc_a_at")
        if not a:
            continue
        grays = tr.select("div.gs_gray")
        authors = grays[0].get_text(" ", strip=True) if grays else ""
        venue = ""
        if len(grays) > 1:
            g = grays[1]
            for span in g.select(".gs_oph"):
                span.decompose()
            venue = g.get_text(" ", strip=True)
        year = _int((tr.select_one(".gsc_a_y span") or tr.select_one(".gsc_a_h")).get_text()) if tr.select_one(".gsc_a_y") else None
        cites_el = tr.select_one("a.gsc_a_ac")
        href = a.get("href") or a.get("data-href") or ""
        out.append(ScholarPub(
            title=a.get_text(" ", strip=True),
            authors=authors,
            venue=venue,
            year=year,
            citations=_int(cites_el.get_text()) if cites_el else None,
            url=("https://scholar.google.com" + href) if href.startswith("/") else href,
        ))
    return out


def venue_name(venue: str) -> str:
    """'Nature communications 17 (1), 6397' -> 'Nature communications'."""
    v = re.split(r"\s\d|,", venue, maxsplit=1)[0]
    return v.strip(" .")
