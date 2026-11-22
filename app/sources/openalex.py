"""OpenAlex (openalex.org) — open bibliographic database, used for publication metadata,
DOIs, authorship positions, citation counts and author-level metrics."""
from __future__ import annotations

from app import config
from app.sources.http import http

BASE = "https://api.openalex.org"
WORK_FIELDS = (
    "id,doi,title,publication_year,type,primary_location,authorships,cited_by_count,"
    "corresponding_author_ids,is_authors_truncated"
)
AUTHOR_FIELDS = (
    "id,display_name,display_name_alternatives,orcid,works_count,cited_by_count,"
    "summary_stats,last_known_institutions,topics,affiliations"
)


def _get(path: str, params: dict) -> dict | None:
    p = dict(params)
    key_params = dict(p)
    if config.OPENALEX_API_KEY:
        p["api_key"] = config.OPENALEX_API_KEY
    if config.CONTACT_EMAIL:
        p["mailto"] = config.CONTACT_EMAIL
    return http().get_json(f"{BASE}{path}", params=p, cache_key_params=key_params)


def short_id(oa_id: str) -> str:
    return oa_id.rsplit("/", 1)[-1]


def author_by_orcid(orcid: str) -> dict | None:
    data = _get("/authors", {"filter": f"orcid:{orcid}", "select": AUTHOR_FIELDS})
    res = (data or {}).get("results") or []
    # an ORCID can be attached to several OpenAlex author records; take the biggest first
    return max(res, key=lambda a: a.get("works_count", 0)) if res else None


def authors_by_orcid(orcid: str) -> list[dict]:
    data = _get("/authors", {"filter": f"orcid:{orcid}", "select": AUTHOR_FIELDS})
    return (data or {}).get("results") or []


def get_authors(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    data = _get("/authors", {"filter": "openalex:" + "|".join(short_id(i) for i in ids),
                             "select": AUTHOR_FIELDS, "per-page": 50})
    return (data or {}).get("results") or []


def search_authors(name: str, limit: int = 8) -> list[dict]:
    data = _get("/authors", {"search": name, "select": AUTHOR_FIELDS, "per-page": limit})
    return (data or {}).get("results") or []


def recent_titles(author_id: str, n: int = 50) -> list[str]:
    data = _get("/works", {"filter": f"author.id:{short_id(author_id)}", "select": "title",
                           "sort": "publication_year:desc", "per-page": n})
    return [w.get("title") or "" for w in (data or {}).get("results") or []]


def works_for_authors(author_ids: list[str], from_year: int, to_year: int) -> list[dict]:
    ids = "|".join(short_id(a) for a in author_ids)
    out: list[dict] = []
    # page= paging (cursor paging needs an API key); 10 x 200 is far more than any window
    for page in range(1, 11):
        data = _get("/works", {
            "filter": f"author.id:{ids},publication_year:{from_year}-{to_year}",
            "select": WORK_FIELDS, "per-page": 200, "page": page, "sort": "publication_year:desc",
        })
        results = (data or {}).get("results") or []
        out.extend(results)
        total = ((data or {}).get("meta") or {}).get("count") or 0
        if not results or len(out) >= total:
            break
    return out


def works_by_dois(dois: list[str]) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(dois), 50):
        chunk = dois[i:i + 50]
        data = _get("/works", {"filter": "doi:" + "|".join(chunk), "select": WORK_FIELDS, "per-page": 50})
        out.extend((data or {}).get("results") or [])
    return out
