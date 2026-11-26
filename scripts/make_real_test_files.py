"""Create test applications for REAL ITMO Fellowship participants from public data only.

Source: the public participant list on fellowship.itmo.ru (name, host unit) plus public ORCID and OpenAlex
records (positions, education, publications, h-index). Nothing private is used: no birth date, phone, address
or e-mail — those form fields are left as "not provided". Every document carries a TEST banner saying it was
compiled from public records and was not written or submitted by the researcher. The research proposal is a
synthetic text built from the titles and topics of the researcher's own recent publications.

Two sets:
  participants  current Fellowship participants; the form names their real host unit (rank can be measured)
  external      researchers with no ITMO link; no host unit anywhere (blind test of the unit recommendation)
  blind         current Fellowship participants with the host unit and every ITMO position removed from the
                documents; the real unit is only in the answer key (blind, measurable test)

Output goes to samples/real-test-applications/ or samples/external-test-applications/ (git-ignored: compiled
data about real people).

    env -u ALL_PROXY uv run python scripts/make_real_test_files.py [participants|external]   # needs LibreOffice
"""
from __future__ import annotations

import shutil
import sys
from datetime import date
from pathlib import Path

import httpx
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_test_files import convert, docx, form  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples" / "real-test-applications"
OUT_EXTERNAL = ROOT / "samples" / "external-test-applications"
OUT_BLIND = ROOT / "samples" / "blind-test-applications"
YEAR_FROM = date.today().year - 4

# folder, ORCID, host unit (as on fellowship.itmo.ru), track, language, CV format, proposal format
PEOPLE = [
    ("01_EN_PDF_Kokhanovskiy", "0000-0002-1382-3589", "Faculty of Physics", "Fellowship", "en", "pdf", "pdf"),
    ("02_RU_DOCX_Surkova", "0000-0001-6220-7822",
     "Международный научно-образовательный центр физики наноструктур", "Fellowship", "ru", "docx", "docx"),
    ("03_EN_DOC_Arsentev", "0000-0001-6166-4618", "Infochemistry Scientific Center", "Fellowship", "en", "doc", "pdf"),
    ("04_EN_PDF_Justo", "0000-0003-0638-0713", "Faculty of Control Systems and Robotics", "Fellowship", "en", "pdf", "pdf"),
    ("05_EN_DOCX_Cam", "0000-0003-4053-371X", "Center for Information Optical Technologies", "Postdoc", "en", "docx", "pdf"),
    ("06_EN_PDF_El-Messery", "0000-0002-4824-5774", "Faculty of Biotechnologies", "Fellowship", "en", "pdf", "docx"),
]

# researchers with no ITMO affiliation, found through recent first-authored papers in OpenAlex; unit = None
EXTERNAL = [
    ("01_IN_PDF_Rane", "0000-0002-6640-1929", None, "Postdoc", "en", "pdf", "pdf"),
    ("02_EG_DOCX_Abdelshafy", "0000-0002-5271-7009", None, "Fellowship", "en", "docx", "pdf"),
    ("03_IR_PDF_Kazemi", "0000-0001-9314-8327", None, "Postdoc", "en", "pdf", "docx"),
    ("04_BR_DOC_Antonelo", "0000-0002-7285-0974", None, "Fellowship", "en", "doc", "pdf"),
    ("05_MX_PDF_Cordova", "0000-0002-5657-7752", None, "Fellowship", "en", "pdf", "pdf"),
    ("06_VN_DOCX_Nguyen", "0000-0001-7172-0548", None, "Fellowship", "en", "docx", "docx"),
]

