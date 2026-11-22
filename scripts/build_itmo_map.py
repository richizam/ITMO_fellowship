"""Build the ITMO "science map": a catalog of research / academic units with descriptions.

Source: the official unit pages on en.itmo.ru (`/en/faculty/<id>/`, `/en/department/<id>/`). Every unit page has a name, head, homepage and a description; the script walks the
numeric IDs (responses are cached in data/cache, so re-runs are fast), keeps research and academic units, and
writes app/data/itmo_units.json, which is committed to the repository.

    uv run python scripts/build_itmo_map.py
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bs4 import BeautifulSoup  # noqa: E402

from app.sources.http import FetchError, http  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "app" / "data" / "itmo_units.json"
EN = "https://en.itmo.ru/en/{kind}/{id}/x.htm"
RANGES = {"faculty": range(1, 201), "department": range(1, 651)}

# unit pages whose <h1> is empty
NAME_FALLBACK = {"faculty-110": "ChemBio Cluster"}

RESEARCH_NAME = re.compile(
    r"laborator|\blab\b|cent(er|re)|institute|faculty|school|cluster|research|scientific|"
    r"лаборатор|центр|институт|факультет|школа|кластер|научн", re.I)
ADMIN_NAME = re.compile(
    r"office|service|admission|accounting|finance|legal|security|library|press|museum|personnel|"
    r"human resources|procurement|campus|dormitor|sport|alumni|career|marketing|communications department|"
    r"управлени[ея]|отдел|служб|бухгалтер|приёмн|приемн|библиотек|общежит", re.I)
RESEARCH_TEXT = re.compile(r"research|laborator|scientific|исследован|лаборатор", re.I)


def clean(text: str) -> str:
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse(page: str, fallback_name: str = "") -> dict | None:
    soup = BeautifulSoup(page, "lxml")
    if "404" in (soup.title.get_text() if soup.title else ""):
        return None
    h1 = soup.select_one("section.personality h1") or soup.select_one("article h1")
    name = clean(h1.get_text()) if h1 else ""
    name = name or fallback_name
    if not name:
        return None
    head = soup.select_one("ul.workdata li a")
    homepage = ""
    email = ""
    for li in soup.select("div.contents li"):
        label = li.select_one(".label")
        if not label:
            continue
        value = clean(li.get_text().replace(label.get_text(), ""))
        if "homepage" in label.get_text().lower():
            homepage = value
        elif "email" in label.get_text().lower():
            email = value
    bio = soup.select_one("div.biography")
    paras = [clean(p.get_text(" ")) for p in bio.select("p, li")] if bio else []
    paras = [p for p in paras if len(p) > 40 and "booklet" not in p.lower()]
    description = " ".join(paras)
    ru = soup.select_one("div.headerline a[href*='viewdepartment'], div.headerline a[href*='viewfaculty']")
    parent = ""
    m = re.search(r"part of the\s+(?:<[^>]+>)*([^<.,]+)", str(bio) if bio else "", re.I)
    if m:
        parent = clean(m.group(1))
    return {
        "name_en": name,
        "head": clean(head.get_text()) if head else "",
        "homepage": homepage,
        "email": email,
        "description_en": description[:4000],
        "url_ru": ("https:" + ru["href"]) if ru and ru["href"].startswith("//") else (ru["href"] if ru else ""),
        "parent": parent,
    }


def _paragraphs(url: str, min_len: int = 60) -> list[str]:
    status, body = http().get(url, browser=True)
    if status != 200:
        return []
    soup = BeautifulSoup(body, "lxml")
    return [clean(p.get_text(" ")) for p in soup.select("div.biography p, article p, main p, p")
            if len(clean(p.get_text(" "))) >= min_len]


def extra_units() -> list[dict]:
    """Units the structure page names without an en.itmo.ru page, described with official text."""
    out = []
    chembio = _paragraphs(EN.format(kind="faculty", id=110))
    scamt = [p for p in chembio if "Solution Chemistry" in p or "DNA" in p]
    if scamt:
        out.append({"id": "extra-scamt", "kind": "extra", "type": "Лаборатория", "parent": "ChemBio Cluster",
                    "name_en": 'International Laboratory "Solution Chemistry of Advanced Materials and Technologies" (SCAMT)',
                    "name_ru": "Международная лаборатория SCAMT", "head": "", "homepage": "https://scamt-itmo.ru/",
                    "email": "", "url_en": EN.format(kind="faculty", id=110), "url_ru": "",
                    "description_en": " ".join(scamt)[:4000]})
    bio = _paragraphs("http://bio.ifmo.ru/")
    if bio:
        out.append({"id": "extra-bio3m", "kind": "extra", "type": "Центр", "parent": "School of Life Sciences",
                    "name_en": 'International Research Center "Biotechnologies of the Third Millennium"',
                    "name_ru": 'Международный научный центр "Биотехнологии третьего тысячелетия"', "head": "",
                    "homepage": "http://bio.ifmo.ru/", "email": "", "url_en": "http://bio.ifmo.ru/", "url_ru": "",
                    "description_en": " ".join(bio)[:4000]})
    return out


def unit_type(name: str) -> str:
    n = name.lower()
    if n.startswith("school of"):  # the four umbrella "megafaculties" (School of Physics and Engineering, ...)
        return "Мегафакультет"
    for key, label in (("laborator", "Лаборатория"), (" lab", "Лаборатория"), ("cluster", "Кластер"),
                       ("faculty", "Факультет"), ("school", "Школа"), ("institute", "Институт"),
                       ("cent", "Центр"), ("лаборатор", "Лаборатория"), ("центр", "Центр"),
                       ("институт", "Институт"), ("факультет", "Факультет"), ("школа", "Школа")):
        if key in f" {n}":
            return label
    return "Подразделение"


def main() -> None:
    units = []
    for kind, ids in RANGES.items():
        for i in ids:
            url = EN.format(kind=kind, id=i)
            try:
                status, body = http().get(url, browser=True)
            except FetchError as e:
                print("skip", url, e)
                continue
            if status != 200 or len(body) < 5000:
                continue
            u = parse(body, NAME_FALLBACK.get(f"{kind}-{i}", ""))
            if not u:
                continue
            name = u["name_en"]
            research = RESEARCH_NAME.search(name) or RESEARCH_TEXT.search(u["description_en"])
            if not research or re.search(r"\boffice\b", name, re.I) or (ADMIN_NAME.search(name) and not RESEARCH_NAME.search(name)):
                continue
            if len(u["description_en"]) < 80 and not RESEARCH_NAME.search(name):
                continue
            u.update({"id": f"{kind}-{i}", "kind": kind, "type": unit_type(name),
                      "url_en": f"https://en.itmo.ru/en/{kind}/{i}/x.htm", "name_ru": ""})
            units.append(u)
            print(f"{u['id']:16} {u['type']:12} {name[:70]}")
    units += extra_units()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(units, ensure_ascii=False, indent=1))
    print(f"{len(units)} units -> {OUT}")


if __name__ == "__main__":
    main()
