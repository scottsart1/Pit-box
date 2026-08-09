from __future__ import annotations

import math
import re
import zlib
from statistics import median
from typing import Any

import numpy as np

from .config import settings
from .database import PitWallDatabase
from .race_plan import plan_matches, remaining_plan
from .setup_model import setup_effects
from .state import StateStore

# Green-flag drive-through loss estimates. IDs follow f1-packets 2026 TRACKS.
PIT_LOSS_SECONDS = {
    0: 22.0,  # Melbourne
    3: 23.5,  # Bahrain / Sakhir
    4: 22.0,  # Catalunya
    5: 19.5,  # Monaco
    6: 21.0,  # Montreal
    7: 28.0,  # Silverstone
    9: 20.5,  # Hungaroring
    10: 23.0,  # Spa
    11: 24.0,  # Monza
    12: 28.0,  # Singapore
    13: 23.0,  # Suzuka
    14: 22.0,  # Abu Dhabi
    15: 22.0,  # Texas / COTA
    16: 21.0,  # Brazil
    17: 20.0,  # Austria
    19: 20.5,  # Mexico
    20: 20.5,  # Baku
    26: 19.5,  # Zandvoort
    27: 28.0,  # Imola
    29: 21.5,  # Jeddah
    30: 22.5,  # Miami
    31: 20.5,  # Las Vegas
    32: 23.0,  # Losail
    42: 24.0,  # Madrid
}

# Approximate point beyond which a current-lap stop is no longer realistic.
# This is deliberately conservative and can later be replaced by labelled maps.
PIT_ENTRY_FRACTION = {
    5: 0.965,  # Monaco
    7: 0.955,  # Silverstone
    10: 0.955,  # Spa
    11: 0.965,  # Monza
    12: 0.945,  # Singapore
    13: 0.955,  # Suzuka
    16: 0.965,  # Brazil
    20: 0.955,  # Baku
}

# Track multiplier for cold-start wear/deg defaults before personal evidence exists.
TRACK_TYRE_SEVERITY = {
    0: 1.00,
    3: 1.15,
    4: 1.20,
    5: 0.88,
    6: 0.95,
    7: 1.18,
    9: 1.08,
    10: 1.12,
    11: 0.92,
    12: 1.12,
    13: 1.28,
    14: 0.98,
    15: 1.15,
    16: 1.18,
    17: 1.08,
    19: 1.05,
    20: 0.92,
    26: 1.08,
    27: 1.05,
    29: 1.00,
    30: 1.02,
    31: 0.82,
    32: 1.30,
    42: 1.05,
}

# 0.0 is straightforward to pass, 1.0 is exceptionally difficult. These are
# deliberately broad circuit characteristics, not claims about a particular
# race. Unknown/new layouts fall back to 0.60 and lower projection confidence.
TRACK_OVERTAKING_DIFFICULTY = {
    0: 0.52,   # Melbourne
    3: 0.38,   # Bahrain
    4: 0.62,   # Catalunya
    5: 0.95,   # Monaco
    6: 0.35,   # Montreal
    7: 0.44,   # Silverstone
    9: 0.72,   # Hungaroring
    10: 0.40,  # Spa
    11: 0.25,  # Monza
    12: 0.84,  # Singapore
    13: 0.66,  # Suzuka
    14: 0.48,  # Abu Dhabi
    15: 0.45,  # Texas / COTA
    16: 0.34,  # Brazil
    17: 0.30,  # Austria
    19: 0.46,  # Mexico
    20: 0.30,  # Baku
    26: 0.80,  # Zandvoort
    27: 0.74,  # Imola
    29: 0.34,  # Jeddah
    30: 0.40,  # Miami
    31: 0.22,  # Las Vegas
    32: 0.66,  # Losail
    42: 0.60,  # Madrid; provisional until personal race evidence accumulates
}

COMPOUND_DELTA = {
    "SOFT": -0.55,
    "MEDIUM": 0.0,
    "HARD": 0.65,
    "INTER": 7.0,
    "WET": 12.0,
}

DEFAULT_DEG = {
    "SOFT": 0.16,
    "MEDIUM": 0.09,
    "HARD": 0.055,
    "INTER": 0.11,
    "WET": 0.10,
}

OPERATIONAL_WEAR_LIMIT = {
    "SOFT": 80.0,
    "MEDIUM": 85.0,
    "HARD": 88.0,
    "INTER": 88.0,
    "WET": 90.0,
}

DEFAULT_WEAR_PER_LAP = {
    "SOFT": 4.8,
    "MEDIUM": 3.2,
    "HARD": 2.1,
    "INTER": 3.1,
    "WET": 2.6,
}

DRY_COMPOUNDS = {"SOFT", "MEDIUM", "HARD"}
WET_COMPOUNDS = {"INTER", "WET"}

# Typical usable stint length per compound, derived from the operational wear
# limit and default wear rate. Used only to *estimate* when a rival is likely to
# stop; the game AI may deviate, so anything built on this is labelled an
# estimate.
TYPICAL_STINT_LAPS = {
    compound: max(6, round(OPERATIONAL_WEAR_LIMIT[compound] / DEFAULT_WEAR_PER_LAP[compound]))
    for compound in DEFAULT_WEAR_PER_LAP
}

# F1 points for finishing positions 1..10 (2026 system unchanged from 2010+).
F1_POINTS = {1: 25, 2: 18, 3: 15, 4: 12, 5: 10, 6: 8, 7: 6, 8: 4, 9: 2, 10: 1}


def points_for_position(position: int) -> int:
    return F1_POINTS.get(int(position), 0)


