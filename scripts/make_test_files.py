"""Create test applications for FICTIONAL candidates (samples/test-applications/).

No real person is described: invented names, example.org e-mails, fake phone numbers. Each set exercises
different parts of the tool (English / Russian forms, PDF / DOCX / legacy DOC, metric claims, a broken Scopus
link, ITMO unit matching) plus one oversized file for the upload limit.

    uv run python scripts/make_test_files.py        # needs LibreOffice (soffice) for PDF / DOC conversion
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from docx import Document
from docx.shared import Pt

OUT = Path(__file__).resolve().parents[1] / "samples" / "test-applications"


def docx(path: Path, title: str, blocks: list[tuple[str, str | list[str]]]) -> None:
    d = Document()
    d.styles["Normal"].font.name = "Arial"
    d.styles["Normal"].font.size = Pt(10.5)
    d.add_heading(title, level=1)
    for heading, body in blocks:
        if heading:
            d.add_heading(heading, level=2)
        for para in ([body] if isinstance(body, str) else body):
            d.add_paragraph(para)
    path.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(path))


def form(path: Path, lines: list[str]) -> None:
    """Tilda export: one `Key: value` per paragraph, track on the first line."""
    d = Document()
    for line in lines:
        d.add_paragraph(line)
    path.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(path))


def convert(src: Path, fmt: str) -> Path:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise SystemExit("LibreOffice (soffice) is needed to create PDF / DOC files")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([soffice, "--headless", "--convert-to", fmt, "--outdir", tmp, str(src)],
                       check=True, capture_output=True, timeout=180)
        produced = next(Path(tmp).iterdir())
        target = src.with_suffix("." + fmt.split(":")[0])
        shutil.move(str(produced), target)
    src.unlink()
    return target


# --------------------------------------------------------------------------- 01: English, photonics
def set_01() -> None:
    d = OUT / "01_EN_photonics_postdoc"
    form(d / "Заявка.docx", [
        "Трек Postdoc", "",
        "Host_department: International Research and Educational Center for Physics of Nanostructures",
        "Your_contact_at_ITMO: Dr. Jane Example",
        "Name: Zorvana", "Name_2: Kestrelmoor-Test",
        "Date_of_birth: 14-05-1992", "Citizenship: Fictionland", "Phone: +00-000-0000000",
        "Email: zorvana.test@example.org", "Сountry: Fictionland", "Сity: Testville", "Postal_code: 00000",
        "Organization: Example Institute of Photonics", "Job_title: Postdoctoral Researcher",
        "Country: Fictionland", "Institution: Example University of Science", "Year_of_issue: 2021",
        # deliberately broken Scopus link (no authorId) — the tool should flag it
        "Link_to_your_Scopus_profile: https://id.elsevier.com/settings/redirect?code=TEST",
        "Full_name: Prof. Alan Sample", "Affiliation: Example University of Science", "Email_2: alan.sample@example.org",
        "Full_name_2: Dr. Maria Placeholder", "Affiliation_2: Institute of Testing", "Email_3: maria.p@example.org",
        "How_did_you_learn_about_the_competition: From colleagues/friends", "Checkbox: yes",
    ])
    docx(d / "CV.docx", "Zorvana Kestrelmoor-Test, PhD — Curriculum Vitae", [
        ("", "FICTIONAL TEST CANDIDATE — generated for testing Fellowship Candidate Intelligence. "
             "Email: zorvana.test@example.org"),
        ("Summary", "Experimental physicist working on dielectric metasurfaces, nonlinear nanophotonics and "
                    "light-matter interaction in halide perovskite nanostructures. H-index 7 (Scopus), "
                    "total citations 310 (Google Scholar)."),
        ("Education", ["2017–2021 PhD in Optics and Photonics, Example University of Science.",
                       "2015–2017 MSc in Physics, Example University of Science."]),
        ("Experience", ["2021–present Postdoctoral Researcher, Example Institute of Photonics: design and "
                        "fabrication of Mie-resonant metasurfaces for harmonic generation; electron-beam "
                        "lithography, dark-field spectroscopy, FDTD simulations.",
                        "2017–2021 PhD researcher: second-harmonic generation in silicon and perovskite nanoparticles."]),
        ("Skills", "FDTD and FEM modelling (Lumerical, COMSOL), nanofabrication, femtosecond laser spectroscopy, "
                   "Python for data analysis."),
        ("Selected topics", ["Nonlinear dielectric metasurfaces", "Perovskite nanophotonics",
                             "Bound states in the continuum", "Optical anapoles"]),
    ])
    docx(d / "Proposal.docx", "Research proposal", [
        ("Project title", "Perovskite Metasurfaces with Bound States in the Continuum for Efficient Nonlinear "
                          "Light Generation"),
        ("Abstract", "The project aims to design and fabricate halide perovskite metasurfaces that support "
                     "quasi-bound states in the continuum to boost second- and third-harmonic generation. "
                     "We will combine numerical optimisation, nanofabrication and nonlinear spectroscopy, and "
                     "explore applications in compact light sources and optical sensing."),
        ("Collaboration with ITMO", "The work fits the nanophotonics and metamaterials research of ITMO's School "
                                    "of Physics and Engineering; we plan joint experiments and two co-authored "
                                    "papers in Q1 journals within two years."),
    ])
    convert(d / "CV.docx", "pdf")
    convert(d / "Proposal.docx", "pdf")


# --------------------------------------------------------------------------- 02: Russian, AI in medicine
def set_02() -> None:
    d = OUT / "02_RU_ai_medicine_fellowship"
    form(d / "Заявка.docx", [
        "Трек Fellowship", "",
        "ФИО: Квазитестов Ипполит Сергеевич", "Дата_рождения: 02-11-1987", "Phone: +7 000 000 0000",
        "Email: ippolit.test@example.org", "Место_работы: Тестовый государственный университет, доцент",
        "Информация_о_дипломе_кандидата_наук_или_эквивалента: Россия, Тестовый университет, 2014 год",
        "Тема_кандидатской_диссертации: Методы машинного обучения для анализа медицинских изображений",
        "Подразделение_или_проект_в_ИТМО: Институт искусственного интеллекта",
        "ФИО_2: Примерова Анна Петровна", "Аффилиация: Тестовый медицинский центр", "Email_2: anna.p@example.org",
        "Как_вы_узнали_о_нашей_программе: от коллег", "Checkbox: yes",
    ])
    docx(d / "CV.docx", "Квазитестов Ипполит Сергеевич — резюме", [
        ("", "ВЫМЫШЛЕННЫЙ ТЕСТОВЫЙ КАНДИДАТ — создан для проверки Fellowship Candidate Intelligence."),
        ("О себе", "Кандидат технических наук, доцент. Занимаюсь глубоким обучением для анализа медицинских "
                   "изображений: сегментация опухолей на КТ и МРТ, объяснимый ИИ для врачей. "
                   "Индекс Хирша по Scopus — 9."),
        ("Опыт работы", ["2018–н.в. Доцент кафедры прикладной информатики, Тестовый государственный университет.",
                         "2014–2018 Научный сотрудник лаборатории машинного обучения."]),
        ("Навыки", "PyTorch, сверточные нейронные сети, трансформеры для изображений, федеративное обучение, "
                   "анализ DICOM-данных, статистика."),
    ])
    docx(d / "Proposal.docx", "Проект исследования", [
        ("Название проекта", "Федеративное обучение для объяснимой диагностики рака лёгкого по данным КТ"),
        ("Аннотация", "Цель проекта — разработать методы федеративного обучения, позволяющие нескольким клиникам "
                      "обучать общую модель сегментации и классификации узлов в лёгких без передачи данных "
                      "пациентов, а также методы объяснимого ИИ для поддержки решений врача."),
        ("Ожидаемые результаты", "Открытая библиотека, две статьи в журналах Q1, прототип системы поддержки "
                                 "принятия врачебных решений совместно с ИТМО."),
    ])


# --------------------------------------------------------------------------- 03: legacy .doc, chemistry
def set_03() -> None:
    d = OUT / "03_DOC_chemistry_micro_fellowship"
    form(d / "Заявка.docx", [
        "Трек Micro Fellowship", "",
        "Host_department: SCAMT",
        "Your_contact_at_ITMO: Dr. Example Contact",
        "Name: Quilla", "Name_2: Brightwater-Test",
        "Date_of_birth: 30-08-1985", "Citizenship: Fictionland", "Email: quilla.test@example.org",
        "Organization: Example Polytechnic", "Job_title: Associate Professor",
        "Institution: Example Polytechnic", "Year_of_issue: 2013",
        "Full_name: Prof. Omar Fictive", "Affiliation: Example Polytechnic", "Email_2: omar.f@example.org",
        "Checkbox: yes",
    ])
    docx(d / "CV.docx", "Quilla Brightwater-Test — CV", [
        ("", "FICTIONAL TEST CANDIDATE. Email: quilla.test@example.org"),
        ("Research interests", "Injectable hydrogels, carbon quantum dots, antibacterial photodynamic therapy, "
                               "wound healing, sol-gel chemistry."),
        ("Metrics", "Google Scholar h-index: 11; citations: 540."),
        ("Positions", ["2016–present Associate Professor, Example Polytechnic, Department of Chemical Engineering."]),
    ])
    docx(d / "Proposal.docx", "Micro Fellowship proposal", [
        ("Project title", "Photoactive Hydrogel Dressings with Carbon Quantum Dots for Infected Wounds"),
        ("Summary", "Short-term visit to develop gelatin-based injectable hydrogels loaded with carbon quantum "
                    "dots that generate reactive oxygen species under visible light, and to test their "
                    "antibacterial activity and biocompatibility together with ITMO chemists."),
    ])
    convert(d / "CV.docx", "doc")  # legacy Word 97 format
    convert(d / "Proposal.docx", "pdf")


# --------------------------------------------------------------------------- 04: oversized file
def set_04() -> None:
    d = OUT / "04_TOO_BIG_upload_limit"
    d.mkdir(parents=True, exist_ok=True)
    big = d / "CV_12MB.txt"
    line = "FICTIONAL padding text to exceed the 10 MB upload limit. " * 8 + "\n"
    with big.open("w") as f:
        while f.tell() < 12 * 1024 * 1024:
            f.write(line)


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    for fn in (set_01, set_02, set_03, set_04):
        fn()
    for p in sorted(OUT.rglob("*")):
        if p.is_file():
            print(f"{p.stat().st_size / 1024:9.1f} KB  {p.relative_to(OUT)}")


if __name__ == "__main__":
    main()
