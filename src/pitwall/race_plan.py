"""The driver's own race plan, and how it survives contact with the race.

The override this sits beside constrains exactly one stop: a lap and a compound.
That is enough to say "box lap 18 for hards" and nothing more, so a driver who
wants to commit to a two-stop before the lights go out has no way to say so, and
the engine re-decides the shape of the whole race every lap.

A plan here is the whole race: the compound each stint starts on and the lap each
stop is meant for. The work is in projecting it onto the race as it actually is.
Once a stop has been made the plan's first stop is history, and the ranker only
ever sees the *remaining* race: candidates whose ``compounds[0]`` is the tyre
currently fitted and whose ``box_laps`` are the stops still to come. A plan that
is not re-based onto that view stops matching anything the moment the first stop
happens, and a constraint that silently matches nothing is worse than no
constraint at all - the driver believes they are on their plan while the engine
quietly does something else.
"""

from __future__ import annotations

from typing import Any

DRY_COMPOUNDS = ("SOFT", "MEDIUM", "HARD")
WET_COMPOUNDS = ("INTER", "WET")
KNOWN_COMPOUNDS = frozenset(DRY_COMPOUNDS + WET_COMPOUNDS)

MAX_STOPS = 3
# How far the ranker may slide a planned stop before it counts as a different
# plan. A driver who says "box around lap 18" means the window, not the lap:
# holding out for the exact lap through a safety car or traffic would follow the
# letter of the plan while throwing away the reason for it.
DEFAULT_LAP_TOLERANCE = 2


class PlanError(ValueError):
    """The plan cannot be raced as stated."""


def normalise_plan(raw: Any, total_laps: int = 0) -> dict[str, Any]:
    """Validate a driver plan and put it in the shape the ranker expects.

    Raises ``PlanError`` with a sentence a driver would understand, because
    these come straight back over the radio or into the UI.
    """
    if not isinstance(raw, dict):
        raise PlanError("A plan needs a compound for each stint.")

    compounds = [str(item or "").strip().upper() for item in raw.get("compounds") or []]
    if not compounds:
        raise PlanError("A plan needs a compound for each stint.")
    unknown = [item for item in compounds if item not in KNOWN_COMPOUNDS]
    if unknown:
        raise PlanError(f"{unknown[0].title()} is not a tyre I know.")
    if len(compounds) - 1 > MAX_STOPS:
        raise PlanError(f"{len(compounds) - 1} stops is more than I can plan.")

    box_laps = [int(lap) for lap in raw.get("box_laps") or []]
    stops = len(compounds) - 1
    if len(box_laps) != stops:
        raise PlanError(
            f"{stops} stop{'s' if stops != 1 else ''} needs "
            f"{stops} box lap{'s' if stops != 1 else ''}, not {len(box_laps)}."
        )
    if any(lap < 1 for lap in box_laps):
        raise PlanError("Box laps start at lap 1.")
    if any(later <= earlier for earlier, later in zip(box_laps, box_laps[1:])):
        raise PlanError("Each stop has to come after the one before it.")
    if total_laps and box_laps and box_laps[-1] >= total_laps:
        raise PlanError(f"The race is {total_laps} laps; the last stop is too late.")

    # Refitting the tyre you just took off is never a plan, it is a typo.
    repeated = next(
        (
            item
            for item, following in zip(compounds, compounds[1:])
            if item == following
        ),
        None,
    )
    if repeated:
        raise PlanError(f"Two {repeated.lower()} stints back to back is not a stop.")

    tolerance = raw.get("lap_tolerance", DEFAULT_LAP_TOLERANCE)
    try:
        tolerance = max(0, min(6, int(tolerance)))
    except (TypeError, ValueError):
        tolerance = DEFAULT_LAP_TOLERANCE

    return {
        "compounds": compounds,
        "box_laps": box_laps,
        "stops": stops,
        "lap_tolerance": tolerance,
    }


