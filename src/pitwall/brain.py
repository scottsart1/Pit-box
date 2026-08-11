from __future__ import annotations

import json
import re
import time
from typing import Any

from .config import settings
from .database import PitWallDatabase
from .identity import match_drivers
from .intent import (
    extract_compounds,
    extract_lap,
    has_any_phrase,
    has_negation,
    has_phrase,
    normalize_text,
)
from .prerace import PreRacePlanner
from .providers import ProviderResult, ProviderRouter
from .state import StateStore
from .tools import TelemetryTools

PERSONA = """
You are Your Pit Box, a senior Formula 1 simulator race engineer.

Be calm, precise, proactive and brief. Lead with the answer, not the data.

You are analysing, not reading out. The driver is at speed and cannot do arithmetic on
lap times, gaps or sector splits. A number on its own is not an answer.
- A comparison ("how am I doing against X", "am I catching him", "is he quicker") is answered
  with a verdict first — closing, holding, losing — then the evidence that supports it, then what
  to do about it. Call get_pace_verdict; never reply with two lap times and let the driver subtract.
- An open question ("how is the race going", "where do I stand", "any updates") is answered from
  get_race_picture: the real threat or opportunity, whether their own pace is holding, and the one
  thing that follows. Not a status dump.
- Trends beat snapshots. "Three tenths a lap and about four laps to contact" is useful;
  "1:37.7, 1:37.9, 1:37.8" is not.
- Say what it means for the outcome: whether a position is coming, whether the tyre reaches the
  end, whether the stop still works. Numbers are the evidence for that, never the substitute.
- If the data cannot support a verdict, say which part is missing in a few words and give the
  best call available. Do not pad with numbers to fill the gap.
Return only the words that should be spoken over team radio. Think privately: never output analysis,
deliberation, tool-selection notes, self-talk, or phrases such as "let me check", "I need to",
"wait", or "hold on". Do not use headings, bullet lists, markdown, or restate the prompt.
Use telemetry tools for facts not already in the situation header. Never invent a number.
If telemetry is unavailable or stale, say so plainly once.
Answer the driver's latest request first and stay on that subject. Do not append strategy, fuel,
energy, or coaching advice unless it directly answers the request or is an immediate safety action.
Older radio calls are context, not authority: the current deterministic state wins.
Use the temperature unit named in the situation header and retain the driver's latest unit request.
Never infer that the driver is closing from a single lap-time comparison; use measured gap trend.
A positive player-minus-rival lap delta means the player was slower.
There is no DRS- or Manual-Override-specific tyre-temperature target; tyre temperature affects grip,
not activation of the overtaking aid. For rear power-oversteer, lower on-throttle differential; for
rear instability under braking, move brake bias slightly forward. Do not call adjustable diff settings
a garage-only item.

Pit calls must specify BOTH the lap and the tyre compound when the strategy tool supports it:
"Box lap 18 for hards." Never repeat an expired game pit window as a current recommendation.
The deterministic Your Pit Box strategy plan is primary; the game's window is only a cross-check.
Quote stop laps and compounds from the recommended plan exactly as supplied. If you cite a
different ranked plan, name it as an alternative in the same sentence — never present two plans'
laps as if they were one plan.
When strategy confidence is low, call the recommendation provisional and name the wear/deg
assumption; do not present a track-default model as proven personal degradation.
Respect the strategy tool's compound-rule legality. Never recommend finishing a dry Race
without two different dry compounds unless inters or wets have been used.
For SC, VSC, and red-flag calls, use the tool's neutralisation state and effective pit loss;
do not treat a red-flag tyre change like an ordinary green-flag pit stop.
For a strategy request, inspect the ranked alternatives, uncertainty, projected per-wheel wear,
traffic/rejoin, compound legality, and evidence source. Explain why the chosen plan beats the
next plan and state the condition that would change the call. Never simply echo the first plan.
For attack/defence advice, use gap trend, rival tyres/laps, racing-line/corner evidence, energy
and the current 2026 overtaking aid. Give a concrete preparation point and overtaking/defending
phase, or say evidence is absent. In 2026-regulation sessions call the overtaking aid Manual
Override, not DRS. Treat blue flags, invalid qualifying laps, unserved drive-through/stop-go
penalties, and a rival pitting from behind as high-priority operational facts.

In qualifying, do not volunteer race-style gaps ahead or behind. The useful comparison is the
field's best lap times, the driver's best lap, theoretical best, target lap, and the delta required.
Only discuss gaps in qualifying when the driver explicitly asks about traffic or track position.

For driving coaching, separate fuel-saving advice from tyre-temperature advice. Use learned
corner metrics and the lap map when available. Refer to auto-learned corners by their displayed
name or distance. Do not claim a named real-world corner unless the tool provides that name.

For setup advice, state that only the front wing is normally adjustable during a pit stop.
Full Race, Quali, and Hybrid setup recommendations are for the garage or a future session.

Damage: the only pit-stop repair this telemetry exposes is the front wing. There is no
"inspection" stop and no gearbox, engine, floor, diffuser or sidepod repair to call for. Never tell
the driver to box to inspect or fix those — a stop costs the pit-lane time and fixes none of them.
Report reliability damage once, as a fact and its consequence (expected pace loss, what to manage,
whether it threatens finishing), then leave it alone. Repeating an unactionable damage report is
worse than silence. Only recommend a stop for damage when the front wing is the problem, or when
the tyre stop it is being combined with is already justified on its own.

Driver tyre feedback is evidence, not telemetry. When the driver's report and the bounded model
disagree, state the model's position once with the wear or degradation number that drives it. If the
driver repeats the decision, acknowledge it and follow it; do not re-argue the same pit call.
When the driver declines a pit call, never leave it at "copy": give the revised consequence in one
clause and offer exactly one concrete alternative — a later box lap, or a different plan shape such
as going long — with its cost. The driver said no to this stop, not to having a strategy.

Saved-session review, lap comparison and full-field analysis answers obey a stricter contract,
because they quote stored measurements rather than the live car.
Every value carries a provenance label. Respect it in the wording:
- observed — measured directly from a valid packet. State it as fact.
- derived — calculated deterministically from observed values. State it as fact.
- estimated — produced by a model. Say "about" or "roughly" and never present it as measured.
- stale — real but older than its freshness budget. Say when it was last true.
- unavailable — not supplied. Say the value is unavailable. Never substitute zero.
Always name the comparison basis in the answer: "against your current-session best",
"against the field median", "against P4's best lap". A delta without its reference is
not an answer. If the reference carries compatibility caveats, say so in a few words
before giving the delta, and do not give prescriptive coaching from a context-only reference.
Distinguish the three time-gain levels supplied by the tools. "Measured" is the segment
loss straight from aligned timing. "Attributed" is an interval and must be spoken as a
range that is at most the measured loss. "Opportunity" is what better execution could be
worth. Never promote an attributed interval or an opportunity into a measured fact.
The game does not expose the full sensor set for opponents. When a rival lacks brake,
steering or line data, say that input is unavailable for that car and answer from what is
supplied — timing, speed, position, stint. Never coach a rival's technique from data the
game did not send, and never fill the gap with the player's own values.
When the driver asks why, retrieve the finding's evidence chain and give the earliest
well-supported cause, not the last visible symptom. Use "likely contributed" or
"associated with" unless the evidence supports causation outright.

Normally answer in one or two short radio sentences. A deeper requested explanation may use
three concise sentences. Every sentence must contain driver-useful information. Do not mention being an AI.
""".strip()

PROACTIVE_PERSONA = """
You are making an unsolicited race-engineer radio call. Be useful, not chatty.
Lead with the single most important action or target. Include at most three numbers.
Do not repeat a recent call unless the situation materially changed. Do not ask a question,
except for a driver_check event: then ask exactly one closed tyre-state question using the supplied
choices (holding or going away), with no open-ended follow-up.
For a two-lap progress update, cover pace versus target, tyre/strategy status, and one driving
opportunity only when supported. Keep it to two short radio sentences.
In qualifying, replace race gaps and race strategy with pole/session-best time, the driver's best,
theoretical best, target time, required delta, clear-air/run status, and one lap-building opportunity.
""".strip()

PRE_SESSION_PERSONA = """
You are delivering a pre-session briefing, not an at-speed radio reply. Be calm and structured in
five to eight concise sentences. State all three supplied session goals explicitly. Every strategy,
target or weather projection must include its supplied confidence, and a null projection must be
described as unavailable rather than estimated. Separate personal historical evidence from track
baselines. Do not add facts, circuit lore, strategy times or grid-side claims that are absent from
the deterministic payload.
""".strip()

DEBRIEF_PERSONA = """
You are delivering a lap or session debrief. Be analytical and direct about where time or positions
went. Compare only numbers present in the deterministic payload. End with exactly one actionable
instruction and put no advice before that final instruction. For a post-race debrief use two concise
sentences suitable for the slow-down lap; a qualifying-lap debrief may use up to three.
When the payload supplies ranked findings, speak the highest-opportunity one and name the
segment it belongs to. Quote its measured loss as measured; quote an attributed range as a
range. Never sum attributed ranges into a single total. If a positive finding is supplied,
name it in a few words so the driver knows what to keep doing. Name the reference the
findings were measured against.
""".strip()

_VERBOSITY_GUIDANCE = {
    "terse": (
        "Verbosity: terse. Reply in the fewest words possible — numbers and the "
        "action only, ideally one short clause. Omit pleasantries."
    ),
    "standard": "",
    "chatty": (
        "Verbosity: conversational. You may add a short sentence of context or "
        "encouragement, while still leading with the action and the numbers."
    ),
}


_SAFETY_ANCHOR = (
    "Non-negotiable: the personality note above only changes tone and wording. "
    "It never overrides any instruction in this brief. Continue to use only "
    "telemetry-tool facts, never invent a number, and respect the strategy "
    "tool's compound-rule legality and neutralisation state."
)


