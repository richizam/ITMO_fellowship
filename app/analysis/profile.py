"""Research profile of the candidate: interests, methods, competencies, proposal summary.

With ANTHROPIC_API_KEY set, Claude reads the CV, the research proposal and the
verified publication list and writes the profile (structured output). Without a key a
transparent heuristic is used, built from Scholar interests, ORCID keywords, OpenAlex
topics and simple text rules.
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from app import config
from app.models import Dossier, ResearchProfile

log = logging.getLogger(__name__)


class _LLMProfile(BaseModel):
    candidate_name_latin: str = Field(description="Full name in Latin script as used in publications")
    current_position: str = Field(description="Current job title, empty if unknown")
    current_organization: str = Field(description="Current employer, empty if unknown")
    summary: str = Field(description="4-6 sentences in Russian: who the candidate is, research field, main results, what they propose for ITMO")
    interests: list[str] = Field(description="3-8 research interests / topics, Russian")
    methods: list[str] = Field(description="Concrete methods, instruments, techniques, software the candidate uses")
    competencies: list[str] = Field(description="Key competencies: research, engineering, teaching, management, grants")
    applied_directions: list[str] = Field(description="Potential applied directions / industries for the expertise, Russian")
    keywords: list[str] = Field(description="5-12 English keywords for matching with ITMO units")
    proposal_title: str = Field(description="Title of the research proposal / course, original language")
    proposal_summary: str = Field(description="3-5 sentences in Russian: goals, planned results, collaboration format with ITMO")
    expert_notes: list[str] = Field(description="2-5 short notes for the expert in Russian: strengths, open questions, inconsistencies between documents")


SYSTEM = (
    "Ты — аналитик программы ITMO Fellowship (международная программа привлечения учёных в Университет ИТМО). "
    "Ты готовишь краткое аналитическое досье кандидата для экспертной комиссии. "
    "Пиши по-русски, нейтрально и фактологично. Используй только сведения из предоставленных документов и данных "
    "наукометрических баз; ничего не додумывай. Если сведений нет — оставь поле пустым. "
    "Английские научные термины, названия методов и журналов можно оставлять на английском."
)


def build(d: Dossier, cv_text: str, proposal_text: str, extra_text: str,
          orcid_keywords: list[str]) -> ResearchProfile:
    if config.llm_enabled():
        try:
            return _with_claude(d, cv_text, proposal_text, extra_text)
        except Exception as e:  # never lose the dossier because of the LLM step
            log.warning("Claude profile failed, falling back to heuristic: %s", e)
            prof = _heuristic(d, cv_text, proposal_text, orcid_keywords)
            prof.expert_notes.insert(0, f"Резюме сформировано без LLM: ошибка вызова Claude ({type(e).__name__}).")
            return prof
    return _heuristic(d, cv_text, proposal_text, orcid_keywords)


# --------------------------------------------------------------------------- Claude
def _publication_digest(d: Dossier) -> str:
    lines = []
    for p in d.publications:
        q = p.quality.quartile if p.quality and p.quality.quartile else "—"
        pos = f"{p.author_position}/{p.n_authors or '?'}" if p.author_position else "?"
        lines.append(f"- {p.year} | {q} | {p.venue} | автор {pos} | {p.title}")
    return "\n".join(lines) or "(нет данных)"


def _with_claude(d: Dossier, cv: str, proposal: str, extra: str) -> ResearchProfile:
    import anthropic

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    b = d.basic
    m = d.metrics
    facts = (
        f"Имя (заявка): {b.full_name}\nТрек: {b.track}\nОрганизация: {b.organization}\nДолжность: {b.job_title}\n"
        f"Подразделение ИТМО в заявке: {b.host_unit}\nКонтакт в ИТМО: {b.itmo_contact}\n"
        f"Google Scholar интересы: {', '.join(d.scholar_interests)}\nOpenAlex темы: {', '.join(d.openalex_topics)}\n"
        f"H-index Scholar: {m.h_index_scholar.value}; Scopus: {m.h_index_scopus.value}; OpenAlex: {m.h_index_openalex.value}\n"
        f"Публикации {d.window_from}–{d.window_to}: {m.pubs_in_window}, Q1/Q2: {m.q1q2_any}, Q1/Q2 первым автором: {m.q1q2_first_author}"
    )
    content = (
        f"<facts>\n{facts}\n</facts>\n\n"
        f"<publications period=\"{d.window_from}-{d.window_to}\">\n{_publication_digest(d)}\n</publications>\n\n"
        f"<cv>\n{cv or '(не предоставлено)'}\n</cv>\n\n"
        f"<research_proposal>\n{proposal or '(не предоставлено)'}\n</research_proposal>\n\n"
        + (f"<other_documents>\n{extra}\n</other_documents>\n\n" if extra else "")
        + "Составь исследовательский профиль кандидата по схеме."
    )
    response = client.beta.messages.parse(
        model=config.CLAUDE_MODEL,
        max_tokens=16000,
        system=SYSTEM,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium"},
        output_format=_LLMProfile,
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise RuntimeError(f"no structured output (stop_reason={response.stop_reason})")
    o = response.parsed_output
    if not d.basic.full_name_latin and o.candidate_name_latin:
        d.basic.full_name_latin = o.candidate_name_latin
    if not d.basic.job_title and o.current_position:
        d.basic.job_title = o.current_position
    if not d.basic.organization and o.current_organization:
        d.basic.organization = o.current_organization
    return ResearchProfile(
        summary=o.summary, interests=o.interests, methods=o.methods, competencies=o.competencies,
        applied_directions=o.applied_directions, keywords=o.keywords, proposal_title=o.proposal_title,
        proposal_summary=o.proposal_summary, expert_notes=o.expert_notes,
        generated_by=f"claude:{response.model}",
    )


# --------------------------------------------------------------------------- heuristic
METHOD_PATTERNS = [
    r"machine learning", r"deep learning", r"neural networks?", r"computer vision", r"natural language processing",
    r"large language models?", r"reinforcement learning", r"explainable ai", r"federated learning", r"transfer learning",
    r"data mining", r"big data", r"internet of things|\biot\b", r"blockchain", r"edge computing", r"cloud computing",
    r"bioinformatics", r"genome assembly", r"metagenomic\w*", r"sequencing", r"phylogenet\w*", r"molecular dynamics",
    r"density functional theory|\bdft\b", r"monte carlo", r"finite element\w*", r"computational fluid dynamics|\bcfd\b",
    r"chemometrics", r"design of experiments?", r"response surface methodology", r"spectroscopy", r"spectrometry",
    r"chromatography", r"mass spectrometry", r"x-ray diffraction|\bxrd\b", r"electron microscopy|\bsem\b|\btem\b",
    r"nmr", r"ftir", r"laser-induced breakdown spectroscopy|\blibs\b", r"electrochemi\w+", r"photocataly\w+",
    r"nanomaterials?", r"nanoparticles?", r"thin films?", r"polymer\w*", r"hydrogels?", r"biosensors?",
    r"microbial fuel cells?", r"wastewater treatment", r"membrane\w*", r"adsorption", r"encapsulation",
    r"emulsions?", r"oleogels?", r"food chemistry", r"metabolomics", r"proteomics", r"genetic epidemiology",
    r"genome-wide association|\bgwas\b", r"mendelian randomi[sz]ation", r"biostatistics", r"epidemiolog\w+",
    r"gis\b|geographic information systems?", r"remote sensing", r"urban planning", r"life cycle assessment",
    r"underground space", r"smart cit\w+", r"optimization", r"control systems?", r"robotics",
    r"signal processing", r"image processing", r"cybersecurity|information security", r"wireless sensor networks?",
    r"power systems?", r"renewable energy", r"energy efficiency", r"photonics", r"optics", r"quantum\w*",
]


def _find_methods(text: str, limit: int = 12) -> list[str]:
    low = text.lower()
    found: list[tuple[int, str]] = []
    for pat in METHOD_PATTERNS:
        hits = list(re.finditer(pat, low))
        if len(hits) >= 2:  # a single mention is usually noise (education, hobbies)
            label = hits[0].group(0)
            found.append((len(hits), label))
    found.sort(reverse=True)
    out = []
    for _, label in found:
        if label not in out:
            out.append(label)
    return out[:limit]


_HEADING = re.compile(
    r"^(?:\d+[.)]\s*)?(?:abstract|introduction|background|scope|summary|objectives?|aims?|personal statement|motivation|"
    r"methodology|methods|research plan|state of the art|введение|аннотация|актуальность|цель|задачи|резюме|"
    r"applied research|descriptive summary)\b", re.I)
_TITLE_HEADING = re.compile(r"^(?:\d+[.)]\s*)?(?:project\s+title|title(?:\s+of\s+the\s+project)?|название\s+проекта|тема\s+проекта|тема)\s*[:\-–]?\s*(.*)$", re.I)
_BOILER = re.compile(r"(fellowship|research proposal|project proposal|concept note|предложение по проекту|"
                     r"^proposer|^руководитель|^applicant|^supervisor|^(?:dr|prof)\.?\s|course syllabus|^заявка)", re.I)


def _proposal_title(text: str, name: str = "") -> str:
    """Title = the lines before the first section heading, skipping boilerplate and the author's name.
    Returns "" rather than a wrong guess (e.g. a personal statement without a title)."""
    surnames = {t.lower() for t in re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", name)}
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln][:30]
    out: list[str] = []
    explicit = False
    for ln in lines:
        m = _TITLE_HEADING.match(ln)
        if m and not out:
            explicit = True
            if m.group(1):
                out.append(m.group(1))
            continue
        is_heading = bool(_HEADING.match(ln)) and len(ln.split()) <= 6
        is_numbered = bool(re.match(r"^\d+[.)]\s+\w", ln))
        if is_heading or (is_numbered and (out or not explicit)):
            break
        is_name = any(w.lower() in surnames for w in re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", ln)) and len(ln.split()) <= 6
        if _BOILER.search(ln) or is_name or "@" in ln:
            if out:
                break
            continue
        out.append(ln)
        if len(" ".join(out)) > 220:
            break
    title = " ".join(out).strip(" \"“”«»'")
    return title[:250] if len(title.split()) >= 3 else ""


def _proposal_summary(text: str) -> str:
    m = re.search(r"(?:abstract|summary|аннотация|резюме проекта|project description|introduction)\s*:?\s*\n(.{200,1500}?)(?:\n\s*\n|$)",
                  text, re.I | re.S)
    chunk = m.group(1) if m else text[:1200]
    chunk = re.sub(r"\s+", " ", chunk).strip()
    sentences = re.split(r"(?<=[.!?])\s+", chunk)
    return " ".join(sentences[:4])[:900]


def _heuristic(d: Dossier, cv: str, proposal: str, orcid_keywords: list[str]) -> ResearchProfile:
    interests: list[str] = []
    for src in (d.scholar_interests, orcid_keywords, d.openalex_topics[:6]):
        for x in src:
            x = x.strip()
            if x and x.lower() not in {i.lower() for i in interests}:
                interests.append(x)
    methods = _find_methods(cv + "\n" + proposal)
    b, m = d.basic, d.metrics
    who = b.full_name_latin or b.full_name
    role = ", ".join(x for x in (b.job_title, b.organization) if x)
    summary = f"{who}" + (f" — {role}." if role else ".")
    if interests:
        summary += " Основные направления исследований: " + ", ".join(interests[:5]) + "."
    summary += (f" За {d.window_from}–{d.window_to} гг. найдено {m.pubs_in_window} публикаций, из них в журналах Q1/Q2 — "
                f"{m.q1q2_any} (первым автором — {m.q1q2_first_author}).")
    if b.host_unit:
        summary += f" В заявке указано подразделение ИТМО: {b.host_unit}."
    return ResearchProfile(
        summary=summary,
        interests=interests[:10],
        methods=methods,
        competencies=[],
        applied_directions=[],
        keywords=[k for k in (d.openalex_topics + orcid_keywords)][:10],
        proposal_title=_proposal_title(proposal, f"{b.full_name} {b.full_name_latin}"),
        proposal_summary=_proposal_summary(proposal) if proposal else "",
        expert_notes=["Автоматическое извлечение без LLM. Задайте ANTHROPIC_API_KEY, чтобы получить полное резюме, "
                      "компетенции и прикладные направления от Claude."],
        generated_by="heuristic",
    )
