"""Offline unit tests: application-form parsing, identifier/claim extraction, name matching,
publication merging, quartile lookup and metrics."""
from __future__ import annotations

import pytest

from app.analysis import publications as pubs_mod
from app.analysis.metrics import checks, compute
from app.analysis.names import author_matches, latin_display_name, name_keys
from app.analysis.profile import _proposal_title
from app.ingest.application import parse_application
from app.ingest.identifiers import scan_text, scholar_id_from, scopus_id_from
from app.models import Dossier, JournalQuality, Metric, Publication
from app.sources import sjr
from app.sources.scholar import ScholarPub, venue_name

EN_FORM = """Трек Fellowship

Host_department: Test Laboratory
Your_contact_at_ITMO: Dr. Jane Roe
Name: Ivan
Name_2: Testov
Date_of_birth: 15-03-1990
Citizenship: Russia
Сountry: Russia
Organization: Example University
Job_title: Postdoctoral researcher
Country: USA
Institution: Example University
Year_of_issue: 2024
Link_to_your_Google_Scholar_profile: https://scholar.google.com/citations?user=AbCdEfGh1234&hl=en
Link_to_your_Scopus_profile: https://www.scopus.com/authid/detail.uri?authorId=12345678901
Full_name: Alice Example
Affiliation: Example Institute
Email_2: alice@example.org
Full_name_2: Bob Sample
Affiliation_2: Sample University
Email_3: bob@example.org
"""

RU_SHORT_FORM = """Трек Micro Fellowship

Name: Иванов Пётр Сергеевич
Date: 01-02-1980
Work: Примерный университет, заведующий лабораторией
Информация_о_дипломе_кандидата_наук_или_эквивалента: UK, Example University, 2021
Scholar: https://scholar.google.com/citations?user=XyZ123456789&hl=ru
Scopus: https://www.scopus.com/authid/detail.uri?authorId=10987654321
Department: Тестовая школа ИТМО
Contact: Сидоров Иван Иванович
ФИО: John A Example
Аффилиация: Example University
Email_2: j@example.org
"""


def test_english_form():
    a = parse_application(EN_FORM)
    b = a.basic
    assert b.full_name == "Ivan Testov"
    assert b.track == "Fellowship"
    assert b.date_of_birth == "1990-03-15" and b.age and b.age >= 36
    assert b.country == "Russia" and b.citizenship == "Russia"
    assert b.phd_institution == "Example University, USA" and b.phd_year == "2024"
    assert b.host_unit == "Test Laboratory" and b.itmo_contact.startswith("Dr.")
    assert [r.name for r in b.referees] == ["Alice Example", "Bob Sample"]
    assert scholar_id_from(a.scholar_url) == "AbCdEfGh1234"
    assert scopus_id_from(a.scopus_url) == "12345678901"


def test_short_russian_form_splits_workplace_and_referee():
    b = parse_application(RU_SHORT_FORM).basic
    assert b.full_name == "Иванов Пётр Сергеевич"
    assert b.organization == "Примерный университет" and b.job_title == "заведующий лабораторией"
    assert b.phd_year == "2021"
    assert b.host_unit == "Тестовая школа ИТМО"
    assert [r.name for r in b.referees] == ["John A Example"]


def test_invalid_scopus_link_gives_no_id():
    assert scopus_id_from("https://id.elsevier.com/settings/redirect?code=abc") == ""
    assert scopus_id_from("10987654321") == "10987654321"


@pytest.mark.parametrize("text,value,source", [
    ("Selected Publications (H-index - 13, total citations - 4390)", 13, "источник не указан"),
    ("H index: 06 (Google Scholar) Total number of citations: 93", 6, "Google Scholar"),
    ("H-Index (Scopus & Web of Science): 15", 15, "Scopus"),
    ("Dr. Example's H-index is 15 according to Scopus", 15, "Scopus"),
    ("chemometrics (Google scholar h-index = 5)", 5, "Google Scholar"),
])
def test_h_index_claims(text, value, source):
    claims = [c for c in scan_text(text).claims if c.metric == "h_index"]
    assert claims and claims[0].value == value and claims[0].source == source


def test_stat_header_is_not_a_claim():
    # "H-Index i10-Index" header must not be read as h-index = 10
    assert not [c for c in scan_text("Publications Citations H-Index i10-Index Funded Projects").claims
                if c.metric == "h_index"]


def test_identifiers_in_cv():
    f = scan_text("ORCID: 0000-0002-1825-0097 ResearcherID: A-1234-2011 Scopus Author ID: 12345678901 "
                  "doi 10.1093/bioinformatics/btab597.")
    assert f.orcid == "0000-0002-1825-0097" and f.researcherid == "A-1234-2011"
    assert f.scopus_id == "12345678901" and f.dois == ["10.1093/bioinformatics/btab597"]


def test_name_matching():
    keys = name_keys("Ivan Testov")
    assert author_matches("I Testov", keys) and author_matches("Testov, I.", keys)
    assert not author_matches("IC Tester", keys)
    ru = name_keys("Смирнов Николай Петрович")
    assert author_matches("N Smirnov", ru)
    assert author_matches("A Abd El-Example", name_keys("Amina Abd El-Example"))
    assert latin_display_name("Кузнецов Тимур Ильич") == "Timur Kuznetsov"


