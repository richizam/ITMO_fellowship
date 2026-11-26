"""Publication-activity metrics over the window and consistency checks for the expert."""
from __future__ import annotations

import re
from collections import Counter
from datetime import date

from app.analysis.names import name_keys, similarity
from app.ingest.identifiers import Claim
from app.models import Check, Dossier, Metrics


def compute(d: Dossier) -> None:
    m: Metrics = d.metrics
    pubs = d.publications
    m.pubs_in_window = len(pubs)
    by_year = Counter(p.year for p in pubs if p.year)
    m.pubs_by_year = {y: by_year.get(y, 0) for y in range(d.window_from, d.window_to + 1)}
    last_full = min(d.window_to, date.today().year - 1)
    m.gap_years = [y for y in range(d.window_from, last_full + 1) if by_year.get(y, 0) == 0]
    q = Counter((p.quality.quartile if p.quality and p.quality.quartile else "—") for p in pubs)
    m.quartiles = {k: q.get(k, 0) for k in ("Q1", "Q2", "Q3", "Q4", "—")}
    m.first_author = sum(p.is_first for p in pubs)
    m.last_author = sum(p.is_last for p in pubs)
    m.corresponding_author = sum(p.is_corresponding for p in pubs)
    top = [p for p in pubs if p.quality and p.quality.quartile in ("Q1", "Q2")]
    m.q1q2_any = len(top)
    m.q1q2_first_author = sum(p.is_first for p in top)
    m.citations_window = sum(p.citations or 0 for p in pubs)


def checks(d: Dossier, claims: list[Claim], scholar_name: str = "", orcid_name: str = "",
           scopus_raw: str = "", openalex_confidence: str = "") -> list[Check]:
    out: list[Check] = []
    ids = d.identifiers
    m = d.metrics

    # --- identifiers ----------------------------------------------------------------
    if not ids.orcid:
        out.append(Check(level="warn", topic="Идентификаторы",
                         message="ORCID нет ни в заявке, ни в CV. Публикации найдены по имени через Google Scholar и OpenAlex."))
    if scopus_raw and not ids.scopus_id:
        out.append(Check(level="warn", topic="Идентификаторы",
                         message=f"В ссылке на Scopus нет Author ID (указано: «{scopus_raw[:80]}»). Нужна ссылка вида scopus.com/authid/detail.uri?authorId=…"))
    elif not ids.scopus_id:
        out.append(Check(level="warn", topic="Идентификаторы", message="Scopus Author ID не указан."))
    if not ids.scholar_id:
        out.append(Check(level="warn", topic="Идентификаторы", message="Профиль Google Scholar не указан."))

    # --- profile ownership ---------------------------------------------------------------
    cand = d.basic.full_name_latin or d.basic.full_name
    for label, other in (("Google Scholar", scholar_name), ("ORCID", orcid_name)):
        other = re.sub(r"\([^)]*\)", "", other or "").strip()  # "Ivan Testov (PhD, Example University)"
        same_person = bool(name_keys(cand) & name_keys(other)) or similarity(cand, other) >= 0.6
        if cand and other and not same_person:
            out.append(Check(level="warn", topic="Профили",
                             message=f"Имя в профиле {label} («{other}») не совпадает с именем в заявке («{cand}»). Проверьте, что это профиль кандидата."))
    if openalex_confidence == "low":
        out.append(Check(level="warn", topic="Профили",
                         message="Профиль OpenAlex найден только по имени, поэтому в списке могут быть статьи однофамильцев. Проверьте его."))

    # --- self-reported metrics vs external data ---------------------------------------------
    out += h_index_checks(claims, m)

    # --- publication activity --------------------------------------------------------------
    if m.gap_years:
        out.append(Check(level="warn", topic="Публикационная активность",
                         message="Перерыв в публикациях: нет статей за " + ", ".join(map(str, m.gap_years)) + " г."))
    if m.pubs_in_window and m.q1q2_first_author == 0:
        out.append(Check(level="info", topic="Публикационная активность",
                         message="За период нет статей Q1/Q2 в качестве первого автора."))
    unknown_pos = sum(1 for p in d.publications if p.author_position is None)
    if unknown_pos:
        out.append(Check(level="info", topic="Публикационная активность",
                         message=f"Для {unknown_pos} публикаций не удалось определить место кандидата среди авторов: список авторов слишком длинный или сокращён."))
    return out


KNOWN_DB = ("Scopus", "Google Scholar")


def h_index_checks(claims: list[Claim], m: Metrics) -> list[Check]:
    """Compare the H-index the candidate wrote in the documents with the databases.

    A value that names its database is compared with that database only, because Scopus and Google Scholar
    differ systematically. A value without a database is compared with Scopus, Google Scholar or OpenAlex,
    whichever is available first."""
    actual = {"Scopus": m.h_index_scopus.value, "Google Scholar": m.h_index_scholar.value,
              "OpenAlex": m.h_index_openalex.value}
    oa = m.h_index_openalex.value
    out: list[Check] = []
    for c in claims:
        if c.metric != "h_index":
            continue
        named = c.source in KNOWN_DB
        said = f"В CV указан H-index {c.value} ({c.source})" if named else f"В CV указан H-index {c.value} без названия базы"
        if named:
            ref_src, ref = c.source, actual[c.source]
        else:
            ref_src, ref = next(((s, actual[s]) for s in ("Scopus", "Google Scholar", "OpenAlex") if actual[s] is not None),
                                (None, None))
        if ref is None:
            msg = f"{said}. Значение {c.source} получить не удалось, поэтому сверить его нельзя." if named else \
                f"{said}. Ни одна база не дала H-index, поэтому сверить его нельзя."
            if c.source == "Scopus":
                msg += " H-index Scopus можно ввести вручную в форме заявки."
            if named and oa is not None:
                msg += f" Для ориентира: OpenAlex показывает {int(oa)}."
            out.append(Check(level="info", topic="Сверка с CV", message=msg))
            continue
        ref = int(ref)
        diff = c.value - ref
        if diff > 1:
            out.append(Check(level="warn", topic="Сверка с CV",
                             message=f"{said}, а {ref_src} показывает {ref}. Значение в CV завышено или относится к другой базе."))
        elif diff < -1:
            out.append(Check(level="info", topic="Сверка с CV",
                             message=f"{said}, а {ref_src} показывает {ref}. Вероятно, CV написано давно."))
        else:
            out.append(Check(level="ok", topic="Сверка с CV",
                             message=f"H-index из CV ({c.value}) совпадает с {ref_src} ({ref})."))
    return out
