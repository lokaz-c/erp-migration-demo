"""Generate the ways clerks misspell a supplier name.

Each supplier gets a pool of spelling variants built from the kinds of
differences seen in hand-kept books: case, punctuation, legal-form wording
("Ltd" / "Limited" / "Ltd."), "&" vs "and", abbreviations, a missing space,
truncation and one-letter typos. The canonical spelling stays the most common.
"""

from __future__ import annotations

import random
import re

LEGAL_SWAPS = {
    "Ltd": ["Limited", "Ltd.", "LTD", "Ltd", ""],
    "Limited": ["Ltd", "Ltd.", "Ltd", "LIMITED", ""],
    "Co. Ltd": ["Company Limited", "Co Ltd", "Co. Limited", "Ltd", ""],
    "SARL": ["S.A.R.L.", "Sarl", "S.A.R.L", ""],
}
ABBREVIATIONS = {
    "International": ["Intl", "Int'l", "Intl."],
    "Textiles": ["Tex", "Textile", "Txtls"],
    "Services": ["Svcs", "Service", "Servs"],
    "Engineering": ["Eng.", "Engg"],
    "Packaging": ["Pkg", "Packing"],
    "Accessories": ["Acc.", "Accs", "Accessory"],
    "Merchants": ["Merchant", "Mrchts"],
    "Freight": ["Frt"],
    "Haberdashery": ["Haberdashers"],
    "Fabrics": ["Fabric", "Fabs"],
    "Logistics": ["Logistic"],
}
_KEYBOARD_NEIGHBOURS = {
    "a": "sq",
    "b": "vn",
    "c": "xv",
    "d": "sf",
    "e": "wr",
    "f": "dg",
    "g": "fh",
    "h": "gj",
    "i": "uo",
    "j": "hk",
    "k": "jl",
    "l": "k",
    "m": "n",
    "n": "bm",
    "o": "ip",
    "p": "o",
    "r": "et",
    "s": "ad",
    "t": "ry",
    "u": "yi",
    "v": "cb",
    "w": "qe",
    "x": "zc",
    "y": "tu",
}


def _split_legal(name: str) -> tuple[str, str]:
    for legal in sorted(LEGAL_SWAPS, key=len, reverse=True):
        if name.endswith(" " + legal):
            return name[: -len(legal) - 1], legal
    return name, ""


def _typo(rng: random.Random, word: str) -> str:
    if len(word) < 4:
        return word
    i = rng.randrange(1, len(word) - 1)
    kind = rng.choice(["delete", "swap", "neighbour", "double"])
    if kind == "delete":
        return word[:i] + word[i + 1 :]
    if kind == "swap":
        return word[:i] + word[i + 1] + word[i] + word[i + 2 :]
    if kind == "double":
        return word[:i] + word[i] + word[i:]
    ch = word[i].lower()
    if ch not in _KEYBOARD_NEIGHBOURS:
        return word[:i] + word[i + 1 :]
    sub = rng.choice(_KEYBOARD_NEIGHBOURS[ch])
    return word[:i] + (sub.upper() if word[i].isupper() else sub) + word[i + 1 :]


def _mutate(rng: random.Random, name: str) -> str:
    stem, legal = _split_legal(name)
    words = stem.split()
    op = rng.choice(
        [
            "legal",
            "legal",
            "abbrev",
            "abbrev",
            "typo",
            "typo",
            "amp",
            "space",
            "punct",
            "truncate",
            "case",
        ]
    )
    if op == "legal" and legal:
        legal = rng.choice(LEGAL_SWAPS[legal])
    elif op == "abbrev":
        candidates = [i for i, w in enumerate(words) if w in ABBREVIATIONS]
        if candidates:
            i = rng.choice(candidates)
            words[i] = rng.choice(ABBREVIATIONS[words[i]])
        else:
            legal = rng.choice(LEGAL_SWAPS[legal]) if legal else legal
    elif op == "typo":
        # Typos land in the distinctive part of the name more often than not.
        i = 0 if rng.random() < 0.6 else rng.randrange(len(words))
        words[i] = _typo(rng, words[i])
    elif op == "amp" and "&" in words:
        words[words.index("&")] = rng.choice(["and", "And", "AND"])
    elif op == "space" and len(words) >= 2:
        i = rng.randrange(len(words) - 1)
        words[i : i + 2] = [words[i] + words[i + 1]]
    elif op == "punct":
        if legal:
            stem_text = " ".join(words)
            return f"{stem_text}, {legal}" if rng.random() < 0.5 else f"{stem_text} - {legal}"
        words[-1] = words[-1] + "."
    elif op == "truncate":
        text = " ".join(words + ([legal] if legal else []))
        cut = max(len(words[0]) + 4, int(len(text) * rng.uniform(0.7, 0.85)))
        return text[:cut].rstrip()
    elif op == "case":
        text = " ".join(words + ([legal] if legal else []))
        return text.upper() if rng.random() < 0.5 else text.lower()
    return " ".join(words + ([legal] if legal else []))


def make_alias_pool(rng: random.Random, name: str, size: int) -> list[str]:
    """Return `size` distinct spellings; index 0 is the canonical name."""
    pool = [name]
    attempts = 0
    while len(pool) < size and attempts < size * 20:
        attempts += 1
        variant = name
        for _ in range(rng.choice([1, 1, 1, 2, 2, 3])):
            variant = _mutate(rng, variant)
        variant = re.sub(r"\s+", " ", variant).strip()
        if variant and variant not in pool:
            pool.append(variant)
    return pool


def pick_spelling(rng: random.Random, pool: list[str]) -> str:
    """Canonical about half the time; other variants with decaying weights."""
    if len(pool) == 1 or rng.random() < 0.5:
        return pool[0]
    weights = [1.0 / k for k in range(1, len(pool))]
    return rng.choices(pool[1:], weights=weights)[0]
