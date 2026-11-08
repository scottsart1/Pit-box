"""Whether the numbers in a spoken line are backed by the evidence behind it.

Narration may change the wording of a call, never its facts. Where the
evidence a line was written from is closed (an unsolicited call is narrated
from the situation header and its own facts, with no tools), every number the
line commits to can be looked for in that evidence. A number found nowhere in
it was made up, however plausible it sounds.

What is read as a number: digits and decimals, P positions, T corner numbers,
lap and clock times (1:31.2), number words ("fourteen", "one point four"),
"three tenths", "half a second", "two and a half" and percentages.

A spoken number is backed when some evidence value rounds to it at the
precision it was said (0.4 for 0.38, 62 for 61.7), sign ignored, or does so
after a conversion its own unit calls for: Celsius to Fahrenheit for a
temperature said in Fahrenheit, milliseconds to seconds for a time, a fraction
to a percentage, seconds to minutes. Three derived numbers are accepted
because they can be checked reliably: a count of laps from the current lap or
to the flag to a lap the caller names (laps to go, laps to the stop), a count
of places from the player's place to one the caller names (cars behind), and
a gap under ten seconds between two lap times written in the evidence.

Known limits, by design. The check does not know which evidence value a number
stands for, so a real number in the wrong place passes, and small integers are
nearly always somewhere in a full header. Sign is ignored, so "plus 0.4" for a
deficit of 0.4 passes. Other sums and differences (a closing rate, a fuel
figure converted from laps to kilograms, a gap between two rivals) are not
derived and count as unbacked unless the evidence states them. Ordinal words
(first, second) and the pronoun "one" are not read as numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Iterable

_SMALL = {
    "zero": 0, "nought": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_DIGIT_WORDS = {**{name: value for name, value in _SMALL.items() if value < 10}, "oh": 0}


def _alternation(names: Iterable[str]) -> str:
    return "|".join(sorted(names, key=len, reverse=True))


_ONES = _alternation(name for name, value in _SMALL.items() if 0 < value < 10)
_WORD = (
    rf"(?:(?:{_alternation(_TENS)})(?:[- ](?:{_ONES}))?|(?:{_alternation(_SMALL)}))"
    r"(?:\s+hundred(?:\s+(?:and\s+)?(?:"
    + _alternation(_TENS) + "|" + _alternation(_SMALL)
    + r"))?)?"
)
_DIGIT_WORD = _alternation(_DIGIT_WORDS)

_LAP_TIME = re.compile(r"(?<![\w.:])(\d{1,2}):([0-5]\d(?:\.\d{1,3})?)(?![\d:])")
_POSITION = re.compile(r"\b([PT])(\d{1,2})\b")
_SPOKEN_DECIMAL = re.compile(
    rf"\b({_WORD})\s+point((?:\s+(?:{_DIGIT_WORD})\b)+)", re.I
)
_AND_A_HALF = re.compile(rf"\b(\d+|{_WORD})\s+and\s+a\s+half\b", re.I)
_FRACTION = re.compile(rf"\b(a|\d|{_ONES})\s+(tenth|hundredth)s?\b", re.I)
_HALF = re.compile(r"\bhalf\s+an?\s+(second|tenth|lap)\b", re.I)
_DIGITS = re.compile(r"(?<![\w.:])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?")
_WORDS = re.compile(rf"\b{_WORD}\b", re.I)
# What joins two numbers said in one unit: "48 to 51 percent", "92/94 C",
# "1, 4 and 12 laps".
_RANGE_JOIN = re.compile(r"\s*(?:-|–|—|/|,|to|and|or)\s*", re.I)

# The unit a number is said in, read from the words right after it. Order
# matters: "degrees F" before "degrees", "ms" before "s".
_UNIT_AFTER = (
    ("pct", re.compile(r"\s?(?:%|per\s?cent\b)", re.I)),
    ("temp_f", re.compile(r"\s?(?:°\s?f\b|degrees?\s+f(?:ahrenheit)?\b|f\b|fahrenheit\b)", re.I)),
    ("temp_c", re.compile(r"\s?(?:°\s?c\b|degrees?\s+c(?:elsius)?\b|c\b|celsius\b)", re.I)),
    ("temp", re.compile(r"\s?(?:°|degrees?\b)", re.I)),
    ("ms", re.compile(r"\s?(?:ms\b|milliseconds?\b)", re.I)),
    ("s", re.compile(r"\s?(?:s\b|secs?\b|seconds?\b)", re.I)),
    ("min", re.compile(r"\s?(?:mins?\b|minutes?\b)", re.I)),
    ("kg", re.compile(r"\s?(?:kg\b|kilos?\b|kilograms?\b)", re.I)),
    ("laps", re.compile(r"(?:[\s-]?laps?\b|\s+to\s+go\b|\s+remaining\b)", re.I)),
    ("places", re.compile(r"\s+(?:places?|positions?|cars?|spots?)\b", re.I)),
)

# Keys whose values are lap numbers or running-order places. A count of laps
# from the current lap or to the flag, or of places from the player's own, to
# one of them is a number a line may say without it being written anywhere:
# laps to go, laps to the stop, cars behind.
_LAP_KEYS = frozenset({"lap", "box_lap", "box_laps", "until_lap"})
_PLACE_KEYS = frozenset({
    "position", "grid_position", "active_cars", "rejoin_position", "projected_position",
})


@dataclass(frozen=True)
class SpokenNumber:
    """One number a line commits to: as written, its magnitude, precision and unit."""

    text: str
    value: float
    decimals: int
    unit: str = ""


def _word_value(phrase: str) -> int:
    """"twenty-two" -> 22, "one hundred and two" -> 102."""
    value = 0
    for word in re.split(r"[\s-]+", phrase.lower()):
        if word == "hundred":
            value = max(value, 1) * 100
        else:
            value += _TENS.get(word, _SMALL.get(word, 0))
    return value


def _decimals(digits: str) -> int:
    return len(digits.split(".", 1)[1]) if "." in digits else 0


def _unit_after(text: str, end: int) -> str:
    for unit, pattern in _UNIT_AFTER:
        if pattern.match(text, end):
            return unit
    return ""


def _mentions(text: str, *, evidence: bool = False) -> list[SpokenNumber]:
    """Every number in the text, in reading order.

    ``evidence`` reads every number word as a value, "one" included, since
    on the evidence side a wider reading can only make the check more lenient.
    """
    found: list[tuple[int, int, SpokenNumber]] = []
    taken: list[tuple[int, int]] = []

    def add(start: int, end: int, value: float, decimals: int, unit: str | None = None) -> None:
        if any(start < stop and end > begin for begin, stop in taken):
            return
        taken.append((start, end))
        found.append((start, end, SpokenNumber(
            text[start:end], abs(value), decimals,
            _unit_after(text, end) if unit is None else unit,
        )))

    for match in _LAP_TIME.finditer(text):
        seconds = match.group(2)
        add(match.start(), match.end(), int(match.group(1)) * 60 + float(seconds),
            _decimals(seconds), "laptime")
    for match in _SPOKEN_DECIMAL.finditer(text):
        fraction = "".join(str(_DIGIT_WORDS[word.lower()]) for word in match.group(2).split())
        add(match.start(), match.end(), float(f"{_word_value(match.group(1))}.{fraction}"),
            len(fraction))
    for match in _AND_A_HALF.finditer(text):
        whole = match.group(1)
        base = float(whole) if whole.isdigit() else _word_value(whole)
        add(match.start(), match.end(), base + 0.5, 1)
    for match in _HALF.finditer(text):
        kind = match.group(1).lower()
        value, unit = {"second": (0.5, "s"), "tenth": (0.05, "s"), "lap": (0.5, "laps")}[kind]
        add(match.start(), match.end(), value, 2 if kind == "tenth" else 1, unit)
    for match in _FRACTION.finditer(text):
        count = match.group(1).lower()
        count_value = 1 if count == "a" else int(count) if count.isdigit() else _SMALL[count]
        tenths = match.group(2).lower() == "tenth"
        add(match.start(), match.end(), count_value / (10 if tenths else 100),
            1 if tenths else 2, "s")
    for match in _POSITION.finditer(text):
        add(match.start(), match.end(), float(match.group(2)), 0,
            "position" if match.group(1) == "P" else "")
    for match in _DIGITS.finditer(text):
        whole, fraction = match.group(1), match.group(2) or ""
        value = float(whole.replace(",", "") + fraction)
        if (
            not evidence and not fraction and len(whole) == 4
            and 1950 <= value <= 2100 and not _unit_after(text, match.end())
        ):
            continue  # a year: "the 2026 regulations"
        add(match.start(), match.end(), value, len(fraction.lstrip(".")))
    for match in _WORDS.finditer(text):
        value = _word_value(match.group(0))
        unit = _unit_after(text, match.end())
        if not evidence and value == 1 and match.group(0).lower() == "one" and not unit:
            continue  # "the one thing", "one more", "no one": a pronoun, not a count
        add(match.start(), match.end(), float(value), 0, unit)

    found.sort(key=lambda item: item[0])
    numbers = [number for _, _, number in found]
    # "48 to 51 percent": the first of a run of numbers is said in the last's
    # unit. Walked backwards so the unit reaches every number in the run.
    for index in range(len(found) - 2, -1, -1):
        end = found[index][1]
        start_next = found[index + 1][0]
        number, following = numbers[index], numbers[index + 1]
        if not number.unit and following.unit and _RANGE_JOIN.fullmatch(text[end:start_next]):
            numbers[index] = SpokenNumber(number.text, number.value, number.decimals, following.unit)
    return numbers


def spoken_numbers(text: str) -> list[SpokenNumber]:
    """The numbers a spoken line commits to, in reading order."""
    return _mentions(text or "")


def _walk(source: Any, out: set[float], times: list[float]) -> None:
    """Every number in the evidence into ``out``, and its lap times into ``times``.

    Values, numbers in text and numbers in keys (rain_15_pct) all count.
    """
    if source is None or isinstance(source, bool):
        return
    if isinstance(source, (int, float)):
        out.add(abs(float(source)))
    elif isinstance(source, str):
        out.update(abs(float(item)) for item in re.findall(r"\d+(?:\.\d+)?", source))
        for number in _mentions(source, evidence=True):
            out.add(number.value)
            if number.unit == "laptime" and number.decimals:
                times.append(number.value)
    elif isinstance(source, dict):
        for key, value in source.items():
            if isinstance(key, str):
                _walk(key, out, times)
            _walk(value, out, times)
    elif isinstance(source, (list, tuple, set, frozenset)):
        for item in source:
            _walk(item, out, times)


def _marks(sources: Iterable[Any], keys: frozenset[str]) -> list[float]:
    marks: set[float] = set()

    def visit(source: Any) -> None:
        if isinstance(source, dict):
            for key, value in source.items():
                if key in keys:
                    items = value if isinstance(value, (list, tuple)) else [value]
                    marks.update(
                        float(item) for item in items
                        if isinstance(item, (int, float)) and not isinstance(item, bool)
                        and float(item).is_integer() and item > 0
                    )
                else:
                    visit(value)
        elif isinstance(source, (list, tuple)):
            for item in source:
                visit(item)

    visit(list(sources))
    return sorted(marks)


def lap_counts(current_lap: Any, total_laps: Any, *sources: Any) -> list[float]:
    """Counts of laps a line may say: to go, to a named lap, from it to the flag.

    A named lap is a box, hold or event lap in ``sources``. Each count is
    accepted with and without the current lap, since "twelve to go" on lap 8
    of 20 may count it or not.
    """
    anchors = [
        float(value) for value in (current_lap, total_laps)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
    ]
    marks = set(_marks(sources, _LAP_KEYS)) | set(anchors)
    counts: set[float] = set()
    for mark in marks:
        for anchor in anchors:
            gap = abs(mark - anchor)
            counts.update({gap, gap + 1})
    return sorted(count for count in counts if count > 0)


def place_counts(position: Any, *sources: Any) -> list[float]:
    """Counts of places between the player's place and a named one.

    A named place is a rival's, a rejoin or projected place, or the size of
    the field in ``sources``: "two cars behind you" from P18 of 20.
    """
    if not isinstance(position, (int, float)) or isinstance(position, bool) or position <= 0:
        return []
    return sorted({abs(mark - float(position)) for mark in _marks(sources, _PLACE_KEYS)} - {0.0})


def _close(spoken: float, value: float, decimals: int) -> bool:
    return abs(spoken - value) <= 0.5 * 10.0 ** -decimals + 1e-9


def _readings(value: float, unit: str) -> Iterable[float]:
    """An evidence value as it may be said in the spoken number's unit."""
    yield abs(value)
    if unit == "pct" and abs(value) <= 1:
        yield abs(value) * 100
    if unit in {"s", "laptime"}:
        yield abs(value) / 1000
    if unit == "ms":
        yield abs(value) * 1000
    if unit == "min":
        yield abs(value) / 60
    if unit in {"temp_f", "temp"}:
        yield abs(value * 9 / 5 + 32)
    if unit in {"temp_c", "temp"}:
        yield abs((value - 32) * 5 / 9)


