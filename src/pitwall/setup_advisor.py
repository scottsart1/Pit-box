from __future__ import annotations

import math
import sqlite3
from copy import deepcopy
from statistics import median
from typing import Any

from .database import PitWallDatabase
from .setup_insights import (
    adjustments_for,
    analyze_turn,
    characterize_lock,
    cluster_turns,
    resolve_adjustments,
)
from .setup_model import setup_effects, track_archetype, track_name
from .setup_reference import reference_for_track
from .state import StateStore

SETUP_LIMITS: dict[str, tuple[float, float]] = {
    "front_wing": (0, 50),
    "rear_wing": (0, 50),
    "on_throttle": (10, 100),
    "off_throttle": (10, 100),
    "front_camber": (-3.5, -2.5),
    "rear_camber": (-2.0, -1.0),
    "front_toe": (0.0, 0.1),
    "rear_toe": (0.1, 0.3),
    "front_suspension": (1, 41),
    "rear_suspension": (1, 41),
    "front_anti_roll_bar": (1, 21),
    "rear_anti_roll_bar": (1, 21),
    "front_suspension_height": (10, 40),
    "rear_suspension_height": (40, 100),
    "brake_pressure": (80, 100),
    "brake_bias": (50, 70),
    "engine_braking": (0, 100),
    "rear_left_tyre_pressure": (20.5, 26.5),
    "rear_right_tyre_pressure": (20.5, 26.5),
    "front_left_tyre_pressure": (22.5, 29.5),
    "front_right_tyre_pressure": (22.5, 29.5),
    "ballast": (0, 50),
    "fuel_load": (0, 110),
}
INTEGER_FIELDS = {
    "front_wing",
    "rear_wing",
    "on_throttle",
    "off_throttle",
    "front_suspension",
    "rear_suspension",
    "front_anti_roll_bar",
    "rear_anti_roll_bar",
    "front_suspension_height",
    "rear_suspension_height",
    "brake_pressure",
    "brake_bias",
    "engine_braking",
    "ballast",
}
LEARNED_FIELDS = (
    "front_wing",
    "rear_wing",
    "on_throttle",
    "off_throttle",
    "front_camber",
    "rear_camber",
    "front_toe",
    "rear_toe",
    "front_suspension",
    "rear_suspension",
    "front_anti_roll_bar",
    "rear_anti_roll_bar",
    "front_suspension_height",
    "rear_suspension_height",
    "brake_pressure",
    "brake_bias",
    "engine_braking",
    "rear_left_tyre_pressure",
    "rear_right_tyre_pressure",
    "front_left_tyre_pressure",
    "front_right_tyre_pressure",
    "ballast",
)

CHANGE_LEVELS = {"minimum": 1.0, "moderate": 0.5, "radical": 0.0}


def _minimum_step(field: str) -> float:
    if "tyre_pressure" in field:
        return 0.3
    if "camber" in field:
        return 0.1
    if "toe" in field:
        return 0.05
    if field in {"on_throttle", "off_throttle", "engine_braking"}:
        return 5.0
    return 2.0


