"""Demo candidates from the ITMO Fellowship sample folder (data/examples/<Name>/{CV,Proposal,Заявка}.*)."""
from __future__ import annotations

from pathlib import Path

from app import config
from app.pipeline import CandidateInput, _slug

KIND_BY_STEM = {"cv": "cv", "cv_2": "cv", "proposal": "proposal", "motivation": "proposal", "заявка": "application"}


def example_dirs() -> list[Path]:
    if not config.EXAMPLES_DIR.exists():
        return []
    return sorted(p for p in config.EXAMPLES_DIR.iterdir() if p.is_dir())


def example_id(folder: Path) -> str:
    return "example-" + _slug(folder.name)


def example_input(folder: Path) -> CandidateInput:
    files: dict[str, list[Path]] = {}
    for f in sorted(folder.iterdir()):
        if f.is_file():
            kind = KIND_BY_STEM.get(f.stem.lower(), "other")
            files.setdefault(kind, []).append(f)
    return CandidateInput(files=files, example=True, dossier_id=example_id(folder))
