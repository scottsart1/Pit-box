"""The conversation that settles a race strategy before the lights go out.

Mid-race the driver reacts to what the engine says. Before the start it is the
other way round: the engineer proposes, the driver pushes back, and the plan is
only worth anything once both agree. That exchange is what this models.

Three rules shape it.

The engineer proposes from the *shapes* the strategy engine offers - one stop,
two, three - not from a single ranked answer, because "I'd rather one-stop it" is
the first thing most drivers say and there has to be something real to say back.

The driver's turn is parsed deterministically. This module never guesses at an
intent it does not recognise: a misread here commits a whole race to a strategy
nobody asked for, so an unrecognised sentence is answered with what *is*
understood rather than with a plan. The LLM narrates; it does not decide.

Nothing constrains the race until the driver agrees. The proposal lives here, in
``prerace_briefing``, and only ``commit`` writes it into ``strategy_override``,
so a discussion that is abandoned - or never finished before the lights - leaves
the engine running automatically, exactly as if it had never happened.
"""

from __future__ import annotations

import time
from typing import Any

from .intent import (
    extract_compounds,
    extract_lap,
    has_any_phrase,
    has_negation,
    normalize_text,
)
from .race_plan import (
    PlanError,
    compound_rule_ok,
    describe_plan,
    normalise_plan,
)

# How far "earlier" and "later" move a stop. Big enough to be worth saying, small
# enough that a driver can say it twice rather than overshooting in one go.
LAP_NUDGE = 3

ACCEPT_PHRASES = (
    "yes", "yeah", "yep", "agreed", "agree", "confirm", "confirmed", "copy",
    "copy that", "lock it in", "lock it", "lock that in", "do it", "go with that",
    "go with it", "happy with that", "sounds good", "that works", "perfect",
    "run it", "commit", "let s do it", "lets do it", "im happy", "i m happy",
)

REJECT_PHRASES = (
    "no", "nope", "negative", "i dont like", "i don t like", "dont like",
    "not happy", "something else", "another option", "alternative",
)

QUESTION_PHRASES = (
    "why", "what if", "what does that cost", "how much", "what about",
    "explain", "talk me through", "what are my options", "options",
)

EARLIER_PHRASES = ("earlier", "sooner", "bring it forward", "before that", "undercut")
LATER_PHRASES = ("later", "extend", "go longer", "stay out longer", "push it back", "overcut")

STOP_WORDS = {
    0: ("no stop", "zero stop", "no stops", "without stopping"),
    1: ("one stop", "one stopper", "1 stop", "single stop"),
    2: ("two stop", "two stopper", "2 stop"),
    3: ("three stop", "three stopper", "3 stop"),
}


def _requested_stops(text: str) -> int | None:
    for stops, phrases in STOP_WORDS.items():
        if has_any_phrase(text, phrases):
            return stops
    return None


