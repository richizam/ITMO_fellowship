"""Plain-text extraction from the documents a candidate submits (PDF, DOCX, DOC, TXT)."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path


class ExtractionError(RuntimeError):
    pass


# Some PDF fonts map ligatures to private-use code points; the "ti" ligature is the usual one.
_LIGATURES = {"\U0010019f": "ti", "\ufb01": "fi", "\ufb02": "fl", "\ufb00": "ff", "\ufb03": "ffi"}


def clean_text(text: str) -> str:
    for k, v in _LIGATURES.items():
        text = text.replace(k, v)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Co")
    return text.replace("\x00", "")


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return clean_text(_pdf(path))
    if suffix == ".docx":
        return clean_text(_docx(path))
    if suffix in (".doc", ".rtf", ".odt"):
        return clean_text(_via_soffice(path))
    if suffix in (".txt", ".md"):
        return clean_text(path.read_text(errors="ignore"))
    raise ExtractionError(f"Unsupported file type: {path.name}")


def _pdf(path: Path) -> str:
    import pymupdf

    with pymupdf.open(path) as doc:
        text = "\n".join(page.get_text("text", sort=True) for page in doc)
    if len(text.strip()) < 50:
        raise ExtractionError(f"{path.name}: PDF has no text layer (scanned?)")
    return text


def _docx(path: Path) -> str:
    import docx

    d = docx.Document(str(path))
    parts = [p.text for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            cells = []
            for c in row.cells:
                t = c.text.strip()
                if t and (not cells or cells[-1] != t):  # merged cells repeat their text
                    cells.append(t)
            parts.append(" | ".join(cells))
    return "\n".join(parts)


def _via_soffice(path: Path) -> str:
    """Legacy .doc etc.: try antiword, then LibreOffice headless conversion."""
    if shutil.which("antiword") and path.suffix.lower() == ".doc":
        out = subprocess.run(["antiword", str(path)], capture_output=True, text=True, timeout=60)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise ExtractionError(f"{path.name}: install LibreOffice (soffice) or antiword to read .doc files")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "txt:Text", "--outdir", tmp, str(path)],
            capture_output=True, timeout=120,
        )
        out = Path(tmp) / (path.stem + ".txt")
        if not out.exists():
            raise ExtractionError(f"{path.name}: LibreOffice conversion failed")
        return out.read_text(errors="ignore")
