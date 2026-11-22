"""Evidence check for the AI profile.

The model must back every statement with a verbatim quote from the candidate's documents. This module checks,
without any AI, that the quote really exists: exact match after normalisation (case, whitespace, PDF line
breaks and hyphenation, typographic quotes), otherwise a close fuzzy match. Statements whose quote cannot be
found are marked as not confirmed on the dossier page.
"""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

from app.models import EvidenceItem

MIN_QUOTE_CHARS = 12
FUZZY_THRESHOLD = 90  # partial_ratio, 0-100

_TRANSLATE = str.maketrans({"«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "’": "'", "‘": "'", "`": "'",
                            "–": "-", "—": "-", "‑": "-", "\xa0": " ", "­": ""})


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").translate(_TRANSLATE)
    text = re.sub(r"-\s*\n\s*", "", text)  # "meta-\ngenomic" -> "metagenomic"
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s%.,;:()/'\"+-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_NUM = re.compile(r"\d+(?:[.,]\d+)?")
SHORT_QUOTE_CHARS = 40  # shorter quotes must match exactly: one changed word matters too much


def _find(quote: str, corpus: dict[str, str]) -> tuple[str, int]:
    """(source where the quote was found, similarity 0-100). A close (non-exact) match is accepted only if
    the quote is long enough and every number in it appears exactly in the matched passage, so that
    "H-index 31" is not accepted for "H-index 13"."""
    q = normalize(quote).strip(" .,;:\"'")
    if len(q) < MIN_QUOTE_CHARS:
        return "", 0
    numbers = _NUM.findall(q)
    best_src, best = "", 0
    for src, text in corpus.items():
        if not text:
            continue
        if q in text:
            return src, 100
        al = fuzz.partial_ratio_alignment(q, text)
        score = int(al.score)
        if score >= FUZZY_THRESHOLD:
            if len(q) < SHORT_QUOTE_CHARS:
                score = min(score, FUZZY_THRESHOLD - 1)
            else:
                window = text[max(0, al.dest_start - 5): al.dest_end + 5]
                if any(n not in _NUM.findall(window) for n in numbers):
                    score = min(score, FUZZY_THRESHOLD - 1)  # numbers differ: not the same statement
        if score > best:
            best_src, best = src, score
    return best_src, best


def verify(items: list[EvidenceItem], documents: dict[str, str]) -> list[EvidenceItem]:
    """Fill `verified`, `found_in` and `similarity` of every item."""
    corpus = {src: normalize(t) for src, t in documents.items() if t and t.strip()}
    for it in items:
        if not it.quote or len(it.quote.strip()) < MIN_QUOTE_CHARS:
            it.verified, it.similarity = None, None
            continue
        src, score = _find(it.quote, corpus)
        it.similarity = score
        it.verified = score >= FUZZY_THRESHOLD
        it.found_in = src if it.verified else ""
    return items


def summary(items: list[EvidenceItem]) -> tuple[int, int]:
    """(confirmed, checked) — items without a quote are not counted as checked."""
    checked = [i for i in items if i.verified is not None]
    return sum(1 for i in checked if i.verified), len(checked)
