"""Build one de-duplicated publication list from OpenAlex, ORCID and Google Scholar,
then add journal quartiles (SJR) and the candidate's author position."""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from app.analysis.names import author_matches, split_author_list
from app.models import Publication
from app.sources import sjr
from app.sources.orcid import OrcidWork
from app.sources.scholar import ScholarPub, venue_name

TYPE_LABELS = {
    ("article", "journal"): "Статья в журнале",
    ("article", "conference"): "Статья в трудах конференции",
    ("article", "book series"): "Статья в серии трудов",
    ("article", "repository"): "Препринт",
    ("review", None): "Обзор",
    ("book-chapter", None): "Глава в книге",
    ("book", None): "Книга",
    ("preprint", None): "Препринт",
    ("dataset", None): "Датасет",
    ("editorial", None): "Редакционная статья",
    ("letter", None): "Письмо в редакцию",
    ("erratum", None): "Исправление",
    ("dissertation", None): "Диссертация",
    ("report", None): "Отчёт",
    ("peer-review", None): "Рецензия",
    ("paratext", None): "Прочее",
    ("conference-paper", None): "Статья в трудах конференции",
    ("proceedings-article", None): "Статья в трудах конференции",
    ("data-paper", None): "Статья о данных",
    ("software-paper", None): "Статья о ПО",
    ("other", None): "Прочее",
    ("supplementary-materials", None): "Прочее",
}
ORCID_TYPES = {
    "journal article": "Статья в журнале",
    "conference paper": "Статья в трудах конференции",
    "book chapter": "Глава в книге",
    "book": "Книга",
    "preprint": "Препринт",
    "review": "Обзор",
    "dissertation thesis": "Диссертация",
    "conference abstract": "Тезисы конференции",
    "conference poster": "Тезисы конференции",
    "patent": "Патент",
    "edited book": "Книга",
    "other": "Прочее",
}
NON_JOURNAL = {"Препринт", "Датасет", "Диссертация", "Отчёт", "Рецензия", "Исправление", "Прочее", "Патент", "Тезисы конференции"}


def norm_title(t: str) -> str:
    t = re.sub(r"<[^>]+>", "", t or "").lower()
    t = re.sub(r"\((?:ssrn|preprint|arxiv)[^)]*\)\s*$", "", t)  # "(SSRN Scholarly Paper No. 4871972)"
    return re.sub(r"[^a-z0-9а-яё]+", "", t)


def _strip_doi(d: str | None) -> str:
    return (d or "").lower().removeprefix("https://doi.org/").removeprefix("http://doi.org/").strip()


def _label(work_type: str, source_type: str | None) -> str:
    return (TYPE_LABELS.get((work_type, source_type)) or TYPE_LABELS.get((work_type, None))
            or ("Статья в журнале" if work_type == "article" else work_type or "—"))


def from_openalex(w: dict, author_ids: set[str], keys: set[tuple[str, str]]) -> Publication:
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    auths = w.get("authorships") or []
    names = [(a.get("author") or {}).get("display_name") or a.get("raw_author_name") or "" for a in auths]
    pos = None
    corr = False
    for i, a in enumerate(auths):
        aid = ((a.get("author") or {}).get("id") or "").rsplit("/", 1)[-1]
        if aid and aid in author_ids:
            pos, corr = i + 1, bool(a.get("is_corresponding"))
            break
    if pos is None:  # fall back to name matching
        for i, a in enumerate(auths):
            if author_matches(a.get("raw_author_name") or names[i], keys):
                pos, corr = i + 1, bool(a.get("is_corresponding"))
                break
    n = len(auths)
    doi = _strip_doi(w.get("doi"))
    p = Publication(
        title=re.sub(r"<[^>]+>", "", w.get("title") or "").strip(),
        year=w.get("publication_year"),
        pub_type=_label(w.get("type") or "", src.get("type")),
        venue=src.get("display_name") or "",
        issn=list(src.get("issn") or []),
        doi=doi,
        url=f"https://doi.org/{doi}" if doi else (loc.get("landing_page_url") or w.get("id") or ""),
        authors=names,
        n_authors=n,
        author_position=pos,
        is_first=pos == 1,
        is_last=pos is not None and n > 1 and pos == n,
        is_corresponding=corr,
        authors_truncated=bool(w.get("is_authors_truncated")),
        citations=w.get("cited_by_count"),
        citations_by_source={"OpenAlex": w.get("cited_by_count") or 0},
        sources=["OpenAlex"],
    )
    return p