def is_backed(
    number: SpokenNumber,
    values: Iterable[float],
    *,
    laps: Iterable[float] = (),
    places: Iterable[float] = (),
    times: Iterable[float] = (),
) -> bool:
    """Whether one spoken number is backed by the evidence values.

    ``laps`` and ``places`` are counts (from lap_counts and place_counts) a
    whole number said in laps or places may be; ``times`` are lap times whose
    gaps may be said in seconds.
    """
    for value in values:
        if any(_close(number.value, reading, number.decimals) for reading in _readings(value, number.unit)):
            return True
    if number.decimals == 0 and number.unit in {"laps", "places"}:
        counts = laps if number.unit == "laps" else places
        if any(_close(number.value, count, 0) for count in counts):
            return True
    if number.unit in {"s", ""} and number.decimals and number.value < 10:
        for first, second in combinations(sorted(set(times)), 2):
            if _close(number.value, abs(first - second), number.decimals):
                return True
    return False


def unsupported(
    text: str,
    *evidence: Any,
    laps: Iterable[float] = (),
    places: Iterable[float] = (),
) -> list[SpokenNumber]:
    """The numbers in ``text`` that nothing in ``evidence`` backs.

    ``evidence`` is any mix of text (a header, a prompt, a template) and data
    (a facts mapping, a tool result). ``laps`` and ``places`` are the counts
    of laps and places the line may say without the evidence stating them
    (see lap_counts and place_counts).
    """
    values: set[float] = set()
    times: list[float] = []
    _walk(list(evidence), values, times)
    laps, places = list(laps), list(places)
    return [
        number for number in spoken_numbers(text)
        if not is_backed(number, values, laps=laps, places=places, times=times)
    ]