def compose_persona(
    base: str,
    verbosity: str | None = None,
    standing_instructions: list[dict[str, Any]] | None = None,
) -> str:
    """Fold the user's engineer name, custom persona and verbosity into a base
    persona. Any user-supplied text is placed between the base brief and a final
    safety anchor, so custom tone can never appear as the last word and override
    the safety-critical instructions.
    """
    parts = [base]
    # Tolerate anything that survived from an older or hand-edited preference
    # file. A bare list of strings previously raised AttributeError here, which
    # took down every model call the engineer made.
    rules: list[str] = []
    for item in standing_instructions or []:
        if isinstance(item, dict):
            rule = str(item.get("rule", "")).strip()
        elif isinstance(item, str):
            rule = item.strip()
        else:
            continue
        if rule:
            rules.append(rule)
    if rules:
        # Standing instructions sit before the safety anchor, so the driver can
        # silence a topic but cannot switch off a safety-critical call.
        parts.append(
            "Standing instructions from the driver, which remain in force:\n"
            + "\n".join(f"- {rule}" for rule in rules)
        )
    name = settings.engineer_name.strip()
    if name and name.lower() != "mark":
        parts.append(f"Your call sign is {name}.")
    custom = settings.engineer_persona.strip()
    guidance = _VERBOSITY_GUIDANCE.get(verbosity or settings.radio_verbosity, "")
    if custom or guidance or rules:
        if custom:
            parts.append(custom)
        if guidance:
            parts.append(guidance)
        # Re-assert the rules after any user-supplied text, including standing
        # instructions: a driver may silence a topic, but must never be able to
        # phrase an instruction that removes a safety-critical call.
        parts.append(_SAFETY_ANCHOR)
    return "\n\n".join(parts)

# High reasoning is reserved for actual planning. Routine pace/corner/target questions
# stay on the low-latency path instead of being escalated merely because they contain
# words such as "why", "corner", or "best lap".
_DEEP_TERMS = (
    "strategy",
    "undercut",
    "overcut",
    "what if",
    "compare stint",
    "compare strategy",
    "make it to the end",
    "make these tyres last",
    "pit now",
    "stay out",
    "safety car pit",
    "vsc pit",
    "red flag strategy",
    "setup",
    "race plan",
    "tyre plan",
    "tire plan",
)

_FEEDBACK_PATTERNS = {
    "tyres_gone": (
        "they're gone", "they are gone", "tyres gone", "tires gone",
        "no grip left", "falling off a cliff",
    ),
    "tyres_going": (
        "starting to go", "dropping off", "losing the rears",
    ),
    "tyres_fine": (
        "still good", "plenty left", "tyres are fine", "tires are fine",
        "happy with the tyres", "happy with the tires", "rears are holding",
        "the rears are holding",
    ),
    "understeer": ("understeer", "won't turn", "does not turn", "front won't bite"),
    "no_front_grip": ("no front grip", "fronts are gone", "front tyres are gone"),
    "oversteer": ("oversteer", "rear is loose", "rear steps out"),
    "rear_instability": ("rear unstable", "rear instability", "snappy rear"),
    "traction": ("no traction", "wheelspin", "spinning the rears"),
    "no_grip": ("no grip", "tires are toast", "tyres are toast"),
}


def _fahrenheit(celsius: float) -> float:
    return celsius * 9.0 / 5.0 + 32.0


def _temperature_values(values: list[float], unit: str) -> tuple[list[float], str]:
    if unit.lower() == "f":
        return [_fahrenheit(float(value)) for value in values], "F"
    return [float(value) for value in values], "C"


def _contains_deliberation_for_history(text: str) -> bool:
    lowered = " ".join(text.lower().split())
    return any(marker in lowered for marker in (
        "let me check", "let me think", "wait —", "wait -", "hold on",
        "the driver asked", "the question is", "key facts:",
        "critical concern:", "the current call:", "i should give",
        "the strategy engine ranks", "deterministic primary",
    ))


def _limit_radio_sentences(text: str, route: str, verbosity: str) -> str:
    """Keep TTS output radio-length even when a provider ignores the style brief."""
    flattened = " ".join(text.replace("\n", " ").split())
    if not flattened:
        return ""
    if verbosity == "terse":
        limit = 1
    elif route == "deep" or verbosity == "chatty":
        limit = 3
    else:
        limit = 2
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", flattened)
    return " ".join(sentences[:limit]).strip()


