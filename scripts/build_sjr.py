"""Build data/sjr.sqlite — a compact journal-quartile lookup from SCImago Journal Rank.

Source: the `sjrdata` R package (github.com/ikashnitsky/sjrdata), which mirrors the
official SCImago Journal & Country Rank exports (scimagojr.com). scimagojr.com itself
sits behind a browser challenge, so we read the mirrored .rda file instead.

Usage:
    uv run python scripts/build_sjr.py                 # downloads the .rda if missing
    uv run python scripts/build_sjr.py path/to/sjr_journals.rda
"""
from __future__ import annotations

import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "sjr_journals.rda"
OUT = ROOT / "data" / "sjr.sqlite"
URL = "https://raw.githubusercontent.com/ikashnitsky/sjrdata/master/data/sjr_journals.rda"
MIN_YEAR = 2020


def norm_title(t: str) -> str:
    t = t.lower().replace("&", " and ")
    t = re.sub(r"^the\s+", "", t)
    return re.sub(r"[^a-z0-9]+", "", t)


def main() -> None:
    import rdata  # dev dependency

    src = Path(sys.argv[1]) if len(sys.argv) > 1 else RAW
    if not src.exists():
        src.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {URL}")
        urllib.request.urlretrieve(URL, src)

    df = list(rdata.read_rda(str(src)).values())[0]
    df["year"] = df["year"].astype(float).astype(int)
    df = df[df["year"] >= MIN_YEAR]
    print("rows:", len(df), "years:", sorted(df["year"].unique()))

    OUT.unlink(missing_ok=True)
    con = sqlite3.connect(OUT)
    con.executescript(
        """
        CREATE TABLE source(
            sourceid INTEGER, year INTEGER, title TEXT, type TEXT, publisher TEXT,
            sjr REAL, best_q TEXT, categories TEXT, areas TEXT, h_index INTEGER,
            PRIMARY KEY(sourceid, year));
        CREATE TABLE issn(issn TEXT, sourceid INTEGER);
        CREATE TABLE title(norm TEXT, sourceid INTEGER);
        """
    )
    seen_issn: set[tuple[str, int]] = set()
    seen_title: set[tuple[str, int]] = set()
    rows = []
    for r in df.itertuples(index=False):
        sid = int(r.sourceid)
        q = r.sjr_best_quartile if isinstance(r.sjr_best_quartile, str) and r.sjr_best_quartile.startswith("Q") else None
        rows.append((sid, int(r.year), r.title, r.type, r.publisher if isinstance(r.publisher, str) else None,
                     float(r.sjr) if r.sjr == r.sjr else None, q,
                     r.categories if isinstance(r.categories, str) else None,
                     r.areas if isinstance(r.areas, str) else None,
                     int(r.h_index) if r.h_index == r.h_index else None))
        for raw in str(r.issn or "").split(","):
            i = raw.strip().upper()
            if len(i) == 8:
                seen_issn.add((f"{i[:4]}-{i[4:]}", sid))
        seen_title.add((norm_title(str(r.title)), sid))
    con.executemany("INSERT OR REPLACE INTO source VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.executemany("INSERT INTO issn VALUES (?,?)", sorted(seen_issn))
    con.executemany("INSERT INTO title VALUES (?,?)", sorted(seen_title))
    con.executescript(
        "CREATE INDEX i_issn ON issn(issn); CREATE INDEX i_title ON title(norm);"
        "CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);"
    )
    con.execute("INSERT INTO meta VALUES ('max_year', ?)", (str(int(df["year"].max())),))
    con.execute("INSERT INTO meta VALUES ('source', 'SCImago Journal Rank (via sjrdata)')")
    con.commit()
    con.execute("VACUUM")
    con.close()
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
