"""Excel export: a full dossier workbook and the side-by-side summary in the format of
the Fellowship team's own spreadsheet ("Кандидаты.xlsx")."""
from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.models import Dossier

HEADER_FILL = PatternFill("solid", fgColor="7F00FF")
HEADER_FONT = Font(bold=True, color="FFFFFF")
LABEL_FONT = Font(bold=True)
WRAP = Alignment(wrap_text=True, vertical="top")


def _fmt_date(iso: str) -> str:
    if len(iso) == 10 and iso[4] == "-":
        y, m, d = iso.split("-")
        return f"{d}.{m}.{y}"
    return iso


def _num(v) -> str | int | float:
    return "—" if v is None else v


def summary_rows(d: Dossier) -> list[tuple[str, object]]:
    """Rows of the team's template, in the same order."""
    b, m, p = d.basic, d.metrics, d.profile
    name = b.full_name + (f" ({b.full_name_latin})" if b.full_name_latin and b.full_name_latin != b.full_name else "")
    work = ", ".join(x for x in (b.job_title, b.organization) if x)
    sphere = p.proposal_title or "; ".join(p.interests[:4])
    pubs = (m.scholar_pubs_in_window if m.scholar_pubs_in_window is not None else m.pubs_in_window)
    pubs_txt = f"{pubs}"
    if m.scholar_pubs_in_window is not None and m.scholar_pubs_in_window != m.pubs_in_window:
        pubs_txt += f" (в досье после сверки источников: {m.pubs_in_window})"
    if m.gap_years:
        pubs_txt += f"\n* перерыв: {', '.join(map(str, m.gap_years))}"
    return [
        ("First and Last Name", name),
        ("Program", b.track),
        ("Date of birth", _fmt_date(b.date_of_birth)),
        ("Age", _num(b.age)),
        ("Citizenship", b.citizenship),
        ("Current work place", work),
        ("Research sphere (Project title/ lectures topic) (proposal)", sphere),
        (f"Number of publications in {d.window_from}-{d.window_to} (Scholar) \n * break in publishing, if any", pubs_txt),
        ("Публикации в журналах Q1 или Q2 в качестве первого автора", m.q1q2_first_author),
        ("H-index (Scopus)", _num(m.h_index_scopus.value)),
        ("H-index (Scholar)", _num(m.h_index_scholar.value)),
        ("Host Unit (LOS)", b.host_unit),
        ("Head of the host Unit", b.itmo_contact),
        ("Letter of Support signed by", ""),
    ]


