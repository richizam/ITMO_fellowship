"""Stage 2 — match the candidate's research profile to the ITMO science map.

Candidate side: proposal title and summary, research interests, methods, keywords, OpenAlex topics and the
titles of publications in the window. ITMO side: the unit catalog built from the official unit pages
(app/data/itmo_units.json, see scripts/build_itmo_map.py), each description split into chunks.

Score = 0.75 · semantic similarity (multilingual sentence embeddings, works across Russian and English)
      + 0.25 · lexical similarity (TF-IDF over English terms),
both min-max normalised over the catalog. For every unit the evidence is kept: which candidate items matched
it best and which terms are shared. With an LLM key (DeepSeek or Claude) the top units are re-ranked by the model, which also writes
the rationale and possible collaboration directions.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
from rapidfuzz import fuzz

from app import config
from app.models import Dossier, Matching, UnitMatch

log = logging.getLogger(__name__)

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "itmo_units.json"
MODEL_NAME = config.EMBED_MODEL
SEMANTIC_WEIGHT = 0.75
# share of each kind of evidence in the semantic score, so that 40 publication titles do not drown out
# the proposal: what the candidate plans to do at ITMO matters most
GROUP_WEIGHTS = {"proposal": 0.30, "profile": 0.30, "methods": 0.10, "publications": 0.30}
KIND_GROUP = {"тема проекта": "proposal", "проект": "proposal", "интерес": "profile", "ключевое слово": "profile",
              "тема OpenAlex": "profile", "прикладное направление": "profile", "метод": "methods",
              "публикация": "publications"}
TOP_N = 6
# broad umbrella units are kept for context (parent) but not recommended themselves
NOT_RECOMMENDED = {"Мегафакультет"}

# Russian abbreviations that appear in applications
ABBREVIATIONS = {
    r"\bпиш\b": "Передовая инженерная школа Advanced Engineering School",
    r"\bмноц\b": "Международный научно-образовательный центр International Research and Educational Center",
    r"\bноц\b": "Научно-образовательный центр Research and Educational Center",
    r"\bниц\b": "Научно-исследовательский центр Research Center",
    r"\bфтмф\b": "Физико-технический мегафакультет School of Physics and Engineering",
}


# ----------------------------------------------------------------------------------------- catalog
@lru_cache(maxsize=1)
def catalog() -> list[dict]:
    if not CATALOG_PATH.exists():
        return []
    return json.loads(CATALOG_PATH.read_text())


def available() -> bool:
    return bool(catalog())


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 20]


def unit_chunks(u: dict, words_per_chunk: int = 45, max_chunks: int = 14) -> list[str]:
    """The model reads ~128 tokens, so long descriptions are split into short chunks."""
    head = u["name_en"] + (f". {u['parent']}" if u.get("parent") else "")
    chunks = [head]
    buf: list[str] = []
    for s in _sentences(u.get("description_en", "")):
        buf.append(s)
        if sum(len(x.split()) for x in buf) >= words_per_chunk:
            chunks.append(" ".join(buf))
            buf = []
        if len(chunks) >= max_chunks:
            break
    if buf and len(chunks) < max_chunks:
        chunks.append(" ".join(buf))
    return chunks


def unit_text(u: dict) -> str:
    return f"{u['name_en']}. {u.get('parent', '')}. {u.get('description_en', '')}"


# ----------------------------------------------------------------------------------------- embeddings
@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding

    return TextEmbedding(MODEL_NAME, cache_dir=str(config.MODELS_DIR))


def embed(texts: list[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, 1), dtype=np.float32)
    v = np.array(list(_model().embed(texts)), dtype=np.float32)
    return v / np.clip(np.linalg.norm(v, axis=1, keepdims=True), 1e-9, None)


@lru_cache(maxsize=1)
def _index() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(chunk vectors, unit index of every chunk, name vectors) — cached on disk per catalog + model."""
    units = catalog()
    key = hashlib.sha1((MODEL_NAME + json.dumps(units, sort_keys=True)).encode()).hexdigest()[:12]
    path = config.MODELS_DIR / f"itmo_index_{key}.npz"
    if path.exists():
        z = np.load(path)
        return z["chunks"], z["owner"], z["names"]
    chunks, owner = [], []
    for i, u in enumerate(units):
        for c in unit_chunks(u):
            chunks.append(c)
            owner.append(i)
    names = [u["name_en"] for u in units]
    cv, nv = embed(chunks), embed(names)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, chunks=cv, owner=np.array(owner), names=nv)
    return cv, np.array(owner), nv