# participants and their real host unit (fellowship.itmo.ru) — the unit goes only into answer_key.json
BLIND = [
    ("01_Kokhanovskiy", "0000-0002-1382-3589", "Faculty of Physics", "en", "pdf", "pdf"),
    ("02_Chichay", "0000-0002-6921-6075", "Faculty of Physics", "en", "docx", "pdf"),
    ("03_Melchakova", "0000-0003-4694-2995", "Faculty of Physics", "en", "pdf", "docx"),
    ("04_Terekhov", "0000-0002-6793-8648", "School of Physics and Engineering", "en", "doc", "pdf"),
    ("05_Surkova", "0000-0001-6220-7822",
     "International Research and Educational Center for Physics of Nanostructures", "ru", "docx", "docx"),
    ("06_Arsentev", "0000-0001-6166-4618", "Infochemistry Scientific Center", "en", "doc", "pdf"),
    ("07_El-Messery", "0000-0002-4824-5774", "Faculty of Biotechnologies", "en", "pdf", "docx"),
    ("08_Mousavi-Khaneghah", "0000-0001-5769-0004", "Faculty of Biotechnologies", "en", "pdf", "pdf"),
    ("09_Justo", "0000-0003-0638-0713", "Faculty of Control Systems and Robotics", "en", "pdf", "pdf"),
    ("10_Cam", "0000-0003-4053-371X", "Center for Information Optical Technologies", "en", "docx", "pdf"),
]

# full names as on fellowship.itmo.ru (OpenAlex sometimes keeps only initials)
NAMES = {"0000-0001-6166-4618": "Maxim Arsentev", "0000-0001-6220-7822": "Анастасия Суркова"}

BANNER_EN = ("TEST DOCUMENT — compiled automatically from public ORCID / OpenAlex records to test "
             "Fellowship Candidate Intelligence. It was not written or submitted by the researcher.")
BANNER_RU = ("ТЕСТОВЫЙ ДОКУМЕНТ — собран автоматически из открытых данных ORCID / OpenAlex для проверки "
             "Fellowship Candidate Intelligence. Не написан и не подавался самим исследователем.")


