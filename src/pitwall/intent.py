from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from .config import settings


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
    "cancel", "forget", "disregard", "ignore",
    # "stop" only negates as a verb of cessation. As a bare word it is far
    # more often the noun in "one stop", "two-stop" and "pit stop", and
    # treating those as negations silently discarded "I'd prefer the soft
    # two-stop" as a plan the driver had ruled out - in a real race the
    # engineer said "copy" and locked nothing.
    "stop telling", "stop calling", "stop raising", "stop going on",
    "stop mentioning", "stop talking", "stop asking", "stop suggesting",
    "stop recommending", "stop saying", "stop bringing up", "stop reminding",
    "stop it", "stop that", "stop with",
    # French, German and Spanish negations that cannot occur in English
    # radio. Drivers mix English jargon into their own language, so "Non, je
    # ne veux pas box lap 18 pour les mediums" carries the English commitment
    # words "box lap 18" and was locked as a pit stop. Recognising the
    # negation keeps such a refusal from changing the plan; it is passed to
    # the model instead. Bare "no" and "non" stay out: they appear in "no
    # problem" and "non-stop".
    "pas", "jamais",
    "nicht", "nichts", "nein", "kein", "keine", "keinen", "keinem", "keiner",
    "nie", "niemals",
    "nunca", "jamas", "tampoco", "no quiero", "no queremos", "no voy",
    "no vamos", "no hagas", "no hagamos", "no entres", "no entramos",
    "no paro", "no paramos",
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
    normalized = spoken_numbers_to_digits(text)
    if has_phrase(normalized, "this lap") or has_phrase(normalized, "now"):
        return max(1, int(current_lap))
    if has_phrase(normalized, "next lap"):
        return max(1, int(current_lap) + 1)
    match = re.search(r"\blap\s*(\d{1,3})\b", normalized)
    if match:
        return int(match.group(1))
    return None


# ---------------------------------------------------------------------------
# Spoken numbers
# ---------------------------------------------------------------------------

_UNIT_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9,
}
_TEEN_WORDS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS_WORDS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_NUMBER_WORD = (
    r"(?:(?:" + "|".join(_TENS_WORDS) + r")(?:[\s-]+(?:" + "|".join(_UNIT_WORDS) + r"))?"
    r"|" + "|".join(_TEEN_WORDS) + r"|" + "|".join(_UNIT_WORDS) + r")"
)
# A number is only read from words where a lap or a position is being named:
# directly after "lap", "P" or "position", or between "in" and "laps". Anywhere
# else "one stop", "two-stop" and "no one" are words, not numbers.
_SPOKEN_NUMBER = re.compile(
    r"(?P<lead>\b(?:lap|p|position|in)[\s-]+(?:number\s+)?)"
    r"(?P<number>" + _NUMBER_WORD + r")\b",
)
# "last lap two tenths quicker" measures time; the number is not the lap.
_NOT_A_LAP_NUMBER = re.compile(
    r"\s+(?:tenths?|seconds?|secs?|hundredths?|thousandths?|percent|per\s+cent|"
    r"degrees?|minutes?|mins?|metres?|meters?|kph|mph|places?|positions?|cars?|"
    r"points?|stops?|stopper|laps?)\b"
)


def _number_value(words: str) -> int:
    total = 0
    for word in re.findall(r"[a-z]+", words):
        total += _UNIT_WORDS.get(word, 0) + _TEEN_WORDS.get(word, 0) + _TENS_WORDS.get(word, 0)
    return total


def spoken_numbers_to_digits(text: str) -> str:
    """Write a spoken lap or position number as digits.

    A transcriber may spell the number out: "box lap twenty two for hards"
    read as a lap-less "take hards next" lock, so the engine picked its own lap
    while the driver believed lap 22 was set. Only numbers that name a lap or a
    position are converted ("lap twenty two", "P five", "in three laps"), so
    stop counts and ordinary words keep their meaning.
    """
    normalized = normalize_text(text)

    def replace(match: re.Match[str]) -> str:
        lead = match.group("lead")
        following = normalized[match.end():]
        if lead.startswith("in"):
            # "in two" only counts when "laps" follows; "in one stop" is a plan.
            if not re.match(r"\s+laps?\b", following):
                return match.group(0)
        elif _NOT_A_LAP_NUMBER.match(following):
            return match.group(0)
        value = _number_value(match.group("number"))
        lead = re.sub(r"number\s+$", "", lead)
        if lead.startswith("p") and not lead.startswith("position"):
            return f"p{value}"
        return f"{lead}{value}"

    return _SPOKEN_NUMBER.sub(replace, normalized)


# ---------------------------------------------------------------------------
# Question, request or statement
# ---------------------------------------------------------------------------
#
# Several radio commands change binding state: a strategy lock, a five-lap
# pit-call hold, an agreed tactic, a saved preference, a topic the engineer
# must stop raising. Each used to decide "is this a question?" with its own
# regex anchored at the first word, so "Okay, should we stay out?", "Pit now or
# stay out?" and "is there enough grip left with these tyres" were all taken
# as decisions. One classifier, consulted before any state changes, keeps them
# consistent.