class SetupAdvisor:
    """Sourced references and bounded, explicitly unvalidated personal tests."""

    def __init__(self, store: StateStore, database: PitWallDatabase) -> None:
        self.store = store
        self.database = database

    @staticmethod
    def _clamp(field: str, value: float) -> float | int:
        minimum, maximum = SETUP_LIMITS.get(field, (-math.inf, math.inf))
        clamped = min(maximum, max(minimum, value))
        if field in {"on_throttle", "off_throttle"}:
            clamped = min(maximum, max(minimum, 5 * round(clamped / 5)))
        if field in INTEGER_FIELDS:
            return round(clamped)
        precision = 1 if "tyre_pressure" in field or "camber" in field else 2
        return round(clamped, precision)

    @staticmethod
    def _profile_for_session(session_type: str) -> str:
        lowered = session_type.lower()
        if "time trial" in lowered:
            return "time_trial"
        if "quali" in lowered or "shootout" in lowered:
            return "quali"
        if "race" in lowered or "sprint" in lowered:
            return "race"
        return "hybrid"

    @staticmethod
    def _handling_signals(state: dict[str, Any]) -> dict[str, Any]:
        tyre = state.get("tyre", {})
        temps = list(tyre.get("inner_temps_c", [0, 0, 0, 0]))
        wear = list(tyre.get("wear", [0, 0, 0, 0]))
        feedback = [
            item.get("category", "") for item in state.get("feedback", [])[-20:]
        ]
        corners = state.get("analysis", {}).get("corner_metrics", [])
        return {
            "front_temp_c": sum(temps[:2]) / 2 if len(temps) >= 4 else 0,
            "rear_temp_c": sum(temps[2:]) / 2 if len(temps) >= 4 else 0,
            "front_wear_pct": sum(wear[:2]) / 2 if len(wear) >= 4 else 0,
            "rear_wear_pct": sum(wear[2:]) / 2 if len(wear) >= 4 else 0,
            "understeer_feedback": feedback.count("understeer")
            + feedback.count("no_front_grip"),
            "oversteer_feedback": feedback.count("oversteer")
            + feedback.count("rear_instability"),
            "traction_feedback": feedback.count("traction"),
            "locks": sum(1 for c in corners if c.get("wheel_lock")),
            "wheelspin": sum(1 for c in corners if c.get("wheelspin")),
            "temps": temps,
        }

    async def generate(
        self,
        profile: str,
        track_id: int | None = None,
        change_level: str = "minimum",
        *,
        basis: str = "personalized",
        conditions: str = "auto",
        reference_style: str = "stable",
    ) -> dict[str, Any]:
        profile = profile.strip().lower()
        if profile not in {"race", "quali", "hybrid"}:
            return {
                "available": False,
                "reason": "Profile must be race, quali, or hybrid.",
            }
        if change_level not in CHANGE_LEVELS:
            return {
                "available": False,
                "reason": "Change level must be minimum, moderate, or radical.",
            }
        if basis not in {"reference", "personalized"}:
            return {
                "available": False,
                "reason": "Basis must be reference or personalized.",
            }
        if conditions not in {"auto", "dry", "wet"}:
            return {
                "available": False,
                "reason": "Conditions must be auto, dry, or wet.",
            }
        if reference_style not in {"stable", "rotation"}:
            return {
                "available": False,
                "reason": "Reference style must be stable or rotation.",
            }

        origin = await self.store.snapshot_analysis()
        selected_track_id = int(
            track_id if track_id is not None else origin.get("track_id", -1)
        )
        if selected_track_id < 0:
            return {
                "available": False,
                "reason": "Select a track or connect live telemetry first.",
            }
        live_track_id = int(origin.get("track_id", -1))
        same_track = selected_track_id == live_track_id
        weather = (
            str(origin.get("weather", "Unknown")).lower() if same_track else "unknown"
        )
        compound = (
            str(origin.get("tyre", {}).get("compound", "UNKNOWN")).upper()
            if same_track
            else "UNKNOWN"
        )
        wet_observed = any(
            term in weather for term in ("rain", "wet", "storm")
        ) or compound in {"INTER", "WET"}
        dry_observed = (
            any(term in weather for term in ("clear", "cloud", "overcast", "sunny"))
            and not wet_observed
        )
        selected_condition = (
            ("wet" if wet_observed else "dry") if conditions == "auto" else conditions
        )
        reference = deepcopy(
            reference_for_track(
                selected_track_id,
                condition=selected_condition,
                style=reference_style,
                profile=profile,
            )
        )
        reference["requested_condition"] = selected_condition
        reference["reference_style"] = reference_style
        reference_notes = list(reference.get("notes", []))
        reference["notes"] = reference_notes
        if not reference.get("setup") or reference.get("status") == "unavailable":
            return {
                "available": False,
                "reason": reference.get("reason")
                or (
                    reference_notes[0]
                    if reference_notes
                    else "No published setup reference is available for this selection."
                ),
                "baseline_reference": reference,
                "conditions": selected_condition,
                "reference_style": reference_style,
                "basis": basis,
            }

        # The Session packet identifies the selected formula. Packet year alone
        # cannot distinguish a 2025 car, F2 or F1 World from the 2026 formula.
        freshness = origin.get("packet_group_freshness", {}) or {}
        availability = origin.get("availability", {}) or {}
        formula_observed = (
            "1" in freshness
            or 1 in freshness
            or availability.get("packet:1") == "observed"
        )
        formula = int(origin.get("formula", 0) or 0)
        compatible_formula = formula_observed and formula == 13
        incompatible_formula = formula_observed and formula != 13
        reference["applicability"] = (
            "compatible_formula"
            if compatible_formula
            else "incompatible_formula"
            if incompatible_formula
            else "unverified_formula"
        )
        reference["observed_formula"] = formula if formula_observed else None
        if incompatible_formula:
            reason = "This reference is for the F1 2026 car. The observed Session packet identifies a different formula; no personalized 2026 setup can be applied to it."
            reference_notes.append(reason)
            if basis == "personalized":
                return {
                    "available": False,
                    "reason": reason,
                    "baseline_reference": reference,
                }
        elif not formula_observed:
            reference_notes.append(
                "The live car formula has not been observed. This is an F1 2026 reference, not a verified match to the connected car."
            )
        if conditions == "auto" and not wet_observed and not dry_observed:
            reference_notes.append(
                "Current weather is unknown; the selected reference is for dry conditions."
            )
        if selected_condition == "dry" and wet_observed:
            reference_notes.append(
                "Wet conditions are currently observed. This explicitly selected dry reference has not been validated for them."
            )
        source_profile = reference.get("source_profile", "race")
        profile_note = (
            f"The source provides a {source_profile} setup. Only profile differences "
            "present in that source are used; this is not presented as a validated "
            "time-trial setup."
        )
        reference_notes.append(profile_note)

        foundation = deepcopy(reference["setup"])
        condition_matches = (selected_condition == "wet" and wet_observed) or (
            selected_condition == "dry" and dry_observed
        )
        live_session = bool(origin.get("session_uid")) and (
            bool(origin.get("connected"))
            or origin.get("game_presence") in {"receiving", "standing_by"}
        )
        live_applicable = (
            basis == "personalized"
            and same_track
            and condition_matches
            and compatible_formula
            and live_session
        )
        reference["live_evidence_applicable"] = live_applicable
        state = origin if live_applicable else {"track_id": selected_track_id}
        preferences = (
            dict(origin.get("driver_preferences", {}) or {})
            if basis == "personalized"
            else {}
        )
        current = {
            field: value
            for field, value in state.get("car_setup", {}).items()
            if field in SETUP_LIMITS
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        }
        raw_current = dict(current)
        current = {
            field: self._clamp(field, float(value)) for field, value in current.items()
        }
        normalizations = {
            field: {"from": raw_current[field], "to": value}
            for field, value in current.items()
            if raw_current[field] != value
        }
        recommendation = deepcopy(foundation)
        source = (
            f"{reference.get('status', 'unavailable')}_reference"
            if basis == "reference"
            else "foundational_pre_weekend"
        )
        rationale = [
            f"{reference.get('title', 'Published reference')} for {track_name(selected_track_id)}.",
            *reference_notes,
        ]
        if normalizations:
            rationale.append(
                "Live values outside the supported control range or increment were normalized before applying adjustment caps; see input normalizations."
            )
        current_weight = CHANGE_LEVELS[change_level]
        if basis == "personalized":
            for field in foundation:
                if field in current:
                    recommendation[field] = self._clamp(
                        field,
                        current_weight * float(current[field])
                        + (1 - current_weight) * float(foundation[field]),
                    )
            if current:
                source = {
                    "minimum": "live_setup_refinement",
                    "moderate": "circuit_setup_rebalance",
                    "radical": "circuit_setup_rebuild",
                }[change_level]
                if "fuel_load" in current:
                    recommendation["fuel_load"] = current["fuel_load"]
            rationale.append(
                {
                    "minimum": "Minimum change keeps the current settings and caps proposed adjustments to small steps.",
                    "moderate": "Moderate change blends current settings halfway toward the selected reference before bounded preferences.",
                    "radical": "Radical change starts from the selected reference before bounded preferences; test in the garage.",
                }[change_level]
            )
            if not live_applicable:
                rationale.append(
                    "Live handling and setup evidence do not apply to this selection; only explicit driver preferences can alter the reference."
                )
            signals = self._handling_signals(state)

            def adjust(field: str, delta: float) -> None:
                if field in recommendation:
                    recommendation[field] = self._clamp(
                        field, float(recommendation[field]) + delta
                    )

            for preference, fields in (
                ("rear_stability", {"rear_wing": 1, "on_throttle": -2}),
                ("traction", {"on_throttle": -2, "rear_anti_roll_bar": -1}),
                ("rotation", {"front_wing": 1, "off_throttle": -2}),
                ("straight_line", {"front_wing": -1, "rear_wing": -1}),
            ):
                level = max(0, min(3, int(preferences.get(preference, 0) or 0)))
                if level:
                    for field, delta in fields.items():
                        adjust(field, delta * level)
                    rationale.append(
                        f"Driver preference: {preference.replace('_', ' ')} level {level}; a bounded test hypothesis."
                    )
            tyre_life = max(0, min(3, int(preferences.get("tyre_life", 0) or 0)))
            if tyre_life:
                adjust("front_anti_roll_bar", -max(1, tyre_life - 1))
                adjust("rear_anti_roll_bar", -max(1, tyre_life - 1))
                rationale.append(
                    "Driver preference: tyre-life protection; test the softer roll-stiffness hypothesis against measured wear."
                )
            if signals["understeer_feedback"] > signals["oversteer_feedback"]:
                adjust("front_wing", 1)
                adjust("off_throttle", -3)
                rationale.append(
                    "Reported understeer suggests testing a little more front authority; this does not establish the cause of a pace loss."
                )
            elif signals["oversteer_feedback"] > signals["understeer_feedback"]:
                adjust("rear_wing", 1)
                adjust("on_throttle", -4)
                rationale.append(
                    "Reported rear instability suggests testing more rear support and gentler power locking."
                )
            if signals["traction_feedback"]:
                adjust("on_throttle", -3)
                adjust("rear_anti_roll_bar", -1)
                rationale.append(
                    "Reported traction difficulty suggests a bounded differential and rear roll-stiffness test."
                )
            if signals["locks"]:
                # A generic wheel_lock flag does not identify the axle. Moving
                # bias rearward for a rear lock would worsen the problem.
                rationale.append(
                    "Wheel locking was observed. Confirm the axle and repeatability before changing brake bias or pressure."
                )
            if signals["wheelspin"]:
                rationale.append(
                    "Wheelspin was observed; compare repeated exits and driver reports before attributing it to setup."
                )
            if current and change_level == "minimum":
                for field in LEARNED_FIELDS:
                    if field in current and field in recommendation:
                        step = _minimum_step(field)
                        recommendation[field] = self._clamp(
                            field,
                            max(
                                float(current[field]) - step,
                                min(
                                    float(current[field]) + step,
                                    float(recommendation[field]),
                                ),
                            ),
                        )
            recommendation = {
                field: self._clamp(field, float(value))
                if field in SETUP_LIMITS
                else value
                for field, value in recommendation.items()
            }
        else:
            rationale.append(
                "Reference mode preserves the selected reference values, including disclosed source normalizations; no live, preference or historical adjustments are applied."
            )

        learning_reason = (
            "Stored legacy setup runs and corner histories lack the matched game, "
            "setup, tyre, fuel and condition provenance required for automatic "
            "learning. They remain available for review and do not alter this setup."
        )
        rationale.append(learning_reason)
        rationale.append(
            "Pressure changes are not inferred from universal temperature thresholds or wear alone; use compound-specific measured tests."
        )
        pace_review = (
            self._pace_review(state)
            if basis == "personalized"
            else {
                "available": False,
                "summary": "Reference mode does not use live pace as setup evidence.",
            }
        )
        rationale.append(pace_review["summary"])
        comparison_base = raw_current if raw_current else foundation
        changes = {
            field: {"from": comparison_base.get(field), "to": value}
            for field, value in recommendation.items()
            if comparison_base.get(field) != value
        }
        wing_to = int(recommendation.get("front_wing", 0))
        wing_from = int(current.get("front_wing", wing_to))
        wing_change = wing_to - wing_from
        pit_available = (
            basis == "personalized"
            and live_applicable
            and bool(current)
            and bool(wing_change)
            and change_level == "minimum"
            and "front_wing" not in normalizations
        )
        pit_adjustment = {
            "available": pit_available,
            "next_front_wing": wing_to,
            "change": wing_change,
            "instruction": f"At the next stop, set front wing to {wing_to} ({wing_change:+d} click)."
            if pit_available
            else "This setup is for garage testing. No live pit-stop front-wing change is recommended.",
            "note": "Full setups are for garage use; only a separate permitted front-wing adjustment is offered for a stop.",
        }
        result = {
            "available": True,
            "profile": profile,
            "basis": basis,
            "conditions": selected_condition,
            "reference_style": reference_style,
            "change_level": change_level,
            "pace_review": pace_review,
            "track_id": selected_track_id,
            "track": track_name(selected_track_id),
            "track_character": track_archetype(selected_track_id),
            "source": source,
            "baseline_reference": reference,
            "foundational": foundation,
            "current": raw_current,
            "input_normalizations": normalizations,
            "recommended": recommendation,
            "changes": changes,
            "rationale": rationale,
            "corner_findings": [],
            "corner_notes": [learning_reason],
            "driver_preferences": preferences,
            "setup_effects": setup_effects(recommendation, selected_track_id),
            "pit_adjustment": pit_adjustment,
            "learning_samples": 0,
            "learning_status": {"qualified": False, "reason": learning_reason},
            "confidence": "low",
            "confidence_reason": "Source provenance is available; performance for this driver and these conditions has not been validated with matched runs.",
            "session_context": {
                key: origin.get(key, 0)
                for key in (
                    "session_uid",
                    "restart_epoch",
                    "timeline_epoch",
                    "session_generation",
                )
            },
        }
        await self.database.save_setup_recommendation(
            selected_track_id,
            track_name(selected_track_id),
            profile,
            recommendation,
            rationale,
            "low",
        )
        published = False

        def publish(live_state):
            nonlocal published
            if int(live_state.track_id) == live_track_id:
                live_state.setup_recommendation = result
                published = True

        matched = await self.store.mutate_for_session(origin, publish)
        if not matched or not published:
            return {
                "available": False,
                "reason": "Session changed while the setup was being prepared; generate it again for the current session.",
            }
        return result

    @staticmethod
    def _pace_review(state: dict[str, Any]) -> dict[str, Any]:
        """Describe observed pace without claiming that setup caused a deficit."""
        clean = [
            lap
            for lap in state.get("completed_laps", [])
            if lap.get("valid")
            and float(lap.get("lap_time_ms", 0)) > 0
            and not lap.get("learning_exclusions")
        ][-5:]
        if not clean:
            return {
                "available": False,
                "summary": "Pace review unavailable: record clean laps at this circuit before judging whether the car is too slow.",
            }
        own = median(float(lap["lap_time_ms"]) for lap in clean) / 1000
        lap_numbers = {int(lap.get("lap_num", 0)) for lap in clean}
        compounds = {str(lap.get("compound", "UNKNOWN")) for lap in clean}
        rivals = []
        for driver in state.get("drivers", []):
            if (
                driver.get("is_player")
                or driver.get("car_idx") == state.get("player_car_index")
                or driver.get("restricted")
            ):
                continue
            # Current compound does not establish an earlier stint's compound.
            age = int(driver.get("tyre_age", 0))
            first_stint_lap = int(driver.get("current_lap", 0)) - age
            if (
                len(compounds) != 1
                or driver.get("tyre_compound", "UNKNOWN") not in compounds
                or "UNKNOWN" in compounds
            ):
                continue
            times = [
                float(lap.get("lap_ms", 0)) / 1000
                for lap in driver.get("lap_history", [])
                if int(lap.get("lap_num", 0)) in lap_numbers
                and int(lap.get("lap_num", 0)) > first_stint_lap
                and int(lap.get("valid_flags", 0)) & 1
                and float(lap.get("lap_ms", 0)) > 0
                and not lap.get("learning_exclusions")
            ]
            if times:
                rivals.append(median(times))
        result = {
            "available": True,
            "player_median_s": round(own, 3),
            "clean_laps": len(clean),
            "rival_samples": len(rivals),
        }
        if rivals:
            benchmark = median(rivals)
            delta = own - benchmark
            result.update(field_median_s=round(benchmark, 3), delta_s=round(delta, 3))
            verdict = (
                f"{abs(delta):.2f}s {'slower' if delta > 0 else 'faster'} than"
                if abs(delta) >= 0.05
                else "level with"
            )
            result["summary"] = (
                f"Recent clean pace: {own:.2f}s, {verdict} the same-compound field median across {len(rivals)} rivals on matching lap numbers. Fuel, traffic and tyre age can differ; this does not establish a setup-caused loss."
            )
        else:
            result["summary"] = (
                f"Recent clean pace: {own:.2f}s over {len(clean)} laps. No matching same-compound rival sample is available; a pace deficit cannot be established."
            )
        return result

    async def _corner_causal_findings(
        self, track_id: int
    ) -> tuple[list[dict[str, Any]], dict[str, float], list[str]]:
        """Turn stored per-corner history into causal setup findings.

        Entry lock-ups get their traces re-read to learn WHICH axle locks —
        front locking wants bias rearward, rear locking wants the opposite,
        and treating them alike would move the car the wrong way half the
        time.
        """
        try:
            rows = await self.database.corner_rows_for_track(track_id, 40)
        except (sqlite3.Error, OSError, ValueError, TypeError):
            return [], {}, []
        findings: list[dict[str, Any]] = []
        for cluster in cluster_turns(rows):
            finding = analyze_turn(cluster)
            if finding is None:
                continue
            axle: str | None = None
            if finding["mechanism"] == "entry-lockup":
                windows: list[list[dict[str, Any]]] = []
                locked = [row for row in cluster["rows"] if row.get("wheel_lock")]
                for row in locked[:3]:
                    trace = await self.database.lap_trace(
                        int(row["session_uid"]), int(row["lap_num"])
                    )
                    if not trace:
                        continue
                    start = float(row.get("entry_m") or 0.0)
                    end = float(row.get("exit_m") or start + 1.0)
                    window = [
                        point
                        for point in trace
                        if start <= float(point.get("d", -1.0)) <= end
                    ]
                    if window:
                        windows.append(window)
                axle = characterize_lock(windows)
                finding["evidence"]["lock_axle"] = axle
            finding["adjustments"] = (
                adjustments_for(finding, axle) if finding["setup_addressable"] else {}
            )
            findings.append(finding)
        findings.sort(key=lambda f: float(f["median_loss_s"]), reverse=True)
        net, notes = resolve_adjustments(findings)
        return findings, net, notes

    @staticmethod
    def _personal_wear_style(
        state: dict[str, Any], historical: dict[str, Any]
    ) -> float | None:
        """How fast THIS driver uses a tyre here, as a multiple of baseline.

        Reuses the strategy engine's evidence extraction so setup and strategy
        agree about the driver's wear style. None when only the track default
        is available — a default is not evidence about the driver.
        """
        from .strategy import StrategyEngine

        factor, evidence = StrategyEngine._driver_wear_factor(state, historical)
        if evidence.get("source") == "track_default":
            return None
        return float(factor)

    async def learn_current_session(self) -> bool:
        """Do not label a whole session as one measured setup run.

        The legacy table has no run/condition identity and previously assigned
        every lap to the current car setup once the third lap arrived. Existing
        records are retained for review. Qualified future learning must use
        canonical runs and matched evidence, rather than adding more such rows.
        """
        return False