# ----------------------------------------------------------------------------------------- candidate
def candidate_items(d: Dossier) -> list[tuple[str, float, str]]:
    """(text, weight, kind) — what the candidate works on, most specific first."""
    p = d.profile
    items: list[tuple[str, float, str]] = []
    if p.proposal_title:
        items.append((p.proposal_title, 3.0, "тема проекта"))
    for s in _sentences(p.proposal_summary)[:4]:
        items.append((s, 1.5, "проект"))
    for x in p.interests + d.scholar_interests:
        items.append((x, 2.0, "интерес"))
    for x in p.keywords:
        items.append((x, 1.5, "ключевое слово"))
    for x in d.openalex_topics[:6]:
        items.append((x, 1.5, "тема OpenAlex"))
    for x in p.methods:
        items.append((x, 1.0, "метод"))
    for x in p.applied_directions:
        items.append((x, 1.0, "прикладное направление"))
    for pub in d.publications[:40]:
        if pub.title:
            items.append((pub.title, 1.2 if pub.is_first else 1.0, "публикация"))
    seen: set[str] = set()
    out = []
    for text, w, kind in items:
        k = text.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append((text.strip(), w, kind))
    return out


def _expand_abbreviations(s: str) -> str:
    low = s.lower()
    for pat, full in ABBREVIATIONS.items():
        if re.search(pat, low):
            s = f"{s} ({full})"
    return s


def _translit_key(s: str) -> str:
    """'Инфохимии' and the slug 'infohimii' -> the same key (different transliteration schemes)."""
    from app.analysis.names import translit

    s = translit(s).lower()
    for a, b in (("kh", "h"), ("ts", "c"), ("yy", "y"), ("iy", "i"), ("y", "i"), ("j", "i"), ("e", "e")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z]", "", s)


def resolve_unit(name: str) -> tuple[int | None, float]:
    """Best catalog match for a unit name written in an application (any language, abbreviations)."""
    units = catalog()
    if not name.strip() or not units:
        return None, 0.0
    q = _expand_abbreviations(name)
    _, _, names = _index()
    # compare the name as written and with abbreviations expanded; keep the better match per unit
    sims = (names @ embed([name, q]).T).max(axis=1) if q != name else names @ embed([name])[0]
    best, best_score = None, 0.0
    acronyms = set(re.findall(r"\b[A-ZА-Я]{3,}\b", name)) - {"ITMO", "ИТМО"}
    # Russian names: compare the transliterated words with the slug of the unit's Russian page
    ru_words = [_translit_key(w) for w in re.findall(r"[А-Яа-яЁё]{6,}", name)]
    for i, u in enumerate(units):
        score = float(sims[i])
        slug = u.get("url_ru", "").rsplit("/", 1)[-1].replace(".htm", "")
        slug_words = [_translit_key(w) for w in re.split(r"[_\-]+", slug) if len(w) >= 4]
        if ru_words and slug_words and all(max(fuzz.ratio(w, sw) for sw in slug_words) >= 90 for w in ru_words):
            score = max(score, 0.92)
        score = max(score, fuzz.token_set_ratio(q.lower(), u["name_en"].lower()) / 100 * 0.95)
        hay = u["name_en"] + " " + u.get("description_en", "")[:600]
        if any(re.search(rf"\b{re.escape(a)}\b", hay) for a in acronyms):
            score = max(score, 0.9)
        if score > best_score:
            best, best_score = i, score
    return (best, best_score) if best_score >= 0.6 else (None, best_score)


# ----------------------------------------------------------------------------------------- matching
def _norm(x: np.ndarray) -> np.ndarray:
    span = float(x.max() - x.min()) if x.size else 0.0
    return (x - x.min()) / span if span > 1e-9 else np.zeros_like(x)


