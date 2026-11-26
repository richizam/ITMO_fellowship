"""End-to-end pipeline: documents + identifiers -> candidate dossier."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from app import config, llm
from app.analysis import matching as matching_mod
from app.analysis import metrics as metrics_mod
from app.analysis import profile as profile_mod
from app.analysis import publications as pubs_mod
from app.analysis.names import ascii_fold, has_cyrillic, latin_display_name, name_keys
from app.ingest.application import ApplicationData, parse_application
from app.ingest.extract import ExtractionError, extract_text
from app.ingest.identifiers import orcid_from, scan_text, scholar_id_from, scopus_id_from
from app.models import AICall, DocumentMeta, Dossier, Metric, SourceStatus
from app.sources import openalex, orcid, scholar, scopus, sjr
from app.sources.http import FetchError

log = logging.getLogger(__name__)
Progress = Callable[[str], None]


@dataclass
class CandidateInput:
    files: dict[str, list[Path]] = field(default_factory=dict)  # cv | proposal | application | other
    full_name: str = ""
    orcid: str = ""
    scopus: str = ""
    scholar: str = ""
    h_index_scopus_manual: int | None = None
    from_year: int | None = None
    example: bool = False
    dossier_id: str | None = None


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", ascii_fold(name).lower()).strip("-")
    return s[:40] or "candidate"


def dossier_path(dossier_id: str) -> Path:
    return config.DOSSIER_DIR / f"{dossier_id}.json"


def save(d: Dossier) -> None:
    dossier_path(d.id).write_text(d.model_dump_json(indent=2))


def load(dossier_id: str) -> Dossier | None:
    p = dossier_path(dossier_id)
    if not p.exists() or not re.fullmatch(r"[a-z0-9-]+", dossier_id):
        return None
    return Dossier.model_validate_json(p.read_text())


def list_dossiers() -> list[Dossier]:
    out = []
    for p in sorted(config.DOSSIER_DIR.glob("*.json")):
        try:
            out.append(Dossier.model_validate_json(p.read_text()))
        except Exception as e:  # a broken file must not break the list page
            log.warning("skip %s: %s", p.name, e)
    return sorted(out, key=lambda d: d.created_at, reverse=True)


# ----------------------------------------------------------------------------------------
def build_dossier(inp: CandidateInput, progress: Progress = lambda s: None) -> Dossier:
    y1 = date.today().year
    y0 = inp.from_year or config.default_from_year()

    # 1. documents ---------------------------------------------------------------------
    progress("Извлечение текста из документов")
    texts: dict[str, str] = {}
    docs: list[DocumentMeta] = []
    sources: list[SourceStatus] = []
    for kind, paths in inp.files.items():
        chunks = []
        for p in paths:
            try:
                t = extract_text(p)
            except ExtractionError as e:
                sources.append(SourceStatus(name=f"Документ {p.name}", status="error", detail=str(e)))
                continue
            chunks.append(t)
            docs.append(DocumentMeta(kind=kind, filename=p.name, chars=len(t)))
        texts[kind] = "\n\n".join(chunks)
    cv_text = texts.get("cv", "")
    proposal_text = texts.get("proposal", "")
    other_text = texts.get("other", "")

    # 2. application form ------------------------------------------------------------
    app_data: ApplicationData | None = None
    if texts.get("application"):
        progress("Разбор анкеты заявки")
        app_data = parse_application(texts["application"])
    basic = app_data.basic if app_data else None

    # 3. identifiers: manual input > application form > CV / proposal --------------------
    findings = scan_text("\n".join([cv_text, proposal_text, other_text]))
    d = Dossier(id="tmp", window_from=y0, window_to=y1, example=inp.example, documents=docs, sources=sources)
    if basic:
        d.basic = basic
    if inp.full_name:
        d.basic.full_name = inp.full_name.strip()

    ids = d.identifiers

    def pick(field_name: str, candidates: list[tuple[str, str]]) -> None:
        for value, origin in candidates:
            if value:
                setattr(ids, field_name, value)
                ids.provenance[field_name] = origin
                return

    pick("orcid", [(orcid_from(inp.orcid), "введено вручную"),
                   (orcid_from(app_data.orcid) if app_data else "", "анкета"),
                   (findings.orcid, "CV / документы")])
    pick("scopus_id", [(scopus_id_from(inp.scopus), "введено вручную"),
                       (scopus_id_from(app_data.scopus_url) if app_data else "", "анкета"),
                       (findings.scopus_id, "CV / документы")])
    pick("scholar_id", [(scholar_id_from(inp.scholar) or (inp.scholar.strip() if re.fullmatch(r"[\w-]{12}", inp.scholar.strip()) else ""), "введено вручную"),
                        (scholar_id_from(app_data.scholar_url) if app_data else "", "анкета"),
                        (findings.scholar_id, "CV / документы")])
    ids.researcherid = findings.researcherid
    if not d.basic.email and findings.emails:
        d.basic.email = findings.emails[0]
    scopus_raw = app_data.scopus_url if app_data else inp.scopus

    # 4. Google Scholar ---------------------------------------------------------------
    sch = None
    if ids.scholar_id:
        progress("Google Scholar: профиль и публикации")
        try:
            sch = scholar.fetch_profile(ids.scholar_id, y0)
            sources.append(SourceStatus(name="Google Scholar", status="ok", url=sch.url,
                                        detail=f"{len(sch.publications)} публикаций в профиле, h-index {sch.h_all}"))
            d.scholar_interests = sch.interests
            if not sch.complete_for_window:
                sources[-1].status = "partial"
                sources[-1].detail += f"; прочитаны первые {len(sch.publications)} — список за период может быть неполным"
        except FetchError as e:
            sources.append(SourceStatus(name="Google Scholar", status="error", detail=str(e),
                                        url=f"{scholar.BASE}?user={ids.scholar_id}"))
    else:
        sources.append(SourceStatus(name="Google Scholar", status="skipped", detail="профиль не указан"))

    # 5. ORCID -------------------------------------------------------------------------
    orc = None
    if ids.orcid:
        progress("ORCID: список работ")
        try:
            orc = orcid.fetch(ids.orcid)
            if orc:
                sources.append(SourceStatus(name="ORCID", status="ok", url=orc.url,
                                            detail=f"{len(orc.works)} работ в профиле"))
            else:
                sources.append(SourceStatus(name="ORCID", status="error", detail="ORCID не найден"))
        except FetchError as e:
            sources.append(SourceStatus(name="ORCID", status="error", detail=str(e)))
    else:
        sources.append(SourceStatus(name="ORCID", status="skipped", detail="ORCID не указан"))

    # Latin name for searching / matching
    if not d.basic.full_name and sch:
        d.basic.full_name = sch.name
    if not d.basic.full_name and orc:
        d.basic.full_name = orc.name
    if has_cyrillic(d.basic.full_name):
        d.basic.full_name_latin = (orc.name if orc and orc.name else "") or (sch.name if sch else "") or latin_display_name(d.basic.full_name)
    elif not d.basic.full_name_latin:
        d.basic.full_name_latin = d.basic.full_name

    # 6. OpenAlex: resolve the author ----------------------------------------------------
    progress("OpenAlex: поиск профиля автора")
    oa_authors: list[dict] = []
    confidence = ""
    try:
        if ids.orcid:
            oa_authors = openalex.authors_by_orcid(ids.orcid)
            confidence = "high" if oa_authors else ""
        if not oa_authors:
            oa_authors, confidence = _resolve_by_name(d, sch)
        elif sch and sch.publications:
            # OpenAlex often splits one researcher into several records and only one carries
            # the ORCID; add records confirmed by overlapping titles with Google Scholar.
            extra, _ = _resolve_by_name(d, sch)
            known = {a["id"] for a in oa_authors}
            oa_authors += [a for a in extra if a["id"] not in known]
        ids.openalex_ids = [openalex.short_id(a["id"]) for a in oa_authors]
        if oa_authors:
            main = max(oa_authors, key=lambda a: a.get("works_count", 0))
            sources.append(SourceStatus(
                name="OpenAlex", status="ok" if confidence == "high" else "partial",
                url=main["id"].replace("https://openalex.org/", "https://openalex.org/authors/"),
                detail=f"{len(oa_authors)} профил(ь/я), привязка: "
                       + {"high": "по ORCID", "medium": "по совпадению публикаций со Scholar", "low": "только по имени"}.get(confidence, "—")))
        else:
            sources.append(SourceStatus(name="OpenAlex", status="error", detail="автор не найден"))
    except FetchError as e:
        sources.append(SourceStatus(name="OpenAlex", status="error", detail=str(e)))

    # 7. Scopus ---------------------------------------------------------------------------
    m = d.metrics
    if ids.scopus_id and scopus.available():
        progress("Scopus: метрики автора")
        try:
            sa = scopus.fetch(ids.scopus_id)
            m.h_index_scopus = Metric(value=sa.h_index, source="Scopus API", url=sa.url)
            m.citations_scopus = Metric(value=sa.citation_count, source="Scopus API", url=sa.url)
            m.documents_scopus = Metric(value=sa.document_count, source="Scopus API", url=sa.url)
            sources.append(SourceStatus(name="Scopus", status="ok" if sa.h_index is not None else "partial", url=sa.url,
                                        detail="h-index недоступен без подписки ИТМО" if sa.h_index is None else f"h-index {sa.h_index}"))
        except FetchError as e:
            sources.append(SourceStatus(name="Scopus", status="error", detail=str(e)))
    elif ids.scopus_id:
        sources.append(SourceStatus(name="Scopus", status="skipped",
                                    url=f"https://www.scopus.com/authid/detail.uri?authorId={ids.scopus_id}",
                                    detail="нет SCOPUS_API_KEY — откройте профиль по ссылке или введите H-index вручную"))
    else:
        sources.append(SourceStatus(name="Scopus", status="skipped", detail="Scopus Author ID не указан"))
    if inp.h_index_scopus_manual is not None:
        m.h_index_scopus = Metric(value=inp.h_index_scopus_manual, source="введено вручную",
                                  url=f"https://www.scopus.com/authid/detail.uri?authorId={ids.scopus_id}" if ids.scopus_id else "")

    # 8. publications --------------------------------------------------------------------
    progress("Сбор и сверка публикаций")
    keys: set[tuple[str, str]] = set()
    for n in [d.basic.full_name, d.basic.full_name_latin, sch.name if sch else "", orc.name if orc else ""] + \
             [a.get("display_name", "") for a in oa_authors]:
        if n:
            keys |= name_keys(n)
    author_ids = set(ids.openalex_ids)

    oa_pubs = []
    if author_ids:
        try:
            oa_pubs = [pubs_mod.from_openalex(w, author_ids, keys)
                       for w in openalex.works_for_authors(sorted(author_ids), y0, y1)]
        except FetchError as e:
            sources.append(SourceStatus(name="OpenAlex (работы)", status="error", detail=str(e)))
    orcid_pubs = []
    if orc:
        known = {p.doi for p in oa_pubs if p.doi}
        win = [w for w in orc.works if w.year and y0 <= w.year <= y1]
        missing = sorted({w.doi for w in win if w.doi and w.doi not in known})
        found = []
        if missing:
            try:
                found = [pubs_mod.from_openalex(w, author_ids, keys) for w in openalex.works_by_dois(missing)]
            except FetchError:
                found = []
        found_dois = {p.doi for p in found}
        orcid_pubs = found + [pubs_mod.from_orcid(w) for w in win if not w.doi or w.doi not in found_dois]
        for p in found:
            p.sources.append("ORCID")
    scholar_pubs = [pubs_mod.from_scholar(w, keys) for w in (sch.publications if sch else [])
                    if w.year and y0 <= w.year <= y1]
    for p in oa_pubs:
        if orc and p.doi and any(w.doi == p.doi for w in orc.works):
            p.sources.append("ORCID")

    merged = [p for p in pubs_mod.merge(oa_pubs, orcid_pubs, scholar_pubs) if pubs_mod.in_window(p, y0, y1)]
    progress("Квартили журналов (SJR)")
    pubs_mod.add_quality(merged)
    merged.sort(key=pubs_mod.sort_key)
    d.publications = merged
    sources.append(SourceStatus(name="SCImago Journal Rank", status="ok" if sjr.available() else "error",
                                url="https://www.scimagojr.com/",
                                detail=f"локальная база SJR до {sjr.max_year()} г." if sjr.available() else "нет data/sjr.sqlite — запустите scripts/build_sjr.py"))

    # 9. metrics ----------------------------------------------------------------------------
    if sch:
        m.h_index_scholar = Metric(value=sch.h_all, source="Google Scholar", url=sch.url)
        m.h_index_scholar_recent = Metric(value=sch.h_recent, source="Google Scholar", url=sch.url, note=sch.recent_label)
        m.i10_scholar = Metric(value=sch.i10_all, source="Google Scholar", url=sch.url)
        m.citations_scholar = Metric(value=sch.citations_all, source="Google Scholar", url=sch.url)
        m.citations_scholar_recent = Metric(value=sch.citations_recent, source="Google Scholar", url=sch.url, note=sch.recent_label)
        m.scholar_recent_label = sch.recent_label
        m.scholar_pubs_in_window = sum(1 for w in sch.publications if w.year and y0 <= w.year <= y1)
    if oa_authors:
        main = max(oa_authors, key=lambda a: a.get("works_count", 0))
        url = main["id"].replace("https://openalex.org/", "https://openalex.org/authors/")
        m.h_index_openalex = Metric(value=(main.get("summary_stats") or {}).get("h_index"), source="OpenAlex", url=url,
                                    note="по основному профилю" if len(oa_authors) > 1 else "")
        m.citations_openalex = Metric(value=sum(a.get("cited_by_count", 0) for a in oa_authors), source="OpenAlex", url=url)
        m.works_openalex = Metric(value=sum(a.get("works_count", 0) for a in oa_authors), source="OpenAlex", url=url)
        topics: dict[str, int] = {}
        for a in oa_authors:
            for t in a.get("topics") or []:
                topics[t["display_name"]] = topics.get(t["display_name"], 0) + t.get("count", 0)
        d.openalex_topics = [t for t, _ in sorted(topics.items(), key=lambda kv: -kv[1])[:10]]

    metrics_mod.compute(d)
    d.checks = metrics_mod.checks(d, findings.claims, scholar_name=sch.name if sch else "",
                                  orcid_name=orc.name if orc else "", scopus_raw=scopus_raw or "",
                                  openalex_confidence=confidence)

    # 10–11. AI steps (recorded for the AI-call log) ----------------------------------------
    with llm.recording() as ai_records:
        _ai_steps(d, cv_text, proposal_text, other_text, texts.get("application", ""),
                  orc.keywords if orc else [], sources, progress)
    d.ai_calls = [_ai_summary(i + 1, r) for i, r in enumerate(ai_records)]
    d.sources = sources

    # 12. save ------------------------------------------------------------------------------
    base = _slug(d.basic.full_name_latin or d.basic.full_name)
    d.id = inp.dossier_id or f"{base}-{hashlib.sha1(json.dumps([base, str(d.created_at)]).encode()).hexdigest()[:6]}"
    save(d)
    save_ai_log(d.id, ai_records)
    progress("Готово")
    return d


def _ai_steps(d: Dossier, cv_text: str, proposal_text: str, other_text: str, form_text: str,
              orcid_keywords: list[str], sources: list[SourceStatus], progress: Progress) -> None:
    progress("Исследовательский профиль")
    d.profile = profile_mod.build(d, cv_text, proposal_text, other_text, orcid_keywords, form_text)

    # stage 2: matching with the ITMO science map
    if matching_mod.available():
        progress("Подбор подразделений ИТМО")
        try:
            d.matching = matching_mod.match(d)
            sources.append(SourceStatus(name="Научная карта ИТМО", status="ok", url="https://en.itmo.ru/en/department_list/Academic_Structure.htm",
                                        detail=f"{d.matching.catalog_size} подразделений в каталоге"))
        except Exception as e:  # the dossier is still useful without stage 2
            log.warning("matching failed: %s", e)
            sources.append(SourceStatus(name="Научная карта ИТМО", status="error", detail=f"{type(e).__name__}: {e}"))
    else:
        sources.append(SourceStatus(name="Научная карта ИТМО", status="skipped",
                                    detail="нет app/data/itmo_units.json — запустите scripts/build_itmo_map.py"))


def _ai_summary(n: int, r: dict) -> AICall:
    return AICall(n=n, purpose=r.get("purpose", ""), provider=r.get("provider", ""), model=r.get("model", ""),
                  started_at=r.get("started_at"), seconds=r.get("seconds", 0.0), attempts=max(1, len(r.get("attempts", []))),
                  status=r.get("status", "failed"), error=r.get("error", ""), input_tokens=r.get("input_tokens"),
                  output_tokens=r.get("output_tokens"), cost_usd=r.get("cost_usd"))


def ai_log_path(dossier_id: str) -> Path:
    return config.LLM_LOG_DIR / f"{dossier_id}.json"


def save_ai_log(dossier_id: str, records: list[dict]) -> None:
    """Full prompts and raw answers, kept next to the dossier (they contain the candidate's documents)."""
    if not records:
        return
    config.LLM_LOG_DIR.mkdir(parents=True, exist_ok=True)
    ai_log_path(dossier_id).write_text(json.dumps(records, ensure_ascii=False, indent=1))


def load_ai_log(dossier_id: str) -> list[dict]:
    p = ai_log_path(dossier_id)
    if not re.fullmatch(r"[a-z0-9-]+", dossier_id) or not p.exists():
        return []
    return json.loads(p.read_text())


def _resolve_by_name(d: Dossier, sch) -> tuple[list[dict], str]:
    """Find the candidate's OpenAlex author record(s) without ORCID.
    Candidates are confirmed by overlapping publication titles with Google Scholar."""
    from app.analysis.publications import norm_title

    names = []
    for n in (sch.name if sch else "", d.basic.full_name_latin, d.basic.full_name):
        n = latin_display_name(n) if n else ""
        if n and n.lower() not in [x.lower() for x in names]:
            names.append(n)
    seen: dict[str, dict] = {}
    for n in names[:2]:
        for a in openalex.search_authors(n):
            seen.setdefault(a["id"], a)
    if not seen:
        return [], ""
    if sch and sch.publications:
        sch_titles = {norm_title(p.title) for p in sch.publications}
        need = 2 if len(sch_titles) >= 5 else 1
        accepted = []
        for a in list(seen.values())[:8]:
            titles = {norm_title(t) for t in openalex.recent_titles(a["id"])}
            if len(titles & sch_titles) >= need:
                accepted.append(a)
        if accepted:
            return accepted, "medium"
    keys = set()
    for n in names:
        keys |= name_keys(n)
    best = [a for a in seen.values() if name_keys(a.get("display_name", "")) & keys]
    return (best[:1], "low") if best else ([], "")
