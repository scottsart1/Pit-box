from __future__ import annotations

from statistics import median
from typing import Any

from .analysis import AnalysisEngine, fmt_ms
from .config import settings
from .database import PitWallDatabase
from .setup_advisor import SetupAdvisor
from .state import StateStore
from .strategy import StrategyEngine


class TelemetryTools:
    def __init__(
        self,
        store: StateStore,
        database: PitWallDatabase,
        analysis: AnalysisEngine,
        strategy: StrategyEngine,
        setup_advisor: SetupAdvisor,
    ) -> None:
        self.store = store
        self.database = database
        self.analysis = analysis
        self.strategy = strategy
        self.setup_advisor = setup_advisor

    @staticmethod
    def _resolve_driver(state: dict[str, Any], driver: str) -> dict[str, Any] | None:
        query = driver.strip().lower()
        player_position = int(state.get("player_position", 0))
        if query in {"me", "myself", "player", "my car", "i", "mine"}:
            player_index = int(state.get("player_car_index", 0))
            return next(
                (
                    item
                    for item in state.get("drivers", [])
                    if item.get("car_idx") == player_index
                ),
                None,
            )
        for item in state.get("drivers", []):
            if query == "ahead" and item.get("position") == player_position - 1:
                return item
            if query == "behind" and item.get("position") == player_position + 1:
                return item
            if query == "leader" and item.get("position") == 1:
                return item
            if query == f"p{item.get('position')}":
                return item
            if query and query in item.get("name", "").lower():
                return item
        return None

    async def get_session_overview(self) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        return {
            key: state[key]
            for key in (
                "connected",
                "telemetry_stale",
                "session_type",
                "mode_profile",
                "track_name",
                "current_lap",
                "total_laps",
                "session_time_left_s",
                "player_position",
                "weather",
                "rain_next_15_pct",
                "rain_next_30_pct",
                "safety_car",
                "race_control_phase",
                "red_flag_active",
                "game_paused",
                "packet_rate_hz",
            )
        }

    async def get_standings(
        self,
        top_n: int = 10,
        around_player: bool = True,
    ) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        drivers = sorted(
            (driver for driver in state["drivers"] if driver.get("position")),
            key=lambda item: item["position"],
        )
        if around_player and len(drivers) > top_n:
            position = state["player_position"]
            radius = max(2, top_n // 2)
            drivers = [
                driver
                for driver in drivers
                if abs(driver["position"] - position) <= radius
            ]
        return {
            "drivers": [
                {
                    "position": driver["position"],
                    "name": driver["name"],
                    "gap_to_player_s": driver["gap_to_player_s"],
                    "last_lap": fmt_ms(driver["last_lap_ms"]),
                    "best_lap": fmt_ms(driver["best_lap_ms"]),
                    "tyre": driver["tyre_compound"],
                    "tyre_age": driver["tyre_age"],
                    "pit_stops": driver["pit_stops"],
                    "status": driver["status"],
                }
                for driver in drivers[:top_n]
            ]
        }


    @staticmethod
    def qualifying_summary(
        state: dict[str, Any],
        top_n: int = 8,
    ) -> dict[str, Any]:
        drivers = sorted(
            (
                driver
                for driver in state.get("drivers", [])
                if int(driver.get("best_lap_ms", 0)) > 0
            ),
            key=lambda item: int(item.get("best_lap_ms", 0)),
        )
        session_best_ms = (
            int(drivers[0].get("best_lap_ms", 0)) if drivers else 0
        )
        player_index = int(state.get("player_car_index", 0))
        player = next(
            (
                driver
                for driver in state.get("drivers", [])
                if int(driver.get("car_idx", -1)) == player_index
            ),
            None,
        )
        target = state.get("analysis", {}).get("target", {})
        field = []
        for rank, driver in enumerate(drivers[:top_n], start=1):
            best_ms = int(driver.get("best_lap_ms", 0))
            field.append(
                {
                    "timing_rank": rank,
                    "name": driver.get("name", "Unknown"),
                    "best_lap_ms": best_ms,
                    "best_lap": fmt_ms(best_ms),
                    "delta_to_session_best_s": round(
                        (best_ms - session_best_ms) / 1000,
                        3,
                    )
                    if session_best_ms
                    else None,
                }
            )
        player_best_ms = int(player.get("best_lap_ms", 0)) if player else 0
        target_ms = int(target.get("target_ms", 0) or 0)
        return {
            "available": bool(drivers or target_ms),
            "mode": state.get("mode_profile"),
            "field": field,
            "session_best_ms": session_best_ms,
            "session_best": fmt_ms(session_best_ms),
            "player_best_ms": player_best_ms,
            "player_best": fmt_ms(player_best_ms),
            "theoretical_best_ms": int(target.get("theoretical_ms", 0) or 0),
            "theoretical_best": target.get("theoretical"),
            "target_ms": target_ms,
            "target": target.get("target"),
            "target_basis": target.get("basis"),
            "delta_player_to_target_s": round(
                (player_best_ms - target_ms) / 1000,
                3,
            )
            if player_best_ms and target_ms
            else None,
            "instruction": (
                "Use best-lap times and the target; do not use race gaps in "
                "qualifying unless the driver explicitly asks about traffic."
            ),
        }

    async def get_qualifying_targets(
        self,
        top_n: int = 8,
    ) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        return self.qualifying_summary(state, top_n)

    async def get_session_events(
        self,
        last_n: int = 10,
        types: str = "all",
    ) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        events = state.get("events_log", [])
        if types.strip().lower() not in {"", "all"}:
            allowed = {value.strip().upper() for value in types.split(",")}
            events = [
                event for event in events if event.get("type", "").upper() in allowed
            ]
        return {"events": events[-last_n:]}

    async def get_weather_forecast(self, horizon_min: int = 30) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        samples = [
            sample
            for sample in state.get("weather_forecast", [])
            if sample.get("time_offset_min", 0) <= horizon_min
        ]
        crossover = next(
            (sample for sample in samples if sample.get("rain_pct", 0) >= 60),
            None,
        )
        return {
            "current": state["weather"],
            "track_temp_c": state["track_temp_c"],
            "air_temp_c": state["air_temp_c"],
            "samples": samples,
            "rain_crossover_min": crossover.get("time_offset_min")
            if crossover
            else None,
        }

    async def get_gap(self, target: str = "ahead") -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        match = self._resolve_driver(state, target)
        if not match or match.get("gap_to_player_s") is None:
            return {"available": False, "reason": "Driver or gap unavailable."}
        gap = float(match["gap_to_player_s"])
        return {
            "available": True,
            "driver": match["name"],
            "gap_s": abs(gap),
            "relative": "ahead" if gap < 0 else "behind",
            "last_lap": fmt_ms(match.get("last_lap_ms", 0)),
            "tyre": match.get("tyre_compound"),
            "tyre_age": match.get("tyre_age"),
        }

    async def get_driver_lap_history(
        self,
        driver: str,
        n_laps: int = 5,
    ) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        match = self._resolve_driver(state, driver)
        if not match:
            return {"available": False, "reason": "Driver not found."}
        laps = []
        for item in match.get("lap_history", [])[-n_laps:]:
            laps.append(
                {
                    **item,
                    "lap": fmt_ms(item.get("lap_ms", 0)),
                    "s1": fmt_ms(item.get("s1_ms", 0)),
                    "s2": fmt_ms(item.get("s2_ms", 0)),
                    "s3": fmt_ms(item.get("s3_ms", 0)),
                    "valid": bool(item.get("valid_flags", 0) & 1),
                }
            )
        return {
            "available": bool(laps),
            "driver": match["name"],
            "laps": laps,
            "restricted": match.get("restricted", False),
            "staleness_s": max(
                0.0,
                float(state.get("last_packet_at", 0))
                - float(match.get("history_updated_at", 0)),
            ),
        }

    async def get_rival_pace_analysis(self, driver: str) -> dict[str, Any]:
        history = await self.get_driver_lap_history(driver, 8)
        valid = [
            lap
            for lap in history.get("laps", [])
            if lap.get("lap_ms", 0) > 0 and lap.get("valid")
        ]
        times = [lap["lap_ms"] for lap in valid]
        state = await self.store.snapshot_analysis()
        match = self._resolve_driver(state, driver)
        return {
            **history,
            "compound": match.get("tyre_compound") if match else None,
            "tyre_age": match.get("tyre_age") if match else None,
            "best_lap": fmt_ms(min(times)) if times else None,
            "median_lap": fmt_ms(int(median(times))) if times else None,
            "sample_size": len(times),
            "prediction_note": "Prediction confidence improves after at least three valid laps on the current stint.",
        }

    async def get_driver_stint_info(self, driver: str) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        match = self._resolve_driver(state, driver)
        if not match:
            return {"available": False, "reason": "Driver not found."}
        return {
            "available": bool(match.get("tyre_stints")),
            "driver": match["name"],
            "stints": match.get("tyre_stints", []),
            "pit_stops": match.get("pit_stops", 0),
            "restricted": match.get("restricted", False),
        }

    async def get_position_chart(
        self,
        driver: str = "me",
        laps: int = 20,
    ) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        match = self._resolve_driver(state, driver)
        if not match:
            return {"available": False, "reason": "Driver not found."}
        return {
            "available": bool(match.get("position_history")),
            "driver": match["name"],
            "positions": match.get("position_history", [])[-laps:],
        }

    async def get_my_car_state(self) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        return {
            key: state[key]
            for key in (
                "speed_kph",
                "gear",
                "fuel_kg",
                "fuel_laps_delta",
                "ers_pct",
                "ers_mode",
                "drs_allowed",
                "active_aero_mode",
                "active_aero_available",
                "overtake_available",
                "overtake_active",
                "tyre",
                "damage",
                "penalties_s",
                "warnings",
                "corner_cutting_warnings",
                "fia_flag",
                "race_control_phase",
                "safety_car_delta_s",
                "unserved_drive_through_penalties",
                "unserved_stop_go_penalties",
                "pit_stop_should_serve_penalty",
            )
        }

    async def get_tyre_condition(self, detail: bool = False) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        tyre = state["tyre"]
        wear = tyre.get("wear", [0, 0, 0, 0])
        temps = tyre.get("inner_temps_c", [0, 0, 0, 0])
        average_wear = sum(wear) / len(wear) if wear else 0
        front_temp = sum(temps[:2]) / 2 if len(temps) >= 4 else 0
        rear_temp = sum(temps[2:]) / 2 if len(temps) >= 4 else 0
        deg = state.get("analysis", {}).get("deg_model", {})
        return {
            "compound": tyre["compound"],
            "age_laps": tyre["age_laps"],
            "average_wear_pct": round(average_wear, 1),
            "max_wear_pct": round(max(wear or [0]), 1),
            "wheel_order": ["FL", "FR", "RL", "RR"],
            "wear": wear if detail else None,
            "inner_temps_c": temps if detail else None,
            "front_to_rear_temp_delta_c": round(front_temp - rear_temp, 1),
            "blisters": tyre.get("blisters", []),
            "deg_s_per_lap": deg.get("current_slope_s_per_lap"),
            "projected_cliff_lap": deg.get("projected_cliff_lap"),
        }

    async def get_fuel_state(self) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        model = state.get("analysis", {}).get("fuel_model", {})
        return {
            "fuel_kg": state["fuel_kg"],
            "fuel_remaining_laps": state["fuel_remaining_laps"],
            "fuel_laps_delta": state["fuel_laps_delta"],
            **model,
        }

    async def get_ers_report(self) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        return {
            "store_pct": state["ers_pct"],
            "deploy_mode": state["ers_mode"],
            "deployed_this_lap_j": state["ers_deployed_lap_j"],
            "harvested_this_lap_j": state["ers_harvested_lap_j"],
            "overtake_available": state["overtake_available"],
            "overtake_active": state["overtake_active"],
        }

    async def get_pace_mode_options(self) -> dict[str, Any]:
        """Deterministic fuel-vs-pace trade for the current fuel margin.

        fuel_laps_delta is the margin in laps: negative means the tank is short
        of race distance and the driver must save. Each lap of fuel recovered by
        lifting and coasting costs a fixed per-lap time, so the required saving
        translates directly into a lap-time cost.
        """
        state = await self.store.snapshot_analysis()
        delta = float(state.get("fuel_laps_delta", 0.0))
        laps_remaining = max(
            0,
            int(state.get("total_laps", 0)) - int(state.get("current_lap", 0)),
        )
        rate = settings.strategy_fuel_save_s_per_lap
        if delta < -0.05:
            deficit = abs(delta)
            per_lap_cost = (
                round(deficit * rate / laps_remaining, 3) if laps_remaining else None
            )
            recommendation = (
                f"Save {deficit:.1f} laps of fuel. Lift and coast into the heaviest "
                "braking zones; expect a small lap-time cost spread over the stint."
            )
            mode = "fuel_save"
        elif delta > 0.6:
            per_lap_cost = None
            recommendation = (
                f"Fuel is healthy (+{delta:.1f} laps). You can run a richer mix and "
                "push; no lift-and-coast required."
            )
            mode = "push"
        else:
            per_lap_cost = None
            recommendation = "Fuel is on target. Hold the current pace mode."
            mode = "neutral"
        return {
            "recommended_mode": mode,
            "fuel_laps_delta": round(delta, 2),
            "laps_remaining": laps_remaining,
            "estimated_cost_s_per_lap": per_lap_cost,
            "recommendation": recommendation,
        }

    async def predict_rival_strategy(self, top_n: int = 6) -> dict[str, Any]:
        return await self.strategy.predict_rival_strategy(top_n)

    async def get_championship_scenario(self) -> dict[str, Any]:
        return await self.strategy.championship_scenario()

    async def get_damage_report(self) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        damage = state["damage"]
        wing = max(
            int(damage.get("front_left_wing", 0)),
            int(damage.get("front_right_wing", 0)),
        )
        estimated_cost = round(wing * 0.012 + int(damage.get("floor", 0)) * 0.018, 2)
        return {
            "available": bool(damage),
            "damage": damage,
            "estimated_lap_cost_s": estimated_cost,
        }

    async def get_car_setup(self) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        return {
            "available": bool(state.get("car_setup")),
            "setup": state.get("car_setup", {}),
            "next_front_wing_value": state.get("next_front_wing_value", 0),
        }

    async def get_lap_analysis(
        self,
        lap: str = "last",
        compare_to: str = "personal_best",
    ) -> dict[str, Any]:
        return await self.analysis.get_lap_analysis(lap, compare_to)

    async def get_corner_analysis(
        self,
        corner: str,
        last_n_laps: int = 3,
    ) -> dict[str, Any]:
        return await self.analysis.get_corner_analysis(corner, last_n_laps)

    async def get_racing_line_analysis(
        self,
        lap: str = "last",
    ) -> dict[str, Any]:
        return await self.analysis.get_racing_line_analysis(lap)

    async def get_stored_history(
        self,
        scope: str = "current_session",
        limit: int = 30,
    ) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        session_uid = int(state.get("session_uid", 0)) if scope == "current_session" else None
        track_id = int(state.get("track_id", -1)) if scope in {"current_session", "current_track"} else None
        return await self.database.history_query(track_id=track_id, session_uid=session_uid, limit=limit)

    async def get_attack_plan(self, driver: str = "ahead") -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        target = self._resolve_driver(state, driver)
        if not target or target.get("gap_to_player_s") is None:
            return {"available": False, "reason": "Target driver or live gap is unavailable."}
        gap = abs(float(target.get("gap_to_player_s", 0.0)))
        own_tyre = state.get("tyre", {})
        rival_age = int(target.get("tyre_age", 0))
        own_age = int(own_tyre.get("age_laps", 0))
        tyre_offset = rival_age - own_age
        line = state.get("analysis", {}).get("racing_line", {})
        corner = state.get("analysis", {}).get("flagged_corners", [])
        assist_name = "Manual Override" if state.get("regulations_2026") else "DRS"
        preparation = (
            f"Stay within the {assist_name} activation window and prioritise the "
            "exit onto the longest straight."
        )
        if state.get("ers_pct", 0) < 25:
            preparation = (
                f"Harvest for one lap, stay in the {assist_name} window, then "
                "attack with a fuller battery."
            )
        elif gap > 1.0:
            preparation = f"Close the {gap:.1f}s gap without overheating the tyres; do not spend full energy yet."
        opportunity = line.get("top_opportunity") or (corner[0].get("instruction") if corner else None)
        return {
            "available": True,
            "driver": target.get("name"),
            "gap_s": round(gap, 2),
            "own_tyre": {"compound": own_tyre.get("compound"), "age": own_age, "wear": own_tyre.get("wear")},
            "rival_tyre": {"compound": target.get("tyre_compound"), "age": rival_age},
            "tyre_age_offset_laps": tyre_offset,
            "ers_pct": state.get("ers_pct"),
            "overtaking_assist": assist_name,
            "assist_available": (
                state.get("overtake_available")
                if state.get("regulations_2026")
                else state.get("drs_allowed")
            ),
            "preparation": preparation,
            "driving_opportunity": opportunity,
            "attack_window": "next lap" if gap <= 1.0 and state.get("ers_pct", 0) >= 30 else "build the gap/energy first",
            "note": "Opponent ERS is not assumed when telemetry does not expose it.",
        }

    async def get_defence_plan(self, driver: str = "behind") -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        target = self._resolve_driver(state, driver)
        if not target or target.get("gap_to_player_s") is None:
            return {"available": False, "reason": "Target driver or live gap is unavailable."}
        gap = abs(float(target.get("gap_to_player_s", 0.0)))
        assist_name = "Manual Override" if state.get("regulations_2026") else "DRS"
        recommendation = (
            "Prioritise exits and deploy energy where the following car is most "
            f"likely to use {assist_name}."
        )
        if gap > 1.2:
            recommendation = "Do not compromise your line yet; build a clean exit and manage tyres."
        elif state.get("ers_pct", 0) < 20:
            recommendation = "Use positioning rather than battery; harvest where the rival cannot pass."
        return {
            "available": True,
            "driver": target.get("name"),
            "gap_s": round(gap, 2),
            "rival_tyre": {"compound": target.get("tyre_compound"), "age": target.get("tyre_age")},
            "ers_pct": state.get("ers_pct"),
            "recommendation": recommendation,
            "warning": "Avoid weaving or reactive moves; make one clear defensive choice.",
        }

    async def get_setup_learning(
        self,
        profile: str = "race",
    ) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        return await self.database.setup_learning_summary(int(state.get("track_id", -1)), profile, 20)

    async def get_consistency_report(self) -> dict[str, Any]:
        return await self.analysis.get_consistency()

    async def get_target_lap_time(self) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        return state.get("analysis", {}).get("target", {})

    async def get_pit_strategy(self) -> dict[str, Any]:
        return await self.strategy.get_plan()

    async def evaluate_undercut(self, driver: str = "ahead") -> dict[str, Any]:
        return await self.strategy.evaluate_undercut(driver)

    async def evaluate_overcut(self, driver: str = "ahead") -> dict[str, Any]:
        return await self.strategy.evaluate_overcut(driver)

    async def get_tyre_sets_available(self) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        return {
            "available": bool(state.get("tyre_sets")),
            "fitted_index": state.get("fitted_tyre_set_idx"),
            "sets": state.get("tyre_sets", []),
        }

    async def what_if(self, scenario: str) -> dict[str, Any]:
        return await self.strategy.what_if(scenario)

    async def get_personal_history(self, track: str = "current") -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        track_id = int(state.get("track_id", -1))
        review = await self.database.track_review(track_id, 30)
        personal_best = await self.database.get_personal_best(track_id)
        return {
            "track": state.get("track_name") if track == "current" else track,
            "personal_best": fmt_ms(personal_best.get("lap_time_ms", 0))
            if personal_best
            else None,
            "recent_laps": review.get("laps", []),
            "typical_weak_corners": review.get("corner_opportunities", [])[:5],
        }

    async def get_sector_bests(self) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        track_id = int(state.get("track_id", -1))
        data = await self.database.sector_bests(track_id)
        return {
            "track": state.get("track_name"),
            "sector_bests": {
                key: (fmt_ms(int(value["ms"])) if value else None)
                for key, value in data.get("sector_bests", {}).items()
            },
            "personal_best": fmt_ms(data["personal_best_lap_ms"])
            if data.get("personal_best_lap_ms")
            else None,
            "theoretical_best": fmt_ms(data["theoretical_best_ms"])
            if data.get("theoretical_best_ms")
            else None,
            "time_left_on_table_s": (
                round(data["time_left_on_table_ms"] / 1000, 3)
                if data.get("time_left_on_table_ms") is not None
                else None
            ),
        }

    async def get_progress_trend(self) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        track_id = int(state.get("track_id", -1))
        data = await self.database.progress_trend(track_id, 20)
        sessions = [
            {
                "session_type": row.get("session_type"),
                "best_lap": fmt_ms(int(row["best_lap_ms"])) if row.get("best_lap_ms") else None,
                "valid_laps": row.get("valid_laps"),
            }
            for row in data.get("sessions", [])
        ]
        improvement = data.get("improvement_ms")
        return {
            "track": state.get("track_name"),
            "session_count": data.get("session_count", 0),
            "sessions": sessions,
            "improvement_s": round(improvement / 1000, 3) if improvement is not None else None,
            "faster_than_first": bool(improvement is not None and improvement < 0),
        }

    async def get_setup_correlation(self, profile: str = "all") -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        track_id = int(state.get("track_id", -1))
        data = await self.database.setup_correlation(
            track_id, None if profile in {"all", ""} else profile
        )
        return {
            "track": state.get("track_name"),
            "profile": profile,
            "run_count": data.get("run_count", 0),
            "best_run": data.get("best_run"),
            "worst_run": data.get("worst_run"),
        }

    async def generate_setup(
        self,
        profile: str = "hybrid",
        track_id: int = -1,
    ) -> dict[str, Any]:
        return await self.setup_advisor.generate(profile, None if track_id < 0 else track_id)

    async def get_front_wing_adjustment(self) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        recommendation = state.get("setup_recommendation", {})
        if not recommendation:
            recommendation = await self.setup_advisor.generate("hybrid")
        return recommendation.get("pit_adjustment", recommendation)

    def schemas(self) -> list[dict[str, Any]]:
        definitions = [
            (
                "get_session_overview",
                "Get session, track, lap, weather, race-control and telemetry health.",
                {},
            ),
            (
                "get_standings",
                "Get live standings, gaps, laps and tyres.",
                {
                    "top_n": {"type": "integer", "minimum": 1, "maximum": 24},
                    "around_player": {"type": "boolean"},
                },
            ),
            (
                "get_qualifying_targets",
                (
                    "Get the leading best laps, the player's best, theoretical "
                    "best and target lap for qualifying. Do not use race gaps."
                ),
                {
                    "top_n": {"type": "integer", "minimum": 1, "maximum": 24},
                },
            ),
            (
                "get_session_events",
                "Get recent safety-car, penalty, overtake, retirement and session events.",
                {
                    "last_n": {"type": "integer", "minimum": 1, "maximum": 50},
                    "types": {"type": "string"},
                },
            ),
            (
                "get_weather_forecast",
                "Get current weather and forecast samples through a horizon.",
                {"horizon_min": {"type": "integer", "minimum": 5, "maximum": 60}},
            ),
            (
                "get_gap",
                "Get the live gap to a named driver, ahead, behind, leader, or position.",
                {"target": {"type": "string"}},
            ),
            (
                "get_driver_lap_history",
                "Get exact recent lap and sector history for a driver or the player.",
                {
                    "driver": {"type": "string"},
                    "n_laps": {"type": "integer", "minimum": 1, "maximum": 20},
                },
            ),
            (
                "get_rival_pace_analysis",
                "Analyze a driver's recent valid lap pace and current stint.",
                {"driver": {"type": "string"}},
            ),
            (
                "get_driver_stint_info",
                "Get a driver's tyre stint history and stop count.",
                {"driver": {"type": "string"}},
            ),
            (
                "get_position_chart",
                "Get position by lap for a driver.",
                {
                    "driver": {"type": "string"},
                    "laps": {"type": "integer", "minimum": 1, "maximum": 50},
                },
            ),
            (
                "get_my_car_state",
                "Get player speed, fuel, ERS, tyres, damage, active aero and penalties.",
                {},
            ),
            (
                "get_tyre_condition",
                "Get tyre wear, wheel temperatures, degradation and projected cliff.",
                {"detail": {"type": "boolean"}},
            ),
            (
                "get_fuel_state",
                "Get fuel level, burn, race-distance delta and lift-and-coast requirement.",
                {},
            ),
            (
                "get_ers_report",
                "Get ERS store, harvesting, deployment and overtake state.",
                {},
            ),
            ("get_damage_report", "Get player damage and estimated lap-time cost.", {}),
            ("get_car_setup", "Get the current setup and queued front-wing value.", {}),
            (
                "get_lap_analysis",
                "Analyze a lap against the personal best or session reference.",
                {"lap": {"type": "string"}, "compare_to": {"type": "string"}},
            ),
            (
                "get_corner_analysis",
                "Analyze a learned lap-map corner over recent laps.",
                {
                    "corner": {"type": "string"},
                    "last_n_laps": {"type": "integer", "minimum": 1, "maximum": 10},
                },
            ),
            (
                "get_racing_line_analysis",
                "Compare the player's world-coordinate racing line with the personal-best line and identify persistent deviation zones.",
                {"lap": {"type": "string"}},
            ),
            (
                "get_stored_history",
                "Query persisted sessions, laps, strategy snapshots, radio calls and racing-line metrics.",
                {
                    "scope": {"type": "string", "enum": ["current_session", "current_track", "all"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
            ),
            (
                "get_attack_plan",
                "Build a live attack plan against a rival using gap, tyres, energy deployment, overtaking assistance and driving evidence.",
                {"driver": {"type": "string"}},
            ),
            (
                "get_defence_plan",
                "Build a live defence plan against a rival using gap, tyres and ERS.",
                {"driver": {"type": "string"}},
            ),
            (
                "get_setup_learning",
                "Get stored setup-performance evidence for the current track and profile.",
                {"profile": {"type": "string", "enum": ["race", "quali", "hybrid"]}},
            ),
            (
                "get_consistency_report",
                "Get lap consistency and repeated corner opportunities.",
                {},
            ),
            (
                "get_target_lap_time",
                "Get the mode-aware target, session best and theoretical best.",
                {},
            ),
            (
                "get_pit_strategy",
                "Get ranked tyre-and-lap-stop plans with recommended compound and rejoin estimate.",
                {},
            ),
            (
                "evaluate_undercut",
                "Evaluate an undercut against a driver.",
                {"driver": {"type": "string"}},
            ),
            (
                "evaluate_overcut",
                "Evaluate an overcut against a driver.",
                {"driver": {"type": "string"}},
            ),
            (
                "get_tyre_sets_available",
                "Get all available tyre sets, life and wear.",
                {},
            ),
            (
                "what_if",
                "Simulate a tyre/lap-stop scenario such as box lap 18 for hards.",
                {"scenario": {"type": "string"}},
            ),
            (
                "get_personal_history",
                "Get personal best, recent laps and recurring weak corners at a track.",
                {"track": {"type": "string"}},
            ),
            (
                "generate_setup",
                "Generate a learned Race, Quali, or Hybrid setup recommendation.",
                {
                    "profile": {"type": "string", "enum": ["race", "quali", "hybrid"]},
                    "track_id": {"type": "integer", "minimum": -1, "maximum": 100},
                },
            ),
            (
                "get_front_wing_adjustment",
                "Get the recommended front-wing change for the next pit stop.",
                {},
            ),
            (
                "get_sector_bests",
                (
                    "Get best-ever sector times for this track and the "
                    "theoretical-best lap they compose, versus the real best lap."
                ),
                {},
            ),
            (
                "get_progress_trend",
                (
                    "Get the driver's best lap per past session at this track "
                    "over time to show improvement or regression."
                ),
                {},
            ),
            (
                "get_setup_correlation",
                (
                    "Compare stored setup runs at this track to link setup "
                    "choices with measured performance. profile: race/quali/hybrid/all."
                ),
                {"profile": {"type": "string"}},
            ),
            (
                "get_pace_mode_options",
                (
                    "Get the fuel-save versus push trade for the current fuel "
                    "margin, with the estimated lap-time cost of saving."
                ),
                {},
            ),
            (
                "predict_rival_strategy",
                (
                    "Estimate nearby rivals' likely next pit laps from tyre age "
                    "and compound, flagging pre-emptive undercut threats."
                ),
                {"top_n": {"type": "integer", "minimum": 1, "maximum": 12}},
            ),
            (
                "get_championship_scenario",
                (
                    "Attach championship points to each strategy plan's projected "
                    "finish position to weigh a safe finish against a gamble."
                ),
                {},
            ),
        ]
        return [
            self._schema(name, description, properties, list(properties.keys()))
            for name, description, properties in definitions
        ]

    @staticmethod
    def _schema(
        name: str,
        description: str,
        properties: dict[str, Any],
        required: list[str],
    ) -> dict[str, Any]:
        return {
            "type": "function",
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
            "strict": True,
        }

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        function = getattr(self, name, None)
        if function is None or name.startswith("_"):
            return {"error": f"Unknown tool: {name}"}
        return await function(**arguments)
