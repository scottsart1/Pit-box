from __future__ import annotations

import math
from statistics import median, pstdev
from typing import Any

from .database import PitWallDatabase
from .setup_insights import (
    adjustments_for,
    analyze_turn,
    characterize_lock,
    cluster_turns,
    resolve_adjustments,
)
from .setup_model import foundational_setup, setup_effects, track_archetype, track_name
from .state import StateStore

SETUP_LIMITS: dict[str, tuple[float, float]] = {
    "front_wing": (0, 50), "rear_wing": (0, 50), "on_throttle": (10, 100),
    "off_throttle": (10, 100), "front_camber": (-3.5, -2.5),
    "rear_camber": (-2.2, -1.0), "front_toe": (0.0, 0.5), "rear_toe": (0.0, 0.5),
    "front_suspension": (1, 41), "rear_suspension": (1, 41),
    "front_anti_roll_bar": (1, 21), "rear_anti_roll_bar": (1, 21),
    "front_suspension_height": (10, 50), "rear_suspension_height": (10, 60),
    "brake_pressure": (80, 100), "brake_bias": (50, 70), "engine_braking": (0, 100),
    "rear_left_tyre_pressure": (20.0, 29.5), "rear_right_tyre_pressure": (20.0, 29.5),
    "front_left_tyre_pressure": (20.0, 29.5), "front_right_tyre_pressure": (20.0, 29.5),
    "ballast": (0, 50), "fuel_load": (0, 110),
}
INTEGER_FIELDS = {
    "front_wing", "rear_wing", "on_throttle", "off_throttle", "front_suspension",
    "rear_suspension", "front_anti_roll_bar", "rear_anti_roll_bar",
    "front_suspension_height", "rear_suspension_height", "brake_pressure",
    "brake_bias", "engine_braking", "ballast",
}
LEARNED_FIELDS = (
    "front_wing", "rear_wing", "on_throttle", "off_throttle", "front_camber",
    "rear_camber", "front_toe", "rear_toe", "front_suspension", "rear_suspension",
    "front_anti_roll_bar", "rear_anti_roll_bar", "front_suspension_height",
    "rear_suspension_height", "brake_pressure", "brake_bias", "engine_braking",
    "rear_left_tyre_pressure", "rear_right_tyre_pressure", "front_left_tyre_pressure",
    "front_right_tyre_pressure", "ballast",
)


