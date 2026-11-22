"""Scopus Author Retrieval API (Elsevier). Needs SCOPUS_API_KEY (dev.elsevier.com);
h-index and citation counts are returned only to subscribing institutions, i.e. when
the request comes from the ITMO network or carries an institutional token."""
from __future__ import annotations

from dataclasses import dataclass

from app import config
from app.sources.http import FetchError, http

BASE = "https://api.elsevier.com/content/author/author_id"


@dataclass
class ScopusAuthor:
    scopus_id: str
    name: str = ""
    h_index: int | None = None
    citation_count: int | None = None
    document_count: int | None = None
    affiliation: str = ""

    @property
    def url(self) -> str:
        return f"https://www.scopus.com/authid/detail.uri?authorId={self.scopus_id}"


def available() -> bool:
    return bool(config.SCOPUS_API_KEY)


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def fetch(scopus_id: str) -> ScopusAuthor:
    if not available():
        raise FetchError("SCOPUS_API_KEY не задан")
    headers = {"Accept": "application/json", "X-ELS-APIKey": config.SCOPUS_API_KEY}
    if config.SCOPUS_INST_TOKEN:
        headers["X-ELS-Insttoken"] = config.SCOPUS_INST_TOKEN
    data = http().get_json(f"{BASE}/{scopus_id}", params={"view": "ENHANCED"}, headers=headers)
    if not data:
        raise FetchError("Scopus author not found", 404)
    resp = data.get("author-retrieval-response")
    if isinstance(resp, list):
        resp = resp[0] if resp else {}
    resp = resp or {}
    core = resp.get("coredata") or {}
    a = ScopusAuthor(scopus_id=scopus_id)
    a.h_index = _int(resp.get("h-index"))
    a.citation_count = _int(core.get("citation-count"))
    a.document_count = _int(core.get("document-count"))
    prof = resp.get("author-profile") or {}
    pn = prof.get("preferred-name") or {}
    a.name = f"{pn.get('given-name', '')} {pn.get('surname', '')}".strip()
    aff = (prof.get("affiliation-current") or {}).get("affiliation") or {}
    if isinstance(aff, list):
        aff = aff[0] if aff else {}
    a.affiliation = ((aff.get("ip-doc") or {}).get("afdispname") or "") if isinstance(aff, dict) else ""
    return a