def from_orcid(w: OrcidWork) -> Publication:
    return Publication(
        title=w.title, year=w.year, pub_type=ORCID_TYPES.get(w.work_type, w.work_type.capitalize() if w.work_type else "—"),
        venue=w.journal, doi=w.doi, url=f"https://doi.org/{w.doi}" if w.doi else w.url, sources=["ORCID"],
    )


def from_scholar(w: ScholarPub, keys: set[tuple[str, str]]) -> Publication:
    authors = split_author_list(w.authors)
    truncated = w.authors.strip().endswith("...") or w.authors.strip().endswith("…")
    pos = next((i + 1 for i, a in enumerate(authors) if author_matches(a, keys)), None)
    v = venue_name(w.venue)
    vl = v.lower()
    if re.search(r"arxiv|biorxiv|medrxiv|chemrxiv|ssrn|research square|preprint", vl):
        t = "Препринт"
    elif re.search(r"conference|proceedings|symposium|workshop|congress", vl):
        t = "Статья в трудах конференции"
    elif re.search(r"thesis|dissertation", vl) or (
            re.search(r"universit|college|school of", vl) and not re.search(r"\d", w.venue)):
        t = "Диссертация"
    elif re.search(r"patent", vl):
        t = "Патент"
    elif v:
        t = "Статья в журнале"
    else:
        t = "—"
    n = None if truncated else len(authors) or None
    return Publication(
        title=w.title, year=w.year, pub_type=t, venue=v, url=w.url, authors=authors,
        n_authors=n, author_position=pos, is_first=pos == 1,
        is_last=bool(pos and n and n > 1 and pos == n), authors_truncated=truncated,
        citations=w.citations, citations_by_source={"Google Scholar": w.citations or 0},
        sources=["Google Scholar"],
    )


def _find(pubs: list[Publication], p: Publication) -> Publication | None:
    if p.doi:
        for q in pubs:
            if q.doi and q.doi == p.doi:
                return q
    nt = norm_title(p.title)
    if len(nt) < 12:
        return None
    for q in pubs:
        nq = norm_title(q.title)
        if nq == nt:
            return q
        if p.year and q.year and abs(p.year - q.year) > 1:
            continue
        if fuzz.ratio(nt, nq) >= 92:
            return q
        short, long_ = sorted((nt, nq), key=len)
        if len(short) >= 40 and long_.startswith(short):
            return q
    return None


def _merge_into(base: Publication, other: Publication) -> None:
    for s in other.sources:
        if s not in base.sources:
            base.sources.append(s)
    base.citations_by_source.update(other.citations_by_source)
    base.citations = max(base.citations_by_source.values()) if base.citations_by_source else base.citations
    if not base.doi and other.doi:
        base.doi, base.url = other.doi, f"https://doi.org/{other.doi}"
    if not base.venue and other.venue:
        base.venue = other.venue
    if base.author_position is None and other.author_position is not None:
        base.author_position, base.is_first, base.is_last = other.author_position, other.is_first, other.is_last
        base.n_authors = base.n_authors or other.n_authors
    if base.pub_type in ("—", "") and other.pub_type:
        base.pub_type = other.pub_type


def _prefer(a: Publication, b: Publication) -> tuple[Publication, Publication]:
    """When OpenAlex lists both a preprint and the published paper, keep the published one."""
    if a.pub_type == "Препринт" and b.pub_type != "Препринт":
        return b, a
    return a, b


SOURCE_ORDER = ["OpenAlex", "ORCID", "Google Scholar", "Scopus"]


def merge(openalex: list[Publication], orcid: list[Publication], scholar: list[Publication]) -> list[Publication]:
    pubs: list[Publication] = []
    for p in openalex + orcid + scholar:
        existing = _find(pubs, p)
        if existing is None:
            pubs.append(p)
            continue
        keep, drop = _prefer(existing, p)
        if keep is not existing:
            pubs[pubs.index(existing)] = keep
        _merge_into(keep, drop)
    for p in pubs:
        p.sources = sorted(set(p.sources), key=lambda s: SOURCE_ORDER.index(s) if s in SOURCE_ORDER else 99)
    return pubs


def add_quality(pubs: list[Publication]) -> None:
    for p in pubs:
        if p.pub_type in NON_JOURNAL or not (p.issn or p.venue):
            continue
        p.quality = sjr.lookup(p.issn, p.venue, p.year)


def in_window(p: Publication, y0: int, y1: int) -> bool:
    return p.year is not None and y0 <= p.year <= y1


def sort_key(p: Publication):
    qrank = {"Q1": 0, "Q2": 1, "Q3": 2, "Q4": 3}.get(p.quality.quartile if p.quality else None, 4)
    return (-(p.year or 0), qrank, -(p.citations or 0))
