"""ORCID public API (pub.orcid.org, no key needed): researcher name and the list of works."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.sources.http import http

BASE = "https://pub.orcid.org/v3.0"
HEADERS = {"Accept": "application/json"}


@dataclass
class OrcidWork:
    title: str
    year: int | None
    work_type: str
    journal: str
    doi: str
    url: str


@dataclass
class OrcidRecord:
    orcid: str
    given_names: str = ""
    family_name: str = ""
    other_names: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    works: list[OrcidWork] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"{self.given_names} {self.family_name}".strip()

    @property
    def url(self) -> str:
        return f"https://orcid.org/{self.orcid}"


def _val(d: dict | None, *path: str):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def fetch(orcid: str) -> OrcidRecord | None:
    rec = OrcidRecord(orcid=orcid)
    person = http().get_json(f"{BASE}/{orcid}/person", headers=HEADERS)
    if person is None:
        return None
    rec.given_names = _val(person, "name", "given-names", "value") or ""
    rec.family_name = _val(person, "name", "family-name", "value") or ""
    rec.other_names = [o.get("content", "") for o in (_val(person, "other-names", "other-name") or [])]
    for k in _val(person, "keywords", "keyword") or []:
        # researchers often type all keywords into one field: "A, B; C"
        for part in re.split(r"[,;]", k.get("content") or ""):
            part = part.strip()
            if part and part.lower() not in {x.lower() for x in rec.keywords}:
                rec.keywords.append(part)

    works = http().get_json(f"{BASE}/{orcid}/works", headers=HEADERS) or {}
    for group in works.get("group") or []:
        summaries = group.get("work-summary") or []
        if not summaries:
            continue
        s = summaries[0]
        doi = ""
        for ext in _val(group, "external-ids", "external-id") or []:
            if (ext.get("external-id-type") or "").lower() == "doi":
                doi = (ext.get("external-id-normalized") or {}).get("value") or ext.get("external-id-value") or ""
                break
        year = _val(s, "publication-date", "year", "value")
        rec.works.append(OrcidWork(
            title=_val(s, "title", "title", "value") or "",
            year=int(year) if year and str(year).isdigit() else None,
            work_type=(s.get("type") or "").replace("-", " "),
            journal=_val(s, "journal-title", "value") or "",
            doi=doi.lower().removeprefix("https://doi.org/"),
            url=_val(s, "url", "value") or "",
        ))
    return rec
