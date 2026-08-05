from __future__ import annotations

import asyncio
from itertools import pairwise
from typing import Any

from .analysis import AnalysisEngine
from .database import PitWallDatabase
from .setup_advisor import SetupAdvisor
from .state import StateStore
from .strategy import PIT_LOSS_SECONDS, TRACK_TYRE_SEVERITY, points_for_position
from .tools import TelemetryTools
from .udp import TRACKS


def _confidence(samples: int) -> str:
    return "high" if samples >= 12 else "medium" if samples >= 5 else "low"


def _valid_history(driver: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        lap
        for lap in driver.get("lap_history", [])
        if int(lap.get("lap_ms", 0) or 0) > 0
        and bool(int(lap.get("valid_flags", 0) or 0) & 1)
    ]


class BriefingEngine:
    """Build briefing facts deterministically; the model only narrates them."""

    def __init__(
        self,
        store: StateStore,
        database: PitWallDatabase,
        analysis: AnalysisEngine,
        setup_advisor: SetupAdvisor,
        tools: TelemetryTools,
    ) -> None:
        self.store = store
        self.database = database
        self.analysis = analysis
        self.setup_advisor = setup_advisor
        self.tools = tools

    @staticmethod
    def _target_lap(
        mode: str, sector_bests: dict[str, Any], laps: list[dict[str, Any]]
    ) -> dict[str, Any]:
        valid = [int(lap.get("lap_time_ms", 0) or 0) for lap in laps if lap.get("valid")]
        personal_best = int(sector_bests.get("personal_best_lap_ms", 0) or 0)
        if not personal_best and valid:
            personal_best = min(value for value in valid if value > 0)
        if not personal_best:
            return {
                "lap_time_ms": None,
                "source": "unavailable",
                "confidence": "low",
                "reason": "No valid personal lap is stored for this circuit.",
            }
        offset_ms = 0 if mode == "qualifying" else 450 if mode == "race" else 250
        return {
            "lap_time_ms": personal_best + offset_ms,
            "source": "personal_history_plus_mode_margin",
            "confidence": _confidence(len(valid)),
            "sample_size": len(valid),
        }

    @staticmethod
    def _strategy_candidates(
        mode: str,
        total_laps: int,
        target_ms: int | None,
        pit_loss_s: float,
        expected_stops: int,
    ) -> list[dict[str, Any]]:
        if mode != "race":
            return []
        sequences = (
            [("MEDIUM", "HARD"), ("HARD", "MEDIUM")]
            if expected_stops == 1
            else [("SOFT", "MEDIUM", "HARD"), ("MEDIUM", "HARD", "MEDIUM")]
        )
        plans: list[dict[str, Any]] = []
        for index, sequence in enumerate(sequences):
            projected: float | None = None
            reason = "Race distance or a personal target lap is unavailable."
            if total_laps > 0 and target_ms:
                # This deliberately remains a transparent baseline projection;
                # the live strategy engine replaces it once fuel, wear and field
                # telemetry exist.
                compound_adjustment = 0.20 * total_laps if index else 0.0
                projected = round(
                    target_ms / 1000.0 * total_laps
                    + pit_loss_s * expected_stops
                    + compound_adjustment,
                    1,
                )
                reason = "Personal target pace plus modelled green-flag pit loss."
            plans.append(
                {
                    "rank": index + 1,
                    "compounds": list(sequence),
                    "stops": expected_stops,
                    "projected_time_s": projected,
                    "projection_confidence": "low",
                    "projection_source": "pre_session_baseline_model",
                    "projection_reason": reason,
                }
            )
        return plans

    async def pre_session(self, mode: str, track_id: int | None = None) -> dict[str, Any]:
        mode = mode.strip().lower()
        if mode not in {"race", "qualifying", "practice"}:
            raise ValueError("mode must be race, qualifying, or practice")
        state = await self.store.snapshot_analysis()
        selected = int(track_id if track_id is not None else state.get("track_id", -1))
        if selected < 0:
            raise ValueError("Select a track before generating a session brief.")

        setup_profile = "race" if mode == "race" else "quali" if mode == "qualifying" else "hybrid"
        review, tyre_model, progress, sectors, setup, consistency = await asyncio.gather(
            self.database.track_review(selected, 50),
            self.database.tyre_history_model(selected, context=state),
            self.database.progress_trend(selected, 20),
            self.database.sector_bests(selected),
            self.setup_advisor.generate(setup_profile, selected),
            self.analysis.get_consistency(),
        )
        laps = list(review.get("laps", []))
        target = self._target_lap(mode, sectors, laps)
        compounds = dict(tyre_model.get("compounds", {}))
        tyre_evidence = []
        for compound in ("SOFT", "MEDIUM", "HARD", "INTER", "WET"):
            evidence = compounds.get(compound, {}) or {}
            samples = int(evidence.get("sample_size", 0) or 0)
            tyre_evidence.append(
                {
                    "compound": compound,
                    "sample_size": samples,
                    "confidence": _confidence(samples),
                    "wear_per_lap_pct": evidence.get("condition_adjusted_wear_per_lap_pct")
                    if evidence.get("condition_adjusted_wear_per_lap_pct") is not None
                    else evidence.get("max_wear_per_lap_pct"),
                    "degradation_s_per_lap": evidence.get("condition_adjusted_deg_s_per_lap")
                    if evidence.get("condition_adjusted_deg_s_per_lap") is not None
                    else evidence.get("slope_s_per_lap"),
                    "source": evidence.get("source", "no_personal_sample"),
                }
            )

        weak_corners = [
            {
                "corner": item.get("name") or f"Corner {item.get('corner_no', '?')}",
                "average_loss_s": item.get("avg_loss_s"),
                "samples": int(item.get("samples", 0) or 0),
            }
            for item in review.get("corner_opportunities", [])[:3]
        ]
        severity = float(TRACK_TYRE_SEVERITY.get(selected, 1.0))
        total_laps = int(state.get("total_laps", 0) or 0) if int(state.get("track_id", -1)) == selected else 0
        expected_stops = 2 if severity >= 1.24 and (not total_laps or total_laps >= 28) else 1
        pit_loss = float(PIT_LOSS_SECONDS.get(selected, 22.5))
        strategies = self._strategy_candidates(
            mode, total_laps, target.get("lap_time_ms"), pit_loss, expected_stops
        )
        connected_here = bool(state.get("connected")) and int(state.get("track_id", -1)) == selected
        grid = int(state.get("grid_position", 0) or 0) if connected_here else 0
        history_count = sum(int(item.get("sample_size", 0)) for item in tyre_evidence)

        payload: dict[str, Any] = {
            "kind": "pre_session",
            "mode": mode,
            "track_id": selected,
            "track_name": TRACKS.get(selected, str(state.get("track_name") or f"Track {selected}")),
            "telemetry_connected": connected_here,
            "historical_lap_count": len(laps),
            "personal_data_available": bool(laps or history_count or weak_corners),
            "expected_stop_count": expected_stops if mode == "race" else None,
            "leading_strategies": strategies,
            "pit_loss_s": pit_loss,
            "pit_loss_source": "track_baseline",
            "two_compound_requirement": mode == "race",
            "tyre_evidence": tyre_evidence,
            "weak_corners": weak_corners,
            "realistic_target_lap": target,
            "grid": {
                "position": grid or None,
                "side": "odd-slot side" if grid and grid % 2 else "even-slot side" if grid else "unknown",
                "clean_dirty": (
                    "clean/racing-line side"
                    if grid and grid % 2
                    else "dirty/off-line side"
                    if grid
                    else "unknown"
                ),
                "confidence": "low" if grid else "unavailable",
                "reason": (
                    "Standard grid assumption: pole-side odd slots are on the established racing line; the game packet does not label surface grip."
                    if grid
                    else "No grid slot has arrived."
                ),
            },
            "weather": {
                "available": connected_here,
                "current": state.get("weather") if connected_here else None,
                "rain_15_pct": state.get("rain_next_15_pct") if connected_here else None,
                "forecast": state.get("weather_forecast", []) if connected_here else [],
                "reason": None if connected_here else "No live Session packet is available.",
            },
            "progress": progress,
            "sector_bests": sectors,
            "setup": setup,
            "projection_notice": (
                "All strategy timings are low-confidence baseline projections until live race distance, "
                "fuel, weather, wear and field pace arrive. Null values mean no defensible projection exists."
            ),
        }

        if mode == "race":
            goals = [
                "Complete lap 1 without contact or a track-limit warning.",
                (
                    f"Hold repeatable pace within 0.3 seconds of the {target['lap_time_ms'] / 1000:.3f}-second target."
                    if target.get("lap_time_ms")
                    else "Establish three valid representative laps before changing the strategy model."
                ),
                "Keep the first stint legal and preserve both dry-compound options.",
            ]
        elif mode == "qualifying":
            active = int(state.get("lobby", {}).get("active", 0) or 0)
            payload["run_plan"] = {
                "timed_laps_supported": 2 if severity < 1.18 else 1,
                "advice": (
                    "Wait for the current traffic group to clear, then leave enough gap for one preparation lap."
                    if active
                    else "Use one preparation lap; avoid the final traffic release unless track evolution is decisive."
                ),
                "source": "live_lobby" if active else "track_evolution_heuristic",
            }
            field_times = sorted(
                int(driver.get("best_lap_ms", 0) or 0)
                for driver in state.get("drivers", [])
                if int(driver.get("best_lap_ms", 0) or 0) > 0
            )
            target_slot_ms = field_times[9] if len(field_times) >= 10 else None
            personal_reference = int(
                sectors.get("theoretical_best_ms", 0)
                or sectors.get("personal_best_lap_ms", 0)
                or 0
            )
            required_ms = (
                max(0, personal_reference - target_slot_ms)
                if target_slot_ms and personal_reference
                else None
            )
            payload["target_grid_delta"] = {
                "target_position": 10,
                "target_time_ms": target_slot_ms,
                "personal_reference_ms": personal_reference or None,
                "required_ms": required_ms,
                "reason": (
                    "Live P10 field time compared with the stored personal theoretical best."
                    if required_ms is not None
                    else "A target-grid delta needs both a live P10 field time and personal sector history."
                ),
                "confidence": "medium" if required_ms is not None else "low",
            }
            goals = [
                "Bank a valid first timed lap.",
                "Use one preparation lap before the final push.",
                "Convert the largest historical corner loss on the next run.",
            ]
        else:
            least_sampled = min(tyre_evidence[:3], key=lambda item: int(item["sample_size"]))
            payload["measurement_plan"] = {
                "compound": least_sampled["compound"],
                "reason": f"Only {least_sampled['sample_size']} personal valid samples are stored.",
                "long_run_laps": 8,
                "corners": weak_corners,
                "consistency_target_s": consistency.get("lap_time_stddev_s") or 0.35,
                "consistency_source": "live_session" if consistency.get("sample_size", 0) >= 2 else "baseline",
            }
            goals = [
                f"Complete an eight-lap {least_sampled['compound'].lower()} run.",
                "Record valid sectors through the three weakest historical corners.",
                f"Keep representative-lap spread under {payload['measurement_plan']['consistency_target_s']:.2f} seconds.",
            ]
        payload["session_goals"] = goals
        return payload

    async def post_qualifying_lap(self) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        player_idx = int(state.get("player_car_index", 0) or 0)
        player = next((d for d in state.get("drivers", []) if int(d.get("car_idx", -1)) == player_idx), {})
        player_laps = _valid_history(player)
        if not player_laps:
            return {"kind": "post_qualifying_lap", "available": False, "reason": "No valid timed lap is available."}
        lap = player_laps[-1]
        teammate = next((d for d in state.get("drivers", []) if d.get("is_teammate")), None)
        field = [d for d in state.get("drivers", []) if int(d.get("best_lap_ms", 0) or 0) > 0]
        reference = min(field, key=lambda d: int(d.get("best_lap_ms", 0))) if field else None
        comparisons: dict[str, Any] = {}
        for label, driver in (("teammate", teammate), ("session_best", reference)):
            if driver:
                comparisons[label] = await self.tools.get_rival_sector_comparison(str(driver.get("name", "")))
            else:
                comparisons[label] = {"available": False, "reason": f"No {label.replace('_', ' ')} reference."}
        personal_best = min(int(item["lap_ms"]) for item in player_laps)
        energy = next(
            (item for item in reversed(player.get("energy_lap_history", [])) if int(item.get("lap", -1)) == int(lap.get("lap_num", -2))),
            None,
        )
        reference_energy = None
        reference_best_lap = None
        if reference:
            valid_reference_laps = _valid_history(reference)
            if valid_reference_laps:
                reference_best_lap = min(
                    valid_reference_laps,
                    key=lambda item: int(item.get("lap_ms", 0) or 0),
                )
                reference_energy = next(
                    (
                        item
                        for item in reversed(reference.get("energy_lap_history", []))
                        if int(item.get("lap", -1))
                        == int(reference_best_lap.get("lap_num", -2))
                    ),
                    None,
                )
        corners = [
            item for item in state.get("analysis", {}).get("corner_metrics", [])
            if int(item.get("lap_num", lap.get("lap_num", 0)) or 0) == int(lap.get("lap_num", 0) or 0)
        ] or list(state.get("analysis", {}).get("corner_metrics", []))
        biggest = max(corners, key=lambda item: float(item.get("loss_vs_pb_s", 0) or 0), default=None)
        instruction = (
            str(biggest.get("instruction") or f"Prioritise the braking and minimum-speed phase at {biggest.get('name', 'the largest-loss corner')}.")
            if biggest else "Bank another valid lap before changing the run plan."
        )
        return {
            "kind": "post_qualifying_lap",
            "available": True,
            "lap_num": int(lap.get("lap_num", 0) or 0),
            "lap_ms": int(lap.get("lap_ms", 0)),
            "sectors_ms": {key[:2]: int(lap.get(key, 0) or 0) for key in ("s1_ms", "s2_ms", "s3_ms")},
            "delta_to_personal_best_s": round((int(lap["lap_ms"]) - personal_best) / 1000.0, 3),
            "personal_best_ms": personal_best,
            "comparisons": comparisons,
            "energy": {
                "player": energy
                or {"available": False, "reason": "Player per-lap energy history is not available."},
                "reference_driver": reference.get("name") if reference else None,
                "reference_best_lap": reference_best_lap,
                "reference": reference_energy
                or {"available": False, "reason": "Reference per-lap energy history is not available."},
                "manual_override_comparison": {
                    "player_used": bool((energy or {}).get("manual_override_used")),
                    "reference_used": bool(
                        (reference_energy or {}).get("manual_override_used")
                    ),
                },
            },
            "speed_trap": {
                "player_kph": player.get("speed_trap_kph") or None,
                "reference_kph": reference.get("speed_trap_kph") if reference else None,
                "reference_driver": reference.get("name") if reference else None,
            },
            "corner_losses": corners,
            "racing_line": state.get("analysis", {}).get("racing_line", {}),
            "verdict": {
                "biggest_loss": biggest,
                "instruction": instruction,
            },
        }

    async def post_race(self) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        session_uid = int(state.get("session_uid", 0) or 0)
        base = await self.database.session_debrief(session_uid)
        classification = dict(state.get("final_classification", {}) or {})
        player_idx = int(state.get("player_car_index", 0) or 0)
        player = next((d for d in state.get("drivers", []) if int(d.get("car_idx", -1)) == player_idx), {})
        positions = list(player.get("position_history", []))
        transitions = []
        for previous, current in pairwise(positions):
            delta = int(previous.get("position", 0)) - int(current.get("position", 0))
            if delta:
                transitions.append({"lap": current.get("lap"), "places": delta})
        biggest_gain = max((item for item in transitions if item["places"] > 0), key=lambda item: item["places"], default=None)
        biggest_loss = min((item for item in transitions if item["places"] < 0), key=lambda item: item["places"], default=None)
        grid = int(classification.get("grid_position", 0) or state.get("grid_position", 0) or 0)
        finish = int(classification.get("position", 0) or 0)
        actual_stints = list(player.get("tyre_stints", []))
        recommended = dict(state.get("strategy", {}).get("recommended", {}) or {})
        return {
            **base,
            "kind": "post_race",
            "available": bool(session_uid and finish),
            "grid_position": grid or None,
            "finish_position": finish or None,
            "net_places": grid - finish if grid and finish else None,
            "position_history": positions,
            "pit_stops": list(player.get("pit_stop_history", [])),
            "actual_strategy": {
                "stints": actual_stints,
                "compounds": [item.get("compound") for item in actual_stints],
                "stops": int(classification.get("pit_stops", 0) or 0),
            },
            "last_recommended_strategy": recommended,
            "strategy_comparison_note": "Comparison uses the last recommendation retained at the chequered flag.",
            "biggest_gain": biggest_gain,
            "biggest_loss": biggest_loss,
            "points": int(classification.get("points", points_for_position(finish)) or 0),
            "penalties_s": int(classification.get("penalties_s", 0) or 0),
        }

    @staticmethod
    def fallback_text(kind: str, payload: dict[str, Any]) -> str:
        """Deterministic narration used when the configured model is unavailable."""
        if kind == "pre_session":
            target = payload.get("realistic_target_lap", {}) or {}
            target_text = (
                f"Target {int(target['lap_time_ms']) / 1000:.3f} seconds, confidence {target.get('confidence', 'low')}."
                if target.get("lap_time_ms") else "No defensible personal target lap yet; confidence low."
            )
            goals = " ".join(
                f"Goal {index}: {goal}" for index, goal in enumerate(payload.get("session_goals", []), start=1)
            )
            return (
                f"{payload.get('track_name')}, {payload.get('mode')} brief. {target_text} "
                f"Pit loss baseline {float(payload.get('pit_loss_s', 0)):.1f} seconds. "
                f"{payload.get('projection_notice')} {goals}"
            )
        if kind == "post_qualifying_lap":
            if not payload.get("available"):
                return str(payload.get("reason", "No qualifying lap is available."))
            verdict = payload.get("verdict", {}) or {}
            return (
                f"Lap {payload.get('lap_num')}, {int(payload.get('lap_ms', 0)) / 1000:.3f} seconds, "
                f"{float(payload.get('delta_to_personal_best_s', 0)):+.3f} to your best. "
                f"{verdict.get('instruction')}"
            )
        if kind == "post_race":
            return (
                f"Race complete: P{payload.get('finish_position')}, "
                f"{int(payload.get('net_places') or 0):+d} places, {int(payload.get('points', 0))} points. "
                f"Next session, {((payload.get('top_time_losses') or [{}])[0].get('name') or 'prioritise repeatable clean laps')}."
            )
        return "Briefing data is available on the dashboard."
