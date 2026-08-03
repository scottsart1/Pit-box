from __future__ import annotations

import re
import unicodedata
from typing import Iterable


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(ascii_text.lower().split())


def has_phrase(text: str, phrase: str) -> bool:
    """Boundary-aware phrase matching.

    Plain substring checks are unsafe for radio intent routing: for example,
    ``ers`` occurs inside ``Verstappen``. This matcher treats each phrase as a
    sequence of complete alphanumeric tokens while tolerating punctuation and
    variable whitespace between words.
    """
    normalized = normalize_text(text)
    words = re.findall(r"[a-z0-9]+", normalize_text(phrase))
    if not words:
        return False
    pattern = r"(?<![a-z0-9])" + r"[^a-z0-9]+".join(map(re.escape, words)) + r"(?![a-z0-9])"
    return re.search(pattern, normalized) is not None


def has_any_phrase(text: str, phrases: Iterable[str]) -> bool:
    return any(has_phrase(text, phrase) for phrase in phrases)


NEGATIONS = (
    "not", "dont", "don t", "do not", "wont", "won t", "will not", "isnt",
    "is not", "arent", "are not", "cant", "can t", "cannot", "never",
    "no longer", "instead of", "rather than", "without", "avoid", "skip",
    "cancel", "forget", "disregard", "ignore", "stop",
)


def has_negation(text: str) -> bool:
    """Whether an utterance negates or withdraws what it mentions.

    A driver saying "I'm *not* boxing" or "I will *not* take another pit stop"
    previously matched the same intent as "I'm boxing", because only the
    compound and the verb were inspected. That inverted the driver's own
    decision into a locked pit call.
    """
    return has_any_phrase(text, NEGATIONS)


COMPOUND_ALIASES = {
    "SOFT": ("soft", "softs", "soft tyre", "soft tire", "red tyre", "red tire"),
    "MEDIUM": ("medium", "mediums", "medium tyre", "medium tire", "yellow tyre", "yellow tire"),
    "HARD": ("hard", "hards", "hard tyre", "hard tire", "white tyre", "white tire"),
    "INTER": ("inter", "inters", "intermediate", "intermediates"),
    "WET": ("wet", "wets", "full wet", "full wets"),
}


def extract_compounds(text: str) -> list[str]:
    normalized = normalize_text(text)
    found: list[tuple[int, str]] = []
    for compound, aliases in COMPOUND_ALIASES.items():
        best: int | None = None
        for alias in aliases:
            words = re.findall(r"[a-z0-9]+", alias)
            pattern = r"(?<![a-z0-9])" + r"[^a-z0-9]+".join(map(re.escape, words)) + r"(?![a-z0-9])"
            match = re.search(pattern, normalized)
            if match and (best is None or match.start() < best):
                best = match.start()
        if best is not None:
            found.append((best, compound))
    found.sort()
    return [compound for _, compound in found]


def extract_lap(text: str, current_lap: int = 0) -> int | None:
    normalized = normalize_text(text)
    if has_phrase(normalized, "this lap") or has_phrase(normalized, "now"):
        return max(1, int(current_lap))
    if has_phrase(normalized, "next lap"):
        return max(1, int(current_lap) + 1)
    match = re.search(r"\blap\s*(\d{1,3})\b", normalized)
    if match:
        return int(match.group(1))
    return None