# Words said in front of a sentence that do not change what it is. Radio
# acknowledgements are included because a reply so often starts with one:
# "copy, what's the weather looking like" is a question.
_LEADING_FILLERS = (
    "uh", "um", "erm", "er", "hmm", "oh", "ah", "hey", "ok", "okay", "alright",
    "all right", "so", "right", "well", "and", "but", "now", "yeah", "yes", "yep",
    "no", "nope", "please", "look", "listen", "copy", "copy that", "roger",
    "understood", "got it", "will do", "can do", "thanks", "thank you", "cheers",
    "mate", "quick question", "question", "just", "then", "also", "actually",
    "radio",
)

# Wrappers that turn what follows into a question about it.
_QUESTION_WRAPPERS = (
    "can you tell me", "could you tell me", "can you let me know",
    "could you let me know", "tell me", "let me know", "i want to know",
    "i d like to know", "i would like to know", "i wonder", "i m wondering",
    "i am wondering", "i was wondering", "do you know", "any idea",
    "i m asking", "i am asking", "just checking", "quick check",
)

_SUBJECT_WORDS = r"we|i|you|it|that|this|they|he|she|there|the|my|our|these|those"
_SUBJECT = r"(?:" + _SUBJECT_WORDS + r")"
_QUESTION_OPENER = re.compile(
    r"^(?:how|what|whats|when|where|why|which|who|whose|could|can|is|are|does|"
    r"did|should|shall|"
    # "Will do", "will stay out" and "would rather box" drop the subject;
    # they are statements, not inverted questions.
    r"(?:will|would)(?!\s+(?:do|stay|box|pit|push|try|keep|go|stop|come|take|"
    r"manage|save|look|rather|prefer|like)\b)|"
    # "Do it", "do that" and "do me a favour" are imperatives - "do it" is
    # how a driver agrees to a plan - while "do we box" asks.
    r"do(?!\s+(?:not|it|that|this|me|us|so)\b)|"
    r"am\s+i|(?:was|were)\s+" + _SUBJECT + r"|"
    # "Have" asks only with a pronoun after it: "have the hards ready" is an
    # instruction, "have we got enough fuel" a question.
    r"have\s+(?:we|i|you|they|there|anyone|anybody|someone|somebody)|"
    r"(?:has|had)\s+(?:" + _SUBJECT_WORDS + r"|anyone|anybody|someone|somebody)|"
    # A hypothetical asks what would happen; it decides nothing.
    r"(?:suppose|supposing|imagine|say)\s+(?:we|i)|"
    r"any\s+(?:chance|idea|reason|point|news|word|sign)|"
    r"(?:isn|aren|doesn|didn|shouldn|won|can|wouldn|couldn|haven|hasn)\s*t\s+"
    + _SUBJECT + r")\b"
)
# After "tell me" or "I wonder", an indirect question opens with "if" or
# "whether": "tell me if I should stay out".
_INDIRECT_QUESTION = re.compile(r"^(?:if|whether)\b")

# Markers that make a sentence a question wherever they fall.
_QUESTION_MARKER = re.compile(
    r"\b(?:what\s+if|what\s+happens\s+if|what\s+about|whether|"
    r"hypothetically|theoretically|in\s+theory|"
    r"if\s+(?:i|we)\s+(?:should|shall)|"
    r"(?:should|shall|could|would|can)\s+(?:i|we)|"
    r"do\s+you\s+(?:think|reckon|feel|want)|"
    r"is\s+it\s+worth|would\s+it\s+(?:be|make)|"
    r"does\s+(?:it|that)\s+make\s+sense)\b"
)

# A trailing tag that insists rather than asks: "I'm staying out, okay?"
_EMPHATIC_TAG = re.compile(
    r"[\s,.;:!-]+(?:ok|okay|alright|all\s+right|got\s+it|understood|copy|"
    r"copy\s+that|clear)\s*\?+\s*$"
)

# A tag question at the end asks for confirmation even when the transcriber
# left the "?" off: "we're staying out, aren't we".
_TAG_QUESTION = re.compile(
    r"\b(?:isn|aren|don|doesn|didn|won|wouldn|shouldn|can|couldn|haven|hasn)\s*t\s+"
    r"(?:we|i|it|you|they|he|she|there)$"
)

# Two pit options offered as a choice: "pit now or stay out", "hards or
# mediums", "box this lap or next". Each word maps to the option it names; a
# choice needs two different options either side of "or".
_OPTION_WORDS = {
    "box": "pit", "boxing": "pit", "pit": "pit", "pitting": "pit",
    "stop": "pit", "stopping": "pit", "come in": "pit",
    "stay out": "stay", "staying out": "stay", "go long": "stay",
    "extend": "stay", "keep going": "stay",
    "now": "now", "this lap": "now", "next lap": "next", "next": "next",
    "later": "next",
    "not": "not",
    **{
        alias: compound
        for compound, aliases in COMPOUND_ALIASES.items()
        for alias in aliases
    },
}
_OPTION_PATTERN = "|".join(
    re.escape(option).replace(r"\ ", r"\s+")
    for option in sorted(_OPTION_WORDS, key=len, reverse=True)
)
_ALTERNATIVE = re.compile(
    r"\b(?P<left>" + _OPTION_PATTERN + r")\b(?:\s+\w+){0,3}?\s+or\s+"
    r"(?:\w+\s+){0,1}?(?P<right>" + _OPTION_PATTERN + r")\b"
)