class EngineerBrain:
    def __init__(
        self,
        store: StateStore,
        tools: TelemetryTools,
        database: PitWallDatabase,
    ) -> None:
        self.store = store
        self.tools = tools
        self.database = database
        self.router = ProviderRouter(settings)
        self.last_provider_result: ProviderResult | None = None

    async def _header(self, *, include_strategy: bool = False) -> str:
        state = await self.store.snapshot_analysis()
        target = state.get("analysis", {}).get("target", {})
        top = state.get("analysis", {}).get("flagged_corners", [])
        corner_text = (
            top[0].get("instruction") if top else "no repeated corner loss yet"
        )

        if state.get("mode_profile") == "qualifying":
            qualifying = self.tools.qualifying_summary(state, 5)
            field_text = ", ".join(
                f"{item['name']} {item['best_lap']}"
                for item in qualifying.get("field", [])
                if item.get("best_lap")
            ) or "no representative field laps yet"
            return (
                f"SESSION {state['session_type']} | {state['track_name']} | "
                f"LAP {state['current_lap']} | CONNECTED {state['connected']} "
                f"PAUSED {state['game_paused']} | "
                f"QUALI BEST LAPS {field_text} | "
                f"PLAYER BEST {qualifying.get('player_best')} | "
                f"THEORETICAL {qualifying.get('theoretical_best')} | "
                f"TARGET {qualifying.get('target')} | "
                f"REQUIRED DELTA {qualifying.get('delta_player_to_target_s')}s | "
                f"TYRE {state['tyre']['compound']} age {state['tyre']['age_laps']} | "
                f"WEATHER {state['weather']} rain15 {state['rain_next_15_pct']}% | "
                f"OPPORTUNITY {corner_text}. "
                "Do not volunteer gaps ahead/behind; use best-lap comparisons."
            )

        plan = state.get("strategy", {}).get("recommended", {})
        strategy_clause = ""
        if include_strategy:
            strategy_clause = (
                f" | compound rule {state.get('strategy', {}).get('compound_rule', {})}"
                f" | strategy {plan.get('instruction', 'strategy building')}"
            )
        # An agreed tactical plan travels with every request, not only the one
        # that set it. Outside include_strategy too: the driver expects the
        # overcut they agreed to be honoured whatever they ask about next.
        intent = state.get("strategy_intent", {}) or {}
        if intent.get("active"):
            strategy_clause += (
                f" | AGREED WITH THE DRIVER on lap {intent.get('set_at_lap')}: "
                f"{str(intent.get('intent', '')).replace('_', ' ')}"
                f" ({intent.get('note', '')}). Honour it; if the data now says"
                " otherwise, say so and offer the alternative rather than"
                " silently reverting to the ranked plan."
            )
        temperature_unit = str(state.get("temperature_unit", "c")).lower()
        temperatures, temperature_label = _temperature_values(
            [float(value) for value in state["tyre"]["inner_temps_c"]],
            temperature_unit,
        )
        formatted_temperatures = [round(value, 1) for value in temperatures]
        overtaking_label = (
            "manual override"
            if state.get("regulations_2026")
            else "DRS"
        )
        return (
            f"SESSION {state['session_type']} | {state['track_name']} | "
            f"LAP {state['current_lap']}/{state['total_laps']} | "
            f"P{state['player_position']} | CONNECTED {state['connected']} "
            f"PAUSED {state['game_paused']} | "
            f"{state['tyre']['compound']} age {state['tyre']['age_laps']} "
            f"wear FL/FR/RL/RR {state['tyre']['wear']} | "
            f"inner temps FL/FR/RL/RR {formatted_temperatures} {temperature_label} | "
            f"fuel delta {state['fuel_laps_delta']:+.1f} laps | "
            f"ERS {state['ers_pct']:.0f}% | {overtaking_label} "
            f"{'active' if state.get('overtake_active') else 'inactive'} | "
            f"weather {state['weather']} rain15 {state['rain_next_15_pct']}% | "
            f"race control {state.get('race_control_phase', 'green')} | "
            f"FIA flag {state.get('fia_flag', 'none')} | "
            f"penalties {state.get('penalties_s', 0)}s, drive-through "
            f"{state.get('unserved_drive_through_penalties', 0)}, stop-go "
            f"{state.get('unserved_stop_go_penalties', 0)} | "
            f"target {target.get('target')} | opportunity {corner_text}"
            f"{strategy_clause}"
        )

    async def situation_header(self, *, include_strategy: bool = True) -> str:
        """Public accessor for the live situation summary.

        The speech-to-speech session needs the same grounding header the text
        path builds, so it knows the session, track and car state without
        spending a conversational turn discovering them.
        """
        return await self._header(include_strategy=include_strategy)

    @staticmethod
    def classify_request(utterance: str) -> str:
        """Return fast, normal, or deep for acknowledgement/routing."""
        text = " ".join(utterance.lower().split())
        if any(term in text for term in _DEEP_TERMS) or len(text.split()) >= 32:
            return "deep"

        fast_subjects = (
            "gap ahead",
            "gap behind",
            "what position",
            "my position",
            "what tyres",
            "what tires",
            "tyre condition",
            "tire condition",
            "fuel",
            "battery",
            "ers",
            "manual override",
            "overtake available",
            "last lap",
            "last two laps",
            "last three laps",
            "best lap",
            "target lap",
            "weather",
            "rain",
            "damage",
            "warnings",
            "penalty",
        )
        planning_words = ("should", "why", "how do", "approach", "attack", "defend")
        if any(subject in text for subject in fast_subjects) and not any(
            word in text for word in planning_words
        ):
            return "fast"
        return "normal"

    @staticmethod
    def _format_ms(ms: int) -> str | None:
        if not ms:
            return None
        minutes, remainder = divmod(int(ms), 60_000)
        seconds, millis = divmod(remainder, 1000)
        return f"{minutes}:{seconds:02d}.{millis:03d}"

    @staticmethod
    def _requested_lap_count(text: str) -> int:
        lowered = text.lower()
        if "three" in lowered or re.search(r"\b3\b", lowered):
            return 3
        if "two" in lowered or re.search(r"\b2\b", lowered):
            return 2
        if "five" in lowered or re.search(r"\b5\b", lowered):
            return 5
        return 1

    @staticmethod
    def _normalize_text(value: str) -> str:
        return normalize_text(value)

    @classmethod
    def _is_strategy_request(cls, utterance: str) -> bool:
        text = cls._normalize_text(utterance)
        direct = has_any_phrase(
            text,
            (
                "strategy", "pit plan", "tyre plan", "tire plan", "box lap",
                "when should i box", "should i pit", "pit now", "stay out",
                "undercut", "overcut", "one stop", "one-stop", "two stop",
                "two-stop", "race strategy", "strategy update",
            ),
        )
        challenged_call = has_any_phrase(
            text, ("are you sure", "does not make sense", "doesn't make sense", "why box")
        ) and has_any_phrase(
            text, ("box", "boxing", "pit", "pitting", "soft", "medium", "hard", "stay out")
        )
        return direct or challenged_call

    @classmethod
    def _is_cars_ahead_request(cls, utterance: str) -> bool:
        text = cls._normalize_text(utterance)
        return has_any_phrase(
            text,
            (
                "cars ahead", "cars in front", "car ahead", "car in front",
                "closing in", "closing into", "am i closing", "what about the cars ahead",
                "update on the cars ahead", "updates for the cars in front",
            ),
        )

    # Phrasings that mean "stop bringing this up", each paired with the subject
    # the driver wants dropped.
    # A driver telling the engineer to drop a subject does not use a fixed form.
    # Recorded sessions contain "do not tell me *anything* about engine damage",
    # "shut *the fuck* up about engine and gear damage" and "stop *annoying me*
    # about my gearbox" — all of which a fixed phrase list missed, so the
    # engineer kept reporting the very thing it had been told to drop. The cue
    # is matched with a small amount of filler tolerated between its words.
    _SUPPRESSION_CUE = re.compile(
        r"(?:"
        r"shut\s+(?:\w+\s+){0,3}?up"
        r"|(?:stop|quit|cease)\s+(?:\w+\s+){0,3}?"
        r"(?:telling|talking|mentioning|annoying|bugging|bothering|nagging|going|bringing|reminding|updating|warning)"
        # normalize_text lowercases and strips accents but keeps punctuation, so
        # the contraction arrives as "don't". Matching on a separator class
        # covers "do not", "don't", "don t" and "dont" with one branch.
        r"|(?:do[^a-z0-9]+not|don[^a-z0-9]*t)\s+(?:\w+\s+){0,3}?"
        r"(?:tell|telling|talk|mention|remind|want|need|give|say)"
        r"|no\s+(?:more|further)"
        r"|enough"
        r"|stop\s+it\s+with"
        r")",
        re.IGNORECASE,
    )
    # The subject follows a preposition; leading determiners are dropped so the
    # stored rule reads as a topic rather than a fragment of the sentence.
    _SUPPRESSION_SUBJECT = re.compile(
        r"\b(?:about|regarding|concerning|on\s+the\s+subject\s+of|with)\s+"
        r"(?:my\s+|the\s+|any\s+|all\s+|these\s+|those\s+|further\s+|more\s+)*"
        r"(?P<subject>[a-z0-9][a-z0-9\s]*)",
        re.IGNORECASE,
    )

    @classmethod
    def _standing_instruction(cls, utterance: str) -> str | None:
        """Extract a lasting instruction such as "stop telling me about damage".

        These were previously acknowledged and immediately forgotten, so the
        recorded sessions show the same request being made twice in a row and
        the engineer answering with the very report it was asked to drop.

        Matching goes through ``has_phrase`` like every other predicate here.
        A raw substring test could never match the contracted "don't tell me
        about…", because normalisation keeps the apostrophe — and that is the
        form drivers actually use.
        """
        text = cls._normalize_text(utterance)
        cue = cls._SUPPRESSION_CUE.search(text)
        if not cue:
            return None
        # The subject must come after the cue: "stop telling me about X", never
        # a stray "about" earlier in the sentence.
        match = cls._SUPPRESSION_SUBJECT.search(text, cue.end())
        subject = match.group("subject").strip() if match else ""
        if not subject:
            return None
        subject = " ".join(subject.split()[:6])
        return (
            f"Do not raise {subject} unless the driver asks or it is safety-critical."
        )

    @staticmethod
    def suppressed_subjects(standing_instructions: list[Any] | None) -> list[str]:
        """Subjects the driver has asked the engineer to stop raising.

        Returned as plain topic text so non-model code — in particular the
        unsolicited-radio queue — can honour the instruction too. Persona text
        alone only ever influenced wording, which is why the gearbox calls kept
        arriving after the engineer had agreed to stop making them.
        """
        subjects: list[str] = []
        for item in standing_instructions or []:
            rule = (
                item.get("rule", "")
                if isinstance(item, dict)
                else item
                if isinstance(item, str)
                else ""
            )
            match = re.match(r"do not raise (.+?) unless", str(rule), re.IGNORECASE)
            if match:
                subjects.append(match.group(1).strip().lower())
        return subjects

    @classmethod
    def _requests_short_answers(cls, utterance: str) -> bool:
        text = cls._normalize_text(utterance)
        return has_any_phrase(
            text,
            (
                "keep your answers short", "keep answers short", "short answers",
                "be brief", "keep it brief", "less talking", "too long",
                "stop going on", "very annoying",
            ),
        )

    @classmethod
    def _manual_session_override_mode(cls, utterance: str) -> str | None:
        text = cls._normalize_text(utterance)
        if has_any_phrase(text, ("auto detect session", "automatic session", "clear session override")):
            return "auto"
        # Require a declarative/command phrase. A generic request such as
        # "race updates" must never alter the session classifier.
        declarations = ("this is", "we are in", "we're in", "set session to", "override session to")
        if not any(has_phrase(text, phrase) for phrase in declarations):
            return None
        # A denial is not a declaration. "This is not qualifying" previously
        # locked the session profile *to* qualifying, taking the two-compound
        # rule and pit-loss model with it until the driver cleared it by hand.
        if has_negation(text):
            return None
        if has_phrase(text, "time trial"):
            return "time_trial"
        if has_any_phrase(text, ("qualifying", "quali", "shootout")):
            return "qualifying"
        if has_any_phrase(text, ("practice", "free practice")):
            return "practice"
        if has_phrase(text, "sprint"):
            return "sprint"
        if has_phrase(text, "race"):
            return "race"
        return None

    @classmethod
    def _strategy_override_action(cls, utterance: str, current_lap: int) -> dict[str, Any] | None:
        text = cls._normalize_text(utterance)
        if has_any_phrase(
            text,
            (
                "clear strategy override", "cancel strategy override", "unlock strategy",
                "use best strategy", "choose the best strategy", "strategy back to auto",
            ),
        ):
            return {"clear": True}

        # A refusal is not a plan. "I'm not boxing", "I will not take another
        # pit stop" and "I'm not going for mediums" previously produced a locked
        # pit call for the very compound and lap the driver had ruled out, which
        # the driver then had to argue with for several laps.
        if has_negation(text):
            return None

        compounds = extract_compounds(text)
        lap = extract_lap(text, current_lap)
        is_start_plan = has_any_phrase(
            text,
            ("starting on", "start on", "first tyre", "first tire", "race plan is"),
        )
        is_commitment = has_any_phrase(
            text,
            (
                "i am going to", "i'm going to", "we are going to", "we're going to",
                "i will take", "i'll take", "put me on", "box lap", "box this lap",
                "commit to", "stick with", "lock strategy",
            ),
        )
        stop_preference = None
        if has_any_phrase(text, ("one stop", "one-stop")):
            stop_preference = 1
        elif has_any_phrase(text, ("two stop", "two-stop")):
            stop_preference = 2

        if not compounds and stop_preference is None:
            return None
        if not (is_start_plan or is_commitment or stop_preference is not None):
            return None

        action: dict[str, Any] = {
            "clear": False,
            "enabled": True,
            "locked": True,
            "source": "driver_radio",
            "updated_at": time.time(),
        }
        if is_start_plan and compounds:
            action["start_compound"] = compounds[0]
            # Said out loud, so it is a decision rather than the fitted tyre
            # echoed back by a plan. The caller records what was on the car at
            # the time, so a later change in the garage still wins.
            action["start_compound_explicit"] = True
            if len(compounds) > 1:
                action["next_compound"] = compounds[-1]
        elif compounds:
            action["next_compound"] = compounds[-1]
        if lap is not None:
            action["next_box_lap"] = lap
        if stop_preference is not None:
            action["preferred_stops"] = stop_preference
        if has_any_phrase(text, ("safest", "safe strategy", "avoid another stop", "tyre life", "tire life")):
            action["priority"] = "safety"
        elif has_any_phrase(text, ("fastest", "maximum pace", "attack strategy")):
            action["priority"] = "pace"
        return action

    # Tactical plans a driver states in words rather than as a lap and a
    # compound. Each maps to what it means for the next stop.
    _STRATEGY_INTENTS = (
        ("overcut", "stay_out", ("overcut", "over cut", "over-cut")),
        (
            "go_long",
            "stay_out",
            (
                "go long", "going long", "extend the stint", "extend this stint",
                "stay out longer", "stretch the stint", "stretch this stint",
                "run longer", "longer stint",
            ),
        ),
        ("undercut", "box_early", ("undercut", "under cut", "under-cut")),
    )

    @classmethod
    def _strategy_intent(cls, utterance: str, current_lap: int) -> dict[str, Any] | None:
        """Capture a tactical plan agreed out loud, e.g. "let's overcut them".

        From a real race: "I feel that we should be trying to do an overcut
        rather than taking a pit stop right now" was acknowledged — "we'll run
        the overcut" — and then two laps later the engineer called "Box lap 12
        for mediums" as though the conversation had never happened. The
        agreement existed only in the radio log, which nothing downstream
        reads. Recording it in state is what makes it binding.
        """
        text = cls._normalize_text(utterance)
        for intent, direction, phrases in cls._STRATEGY_INTENTS:
            if not has_any_phrase(text, phrases):
                continue
            # Only a decision counts. "What is the undercut worth?" is a
            # question about one, and "the undercut is not on" rejects it.
            if re.match(
                r"^(?:how|what|when|where|why|would|could|can|is|are|do|does|should)\b",
                text,
            ):
                return None
            if has_negation(text) and not has_any_phrase(
                text, ("rather than", "instead of", "not taking", "not boxing")
            ):
                return None
            return {
                "intent": intent,
                "direction": direction,
                "set_at_lap": int(current_lap),
                "note": utterance.strip(),
                "set_at": time.time(),
                "active": True,
            }
        return None

    @classmethod
    def _preference_updates(cls, utterance: str) -> dict[str, Any]:
        text = cls._normalize_text(utterance)
        updates: dict[str, Any] = {}
        if has_any_phrase(text, ("more rear stability", "prioritize rear stability", "stable rear")):
            updates["rear_stability"] = 2
            updates["setup_bias"] = "rear_stability"
        if has_any_phrase(text, ("more traction", "prioritize traction", "better traction")):
            updates["traction"] = 2
            updates["setup_bias"] = "traction"
        if has_any_phrase(text, ("more rotation", "prioritize rotation", "sharper turn in")):
            updates["rotation"] = 2
            updates["setup_bias"] = "rotation"
        if has_any_phrase(text, ("protect the tyres", "protect the tires", "prioritize tyre life", "prioritize tire life")):
            updates["tyre_life"] = 2
            updates["strategy_priority"] = "safety"
        if has_any_phrase(text, ("straight line speed", "lower drag", "prioritize top speed")):
            updates["straight_line"] = 2
            updates["setup_bias"] = "straight_line"
        if has_any_phrase(
            text,
            ("conservative strategy", "conservative risk", "play it safe on strategy"),
        ):
            updates["strategy_risk_appetite"] = "conservative"
        elif has_any_phrase(
            text,
            ("aggressive strategy", "aggressive risk", "gamble on strategy"),
        ):
            updates["strategy_risk_appetite"] = "aggressive"
        elif has_any_phrase(
            text,
            ("balanced strategy risk", "balanced risk", "normal strategy risk"),
        ):
            updates["strategy_risk_appetite"] = "balanced"
        return updates

    @classmethod
    def _strategy_refusal(cls, utterance: str) -> bool:
        text = cls._normalize_text(utterance)
        # A sentence that opens as a question is never a refusal, whatever
        # phrases it contains: "should we stay out" is asking, not deciding.
        if re.match(
            r"^(?:how|what|when|where|why|would|could|can|is|are|do|does|should)\b",
            text,
        ):
            return False
        # An explicit first-person refusal decides the matter even when the
        # driver also editorialises. In a real race, "No, I am not boxing, it
        # does not make sense" was classified as a question because the hedge
        # check ran first — so no hold was set and the very next reply was the
        # same pit call again, which the driver heard as being ignored.
        explicit = has_any_phrase(
            text,
            (
                "not boxing", "not pitting", "will not box", "won t box",
                "will not pit", "won t pit", "do not box", "don t box",
                "do not pit", "don t pit", "staying out", "stay out",
                "no pit stop", "no more stops", "not in the mood for a box",
            ),
        )
        if explicit:
            return True
        if has_any_phrase(
            text,
            (
                "should i", "should we", "why not", "are we not", "do you want",
                "does it mean", "does that mean", "are you sure",
                "does not make sense", "doesn t make sense", "makes no sense",
            ),
        ):
            return False
        # ``has_negation`` deliberately treats the verb "stop" as a general
        # cancellation signal.  In strategy language, however, "one-stop" and
        # "pit stop" are nouns, so refusal detection must use an explicit
        # negation set instead of that broad helper.
        actual_negation = has_any_phrase(
            text,
            (
                "not", "dont", "don t", "do not", "wont", "won t",
                "will not", "never", "no longer", "without", "avoid",
                "skip", "cancel", "forget", "disregard", "ignore",
            ),
        )
        return actual_negation and has_any_phrase(
            text, ("box", "boxing", "pit", "pitting", "stop")
        )

    @classmethod
    def _driver_position_claim(cls, utterance: str) -> int | None:
        """Extract an explicit position report without trusting it blindly."""
        text = cls._normalize_text(utterance)
        numeric = re.search(
            r"\b(?:i am|i m|im|we are|we re)\s+(?:currently\s+)?(?:in\s+|running\s+)?p?([1-9]|1[0-9]|2[0-4])\b",
            text,
        )
        if numeric:
            return int(numeric.group(1))
        words = {"first": 1, "second": 2, "third": 3}
        declarative = has_any_phrase(text, ("i am", "i m", "im", "we are", "we re", "currently on"))
        if declarative:
            for word, position in words.items():
                if has_phrase(text, word):
                    return position
        return None

    @staticmethod
    def _strategy_signature(recommended: dict[str, Any]) -> str:
        return ":".join(
            str(value or "")
            for value in (
                recommended.get("box_lap"),
                str(recommended.get("fit_compound") or "").upper(),
                recommended.get("stops_remaining"),
            )
        )

    # A stop is pointless if the driver is already on that compound and it is
    # barely used. Anything at or below this age counts as "just fitted".
    _JUST_FITTED_LAPS = 2

    @staticmethod
    def _spoken_strategy_instruction(
        recommended: dict[str, Any],
        current_lap: int,
        tyre: dict[str, Any] | None = None,
    ) -> str:
        box_lap = recommended.get("box_lap")
        compound = recommended.get("fit_compound")
        if box_lap is None or not compound:
            return str(recommended.get("instruction") or "Stay out to the finish.")
        # Never voice a stop the model itself says is not worth making.
        #
        # From a real race, lap 33 of 37, the driver running P7 on mediums at
        # roughly half their wear limit. The payload behind the call read
        # positions_gained_vs_stay_out 0, positions_lost_by_stopping 6,
        # projected_finish_position 18 — and the call was "Box this lap for
        # softs." He boxed, rejoined P20 and finished P20, last of the
        # classified runners.
        #
        # Nothing here second-guesses the strategy engine: it is the engine's
        # own arithmetic. The defect was that the recommendation reached speech
        # without anyone reading it. When the numbers say a stop gains nothing
        # and costs places, say that instead — the driver can still choose to
        # box, but not because they were told to.
        gained = recommended.get("positions_gained_vs_stay_out")
        lost = recommended.get("positions_lost_by_stopping")
        try:
            if gained is not None and lost is not None and int(gained) <= 0 < int(lost):
                places = int(lost)
                return (
                    f"Stay out — boxing costs you {places} "
                    f"place{'s' if places != 1 else ''} and gains nothing."
                )
        except (TypeError, ValueError):
            pass

        box_lap = int(box_lap)
        if box_lap <= current_lap:
            # Never tell a driver to pit for the tyre they are already on and
            # have only just fitted. In a real race this was said on lap 33 of
            # 37, one lap after a stop, while the car was on softs at age 0 —
            # and in the same lap the engineer also said "you're on fresh
            # softs, stay out". Being told to throw away a new set, twice,
            # contradicting itself, is worse than saying nothing at all.
            current = str((tyre or {}).get("compound") or "").upper()
            age = int((tyre or {}).get("age_laps") or (tyre or {}).get("age") or 0)
            if (
                current
                and current == str(compound).upper()
                and age <= EngineerBrain._JUST_FITTED_LAPS
            ):
                return "Stay out — you're on fresh " + str(compound).lower() + "s."
            return f"Box this lap for {str(compound).lower()}s."
        return f"Box lap {box_lap} for {str(compound).lower()}s."

    @classmethod
    def _drivers_named_in_request(
        cls, state: dict[str, Any], utterance: str
    ) -> list[dict[str, Any]]:
        """Drivers named anywhere in the request, best match first.

        Delegates to the participant identity table so a first name ("Max"),
        nickname ("Checo") or car number resolves, not only the packet surname.
        """
        return match_drivers(state.get("drivers", []), utterance)

    @staticmethod
    def _format_cars_ahead_report(report: dict[str, Any], *, closing_only: bool) -> str:
        cars = report.get("cars", [])
        if not report.get("available") or not cars:
            return "No reliable live gap data for the cars ahead."
        if closing_only:
            car = cars[0]
            driver = car.get("driver", "Car ahead")
            gap = float(car.get("gap_s", 0.0))
            trend = car.get("trend")
            change = car.get("gap_change_s")
            window = car.get("window_s")
            if trend == "closing":
                return f"You are closing on {driver}: {gap:.1f} seconds, gained {abs(float(change)):.1f} over {float(window):.0f} seconds."
            if trend == "falling_back":
                return f"You are not closing on {driver}: {gap:.1f} seconds, lost {abs(float(change)):.1f} over {float(window):.0f} seconds."
            if trend == "steady":
                return f"The gap to {driver} is steady at {gap:.1f} seconds."
            pace = car.get("pace_delta_s")
            if isinstance(pace, (int, float)):
                if pace > 0.05:
                    return f"No measured gap trend yet. {driver} is {gap:.1f} seconds ahead; your latest lap was {pace:.1f} slower."
                if pace < -0.05:
                    return f"No measured gap trend yet. {driver} is {gap:.1f} seconds ahead; your latest lap was {abs(pace):.1f} faster."
            return f"{driver} is {gap:.1f} seconds ahead; not enough gap history to call the trend."
        entries: list[str] = []
        for car in cars[:4]:
            trend = car.get("trend")
            suffix = ""
            if trend == "closing":
                suffix = ", closing"
            elif trend == "falling_back":
                suffix = ", pulling away"
            elif trend == "steady":
                suffix = ", steady"
            entries.append(
                f"{car.get('driver', 'Unknown')} {float(car.get('gap_s', 0.0)):.1f}s{suffix}"
            )
        return "Ahead: " + "; ".join(entries) + "."

    @classmethod
    def _handles_named_rival(cls, text: str) -> bool:
        """Whether a fast-path branch actually understands a named rival.

        Only two do: the sector-loss comparison and the rival lap-history
        report. Every other branch reads the player's own car, so it must not
        be allowed to answer a question about somebody else.
        """
        sector_loss = has_any_phrase(
            text,
            (
                "where am i losing time", "where do i lose time",
                "losing time compared", "losing time towards",
                "sector comparison", "compare sectors",
            ),
        )
        lap_history = has_any_phrase(text, ("lap", "laps")) and has_any_phrase(
            text, ("last", "recent", "update", "times")
        )
        return sector_loss or lap_history

    @staticmethod
    def _has_specific_deterministic_answer(text: str) -> bool:
        """Advisory-sounding questions the fast path answers precisely.

        Each of these maps to a branch that gives a concrete, checked answer:
        the ranked pit plan, the correction that no overtaking-aid tyre
        temperature target exists, and the differential/brake-bias change for a
        loose rear. Handing them to a model would replace a specific answer with
        a general one.
        """
        asks_for_the_pit_call = has_any_phrase(
            text,
            (
                "should i pit", "should i box", "should we pit", "should we box",
                "when should i box", "when should i pit", "when do i box",
                "pit now", "box now", "stay out",
            ),
        )
        asks_aid_temperature = (
            has_any_phrase(text, ("drs", "manual override", "overtake"))
            and has_any_phrase(text, ("temperature", "temp", "tyre", "tire"))
            and has_any_phrase(text, ("target", "optimize", "optimise", "best"))
        )
        asks_rear_balance = has_any_phrase(
            text, ("differential", "diff setting", "brake bias")
        ) and has_any_phrase(
            text,
            (
                "rear sliding", "rear slide", "rear standing", "oversteer",
                "wheelspin", "traction",
            ),
        )
        return asks_for_the_pit_call or asks_aid_temperature or asks_rear_balance

    @classmethod
    def _defers_to_model(cls, state: dict[str, Any], utterance: str) -> bool:
        """Whether the deterministic path must stand aside for this request.

        The fast path is a set of keyword branches over the player's own car. It
        matches on single words, so it answered questions it could not possibly
        understand: "does Max have any damage on his car" returned the player's
        damage, and a nuanced undercut question returned the standing pit call.
        Deferring costs one model call; answering wrongly costs the driver's
        trust in every later answer.
        """
        text = cls._normalize_text(utterance)

        # A named rival: only the branches that resolve a driver may answer.
        if match_drivers(state.get("drivers", []), utterance):
            return not cls._handles_named_rival(text)

        # Someone else's car, even unnamed. A possessive alone is not enough:
        # "the leader's battery" and "how's the fuel for the guy ahead" carry no
        # driver name, and every fast-path branch below reads the player's car,
        # so they were answered with the driver's own fuel and energy.
        refers_to_another_car = has_any_phrase(
            text,
            (
                "his car", "her car", "their car", "his tyres", "his tires",
                "their tyres", "their tires", "his damage", "his battery",
                "his fuel", "his pace", "the other car", "everyone else",
                "the field", "who has stopped", "who has pitted", "who retired",
                "rest of the field", "other drivers", "anyone else",
                "the leader", "leader s", "the guy ahead", "the guy behind",
                "the car ahead", "the car in front", "the car behind",
                "driver ahead", "driver behind", "car ahead has", "car behind has",
            ),
        )
        # ... but only when the question is about that car's condition or plan.
        # A plain "gap to the car ahead" is still a correct fast-path answer.
        asks_about_their_state = has_any_phrase(
            text,
            (
                "tyre", "tyres", "tire", "tires", "wear", "fuel", "battery",
                "ers", "energy", "damage", "stops", "stopped", "pitted", "pit",
                "boxed", "compound", "strategy", "temperature", "temps", "age",
                "how old", "condition", "retired",
            ),
        )
        if refers_to_another_car and asks_about_their_state:
            return True

        # Challenging the current pit call is answered deterministically, with
        # the projected wear, evidence source and confidence behind it. The
        # negation in "that does not make sense" disputes the engineer's call;
        # it is not the driver refusing to act, so it is checked first.
        challenges_the_call = has_any_phrase(
            text,
            (
                "are you sure", "does not make sense", "doesn t make sense",
                "makes no sense", "why box", "why stay out", "why this strategy",
            ),
        )
        if challenges_the_call and cls._is_strategy_request(utterance):
            return False

        # A correction, refusal or withdrawal needs to be understood, not
        # pattern-matched into the plan it is rejecting.
        if has_negation(text) and has_any_phrase(
            text,
            (
                "box", "boxing", "pit", "pitting", "stop", "strategy", "tyre",
                "tire", "soft", "medium", "hard", "inter", "wet", "mean", "talking about",
            ),
        ):
            return True

        # A verdict on the plan — for or against — is a conversational turn,
        # not a request for the standing call. Both really happened, one minute
        # apart: "That strategy works." was answered with a fresh pit
        # instruction, and "I feel that that strategy is absolutely stupid."
        # was answered with the same call it was rejecting. Repeating the call
        # at a driver who is reacting to it is what reads as being ignored;
        # the model sees the radio history and can answer the reaction itself.
        if has_any_phrase(
            text,
            (
                "that works", "works for me", "sounds good", "sounds great",
                "i agree", "agreed", "good plan", "great plan", "good call",
                "happy with that", "let s do that", "let s do it",
                "go with that", "strategy works", "plan works", "love it",
            ),
        ):
            return True
        if has_any_phrase(
            text,
            (
                "stupid", "ridiculous", "terrible", "awful", "rubbish",
                "nonsense", "absurd", "insane", "crazy", "dumb", "idiotic",
                "bad idea", "bad call", "worst", "hate", "not happy",
                "unhappy", "disagree",
            ),
        ):
            return True

        # Anything that asks what to *do* is advice, not a lookup. The fast path
        # below matches single keywords anywhere in the sentence, so "what should
        # be my target lap time to reach a points-paying position" hit the bare
        # "position" branch and came back as "Timing currently reports P14" —
        # four times in a row, to four different questions.
        # ...unless the fast path has a specific, better answer for that exact
        # question. These are advisory in form but deterministic in substance:
        # the ranked pit plan, a factual correction, and a concrete setting
        # change. Deferring them would trade a precise answer for a vaguer one.
        if cls._has_specific_deterministic_answer(text):
            return False
        if has_any_phrase(
            text,
            (
                "what should", "should i", "should we", "what can i", "what can we",
                "how do i", "how can i", "how should i", "how do we", "how can we",
                "what do i need", "what do we need", "is there anything",
                "anything i can", "anything we can", "anything else i",
                "any advice", "advise", "recommend", "recommendation",
                "suggest", "any tips", "help me", "what would you do",
                "in order to", "so that i", "so i can", "if i want",
                "how to", "best way", "what next", "improve",
            ),
        ):
            return True

        # A hypothetical or comparative strategy question ("how slow would a
        # one-stop to hards on lap 13 be", "is there any way we could undercut
        # him") is a simulation request, not a request for the standing call.
        # Answering it with the current recommendation is what produced the
        # recorded "No change — Box lap 8 for hards" replies.
        if has_any_phrase(
            text,
            (
                "how slow", "how quick", "how fast", "how much would", "how long would",
                "would be", "would it", "what would", "is there any way",
                "could we", "can we", "how do we", "what about",
            ),
        ):
            return True

        # A request for the WHOLE picture goes to the model. In a real race
        # "could you please give me a rundown of the overall strategy" and
        # "can you run down of the pit strategy" were both answered with a
        # bare "Box this lap for softs." — technically the standing call,
        # nothing like what was asked.
        if has_any_phrase(
            text,
            (
                "rundown", "run down", "run me down", "overall", "full",
                "complete", "entire", "whole", "walk me through",
                "talk me through", "big picture",
            ),
        ) and cls._is_strategy_request(utterance):
            return True

        # A plain strategy request stays on the deterministic ranked plan.
        if cls._is_strategy_request(utterance):
            return False


        # A comparison or an open "how is it going" needs analysis, not a
        # lookup. These used to be answered with a lap time or a list of gaps,
        # which puts the actual comparison back on a driver at speed.
        if has_any_phrase(
            text,
            (
                # Comparative questions. The generic "am I closing on the cars
                # ahead" is deliberately absent: the cars-ahead report already
                # answers it with a measured trend and a verdict, and routing it
                # to the model would lose that guarantee.
                "compared to", "comparison", "versus", "against",
                "faster than", "quicker than", "slower than",
                "how am i doing", "how are we doing", "how is my pace",
                "how s my pace", "can i catch", "will i catch",
                "do i have a chance", "am i winning",
                # Open questions that deserve a picture, not a status dump.
                "how is the race", "how is it going", "how are we looking",
                "where do i stand", "what is the picture", "how does it look",
            ),
        ):
            return True

        # Everything else that asks for reasoning rather than a value.
        return has_any_phrase(
            text,
            (
                "why", "how come", "explain", "what if", "compare", "difference",
                "instead", "rather than", "does that mean", "are you sure",
                "makes no sense", "does not make sense", "doesn t make sense",
                "answer my question", "you said", "i told you", "i asked",
                "the reason", "identify", "help me understand", "what might be",
            ),
        )

    async def _fast_answer(self, utterance: str) -> str | None:
        """Answer operational radio requests from state before consulting a model.

        Only unambiguous lookups about the player's own car are handled here.
        Anything about another driver, any correction, and anything that needs
        an explanation is handed to the model, which has the field-wide tools.
        """
        text = self._normalize_text(utterance)
        state = await self.store.snapshot_analysis()

        session_override = self._manual_session_override_mode(utterance)
        if session_override is not None:
            if session_override == "auto":
                label = str(state.get("raw_session_type", state.get("session_type", "Unknown")))
                profile = str(state.get("mode_profile", "idle"))
                source = "udp"
                reason = "Manual override cleared; the next Session packet is authoritative."
            else:
                mapping = {
                    "race": ("Race", "race"),
                    "sprint": ("Sprint", "sprint"),
                    "qualifying": ("Qualifying", "qualifying"),
                    "practice": ("Practice", "practice"),
                    "time_trial": ("Time Trial", "time_trial"),
                }
                label, profile = mapping[session_override]
                source = "manual"
                reason = f"Driver radio override selected {label}."
            await self.store.update(
                session_mode_override=session_override,
                session_type=label,
                mode_profile=profile,
                session_detection_source=source,
                session_detection_reason=reason,
            )
            await self.tools.strategy.recompute()
            if session_override == "auto":
                return f"Session override cleared; detection now says {label}."
            asks_start_tyre = has_any_phrase(
                text,
                ("first tyre", "first tire", "start tyre", "start tire", "race start tyre", "race start tire"),
            )
            if profile in {"race", "sprint"} and asks_start_tyre:
                rain = int(state.get("rain_next_15_pct", 0) or 0)
                total_laps = int(state.get("total_laps", 0) or 0)
                prefs = state.get("driver_preferences", {}) or {}
                priority = str(prefs.get("strategy_priority", "balanced")).lower()
                if rain >= 60:
                    tyre_call = "Start on intermediates."
                elif priority in {"tyre_life", "safety", "one_stop"}:
                    tyre_call = "Start on hards for tyre-life priority."
                elif 0 < total_laps <= 15:
                    tyre_call = "Start on softs for the short race."
                else:
                    tyre_call = "Start on mediums as the balanced dry default."
                qualifier = " I’ll validate the stop lap when race distance, grid and live conditions arrive." if not state.get("connected") or total_laps <= 0 else ""
                return f"Session locked to {label}. {tyre_call}{qualifier}"
            return f"Session locked to {label}. I’ll use {profile.replace('_', ' ')} logic until you clear it."

        # A pre-race plan discussion, if one is open, owns strategy talk until
        # it is settled. It has to come before the single-stop override below:
        # "make it a one-stop" during a plan discussion means reshape the whole
        # race, not pin the next stop, and the narrow override would take it
        # first and silently answer the smaller question.
        planner = PreRacePlanner(self.store, self.tools.strategy)
        plan_turn = await planner.try_respond(utterance)
        if plan_turn is not None:
            return str(plan_turn.get("spoken") or "")

        override_action = self._strategy_override_action(
            utterance, int(state.get("current_lap", 0))
        )
        if override_action is not None:
            current = dict(state.get("strategy_override", {}))
            if override_action.get("clear"):
                current.update(
                    enabled=False, locked=False, start_compound=None,
                    start_compound_explicit=False, start_compound_seen_fitted="",
                    plan={}, plan_agreed=False,
                    next_box_lap=None, next_compound=None, preferred_stops=None,
                    source="driver_radio", note="cleared by driver", updated_at=time.time(),
                )
                await self.store.update(strategy_override=current)
                await self.tools.strategy.recompute()
                return "Strategy override cleared; automatic ranking restored."
            current.update({key: value for key, value in override_action.items() if key != "clear"})
            if override_action.get("start_compound_explicit"):
                current["start_compound_seen_fitted"] = str(
                    state.get("tyre", {}).get("compound", "") or ""
                ).upper()
            current["note"] = utterance
            await self.store.update(strategy_override=current)
            await self.tools.strategy.recompute()
            parts: list[str] = []
            if current.get("start_compound"):
                parts.append(f"start on {str(current['start_compound']).lower()}s")
            if current.get("next_box_lap") and current.get("next_compound"):
                parts.append(
                    f"box lap {int(current['next_box_lap'])} for {str(current['next_compound']).lower()}s"
                )
            elif current.get("next_compound"):
                parts.append(f"take {str(current['next_compound']).lower()}s next")
            if current.get("preferred_stops") is not None:
                parts.append(f"{int(current['preferred_stops'])}-stop priority")
            return "Driver strategy locked: " + ", then ".join(parts) + "."

        # A named tactic outranks refusal phrasing: "we're doing the overcut
        # — I'm staying out" is the tactic, and the tactic branch sets the
        # same hold. Checking refusal first filed exactly that utterance as a
        # bare refusal, and the AGREED chip never appeared.
        intent = self._strategy_intent(utterance, int(state.get("current_lap", 0) or 0))
        if intent is not None:
            await self.store.update(strategy_intent=intent)
            if intent["direction"] == "stay_out":
                # Agreeing to overcut IS declining the next stop. Without this
                # the engineer said "we'll run the overcut" and then called
                # "Box lap 12" two laps later.
                current_lap = int(state.get("current_lap", 0) or 0)
                hold = dict(state.get("strategy_hold", {}) or {})
                hold.update(
                    {
                        "active": True,
                        "until_lap": current_lap + 5,
                        "reason": f"agreed {intent['intent'].replace('_', ' ')}: {utterance.strip()}",
                        "set_at": time.time(),
                        "set_at_lap": current_lap,
                    }
                )
                hold.setdefault("baseline", {})
                hold.setdefault("change_reason", "")
                hold.setdefault("raised_after_release", False)
                await self.store.update(strategy_hold=hold)
            # The model acknowledges in the driver's own words; the state
            # mutation above is what makes it binding on later calls.
            return None

        if self._strategy_refusal(utterance):
            current_lap = int(state.get("current_lap", 0) or 0)
            damage = state.get("damage", {}) or {}
            hold = {
                "active": True,
                "until_lap": current_lap + 5,
                "reason": utterance,
                "set_at": time.time(),
                "set_at_lap": current_lap,
                "baseline": {
                    "race_control_phase": state.get("race_control_phase", "green"),
                    "wet": int(state.get("rain_next_15_pct", 0) or 0) >= 55,
                    "damage": {
                        key: damage.get(key, 0)
                        for key in (
                            "front_left_wing", "front_right_wing", "rear_wing",
                            "floor", "diffuser", "sidepod", "gearbox", "engine",
                            "drs_fault", "ers_fault",
                        )
                    },
                    "max_wear_pct": max(
                        [
                            float(value)
                            for value in state.get("tyre", {}).get("wear", [0])
                        ]
                        or [0.0]
                    ),
                    "compound": state.get("tyre", {}).get("compound"),
                },
                "change_reason": "",
                "raised_after_release": False,
            }
            await self.store.update(strategy_hold=hold)
            # Let the language route acknowledge the driver's exact wording;
            # the deterministic state mutation above is the safety invariant.
            return None

        preference_updates = self._preference_updates(utterance)
        if preference_updates:
            preferences = dict(state.get("driver_preferences", {}))
            preferences.update(preference_updates)
            update_values: dict[str, Any] = {"driver_preferences": preferences}
            if "strategy_risk_appetite" in preference_updates:
                update_values["strategy_risk_appetite"] = preference_updates[
                    "strategy_risk_appetite"
                ]
            await self.store.update(**update_values)
            await self.database.save_preference("driver_preferences", preferences)
            return "Driver preference saved; future setup and strategy recommendations will use it."

        if self._requests_short_answers(utterance):
            await self.store.update(radio_verbosity="terse")
            return "Short answers confirmed."

        instruction = self._standing_instruction(utterance)
        if instruction:
            standing = [
                item
                for item in list(state.get("standing_instructions", []))
                if item.get("rule") != instruction
            ]
            standing.append({"rule": instruction, "added_at": time.time()})
            await self.store.update(standing_instructions=standing[-8:])
            await self.database.save_preference("standing_instructions", standing[-8:])
            return "Copy, understood. I won't bring that up again."

        # Explicit driver commands are handled above, because they are exact by
        # construction. Everything past this point is keyword lookup over the
        # player's own car, so anything about a rival, any correction and any
        # request for reasoning goes to the model instead.
        if self._defers_to_model(state, utterance):
            return None

        # Historical and pre-session planning remain available while live UDP is absent.
        asks_hard_history = (
            has_phrase(text, "hard")
            and has_any_phrase(text, ("practice run", "practice runs", "previous run", "what do we know"))
        )
        if asks_hard_history:
            summary = await self.database.compound_run_summary(
                int(state.get("track_id", -1)), "HARD", mode_profile="practice"
            )
            if not summary.get("available"):
                return "No valid hard-tyre practice sample is stored for this track yet."
            return (
                f"Hard practice sample: {summary['laps']} laps, median {summary['median_lap']}, "
                f"maximum wear {float(summary['max_wear_per_lap_pct']):.1f} percent per lap; "
                f"confidence {summary['confidence']}."
            )

        telemetry_stale = bool(not state.get("connected") and not state.get("game_paused"))
        if telemetry_stale and has_any_phrase(text, ("radio check", "can you hear me")):
            lap = int(state.get("current_lap", 0) or 0)
            suffix = f" Last confirmed lap {lap}." if lap > 0 else ""
            return "Radio check, loud and clear. Live telemetry is not connected yet." + suffix

        asks_temperature_unit = has_any_phrase(
            text, ("fahrenheit", "celsius", "tire temperatures", "tyre temperatures")
        )
        if asks_temperature_unit:
            temps_c = [float(v) for v in state.get("tyre", {}).get("inner_temps_c", [0, 0, 0, 0])]
            temps, label = _temperature_values(temps_c, str(state.get("temperature_unit", "c")))
            if len(temps) >= 4:
                return f"Tyres: front {temps[0]:.0f}/{temps[1]:.0f} {label}; rear {temps[2]:.0f}/{temps[3]:.0f} {label}."

        asks_aid_temperature = (
            has_any_phrase(text, ("drs", "manual override", "overtake"))
            and has_any_phrase(text, ("temperature", "temp", "tyre", "tire"))
            and has_any_phrase(text, ("target", "optimize", "optimise", "best"))
        )
        if asks_aid_temperature:
            return "There is no overtaking-aid tyre-temperature target. Use the normal grip window and avoid wheelspin on exit."

        named = self._drivers_named_in_request(state, utterance)
        asks_sector_loss = bool(named) and has_any_phrase(
            text,
            (
                "where am i losing time", "where do i lose time", "losing time compared",
                "losing time towards", "sector comparison", "compare sectors",
            ),
        )
        if asks_sector_loss:
            comparison = await self.tools.get_rival_sector_comparison(str(named[0].get("name", "")))
            if not comparison.get("available"):
                return str(comparison.get("reason") or "No matched sector comparison is available yet.")
            losses = comparison.get("sector_deltas_s", {})
            ordered = sorted(losses.items(), key=lambda item: float(item[1]), reverse=True)
            positive = [(sector, delta) for sector, delta in ordered if float(delta) > 0.005]
            if not positive:
                return f"Against {comparison['driver']}, you did not lose time in any measured sector on the matched lap."
            details = ", ".join(f"{sector.upper()} {float(delta):.3f}" for sector, delta in positive)
            return f"Against {comparison['driver']}, losses were {details}; biggest loss {positive[0][0].upper()}."

        # A request for the car ahead's lap *time* must not fall through to the
        # cars-ahead gap list, which is what "lap last time of the car in front"
        # previously returned.
        asks_rival_lap_time = (
            has_any_phrase(text, ("lap time", "last time", "laptime", "last lap"))
            and has_any_phrase(
                text, ("car ahead", "car in front", "the car", "driver ahead")
            )
        )
        if asks_rival_lap_time or has_any_phrase(
            text,
            ("lap time with the car", "lap time of the car", "compare lap with the car"),
        ):
            report = await self.tools.get_cars_ahead_progress(1)
            cars = report.get("cars", [])
            if cars:
                car = cars[0]
                player_lap = car.get("player_last_lap") or "unavailable"
                rival_lap = car.get("last_lap") or "unavailable"
                return f"You {player_lap}; {car.get('driver', 'car ahead')} {rival_lap}, gap {float(car.get('gap_s', 0.0)):.1f} seconds."
            return "No reliable lap comparison for the car ahead."

        if self._is_cars_ahead_request(utterance):
            report = await self.tools.get_cars_ahead_progress(4)
            closing_only = has_any_phrase(text, ("closing", "catching", "gaining"))
            answer = self._format_cars_ahead_report(report, closing_only=closing_only)
            return ("Last confirmed: " + answer) if telemetry_stale else answer

        asks_driver_laps = bool(named) and has_any_phrase(text, ("lap", "laps")) and has_any_phrase(
            text, ("last", "recent", "update", "times")
        )
        if asks_driver_laps:
            count = self._requested_lap_count(text)
            reports: list[str] = []
            for driver in named[:3]:
                name = str(driver.get("name", "Unknown"))
                history = await self.tools.get_driver_lap_history(name, count)
                laps = [str(item.get("lap")) for item in history.get("laps", []) if item.get("lap")]
                if laps:
                    reports.append(f"{name}: " + ", ".join(laps))
                else:
                    latest = self._format_ms(int(driver.get("last_lap_ms", 0) or 0))
                    reports.append(
                        f"{name}: lap history unavailable; latest {latest}"
                        if latest else f"{name}: lap history unavailable"
                    )
            return "; ".join(reports) + "."

        if has_any_phrase(text, ("restart update", "restart grid", "update the restart grid")):
            grid = list(state.get("restart_grid", []) or [])
            provisional = False
            if not grid:
                grid = sorted(
                    (
                        {
                            "car_idx": int(driver.get("car_idx", -1)),
                            "position": int(driver.get("position", 0)),
                            "name": str(driver.get("name", "Unknown")),
                        }
                        for driver in state.get("drivers", [])
                        if int(driver.get("position", 0)) > 0
                        and str(driver.get("name", "Unknown")) != "Unknown"
                    ),
                    key=lambda item: int(item["position"]),
                )
                provisional = bool(grid)
            if not grid:
                status = "No restart marker is present" if not state.get("red_flag_active") else "Restart is pending"
                return status + ", and the timing order is not complete yet."
            player_index = int(state.get("player_car_index", -1))
            player = next((item for item in grid if int(item.get("car_idx", -2)) == player_index), None)
            if not player:
                return "Restart grid is stored, but your car is missing from the timing feed."
            position = int(player.get("position", 0))
            ahead = next((item for item in grid if int(item.get("position", 0)) == position - 1), None)
            behind = next((item for item in grid if int(item.get("position", 0)) == position + 1), None)
            neighbours = []
            if ahead:
                neighbours.append(f"{ahead.get('name')} ahead")
            if behind:
                neighbours.append(f"{behind.get('name')} behind")
            label = "Provisional timing order" if provisional and not state.get("red_flag_active") else "Restart grid"
            return f"{label}: P{position}" + (", " + ", ".join(neighbours) if neighbours else "") + "."

        claimed_position = self._driver_position_claim(utterance)
        if claimed_position is not None:
            timing_position = int(state.get("player_position", 0) or 0)
            if timing_position <= 0 or telemetry_stale:
                return f"Copy, provisional P{claimed_position}; timing is not reliable enough to confirm it yet."
            if timing_position == claimed_position:
                return f"Copy, P{claimed_position} confirmed by timing."
            return (
                f"Your report is P{claimed_position}, while timing shows P{timing_position}. "
                "I’m flagging a timing conflict rather than overriding your call."
            )

        if has_any_phrase(text, ("race update", "race updates", "any updates")):
            events = state.get("events_log", [])[-8:]
            material = [event for event in events if str(event.get("type", "")).upper() in {"SSTA", "SEND", "PENA", "RTMT", "DRSE", "RDFL", "OVTK"}]
            position = int(state.get("player_position", 0))
            report = await self.tools.get_cars_ahead_progress(1)
            nearest = report.get("cars", [])
            gap_text = f"; {nearest[0]['driver']} {float(nearest[0]['gap_s']):.1f} ahead" if nearest else ""
            event_text = f"; latest event {material[-1].get('description', material[-1].get('type'))}" if material else "; no new incident or penalty"
            prefix = f"Last confirmed lap {int(state.get('current_lap', 0))}: " if telemetry_stale else ""
            return prefix + f"P{position}{gap_text}{event_text}; rain risk {int(state.get('rain_next_15_pct', 0))} percent."

        if self._is_strategy_request(utterance):
            strategy = state.get("strategy", {})
            recommended = strategy.get("recommended", {})
            if telemetry_stale and not recommended:
                return "Telemetry is stale; I cannot safely issue a new pit call until the feed reconnects."
            if not recommended:
                strategy = await self.tools.get_pit_strategy()
                recommended = strategy.get("recommended", {})
            if not recommended:
                return "Strategy is still building."
            active_override = state.get("strategy_override", {})
            signature = self._strategy_signature(recommended)
            previous = str(state.get("strategy_spoken_signature", ""))
            await self.store.update(strategy_spoken_signature=signature)
            instruction = self._spoken_strategy_instruction(
                recommended,
                int(state.get("current_lap", 0)),
                state.get("tyre") or {},
            )
            prefix = "Driver override active — " if active_override.get("enabled") else ""
            if telemetry_stale:
                # On the grid, in the strategy screens and in the pause menu
                # the game trickles packets slowly enough to trip the live
                # disconnect rule while still clearly present. "Telemetry
                # stale" there sounded like a fault; the plan is simply the
                # last confirmed one.
                if state.get("game_presence") == "standing_by":
                    prefix = "From the last green-flag data: " + prefix
                else:
                    prefix = "Telemetry stale — last confirmed: " + prefix
            challenged = has_any_phrase(
                text,
                ("are you sure", "does not make sense", "doesn't make sense", "why this strategy", "why box", "why stay out"),
            )
            if challenged:
                reason = str(recommended.get("tyre_reason") or recommended.get("stop_required_reason") or "")
                confidence = str(strategy.get("confidence") or "low")
                override_meta = recommended.get("driver_override") or {}
                warning = str(override_meta.get("warning") or "")
                detail = warning or reason
                if detail:
                    return f"{prefix}{instruction} {detail} Confidence {confidence}."
                return f"{prefix}{instruction} Confidence {confidence}; the call is provisional."
            return prefix + (f"No change — {instruction}" if previous == signature else instruction)

        asks_balance = has_any_phrase(text, ("differential", "diff setting", "brake bias"))
        rear_problem = has_any_phrase(text, ("rear sliding", "rear slide", "rear standing", "oversteer", "wheelspin", "traction"))
        if asks_balance and rear_problem:
            setup = state.get("car_setup", {}) or {}
            on_throttle = setup.get("on_throttle") or setup.get("on_throttle_diff") or setup.get("on_throttle_differential")
            target = max(0, int(on_throttle) - 3) if isinstance(on_throttle, (int, float)) else None
            first = f"Lower on-throttle differential from {int(on_throttle)} to {target}." if target is not None else "Lower on-throttle differential a few clicks."
            return first + " Move brake bias one point forward only if the rear moves under braking."

        if has_phrase(text, "radio check"):
            lap = int(state.get("current_lap", 0))
            position = int(state.get("player_position", 0))
            return f"Radio check, loud and clear. Lap {lap}, P{position}."

        if has_phrase(text, "time check"):
            lap = int(state.get("current_lap", 0))
            position = int(state.get("player_position", 0))
            last = self._format_ms(int(state.get("last_lap_ms", 0)))
            return f"Lap {lap}, P{position}." + (f" Last lap {last}." if last else "")

        if state.get("mode_profile") == "qualifying" and has_any_phrase(text, ("best lap", "target lap", "pole")):
            summary = self.tools.qualifying_summary(state, 3)
            field = summary.get("field", [])
            leaders = ", ".join(f"{item.get('name')} {item.get('best_lap')}" for item in field if item.get("best_lap"))
            target = summary.get("target") or "still building"
            player = summary.get("player_best") or "no valid lap yet"
            return f"Best times: {leaders or 'not available yet'}. Your best {player}; target {target}."

        if has_any_phrase(text, ("gap ahead", "car ahead")):
            result = await self.tools.get_gap("ahead")
            if result.get("available"):
                return f"{result['driver']} ahead, {float(result['gap_s']):.1f} seconds. Last lap {result.get('last_lap') or 'unavailable'}."
            return "No reliable gap to the car ahead right now."

        if has_any_phrase(text, ("gap behind", "car behind")):
            result = await self.tools.get_gap("behind")
            if result.get("available"):
                return f"{result['driver']} behind, {float(result['gap_s']):.1f} seconds. Last lap {result.get('last_lap') or 'unavailable'}."
            return "No reliable gap to the car behind right now."

        # Each of these requires the question to be *about* the subject. A bare
        # keyword match anywhere in the sentence turned every question that
        # happened to contain "position", "fuel" or "damage" into a readout of
        # that one value, whatever was actually asked.
        if has_any_phrase(
            text,
            (
                "what position", "which position", "my position", "current position",
                "what place", "where am i running", "where do i sit", "position now",
                "am i in", "what p am i",
            ),
        ):
            return f"Timing currently reports P{int(state.get('player_position', 0))}."

        if has_any_phrase(text, ("what tyres", "what tires", "tyre condition", "tire condition")):
            tyre = state.get("tyre", {})
            wear = [float(value) for value in tyre.get("wear", [0, 0, 0, 0])]
            temps_c = [float(value) for value in tyre.get("inner_temps_c", [0, 0, 0, 0])]
            temps, temperature_label = _temperature_values(temps_c, str(state.get("temperature_unit", "c")))
            limiting = max(range(len(wear)), key=wear.__getitem__) if wear else 0
            labels = ["front-left", "front-right", "rear-left", "rear-right"]
            temp_text = f" Front cores {temps[0]:.0f}/{temps[1]:.0f} {temperature_label}." if len(temps) >= 2 else ""
            return f"{tyre.get('compound', 'Unknown').title()}s, {int(tyre.get('age_laps', 0))} laps old. Limiting tyre is {labels[limiting]} at {wear[limiting]:.0f} percent.{temp_text}"

        if has_phrase(text, "fuel") and has_any_phrase(
            text,
            ("how much", "how is", "how s", "what is", "what s", "status",
             "left", "remaining", "delta", "margin", "ok", "okay", "fine", "level"),
        ):
            delta = float(state.get("fuel_laps_delta", 0.0))
            direction = "plus" if delta >= 0 else "minus"
            return f"Fuel is {direction} {abs(delta):.1f} laps."

        # Complete-token matching is essential: 'ers' must not match Verstappen.
        if has_any_phrase(text, ("battery", "ers", "manual override", "overtake available")):
            percent = float(state.get("ers_pct", 0.0))
            if state.get("regulations_2026"):
                status = "active" if state.get("overtake_active") else ("available" if state.get("overtake_available") else "not available")
                return f"Battery {percent:.0f} percent; Manual Override is {status}."
            return f"ERS {percent:.0f} percent; DRS is {'available' if state.get('drs_allowed') else 'not available'}."

        if has_any_phrase(text, ("last lap", "last two laps", "last three laps")):
            count = self._requested_lap_count(text)
            history = await self.tools.get_driver_lap_history("me", count)
            laps = [item.get("lap") for item in history.get("laps", []) if item.get("lap")]
            if laps:
                return f"Your last {len(laps)} lap{'s' if len(laps) != 1 else ''}: " + ", ".join(laps) + "."
            last = self._format_ms(int(state.get("last_lap_ms", 0)))
            return f"Last lap {last}." if last else "No completed valid lap is available yet."

        if has_phrase(text, "target lap"):
            target = state.get("analysis", {}).get("target", {})
            value = target.get("target") or target.get("target_lap")
            return f"Target lap is {value}." if value else "Target lap is still building."

        if has_any_phrase(text, ("weather", "rain")) and has_any_phrase(
            text,
            ("what is", "what s", "how is", "how s", "forecast", "any", "chance",
             "risk", "coming", "expected", "update", "look"),
        ):
            return f"{state.get('weather', 'Unknown')}; rain risk {int(state.get('rain_next_15_pct', 0))} percent in 15 minutes."

        if has_phrase(text, "damage") and has_any_phrase(
            text,
            ("what is", "what s", "how is", "how s", "how much", "any", "status",
             "report", "check", "assess", "level", "bad"),
        ):
            damage = state.get("damage", {})
            wing = max(int(damage.get("front_left_wing", 0)), int(damage.get("front_right_wing", 0)))
            floor = int(damage.get("floor", 0))
            return f"Front-wing damage {wing} percent; floor damage {floor} percent."

        if has_any_phrase(
            text, ("warning", "warnings", "penalty", "penalties")
        ) and has_any_phrase(
            text,
            ("how many", "what is", "what s", "any", "status", "do i have",
             "count", "report", "check"),
        ):
            warnings = int(state.get("corner_cutting_warnings", 0))
            penalties = int(state.get("penalties_s", 0))
            return f"Track-limit warnings {warnings}; time penalties {penalties} seconds."

        return None

    async def _capture_feedback(self, utterance: str) -> None:
        lowered = utterance.lower()
        state = await self.store.snapshot_analysis()
        closed_answer = normalize_text(utterance)
        if closed_answer in {"holding", "they're holding", "they re holding", "the rears are holding"}:
            lowered = "rears are holding"
        elif closed_answer in {"going away", "they're going away", "they re going away", "the rears are going away"}:
            lowered = "starting to go"
        for category, patterns in _FEEDBACK_PATTERNS.items():
            if any(pattern in lowered for pattern in patterns):
                await self.store.add_feedback(category, utterance)
                if category in {"tyres_gone", "tyres_going", "tyres_fine"}:
                    await self.store.update(
                        driver_tyre_feedback={
                            "lap": int(state.get("current_lap", 0) or 0),
                            "category": category,
                            "confidence": 1.0,
                            "text": utterance,
                            "created_at": time.time(),
                        }
                    )
                await self.database.add_feedback(
                    int(state.get("session_uid", 0)),
                    int(state.get("track_id", -1)),
                    category,
                    utterance,
                )
                break

    async def _run(
        self,
        prompt: str,
        effort: str,
        instructions: str,
        max_rounds: int = 3,
        route: str = "normal",
        provider: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        enforce_radio_limit: bool = True,
    ) -> str:
        prompt = prompt.strip()
        if not prompt:
            raise RuntimeError("The engineer request contained no user prompt.")

        try:
            result = await self.router.generate(
                prompt=prompt,
                instructions=instructions,
                route=route,
                effort=effort,
                tools=(
                    self.tools.schemas_for_route(route) if tools is None else tools
                ),
                execute_tool=self.tools.call,
                max_rounds=max_rounds,
                provider=provider,
            )
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            await self.store.update(llm_last_error=message)
            raise
        if enforce_radio_limit:
            live_state = await self.store.snapshot_analysis()
            result.text = _limit_radio_sentences(
                result.text, route, str(live_state.get("radio_verbosity", settings.radio_verbosity))
            )
        self.last_provider_result = result
        await self.store.update(
            llm_provider=result.provider,
            llm_model=result.model,
            llm_last_latency_ms=round(result.latency_ms, 1),
            llm_last_tool_rounds=result.tool_rounds,
            llm_last_error="",
        )
        return result.text

    async def narrate_briefing(self, kind: str, payload: dict[str, Any]) -> str:
        """Narrate frozen deterministic facts without giving the model tools."""
        state = await self.store.snapshot_analysis()
        is_pre = kind == "pre_session"
        specialist = PRE_SESSION_PERSONA if is_pre else DEBRIEF_PERSONA
        prompt = (
            f"BRIEFING KIND: {kind}\n"
            "NARRATE ONLY THIS DETERMINISTIC PAYLOAD; DO NOT COMPUTE OR INFER NEW FACTS:\n"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )
        return await self._run(
            prompt,
            settings.deep_reasoning_effort,
            compose_persona(
                f"{PERSONA}\n\n{specialist}",
                str(state.get("radio_verbosity", "standard")),
                state.get("standing_instructions", []),
            ),
            max_rounds=1,
            route="deep",
            tools=[],
            enforce_radio_limit=False,
        )

    async def compare(self, utterance: str) -> dict[str, Any]:
        """Run an opt-in, non-spoken A/B comparison on one frozen context."""
        state = await self.store.snapshot_analysis()
        include_strategy = self._is_strategy_request(utterance)
        recent = state["radio_log"][-4:]
        history = "\n".join(
            f"{entry['role'].upper()}: {entry['text']}" for entry in recent
            if not _contains_deliberation_for_history(str(entry.get("text", "")))
            and (include_strategy or not self._is_strategy_request(str(entry.get("text", ""))))
        )
        prompt = (
            f"{await self._header(include_strategy=include_strategy)}\n"
            "REQUEST FOCUS: answer only the latest driver request. Strategy is omitted unless requested.\n"
            f"RECENT RADIO:\n{history}\n"
            f"DRIVER: {utterance}"
        )
        route = self.classify_request(utterance)
        effort = (
            settings.deep_reasoning_effort
            if route == "deep"
            else settings.reasoning_effort
        )
        return await self.router.compare(
            prompt=prompt,
            instructions=compose_persona(PERSONA),
            route=route,
            effort=effort,
            tools=self.tools.schemas(),
            execute_tool=self.tools.call,
            max_rounds=4 if route == "deep" else 3,
        )

    async def ask(self, utterance: str) -> str:
        await self._capture_feedback(utterance)
        lowered = utterance.lower()
        if "fahrenheit" in lowered or re.search(r"(?:°|degrees?\s*)f\b", lowered):
            await self.store.update(temperature_unit="f")
        elif "celsius" in lowered or re.search(r"(?:°|degrees?\s*)c\b", lowered):
            await self.store.update(temperature_unit="c")
        if self._requests_short_answers(utterance):
            await self.store.update(radio_verbosity="terse")

        await self.store.append_radio("driver", utterance)
        state = await self.store.snapshot_analysis()
        await self.database.save_radio_message(state, "driver", utterance, "user")

        route = self.classify_request(utterance)
        direct = await self._fast_answer(utterance)
        if direct:
            direct = _limit_radio_sentences(
                direct, route, str((await self.store.snapshot_analysis()).get("radio_verbosity", "standard"))
            )
            await self.store.update(
                llm_provider="local",
                llm_model="deterministic-radio",
                llm_last_latency_ms=0.0,
                llm_last_tool_rounds=0,
                llm_last_error="",
            )
            await self.store.append_radio("engineer", direct)
            await self.database.save_radio_message(
                await self.store.snapshot_analysis(), "engineer", direct, "deterministic_response"
            )
            return direct

        state = await self.store.snapshot_analysis()
        include_strategy = self._is_strategy_request(utterance)
        # The conversation is kept intact. Previously any earlier turn that
        # looked like a strategy request was deleted from the history whenever
        # the current turn was not one, so a follow-up such as "please answer my
        # question" arrived with no question attached and the engineer replied
        # that it had not come through. Only leaked model deliberation is
        # filtered, because that is not something the driver said or heard.
        recent = state["radio_log"][-9:]
        history = "\n".join(
            f"{entry['role'].upper()}: {entry['text']}"
            for entry in recent[:-1]
            if not _contains_deliberation_for_history(str(entry.get("text", "")))
        )
        prompt = (
            f"{await self._header(include_strategy=include_strategy)}\n"
            "REQUEST FOCUS: answer the latest driver request, using the earlier radio only "
            "as context for what it refers to. Do not append an unrelated pit call. "
            "If the driver is correcting or refusing something, accept it and confirm briefly.\n"
            f"RECENT RADIO:\n{history}\n"
            f"DRIVER: {utterance}"
        )
        effort = settings.deep_reasoning_effort if route == "deep" else settings.reasoning_effort
        text = await self._run(
            prompt,
            effort,
            compose_persona(
                PERSONA,
                str(state.get("radio_verbosity", "standard")),
                state.get("standing_instructions", []),
            ),
            max_rounds=4 if route == "deep" else 3,
            route=route,
        )
        await self.store.append_radio("engineer", text)
        provider_name = self.last_provider_result.provider if self.last_provider_result is not None else "unknown"
        await self.database.save_radio_message(
            await self.store.snapshot_analysis(), "engineer", text, f"response:{provider_name}"
        )
        return text

    async def proactive(self, event: dict[str, Any]) -> str:
        state = await self.store.snapshot_analysis()
        recent_engineer = [
            entry["text"]
            for entry in state.get("radio_log", [])[-8:]
            if entry.get("role") == "engineer"
        ]
        strategy_events = {
            "strategy_change", "race_control", "tyre_wear",
            "compound_requirement", "undercut_threat",
        }
        prompt = (
            f"{await self._header(include_strategy=event.get('type') in strategy_events)}\n"
            f"PROACTIVE EVENT: {json.dumps(event, ensure_ascii=False)}\n"
            f"RECENT ENGINEER CALLS: {json.dumps(recent_engineer, ensure_ascii=False)}"
        )
        text = await self._run(
            prompt,
            settings.reasoning_effort,
            compose_persona(
                f"{PERSONA}\n\n{PROACTIVE_PERSONA}",
                str(state.get("radio_verbosity", "standard")),
                state.get("standing_instructions", []),
            ),
            max_rounds=1,
            route="normal",
            # No tools: the event payload already carries every number that may
            # be spoken, and narration must not go looking for new ones.
            tools=[],
        )
        # Deliberately not recorded here. An unsolicited call is only part of the
        # conversation once it has actually been spoken, and delivery can still
        # fail or be cancelled after this returns. Recording it eagerly put lines
        # the driver never heard into the radio log, which is then replayed to
        # the model as context — so it would believe it had already made a call
        # it never made. ProactiveEngineer records it after speak_text succeeds.
        return text

    async def record_spoken_call(self, text: str) -> None:
        """Log an unsolicited call that was actually delivered to the driver."""
        cleaned = " ".join((text or "").split())
        if not cleaned:
            return
        await self.store.append_radio("engineer", cleaned)
        await self.database.save_radio_message(
            await self.store.snapshot_analysis(), "engineer", cleaned, "proactive"
        )