def compound_rule_ok(plan: dict[str, Any], wet_race: bool = False) -> bool:
    """Whether the plan serves the mandatory dry-compound change.

    A dry race requires two different dry compounds. A plan that cannot satisfy
    that ends in disqualification, which is not a trade-off worth offering.
    """
    if wet_race:
        return True
    compounds = plan.get("compounds") or []
    if any(item in WET_COMPOUNDS for item in compounds):
        return True
    return len({item for item in compounds if item in DRY_COMPOUNDS}) >= 2


def remaining_plan(
    plan: dict[str, Any],
    stops_completed: int,
    current_lap: int = 0,
) -> dict[str, Any]:
    """Re-base a whole-race plan onto the part of the race that is left.

    ``stops_completed`` comes from telemetry, not from the plan: the driver may
    have stopped early, late, or for something the plan never mentioned. What
    survives is the *shape* of what remains - how many stops and on what - which
    is the part the driver actually committed to.

    Stops whose lap has already gone by are dropped too. A plan of laps 18 and 36
    consulted on lap 20 with no stop made has missed the first one; pretending it
    is still ahead would have the ranker chasing a lap that cannot happen.
    """
    stops_completed = max(0, int(stops_completed))
    compounds = list(plan.get("compounds") or [])
    box_laps = list(plan.get("box_laps") or [])
    tolerance = int(plan.get("lap_tolerance", DEFAULT_LAP_TOLERANCE))

    tail_compounds = compounds[stops_completed:]
    tail_laps = box_laps[stops_completed:]

    # Drop stops the race has run past. Judged against the same tolerance the
    # ranker matches on, or a driver one lap late for their own stop would have
    # it deleted from under them by the very rule meant to protect it.
    missed = 0
    while tail_laps and current_lap and tail_laps[0] + tolerance < current_lap:
        tail_laps = tail_laps[1:]
        if len(tail_compounds) > 1:
            tail_compounds = tail_compounds[:1] + tail_compounds[2:]
        missed += 1

    return {
        "compounds": tail_compounds,
        "box_laps": tail_laps,
        "stops": len(tail_laps),
        "lap_tolerance": tolerance,
        "missed_stops": missed,
        # No stops left to make. True for a plan that is finished and for a
        # no-stop plan from the start; in both cases the constraint still says
        # something ("stay out"), so this is for narration, not control flow.
        "exhausted": not tail_laps,
    }


def plan_matches(candidate: dict[str, Any], tail: dict[str, Any]) -> bool:
    """Whether an enumerated plan is the one the driver asked for.

    Matched on the stops still to come and the tyres they fit, not on the tyre
    currently on the car: the candidate's first compound is whatever telemetry
    says is fitted, and the driver does not get to override that.
    """
    wanted_laps = tail.get("box_laps") or []
    wanted_compounds = (tail.get("compounds") or [])[1:]
    if int(candidate.get("stops_remaining", -1)) != len(wanted_laps):
        return False

    fitted = [str(item).upper() for item in (candidate.get("compounds") or [])[1:]]
    if fitted != wanted_compounds:
        return False

    tolerance = int(tail.get("lap_tolerance", DEFAULT_LAP_TOLERANCE))
    actual_laps = [int(lap) for lap in candidate.get("box_laps") or []]
    if len(actual_laps) != len(wanted_laps):
        return False
    return all(
        abs(actual - wanted) <= tolerance
        for actual, wanted in zip(actual_laps, wanted_laps)
    )


def describe_plan(plan: dict[str, Any]) -> str:
    """The plan as an engineer would say it on the radio."""
    compounds = [str(item).lower() for item in plan.get("compounds") or []]
    box_laps = list(plan.get("box_laps") or [])
    if not compounds:
        return "No plan set."
    if not box_laps:
        return f"No stop, {compounds[0]}s to the flag."

    stops = len(box_laps)
    legs = [f"start on {compounds[0]}s"]
    for index, lap in enumerate(box_laps):
        legs.append(f"box lap {lap} for {compounds[index + 1]}s")
    return f"{stops}-stop: " + ", ".join(legs) + "."