def test_merge_by_doi_and_title():
    oa = Publication(title="Cue: a deep-learning framework for structural variant discovery", year=2023,
                     doi="10.1038/x", sources=["OpenAlex"], citations=80, citations_by_source={"OpenAlex": 80},
                     pub_type="Статья в журнале")
    orc = Publication(title="Cue: A deep-learning framework", year=2023, doi="10.1038/x", sources=["ORCID"])
    sch = pubs_mod.from_scholar(ScholarPub(
        title="Cue: a deep-learning framework for structural variant discovery", authors="A Other, I Testov, ...",
        venue="Nature Methods 20, 1-10", year=2023, citations=84, url=""), name_keys("Ivan Testov"))
    merged = pubs_mod.merge([oa], [orc], [sch])
    assert len(merged) == 1
    assert merged[0].sources == ["OpenAlex", "ORCID", "Google Scholar"]
    assert merged[0].citations == 84


def test_preprint_and_ssrn_suffix_are_merged():
    a = Publication(title="Diversity of the Global RNA Virome in Urban Environments", year=2024, pub_type="Препринт",
                    sources=["OpenAlex"])
    b = Publication(title="Diversity of the Global RNA Virome in Urban Environments (SSRN Scholarly Paper No. 1)",
                    year=2024, pub_type="Препринт", sources=["Google Scholar"])
    assert len(pubs_mod.merge([a], [], [b])) == 1


def test_scholar_author_position_and_venue():
    p = pubs_mod.from_scholar(ScholarPub(title="T", authors="I Testov, R Other, J Third", venue="Bioinformatics 38 (1), 1-8",
                                         year=2022, citations=5, url=""), name_keys("Ivan Testov"))
    assert p.author_position == 1 and p.is_first and p.n_authors == 3
    assert venue_name("Nature communications 17 (1), 6397") == "Nature communications"


@pytest.mark.skipif(not sjr.available(), reason="data/sjr.sqlite not built")
def test_sjr_lookup():
    q = sjr.lookup(["1367-4803"], "", 2023)
    assert q and q.quartile == "Q1" and q.year == 2023 and q.matched_by == "ISSN"
    assert any(c.quartile for c in q.categories)
    future = sjr.lookup(["1367-4803"], "", 2099)
    assert future and future.year == sjr.max_year()
    by_title = sjr.lookup([], "Nucleic Acids Research", 2022)
    assert by_title and by_title.matched_by == "название журнала"


def test_metrics_and_checks():
    d = Dossier(id="t", window_from=2022, window_to=2026)
    q1 = JournalQuality(quartile="Q1", year=2023)
    d.publications = [
        Publication(title="a", year=2022, quality=q1, is_first=True, author_position=1),
        Publication(title="b", year=2023, quality=JournalQuality(quartile="Q3", year=2023), author_position=2),
        Publication(title="c", year=2025, quality=q1, author_position=3, is_last=True),
    ]
    d.metrics.h_index_scholar = Metric(value=10, source="Google Scholar")
    compute(d)
    m = d.metrics
    assert m.pubs_in_window == 3 and m.gap_years == [2024]
    assert m.quartiles["Q1"] == 2 and m.q1q2_first_author == 1 and m.last_author == 1
    from app.ingest.identifiers import Claim
    out = checks(d, [Claim("h_index", 15, "Google Scholar", "")])
    assert any(c.level == "warn" and "завышено" in c.message for c in out)
    assert any("2024" in c.message for c in out)


def test_proposal_title_heuristic():
    assert _proposal_title("1. Project Title\nBioelectrochemical Systems for Hydrogen Production.\n2. Introduction\nText") \
        == "Bioelectrochemical Systems for Hydrogen Production."
    assert _proposal_title("Ivan Testov:\nPersonal statement\nMy scientific journey began", "Ivan Testov") == ""
    assert _proposal_title("ITMO Micro Fellowship Application\nProposer: Dr K\nAgentic AI: Case Studies Across Domains\n1. Background") \
        == "Agentic AI: Case Studies Across Domains"


# ---------------------------------------------------------------- stage 2 (pure functions, no model)
def test_unit_chunks_and_candidate_items():
    from app.analysis.matching import _expand_abbreviations, candidate_items, unit_chunks
    from app.models import ResearchProfile

    u = {"name_en": "Infochemistry Scientific Center", "parent": "ChemBio Cluster",
         "description_en": " ".join(["The center studies chemical information processing systems."] * 30)}
    chunks = unit_chunks(u, words_per_chunk=20, max_chunks=5)
    assert chunks[0].startswith("Infochemistry Scientific Center. ChemBio Cluster") and len(chunks) == 5

    d = Dossier(id="t", window_from=2022, window_to=2026)
    d.profile = ResearchProfile(proposal_title="Hydrogels for wound healing", interests=["hydrogels", "Hydrogels"],
                                methods=["spectroscopy"])
    d.publications = [Publication(title="Quantum dot hydrogel composites", year=2024, is_first=True)]
    items = candidate_items(d)
    assert items[0] == ("Hydrogels for wound healing", 3.0, "тема проекта")
    assert [t for t, _, _ in items].count("hydrogels") == 1  # case-insensitive de-duplication
    assert any(k == "публикация" and w == 1.2 for _, w, k in items)
    assert "Advanced Engineering School" in _expand_abbreviations("ПИШ ИТМО")