# "Can you / could you ... <verb>" asks the engineer to do something. It is a
# request, not a question, unless the verb itself asks ("can you hear me",
# "could you tell me whether we should stay out").
_REQUEST_OPENER = re.compile(
    r"^(?:can|could|would|will)\s+(?:you|ya|u)\s+(?:(?:please|just|kindly|maybe)\s+)*"
    r"(?P<rest>.*)$"
)
_REQUEST_WE = re.compile(
    r"^(?:can|could)\s+we\s+(?:(?:please|just)\s+)*(?:stop|not|quit|drop|leave|cut)\b"
)
_ASKING_VERB = re.compile(
    r"^(?:hear|see|read|explain|think|reckon|believe|guess|imagine|feel|know|"
    r"(?:tell|let)(?:\s+me)?(?:\s+know)?\s+(?:if|whether|what|whats|when|how|why|"
    r"where|which|who)|"
    r"(?:confirm|check|say|work\s+out|find\s+out)\s+(?:if|whether|what|whats|when|"
    r"how|why|where|which|who))\b"
)


def _wake_phrases() -> tuple[str, ...]:
    # Read on every call: the wake name can be changed in settings at runtime.
    return tuple(settings.wake_phrases)


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", normalize_text(text)))


def _strip_leading(words: str, phrases: Iterable[str]) -> tuple[str, bool]:
    ordered = sorted({p for p in phrases if p}, key=len, reverse=True)
    stripped = False
    changed = True
    while changed and words:
        changed = False
        for phrase in ordered:
            if words == phrase:
                return "", True
            if words.startswith(phrase + " "):
                words = words[len(phrase) + 1:]
                changed = stripped = True
                break
    return words, stripped


def strip_leading_fillers(text: str) -> str:
    """The utterance as plain words, without filler or the wake name in front."""
    core, _ = _strip_leading(_words(text), _LEADING_FILLERS + _wake_phrases())
    core, _ = _strip_leading(core, _LEADING_FILLERS)
    return core


def utterance_mood(text: str) -> str:
    """Classify a driver turn as a "question", a "request" or a "statement".

    A question asks for information or an opinion and must never change
    binding state. A request ("could you keep your answers short") asks the
    engineer to do something. A statement is a decision, an imperative or a
    report. Filler and acknowledgements in front ("Okay,", "Copy,", the wake
    name) are skipped first, and a trailing "?" counts unless it only insists
    ("I'm staying out, okay?").
    """
    normalized = normalize_text(text)
    core = strip_leading_fillers(normalized)
    if not core:
        # "Copy?" on its own asks whether the engineer heard.
        return "question" if normalized.rstrip().endswith("?") else "statement"

    request = _REQUEST_OPENER.match(core)
    if request:
        return "question" if _ASKING_VERB.match(request.group("rest")) else "request"
    if _REQUEST_WE.match(core):
        return "request"

    unwrapped, wrapped = _strip_leading(core, _QUESTION_WRAPPERS)
    if wrapped and (_QUESTION_OPENER.match(unwrapped) or _INDIRECT_QUESTION.match(unwrapped)):
        return "question"
    if _QUESTION_OPENER.match(core):
        return "question"

    body = normalized.rstrip()
    if body.endswith("?") and not _EMPHATIC_TAG.search(body):
        return "question"
    if _QUESTION_MARKER.search(core) or _TAG_QUESTION.search(core):
        return "question"
    for match in _ALTERNATIVE.finditer(core):
        left = _OPTION_WORDS.get(" ".join(match.group("left").split()))
        right = _OPTION_WORDS.get(" ".join(match.group("right").split()))
        if left and right and left != right and left != "not":
            return "question"
    return "statement"


def is_question(text: str) -> bool:
    """Whether a driver turn asks rather than tells. See ``utterance_mood``."""
    return utterance_mood(text) == "question"


def sentences(text: str) -> list[str]:
    """The utterance split into its spoken sentences, normalised.

    A turn often carries a question and an instruction together: "What's the
    gap behind? And stop telling me about the tyres." The question must not
    cancel the instruction, so settings that outlive the turn are judged one
    sentence at a time. The split is on sentence punctuation only; a comma
    is too often a pause inside one question ("should I box this lap, or
    protect the tyres").
    """
    parts = re.split(r"(?<=[.?!;])\s+", normalize_text(text))
    return [part for part in parts if re.search(r"[a-z0-9]", part)]
