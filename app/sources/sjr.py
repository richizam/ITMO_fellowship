"""Journal quartiles from the local SCImago Journal Rank database (data/sjr.sqlite,
built by scripts/build_sjr.py)."""
from __future__ import annotations

import re
import sqlite3
from functools import lru_cache

from app import config
from app.models import CategoryQuartile, JournalQuality

SOURCE_NAME = "SCImago Journal Rank (SJR)"


def norm_title(t: str) -> str:
    t = t.lower().replace("&", " and ")
    t = re.sub(r"^the\s+", "", t)
    return re.sub(r"[^a-z0-9]+", "", t)


@lru_cache(maxsize=1)
def _con() -> sqlite3.Connection | None:
    if not config.SJR_DB.exists():
        return None
    con = sqlite3.connect(f"file:{config.SJR_DB}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def available() -> bool:
    return _con() is not None


@lru_cache(maxsize=1)
def max_year() -> int | None:
    con = _con()
    if not con:
        return None
    row = con.execute("SELECT v FROM meta WHERE k='max_year'").fetchone()
    return int(row[0]) if row else None


def _parse_categories(s: str | None) -> list[CategoryQuartile]:
    out = []
    for part in (s or "").split(";"):
        m = re.match(r"\s*(.+?)\s*(?:\((Q[1-4])\))?\s*$", part)
        if m and m.group(1):
            out.append(CategoryQuartile(category=m.group(1), quartile=m.group(2)))
    return out


def _pick(sourceids: list[int], year: int) -> JournalQuality | None:
    """Quartile for the publication year; SJR is published with a lag, so for the
    most recent years fall back to the latest available ranking."""
    con = _con()
    if not con or not sourceids:
        return None
    marks = ",".join("?" * len(sourceids))
    rows = con.execute(
        f"SELECT * FROM source WHERE sourceid IN ({marks}) ORDER BY year DESC", sourceids
    ).fetchall()
    if not rows:
        return None
    target = min(year, max_year() or year)
    best = None
    for r in rows:  # exact year first, then nearest earlier, then nearest later
        if r["year"] == target:
            best = r
            break
    if best is None:
        earlier = [r for r in rows if r["year"] < target]
        best = earlier[0] if earlier else rows[-1]
    return JournalQuality(
        quartile=best["best_q"],
        year=best["year"],
        source=SOURCE_NAME,
        sjr=best["sjr"],
        journal_title=best["title"],
        categories=_parse_categories(best["categories"]),
    )


def lookup(issns: list[str], title: str, year: int | None) -> JournalQuality | None:
    con = _con()
    if not con:
        return None
    y = year or (max_year() or 2025)
    clean = []
    for i in issns or []:
        i = i.upper().replace(" ", "")
        if re.fullmatch(r"\d{4}-?\d{3}[\dX]", i):
            clean.append(i if "-" in i else f"{i[:4]}-{i[4:]}")
    if clean:
        marks = ",".join("?" * len(clean))
        ids = [r[0] for r in con.execute(f"SELECT DISTINCT sourceid FROM issn WHERE issn IN ({marks})", clean)]
        q = _pick(ids, y)
        if q:
            q.matched_by = "ISSN"
            return q
    if title:
        n = norm_title(title)
        if len(n) >= 4:
            ids = [r[0] for r in con.execute("SELECT DISTINCT sourceid FROM title WHERE norm = ?", (n,))]
            if len(ids) == 1:  # ambiguous titles are not guessed
                q = _pick(ids, y)
                if q:
                    q.matched_by = "название журнала"
                    return q
    return None
