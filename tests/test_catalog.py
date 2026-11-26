"""ITMO catalog hierarchy: clean parents and applied-unit relations (offline, uses the committed catalog)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_itmo_map import PARENT_JUNK, clean_parent  # noqa: E402

from app.analysis.matching import _rank_in, ancestors, catalog, relation  # noqa: E402

NAMES = ["School of Physics and Engineering", "Faculty of Secure Information Technologies",
         'International Scientific and Academic Center "Security and Safety for Critical Digital Technologies"']


def by_name(name: str) -> str:
    return next(u["id"] for u in catalog() if u["name_en"] == name)


def test_clean_parent_rejects_sentence_fragments():
    for junk in ("two state mega grants led by Yuri Gunko (Trinity College", "Russian Government Decree No",
                 "European X-ray Free Electron Laser", "final round of RoboCup 2017 in Japan"):
        assert clean_parent(junk, NAMES) == ""


def test_clean_parent_resolves_names():
    assert clean_parent("Faculty of Secure Information Technologies – the leader in cybersecurity", NAMES) \
        == "Faculty of Secure Information Technologies"
    assert clean_parent('International Scientific and Academic Center "Security and Safety for Critical Dig',
                        NAMES) == NAMES[2]


def test_catalog_parents_are_catalog_units():
    ids = {u["id"] for u in catalog()}
    for u in catalog():
        assert not PARENT_JUNK.search(u.get("parent", "")), u["name_en"]
        if u.get("parent"):
            assert u.get("parent_id") in ids, u["name_en"]


def test_physics_hierarchy():
    physics, school = by_name("Faculty of Physics"), by_name("School of Physics and Engineering")
    assert school in ancestors(physics)
    assert relation(physics, school) == "inside"
    assert relation(school, physics) == "contains"
    assert relation(physics, physics) == "same"
    # a megafaculty is never recommended itself, so a unit inside it counts for the rank
    other = by_name("Faculty of Biotechnologies")
    assert _rank_in([other, physics], school) == (2, physics)
    assert _rank_in([physics], physics) == (1, "")
    assert _rank_in([other], school) == (None, "")
