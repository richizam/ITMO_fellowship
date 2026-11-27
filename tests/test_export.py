"""Card rows, Excel workbook and H-index checks (offline)."""
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import load_workbook

from app.analysis.metrics import h_index_checks
from app.export import dossier_workbook, summary_rows, summary_workbook
from app.ingest.identifiers import Claim
from app.models import Dossier, Matching, Metric, Publication, UnitMatch


def make_dossier(**metrics) -> Dossier:
    d = Dossier(id="t", window_from=2022, window_to=2026)
    d.basic.full_name = "Test Person"
    d.basic.track = "Fellowship"
    d.publications = [Publication(title="a", year=2023), Publication(title="b", year=2025)]
    d.metrics.pubs_in_window = 2
    for k, v in metrics.items():
        setattr(d.metrics, k, Metric(value=v, source=k))
    return d


def card(d: Dossier) -> dict:
    return {label: value for label, value in summary_rows(d)}


# ------------------------------------------------------------------------------- card
TEAM = Path(__file__).resolve().parents[1] / "data" / "examples" / "Кандидаты.xlsx"


def test_card_has_the_rows_of_the_team_table():
    labels = [label for label, _ in summary_rows(make_dossier())]
    assert len(labels) == 14 and labels[0] == "First and Last Name" and labels[-1] == "Letter of Support signed by"
    assert not any("OpenAlex" in label for label in labels)


def test_scholar_cell_falls_back_to_openalex():
    assert card(make_dossier(h_index_openalex=99))["H-index (Scholar)"] == "профиль не указан (OpenAlex: 99)"
    assert card(make_dossier(h_index_scholar=20, h_index_openalex=14))["H-index (Scholar)"] == 20


def test_card_explains_missing_h_index():
    c = card(make_dossier())
    assert c["H-index (Scopus)"] == "нет данных"
    assert c["H-index (Scholar)"] == "профиль не указан"
    d = make_dossier()
    d.identifiers.scholar_id = "abc123"
    assert card(d)["H-index (Scholar)"] == "нет данных"


@pytest.mark.parametrize("job, org, expected", [
    ("not provided", "not provided", "not provided"),
    ("Researcher", "Example University", "Researcher, Example University"),
    ("", "Example University", "Example University"),
    ("Professor", "", "Professor"),
])
def test_card_work_place_has_no_repeats(job, org, expected):
    d = make_dossier()
    d.basic.job_title, d.basic.organization = job, org
    assert card(d)["Current work place"] == expected


def test_card_publications_say_where_the_number_comes_from():
    pubs = next(v for k, v in summary_rows(make_dossier()) if k.startswith("Number of publications"))
    assert pubs.startswith("2") and "по всем источникам" in pubs
    d = make_dossier()
    d.metrics.scholar_pubs_in_window = 2
    pubs = next(v for k, v in summary_rows(d) if k.startswith("Number of publications"))
    assert pubs == "2"


# ------------------------------------------------------------------------------- Excel
def layout(ws) -> dict:
    """What makes the team's sheet look the way it does."""
    return {
        "rows_1_2_empty": all(ws.cell(row=r, column=c).value is None for r in (1, 2) for c in (1, 2)),
        "number_cell": (ws["B3"].value, ws["B3"].font.b, ws["B3"].alignment.horizontal, ws["B3"].border.left.style),
        "labels": [ws.cell(row=r, column=1).value.split(" (Scholar)")[0].replace("2021", "Y").replace("2022", "Y")
                   for r in range(4, 18)],
        "label_style": (ws["A4"].font.b, ws["A4"].alignment.horizontal, ws["A4"].alignment.wrap_text, ws["A4"].border.left.style),
        "value_border": ws["B4"].border.left.style,
        "widths": (ws.column_dimensions["A"].width, ws.column_dimensions["B"].width),
    }


@pytest.mark.skipif(not TEAM.exists(), reason="team spreadsheet is personal data, not in git")
def test_summary_matches_the_team_sheet_layout():
    team = layout(load_workbook(TEAM).active)
    ours = layout(load_workbook(BytesIO(summary_workbook([make_dossier()]))).active)
    team["number_cell"] = (1, *team["number_cell"][1:])  # the template stores 1 as 1.0
    assert ours == team


def test_summary_workbook_one_column_per_candidate():
    wb = load_workbook(BytesIO(summary_workbook([make_dossier(h_index_scholar=5), make_dossier()])))
    ws = wb["Кандидаты"]
    assert [ws.cell(row=3, column=c).value for c in (2, 3)] == [1, 2]
    labels = [ws.cell(row=r, column=1).value for r in range(4, 18)]
    row = 4 + labels.index("H-index (Scholar)")
    assert ws.cell(row=row, column=2).value == 5
    assert ws.cell(row=row, column=3).value == "профиль не указан"


def test_dossier_workbook_sheets_and_note():
    d = make_dossier(h_index_openalex=12)
    d.matching = Matching(recommendations=[UnitMatch(unit_id="u1", name="Faculty of Physics")])
    wb = load_workbook(BytesIO(dossier_workbook(d)))
    assert wb.sheetnames == ["Кандидаты", "Публикации", "Наукометрия", "Профиль", "Подразделения ИТМО", "Проверки"]
    ws = wb["Кандидаты"]
    assert ws["B3"].value == 1 and ws["A4"].value == "First and Last Name"
    note = ws["A19"].value
    assert note.startswith("Остальные данные") and "Публикации" in note and "Проверки" in note


# ------------------------------------------------------------------------------- H-index checks
def metrics(**v):
    d = make_dossier(**v)
    return d.metrics


def test_unnamed_h_index_is_compared_with_openalex():
    # the case from the live test: CV says "h-index 100" without a database, OpenAlex has 99
    out = h_index_checks([Claim("h_index", 100, "источник не указан", "")], metrics(h_index_openalex=99))
    assert len(out) == 1 and out[0].level == "ok"
    assert "OpenAlex" in out[0].message and "источник не указан" not in out[0].message


def test_unnamed_h_index_prefers_scopus_then_scholar():
    out = h_index_checks([Claim("h_index", 20, "источник не указан", "")],
                         metrics(h_index_scholar=21, h_index_openalex=15))
    assert out[0].level == "ok" and "Google Scholar" in out[0].message


def test_named_database_is_not_compared_with_another():
    out = h_index_checks([Claim("h_index", 30, "Scopus", "")], metrics(h_index_openalex=12))
    assert out[0].level == "info"
    assert "сверить его нельзя" in out[0].message
    assert "вручную" in out[0].message and "OpenAlex показывает 12" in out[0].message


@pytest.mark.parametrize("claimed, actual, level, phrase", [
    (15, 10, "warn", "завышено"),
    (8, 12, "info", "давно"),
    (11, 10, "ok", "совпадает"),
])
def test_h_index_levels(claimed, actual, level, phrase):
    out = h_index_checks([Claim("h_index", claimed, "Google Scholar", "")], metrics(h_index_scholar=actual))
    assert out[0].level == level and phrase in out[0].message


def test_no_database_at_all():
    out = h_index_checks([Claim("h_index", 7, "источник не указан", "")], metrics())
    assert out[0].level == "info" and "Ни одна база" in out[0].message


def test_check_messages_have_no_dashes():
    cases = [h_index_checks([Claim("h_index", v, s, "")], metrics(h_index_scholar=10, h_index_openalex=9))
             for v in (5, 10, 20) for s in ("Google Scholar", "Scopus", "источник не указан")]
    for out in cases:
        for c in out:
            assert " — " not in c.message, c.message
