"""Parser for the ITMO Fellowship application form export ("Заявка.docx").

The forms come from Tilda and are a list of `Key: value` lines. Several form
versions exist (English, Russian, short Russian), so keys are matched by aliases.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from app.models import BasicInfo, Referee

TRACKS = {
    "postdoc": "Postdoc",
    "fellowship": "Fellowship",
    "micro fellowship": "Micro Fellowship",
    "professorship": "Professorship",
    "research professorship": "Research Professorship",
    "visiting lecturer": "Visiting Lecturer",
    "pi": "PI + team",
}

LINE_RE = re.compile(r"^\s*([A-Za-zА-Яа-яЁё_0-9]{2,80}?)\s*:\s*(.*)$")


@dataclass
class ApplicationData:
    basic: BasicInfo
    scholar_url: str = ""
    scopus_url: str = ""
    orcid: str = ""
    raw: dict[str, str] = field(default_factory=dict)
    documents: dict[str, str] = field(default_factory=dict)  # attachment links named in the form


def parse_application(text: str) -> ApplicationData:
    raw: dict[str, str] = {}
    order: list[str] = []
    track = ""
    for line in text.splitlines():
        if not line.strip():
            continue
        m_track = re.match(r"^\s*Трек\s+(.+?)\s*$", line, re.I)
        if m_track and not track:
            track = m_track.group(1)
            continue
        m = LINE_RE.match(line)
        if not m:
            continue
        key = m.group(1)
        # "Сountry" written with a Cyrillic С is the residence country; Latin "Country" is the PhD country.
        if key.startswith("Сountry"):
            key = "ResidenceCountry"
        else:
            key = re.sub(r"_0$", "", key)
        if key not in raw:
            raw[key] = m.group(2).strip()
            order.append(key)

    def get(*keys: str) -> str:
        for k in keys:
            v = raw.get(k, "").strip()
            if v:
                return v
        return ""

    b = BasicInfo()
    b.track = _normalize_track(track)

    # --- candidate name -------------------------------------------------------
    name_key_used = ""
    if raw.get("Name") and raw.get("Name_2"):
        b.full_name = f"{raw['Name']} {raw['Name_2']}".strip()
        name_key_used = "Name"
    elif raw.get("Name"):
        b.full_name = raw["Name"]
        name_key_used = "Name"
    elif raw.get("ФИО"):
        b.full_name = raw["ФИО"]
        name_key_used = "ФИО"

    dob = get("Date_of_birth", "Дата_рождения", "Date")
    b.date_of_birth, b.age = _parse_date(dob)

    b.citizenship = get("Citizenship", "Гражданство")
    b.country = get("ResidenceCountry", "Страна")
    b.city = get("City", "Сity", "Город")
    b.email = get("Email", "E-mail")
    b.phone = get("Phone", "Телефон")

    org = get("Organization", "Место_работы", "Work")
    job = get("Job_title", "Должность")
    if org and not job and raw.get("Work") and "," in org:
        # short Russian form: "<organization>, <position>"
        head, _, tail = org.rpartition(",")
        if len(tail.strip()) < 60:
            org, job = head.strip(), tail.strip()
    b.organization, b.job_title = org, job

    phd_info = get("Информация_о_дипломе_кандидата_наук_или_эквивалента")
    if phd_info:
        b.phd_institution = phd_info
        years = re.findall(r"(19\d{2}|20\d{2})", phd_info)
        b.phd_year = years[-1] if years else ""
    else:
        inst = get("Institution")
        country = get("Country")
        if country and (country.lower() in inst.lower() or re.search(r"[؀-ۿ]", country)):
            country = ""  # already part of the institution, or written in Arabic script
        b.phd_institution = ", ".join(x for x in (inst, country) if x)
        b.phd_year = get("Year_of_issue")
    b.phd_topic = get("Тема_кандидатской_диссертации", "Thesis_topic")

    b.host_unit = get("Host_department", "Подразделение_или_проект_в_ИТМО", "Department")
    b.itmo_contact = get("Your_contact_at_ITMO", "Contact", "Контакт_в_ИТМО")
    b.how_learned = get("How_did_you_learn_about_the_competition", "Как_вы_узнали_о_нашей_программе")

    # --- referees ---------------------------------------------------------------
    name_keys = [k for k in ("Full_name", "Full_name_2", "ФИО", "ФИО_2", "ФИО_3") if k in raw and k != name_key_used]
    aff_keys = [k for k in ("Affiliation", "Affiliation_2", "Аффилиация", "Аффилиация_2") if k in raw]
    mail_keys = [k for k in ("Email_2", "Email_3") if k in raw]
    for i, nk in enumerate(name_keys):
        b.referees.append(Referee(
            name=raw[nk],
            affiliation=raw[aff_keys[i]] if i < len(aff_keys) else "",
            email=raw[mail_keys[i]] if i < len(mail_keys) else "",
        ))

    data = ApplicationData(basic=b, raw=raw)
    data.scholar_url = get("Link_to_your_Google_Scholar_profile", "Ссылка_на_ваш_профиль_в_Google_Scholar", "Scholar")
    data.scopus_url = get("Link_to_your_Scopus_profile", "Ссылка_на_ваш_профиль_в_Scopus", "Scopus")
    data.orcid = get("ORCID", "Orcid")
    for k in order:
        v = raw[k]
        if v.startswith("http") and re.match(r"(CV|Proposal|Motivation|Letter|PhD|Reference)", k):
            data.documents[k] = v
    return data


def _normalize_track(t: str) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    return TRACKS.get(t.lower(), t[:1].upper() + t[1:] if t else "")


def _parse_date(s: str) -> tuple[str, int | None]:
    m = re.search(r"(\d{1,2})[-./](\d{1,2})[-./](\d{4})", s)
    if not m:
        return s, None
    d, mth, y = map(int, m.groups())
    try:
        born = date(y, mth, d)
    except ValueError:
        return s, None
    today = date.today()
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return born.isoformat(), age