# English stop words + words that every unit description or CV contains
STOP_WORDS = sorted(set(__import__("sklearn.feature_extraction.text", fromlist=["x"]).ENGLISH_STOP_WORDS) | {
    "university", "itmo", "lab", "laboratory", "laboratories", "research", "researchers", "center", "centre",
    "centers", "faculty", "school", "institute", "department", "student", "students", "program", "programs",
    "master", "bachelor", "phd", "project", "projects", "study", "studies", "work", "works", "based", "new",
    "different", "level", "second", "first", "using", "use", "used", "approach", "approaches", "including",
    "development", "field", "fields", "area", "areas", "team", "teams", "year", "years", "international",
    "scientific", "science", "sciences", "education", "educational", "academic", "russia", "russian",
    "saint", "petersburg", "st", "high", "results", "methods", "method", "analysis", "data", "community",
    "efficient", "various", "main", "key", "leading", "modern", "currently", "also", "well"})


def _lexical(cand_text: str, units: list[dict]) -> tuple[np.ndarray, list[list[str]]]:
    from sklearn.feature_extraction.text import TfidfVectorizer

    docs = [unit_text(u) for u in units]
    vec = TfidfVectorizer(stop_words=STOP_WORDS, ngram_range=(1, 2), sublinear_tf=True, min_df=1,
                          token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z\-]{2,}\b")
    m = vec.fit_transform(docs + [cand_text])
    units_m, cand = m[:-1], m[-1]
    sims = (units_m @ cand.T).toarray().ravel()
    terms = vec.get_feature_names_out()
    shared = []
    cand_dense = cand.toarray().ravel()
    for i in range(units_m.shape[0]):
        prod = units_m[i].toarray().ravel() * cand_dense
        top = [terms[j] for j in prod.argsort()[::-1][:8] if prod[j] > 0]
        # drop unigrams already covered by a bigram
        top = [t for t in top if not any(t != o and t in o.split() for o in top)][:6]
        shared.append(top)
    return sims, shared


def match(d: Dossier, top_n: int = TOP_N) -> Matching:
    units = catalog()
    result = Matching(method="multilingual embeddings (paraphrase-multilingual-MiniLM-L12-v2) + TF-IDF",
                      catalog_size=len(units), catalog_source="en.itmo.ru — официальные страницы подразделений",
                      applied_unit=d.basic.host_unit)
    items = candidate_items(d)
    if not units or not items:
        return result

    chunk_vecs, owner, _ = _index()
    texts = [t for t, _, _ in items]
    weights = np.array([w for _, w, _ in items], dtype=np.float32)
    item_vecs = embed(texts)
    sims = item_vecs @ chunk_vecs.T  # items × chunks
    # best chunk per (item, unit): chunks of one unit are contiguous
    starts = np.flatnonzero(np.r_[True, owner[1:] != owner[:-1]])
    best = np.maximum.reduceat(sims, starts, axis=1)  # items × units
    semantic = np.zeros(best.shape[1], dtype=np.float32)
    total = 0.0
    kinds = np.array([KIND_GROUP.get(k, "profile") for _, _, k in items])
    for group, share in GROUP_WEIGHTS.items():
        mask = kinds == group
        if mask.any():
            w = weights[mask]
            semantic += share * (w @ best[mask]) / w.sum()
            total += share
    semantic /= total

    cand_text = " ".join(texts)
    lexical, shared = _lexical(cand_text, units)
    final = SEMANTIC_WEIGHT * _norm(semantic) + (1 - SEMANTIC_WEIGHT) * _norm(lexical)

    order = [i for i in np.argsort(-final) if units[i].get("type") not in NOT_RECOMMENDED]
    applied_idx, _ = resolve_unit(d.basic.host_unit)
    if applied_idx is not None:
        result.applied_unit_id = units[applied_idx]["id"]
        result.applied_unit_name = units[applied_idx]["name_en"]
        if applied_idx in order:
            result.applied_unit_rank = order.index(applied_idx) + 1

    shortlist: dict[str, UnitMatch] = {}
    for i in order[:12]:
        u = units[i]
        contrib = best[:, i] * weights
        ev_idx = [int(j) for j in np.argsort(-contrib)[:3]]
        evidence = [f"{items[j][2]}: {items[j][0][:140]}" for j in ev_idx]
        shortlist[u["id"]] = (UnitMatch(
            unit_id=u["id"], name=u["name_en"], type=u.get("type", ""), parent=u.get("parent", ""),
            head=u.get("head", ""), homepage=u.get("homepage", ""), url=u.get("url_en", ""),
            score=int(round(float(final[i]) * 100)), semantic=round(float(semantic[i]), 3),
            lexical=round(float(lexical[i]), 3), evidence=evidence, shared_terms=shared[i],
            rationale="Наибольшее сходство: " + "; ".join(items[j][0][:80] for j in ev_idx[:2]),
            is_applied_unit=(i == applied_idx),
        ))
    result.recommendations = list(shortlist.values())[:top_n]

    if config.llm_enabled():
        try:
            _rerank_with_llm(d, result, [units[i] for i in order[:12]], shortlist)
            # final ranking = the model's picks, then the rest in embedding order
            picked = [m.unit_id for m in result.recommendations]
            final_ids = picked + [units[i]["id"] for i in order if units[i]["id"] not in picked]
            if result.applied_unit_id in final_ids:
                result.applied_unit_rank = final_ids.index(result.applied_unit_id) + 1
        except Exception as e:  # keep the embedding ranking if the LLM step fails
            log.warning("LLM re-ranking failed: %s", e)
    return result


