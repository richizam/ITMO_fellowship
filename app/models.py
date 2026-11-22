"""Data model of the candidate dossier ("карточка кандидата")."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Referee(BaseModel):
    name: str = ""
    affiliation: str = ""
    email: str = ""


class BasicInfo(BaseModel):
    full_name: str = ""
    full_name_latin: str = ""
    track: str = ""  # Postdoc / Fellowship / Micro Fellowship / Professorship ...
    date_of_birth: str = ""  # ISO yyyy-mm-dd
    age: int | None = None
    citizenship: str = ""
    country: str = ""
    city: str = ""
    email: str = ""
    phone: str = ""
    organization: str = ""
    job_title: str = ""
    phd_institution: str = ""
    phd_year: str = ""
    phd_topic: str = ""
    host_unit: str = ""  # ITMO host department named in the application
    itmo_contact: str = ""  # contact / head of host unit named in the application
    how_learned: str = ""
    referees: list[Referee] = Field(default_factory=list)


class Identifiers(BaseModel):
    orcid: str = ""
    scopus_id: str = ""
    scholar_id: str = ""
    openalex_ids: list[str] = Field(default_factory=list)
    researcherid: str = ""
    # how each identifier was obtained, e.g. {"orcid": "CV"}
    provenance: dict[str, str] = Field(default_factory=dict)


class CategoryQuartile(BaseModel):
    category: str
    quartile: str | None = None


class JournalQuality(BaseModel):
    quartile: str | None = None  # best quartile, Q1..Q4
    year: int | None = None  # SJR year the quartile was taken from
    source: str = "SCImago Journal Rank"
    sjr: float | None = None
    journal_title: str = ""
    categories: list[CategoryQuartile] = Field(default_factory=list)
    matched_by: str = ""  # "ISSN" | "title"


class Publication(BaseModel):
    title: str
    year: int | None = None
    pub_type: str = ""  # article, review, conference paper, book chapter, preprint, ...
    venue: str = ""
    issn: list[str] = Field(default_factory=list)
    doi: str = ""
    url: str = ""
    authors: list[str] = Field(default_factory=list)
    n_authors: int | None = None
    author_position: int | None = None  # 1-based
    is_first: bool = False
    is_last: bool = False
    is_corresponding: bool = False
    authors_truncated: bool = False
    citations: int | None = None  # best available count
    citations_by_source: dict[str, int] = Field(default_factory=dict)
    quality: JournalQuality | None = None
    sources: list[str] = Field(default_factory=list)  # OpenAlex / ORCID / Google Scholar / Scopus


class Metric(BaseModel):
    value: float | int | None = None
    source: str = ""
    note: str = ""
    url: str = ""


class Metrics(BaseModel):
    h_index_scopus: Metric = Field(default_factory=Metric)
    h_index_scholar: Metric = Field(default_factory=Metric)
    h_index_scholar_recent: Metric = Field(default_factory=Metric)
    h_index_openalex: Metric = Field(default_factory=Metric)
    i10_scholar: Metric = Field(default_factory=Metric)
    citations_scholar: Metric = Field(default_factory=Metric)
    citations_scholar_recent: Metric = Field(default_factory=Metric)
    citations_scopus: Metric = Field(default_factory=Metric)
    citations_openalex: Metric = Field(default_factory=Metric)
    documents_scopus: Metric = Field(default_factory=Metric)
    works_openalex: Metric = Field(default_factory=Metric)
    scholar_recent_label: str = ""  # e.g. "since 2021"

    # computed over the publication window
    pubs_in_window: int = 0
    pubs_by_year: dict[int, int] = Field(default_factory=dict)
    gap_years: list[int] = Field(default_factory=list)
    quartiles: dict[str, int] = Field(default_factory=dict)  # Q1..Q4, "—"
    first_author: int = 0
    last_author: int = 0
    corresponding_author: int = 0
    q1q2_first_author: int = 0
    q1q2_any: int = 0
    citations_window: int = 0
    scholar_pubs_in_window: int | None = None  # count as listed on Google Scholar (template field)


class EvidenceItem(BaseModel):
    """A statement of the AI profile with the quote the model gave as its basis, checked against the documents."""
    kind: str  # summary | interest | method | competency | direction | note | proposal_title
    text: str
    quote: str = ""
    source: str = ""  # where the model says the quote is from: cv | proposal | form | publications
    verified: bool | None = None  # True = quote found in the documents, False = not found, None = no quote
    found_in: str = ""  # where the quote was actually found
    similarity: int | None = None  # 0-100


class AICall(BaseModel):
    """One LLM request (with its retries) made while building the dossier."""
    n: int
    purpose: str  # profile | units
    provider: str = ""
    model: str = ""
    started_at: datetime = Field(default_factory=datetime.now)
    seconds: float = 0.0
    attempts: int = 1
    status: Literal["ok", "retried", "failed"] = "ok"
    error: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


class ResearchProfile(BaseModel):
    summary: str = ""
    interests: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    competencies: list[str] = Field(default_factory=list)
    applied_directions: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    proposal_title: str = ""
    proposal_summary: str = ""
    expert_notes: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    generated_by: str = "heuristic"  # "<provider>:<model>" | "heuristic"


class Check(BaseModel):
    level: Literal["ok", "info", "warn", "error"] = "info"
    topic: str = ""
    message: str


class SourceStatus(BaseModel):
    name: str
    status: Literal["ok", "partial", "skipped", "error"]
    detail: str = ""
    url: str = ""


class DocumentMeta(BaseModel):
    kind: str  # cv | proposal | application | other
    filename: str
    chars: int = 0


class UnitMatch(BaseModel):
    unit_id: str
    name: str
    type: str = ""
    parent: str = ""
    head: str = ""
    homepage: str = ""
    url: str = ""
    score: int = 0  # 0-100, relative within this candidate's ranking
    semantic: float = 0.0  # mean best cosine similarity (multilingual embeddings)
    lexical: float = 0.0  # TF-IDF cosine
    evidence: list[str] = Field(default_factory=list)  # candidate items that matched this unit best
    shared_terms: list[str] = Field(default_factory=list)
    rationale: str = ""
    directions: list[str] = Field(default_factory=list)  # possible collaboration directions
    is_applied_unit: bool = False


class Matching(BaseModel):
    method: str = ""
    generated_by: str = "heuristic"  # "claude:<model>" when re-ranked by an LLM
    catalog_size: int = 0
    catalog_source: str = ""
    recommendations: list[UnitMatch] = Field(default_factory=list)
    applied_unit: str = ""  # host unit named in the application
    applied_unit_id: str = ""  # its best match in the catalog
    applied_unit_name: str = ""
    applied_unit_rank: int | None = None  # rank of that unit in our full ranking (1-based)


class Dossier(BaseModel):
    id: str
    created_at: datetime = Field(default_factory=datetime.now)
    window_from: int
    window_to: int
    basic: BasicInfo = Field(default_factory=BasicInfo)
    identifiers: Identifiers = Field(default_factory=Identifiers)
    profile: ResearchProfile = Field(default_factory=ResearchProfile)
    publications: list[Publication] = Field(default_factory=list)
    metrics: Metrics = Field(default_factory=Metrics)
    checks: list[Check] = Field(default_factory=list)
    sources: list[SourceStatus] = Field(default_factory=list)
    documents: list[DocumentMeta] = Field(default_factory=list)
    scholar_interests: list[str] = Field(default_factory=list)
    openalex_topics: list[str] = Field(default_factory=list)
    example: bool = False
    matching: Matching | None = None
    ai_calls: list[AICall] = Field(default_factory=list)