class SetupAdvisor:
    """Complete pre-weekend baselines plus conservative personal learning."""

    def __init__(self, store: StateStore, database: PitWallDatabase) -> None:
        self.store = store
        self.database = database
        self._learned_sessions: set[int] = set()

    @staticmethod
    def _clamp(field: str, value: float) -> float | int:
        minimum, maximum = SETUP_LIMITS.get(field, (-math.inf, math.inf))
        clamped = min(maximum, max(minimum, value))
        if field in INTEGER_FIELDS:
            return int(round(clamped))
        precision = 1 if "tyre_pressure" in field else 2
        return round(clamped, precision)

    @staticmethod
    def _profile_for_session(session_type: str) -> str:
        lowered = session_type.lower()
        if "quali" in lowered or "shootout" in lowered or "time trial" in lowered:
            return "quali"
        if "race" in lowered or "sprint" in lowered:
            return "race"
        return "hybrid"

    @staticmethod
    def _handling_signals(state: dict[str, Any]) -> dict[str, Any]:
        tyre = state.get("tyre", {})
        temps = list(tyre.get("inner_temps_c", [0, 0, 0, 0]))
        wear = list(tyre.get("wear", [0, 0, 0, 0]))
        feedback = [item.get("category", "") for item in state.get("feedback", [])[-20:]]
        corners = state.get("analysis", {}).get("corner_metrics", [])
        return {
            "front_temp_c": sum(temps[:2]) / 2 if len(temps) >= 4 else 0,
            "rear_temp_c": sum(temps[2:]) / 2 if len(temps) >= 4 else 0,
            "front_wear_pct": sum(wear[:2]) / 2 if len(wear) >= 4 else 0,
            "rear_wear_pct": sum(wear[2:]) / 2 if len(wear) >= 4 else 0,
            "understeer_feedback": feedback.count("understeer") + feedback.count("no_front_grip"),
            "oversteer_feedback": feedback.count("oversteer") + feedback.count("rear_instability"),
            "traction_feedback": feedback.count("traction"),
            "locks": sum(1 for c in corners if c.get("wheel_lock")),
            "wheelspin": sum(1 for c in corners if c.get("wheelspin")),
            "temps": temps,
        }

    async def generate(self, profile: str, track_id: int | None = None) -> dict[str, Any]:
        profile = profile.strip().lower()
        if profile not in {"race", "quali", "hybrid"}:
            return {"available": False, "reason": "Profile must be race, quali, or hybrid."}

        state = await self.store.snapshot_analysis()
        selected_track_id = int(track_id if track_id is not None else state.get("track_id", -1))
        if selected_track_id < 0:
            return {"available": False, "reason": "Select a track or connect live telemetry first."}

        foundation = foundational_setup(selected_track_id, profile)
        current = dict(state.get("car_setup", {}))
        source = "live_setup_refinement" if current else "foundational_pre_weekend"
        # Before a weekend, provide every field. During a live session, preserve all
        # transmitted values and use the foundation only to fill missing fields.
        recommendation = dict(foundation)
        recommendation.update({k: v for k, v in current.items() if v is not None})
        rationale: list[str] = [
            f"Complete {profile.title()} foundation for {track_name(selected_track_id)} ({track_archetype(selected_track_id).replace('_', ' ')} circuit)."
        ]
        signals = self._handling_signals(state)
        preferences = dict(state.get("driver_preferences", {}) or {})

        # Apply explicit driver preferences as a bounded prior. Live handling and
        # stored performance evidence below can still correct this direction.
        rear_stability = max(0, min(3, int(preferences.get("rear_stability", 0) or 0)))
        traction = max(0, min(3, int(preferences.get("traction", 0) or 0)))
        rotation = max(0, min(3, int(preferences.get("rotation", 0) or 0)))
        tyre_life = max(0, min(3, int(preferences.get("tyre_life", 0) or 0)))
        straight_line = max(0, min(3, int(preferences.get("straight_line", 0) or 0)))
        if rear_stability:
            recommendation["rear_wing"] = self._clamp("rear_wing", float(recommendation["rear_wing"]) + rear_stability)
            recommendation["on_throttle"] = self._clamp("on_throttle", float(recommendation["on_throttle"]) - 2 * rear_stability)
            rationale.append(f"Driver preference: rear stability level {rear_stability}.")
        if traction:
            recommendation["on_throttle"] = self._clamp("on_throttle", float(recommendation["on_throttle"]) - 2 * traction)
            recommendation["rear_anti_roll_bar"] = self._clamp("rear_anti_roll_bar", float(recommendation["rear_anti_roll_bar"]) - traction)
            rationale.append(f"Driver preference: traction level {traction}.")
        if rotation:
            recommendation["front_wing"] = self._clamp("front_wing", float(recommendation["front_wing"]) + rotation)
            recommendation["off_throttle"] = self._clamp("off_throttle", float(recommendation["off_throttle"]) - 2 * rotation)
            rationale.append(f"Driver preference: rotation level {rotation}.")
        if tyre_life:
            recommendation["front_anti_roll_bar"] = self._clamp("front_anti_roll_bar", float(recommendation["front_anti_roll_bar"]) - max(1, tyre_life - 1))
            recommendation["rear_anti_roll_bar"] = self._clamp("rear_anti_roll_bar", float(recommendation["rear_anti_roll_bar"]) - max(1, tyre_life - 1))
            rationale.append(f"Driver preference: tyre-life protection level {tyre_life}.")
        if straight_line:
            recommendation["front_wing"] = self._clamp("front_wing", float(recommendation["front_wing"]) - straight_line)
            recommendation["rear_wing"] = self._clamp("rear_wing", float(recommendation["rear_wing"]) - straight_line)
            rationale.append(f"Driver preference: straight-line speed level {straight_line}.")

        # Profile intent is applied even when refining a live setup.
        if current:
            if profile == "race":
                recommendation["on_throttle"] = self._clamp("on_throttle", float(recommendation["on_throttle"]) - 2)
                recommendation["rear_anti_roll_bar"] = self._clamp("rear_anti_roll_bar", float(recommendation["rear_anti_roll_bar"]) - 1)
                rationale.append("Race profile prioritises traction, tyre stability and repeatable long-run balance.")
            elif profile == "quali":
                recommendation["front_wing"] = self._clamp("front_wing", float(recommendation["front_wing"]) + 1)
                recommendation["brake_pressure"] = 100
                rationale.append("Qualifying profile prioritises rotation, peak response and one-lap braking performance.")
            else:
                rationale.append("Hybrid profile balances one-lap response with stint stability.")

        if signals["understeer_feedback"] > signals["oversteer_feedback"] or signals["front_wear_pct"] - signals["rear_wear_pct"] > 7:
            recommendation["front_wing"] = self._clamp("front_wing", float(recommendation["front_wing"]) + 1)
            recommendation["off_throttle"] = self._clamp("off_throttle", float(recommendation["off_throttle"]) - 3)
            rationale.append("Personal evidence shows a front-limited balance; added front authority and entry rotation.")
        elif signals["oversteer_feedback"] > signals["understeer_feedback"] or signals["rear_wear_pct"] - signals["front_wear_pct"] > 7:
            recommendation["rear_wing"] = self._clamp("rear_wing", float(recommendation["rear_wing"]) + 1)
            recommendation["on_throttle"] = self._clamp("on_throttle", float(recommendation["on_throttle"]) - 4)
            rationale.append("Personal evidence shows rear limitation; added rear support and gentler power locking.")
        if signals["wheelspin"] or signals["traction_feedback"]:
            recommendation["on_throttle"] = self._clamp("on_throttle", float(recommendation["on_throttle"]) - 3)
            recommendation["rear_anti_roll_bar"] = self._clamp("rear_anti_roll_bar", float(recommendation["rear_anti_roll_bar"]) - 1)
            rationale.append("Observed traction loss: reduced power differential and softened rear roll stiffness.")
        if signals["locks"]:
            recommendation["brake_pressure"] = self._clamp("brake_pressure", float(recommendation["brake_pressure"]) - 2)
            recommendation["brake_bias"] = self._clamp("brake_bias", float(recommendation["brake_bias"]) - 1)
            rationale.append("Observed lock-ups: reduced peak pressure and moved bias one step rearward.")

        # ------------------------------------------------------------------
        # Per-corner causal evidence at THIS circuit: every stored lap's
        # corners, clustered into physical turns, each measured against the
        # driver's own best pass through the same turn. Only mechanisms a
        # setup change can address move the car; technique losses become
        # coaching notes instead of hardware changes.
        corner_findings, net_adjustments, corner_notes = (
            await self._corner_causal_findings(selected_track_id)
        )
        for field, delta in net_adjustments.items():
            if field in recommendation:
                recommendation[field] = self._clamp(
                    field, float(recommendation[field]) + delta
                )
        for finding in corner_findings:
            if finding.get("adjustments"):
                moved = ", ".join(
                    f"{name.replace('_', ' ')} {value:+g}"
                    for name, value in finding["adjustments"].items()
                )
                rationale.append(
                    f"{finding['label']}: median {finding['median_loss_s']:.2f}s "
                    f"lost across {finding['samples']} passes — "
                    f"{finding['mechanism_label']}; {moved}."
                )
        technique = [
            finding
            for finding in corner_findings
            if not finding.get("setup_addressable")
        ][:2]
        for finding in technique:
            rationale.append(
                f"{finding['label']}: {finding['median_loss_s']:.2f}s is "
                f"{finding['mechanism_label']} — that is driving, not setup; "
                "no change made."
            )
        rationale.extend(corner_notes)

        # Personal wear rate vs the circuit's baseline: a driver who burns the
        # tyre faster than the track default gets a setup that protects it.
        tyre_history = await self.database.tyre_history_model(
            selected_track_id, context=state
        )
        style = self._personal_wear_style(state, tyre_history)
        if style is not None and style >= 1.25:
            for field in (
                "front_left_tyre_pressure", "front_right_tyre_pressure",
                "rear_left_tyre_pressure", "rear_right_tyre_pressure",
            ):
                recommendation[field] = self._clamp(
                    field, float(recommendation[field]) - 0.2
                )
            rationale.append(
                f"Your measured tyre wear here runs {style:.1f}× the circuit "
                "baseline — pressures trimmed to protect the stint."
            )
        elif style is not None and style <= 0.8:
            rationale.append(
                f"Your measured tyre wear here runs {style:.1f}× the circuit "
                "baseline — no protective compromise needed; setup keeps its pace bias."
            )

        for index, field in enumerate((
            "front_left_tyre_pressure", "front_right_tyre_pressure",
            "rear_left_tyre_pressure", "rear_right_tyre_pressure",
        )):
            temperature = float(signals["temps"][index]) if index < len(signals["temps"]) else 0.0
            if temperature > 103:
                recommendation[field] = self._clamp(field, float(recommendation[field]) - 0.2)
            elif 0 < temperature < 88:
                recommendation[field] = self._clamp(field, float(recommendation[field]) + 0.2)

        history = await self.database.best_setup_runs(selected_track_id, profile, 8)
        if history:
            # Weighted blend toward the best proven runs, with a capped influence so
            # one anomalous session cannot corrupt the foundation.
            for field in LEARNED_FIELDS:
                values = [(float(run["setup"][field]), 1.0 / (1.0 + i)) for i, run in enumerate(history) if field in run.get("setup", {})]
                if not values or field not in recommendation:
                    continue
                learned = sum(value * weight for value, weight in values) / sum(weight for _, weight in values)
                influence = min(0.35, 0.08 + 0.04 * len(values))
                recommendation[field] = self._clamp(field, (1 - influence) * float(recommendation[field]) + influence * learned)
            rationale.append(f"Blended with {len(history)} stored {profile} run(s), weighted toward the best repeatable performance.")

        recommendation = {field: self._clamp(field, float(value)) if field in SETUP_LIMITS else value for field, value in recommendation.items()}
        comparison_base = current if current else foundation
        changes = {
            field: {"from": comparison_base.get(field), "to": value}
            for field, value in recommendation.items()
            if comparison_base.get(field) != value
        }
        wing_from = int(current.get("front_wing", recommendation.get("front_wing", 0)))
        wing_to = int(recommendation.get("front_wing", wing_from))
        wing_change = wing_to - wing_from
        effects = setup_effects(recommendation, selected_track_id)
        pit_adjustment = {
            "available": bool(current) and wing_change != 0,
            "next_front_wing": wing_to,
            "change": wing_change,
            "instruction": (
                f"At the next stop, set front wing to {wing_to} ({wing_change:+d} click)."
                if current and wing_change else "No live pit-stop front-wing change is recommended."
            ),
            "note": "The full setup is for garage/pre-weekend use; only permitted front-wing adjustment is presented for an in-race stop.",
        }
        samples = len(history)
        live_laps = len(state.get("completed_laps", []))
        confidence = "high" if samples >= 5 and live_laps >= 5 else "medium" if samples >= 2 or live_laps >= 3 else "low"
        result = {
            "available": True,
            "profile": profile,
            "track_id": selected_track_id,
            "track": track_name(selected_track_id),
            "track_character": track_archetype(selected_track_id),
            "source": source,
            "foundational": foundation,
            "current": current,
            "recommended": recommendation,
            "changes": changes,
            "rationale": rationale,
            "corner_findings": corner_findings,
            "corner_notes": corner_notes,
            "driver_preferences": preferences,
            "setup_effects": effects,
            "pit_adjustment": pit_adjustment,
            "learning_samples": samples,
            "confidence": confidence,
        }
        await self.database.save_setup_recommendation(selected_track_id, track_name(selected_track_id), profile, recommendation, rationale, confidence)
        await self.store.update(setup_recommendation=result)
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
        except Exception:
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
                adjustments_for(finding, axle)
                if finding["setup_addressable"]
                else {}
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
        state = await self.store.snapshot_analysis()
        session_uid = int(state.get("session_uid", 0))
        if not session_uid or session_uid in self._learned_sessions or not state.get("car_setup"):
            return False
        laps = [lap for lap in state.get("completed_laps", []) if lap.get("valid") and lap.get("lap_time_ms", 0) > 0]
        if len(laps) < 3:
            return False
        times = [lap["lap_time_ms"] / 1000 for lap in laps]
        wheel_rates: list[list[float]] = [[], [], [], []]
        for lap in laps:
            start, end = lap.get("wear_start", []), lap.get("wear_end", [])
            if len(start) == 4 and len(end) == 4:
                for i, (before, after) in enumerate(zip(start, end)):
                    wheel_rates[i].append(max(0.0, float(after) - float(before)))
        per_wheel = [median(values) if values else 0.0 for values in wheel_rates]
        performance = {
            "best_lap_s": min(times), "median_lap_s": median(times),
            "consistency_s": pstdev(times) if len(times) > 1 else 0.0,
            "wear_per_lap_pct": sum(per_wheel) / 4,
            "wheel_wear_per_lap_pct": per_wheel,
            "laps": len(laps), "position": state.get("player_position", 0),
            "line_score": state.get("analysis", {}).get("racing_line", {}).get("line_score"),
        }
        score = performance["best_lap_s"] + 0.5 * performance["consistency_s"] + 0.08 * max(per_wheel or [0])
        profile = self._profile_for_session(state.get("session_type", ""))
        await self.database.save_setup_run(session_uid, int(state.get("track_id", -1)), state.get("track_name", "—"), profile, state["car_setup"], performance, score)
        self._learned_sessions.add(session_uid)
        return True