# ----------------------------------------------------------------------------------------- LLM re-rank
def _rerank_with_llm(d: Dossier, result: Matching, shortlist: list[dict], computed: dict[str, UnitMatch]) -> None:
    from pydantic import BaseModel, Field

    from app import llm

    class _Pick(BaseModel):
        unit_id: str = Field(description="id of a unit from the shortlist")
        rationale: str = Field(description="1-2 sentences in Russian: why this unit fits the candidate")
        directions: list[str] = Field(description="2-3 concrete collaboration directions in Russian")

    class _Ranking(BaseModel):
        picks: list[_Pick] = Field(description="up to 6 most relevant units, best first")

    by_id = {u["id"]: u for u in shortlist}
    units_txt = "\n\n".join(
        f"[{u['id']}] {u['name_en']} ({u.get('type', '')}; {u.get('parent', '')})\n{u.get('description_en', '')[:700]}"
        for u in shortlist)
    p = d.profile
    cand = (f"Резюме: {p.summary}\nТема проекта: {p.proposal_title}\nОписание проекта: {p.proposal_summary}\n"
            f"Интересы: {', '.join(p.interests)}\nМетоды: {', '.join(p.methods)}\n"
            f"Публикации: " + "; ".join(x.title for x in d.publications[:20]))
    system = ("Ты помогаешь программе ITMO Fellowship подобрать подразделения ИТМО для научного сотрудничества "
              "с кандидатом. Выбирай только из списка подразделений (по их id), опирайся на их описания и профиль "
              "кандидата. Пиши по-русски.")
    ranking, generated_by = llm.structured(
        system, f"<candidate>\n{cand}\n</candidate>\n\n<units>\n{units_txt}\n</units>", _Ranking, max_tokens=8000)
    current = computed
    ranked: list[UnitMatch] = []
    for pick in ranking.picks:
        u = by_id.get(pick.unit_id)
        if not u or any(m.unit_id == pick.unit_id for m in ranked):
            continue
        m = current.get(pick.unit_id) or UnitMatch(
            unit_id=u["id"], name=u["name_en"], type=u.get("type", ""), parent=u.get("parent", ""),
            head=u.get("head", ""), homepage=u.get("homepage", ""), url=u.get("url_en", ""),
            is_applied_unit=(u["id"] == result.applied_unit_id))
        m.rationale, m.directions = pick.rationale, pick.directions
        ranked.append(m)
    if ranked:
        result.recommendations = ranked[:TOP_N]
        result.generated_by = generated_by


def warmup() -> None:
    """Download the model and build the catalog index (used at Docker build time)."""
    print(f"{len(catalog())} units; index shape {_index()[0].shape}")


if __name__ == "__main__" and "--warmup" in sys.argv:
    warmup()