def fetch(client: httpx.Client, orcid: str, oa_key: str) -> dict:
    kp = {"api_key": oa_key} if oa_key else {}
    author = client.get(f"https://api.openalex.org/authors/orcid:{orcid}", params=kp).json()
    works = client.get("https://api.openalex.org/works", params={
        "filter": f"author.orcid:{orcid},from_publication_date:{YEAR_FROM}-01-01",
        "sort": "publication_date:desc", "per-page": 60, **kp}).json().get("results", [])
    acts = client.get(f"https://pub.orcid.org/v3.0/{orcid}/activities",
                      headers={"Accept": "application/json"}).json()

    def affs(section: str, key: str) -> list[dict]:
        rows = []
        for g in (acts.get(section) or {}).get("affiliation-group", []):
            for s in g.get("summaries", []):
                x = s.get(key, {})
                y = lambda d: ((d or {}).get("year") or {}).get("value")  # noqa: E731
                rows.append({"org": (x.get("organization") or {}).get("name", "").strip(),
                             "role": (x.get("role-title") or "").strip(), "dept": (x.get("department-name") or "").strip(),
                             "start": y(x.get("start-date")), "end": y(x.get("end-date"))})
        return rows

    pubs = []
    for w in works:
        if len(w.get("authorships", [])) > 40 or not w.get("title"):
            continue  # skip consortium papers and untitled records
        names = [a["author"]["display_name"] for a in w["authorships"]]
        pubs.append({"title": w["title"].strip(), "year": w["publication_year"], "doi": w.get("doi"),
                     "venue": ((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
                     "authors": names, "cites": w.get("cited_by_count", 0)})
    return {"name": author.get("display_name"), "h": author.get("summary_stats", {}).get("h_index"),
            "cites": author.get("cited_by_count"), "works": author.get("works_count"),
            "topics": [t["display_name"] for t in (author.get("topics") or [])[:8]],
            "employments": affs("employments", "employment-summary"),
            "educations": affs("educations", "education-summary"), "pubs": pubs}


def period(r: dict) -> str:
    return f"{r['start'] or '?'}–{r['end'] or 'present'}"


def ref(p: dict) -> str:
    authors = ", ".join(p["authors"][:6]) + (" et al." if len(p["authors"]) > 6 else "")
    doi = f" {p['doi']}" if p["doi"] else ""
    return f"{authors} ({p['year']}). {p['title']}. {p['venue'] or 'n/a'}.{doi}"


def make(folder: str, orcid: str, unit: str | None, track: str, lang: str, cv_fmt: str, pr_fmt: str, d: dict) -> None:
    out = folder
    first, _, last = d["name"].rpartition(" ")
    current = next((e for e in d["employments"] if not e["end"]), d["employments"][0] if d["employments"] else None)
    org = current["org"] if current else "not provided"
    job = (current["role"] or "Researcher") if current else "not provided"
    phd = next((e for e in d["educations"] if "phd" in (e["role"] or "").lower().replace("-", "")), None)
    selected = sorted(d["pubs"], key=lambda p: -p["cites"])[:12]
    recent = d["pubs"][:3]
    topics = d["topics"]

    if lang == "ru":
        form(out / "Заявка.docx", [
            f"Трек {track}", "",
            f"ФИО: {d['name']}", "Дата_рождения: не указано (тест)", "Phone: не указано (тест)",
            "Email: не указано (тест)", f"Место_работы: {org}, {job}",
            "Информация_о_дипломе_кандидата_наук_или_эквивалента: " + (f"{phd['org']}, {phd['end']}" if phd else "не указано"),
            f"Подразделение_или_проект_в_ИТМО: {unit or 'не определено'}", f"ORCID: https://orcid.org/{orcid}",
            "Как_вы_узнали_о_нашей_программе: тест", "Checkbox: yes",
        ])
        docx(out / "CV.docx", f"{d['name']} — резюме", [
            ("", BANNER_RU),
            ("Контакты", f"ORCID: https://orcid.org/{orcid}"),
            ("Научные интересы", "; ".join(topics)),
            ("Наукометрия", f"Индекс Хирша (OpenAlex) — {d['h']}; цитирований — {d['cites']}; публикаций — {d['works']}."),
            ("Опыт работы", [f"{period(e)} {e['role'] or 'Сотрудник'}, {e['org']}" + (f", {e['dept']}" if e['dept'] else "")
                             for e in d["employments"]] or ["нет данных в ORCID"]),
            ("Образование", [f"{period(e)} {e['role'] or ''} {e['org']}".strip() for e in d["educations"]] or ["нет данных в ORCID"]),
            (f"Избранные публикации {YEAR_FROM}–{date.today().year}", [ref(p) for p in selected]),
        ])
        docx(out / "Proposal.docx", "Проект исследования (синтетический текст)", [
            ("", BANNER_RU + " Текст проекта составлен по названиям собственных публикаций исследователя."),
            ("Название проекта", f"{topics[0]}: развитие исследований в ИТМО"),
            ("Аннотация", f"Проект продолжает работу автора по направлениям: {', '.join(topics[:4])}. "
                          f"Основу составляют недавние результаты: " + "; ".join(f"«{p['title']}» ({p['year']})" for p in recent) + "."),
            ("Задачи", [f"Развить методы в области «{t}»." for t in topics[:3]]),
            ("Сотрудничество с ИТМО", "Работа планируется в профильном подразделении ИТМО: совместные эксперименты, "
                                      "две статьи в журналах Q1–Q2, участие студентов магистратуры."),
        ])
    else:
        form(out / "Заявка.docx", [
            f"Трек {track}", "",
            f"Host_department: {unit or 'not decided yet'}", "Your_contact_at_ITMO: not provided (test)",
            f"Name: {first}", f"Name_2: {last}",
            "Date_of_birth: not provided (test)", "Citizenship: not provided (test)", "Phone: not provided (test)",
            "Email: not provided (test)", f"Organization: {org}", f"Job_title: {job}",
            "Institution: " + (phd["org"] if phd else "not provided"), "Year_of_issue: " + ((phd or {}).get("end") or "not provided"),
            f"ORCID: https://orcid.org/{orcid}",
            "How_did_you_learn_about_the_competition: test", "Checkbox: yes",
        ])
        docx(out / "CV.docx", f"{d['name']} — Curriculum Vitae", [
            ("", BANNER_EN),
            ("Contact", f"ORCID: https://orcid.org/{orcid}"),
            ("Research interests", "; ".join(topics)),
            ("Metrics", f"h-index (OpenAlex): {d['h']}; citations: {d['cites']}; publications: {d['works']}."),
            ("Employment", [f"{period(e)} {e['role'] or 'Researcher'}, {e['org']}" + (f", {e['dept']}" if e['dept'] else "")
                            for e in d["employments"]] or ["no public ORCID record"]),
            ("Education", [f"{period(e)} {e['role'] or ''}, {e['org']}".strip(", ") for e in d["educations"]] or ["no public ORCID record"]),
            (f"Selected publications {YEAR_FROM}–{date.today().year}", [ref(p) for p in selected]),
        ])
        docx(out / "Proposal.docx", "Research proposal (synthetic text)", [
            ("", BANNER_EN + " The proposal text is synthesized from the titles of the researcher's own publications."),
            ("Project title", f"{topics[0]}: extending the research programme at ITMO"),
            ("Abstract", f"The project continues the author's work on {', '.join(topics[:4])}. It builds on recent "
                         "results: " + "; ".join(f"\"{p['title']}\" ({p['year']})" for p in recent) + "."),
            ("Objectives", [f"Advance methods in {t.lower()}." for t in topics[:3]]),
            ("Collaboration with ITMO", "The work is planned with a suitable ITMO research unit: joint "
                                        "experiments, two papers in Q1–Q2 journals and supervision of master's students."),
        ])
    if cv_fmt != "docx":
        convert(out / "CV.docx", cv_fmt)
    if pr_fmt != "docx":
        convert(out / "Proposal.docx", pr_fmt)


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "participants"
    if which == "blind":
        return main_blind()
    people, out_dir = (EXTERNAL, OUT_EXTERNAL) if which == "external" else (PEOPLE, OUT)
    oa_key = dotenv_values(ROOT / ".env").get("OPENALEX_API_KEY") or ""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    with httpx.Client(timeout=40) as client:
        for folder, orcid, unit, track, lang, cv_fmt, pr_fmt in people:
            data = fetch(client, orcid, oa_key)
            data["name"] = NAMES.get(orcid, data["name"])
            make(out_dir / folder, orcid, unit, track, lang, cv_fmt, pr_fmt, data)
            print(f"{folder:28} {data['name']:28} h={data['h']:<4} pubs since {YEAR_FROM}: {len(data['pubs'])}")
    for p in sorted(out_dir.rglob("*")):
        if p.is_file():
            print(f"{p.stat().st_size / 1024:9.1f} KB  {p.relative_to(out_dir)}")



def main_blind() -> None:
    import json
    oa_key = dotenv_values(ROOT / ".env").get("OPENALEX_API_KEY") or ""
    if OUT_BLIND.exists():
        shutil.rmtree(OUT_BLIND)
    key = {}
    with httpx.Client(timeout=40) as client:
        for folder, orcid, unit, lang, cv_fmt, pr_fmt in BLIND:
            data = fetch(client, orcid, oa_key)
            data["name"] = NAMES.get(orcid, data["name"])
            # an applicant's CV is written before joining ITMO: drop every ITMO position and degree
            for k in ("employments", "educations"):
                data[k] = [e for e in data[k] if "itmo" not in e["org"].lower()]
            make(OUT_BLIND / folder, orcid, None, "Fellowship", lang, cv_fmt, pr_fmt, data)
            key[folder] = {"name": data["name"], "orcid": orcid, "host_unit": unit}
            print(f"{folder:24} {data['name']:30} -> {unit}")
    (OUT_BLIND / "answer_key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