def summary_workbook(dossiers: list[Dossier]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Кандидаты"
    labels = [r[0] for r in summary_rows(dossiers[0])] if dossiers else []
    ws.cell(row=1, column=1, value="Fellowship Candidate Intelligence — сводка").font = Font(bold=True, size=13)
    for i, label in enumerate(labels):
        c = ws.cell(row=3 + i, column=1, value=label)
        c.font, c.alignment = LABEL_FONT, WRAP
    for j, d in enumerate(dossiers):
        col = 2 + j
        h = ws.cell(row=2, column=col, value=j + 1)
        h.fill, h.font = HEADER_FILL, HEADER_FONT
        for i, (_, value) in enumerate(summary_rows(d)):
            c = ws.cell(row=3 + i, column=col, value=value)
            c.alignment = WRAP
        ws.column_dimensions[get_column_letter(col)].width = 34
    ws.column_dimensions["A"].width = 46
    return _bytes(wb)


def dossier_workbook(d: Dossier) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Карточка"
    for i, (label, value) in enumerate(summary_rows(d), start=1):
        ws.cell(row=i, column=1, value=label).font = LABEL_FONT
        ws.cell(row=i, column=2, value=value).alignment = WRAP
    ws.column_dimensions["A"].width = 50
    ws.column_dimensions["B"].width = 80

    # --- publications ----------------------------------------------------------------
    wp = wb.create_sheet("Публикации")
    head = ["Год", "Название", "Тип", "Журнал / источник", "ISSN", "DOI", "Позиция автора", "Всего авторов",
            "Первый", "Последний", "Corresponding", "Цитирования", "Квартиль", "Источник квартиля",
            "Год квартиля", "Предметные категории (SJR)", "Источники данных"]
    _header(wp, head)
    for p in d.publications:
        q = p.quality
        wp.append([
            p.year, p.title, p.pub_type, p.venue, ", ".join(p.issn), p.doi,
            p.author_position, p.n_authors, "да" if p.is_first else "", "да" if p.is_last else "",
            "да" if p.is_corresponding else "", p.citations,
            q.quartile if q else "", q.source if q else "", q.year if q else "",
            "; ".join(f"{c.category} ({c.quartile})" if c.quartile else c.category for c in q.categories) if q else "",
            ", ".join(p.sources),
        ])
    for col, w in zip("ABCDEFGHIJKLMNOPQ", (6, 70, 22, 34, 20, 28, 9, 9, 7, 9, 9, 10, 9, 22, 9, 50, 28)):
        wp.column_dimensions[col].width = w

    # --- metrics -----------------------------------------------------------------------
    wm = wb.create_sheet("Наукометрия")
    _header(wm, ["Показатель", "Значение", "Источник", "Примечание"])
    m = d.metrics
    for label, metric in [
        ("H-index (Scopus)", m.h_index_scopus), ("H-index (Google Scholar)", m.h_index_scholar),
        (f"H-index (Google Scholar, {m.scholar_recent_label})", m.h_index_scholar_recent),
        ("H-index (OpenAlex)", m.h_index_openalex), ("i10-index (Google Scholar)", m.i10_scholar),
        ("Цитирования (Google Scholar)", m.citations_scholar), ("Цитирования (Scopus)", m.citations_scopus),
        ("Цитирования (OpenAlex)", m.citations_openalex), ("Документы (Scopus)", m.documents_scopus),
        ("Работы (OpenAlex)", m.works_openalex),
    ]:
        wm.append([label, metric.value, metric.source, metric.note or metric.url])
    wm.append([])
    wm.append([f"Публикаций за {d.window_from}–{d.window_to}", m.pubs_in_window])
    for y, n in m.pubs_by_year.items():
        wm.append([f"   {y}", n])
    for q, n in m.quartiles.items():
        wm.append([f"Квартиль {q}", n])
    wm.append(["Первый автор", m.first_author])
    wm.append(["Последний автор", m.last_author])
    wm.append(["Corresponding author", m.corresponding_author])
    wm.append(["Q1/Q2 первым автором", m.q1q2_first_author])
    for col, w in zip("ABCD", (40, 14, 22, 60)):
        wm.column_dimensions[col].width = w

    # --- profile & checks --------------------------------------------------------------
    wr = wb.create_sheet("Профиль")
    pr = d.profile
    for label, value in [
        ("Резюме", pr.summary), ("Научные интересы", "; ".join(pr.interests)), ("Методы", "; ".join(pr.methods)),
        ("Компетенции", "; ".join(pr.competencies)), ("Прикладные направления", "; ".join(pr.applied_directions)),
        ("Ключевые слова", "; ".join(pr.keywords)), ("Тема проекта", pr.proposal_title),
        ("Описание проекта", pr.proposal_summary), ("Заметки для эксперта", "\n".join(pr.expert_notes)),
        ("Сформировано", pr.generated_by),
    ]:
        wr.append([label, value])
        wr.cell(row=wr.max_row, column=1).font = LABEL_FONT
        wr.cell(row=wr.max_row, column=2).alignment = WRAP
    wr.column_dimensions["A"].width = 26
    wr.column_dimensions["B"].width = 110

    mt = d.matching
    if mt and mt.recommendations:
        wu = wb.create_sheet("Подразделения ИТМО")
        _header(wu, ["Место", "Подразделение", "Тип", "Входит в", "Руководитель", "Релевантность", "Обоснование",
                     "Направления сотрудничества", "Совпадения", "Общие термины", "Страница"])
        for i, r in enumerate(mt.recommendations, start=1):
            wu.append([i, r.name, r.type, r.parent, r.head, r.score, r.rationale, "\n".join(r.directions),
                       "\n".join(r.evidence), ", ".join(r.shared_terms), r.url])
        wu.append([])
        wu.append(["В заявке", mt.applied_unit, "", mt.applied_unit_name, "",
                   f"место {mt.applied_unit_rank}" if mt.applied_unit_rank else "—"])
        for col, w in zip("ABCDEFGHIJK", (7, 46, 14, 30, 22, 13, 50, 50, 60, 36, 40)):
            wu.column_dimensions[col].width = w

    wc = wb.create_sheet("Проверки")
    _header(wc, ["Уровень", "Тема", "Сообщение"])
    for c in d.checks:
        wc.append([c.level, c.topic, c.message])
    wc.column_dimensions["C"].width = 120
    return _bytes(wb)


def _header(ws, cols: list[str]) -> None:
    ws.append(cols)
    for c in ws[1]:
        c.fill, c.font, c.alignment = HEADER_FILL, HEADER_FONT, WRAP
    ws.freeze_panes = "A2"


def _bytes(wb: Workbook) -> bytes:
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