class PreRacePlanner:
    """Owns the pre-race plan discussion and the state it lives in."""

    def __init__(self, store: Any, strategy: Any) -> None:
        self.store = store
        self.strategy = strategy

    # -- reading -----------------------------------------------------------

    async def snapshot(self) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        return dict(state.get("prerace_briefing", {}) or {})

    @staticmethod
    def is_prerace(state: dict[str, Any]) -> bool:
        """Whether the session is a race that has not started running yet.

        Deliberately generous about *when* planning is allowed. The session type
        comes from the game's own enum and is correct from the moment the driver
        loads into the session, so the only real question is whether racing has
        begun - and a driver still setting a plan on lap 1 out of 57 is planning,
        not second-guessing a race in progress.
        """
        if str(state.get("mode_profile", "")) not in {"race", "sprint"}:
            return False
        if int(state.get("total_laps", 0) or 0) <= 0:
            return False
        return int(state.get("current_lap", 0) or 0) <= 1

    # -- proposing ---------------------------------------------------------

    async def propose(self) -> dict[str, Any]:
        """Open the discussion with a strategy and the alternatives to it."""
        state = await self.store.snapshot_analysis()

        # Wait for the tyre on the car before planning a race around it. The
        # first frames of a session carry the race distance but not yet the
        # compound, and a proposal built then starts on "UNKNOWN" - which also
        # poisons the compound rule, because UNKNOWN counts as no dry tyre used
        # and the only *legal* one-stop becomes a switch to intermediates in a
        # dry race. Proposing early does not just look wrong, it advises wrong.
        fitted = str(state.get("tyre", {}).get("compound", "") or "").upper()
        if fitted in {"", "UNKNOWN"}:
            return await self._store_briefing(
                phase="idle",
                proposal={},
                alternatives=[],
                rationale="",
                spoken="Waiting to see which tyre you are on.",
            )

        plan = await self.strategy.get_plan()

        if not plan.get("available"):
            return await self._store_briefing(
                phase="idle",
                proposal={},
                alternatives=[],
                rationale=plan.get("reason", "No race strategy applies yet."),
                spoken=plan.get(
                    "reason", "There is no race distance to plan against yet."
                ),
            )

        shapes = list(plan.get("shapes", []) or [])
        preferred = next((shape for shape in shapes if shape.get("feasible")), None)
        if preferred is None:
            preferred = shapes[0] if shapes else None
        if preferred is None:
            return await self._store_briefing(
                phase="idle",
                proposal={},
                alternatives=[],
                rationale="No legal race plan is available.",
                spoken="I cannot find a legal plan for this race yet.",
            )

        proposal = self._shape_to_plan(preferred, state)
        rationale = self._rationale(preferred, shapes, plan)
        return await self._store_briefing(
            phase="proposed",
            proposal=proposal,
            alternatives=shapes,
            rationale=rationale,
            spoken=f"{describe_plan(proposal)} {rationale} Happy with that?",
        )

    @staticmethod
    def _shape_to_plan(shape: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        """Turn a ranked shape into an editable plan.

        The engine's compound list starts with the tyre already fitted, which on
        the grid is the tyre the race starts on, so it carries over directly.
        """
        compounds = [str(item).upper() for item in shape.get("compounds", [])]
        box_laps = [int(lap) for lap in shape.get("box_laps", [])]
        try:
            return normalise_plan(
                {"compounds": compounds, "box_laps": box_laps},
                total_laps=int(state.get("total_laps", 0) or 0),
            )
        except PlanError:
            # A shape the validator rejects is a modelling artefact, not
            # something to show a driver. Fall back to the raw values so the
            # panel still renders and the driver can correct it by hand.
            return {
                "compounds": compounds,
                "box_laps": box_laps,
                "stops": len(box_laps),
                "lap_tolerance": 2,
            }

    @staticmethod
    def _rationale(
        preferred: dict[str, Any],
        shapes: list[dict[str, Any]],
        plan: dict[str, Any],
    ) -> str:
        parts: list[str] = []
        others = [
            shape
            for shape in shapes
            if shape is not preferred and shape.get("stops") != preferred.get("stops")
        ]
        runner_up = next(
            (shape for shape in others if shape.get("feasible")), None
        )
        if runner_up and runner_up.get("cost_vs_best_s") is not None:
            parts.append(
                f"A {runner_up['stops']}-stop costs about "
                f"{abs(float(runner_up['cost_vs_best_s'])):.0f} seconds."
            )
        blocked = next((shape for shape in others if not shape.get("feasible")), None)
        if blocked:
            parts.append(
                f"A {blocked['stops']}-stop {blocked.get('verdict', 'is not on')}."
            )
        confidence = str(plan.get("confidence", "") or "")
        if confidence:
            parts.append(f"Confidence is {confidence} before any laps are run.")
        return " ".join(parts)

    # -- negotiating -------------------------------------------------------

    async def try_respond(self, utterance: str) -> dict[str, Any] | None:
        """Take a driver turn only if it is plainly about the race plan.

        The panel and the radio need different thresholds. Typing into the plan
        box is unambiguous - whatever the driver wrote, they meant it for the
        plan - so ``respond`` answers everything and asks when it cannot tell.
        Over the radio the same sentence competes with every other thing a
        driver says, so an unrecognised one has to fall through to the engineer
        rather than be answered with "tell me a number of stops".

        Returns the briefing when it took the turn, or None to decline it.
        """
        briefing = await self.snapshot()
        if briefing.get("phase") not in {"proposed", "negotiating"}:
            return None

        state = await self.store.snapshot_analysis()
        text = normalize_text(utterance)
        proposal = dict(briefing.get("proposal", {}) or {})
        alternatives = list(briefing.get("alternatives", []) or [])

        recognised = (
            self._requested_change(text, proposal, alternatives, state) is not None
            or has_any_phrase(text, ACCEPT_PHRASES)
            or has_any_phrase(text, REJECT_PHRASES)
            # A bare "why" mid-discussion is about the plan; the same word in a
            # sentence about anything else is not, so it has to be paired with
            # something plan-shaped to count.
            or (
                has_any_phrase(text, QUESTION_PHRASES)
                and has_any_phrase(text, ("plan", "strategy", "stop", "stops", "tyre", "tire"))
            )
        )
        if not recognised:
            return None
        return await self.respond(utterance)

    async def respond(self, utterance: str) -> dict[str, Any]:
        """Take one driver turn and answer it.

        Returns the updated briefing. ``committed`` is True only when the driver
        agreed and the plan is now constraining the race.
        """
        briefing = await self.snapshot()
        if briefing.get("phase") not in {"proposed", "negotiating"}:
            briefing = await self.propose()
            if briefing.get("phase") != "proposed":
                return briefing

        state = await self.store.snapshot_analysis()
        proposal = dict(briefing.get("proposal", {}) or {})
        alternatives = list(briefing.get("alternatives", []) or [])
        text = normalize_text(utterance)
        total_laps = int(state.get("total_laps", 0) or 0)

        # Agreement first, and only when nothing else was asked for in the same
        # breath. "Yes, but make it a two-stop" is a change, not a commitment.
        change = self._requested_change(text, proposal, alternatives, state)
        if change is None and has_any_phrase(text, ACCEPT_PHRASES) and not has_negation(text):
            return await self.commit(proposal, transcript_turn=utterance)

        if change is not None:
            revised, note = change
            try:
                revised = normalise_plan(revised, total_laps=total_laps)
            except PlanError as exc:
                return await self._append_turn(
                    briefing,
                    utterance,
                    spoken=str(exc),
                    proposal=proposal,
                )
            if not compound_rule_ok(revised, wet_race=self._wet(state)):
                return await self._append_turn(
                    briefing,
                    utterance,
                    spoken=(
                        "That runs one dry compound all race, which is a "
                        "disqualification. Pick a second dry tyre."
                    ),
                    proposal=proposal,
                )
            return await self._append_turn(
                briefing,
                utterance,
                spoken=f"{note} {describe_plan(revised)} Happy with that?",
                proposal=revised,
            )

        if has_any_phrase(text, QUESTION_PHRASES):
            return await self._append_turn(
                briefing,
                utterance,
                spoken=self._options_text(proposal, alternatives, briefing),
                proposal=proposal,
            )

        if has_any_phrase(text, REJECT_PHRASES) or has_negation(text):
            return await self._append_turn(
                briefing,
                utterance,
                spoken=(
                    "Understood. "
                    + self._options_text(proposal, alternatives, briefing)
                ),
                proposal=proposal,
            )

        # Nothing recognised. Say so rather than acting on a guess: the cost of
        # misreading this is a whole race run to a plan nobody chose.
        return await self._append_turn(
            briefing,
            utterance,
            spoken=(
                "I did not catch a change there. Tell me a number of stops, a "
                "tyre, or a lap to box on. "
                f"Right now: {describe_plan(proposal)}"
            ),
            proposal=proposal,
        )

    @staticmethod
    def _repair_adjacent_stints(compounds: list[str], displaced: str) -> list[str]:
        """Make a stint sequence legal again after one slot was changed.

        Walking from the changed end: any stint equal to its predecessor is
        replaced, preferring the compound the driver's change displaced (it
        keeps the plan's original balance), otherwise any dry compound that
        differs from both neighbours. At most one pass is needed because each
        repair introduces a compound that differs from its left neighbour.
        """
        dry = ["SOFT", "MEDIUM", "HARD"]
        repaired = list(compounds)
        for index in range(1, len(repaired)):
            if repaired[index] != repaired[index - 1]:
                continue
            right = repaired[index + 1] if index + 1 < len(repaired) else None
            options = [displaced] + dry
            replacement = next(
                (
                    option
                    for option in options
                    if option and option != repaired[index - 1] and option != right
                ),
                None,
            )
            if replacement is None:
                # Nothing legal fits (e.g. wet compounds involved); leave the
                # sequence for the normal legality check to refuse.
                return repaired
            repaired[index] = replacement
        return repaired

    def _requested_change(
        self,
        text: str,
        proposal: dict[str, Any],
        alternatives: list[dict[str, Any]],
        state: dict[str, Any],
    ) -> tuple[dict[str, Any], str] | None:
        """The concrete edit the driver asked for, or None if they asked for none."""
        compounds = list(proposal.get("compounds") or [])
        box_laps = list(proposal.get("box_laps") or [])
        current_lap = int(state.get("current_lap", 0) or 0)

        stops = _requested_stops(text)
        if stops is not None and stops != len(box_laps):
            shape = next(
                (item for item in alternatives if int(item.get("stops", -1)) == stops),
                None,
            )
            if shape is not None:
                note = (
                    f"{stops}-stop it is."
                    if shape.get("feasible")
                    else f"I can set a {stops}-stop, but {shape.get('verdict')}."
                )
                return (
                    {
                        "compounds": [str(c).upper() for c in shape.get("compounds", [])],
                        "box_laps": [int(lap) for lap in shape.get("box_laps", [])],
                        "lap_tolerance": proposal.get("lap_tolerance", 2),
                    },
                    note,
                )
            return (
                self._rebuild_for_stops(proposal, stops, state),
                f"{stops}-stop it is.",
            )

        requested_lap = extract_lap(text, current_lap)
        if requested_lap is not None and box_laps:
            revised = list(box_laps)
            revised[0] = requested_lap
            return (
                {
                    "compounds": compounds,
                    "box_laps": revised,
                    "lap_tolerance": proposal.get("lap_tolerance", 2),
                },
                f"First stop moved to lap {requested_lap}.",
            )

        if box_laps and has_any_phrase(text, EARLIER_PHRASES):
            revised = [max(1, lap - LAP_NUDGE) for lap in box_laps]
            return (
                {
                    "compounds": compounds,
                    "box_laps": revised,
                    "lap_tolerance": proposal.get("lap_tolerance", 2),
                },
                f"Pulled the stops {LAP_NUDGE} laps earlier.",
            )

        if box_laps and has_any_phrase(text, LATER_PHRASES):
            revised = [lap + LAP_NUDGE for lap in box_laps]
            return (
                {
                    "compounds": compounds,
                    "box_laps": revised,
                    "lap_tolerance": proposal.get("lap_tolerance", 2),
                },
                f"Pushed the stops {LAP_NUDGE} laps later.",
            )

        # A compound change only counts when the driver said where it goes.
        # "The softs are quick here" is an observation, not an instruction.
        spoken_compounds = extract_compounds(text)
        if spoken_compounds and not has_negation(text):
            starting = has_any_phrase(
                text, ("start on", "starting on", "first stint", "off the line", "start the race")
            )
            finishing = has_any_phrase(
                text, ("finish on", "finishing on", "last stint", "final stint", "to the flag", "end on")
            )
            if starting and compounds:
                revised = list(compounds)
                displaced = revised[0]
                revised[0] = spoken_compounds[0]
                # Changing one stint can leave two identical stints touching
                # ("SOFT, SOFT, MEDIUM"), which is not a stop. The driver asked
                # for a start tyre, not to be told their plan is illegal — so
                # the rest of the sequence adapts around their choice instead
                # of the request being refused.
                revised = self._repair_adjacent_stints(revised, displaced)
                return (
                    {
                        "compounds": revised,
                        "box_laps": box_laps,
                        "lap_tolerance": proposal.get("lap_tolerance", 2),
                    },
                    f"Starting on {spoken_compounds[0].lower()}s.",
                )
            if finishing and len(compounds) > 1:
                revised = list(compounds)
                displaced = revised[-1]
                revised[-1] = spoken_compounds[-1]
                revised = list(reversed(
                    self._repair_adjacent_stints(list(reversed(revised)), displaced)
                ))
                return (
                    {
                        "compounds": revised,
                        "box_laps": box_laps,
                        "lap_tolerance": proposal.get("lap_tolerance", 2),
                    },
                    f"Finishing on {spoken_compounds[-1].lower()}s.",
                )
            if len(spoken_compounds) == len(compounds) and len(compounds) > 1:
                # A whole sequence stated at once: "mediums, hards, then softs".
                return (
                    {
                        "compounds": spoken_compounds,
                        "box_laps": box_laps,
                        "lap_tolerance": proposal.get("lap_tolerance", 2),
                    },
                    "Sequence updated.",
                )
        return None

    @staticmethod
    def _rebuild_for_stops(
        proposal: dict[str, Any],
        stops: int,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """Space ``stops`` stops evenly when the engine offered no such shape.

        A crude plan the driver asked for beats refusing them the shape. The
        ranker still has to find a real candidate matching it, and will say so
        if it cannot.
        """
        total_laps = int(state.get("total_laps", 0) or 0) or 50
        current = [str(item).upper() for item in proposal.get("compounds") or ["MEDIUM"]]
        start = current[0] if current else "MEDIUM"
        # Alternate the two compounds that are not the starting tyre, so the
        # mandatory change is served without inventing a preference.
        others = [c for c in ("MEDIUM", "HARD", "SOFT") if c != start]
        compounds = [start]
        for index in range(stops):
            compounds.append(others[index % len(others)])
        box_laps = [
            max(1, round(total_laps * (index + 1) / (stops + 1)))
            for index in range(stops)
        ]
        return {
            "compounds": compounds,
            "box_laps": box_laps,
            "lap_tolerance": proposal.get("lap_tolerance", 2),
        }

    @staticmethod
    def _wet(state: dict[str, Any]) -> bool:
        weather = str(state.get("weather", "") or "").lower()
        return any(word in weather for word in ("rain", "shower", "wet", "storm"))

    def _options_text(
        self,
        proposal: dict[str, Any],
        alternatives: list[dict[str, Any]],
        briefing: dict[str, Any],
    ) -> str:
        lines = [describe_plan(proposal)]
        rationale = str(briefing.get("rationale", "") or "")
        if rationale:
            lines.append(rationale)
        for shape in alternatives:
            if int(shape.get("stops", -1)) == int(proposal.get("stops", -1)):
                continue
            cost = shape.get("cost_vs_best_s")
            if shape.get("feasible") and cost is not None:
                lines.append(
                    f"A {shape['stops']}-stop is about {abs(float(cost)):.0f} "
                    "seconds off."
                )
            else:
                lines.append(f"A {shape['stops']}-stop {shape.get('verdict')}.")
        return " ".join(lines)

    # -- committing --------------------------------------------------------

    async def commit(
        self,
        plan: dict[str, Any] | None = None,
        transcript_turn: str = "",
    ) -> dict[str, Any]:
        """Agree the plan and put it in charge of the race."""
        briefing = await self.snapshot()
        state = await self.store.snapshot_analysis()
        raw = plan if plan is not None else briefing.get("proposal", {})
        try:
            settled = normalise_plan(
                raw, total_laps=int(state.get("total_laps", 0) or 0)
            )
        except PlanError as exc:
            return await self._append_turn(
                briefing, transcript_turn, spoken=str(exc), proposal=dict(raw or {})
            )
        if not compound_rule_ok(settled, wet_race=self._wet(state)):
            return await self._append_turn(
                briefing,
                transcript_turn,
                spoken=(
                    "That runs one dry compound all race, which is a "
                    "disqualification. Pick a second dry tyre."
                ),
                proposal=dict(raw or {}),
            )

        fitted = str(state.get("tyre", {}).get("compound", "") or "").upper()
        chosen = str(settled["compounds"][0]).upper()
        override = dict(state.get("strategy_override", {}) or {})
        override.update(
            {
                "enabled": True,
                "locked": True,
                "plan": settled,
                "plan_agreed": True,
                "next_box_lap": None,
                "next_compound": None,
                "preferred_stops": settled["stops"],
                "start_compound": chosen,
                # Only a start tyre that differs from the one on the car is a
                # decision. Matching it is the default the proposal was built
                # from, and pinning that would stop the engine following the
                # driver into the garage when they change their mind.
                "start_compound_explicit": bool(fitted and chosen != fitted),
                "start_compound_seen_fitted": fitted,
                "note": "agreed before the race",
                "source": "prerace",
                "updated_at": time.time(),
            }
        )
        await self.store.update(strategy_override=override)
        await self.strategy.recompute()
        return await self._store_briefing(
            phase="agreed",
            proposal=settled,
            alternatives=list(briefing.get("alternatives", []) or []),
            rationale=str(briefing.get("rationale", "") or ""),
            spoken=f"Locked in. {describe_plan(settled)}",
            transcript=list(briefing.get("transcript", []) or []),
            transcript_turn=transcript_turn,
            committed=True,
        )

    async def discard(self) -> dict[str, Any]:
        """Abandon the discussion and hand the race back to the engine."""
        state = await self.store.snapshot_analysis()
        override = dict(state.get("strategy_override", {}) or {})
        if str(override.get("source", "")) == "prerace":
            override.update(
                {
                    "enabled": False,
                    "locked": False,
                    "plan": {},
                    "plan_agreed": False,
                    "preferred_stops": None,
                    "start_compound": None,
                    "note": "pre-race plan discarded",
                }
            )
            await self.store.update(strategy_override=override)
            await self.strategy.recompute()
        return await self._store_briefing(
            phase="idle",
            proposal={},
            alternatives=[],
            rationale="",
            spoken="Back to automatic strategy.",
        )

    # -- persistence -------------------------------------------------------

    async def _append_turn(
        self,
        briefing: dict[str, Any],
        utterance: str,
        *,
        spoken: str,
        proposal: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._store_briefing(
            phase="negotiating",
            proposal=proposal,
            alternatives=list(briefing.get("alternatives", []) or []),
            rationale=str(briefing.get("rationale", "") or ""),
            spoken=spoken,
            transcript=list(briefing.get("transcript", []) or []),
            transcript_turn=utterance,
        )

    async def _store_briefing(
        self,
        *,
        phase: str,
        proposal: dict[str, Any],
        alternatives: list[dict[str, Any]],
        rationale: str,
        spoken: str,
        transcript: list[dict[str, Any]] | None = None,
        transcript_turn: str = "",
        committed: bool = False,
    ) -> dict[str, Any]:
        history = list(transcript or [])
        if transcript_turn.strip():
            history.append({"who": "driver", "text": transcript_turn.strip()})
        if spoken:
            history.append({"who": "engineer", "text": spoken})
        briefing = {
            "phase": phase,
            "proposal": proposal,
            "alternatives": alternatives,
            "rationale": rationale,
            "spoken": spoken,
            # Bounded: this is live state pushed over the websocket several times
            # a second, not a permanent record of the conversation.
            "transcript": history[-12:],
            "committed": committed,
            "updated_at": time.time(),
        }
        await self.store.update(prerace_briefing=briefing)
        return briefing
