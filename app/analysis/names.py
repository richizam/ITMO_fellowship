"""Name normalisation and author matching (Latin / Cyrillic, "I. Testov" vs "Testov, Ivan")."""
from __future__ import annotations

import re
import unicodedata

_TR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
    "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
TITLES = {"dr", "prof", "professor", "phd", "mr", "mrs", "ms", "md", "dsc", "assoc"}


def has_cyrillic(s: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", s or ""))


def translit(s: str) -> str:
    out = []
    for ch in s:
        low = ch.lower()
        if low in _TR:
            t = _TR[low]
            out.append(t.capitalize() if ch.isupper() and t else t)
        else:
            out.append(ch)
    return "".join(out)


def ascii_fold(s: str) -> str:
    s = translit(s)
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def tokens(name: str) -> list[str]:
    name = ascii_fold(name).replace(",", " ").replace(".", ". ")
    toks = [t.strip(". ") for t in name.split()]
    return [t for t in toks if t and t.lower().strip(".") not in TITLES]


def _norm(t: str) -> str:
    return re.sub(r"[^a-z]", "", t.lower())


def latin_display_name(name: str) -> str:
    """'Смирнов Николай Петрович' -> 'Nikolai Smirnov' (given + family, for searching)."""
    if not has_cyrillic(name):
        return name
    parts = name.split()
    if len(parts) >= 2:
        return translit(f"{parts[1]} {parts[0]}")
    return translit(name)


def name_keys(name: str) -> set[tuple[str, str]]:
    """(surname, first initial) pairs, for both "Given Family" and "Family Given" orders.
    Compound surnames ("Abd El-Rahman") are also joined."""
    toks = tokens(name)
    keys: set[tuple[str, str]] = set()
    if len(toks) < 2:
        return keys
    raw = name.strip()
    family_first = "," in raw or has_cyrillic(raw)
    orders = [(toks[-1], toks[0])] + [(toks[0], toks[1])]
    if family_first:
        orders.reverse()
    for sur, given in orders:
        s, g = _norm(sur), _norm(given)
        if len(s) >= 2 and g:
            keys.add((s, g[0]))
    # compound surname: last two tokens joined, e.g. "Abd El-Rahman" -> "abdelrahman"
    if len(toks) >= 3:
        keys.add((_norm(toks[-2] + toks[-1]), _norm(toks[0])[:1]))
        keys.add((_norm(toks[-1]), _norm(toks[0])[:1]))
    return {k for k in keys if k[1]}


def author_matches(author: str, keys: set[tuple[str, str]]) -> bool:
    return bool(name_keys(author) & keys) if keys else False


def split_author_list(s: str) -> list[str]:
    """Scholar author strings: 'A Other, J Wu, I Testov, ...'."""
    parts = [p.strip() for p in re.split(r",|;| and ", s or "") if p.strip()]
    return [p for p in parts if p not in ("...", "…")]


def similarity(a: str, b: str) -> float:
    from rapidfuzz import fuzz

    return fuzz.token_sort_ratio(ascii_fold(a).lower(), ascii_fold(b).lower()) / 100