class StrategyEngine:
    """Deterministic, explainable tyre, stop and neutralisation strategy model."""

    def __init__(self, store: StateStore, database: PitWallDatabase) -> None:
        self.store = store
        self.database = database
        # Signature of the last snapshot actually written to SQLite. recompute()
        # runs on the 0.35 s proactive tick, so persisting every result wrote a
        # ~25 KB row several times a second (2 800 rows in one session, 515 MB
        # across the database). Only materially different plans are recorded.
        self._last_snapshot_key: tuple[Any, ...] | None = None

    @staticmethod
    def _valid_laps(state: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            lap
            for lap in state.get("completed_laps", [])
            if lap.get("valid")
            and lap.get("lap_time_ms", 0) > 0
            and not lap.get("pit_status")
        ]

    @staticmethod
    def _estimate_base_lap_s(state: dict[str, Any]) -> float:
        valid = StrategyEngine._valid_laps(state)
        recent = [lap["lap_time_ms"] / 1000 for lap in valid[-4:]]
        if recent:
            return float(median(recent))
        player_idx = int(state.get("player_car_index", 0))
        player = next(
            (
                driver
                for driver in state.get("drivers", [])
                if int(driver.get("car_idx", -1)) == player_idx
            ),
            None,
        )
        if player and player.get("best_lap_ms"):
            return float(player["best_lap_ms"]) / 1000
        return 95.0

    @staticmethod
    def _planned_start_compound(
        state: dict[str, Any],
        fitted: str,
        age: int,
    ) -> str | None:
        """A starting tyre the driver chose that is not yet on the car.

        Deliberately narrow. It applies only before the race is running, on a
        set with no laps on it, and only when the driver actually asked for a
        different compound. Once a lap has been run - or a stop has been made -
        the tyre on the car is a fact, and a plan that argued with it would have
        the engine modelling a race nobody is driving.
        """
        override = state.get("strategy_override", {}) or {}
        if not override.get("enabled"):
            return None
        # Only a deliberate choice counts. A plan's first compound is normally
        # the fitted tyre echoed back, and honouring that would mean a driver
        # who agreed a plan on mediums and then fitted softs got a race modelled
        # on mediums - the engine arguing with the car over a preference nobody
        # expressed.
        if not override.get("start_compound_explicit"):
            return None
        requested = str(override.get("start_compound") or "").upper()
        if not requested or requested == str(fitted).upper():
            return None
        if requested not in DRY_COMPOUNDS | WET_COMPOUNDS:
            return None
        # The driver has changed the car since asking. That is them acting on
        # the decision, and what they actually bolted on beats what they said.
        seen = str(override.get("start_compound_seen_fitted") or "").upper()
        if seen and seen != str(fitted).upper():
            return None
        if int(state.get("current_lap", 0) or 0) > 1 or int(age or 0) > 0:
            return None
        player_idx = int(state.get("player_car_index", 0))
        player = next(
            (
                driver
                for driver in state.get("drivers", [])
                if int(driver.get("car_idx", -1)) == player_idx
            ),
            None,
        )
        if player and int(player.get("pit_stops", 0) or 0) > 0:
            return None
        return requested

    # How much a plan has to gain before it is worth moving a stop away from the
    # lap the driver actually named. Below this the difference is model noise,
    # and answering "box lap 16" to a driver who said 14 just sounds like not
    # listening.
    _PLAN_LAP_DRIFT_TOLERANCE_S = 1.5

    @classmethod
    def _closest_to_plan(
        cls,
        candidates: list[dict[str, Any]],
        tail: dict[str, Any],
        ranking_key: Any,
    ) -> tuple[dict[str, Any], str]:
        """The driver's own laps, unless moving genuinely buys something.

        The lap tolerance exists so the engine can adapt - dodge traffic, take a
        safety car - not so it can quietly shift every call to the edge of the
        window. A driver who agreed laps 14 and 34 and is then told 16 and 36
        has been overruled by rounding.

        Returns the chosen plan and, when a stop did move, a sentence saying so.
        Substituting a better lap in silence is how a driver stops believing the
        plan is theirs.
        """
        best = min(candidates, key=ranking_key)
        wanted = [int(lap) for lap in tail.get("box_laps") or []]
        if not wanted:
            return best, ""

        def drift(plan: dict[str, Any]) -> int:
            laps = [int(lap) for lap in plan.get("box_laps") or []]
            if len(laps) != len(wanted):
                return 10_000
            return sum(abs(a - b) for a, b in zip(laps, wanted))

        if drift(best) == 0:
            return best, ""

        best_time = float(best.get("risk_adjusted_time_s", 0.0))
        closer = [
            plan
            for plan in candidates
            if drift(plan) < drift(best)
            and float(plan.get("risk_adjusted_time_s", 0.0)) - best_time
            <= cls._PLAN_LAP_DRIFT_TOLERANCE_S
        ]
        if closer:
            picked = min(closer, key=lambda plan: (drift(plan), ranking_key(plan)))
            if drift(picked) == 0:
                return picked, ""
            best = picked

        # Still not on the driver's laps, so say what the move is worth. The gain
        # is measured against the closest thing to what they actually asked for.
        exact = min(candidates, key=lambda plan: (drift(plan), ranking_key(plan)))
        gain = float(exact.get("risk_adjusted_time_s", 0.0)) - float(
            best.get("risk_adjusted_time_s", 0.0)
        )
        moved = next(
            (
                f"{asked} to {got}"
                for asked, got in zip(wanted, best.get("box_laps") or [])
                if int(asked) != int(got)
            ),
            "",
        )
        if not moved:
            return best, ""
        return best, f"Moved your stop from lap {moved}; it projects {gain:.0f}s better."

    @staticmethod
    def _best_per_shape(all_plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The strongest legal plan at each stop count, from the whole enumeration.

        Forced into the shortlist so every shape gets the same Monte Carlo
        treatment as the favourite and can be compared on equal numbers. Taking
        them from the shortlist instead would show only the shapes that already
        survived a pace cut - on the grid that was two- and three-stop, with no
        one-stop at all, which is the first thing a driver asks for.

        A shape whose best plan is infeasible is still carried. "A one-stop
        leaves you at 104% wear by the flag" is an answer; showing nothing is
        not, and the driver reads the silence as the option not existing.
        """
        best: dict[int, dict[str, Any]] = {}
        for plan in all_plans:
            if not plan.get("legal", True):
                continue
            stops = int(plan.get("stops_remaining", -1))
            if stops < 0:
                continue
            incumbent = best.get(stops)
            key = (
                not plan.get("feasible", False),
                float(plan.get("risk_adjusted_time_s", 0.0)),
            )
            if incumbent is None or key < (
                not incumbent.get("feasible", False),
                float(incumbent.get("risk_adjusted_time_s", 0.0)),
            ):
                best[stops] = plan
        return [best[stops] for stops in sorted(best)]

    @staticmethod
    def _race_shapes(
        candidates: list[dict[str, Any]],
        ranking_key: Any,
        allow_wet: bool = True,
    ) -> list[dict[str, Any]]:
        """The best plan at each stop count, cheapest shape first.

        A driver deciding a race before the lights is choosing between shapes -
        one stop or two - not between five variants of the shape the model
        already likes. Each entry carries the cost of choosing it so the choice
        is informed rather than a preference stated into the dark.
        """
        by_stops: dict[int, dict[str, Any]] = {}
        for plan in candidates:
            if not plan.get("legal", True):
                # An illegal shape is a disqualification, not a trade-off, so it
                # is never offered as something to choose.
                continue
            if not allow_wet and any(
                str(item).upper() in WET_COMPOUNDS
                for item in (plan.get("compounds") or [])[1:]
            ):
                # Fitting inters in the dry waives the two-dry-compound rule, so
                # such a plan reads as "legal" and can be the only legal shape at
                # some stop counts. It is a rules loophole, not a race anyone
                # should be offered as a strategy while the track is dry.
                continue
            stops = int(plan.get("stops_remaining", -1))
            if stops < 0:
                continue
            incumbent = by_stops.get(stops)
            if incumbent is None or ranking_key(plan) < ranking_key(incumbent):
                by_stops[stops] = plan

        # Achievable shapes first, then by rank. A shape the tyres cannot serve
        # is never the recommendation however good its modelled time looks.
        ordered = sorted(
            by_stops.values(),
            key=lambda plan: (not plan.get("feasible", False), ranking_key(plan)),
        )
        if not ordered:
            return []

        feasible = [plan for plan in ordered if plan.get("feasible")]
        reference = (
            float(feasible[0].get("risk_adjusted_time_s", 0.0)) if feasible else None
        )

        shapes = []
        for plan in ordered:
            achievable = bool(plan.get("feasible"))
            wear = plan.get("projected_max_wear_pct")
            # Only compare times between plans that can actually be driven.
            # A one-stop "saves" a pit stop and so always models quicker, which
            # on the grid read as 57 seconds faster while projecting 91% wear:
            # exactly the number that talks a driver into ruining their race.
            cost = (
                round(float(plan.get("risk_adjusted_time_s", 0.0)) - reference, 2)
                if achievable and reference is not None
                else None
            )
            shapes.append(
                {
                    "stops": int(plan.get("stops_remaining", 0)),
                    "compounds": list(plan.get("compounds", [])),
                    "box_laps": list(plan.get("box_laps", [])),
                    # How long each stint runs. The panel shows it, and it is
                    # the only way to check from outside that a plan accounts
                    # for exactly the laps that are left.
                    "stint_laps": [
                        int(stint.get("laps", 0))
                        for stint in plan.get("stint_models", [])
                    ],
                    "projected_finish_position": plan.get(
                        "projected_finish_position"
                    ),
                    "projected_max_wear_pct": wear,
                    "risk_adjusted_time_s": plan.get("risk_adjusted_time_s"),
                    "cost_vs_best_s": cost,
                    "feasible": achievable,
                    "verdict": (
                        "achievable"
                        if achievable
                        else f"runs out of tyre - {wear:.0f}% wear at the flag"
                        if isinstance(wear, (int, float))
                        else "runs out of tyre"
                    ),
                    "reason": plan.get("reason", ""),
                }
            )
        return shapes

    @classmethod
    def _driver_requested_plans(
        cls,
        all_plans: list[dict[str, Any]],
        driver_override: dict[str, Any],
        state: dict[str, Any],
        current_lap: int,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        """Candidates matching what the driver asked for, best first.

        These are pulled from the full enumeration so they can be forced into
        the shortlist. The shortlist is a pace cut, and a driver's plan is not
        chosen on pace - it is chosen on track position, tyre confidence, or
        simply wanting to know what happens if they commit. Judging the request
        by the cut that exists to discard slow plans throws the request away
        before anyone gets to see what it costs.

        ``all_plans`` is the *whole* enumeration, including plans the wear model
        rejects. A request the model calls infeasible is still worth carrying:
        the driver is then told their plan runs out of tyre, which they can act
        on, rather than being handed a different lap under a warning that the
        one they asked for was unavailable.
        """
        if not driver_override.get("enabled") or not driver_override.get("locked"):
            return []

        # Legality first, then whether the wear model accepts it, then pace.
        # This mirrors the deterministic ordering used for the shortlist, which
        # cannot be reused directly because it runs before Monte Carlo has
        # filled in the position fields it also reads.
        def preference(plan: dict[str, Any]) -> tuple[Any, ...]:
            return (
                not plan.get("legal", True),
                not plan.get("feasible", False),
                float(plan.get("risk_adjusted_time_s", 0.0)),
            )

        full_plan = driver_override.get("plan") or {}
        if full_plan.get("compounds"):
            tail = remaining_plan(
                full_plan, cls._completed_stops(state), current_lap
            )
            matches = [plan for plan in all_plans if plan_matches(plan, tail)]
            return sorted(matches, key=preference)[:limit]

        requested_lap = driver_override.get("next_box_lap")
        requested_compound = str(driver_override.get("next_compound") or "").upper()
        requested_stops = driver_override.get("preferred_stops")
        if requested_lap is None and not requested_compound and requested_stops is None:
            return []

        target_lap = (
            max(current_lap, int(requested_lap)) if requested_lap is not None else None
        )
        matches = []
        for plan in all_plans:
            compounds = plan.get("compounds") or []
            if requested_compound:
                if len(compounds) < 2:
                    continue
                if str(compounds[1]).upper() != requested_compound:
                    continue
            if requested_stops is not None and int(
                plan.get("stops_remaining", -1)
            ) != int(requested_stops):
                continue
            if target_lap is not None:
                box_laps = plan.get("box_laps") or []
                if not box_laps or int(box_laps[0]) != target_lap:
                    continue
            matches.append(plan)
        return sorted(matches, key=preference)[:limit]

    @staticmethod
    def _completed_stops(state: dict[str, Any]) -> int:
        """How many stops the driver has actually made, per telemetry.

        A driver plan is re-based on this rather than on the plan's own laps:
        stops happen early, late, and for reasons the plan never mentioned.
        """
        # Nobody has pitted before the race starts. Saying otherwise is always a
        # stale reading - a previous session, or a flashback to the grid - and
        # believing it silently deletes the driver's first stop from the plan
        # they just agreed, then reports that the race moved past it.
        if int(state.get("current_lap", 0) or 0) <= 1:
            return 0
        player_idx = int(state.get("player_car_index", 0))
        player = next(
            (
                driver
                for driver in state.get("drivers", [])
                if int(driver.get("car_idx", -1)) == player_idx
            ),
            None,
        )
        if not player:
            return 0
        return max(0, int(player.get("pit_stops", 0) or 0))

    @staticmethod
    def _used_compounds(state: dict[str, Any]) -> list[str]:
        used: list[str] = []
        player_idx = int(state.get("player_car_index", 0))
        player = next(
            (
                driver
                for driver in state.get("drivers", [])
                if int(driver.get("car_idx", -1)) == player_idx
            ),
            None,
        )
        if player:
            for stint in player.get("tyre_stints", []):
                compound = str(stint.get("compound", "UNKNOWN")).upper()
                if compound not in {"UNKNOWN", "FITTED"} and compound not in used:
                    used.append(compound)
        for lap in state.get("completed_laps", []):
            compound = str(lap.get("compound", "UNKNOWN")).upper()
            if compound not in {"UNKNOWN", "FITTED"} and compound not in used:
                used.append(compound)
        current = str(state.get("tyre", {}).get("compound", "UNKNOWN")).upper()
        if current not in {"UNKNOWN", "FITTED"} and current not in used:
            used.append(current)
        return used

    @staticmethod
    def _compound_rule(
        state: dict[str, Any], future: list[str] | None = None
    ) -> dict[str, Any]:
        used = StrategyEngine._used_compounds(state)
        future = [str(value).upper() for value in (future or [])]
        all_compounds = used + [item for item in future if item not in used]
        wet_used = any(item in WET_COMPOUNDS for item in all_compounds)
        dry_used = sorted({item for item in all_compounds if item in DRY_COMPOUNDS})
        applies = str(state.get("mode_profile", "idle")) == "race" and not wet_used
        compliant = (not applies) or len(dry_used) >= 2
        return {
            "applies": applies,
            "wet_waiver": wet_used,
            "used_compounds": used,
            "dry_compounds_used": dry_used,
            "dry_count": len(dry_used),
            "required_dry_count": 2 if applies else 0,
            "compliant": compliant,
            "change_outstanding": applies and not compliant,
            "eligible_next_compounds": sorted(DRY_COMPOUNDS - set(dry_used))
            if applies
            else [],
            "note": (
                "Two different dry visual compounds are enforced for a dry Race. "
                "The game does not expose the event-specific FIA mandatory race specification."
                if applies
                else "Wet/intermediate use waives the two-dry-compound check."
                if wet_used
                else "The two-dry-compound Race requirement does not apply to this session type."
            ),
        }

    @staticmethod
    def _available_sets(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
        best: dict[str, dict[str, Any]] = {}
        for tyre in state.get("tyre_sets", []):
            if not tyre.get("available"):
                continue
            compound = str(tyre.get("compound", "UNKNOWN")).upper()
            if compound not in DRY_COMPOUNDS | WET_COMPOUNDS:
                continue
            candidate = dict(tyre)
            current = best.get(compound)
            candidate_key = (
                float(candidate.get("wear_pct", 100.0)),
                -int(candidate.get("usable_life_laps", 0)),
                int(candidate.get("lap_delta_ms", 0)),
            )
            if current is None:
                best[compound] = candidate
            else:
                current_key = (
                    float(current.get("wear_pct", 100.0)),
                    -int(current.get("usable_life_laps", 0)),
                    int(current.get("lap_delta_ms", 0)),
                )
                if candidate_key < current_key:
                    best[compound] = candidate
        if not best:
            best = {
                compound: {
                    "compound": compound,
                    "wear_pct": 0.0,
                    "usable_life_laps": TYPICAL_STINT_LAPS.get(compound, 0),
                    "life_span_laps": TYPICAL_STINT_LAPS.get(compound, 0) + 3,
                    "lap_delta_ms": 0,
                    "source": "fallback",
                }
                for compound in ("SOFT", "MEDIUM", "HARD")
            }
        return best

    @staticmethod
    def _live_wear_samples(state: dict[str, Any], compound: str) -> list[float]:
        samples: list[float] = []
        for lap in StrategyEngine._valid_laps(state)[-10:]:
            if str(lap.get("compound", "")).upper() != compound:
                continue
            start = lap.get("wear_start") or []
            end = lap.get("wear_end") or []
            if len(start) == 4 and len(end) == 4:
                # Strategy is constrained by the most worn corner, not the
                # four-tyre average. This catches driving styles that load one
                # axle or one side heavily (for example Spa rear wear).
                samples.append(
                    max(
                        max(0.0, float(after) - float(before))
                        for before, after in zip(start, end)
                    )
                )
        return samples

    @staticmethod
    def _driver_wear_factor(
        state: dict[str, Any],
        historical: dict[str, Any],
    ) -> tuple[float, dict[str, Any]]:
        current = str(state.get("tyre", {}).get("compound", "MEDIUM")).upper()
        severity = TRACK_TYRE_SEVERITY.get(int(state.get("track_id", -1)), 1.0)
        default = DEFAULT_WEAR_PER_LAP.get(current, 3.2) * severity
        live_samples = StrategyEngine._live_wear_samples(state, current)
        observed: float | None = float(median(live_samples)) if live_samples else None
        source = "live_lap_wear"
        sample_size = len(live_samples)

        if observed is None:
            history_model = historical.get("compounds", {}).get(current, {})
            value = history_model.get("max_wear_per_lap_pct")
            if value is not None:
                observed = float(value)
                source = "personal_track_history"
                sample_size = int(history_model.get("wear_sample_size", 0))

        if observed is None:
            age = int(state.get("tyre", {}).get("age_laps", 0))
            wear = state.get("tyre", {}).get("wear", []) or []
            if age >= 2 and wear:
                observed = max(float(v) for v in wear) / age
                source = "current_stint_level"
                sample_size = age

        if observed is None or observed <= 0:
            return 1.0, {
                "factor": 1.0,
                "observed_limiting_wear_per_lap_pct": None,
                "baseline_wear_per_lap_pct": round(default, 3),
                "source": "track_default",
                "sample_size": 0,
            }

        factor = max(0.60, min(2.60, observed / max(default, 0.1)))
        return factor, {
            "factor": round(factor, 3),
            "observed_limiting_wear_per_lap_pct": round(observed, 3),
            "baseline_wear_per_lap_pct": round(default, 3),
            "source": source,
            "sample_size": sample_size,
        }

    @staticmethod
    def _driver_feedback_adjustment(
        state: dict[str, Any], compound: str
    ) -> dict[str, Any]:
        feedback = state.get("driver_tyre_feedback", {}) or {}
        category = str(feedback.get("category", ""))
        current_lap = int(state.get("current_lap", 0) or 0)
        feedback_lap = int(feedback.get("lap", 0) or 0)
        current_compound = str(
            state.get("tyre", {}).get("compound", "UNKNOWN")
        ).upper()
        age = max(0, current_lap - feedback_lap)
        if (
            not category
            or feedback_lap <= 0
            or age >= 5
            or str(compound).upper() != current_compound
        ):
            return {
                "active": False,
                "wear_factor": 1.0,
                "deg_factor": 1.0,
                "lap": feedback_lap or None,
                "category": category or None,
                "weight": 0.0,
            }
        raw = {
            "tyres_gone": (1.25, 1.30),
            "tyres_going": (1.10, 1.15),
            "tyres_fine": (0.92, 0.90),
        }.get(category, (1.0, 1.0))
        confidence = max(0.0, min(1.0, float(feedback.get("confidence", 1.0))))
        decay = max(0.0, 1.0 - age / 5.0)
        weight = confidence * decay
        wear_factor = 1.0 + (raw[0] - 1.0) * weight
        deg_factor = 1.0 + (raw[1] - 1.0) * weight
        return {
            "active": category in {"tyres_gone", "tyres_going", "tyres_fine"},
            "wear_factor": round(max(0.75, min(1.25, wear_factor)), 4),
            "deg_factor": round(max(0.75, min(1.25, deg_factor)), 4),
            "lap": feedback_lap,
            "category": category,
            "weight": round(weight, 3),
        }

    @staticmethod
    def _wear_rate(
        state: dict[str, Any],
        compound: str,
        historical: dict[str, Any],
        style_factor: float,
    ) -> tuple[float, str, int]:
        feedback = StrategyEngine._driver_feedback_adjustment(state, compound)

        def apply_feedback(value: float, source: str, sample_size: int) -> tuple[float, str, int]:
            factor = float(feedback["wear_factor"])
            suffix = "+driver_feedback" if feedback["active"] else ""
            return value * factor, source + suffix, sample_size

        live = StrategyEngine._live_wear_samples(state, compound)
        if live:
            return apply_feedback(float(median(live)), "live_lap_wear", len(live))
        model = historical.get("compounds", {}).get(compound, {})
        condition_adjusted = model.get("condition_adjusted_wear_per_lap_pct")
        if condition_adjusted is not None and int(model.get("wear_sample_size", 0)) >= 6:
            return apply_feedback(
                float(condition_adjusted),
                "condition_adjusted_personal_regression",
                int(model.get("wear_sample_size", 0)),
            )
        value = model.get("max_wear_per_lap_pct")
        if value is not None and int(model.get("wear_sample_size", 0)) >= 2:
            return apply_feedback(
                float(value),
                "personal_track_history",
                int(model.get("wear_sample_size", 0)),
            )
        severity = TRACK_TYRE_SEVERITY.get(int(state.get("track_id", -1)), 1.0)
        return apply_feedback(
            DEFAULT_WEAR_PER_LAP.get(compound, 3.0) * severity * style_factor,
            "style_adjusted_track_default",
            0,
        )

    @staticmethod
    def _deg_for(
        state: dict[str, Any],
        compound: str,
        historical: dict[str, Any],
    ) -> tuple[float, str, int]:
        feedback = StrategyEngine._driver_feedback_adjustment(state, compound)

        def apply_feedback(value: float, source: str, sample_size: int) -> tuple[float, str, int]:
            factor = float(feedback["deg_factor"])
            suffix = "+driver_feedback" if feedback["active"] else ""
            return value * factor, source + suffix, sample_size

        model = state.get("analysis", {}).get("deg_model", {})
        live = (
            model.get("compounds", {}).get(compound, {})
            if isinstance(model, dict)
            else {}
        )
        value = live.get("slope_s_per_lap")
        sample = int(live.get("sample_size", 0))
        if value is not None and sample >= 3 and -0.1 <= float(value) <= 1.5:
            return apply_feedback(max(0.0, float(value)), "live_fuel_corrected_fit", sample)
        prior = historical.get("compounds", {}).get(compound, {})
        condition_adjusted = prior.get("condition_adjusted_deg_s_per_lap")
        sample = int(prior.get("sample_size", 0))
        if condition_adjusted is not None and sample >= 6 and -0.1 <= float(condition_adjusted) <= 1.5:
            return apply_feedback(max(0.0, float(condition_adjusted)), "condition_adjusted_personal_regression", sample)
        value = prior.get("slope_s_per_lap")
        if value is not None and sample >= 3 and -0.1 <= float(value) <= 1.5:
            return apply_feedback(max(0.0, float(value)), "personal_track_history", sample)
        severity = TRACK_TYRE_SEVERITY.get(int(state.get("track_id", -1)), 1.0)
        return apply_feedback(
            DEFAULT_DEG.get(compound, 0.08) * severity, "track_default", 0
        )

    @staticmethod
    def _pit_entry_status(state: dict[str, Any]) -> str:
        track_length = float(state.get("track_length_m", 0) or 0)
        distance = float(state.get("lap_distance_m", 0) or 0)
        if track_length <= 100 or distance <= 0:
            return "unknown"
        threshold = PIT_ENTRY_FRACTION.get(int(state.get("track_id", -1)), 0.94)
        return "passed" if distance / track_length >= threshold else "available"

    @staticmethod
    def _base_pit_loss(state: dict[str, Any]) -> float:
        measured = [
            float(lap.get("pit_lane_time_ms", 0)) / 1000
            for lap in state.get("completed_laps", [])
            if float(lap.get("pit_lane_time_ms", 0)) > 5000
        ]
        return (
            float(median(measured))
            if measured
            else PIT_LOSS_SECONDS.get(int(state.get("track_id", -1)), 22.5)
        )

    @staticmethod
    def _neutralisation(state: dict[str, Any]) -> dict[str, Any]:
        base = StrategyEngine._base_pit_loss(state)
        phase = str(state.get("race_control_phase", "green"))
        safety = str(state.get("safety_car", "none"))
        red = bool(state.get("red_flag_active")) or phase == "red_flag"
        if red:
            factor = 0.0
            kind = "red_flag"
        elif phase in {"safety_car", "formation"} or safety == "full":
            factor = 0.46
            kind = "safety_car"
        elif phase == "vsc" or safety == "virtual":
            factor = 0.64
            kind = "vsc"
        elif phase in {"safety_car_ending", "vsc_ending", "formation_ending"}:
            factor = 0.82
            kind = phase
        else:
            factor = 1.0
            kind = "green"
        effective = base * factor
        pit_entry_status = StrategyEngine._pit_entry_status(state)
        opportunity = (
            "free_tyre_change_during_suspension"
            if red
            else "major_discount_and_field_compression"
            if kind == "safety_car"
            else "discount_with_gaps_partly_preserved"
            if kind == "vsc"
            else "reduced_discount_as_restart_approaches"
            if kind.endswith("ending")
            else "normal_green_flag_stop"
        )
        current_lap = int(state.get("current_lap", 0))
        total_laps = int(state.get("total_laps", 0))
        laps_remaining = max(0, total_laps - current_lap + (1 if current_lap > 0 else 0))
        late_neutralisation = kind in {"safety_car", "safety_car_ending"} and laps_remaining <= 4
        finish_under_neutralisation_risk = (
            "high" if late_neutralisation and laps_remaining <= 2
            else "medium" if late_neutralisation
            else "low"
        )
        return {
            "phase": kind,
            "base_pit_loss_s": round(base, 2),
            "effective_pit_loss_s": round(effective, 2),
            "saving_vs_green_s": round(base - effective, 2),
            "pit_entry_status": pit_entry_status,
            "pit_this_lap_available": red or pit_entry_status != "passed",
            "field_compression_expected": kind == "safety_car",
            "gaps_partly_preserved": kind == "vsc",
            "opportunity": opportunity,
            "laps_remaining": laps_remaining,
            "late_neutralisation": late_neutralisation,
            "finish_under_neutralisation_risk": finish_under_neutralisation_risk,
            "track_position_warning": (
                "A late safety-car stop can permanently surrender track position if the race does not restart."
                if late_neutralisation
                else ""
            ),
            "estimate_confidence": (
                "high"
                if red
                else "medium"
                if kind in {"safety_car", "vsc", "green"}
                else "low"
            ),
            "red_flag_tyre_change": red,
        }

    @staticmethod
    def _weather_crossover(
        state: dict[str, Any],
        base_lap_s: float,
        effective_pit_loss_s: float = 0.0,
    ) -> dict[str, Any] | None:
        current_lap = int(state.get("current_lap", 0))
        total_laps = int(state.get("total_laps", 0))
        weather = str(state.get("weather", "Unknown"))
        current_rain = weather in {"Light rain", "Heavy rain", "Storm"}
        current_compound = str(state.get("tyre", {}).get("compound", "UNKNOWN"))
        if current_rain and current_compound not in WET_COMPOUNDS:
            compound = "WET" if weather in {"Heavy rain", "Storm"} else "INTER"
            # A tyre change has to earn back the pit lane. A tyre fitted at the
            # end of the current lap only benefits the laps run after it, so
            # calling a stop into the flag costs the pit loss to gain nothing.
            # The per-lap gain is the pace penalty currently being paid for
            # running the wrong compound, taken from the same COMPOUND_DELTA
            # table the stint simulation uses rather than a separate figure.
            remaining = max(0, total_laps - current_lap + (1 if current_lap > 0 else 0))
            benefiting_laps = max(0, remaining - 1)
            per_lap_gain_s = COMPOUND_DELTA.get(compound, 7.0)
            payback_s = benefiting_laps * per_lap_gain_s
            if effective_pit_loss_s > 0.0 and payback_s <= effective_pit_loss_s:
                return {
                    "box_lap": None,
                    "compound": compound,
                    "rain_pct": 100 if weather == "Storm" else 80
                    if weather == "Heavy rain" else 60,
                    "time_offset_min": 0,
                    "worth_stopping": False,
                    "benefiting_laps": benefiting_laps,
                    "payback_s": round(payback_s, 1),
                    "pit_loss_s": round(effective_pit_loss_s, 1),
                    "reason": (
                        f"{weather} is on track, but with {benefiting_laps} lap(s) "
                        f"left to gain on the change the stop cannot repay its "
                        f"{effective_pit_loss_s:.0f}s pit loss. Stay out and manage it."
                    ),
                }
            return {
                "box_lap": current_lap,
                "compound": compound,
                "rain_pct": 100
                if weather == "Storm"
                else 80
                if weather == "Heavy rain"
                else 60,
                "time_offset_min": 0,
                "worth_stopping": True,
                "benefiting_laps": benefiting_laps,
                "payback_s": round(payback_s, 1),
                "reason": f"{weather} is already on track.",
            }
        for sample in sorted(
            state.get("weather_forecast", []),
            key=lambda item: int(item.get("time_offset_min", 999)),
        ):
            rain_pct = int(sample.get("rain_pct", 0))
            if rain_pct < 60:
                continue
            minutes = max(0, int(sample.get("time_offset_min", 0)))
            laps_until = max(1, math.ceil(minutes * 60 / max(base_lap_s, 30.0)))
            box_lap = min(total_laps, current_lap + laps_until)
            sample_weather = str(sample.get("weather", "Light rain"))
            compound = (
                "WET"
                if rain_pct >= 80 or sample_weather in {"Heavy rain", "Storm"}
                else "INTER"
            )
            return {
                "box_lap": box_lap,
                "compound": compound,
                "rain_pct": rain_pct,
                "time_offset_min": minutes,
                "reason": f"Rain reaches {rain_pct}% in about {minutes} minutes ({sample_weather.lower()}).",
            }
        return None

    @staticmethod
    def _rejoin_position(state: dict[str, Any], effective_pit_loss_s: float) -> int:
        current_position = int(state.get("player_position", 0)) or 1
        if effective_pit_loss_s <= 0:
            return current_position
        lost_positions = 0
        for driver in state.get("drivers", []):
            gap = driver.get("gap_to_player_s")
            if gap is not None and 0 < float(gap) < effective_pit_loss_s:
                lost_positions += 1
        active = int(state.get("active_cars", 0)) or 24
        return min(active, current_position + lost_positions)

    @staticmethod
    def _recent_driver_pace_s(driver: dict[str, Any]) -> tuple[float | None, int]:
        values = [
            int(lap.get("lap_ms", 0) or 0) / 1000.0
            for lap in (driver.get("lap_history") or [])[-8:]
            if int(lap.get("lap_ms", 0) or 0) > 0
            and bool(lap.get("valid_flags", 1) & 1)
        ][-5:]
        return (float(median(values)), len(values)) if values else (None, 0)

    def _project_rival_finish_times(
        self,
        state: dict[str, Any],
        historical: dict[str, Any],
        remaining: int,
        base_lap_s: float,
        effective_pit_loss_s: float,
    ) -> list[dict[str, Any]]:
        """Project field finish times on the player's current time axis.

        A negative live gap means the rival has already covered the reference
        distance sooner, so it is added directly to their remaining-time model.
        This makes the player's candidate ``projected_time_s`` and every rival
        score comparable without inventing an absolute race clock.
        """
        projections: list[dict[str, Any]] = []
        player_idx = int(state.get("player_car_index", -1))
        player_position = int(state.get("player_position", 0) or 0)
        clean_state = dict(state)
        clean_state["driver_tyre_feedback"] = {}
        for driver in state.get("drivers", []):
            if int(driver.get("car_idx", -1)) == player_idx:
                continue
            position = int(driver.get("position", 0) or 0)
            result = str(driver.get("result_label", ""))
            if position <= 0 or result in {
                "retired", "did not finish", "disqualified", "not classified"
            }:
                continue
            pace, pace_samples = self._recent_driver_pace_s(driver)
            compound = str(driver.get("tyre_compound", "MEDIUM")).upper()
            if pace is None:
                pace = base_lap_s + COMPOUND_DELTA.get(compound, 0.0)
            deg, deg_source, deg_samples = self._deg_for(
                clean_state, compound, historical
            )
            age = max(0, int(driver.get("tyre_age", 0) or 0))
            typical = max(6, int(TYPICAL_STINT_LAPS.get(compound, 18)))
            life_left = max(0, typical - age)
            stops = (
                max(0, math.ceil(max(0, remaining - life_left) / typical))
                if remaining > life_left
                else 0
            )
            running_time = 0.0
            future_age = age
            for offset in range(max(0, remaining)):
                if offset == life_left and stops:
                    future_age = 0
                running_time += pace + deg * max(0, future_age - age)
                future_age += 1
            running_time += stops * effective_pit_loss_s
            gap = driver.get("gap_to_player_s")
            gap_assumed = gap is None
            if gap is None:
                # Preserve the classified ordering when the timing feed withholds
                # a gap, but make the low-confidence assumption explicit.
                offset = position - player_position if player_position else 0
                gap = float(offset) * 2.0
            projections.append(
                {
                    "driver": driver.get("name", "Unknown"),
                    "position": position,
                    "finish_time_s": round(float(gap) + running_time, 3),
                    "current_gap_s": round(float(gap), 3),
                    "pace_s": round(float(pace), 3),
                    "pace_samples": pace_samples,
                    "compound": compound,
                    "tyre_age": age,
                    "likely_remaining_stops": stops,
                    "deg_s_per_lap": round(float(deg), 4),
                    "deg_source": deg_source,
                    "deg_samples": deg_samples,
                    "confidence": (
                        "low" if gap_assumed or pace_samples == 0
                        else "medium" if pace_samples < 3 or deg_samples < 3
                        else "high"
                    ),
                    "gap_assumed": gap_assumed,
                }
            )
        return projections

    @staticmethod
    def _expected_positions_recovered(
        plan: dict[str, Any],
        state: dict[str, Any],
        rival_projections: list[dict[str, Any]],
        difficulty: float,
    ) -> float:
        lost = max(
            0,
            int(plan.get("projected_rejoin_position", 1))
            - int(state.get("player_position", 1) or 1),
        )
        if not plan.get("stops_remaining") or lost <= 0:
            return 0.0
        final_stint = (plan.get("stint_models") or [{}])[-1]
        lap_times = [float(value) for value in final_stint.get("lap_times_s", [])]
        laps_after_stop = len(lap_times)
        if laps_after_stop <= 0:
            return 0.0
        player_final_pace = float(median(lap_times))
        nearby = [
            float(item["pace_s"])
            for item in rival_projections
            if item.get("pace_s") is not None
        ]
        reference_pace = float(median(nearby)) if nearby else player_final_pace
        pace_advantage = max(0.0, reference_pace - player_final_pace)
        # Even with a large tyre offset, track capacity bounds recovery. Seven
        # laps at Monaco-like 0.9 difficulty cannot plausibly recover nine cars.
        capacity = (
            laps_after_stop * (1.0 - difficulty) * 0.90
            + max(0.0, pace_advantage - 0.15)
            * laps_after_stop
            / (1.2 + 2.8 * difficulty)
        )
        return round(max(0.0, min(float(lost), capacity)), 2)

    def _annotate_finish_projection(
        self,
        plan: dict[str, Any],
        state: dict[str, Any],
        rival_projections: list[dict[str, Any]],
        difficulty: float,
        difficulty_known: bool,
    ) -> None:
        projected_time = float(plan.get("projected_time_s", 1e9))
        raw_position = (
            1
            + sum(
                1
                for rival in rival_projections
                if float(rival.get("finish_time_s", 1e9)) < projected_time
            )
            if rival_projections
            else int(state.get("player_position", 1) or 1)
        )
        rejoin = int(
            plan.get("projected_rejoin_position", state.get("player_position", 1))
            or 1
        )
        recovered = self._expected_positions_recovered(
            plan, state, rival_projections, difficulty
        )
        best_from_rejoin = max(1, rejoin - math.floor(recovered + 1e-9))
        projected_position = (
            max(raw_position, best_from_rejoin)
            if int(plan.get("stops_remaining", 0) or 0) > 0
            else raw_position
        )
        active = int(state.get("active_cars", 0) or 0) or max(
            1, len(rival_projections) + 1
        )
        projected_position = max(1, min(active, projected_position))
        current = int(state.get("player_position", 1) or 1)
        plan.update(
            {
                "projected_finish_position": projected_position,
                "projected_points": points_for_position(projected_position),
                "positions_lost_by_stopping": max(0, rejoin - current),
                "expected_positions_recovered": recovered,
                "overtaking_difficulty": round(difficulty, 2),
                "overtaking_difficulty_known": difficulty_known,
                "finish_projection_confidence": (
                    "low"
                    if not difficulty_known
                    or any(item.get("confidence") == "low" for item in rival_projections)
                    else "medium"
                    if any(item.get("confidence") == "medium" for item in rival_projections)
                    else "high"
                ),
            }
        )

    @staticmethod
    def _position_distribution(
        plan: dict[str, Any],
        state: dict[str, Any],
        rival_projections: list[dict[str, Any]],
        outcome_times: np.ndarray,
    ) -> dict[str, Any]:
        active = int(state.get("active_cars", 0) or 0) or max(
            1, len(rival_projections) + 1
        )
        rejoin = int(plan.get("projected_rejoin_position", 1) or 1)
        recovery = math.floor(float(plan.get("expected_positions_recovered", 0.0)))
        cap = max(1, rejoin - recovery)
        positions: list[int] = []
        for outcome in outcome_times:
            raw = (
                1
                + sum(
                    1
                    for rival in rival_projections
                    if float(rival.get("finish_time_s", 1e9)) < float(outcome)
                )
                if rival_projections
                else int(state.get("player_position", 1) or 1)
            )
            if int(plan.get("stops_remaining", 0) or 0) > 0:
                raw = max(raw, cap)
            positions.append(max(1, min(active, raw)))
        counts = {position: positions.count(position) for position in sorted(set(positions))}
        total = max(1, len(positions))
        bands = {
            "P1-3": sum(1 for value in positions if value <= 3) / total,
            "P4-6": sum(1 for value in positions if 4 <= value <= 6) / total,
            "P7-10": sum(1 for value in positions if 7 <= value <= 10) / total,
            "P11-15": sum(1 for value in positions if 11 <= value <= 15) / total,
            "P16+": sum(1 for value in positions if value >= 16) / total,
        }
        expected_points = sum(points_for_position(value) for value in positions) / total
        return {
            "outcome_distribution": {
                key: round(value, 4) for key, value in bands.items()
            },
            "position_probabilities": {
                f"P{key}": round(value / total, 4) for key, value in counts.items()
            },
            "points_expected": round(expected_points, 3),
            "expected_finish_position": round(float(np.mean(positions)), 2),
            "upside_p90": int(np.quantile(positions, 0.10, method="nearest")),
            "downside_p10": int(np.quantile(positions, 0.90, method="nearest")),
            # Unambiguous aliases for callers that use percentile direction
            # rather than the specification's upside/downside labels.
            "upside_p10_position": int(np.quantile(positions, 0.10, method="nearest")),
            "downside_p90_position": int(np.quantile(positions, 0.90, method="nearest")),
        }

    @staticmethod
    def defence_assessment(state: dict[str, Any]) -> dict[str, Any]:
        position = int(state.get("player_position", 0) or 0)
        remaining = max(
            0,
            int(state.get("total_laps", 0) or 0)
            - int(state.get("current_lap", 0) or 0),
        )
        pursuer = next(
            (
                driver
                for driver in state.get("drivers", [])
                if int(driver.get("position", 0) or 0) == position + 1
            ),
            None,
        )
        if position <= 0 or pursuer is None:
            return {
                "available": False,
                "reason": "No classified car directly behind to assess.",
            }
        history = [
            item
            for item in (pursuer.get("gap_history") or [])
            if item.get("gap_s") is not None
        ]
        closing_rate = None
        if len(history) >= 2:
            first, last = history[0], history[-1]
            laps = float(last.get("lap", 0)) - float(first.get("lap", 0))
            if laps < 1:
                seconds = float(last.get("session_time_s", 0)) - float(
                    first.get("session_time_s", 0)
                )
                laps = seconds / 90.0 if seconds >= 20 else 0.0
            if laps > 0:
                closing_rate = (
                    abs(float(last["gap_s"])) - abs(float(first["gap_s"]))
                ) / laps
        gap = abs(float(pursuer.get("gap_to_player_s", 0.0) or 0.0))
        sustainable = (
            round(gap / abs(closing_rate), 1)
            if closing_rate is not None and closing_rate < -0.05 and gap > 0
            else float(remaining)
            if remaining > 0
            else None
        )
        track_id = int(state.get("track_id", -1))
        difficulty = TRACK_OVERTAKING_DIFFICULTY.get(track_id, 0.60)
        zones = len(state.get("active_aero_zones", []) or []) or len(
            state.get("drs_zones", []) or []
        )
        deg = float(
            state.get("analysis", {})
            .get("deg_model", {})
            .get("current_slope_s_per_lap", 0.0)
            or 0.0
        )
        closing_pressure = (
            min(0.25, abs(float(closing_rate)) * 0.10)
            if closing_rate is not None and closing_rate < 0
            else 0.0
        )
        pass_probability = max(
            0.05,
            min(
                0.95,
                (1.0 - difficulty) * 0.62
                + min(0.18, zones * 0.045)
                + closing_pressure
                + min(0.15, max(0.0, deg) * 0.25),
            ),
        )
        player = next(
            (
                driver
                for driver in state.get("drivers", [])
                if int(driver.get("car_idx", -1))
                == int(state.get("player_car_index", -2))
            ),
            {},
        )
        position_history = player.get("position_history", []) or []
        positions_lost = (
            max(0, int(position_history[-1]["position"]) - int(position_history[0]["position"]))
            if len(position_history) >= 2
            else 0
        )
        corner_loss = max(
            [
                float(item.get("loss_vs_pb_s", 0.0) or 0.0)
                for item in state.get("analysis", {}).get("corner_metrics", [])
            ]
            or [0.0]
        )
        score = max(
            1.0,
            min(
                10.0,
                8.5
                - positions_lost * 1.2
                - min(2.5, corner_loss * 4.0)
                - max(0.0, pass_probability - 0.50) * 3.0,
            ),
        )
        return {
            "available": True,
            "pursuer": pursuer.get("name", "Car behind"),
            "gap_s": round(gap, 2),
            "closing_rate_s_per_lap": (
                round(float(closing_rate), 3) if closing_rate is not None else None
            ),
            "defence_laps_sustainable": sustainable,
            "overtaking_difficulty": round(difficulty, 2),
            "passing_zone_count": zones,
            "estimated_pass_probability": round(pass_probability, 3),
            "defence_quality_score_out_of_10": round(score, 1),
            "positions_lost_in_recorded_history": positions_lost,
            "largest_corner_loss_vs_pb_s": round(corner_loss, 3),
            "confidence": (
                "medium" if len(history) >= 3 and position_history else "low"
            ),
            "interpretation": (
                "A negative closing rate means the car behind is catching. The "
                "quality score combines position retention, corner-time retention, "
                "track passing difficulty and measured pressure; it is not subjective."
            ),
        }

    @staticmethod
    def _live_wheel_wear_samples(
        state: dict[str, Any], compound: str
    ) -> list[list[float]]:
        wheel_samples: list[list[float]] = [[], [], [], []]
        for lap in StrategyEngine._valid_laps(state)[-12:]:
            if str(lap.get("compound", "")).upper() != compound:
                continue
            start = lap.get("wear_start") or []
            end = lap.get("wear_end") or []
            if len(start) != 4 or len(end) != 4:
                continue
            for index, (before, after) in enumerate(zip(start, end)):
                wheel_samples[index].append(max(0.0, float(after) - float(before)))
        return wheel_samples

    def _wheel_wear_rates(
        self,
        state: dict[str, Any],
        compound: str,
        historical: dict[str, Any],
        style_factor: float,
    ) -> tuple[list[float], str, int, dict[str, Any]]:
        live = self._live_wheel_wear_samples(state, compound)
        if any(live):
            fallback, _, _ = self._wear_rate(state, compound, historical, style_factor)
            # Samples are clamped at zero when collected, so a lap whose wear
            # did not tick over contributes 0.0. Taking that as the rate gives
            # a tyre that never wears; fall back to the prior for that wheel.
            rates = [
                float(median(values))
                if values and float(median(values)) > 0.0
                else fallback
                for values in live
            ]
            return rates, "live_per_wheel_wear", min(len(v) for v in live if v), setup_effects(state.get("car_setup", {}), int(state.get("track_id", -1)))

        history = historical.get("compounds", {}).get(compound, {})
        history_rates = history.get("wheel_wear_per_lap_pct") or []
        history_samples = int(history.get("wear_sample_size", 0))
        # Match the sample discipline the scalar path already applies: one
        # observation is noise, not a rate. Without this a single sample was
        # trusted outright, and a lap whose wear did not tick over produced a
        # 0%/lap tyre. A stint on it then projected 0% wear at the finish,
        # which both looked perfect to the ranking and reached the radio.
        if len(history_rates) == 4 and history_samples >= 2:
            fallback = float(
                history.get("max_wear_per_lap_pct")
                or DEFAULT_WEAR_PER_LAP.get(compound, 3.0)
            )
            # A tyre that wears nothing per lap does not exist, so a
            # non-positive rate is a data artifact rather than a measurement.
            rates = [
                float(value)
                if value is not None and float(value) > 0.0
                else fallback
                for value in history_rates
            ]
            if any(rate > 0.0 for rate in rates):
                return (
                    rates,
                    "personal_per_wheel_history",
                    history_samples,
                    setup_effects(
                        state.get("car_setup", {}), int(state.get("track_id", -1))
                    ),
                )

        scalar, source, sample_size = self._wear_rate(state, compound, historical, style_factor)
        effects = setup_effects(state.get("car_setup", {}), int(state.get("track_id", -1)))
        multipliers = effects.get("wheel_wear_multipliers", [1.0, 1.0, 1.0, 1.0])
        rates = [scalar * float(multiplier) for multiplier in multipliers]
        return rates, f"{source}+setup_prior", sample_size, effects

    def _simulate_stint(
        self,
        state: dict[str, Any],
        compound: str,
        laps: int,
        starting_age: int,
        starting_wear: float | list[float],
        base_lap_s: float,
        historical: dict[str, Any],
        style_factor: float,
        set_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        deg, deg_source, deg_samples = self._deg_for(state, compound, historical)
        wheel_rates, wear_source, wear_samples, effects = self._wheel_wear_rates(
            state, compound, historical, style_factor
        )
        set_info = set_info or {}
        set_wear = max(0.0, float(set_info.get("wear_pct", 0.0)))
        if isinstance(starting_wear, list) and len(starting_wear) == 4:
            wear = [max(float(value), set_wear) for value in starting_wear]
        else:
            wear = [max(float(starting_wear), set_wear)] * 4
        usable_life = int(set_info.get("usable_life_laps", 0) or 0)
        life_span = int(set_info.get("life_span_laps", 0) or 0)
        set_delta_s = float(set_info.get("lap_delta_ms", 0) or 0) / 1000.0
        reference = str(state.get("tyre", {}).get("compound", "MEDIUM"))
        compound_delta = COMPOUND_DELTA.get(compound, 0.0) - COMPOUND_DELTA.get(reference, 0.0)
        setup_delta_s = float(effects.get("lap_time_delta_s", 0.0))
        expected = conservative = 0.0
        feasible = True
        peak_wear = max(wear)
        lap_times: list[float] = []
        wheel_projection: list[list[float]] = []
        # A fresh set is cold for its first laps. Only a stint that begins on a
        # new tyre (starting_age == 0) pays this; continuing the current stint
        # does not. Full penalty on the out-lap, half on the following lap.
        cold_penalty_s = (
            settings.strategy_cold_tyre_penalty_s if starting_age == 0 else 0.0
        )
        for offset in range(max(0, laps)):
            age = starting_age + offset
            thermal_growth = 1.0 + max(0, age - 8) * 0.012
            for index in range(4):
                wear[index] += wheel_rates[index] * thermal_growth
            peak_wear = max(peak_wear, max(wear))
            average_wear = sum(wear) / 4
            wear_penalty = max(0.0, average_wear - 52.0) * 0.009 + max(0.0, peak_wear - 58.0) * 0.008
            cliff = max(0.0, peak_wear - 70.0) * 0.060
            warm_up = cold_penalty_s if offset == 0 else (cold_penalty_s * 0.5 if offset == 1 else 0.0)
            expected_lap = base_lap_s + compound_delta + set_delta_s + setup_delta_s + deg * age + wear_penalty + cliff + warm_up
            uncertainty = 0.045 + (0.24 if compound == "SOFT" else 0.12 if compound == "MEDIUM" else 0.075) * (1.0 if min(deg_samples, wear_samples) < 3 else 0.35)
            conservative_lap = expected_lap + uncertainty + max(0.0, peak_wear - 65.0) * 0.020
            expected += expected_lap
            conservative += conservative_lap
            lap_times.append(expected_lap)
            wheel_projection.append([round(value, 2) for value in wear])
            if peak_wear >= 92.0:
                feasible = False
                expected += 18.0 + (peak_wear - 92.0) * 2.0
                conservative += 30.0 + (peak_wear - 92.0) * 3.0
        if usable_life > 0 and laps > usable_life + 1:
            feasible = False
            conservative += (laps - usable_life) * 8.0
        if life_span > 0 and starting_age + laps > life_span + 2:
            feasible = False
            conservative += (starting_age + laps - life_span) * 5.0
        operational_limit = OPERATIONAL_WEAR_LIMIT.get(compound, 85.0)
        if peak_wear > operational_limit:
            feasible = False
            conservative += 12.0 + (peak_wear - operational_limit) * 1.75
        # Wear is a percentage of a consumed tyre: 100 is fully worn and there
        # is no such state as 102% worn. The projection is accumulated
        # unclamped above because the penalty terms need the overshoot
        # magnitude to price how badly a stint is over-extended, but the
        # reported figures are what the dashboard, the OBS overlay and the
        # spoken radio call render. Publishing the raw accumulator put
        # "projects 102%" in the engineer's mouth.
        finish_wear = max(wear)
        return {
            # How long this stint runs. Implicit in lap_times_s until now, which
            # made it impossible to check that a plan's stints add up to the
            # race - the arithmetic a pre-race panel lives or dies on.
            "laps": max(0, int(laps)),
            "expected_time_s": expected,
            "conservative_time_s": conservative,
            "projected_finish_wear_pct": min(100.0, finish_wear),
            "projected_finish_wear_fl_fr_rl_rr": [
                round(min(100.0, value), 1) for value in wear
            ],
            "projected_max_wear_pct": min(100.0, peak_wear),
            # How far past a fully worn tyre the stint would run. Zero on any
            # feasible stint; positive values quantify the over-extension that
            # `feasible=False` reports qualitatively.
            "projected_wear_overshoot_pct": round(max(0.0, peak_wear - 100.0), 1),
            "feasible": feasible,
            "wear_per_lap_pct": max(wheel_rates),
            "wheel_wear_per_lap_pct": [round(value, 3) for value in wheel_rates],
            "wear_source": wear_source,
            "wear_sample_size": wear_samples,
            "deg_s_per_lap": deg,
            "deg_source": deg_source,
            "deg_sample_size": deg_samples,
            "usable_life_laps": usable_life,
            "operational_wear_limit_pct": operational_limit,
            "setup_effects": effects,
            "lap_times_s": [round(value, 3) for value in lap_times],
            "wheel_projection": wheel_projection,
        }

    @staticmethod
    def _traffic_cost(
        state: dict[str, Any],
        effective_pit_loss_s: float,
        stops: int,
    ) -> tuple[float, int]:
        current = int(state.get("player_position", 1)) or 1
        if int(stops) <= 0:
            # Staying on track has no pit-lane rejoin and cannot inherit the
            # traffic cost or lost positions of a hypothetical stop.
            return 0.0, current
        rejoin = StrategyEngine._rejoin_position(state, effective_pit_loss_s)
        positions_lost = max(0, rejoin - current)
        # The first positions in a train cost more than an isolated position because
        # the fresh-tyre benefit can be trapped behind traffic.
        cost = positions_lost * (0.32 + 0.06 * max(0, stops - 1))

        phase = str(state.get("race_control_phase", "green"))
        safety = str(state.get("safety_car", "none"))
        current_lap = int(state.get("current_lap", 0))
        total_laps = int(state.get("total_laps", 0))
        laps_remaining = max(0, total_laps - current_lap + (1 if current_lap > 0 else 0))
        late_sc = (phase in {"safety_car", "safety_car_ending"} or safety == "full") and laps_remaining <= 4
        if late_sc and positions_lost:
            # Time alone is the wrong objective near the finish: a position lost
            # during a late Safety Car may be impossible to recover. Convert that
            # classification risk into a large ranking penalty.
            per_position = 15.0 if laps_remaining <= 2 else 9.0 if laps_remaining == 3 else 5.5
            cost += positions_lost * per_position
        return round(cost, 2), rejoin

    @staticmethod
    def _monte_carlo_profile(
        plan: dict[str, Any],
        state: dict[str, Any],
        effective_pit_loss_s: float,
        samples: int,
    ) -> dict[str, Any]:
        samples = max(80, min(1200, int(samples)))
        seed_material = repr(
            (
                int(state.get("session_uid", 0)),
                int(state.get("current_lap", 0)),
                tuple(plan.get("box_laps", [])),
                tuple(plan.get("compounds", [])),
            )
        ).encode("utf-8")
        seed = zlib.crc32(seed_material) & 0xFFFFFFFF
        rng = np.random.default_rng(seed)
        expected = float(plan.get("projected_time_s", 0.0))
        stints = plan.get("stint_models", [])
        evidence = max((int(s.get("wear_sample_size", 0)) + int(s.get("deg_sample_size", 0)) for s in stints), default=0)
        uncertainty_scale = 0.45 if evidence >= 8 else 0.85 if evidence >= 3 else 1.35
        laps = sum(len(s.get("lap_times_s", [])) for s in stints)
        tyre_sigma = uncertainty_scale * math.sqrt(max(1, laps)) * 0.18
        pit_sigma = max(0.15, effective_pit_loss_s * (0.015 if state.get("race_control_phase") == "green" else 0.04)) * max(1, int(plan.get("stops_remaining", 0)))
        traffic_sigma = 0.35 * max(0, int(plan.get("projected_rejoin_position", state.get("player_position", 1))) - int(state.get("player_position", 1)))
        outcomes = expected + rng.normal(0.0, tyre_sigma, samples) + rng.normal(0.0, pit_sigma, samples) + np.abs(rng.normal(0.0, traffic_sigma, samples))
        if not plan.get("feasible", True):
            outcomes += rng.uniform(12.0, 35.0, samples)
        return {
            "samples": samples,
            "p25_s": round(float(np.quantile(outcomes, 0.25)), 2),
            "p50_s": round(float(np.quantile(outcomes, 0.50)), 2),
            "p75_s": round(float(np.quantile(outcomes, 0.75)), 2),
            "p90_s": round(float(np.quantile(outcomes, 0.90)), 2),
            "mean_s": round(float(np.mean(outcomes)), 2),
            "uncertainty_s": round(float(np.std(outcomes)), 2),
            "evidence_samples": evidence,
            "_outcome_times_s": outcomes,
        }

    @staticmethod
    def _radio_signature(plan: dict[str, Any]) -> tuple[int | None, str | None, int]:
        box_lap = plan.get("box_lap")
        if box_lap is None:
            boxes = plan.get("box_laps", []) or []
            box_lap = boxes[0] if boxes else None
        fit = plan.get("fit_compound")
        if not fit:
            compounds = plan.get("compounds", []) or []
            fit = compounds[1] if len(compounds) > 1 else None
        return (
            int(box_lap) if box_lap is not None else None,
            str(fit).upper() if fit else None,
            int(plan.get("stops_remaining", 0) or 0),
        )

    def _stabilize_radio_plan(
        self,
        state: dict[str, Any],
        previous_strategy: dict[str, Any],
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        """Keep the spoken pit call stable unless a material trigger changes it."""
        new_rec = candidate.get("recommended", {})
        old_rec = previous_strategy.get("recommended", {})
        current_lap = int(state.get("current_lap", 0))
        current_compound = str(state.get("tyre", {}).get("compound", "UNKNOWN")).upper()
        phase = str(candidate.get("neutralisation", {}).get("phase", "green"))
        if not new_rec:
            return candidate
        new_rec = dict(new_rec)
        new_rec.setdefault("committed_at_lap", current_lap)
        new_rec["source_compound"] = current_compound
        new_rec["neutralisation_phase"] = phase
        candidate["recommended"] = new_rec
        candidate["stability"] = {"held": False, "reason": "initial or material recommendation"}
        new_override = new_rec.get("driver_override", {})
        old_override = old_rec.get("driver_override", {}) if old_rec else {}
        if new_override.get("active") and new_override != old_override:
            candidate["stability"] = {"held": False, "reason": "driver strategy override changed"}
            return candidate
        if not old_rec or self._radio_signature(old_rec) == self._radio_signature(new_rec):
            if old_rec:
                new_rec["committed_at_lap"] = int(old_rec.get("committed_at_lap", current_lap))
            return candidate

        old_box, _, _ = self._radio_signature(old_rec)
        old_phase = str(old_rec.get("neutralisation_phase", previous_strategy.get("neutralisation", {}).get("phase", "green")))
        old_source = str(old_rec.get("source_compound", current_compound)).upper()
        max_wear = max([float(v) for v in state.get("tyre", {}).get("wear", [0]) or [0]])
        weather_now = bool(new_rec.get("weather_crossover")) and int(
            new_rec.get("weather_crossover", {}).get("time_offset_min", 99)
        ) <= 1
        material_trigger = (
            current_compound != old_source
            or phase != old_phase
            or weather_now
            or max_wear >= 82.0
            or (old_box is not None and current_lap > old_box)
        )
        if material_trigger:
            candidate["stability"] = {"held": False, "reason": "material race-state change"}
            return candidate

        old_sig = self._radio_signature(old_rec)
        old_ranked = next(
            (plan for plan in candidate.get("plans", []) if self._radio_signature(plan) == old_sig),
            None,
        )
        if old_ranked is None:
            candidate["stability"] = {"held": False, "reason": "previous plan no longer feasible or ranked"}
            return candidate

        committed_at = int(old_rec.get("committed_at_lap", current_lap))
        held_laps = max(0, current_lap - committed_at)
        improvement = float(old_ranked.get("risk_adjusted_time_s", 1e9)) - float(
            new_rec.get("risk_adjusted_time_s", 1e9)
        )
        if held_laps >= settings.strategy_min_hold_laps and improvement >= settings.strategy_change_min_gain_s:
            candidate["stability"] = {
                "held": False,
                "reason": "new plan materially faster",
                "gain_s": round(improvement, 2),
            }
            return candidate

        held = dict(old_rec)
        for key in (
            "projected_finish_wear_pct", "projected_finish_wear_fl_fr_rl_rr_pct",
            "risk_adjusted_time_s", "projected_time_s", "monte_carlo",
            "projected_rejoin_position", "projected_finish_position",
            "positions_lost_by_stopping", "positions_gained_vs_stay_out",
            "expected_positions_recovered", "outcome_distribution",
            "position_probabilities", "points_expected", "downside_p10",
            "upside_p90", "driver_feedback_factor", "driver_feedback_lap",
            "feedback_conflict", "stint_models", "feasible", "legal",
        ):
            if key in old_ranked:
                held[key] = old_ranked[key]
        held["committed_at_lap"] = committed_at
        held["source_compound"] = current_compound
        held["neutralisation_phase"] = phase
        candidate["raw_recommended"] = new_rec
        candidate["recommended"] = held
        candidate["stability"] = {
            "held": True,
            "reason": "prevented non-material strategy flap",
            "candidate_gain_s": round(improvement, 2),
            "held_laps": held_laps,
            "required_gain_s": settings.strategy_change_min_gain_s,
        }
        return candidate

    def _snapshot_key(self, state: dict[str, Any], plan: dict[str, Any]) -> tuple[Any, ...]:
        """Identity of a strategy snapshot for persistence de-duplication.

        Two snapshots that would answer every later question identically share a
        key: same session, lap, race-control phase, spoken call, confidence and
        outstanding-compound status. Monte Carlo jitter between ticks does not.
        """
        recommended = plan.get("recommended", {}) or {}
        return (
            int(state.get("session_uid", 0) or 0),
            int(state.get("current_lap", 0) or 0),
            str(state.get("race_control_phase", "green")),
            self._radio_signature(recommended),
            int(recommended.get("projected_finish_position", 0) or 0),
            str(plan.get("strategy_risk_appetite", "balanced")),
            str(plan.get("confidence") or ""),
            bool((plan.get("compound_rule") or {}).get("change_outstanding")),
        )

    async def recompute(self) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        previous_strategy = dict(state.get("strategy", {}) or {})
        historical = await self.database.tyre_history_model(
            int(state.get("track_id", -1)), context=state
        )
        plan = self.compute(state, historical)
        plan = self._stabilize_radio_plan(state, previous_strategy, plan)
        await self.store.update(strategy=plan)
        key = self._snapshot_key(state, plan)
        if key != self._last_snapshot_key:
            self._last_snapshot_key = key
            await self.database.save_strategy_snapshot(state, plan)
        return plan

    def compute(
        self,
        state: dict[str, Any],
        historical: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        historical = historical or {"compounds": {}}
        current_lap = int(state.get("current_lap", 0))
        total_laps = int(state.get("total_laps", 0))
        # Include the current lap because telemetry reports the lap in progress.
        # At lap 3/22 there are 20 lap completions left, not 19.
        remaining = max(
            0,
            total_laps - current_lap + (1 if current_lap > 0 else 0),
        )
        current_compound = str(state.get("tyre", {}).get("compound", "UNKNOWN")).upper()
        current_age = int(state.get("tyre", {}).get("age_laps", 0))
        current_wear = list(state.get("tyre", {}).get("wear", [0.0, 0.0, 0.0, 0.0]) or [0.0, 0.0, 0.0, 0.0])
        planned_start = self._planned_start_compound(state, current_compound, current_age)
        if planned_start:
            # Before the lights, the starting tyre is still the driver's to
            # choose - they fit it in the garage. Modelling the race they intend
            # to drive is the only way to answer "what does starting on softs
            # cost me", which is the whole point of planning beforehand.
            current_compound = planned_start
            current_age = 0
            current_wear = [0.0, 0.0, 0.0, 0.0]
        game = state.get("strategy", {})
        game_ideal = int(game.get("game_ideal_lap", game.get("ideal_lap", 0)) or 0)
        game_latest = int(game.get("game_latest_lap", game.get("latest_lap", 0)) or 0)
        game_status = "unavailable"
        if game_ideal:
            if current_lap > game_latest > 0:
                game_status = "expired"
            elif current_lap >= game_ideal:
                game_status = "open"
            else:
                game_status = "upcoming"

        mode = str(state.get("mode_profile", "idle"))
        base_rule = self._compound_rule(state)
        neutralisation = self._neutralisation(state)
        if remaining <= 0 or total_laps <= 0 or mode not in {"race", "sprint"}:
            return {
                "available": False,
                "reason": (
                    "No race-distance tyre strategy applies in this session."
                    if mode not in {"race", "sprint"}
                    else "Race distance is not available yet."
                ),
                "compound_rule": base_rule,
                "neutralisation": neutralisation,
                "game_window": {
                    "ideal_lap": game_ideal,
                    "latest_lap": game_latest,
                    "status": game_status,
                },
                "recommended": {},
                "plans": [],
            }

        base_lap_s = self._estimate_base_lap_s(state)
        effective_pit_loss = float(neutralisation["effective_pit_loss_s"])
        available_sets = self._available_sets(state)
        compounds = list(available_sets)
        weather_crossover = self._weather_crossover(
            state, base_lap_s, effective_pit_loss
        )
        style_factor, style_evidence = self._driver_wear_factor(state, historical)
        feedback_adjustment = self._driver_feedback_adjustment(
            state, current_compound
        )
        current_max_wear = max([float(value) for value in current_wear] or [0.0])
        feedback_conflict = bool(
            feedback_adjustment.get("active")
            and (
                (
                    feedback_adjustment.get("category") == "tyres_gone"
                    and current_max_wear <= 45.0
                )
                or (
                    feedback_adjustment.get("category") == "tyres_fine"
                    and current_max_wear >= 75.0
                )
            )
        )
        plans: list[dict[str, Any]] = []
        simulation_cache: dict[tuple[Any, ...], dict[str, Any]] = {}
        baseline_simulation_cache: dict[tuple[Any, ...], dict[str, Any]] = {}
        feedback_free_state = dict(state)
        feedback_free_state["driver_tyre_feedback"] = {}

        def simulate(
            state_arg: dict[str, Any],
            compound: str,
            laps: int,
            starting_age: int,
            starting_wear: float | list[float],
            base_lap: float,
            history: dict[str, Any],
            personal_factor: float,
            set_info: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            wear_key = (
                tuple(round(float(value), 3) for value in starting_wear)
                if isinstance(starting_wear, list)
                else round(float(starting_wear), 3)
            )
            set_key = tuple(
                sorted(
                    (str(key), str(value))
                    for key, value in (set_info or {}).items()
                    if key in {
                        "compound", "wear_pct", "usable_life_laps",
                        "life_span_laps", "lap_delta_ms", "source",
                    }
                )
            )
            key = (
                str(compound), int(laps), int(starting_age), wear_key,
                round(float(base_lap), 3), round(float(personal_factor), 4), set_key,
            )
            if key not in simulation_cache:
                simulation_cache[key] = self._simulate_stint(
                    state_arg, compound, laps, starting_age, starting_wear,
                    base_lap, history, personal_factor, set_info,
                )
            result = dict(simulation_cache[key])
            if feedback_adjustment.get("active"):
                if key not in baseline_simulation_cache:
                    baseline_simulation_cache[key] = self._simulate_stint(
                        feedback_free_state,
                        compound,
                        laps,
                        starting_age,
                        starting_wear,
                        base_lap,
                        history,
                        personal_factor,
                        set_info,
                    )
                result["without_driver_feedback"] = baseline_simulation_cache[key]
            return result

        def make_plan(
            *,
            stops: int,
            box_laps: list[int],
            compounds_in_plan: list[str],
            stints: list[dict[str, Any]],
            reason: str,
            weather: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            traffic_cost, projected_rejoin = self._traffic_cost(
                state, effective_pit_loss, stops
            )
            expected = (
                sum(float(stint["expected_time_s"]) for stint in stints)
                + stops * effective_pit_loss
                + traffic_cost
            )
            conservative = (
                sum(float(stint["conservative_time_s"]) for stint in stints)
                + stops * effective_pit_loss
                + traffic_cost * 1.35
            )
            legality = self._compound_rule(state, compounds_in_plan[1:])
            feedback_feasible = all(bool(stint["feasible"]) for stint in stints) and legality["compliant"]
            baseline_stints = [
                stint.get("without_driver_feedback", stint) for stint in stints
            ]
            baseline_feasible = (
                all(bool(stint["feasible"]) for stint in baseline_stints)
                and legality["compliant"]
            )
            # Perception is useful evidence, but cannot by itself declare a
            # telemetry-feasible plan unsafe (or rescue an unsafe plan). It may
            # move timing and risk inside candidates whose deterministic
            # no-feedback classification remains equivalent.
            feasible = baseline_feasible if feedback_adjustment.get("active") else feedback_feasible
            baseline_expected = (
                sum(float(stint["expected_time_s"]) for stint in baseline_stints)
                + stops * effective_pit_loss
                + traffic_cost
            )
            baseline_conservative = (
                sum(float(stint["conservative_time_s"]) for stint in baseline_stints)
                + stops * effective_pit_loss
                + traffic_cost * 1.35
            )
            return {
                "stops_remaining": stops,
                "box_laps": box_laps,
                "compounds": compounds_in_plan,
                "projected_time_s": round(expected, 2),
                "risk_adjusted_time_s": round(conservative, 2),
                "projected_finish_wear_pct": round(
                    float(stints[-1]["projected_finish_wear_pct"]), 1
                ),
                "projected_max_wear_pct": round(
                    max(float(stint["projected_max_wear_pct"]) for stint in stints), 1
                ),
                "feasible": feasible,
                "feedback_feasible": feedback_feasible,
                "feasible_without_driver_feedback": baseline_feasible,
                "projected_time_without_driver_feedback_s": round(
                    baseline_expected, 2
                ),
                "risk_adjusted_time_without_driver_feedback_s": round(
                    baseline_conservative, 2
                ),
                "feedback_guard_applied": bool(
                    feedback_adjustment.get("active")
                    and feedback_feasible != baseline_feasible
                ),
                "legal": legality["compliant"],
                "compound_rule": legality,
                "reason": reason,
                "traffic_cost_s": traffic_cost,
                "projected_rejoin_position": projected_rejoin,
                "driver_feedback_factor": feedback_adjustment.get(
                    "wear_factor", 1.0
                ),
                "driver_feedback_deg_factor": feedback_adjustment.get(
                    "deg_factor", 1.0
                ),
                "driver_feedback_lap": feedback_adjustment.get("lap"),
                "driver_feedback_category": feedback_adjustment.get("category"),
                "feedback_conflict": feedback_conflict,
                "stint_models": stints,
                **({"weather_crossover": weather} if weather else {}),
            }

        no_stop = simulate(
            state,
            current_compound,
            remaining,
            current_age,
            current_wear,
            base_lap_s,
            historical,
            style_factor,
            None,
        )
        plans.append(
            make_plan(
                stops=0,
                box_laps=[],
                compounds_in_plan=[current_compound],
                stints=[no_stop],
                reason="Stay out to the finish",
            )
        )

        red_flag_change = neutralisation["phase"] == "red_flag"
        earliest_box_lap = current_lap
        if neutralisation["pit_entry_status"] == "passed" and not red_flag_change:
            earliest_box_lap += 1
        for box_lap in range(earliest_box_lap, total_laps):
            # A normal stop at the end of the current lap still consumes that
            # lap on the fitted tyre. A red-flag change happens during the
            # suspension and therefore consumes zero additional racing laps.
            pre_laps = (
                0
                if red_flag_change and box_lap == current_lap
                else box_lap - current_lap + 1
            )
            post_laps = remaining - pre_laps
            if post_laps <= 0:
                continue
            pre = simulate(
                state,
                current_compound,
                pre_laps,
                current_age,
                current_wear,
                base_lap_s,
                historical,
                style_factor,
                None,
            )
            for compound in compounds:
                if compound == current_compound and len(compounds) > 1:
                    continue
                post = simulate(
                    state,
                    compound,
                    post_laps,
                    0,
                    0.0,
                    base_lap_s,
                    historical,
                    style_factor,
                    available_sets.get(compound),
                )
                reason = (
                    f"Fit {compound} during the red flag"
                    if neutralisation["phase"] == "red_flag"
                    else f"Box lap {box_lap} for {compound}"
                )
                plans.append(
                    make_plan(
                        stops=1,
                        box_laps=[box_lap],
                        compounds_in_plan=[current_compound, compound],
                        stints=[pre, post],
                        reason=reason,
                    )
                )

        if remaining >= 18 and not red_flag_change:
            for first_box_lap in range(earliest_box_lap, total_laps - 6, 2):
                first_laps = first_box_lap - current_lap + 1
                for second_box_lap in range(first_box_lap + 5, total_laps, 3):
                    middle_laps = second_box_lap - first_box_lap
                    # Derived from what is left to run, as the one-stop branch
                    # does, rather than from total_laps. Counting down from the
                    # race distance assumes the current lap is in progress; on
                    # the grid it has not started, so the stints summed to
                    # total_laps + 1 and every multi-stop plan carried a phantom
                    # lap. Same state at lap 0 and lap 1 projected P20 and P5.
                    final_laps = remaining - first_laps - middle_laps
                    if final_laps <= 0:
                        continue
                    for middle in compounds:
                        for final in compounds:
                            if middle == current_compound or final == middle:
                                continue
                            first = simulate(
                                state,
                                current_compound,
                                first_laps,
                                current_age,
                                current_wear,
                                base_lap_s,
                                historical,
                                style_factor,
                                None,
                            )
                            middle_stint = simulate(
                                state,
                                middle,
                                middle_laps,
                                0,
                                0.0,
                                base_lap_s,
                                historical,
                                style_factor,
                                available_sets.get(middle),
                            )
                            final_stint = simulate(
                                state,
                                final,
                                final_laps,
                                0,
                                0.0,
                                base_lap_s,
                                historical,
                                style_factor,
                                available_sets.get(final),
                            )
                            plans.append(
                                make_plan(
                                    stops=2,
                                    box_laps=[first_box_lap, second_box_lap],
                                    compounds_in_plan=[current_compound, middle, final],
                                    stints=[first, middle_stint, final_stint],
                                    reason=f"Box laps {first_box_lap} and {second_box_lap}",
                                )
                            )

        if (
            settings.strategy_max_stops >= 3
            and remaining >= 28
            and not red_flag_change
        ):
            # Coarse but complete three-stop search. It is intentionally sampled
            # rather than lap-by-lap to keep live recomputes fast.
            first_candidates = range(earliest_box_lap + 4, total_laps - 15, 4)
            for first_box_lap in first_candidates:
                first_laps = first_box_lap - current_lap + 1
                for second_box_lap in range(first_box_lap + 6, total_laps - 8, 5):
                    middle1_laps = second_box_lap - first_box_lap
                    for third_box_lap in range(second_box_lap + 6, total_laps, 5):
                        middle2_laps = third_box_lap - second_box_lap
                        # From what remains, not from the race distance - see
                        # the two-stop branch above for why.
                        final_laps = remaining - first_laps - middle1_laps - middle2_laps
                        if min(first_laps, middle1_laps, middle2_laps, final_laps) <= 0:
                            continue
                        for compound1 in compounds:
                            for compound2 in compounds:
                                for compound3 in compounds:
                                    if compound1 == current_compound or compound2 == compound1 or compound3 == compound2:
                                        continue
                                    stints = [
                                        simulate(state, current_compound, first_laps, current_age, current_wear, base_lap_s, historical, style_factor, None),
                                        simulate(state, compound1, middle1_laps, 0, 0.0, base_lap_s, historical, style_factor, available_sets.get(compound1)),
                                        simulate(state, compound2, middle2_laps, 0, 0.0, base_lap_s, historical, style_factor, available_sets.get(compound2)),
                                        simulate(state, compound3, final_laps, 0, 0.0, base_lap_s, historical, style_factor, available_sets.get(compound3)),
                                    ]
                                    plans.append(
                                        make_plan(
                                            stops=3,
                                            box_laps=[first_box_lap, second_box_lap, third_box_lap],
                                            compounds_in_plan=[current_compound, compound1, compound2, compound3],
                                            stints=stints,
                                            reason=f"Box laps {first_box_lap}, {second_box_lap} and {third_box_lap}",
                                        )
                                    )

        weather_plan: dict[str, Any] | None = None
        # A crossover that cannot repay its pit loss still describes the
        # conditions, but it is not a stop: it must not become a plan.
        if (
            weather_crossover is not None
            and weather_crossover.get("box_lap") is not None
        ):
            box_lap = int(weather_crossover["box_lap"])
            fit = str(weather_crossover["compound"])
            first_laps = max(1, box_lap - current_lap + 1)
            pre = simulate(
                state,
                current_compound,
                first_laps,
                current_age,
                current_wear,
                base_lap_s,
                historical,
                style_factor,
                None,
            )
            post = simulate(
                state,
                fit,
                max(0, remaining - first_laps),
                0,
                0.0,
                base_lap_s,
                historical,
                style_factor,
                available_sets.get(fit),
            )
            weather_plan = make_plan(
                stops=1,
                box_laps=[box_lap],
                compounds_in_plan=[current_compound, fit],
                stints=[pre, post],
                reason=str(weather_crossover["reason"]),
                weather=weather_crossover,
            )
            # Wet use waives the dry-compound requirement.
            weather_plan["feasible"] = bool(pre["feasible"] and post["feasible"])
            weather_plan["legal"] = True
            plans.append(weather_plan)

        track_id = int(state.get("track_id", -1))
        difficulty_known = track_id in TRACK_OVERTAKING_DIFFICULTY
        overtaking_difficulty = TRACK_OVERTAKING_DIFFICULTY.get(track_id, 0.60)
        rival_projections = self._project_rival_finish_times(
            state,
            historical,
            remaining,
            base_lap_s,
            effective_pit_loss,
        )
        for plan in plans:
            self._annotate_finish_projection(
                plan,
                state,
                rival_projections,
                overtaking_difficulty,
                difficulty_known,
            )
            if feedback_adjustment.get("active"):
                baseline_plan = dict(plan)
                baseline_plan["projected_time_s"] = plan[
                    "projected_time_without_driver_feedback_s"
                ]
                baseline_plan["risk_adjusted_time_s"] = plan[
                    "risk_adjusted_time_without_driver_feedback_s"
                ]
                baseline_plan["stint_models"] = [
                    stint.get("without_driver_feedback", stint)
                    for stint in plan.get("stint_models", [])
                ]
                self._annotate_finish_projection(
                    baseline_plan,
                    feedback_free_state,
                    rival_projections,
                    overtaking_difficulty,
                    difficulty_known,
                )
                plan["projected_finish_position_without_driver_feedback"] = (
                    baseline_plan["projected_finish_position"]
                )
                plan["projected_points_without_driver_feedback"] = baseline_plan[
                    "projected_points"
                ]
        stay_out_finish = int(plans[0].get("projected_finish_position", 0) or 0)
        for plan in plans:
            projected = int(plan.get("projected_finish_position", 0) or 0)
            plan["positions_gained_vs_stay_out"] = (
                stay_out_finish - projected if stay_out_finish and projected else 0
            )

        feasible = [plan for plan in plans if plan["feasible"] and plan["legal"]]
        deterministic = sorted(
            feasible or plans,
            key=lambda plan: (
                # Legality outranks pace. Skipping a mandatory compound change
                # always projects a better finish because it skips a pit stop,
                # so ranking on position alone selected a plan that finishes
                # the race and is then disqualified. The filter above drops
                # illegal plans, but the stay-out anchor is re-added below and
                # the whole list is used when nothing is both feasible and
                # legal, so the ordering has to enforce this too.
                not plan.get("legal", True),
                not plan.get("feasible", False),
                int(
                    plan.get(
                        "projected_finish_position_without_driver_feedback",
                        plan.get("projected_finish_position", 99),
                    )
                ),
                int(plan.get("projected_finish_position", 99)),
                -int(plan.get("projected_points", 0)),
                float(plan["risk_adjusted_time_s"]),
            ),
        )
        # Run uncertainty analysis only on credible candidates; this keeps live
        # strategy recomputes bounded even when the full enumeration is large.
        shortlisted = deterministic[: min(48, len(deterministic))]
        # Stay-out is the comparison anchor even when it is too slow to make the
        # first time-based cut. An imminent weather plan must also survive.
        for required in (plans[0], weather_plan):
            if required is not None and all(required is not item for item in shortlisted):
                shortlisted.append(required)
        # So must whatever the driver actually asked for. The shortlist is the
        # fastest 48 of an enumeration that runs into the thousands, and a
        # specific request - "box lap 30 for hards", or a committed two-stop -
        # is chosen for reasons the ranking key does not model, so it is almost
        # never in that 48. Without this the override matched nothing and fell
        # back to the nearest shortlisted plan, which is how asking for lap 30
        # produced lap 27 and a warning that the lap was unavailable when it
        # existed all along.
        driver_override = dict(state.get("strategy_override", {}) or {})
        for required in self._driver_requested_plans(
            plans, driver_override, state, current_lap
        ):
            if all(required is not item for item in shortlisted):
                shortlisted.append(required)
        # One representative of every race shape, so a driver choosing between a
        # one-stop and a two-stop is comparing like with like rather than one
        # modelled plan against an absence.
        for required in self._best_per_shape(plans):
            if all(required is not item for item in shortlisted):
                shortlisted.append(required)
        for plan in shortlisted:
            plan["monte_carlo"] = self._monte_carlo_profile(
                plan,
                state,
                effective_pit_loss,
                settings.strategy_monte_carlo_samples,
            )
            outcome_times = plan["monte_carlo"].pop("_outcome_times_s")
            plan.update(
                self._position_distribution(
                    plan, state, rival_projections, outcome_times
                )
            )
            risk_key = "p75_s" if settings.strategy_risk_quantile >= 0.70 else "p50_s"
            plan["risk_adjusted_time_s"] = plan["monte_carlo"][risk_key]
        risk_appetite = str(
            state.get("strategy_risk_appetite")
            or state.get("driver_preferences", {}).get(
                "strategy_risk_appetite", "balanced"
            )
            or "balanced"
        ).lower()
        if risk_appetite not in {"conservative", "balanced", "aggressive"}:
            risk_appetite = "balanced"

        def ranking_key(plan: dict[str, Any]) -> tuple[Any, ...]:
            if risk_appetite == "conservative":
                appetite_position = int(
                    plan.get("downside_p90_position", plan.get("projected_finish_position", 99))
                )
                appetite_points = points_for_position(appetite_position)
            elif risk_appetite == "aggressive":
                appetite_position = int(
                    plan.get("upside_p10_position", plan.get("projected_finish_position", 99))
                )
                appetite_points = points_for_position(appetite_position)
            else:
                appetite_position = int(plan.get("projected_finish_position", 99))
                appetite_points = float(plan.get("points_expected", 0.0))
            # Classification remains primary for every appetite: an optimistic
            # tail cannot make a projected P18 beat a projected P10. Appetite
            # selects the gamble only among plans with the same central finish.
            return (
                # A disqualified car scores nothing, so an illegal plan can
                # never outrank a legal one whatever the appetite says.
                not plan.get("legal", True),
                not plan.get("feasible", False),
                int(
                    plan.get(
                        "projected_finish_position_without_driver_feedback",
                        plan.get("projected_finish_position", 99),
                    )
                ),
                int(plan.get("projected_finish_position", 99)),
                appetite_position,
                -float(appetite_points),
                float(plan.get("risk_adjusted_time_s", 1e9)),
                int(plan.get("stops_remaining", 9)),
            )
        ranked = sorted(
            shortlisted,
            key=ranking_key,
        )[:8]
        force_weather = bool(
            weather_plan is not None
            and int(weather_plan.get("weather_crossover", {}).get("time_offset_min", 99)) <= 1
        )
        automatic_best = ranked[0]
        override_match: dict[str, Any] | None = None
        override_warning = ""
        plan_tail: dict[str, Any] | None = None
        if driver_override.get("enabled") and driver_override.get("locked") and not force_weather:
            full_plan = driver_override.get("plan") or {}
            if full_plan.get("compounds"):
                # A whole-race plan constrains every stop that is left, not just
                # the next one. It is re-based onto the remaining race first, or
                # it would stop matching anything the moment the first stop is
                # made and the driver would be told their plan is running while
                # the engine ranked freely.
                plan_tail = remaining_plan(
                    full_plan,
                    self._completed_stops(state),
                    current_lap,
                )
                plan_candidates = [
                    plan for plan in shortlisted if plan_matches(plan, plan_tail)
                ]
                safe_plan = [
                    plan
                    for plan in plan_candidates
                    if plan.get("legal") and plan.get("feasible")
                ]
                if safe_plan:
                    override_match, override_warning = self._closest_to_plan(
                        safe_plan, plan_tail, ranking_key
                    )
                elif plan_candidates:
                    legal_plan = [plan for plan in plan_candidates if plan.get("legal")]
                    override_match = min(legal_plan or plan_candidates, key=ranking_key)
                    override_warning = (
                        "Your plan is outside the operational wear margin."
                        if legal_plan
                        else "Your plan does not serve the mandatory compound change."
                    )
                else:
                    override_warning = (
                        "The race has moved past your plan; running the best "
                        "available strategy instead."
                    )

        if (
            driver_override.get("enabled")
            and driver_override.get("locked")
            and not force_weather
            and plan_tail is None
        ):
            requested_lap = driver_override.get("next_box_lap")
            requested_compound = str(driver_override.get("next_compound") or "").upper()
            requested_stops = driver_override.get("preferred_stops")
            candidates = list(shortlisted)
            if requested_compound:
                candidates = [
                    plan for plan in candidates
                    if len(plan.get("compounds", [])) > 1
                    and str(plan.get("compounds", [None, None])[1]).upper() == requested_compound
                ]
            if requested_stops is not None:
                candidates = [plan for plan in candidates if int(plan.get("stops_remaining", -1)) == int(requested_stops)]
            exact = candidates
            if requested_lap is not None:
                exact = [
                    plan for plan in candidates
                    if plan.get("box_laps") and int(plan.get("box_laps", [0])[0]) == max(current_lap, int(requested_lap))
                ]
            safe_exact = [plan for plan in exact if plan.get("legal") and plan.get("feasible")]
            if safe_exact:
                override_match = min(safe_exact, key=ranking_key)
            elif exact:
                legal_exact = [plan for plan in exact if plan.get("legal")]
                override_match = min(legal_exact or exact, key=ranking_key)
                override_warning = "Driver plan is outside the operational wear margin."
            elif candidates:
                override_match = min(
                    candidates,
                    key=lambda plan: (
                        abs(int((plan.get("box_laps") or [current_lap])[0]) - int(requested_lap or current_lap)),
                        ranking_key(plan),
                    ),
                )
                override_warning = "Exact requested lap was unavailable; nearest legal matching plan selected."
            else:
                override_warning = "No legal plan matches the driver override."

        best = weather_plan if force_weather else (override_match or automatic_best)
        second = next((plan for plan in ranked if plan is not best), best)
        best = dict(best)
        if driver_override.get("enabled"):
            best["driver_override"] = {
                "active": True,
                "honored": bool(override_match is not None),
                "requested": driver_override,
                "warning": override_warning,
                "delta_vs_automatic_s": round(
                    float(best.get("risk_adjusted_time_s", 0.0))
                    - float(automatic_best.get("risk_adjusted_time_s", 0.0)), 2
                ),
            }
            if plan_tail is not None:
                # What is left of the plan, and whether it is actually driving
                # the recommendation. "Plan active" on the dashboard has to mean
                # the plan won, not merely that one was entered.
                best["driver_override"]["plan_remaining"] = plan_tail
                best["driver_override"]["following_plan"] = bool(
                    override_match is not None
                )
        best["delta_to_next_s"] = round(
            float(second["risk_adjusted_time_s"]) - float(best["risk_adjusted_time_s"]),
            2,
        )
        best["position_delta_to_next"] = int(
            second.get("projected_finish_position", 0) or 0
        ) - int(best.get("projected_finish_position", 0) or 0)

        if best["stops_remaining"]:
            box_lap = best["box_laps"][0]
            fit = best["compounds"][1]
            if neutralisation["phase"] == "red_flag":
                instruction = f"During the red flag, fit {fit} for the restart."
            else:
                instruction = f"Box lap {box_lap} for {fit}."
            current_projection = plans[0]["projected_finish_wear_pct"]
            tyre_reason = (
                str(best["weather_crossover"]["reason"])
                if best.get("weather_crossover")
                else (
                    f"Your current wear model projects {current_projection:.0f}% by the finish; "
                    f"the {fit.lower()} stint projects {best['projected_finish_wear_pct']:.0f}%."
                )
            )
        else:
            box_lap = None
            fit = None
            instruction = "Stay out to the finish."
            tyre_reason = f"Current {current_compound.lower()}s project to {best['projected_finish_wear_pct']:.0f}% at the finish."
        # Any recommendation that cannot satisfy the mandatory compound change
        # ends the race in disqualification. That applies whether the plan
        # stays out or stops: a stop that refits the same dry compound serves
        # nothing, so "Box lap 18 for HARD." sounded like a normal call while
        # still heading for a DQ. The warning therefore wraps both branches.
        if not best.get("legal", True):
            rule_state = self._compound_rule(state)
            eligible = [
                compound
                for compound in rule_state.get("eligible_next_compounds", [])
                if compound in available_sets
            ]
            if eligible:
                instruction = (
                    "Warning: the mandatory dry-compound change is still "
                    f"outstanding. Box for {eligible[0]} before the flag or "
                    "the result is a disqualification."
                )
            else:
                instruction = (
                    "Warning: the mandatory dry-compound change cannot be "
                    "served - no eligible dry set is available. The race "
                    "finishes under threat of disqualification."
                )
            tyre_reason = (
                f"{rule_state.get('dry_count', 0)} of "
                f"{rule_state.get('required_dry_count', 2)} dry compounds used. "
                + tyre_reason
            )

        evidence_samples = max(
            int(style_evidence.get("sample_size", 0)),
            max(
                (
                    int(stint.get("deg_sample_size", 0))
                    for stint in best.get("stint_models", [])
                ),
                default=0,
            ),
        )
        confidence = (
            "high"
            if evidence_samples >= 8
            else "medium"
            if evidence_samples >= 3
            else "low"
        )
        stay_out_plan = plans[0]
        stay_out_comparable = bool(
            stay_out_plan.get("feasible") and stay_out_plan.get("legal")
        )
        stay_out_risk = float(stay_out_plan["risk_adjusted_time_s"])
        net_gain_vs_stay_out = (
            stay_out_risk - float(best["risk_adjusted_time_s"])
            if stay_out_comparable
            else None
        )
        stop_required_reason = (
            "mandatory compound change"
            if not stay_out_plan.get("legal")
            else "current tyre cannot reach the finish inside the operational wear margin"
            if not stay_out_plan.get("feasible")
            else "best projected finishing position and expected-points outcome"
        )
        defence = self.defence_assessment(state)
        if best.get("stops_remaining"):
            rationale = (
                f"Projects P{best.get('projected_finish_position')} after rejoining "
                f"P{best.get('projected_rejoin_position')}, with about "
                f"{best.get('expected_positions_recovered', 0)} positions recoverable."
            )
        else:
            best_stop_alternative = next(
                (plan for plan in ranked if int(plan.get("stops_remaining", 0) or 0) > 0),
                None,
            )
            avoided_positions = int(
                (best_stop_alternative or {}).get("positions_lost_by_stopping", 0)
                or 0
            )
            rationale = (
                f"Staying out protects projected P{best.get('projected_finish_position')} "
                f"and avoids losing {avoided_positions} positions in the lane."
            )
        if feedback_adjustment.get("active"):
            rationale += (
                f" Driver report from lap {feedback_adjustment.get('lap')} is weighted "
                f"at {float(feedback_adjustment.get('weight', 0.0)):.0%}."
            )
        change_condition = (
            "The call changes for a safety car, red flag, wet crossover, new damage, "
            "or a hard tyre-wear limit breach."
        )
        selected_stint = (
            best.get("stint_models", [])[-1] if best.get("stint_models") else {}
        )
        model_summary = {
            "confidence": confidence,
            "evidence_samples": evidence_samples,
            "personal_style_factor": style_evidence.get("factor", 1.0),
            "personal_style_source": style_evidence.get("source", "track_default"),
            "limiting_wear_per_lap_pct": style_evidence.get(
                "observed_limiting_wear_per_lap_pct"
            ),
            "selected_stint_wear_per_lap_pct": round(
                float(selected_stint.get("wear_per_lap_pct", 0.0)), 3
            ),
            "selected_stint_wear_source": selected_stint.get("wear_source"),
            "selected_stint_deg_s_per_lap": round(
                float(selected_stint.get("deg_s_per_lap", 0.0)), 3
            ),
            "selected_stint_deg_source": selected_stint.get("deg_source"),
            "driver_feedback_factor": feedback_adjustment.get("wear_factor", 1.0),
            "driver_feedback_deg_factor": feedback_adjustment.get("deg_factor", 1.0),
            "driver_feedback_lap": feedback_adjustment.get("lap"),
            "feedback_conflict": feedback_conflict,
        }
        return {
            "available": True,
            "laps_remaining": remaining,
            "pit_loss_s": round(effective_pit_loss, 1),
            "neutralisation": neutralisation,
            # Surfaced even when no stop is called. A driver on slicks in the
            # rain must still learn the conditions were seen and why staying
            # out is the call, rather than hearing nothing about the weather.
            **(
                {"weather_crossover": weather_crossover}
                if weather_crossover is not None
                else {}
            ),
            "compound_rule": self._compound_rule(state, best.get("compounds", [])[1:]),
            "game_window": {
                "ideal_lap": game_ideal,
                "latest_lap": game_latest,
                "rejoin_position": int(
                    game.get("game_rejoin_position", game.get("rejoin_position", 0))
                    or 0
                ),
                "status": game_status,
            },
            "recommended": {
                **best,
                "box_lap": box_lap,
                "fit_compound": fit,
                "instruction": instruction,
                "tyre_reason": tyre_reason,
                "projected_rejoin_position": (
                    self._rejoin_position(state, effective_pit_loss)
                    if best.get("stops_remaining")
                    else int(state.get("player_position", 0) or 0)
                ),
                "net_gain_vs_stay_out_s": (
                    round(net_gain_vs_stay_out, 2)
                    if net_gain_vs_stay_out is not None
                    else None
                ),
                "stop_required_reason": stop_required_reason,
                "rationale": rationale,
            "call_changes_if": change_condition,
            "change_condition": change_condition,
                "driver_feedback_factor": feedback_adjustment.get(
                    "wear_factor", 1.0
                ),
                "driver_feedback_lap": feedback_adjustment.get("lap"),
                "feedback_conflict": feedback_conflict,
                "defence": defence if not best.get("stops_remaining") else {},
            },
            "plans": ranked[:5],
            # The best plan of each shape - one stop, two, three - so a driver
            # can see what a different race costs them. "plans" alone cannot
            # answer that: it is the top five by rank, and on the grid all five
            # were flavours of the same two-stop, which leaves nothing to
            # actually discuss.
            "shapes": self._race_shapes(
                shortlisted,
                ranking_key,
                allow_wet=(
                    str(state.get("weather", "Unknown"))
                    in {"Light rain", "Heavy rain", "Storm"}
                    or int(state.get("rain_next_15_pct", 0) or 0) >= 40
                    or current_compound in WET_COMPOUNDS
                ),
            ),
            "confidence": confidence,
            "strategy_risk_appetite": risk_appetite,
            "rival_finish_projections": rival_projections,
            "defence": defence,
            "model_summary": model_summary,
            "personal_wear_model": style_evidence,
            "assumptions": [
                "Plans are ranked by projected finishing position first; elapsed time only breaks classification ties.",
                f"Risk appetite is {risk_appetite}; it selects the distribution used between plans with the same central finish.",
                f"Overtaking difficulty is {overtaking_difficulty:.2f}; unknown circuits use 0.60 with lower confidence.",
                "Live and historical personal wear/deg data override track defaults as samples accumulate.",
                "A dry Race plan must finish with at least two different dry visual compounds unless inters or wets are used.",
                "SC/VSC loss is an estimate; pit-entry position, traffic and field compression are recalculated from live state.",
            ],
        }

    async def get_plan(self) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        current = state.get("strategy", {})
        if current.get("recommended") or current.get("available") is False:
            return current
        return await self.recompute()

    async def predict_rival_strategy(self, top_n: int = 6) -> dict[str, Any]:
        """Estimate each nearby rival's likely next stop and undercut threat.

        The estimate is deterministic: a rival on a compound with typical stint
        life L who is A laps into that set is projected to stop in L - A laps.
        Anyone projected to stop within a couple of laps who sits just behind is
        flagged as an undercut threat before they actually box.
        """
        state = await self.store.snapshot_analysis()
        current_lap = int(state.get("current_lap", 0))
        total_laps = int(state.get("total_laps", 0))
        player_pos = int(state.get("player_position", 0))
        rivals: list[dict[str, Any]] = []
        for driver in state.get("drivers", []):
            if int(driver.get("car_idx", -1)) == int(state.get("player_car_index", -1)):
                continue
            gap = driver.get("gap_to_player_s")
            compound = str(driver.get("tyre_compound", "UNKNOWN")).upper()
            age = int(driver.get("tyre_age", 0))
            typical = TYPICAL_STINT_LAPS.get(compound)
            laps_to_stop = max(0, typical - age) if typical else None
            projected_stop_lap = (
                current_lap + laps_to_stop
                if laps_to_stop is not None and current_lap > 0
                else None
            )
            # gap_to_player_s is positive for a car behind, negative for a car
            # ahead. A car just behind on dying tyres is the classic undercut.
            behind = gap is not None and float(gap) > 0
            # A car just behind on tyres near the end of their life is the
            # classic pre-emptive undercut threat.
            undercut_threat = bool(
                behind
                and laps_to_stop is not None
                and laps_to_stop <= 2
                and abs(float(gap)) <= 3.0
            )
            rivals.append(
                {
                    "driver": driver.get("name"),
                    "position": driver.get("position"),
                    "gap_to_player_s": round(float(gap), 2) if gap is not None else None,
                    "compound": compound,
                    "tyre_age": age,
                    "typical_stint_laps": typical,
                    "laps_until_estimated_stop": laps_to_stop,
                    "projected_stop_lap": (
                        projected_stop_lap
                        if projected_stop_lap is None or projected_stop_lap <= total_laps or total_laps == 0
                        else None
                    ),
                    "undercut_threat": undercut_threat,
                }
            )
        rivals.sort(
            key=lambda item: abs(item["gap_to_player_s"])
            if item["gap_to_player_s"] is not None
            else 1e9
        )
        return {
            "available": bool(rivals),
            "player_position": player_pos,
            "current_lap": current_lap,
            "rivals": rivals[: max(1, top_n)],
            "note": (
                "Stop laps are estimated from typical compound life; the game AI "
                "may pit earlier or later."
            ),
        }

    async def championship_scenario(self) -> dict[str, Any]:
        """Attach F1 points to each ranked plan's projected finish position so
        the trade-off between a safe finish and an aggressive one is explicit.
        """
        current = await self.store.snapshot_analysis()
        strategy = current.get("strategy", {})
        plans = strategy.get("plans", []) or []
        player_pos = int(current.get("player_position", 0))
        scored: list[dict[str, Any]] = []
        for plan in plans:
            projected = int(
                plan.get("projected_rejoin_position", player_pos) or player_pos
            )
            scored.append(
                {
                    "instruction": plan.get("instruction"),
                    "stops_remaining": plan.get("stops_remaining"),
                    "projected_position": projected,
                    "projected_points": points_for_position(projected),
                    "risk_time_s": plan.get("projected_time_s"),
                    "confidence": plan.get("confidence"),
                }
            )
        current_points = points_for_position(player_pos)
        best_points = max((item["projected_points"] for item in scored), default=current_points)
        return {
            "available": bool(scored),
            "current_position": player_pos,
            "current_points_if_held": current_points,
            "best_projected_points": best_points,
            "plans": scored,
            "note": (
                "Points use the projected finishing position of each plan; a "
                "safe finish can outweigh a higher-variance gamble."
            ),
        }

    async def evaluate_undercut(self, driver: str = "ahead") -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        target = self._resolve_driver(state, driver)
        if target is None or target.get("gap_to_player_s") is None:
            return {
                "available": False,
                "reason": "Target driver or gap is unavailable.",
            }
        gap = abs(float(target["gap_to_player_s"]))
        historical = await self.database.tyre_history_model(
            int(state.get("track_id", -1)), context=state
        )
        own_deg, _, _ = self._deg_for(state, str(state["tyre"]["compound"]), historical)
        rival_age = int(target.get("tyre_age", 0))
        rival_compound = str(target.get("tyre_compound", "MEDIUM"))
        rival_deg = DEFAULT_DEG.get(rival_compound, 0.09)
        fresh_gain = max(0.35, own_deg * max(1, int(state["tyre"]["age_laps"])) + 0.45)
        two_lap_gain = 2 * (fresh_gain + rival_deg * rival_age)
        neutral = self._neutralisation(state)
        traffic_penalty = (
            0.5
            if self._rejoin_position(state, float(neutral["effective_pit_loss_s"]))
            > int(state.get("player_position", 0)) + 3
            else 0.0
        )
        margin = two_lap_gain - gap - traffic_penalty
        return {
            "available": True,
            "driver": target["name"],
            "gap_s": round(gap, 2),
            "estimated_two_lap_gain_s": round(two_lap_gain, 2),
            "traffic_penalty_s": round(traffic_penalty, 2),
            "margin_s": round(margin, 2),
            "verdict": "undercut is on"
            if margin > 0.4
            else "undercut is marginal"
            if margin > -0.4
            else "undercut is unlikely",
            "required": "Clean in-lap, legal compound, no traffic on rejoin, and a strong out-lap.",
            "confidence": "medium" if target.get("lap_history") else "low",
        }

    async def evaluate_overcut(self, driver: str = "ahead") -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        target = self._resolve_driver(state, driver)
        if target is None or target.get("gap_to_player_s") is None:
            return {
                "available": False,
                "reason": "Target driver or gap is unavailable.",
            }
        historical = await self.database.tyre_history_model(
            int(state.get("track_id", -1)), context=state
        )
        current_wear = max(state["tyre"]["wear"] or [0])
        current_deg, _, _ = self._deg_for(
            state, str(state["tyre"]["compound"]), historical
        )
        clean_air_gain = 0.5 if abs(float(target["gap_to_player_s"])) < 2.0 else 0.2
        extra_lap_cost = (
            current_deg * max(1, state["tyre"]["age_laps"])
            + max(0.0, current_wear - 65) * 0.03
        )
        margin = clean_air_gain - extra_lap_cost
        return {
            "available": True,
            "driver": target["name"],
            "margin_s_per_extra_lap": round(margin, 2),
            "verdict": "overcut is viable"
            if margin > 0.15
            else "overcut is marginal"
            if margin > -0.15
            else "overcut is not advised",
            "required": "The rival must rejoin in traffic and your current tyre must remain stable.",
            "confidence": "low" if not target.get("lap_history") else "medium",
        }

    @staticmethod
    def _resolve_driver(state: dict[str, Any], driver: str) -> dict[str, Any] | None:
        query = driver.strip().lower()
        player_position = int(state.get("player_position", 0))
        if query in {"me", "myself", "player", "my car"}:
            index = int(state.get("player_car_index", 0))
            return next(
                (
                    item
                    for item in state.get("drivers", [])
                    if item.get("car_idx") == index
                ),
                None,
            )
        for item in state.get("drivers", []):
            position = int(item.get("position", 0) or 0)
            # A classified position is required. Retired and garaged cars
            # report 0 and are now kept in the snapshot, so an unguarded
            # "player_position - 1" matches them whenever the player leads.
            if position <= 0:
                continue
            if query == "ahead" and position == player_position - 1:
                return item
            if query == "behind" and position == player_position + 1:
                return item
            if query == "leader" and position == 1:
                return item
            if (
                query == f"p{item.get('position')}"
                or query in item.get("name", "").lower()
            ):
                return item
        return None

    async def what_if(self, scenario: str) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        historical = await self.database.tyre_history_model(
            int(state.get("track_id", -1)), context=state
        )
        base = self.compute(state, historical)
        text = scenario.lower()
        compound = next(
            (
                name
                for name in ("SOFT", "MEDIUM", "HARD", "INTER", "WET")
                if name.lower() in text
            ),
            None,
        )
        match = re.search(r"(?:lap\s*)?(\d{1,2})", text)
        requested_lap = (
            int(match.group(1)) if match else int(state.get("current_lap", 0)) + 1
        )
        requested_lap = max(int(state.get("current_lap", 0)), requested_lap)
        if compound is None:
            compound = base.get("recommended", {}).get("fit_compound") or "HARD"
        current_lap = int(state.get("current_lap", 0))
        total_laps = int(state.get("total_laps", 0))
        remaining = max(
            0,
            total_laps - current_lap + (1 if current_lap > 0 else 0),
        )
        first_laps = max(1, requested_lap - current_lap + 1)
        if first_laps >= remaining:
            return {
                "available": False,
                "reason": "That stop is after the race distance.",
            }
        style_factor, evidence = self._driver_wear_factor(state, historical)
        base_lap = self._estimate_base_lap_s(state)
        pre = self._simulate_stint(
            state,
            str(state["tyre"]["compound"]),
            first_laps,
            int(state["tyre"]["age_laps"]),
            max(state["tyre"]["wear"] or [0]),
            base_lap,
            historical,
            style_factor,
            None,
        )
        post = self._simulate_stint(
            state,
            compound,
            remaining - first_laps,
            0,
            0.0,
            base_lap,
            historical,
            style_factor,
            self._available_sets(state).get(compound),
        )
        neutral = self._neutralisation(state)
        total = (
            float(pre["expected_time_s"])
            + float(neutral["effective_pit_loss_s"])
            + float(post["expected_time_s"])
        )
        risk = (
            float(pre["conservative_time_s"])
            + float(neutral["effective_pit_loss_s"])
            + float(post["conservative_time_s"])
        )
        best_time = float(base.get("recommended", {}).get("risk_adjusted_time_s", risk))
        legality = self._compound_rule(state, [compound])
        return {
            "available": True,
            "scenario": f"Box lap {requested_lap} for {compound}",
            "projected_time_s": round(total, 2),
            "risk_adjusted_time_s": round(risk, 2),
            "delta_to_best_s": round(risk - best_time, 2),
            "projected_finish_wear_pct": round(
                float(post["projected_finish_wear_pct"]), 1
            ),
            "feasible": bool(
                pre["feasible"] and post["feasible"] and legality["compliant"]
            ),
            "compound_rule": legality,
            "personal_wear_model": evidence,
        }
