from __future__ import annotations

from statistics import median
from typing import Any

from .analysis import AnalysisEngine, fmt_ms, theil_sen
from .config import settings
from .database import PitWallDatabase
from .identity import display_name, match_drivers
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
        """Resolve a spoken driver reference to a car.

        Accepts the player, a relative position ("ahead", "behind", "leader"),
        the teammate, a grid slot ("p3"), a car number, and any name the driver
        might use — first name, surname, full name or nickname — through the
        participant identity table. The previous substring match on the packet
        surname meant "Max" and "car 1" never resolved at all.
        """
        query = driver.strip().lower()
        player_position = int(state.get("player_position", 0))
        if query in {"me", "myself", "player", "my car", "i", "mine"}:
            player_index = int(state.get("player_car_index", 0))
            drivers = list(state.get("drivers", []))
            matched = next(
                (
                    item
                    for item in drivers
                    if item.get("car_idx") == player_index
                ),
                None,
            )
            if matched is not None:
                return matched
            # Some replay/test feeds omit car_idx but preserve F1 packet order.
            # Falling back to the indexed row is safer than reporting that the
            # player's own timing data is unavailable.
            if 0 <= player_index < len(drivers):
                return drivers[player_index]
            return None
        drivers = list(state.get("drivers", []))
        for item in drivers:
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
            if query in {"teammate", "team mate"} and item.get("is_teammate"):
                return item
            if query == f"p{position}":
                return item

        # Name, nickname, full name or car number, matched on whole tokens.
        matches = match_drivers(drivers, query)
        if matches:
            return matches[0]

        # Last resort: the original loose surname containment, so an unusual
        # online display name still resolves.
        for item in drivers:
            name = str(item.get("name", "")).lower()
            if query and name and query in name:
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


    @staticmethod
    def _gap_trend(driver: dict[str, Any]) -> dict[str, Any]:
        history = list(driver.get("gap_history", []) or [])
        if len(history) < 2:
            return {"trend": "unknown", "gap_change_s": None, "window_s": None}
        latest = history[-1]
        latest_time = float(latest.get("session_time_s", 0.0))
        anchor = None
        for sample in reversed(history[:-1]):
            if latest_time - float(sample.get("session_time_s", latest_time)) >= 8.0:
                anchor = sample
                break
        if anchor is None:
            anchor = history[0]
        window = latest_time - float(anchor.get("session_time_s", latest_time))
        if window < 3.0:
            return {"trend": "unknown", "gap_change_s": None, "window_s": None}
        current_gap = abs(float(latest.get("gap_s", 0.0)))
        prior_gap = abs(float(anchor.get("gap_s", 0.0)))
        change = round(current_gap - prior_gap, 2)
        if change <= -0.15:
            trend = "closing"
        elif change >= 0.15:
            trend = "falling_back"
        else:
            trend = "steady"
        return {
            "trend": trend,
            "gap_change_s": change,
            "window_s": round(window, 1),
        }

    async def get_cars_ahead_progress(self, top_n: int = 4) -> dict[str, Any]:
        """Return current gaps and measured gap trend for the nearest cars ahead.

        Closing status comes from live gap samples, never from a sign-ambiguous
        comparison of one latest lap. Latest-lap pace is included as supporting
        evidence only.
        """
        state = await self.store.snapshot_analysis()
        player_index = int(state.get("player_car_index", -1))
        player = next(
            (
                item
                for item in state.get("drivers", [])
                if int(item.get("car_idx", -2)) == player_index
            ),
            None,
        )
        player_last_ms = int((player or {}).get("last_lap_ms", state.get("last_lap_ms", 0)) or 0)
        ahead = sorted(
            (
                driver
                for driver in state.get("drivers", [])
                if driver.get("gap_to_player_s") is not None
                and float(driver.get("gap_to_player_s", 0.0)) < 0
            ),
            key=lambda driver: abs(float(driver.get("gap_to_player_s", 0.0))),
        )[: max(1, min(12, int(top_n)))]
        cars: list[dict[str, Any]] = []
        for driver in ahead:
            rival_last_ms = int(driver.get("last_lap_ms", 0) or 0)
            pace_delta_s = (
                round((player_last_ms - rival_last_ms) / 1000.0, 3)
                if player_last_ms > 0 and rival_last_ms > 0
                else None
            )
            cars.append(
                {
                    "position": int(driver.get("position", 0)),
                    "driver": driver.get("name", "Unknown"),
                    "gap_s": round(abs(float(driver.get("gap_to_player_s", 0.0))), 2),
                    "last_lap": fmt_ms(rival_last_ms),
                    "player_last_lap": fmt_ms(player_last_ms),
                    "pace_delta_s": pace_delta_s,
                    "tyre": driver.get("tyre_compound"),
                    "tyre_age": driver.get("tyre_age"),
                    **self._gap_trend(driver),
                }
            )
        return {
            "available": bool(cars) and bool(state.get("connected")),
            "telemetry_stale": bool(state.get("telemetry_stale")),
            "cars": cars,
            "interpretation": (
                "pace_delta_s is player lap minus rival lap: positive means the player was slower."
            ),
        }

    @staticmethod
    def _wheel_map(values: Any) -> dict[str, float] | None:
        """Label a four-wheel array, or None when the car reports nothing."""
        items = [float(value) for value in (values or [])]
        if len(items) != 4 or not any(items):
            return None
        return dict(zip(("FL", "FR", "RL", "RR"), (round(v, 1) for v in items)))

    def _car_summary(self, driver: dict[str, Any], detail: bool = False) -> dict[str, Any]:
        """One car's public state, omitting anything the game withholds.

        Restricted cars (online telemetry privacy) report structural zeros. They
        are reported as absent fields rather than as real zeros, so the engineer
        says "not available" instead of "no wear".
        """
        restricted = bool(driver.get("restricted"))
        gap = driver.get("gap_to_player_s")
        summary: dict[str, Any] = {
            "car_idx": driver.get("car_idx"),
            "driver": display_name(driver),
            "team": driver.get("team") or None,
            "position": driver.get("position") or None,
            "gap_to_player_s": round(float(gap), 2) if gap is not None else None,
            "relative": (
                None
                if gap is None or driver.get("is_player")
                else ("ahead" if float(gap) < 0 else "behind")
            ),
            "tyre": driver.get("tyre_compound"),
            "tyre_age_laps": driver.get("tyre_age"),
            "pit_stops": driver.get("pit_stops"),
            "last_lap": fmt_ms(int(driver.get("last_lap_ms", 0) or 0)),
            "best_lap": fmt_ms(int(driver.get("best_lap_ms", 0) or 0)),
            "status": driver.get("status"),
            "is_teammate": bool(driver.get("is_teammate")),
            "telemetry_restricted": restricted,
        }
        result_label = str(driver.get("result_label") or "")
        if result_label and result_label not in {"active", "invalid"}:
            summary["result"] = result_label
        if driver.get("pit_lane_timer_active"):
            summary["in_pit_lane"] = True
            summary["pit_lane_time_s"] = round(
                int(driver.get("pit_lane_time_ms", 0) or 0) / 1000.0, 1
            )
            stationary = int(driver.get("pit_stop_timer_ms", 0) or 0)
            if stationary:
                summary["stationary_time_s"] = round(stationary / 1000.0, 1)
        if restricted:
            return summary

        wear = self._wheel_map(driver.get("tyre_wear"))
        if wear is not None:
            summary["tyre_wear_pct"] = wear
            summary["max_tyre_wear_pct"] = max(wear.values())
        if driver.get("ers_pct") is not None:
            summary["ers_pct"] = driver.get("ers_pct")
        if driver.get("fuel_remaining_laps") is not None:
            summary["fuel_laps_delta"] = driver.get("fuel_remaining_laps")
        damage = driver.get("damage") or {}
        significant = {
            key: value
            for key, value in damage.items()
            if isinstance(value, int) and value > 0
        }
        faults = [key for key in ("drs_fault", "ers_fault") if damage.get(key)]
        if significant or faults:
            summary["damage"] = {**significant, **{key: True for key in faults}}
        if not detail:
            return summary

        summary["grid_position"] = driver.get("grid_position") or None
        summary["speed_kph"] = driver.get("speed_kph") or None
        summary["drs_open"] = bool(driver.get("drs_open"))
        summary["overtake_active"] = bool(driver.get("overtake_active"))
        summary["overtake_available"] = bool(driver.get("overtake_available"))
        summary["tyre_inner_temps_c"] = self._wheel_map(
            driver.get("tyre_inner_temps_c")
        )
        summary["blisters_pct"] = self._wheel_map(driver.get("blisters"))
        summary["component_wear_pct"] = {
            key: round(float(value), 1)
            for key, value in (driver.get("component_wear") or {}).items()
            if float(value) > 0
        } or None
        summary["stints"] = driver.get("tyre_stints", [])
        summary["penalties_s"] = driver.get("penalties_s") or None
        speed_trap = float(driver.get("speed_trap_kph", 0.0) or 0.0)
        summary["speed_trap_kph"] = round(speed_trap, 1) if speed_trap else None
        return summary

    # ---------------------------------------------------------------- analysis
    #
    # The tools above report state. These interpret it. A driver mid-corner
    # cannot do arithmetic on three lap times and a gap history, so answering
    # "how am I doing against Perez" with a list of numbers puts the work back
    # on the person least able to do it. Each of these returns a conclusion and
    # the evidence for it, computed deterministically so the spoken numbers stay
    # grounded.

    @staticmethod
    def _valid_laps(driver: dict[str, Any], count: int = 5) -> list[dict[str, Any]]:
        laps = [
            lap
            for lap in (driver.get("lap_history") or [])
            if int(lap.get("lap_ms", 0) or 0) > 0 and int(lap.get("valid_flags", 0)) & 1
        ]
        return laps[-max(1, count):]

    @staticmethod
    def _pace_trend_s_per_lap(laps: list[dict[str, Any]]) -> float | None:
        """Robust seconds-per-lap trend. Positive means getting slower."""
        points = [
            (float(lap.get("lap_num", index)), float(lap["lap_ms"]) / 1000.0)
            for index, lap in enumerate(laps)
            if int(lap.get("lap_ms", 0) or 0) > 0
        ]
        fit = theil_sen(points)
        return round(fit[0], 3) if fit else None

    @staticmethod
    def _closing_rate_s_per_lap(driver: dict[str, Any]) -> tuple[float | None, float | None]:
        """Gap change per lap, and laps until contact at that rate.

        Negative rate means the gap is shrinking. ``None`` when the samples do
        not span enough time to say anything honest.
        """
        history = [
            sample
            for sample in (driver.get("gap_history") or [])
            if sample.get("gap_s") is not None
        ]
        if len(history) < 2:
            return None, None
        first, last = history[0], history[-1]
        laps = float(last.get("lap", 0)) - float(first.get("lap", 0))
        if laps < 1.0:
            # Fall back to elapsed time when the samples sit inside one lap.
            seconds = float(last.get("session_time_s", 0)) - float(
                first.get("session_time_s", 0)
            )
            if seconds < 20.0:
                return None, None
            laps = seconds / 90.0
        change = abs(float(last["gap_s"])) - abs(float(first["gap_s"]))
        rate = round(change / laps, 3)
        gap = abs(float(last["gap_s"]))
        laps_to_contact = (
            round(gap / abs(rate), 1) if rate < -0.05 and gap > 0 else None
        )
        return rate, laps_to_contact

    async def get_pace_verdict(self, driver: str = "ahead") -> dict[str, Any]:
        """Judge the player's pace against one rival, and say where it is going.

        Answers "am I actually catching him, and why" rather than reporting two
        lap times and leaving the comparison to the driver. Sector attribution
        names where the time moves; tyre and stop context says whether the
        difference is the car or the rubber.
        """
        state = await self.store.snapshot_analysis()
        rival = self._resolve_driver(state, driver)
        player = self._resolve_driver(state, "me")
        if not rival or not player:
            return {"available": False, "reason": f"No car matches '{driver}'."}
        if rival.get("car_idx") == player.get("car_idx"):
            return {"available": False, "reason": "That is your own car."}

        player_laps = self._valid_laps(player)
        rival_laps = self._valid_laps(rival)
        player_median = (
            median(int(lap["lap_ms"]) for lap in player_laps) if player_laps else 0
        )
        rival_median = (
            median(int(lap["lap_ms"]) for lap in rival_laps) if rival_laps else 0
        )
        pace_delta_s = (
            round((player_median - rival_median) / 1000.0, 3)
            if player_median and rival_median
            else None
        )

        # Where the time actually goes, from the most recent lap both completed.
        sector_deltas: dict[str, float] = {}
        comparison = await self.get_rival_sector_comparison(
            str(rival.get("name") or driver)
        )
        if comparison.get("available"):
            sector_deltas = comparison.get("sector_deltas_s", {})
        dominant = (
            max(sector_deltas.items(), key=lambda item: abs(float(item[1])))
            if sector_deltas
            else None
        )

        gap = rival.get("gap_to_player_s")
        rate, laps_to_contact = self._closing_rate_s_per_lap(rival)
        ahead = gap is not None and float(gap) < 0

        if rate is None:
            verdict = "not enough gap history to call the trend"
        elif rate <= -0.05:
            verdict = "closing" if ahead else "being caught"
        elif rate >= 0.05:
            verdict = "dropping back" if ahead else "pulling away"
        else:
            verdict = "holding station"

        # The measured gap trend and raw lap-time pace can disagree — a stop, a
        # slow lap in traffic or a safety car all break the link between them.
        # Saying so is better than asserting a confident call built on two
        # signals that contradict each other.
        conflict = None
        if rate is not None and pace_delta_s is not None:
            closing = rate <= -0.05
            quicker = pace_delta_s < -0.05
            if closing and not quicker and abs(pace_delta_s) > 0.15:
                conflict = (
                    "the gap is shrinking while raw lap times say he is quicker; "
                    "likely traffic, a stop or an out-lap rather than pace"
                )
            elif not closing and quicker and abs(pace_delta_s) > 0.15:
                conflict = (
                    "your lap times are quicker but the gap is not coming down; "
                    "check for traffic or a slow sector"
                )

        # Tyre offset explains a large part of most pace differences.
        own_age = int((state.get("tyre") or {}).get("age_laps", 0) or 0)
        rival_age = int(rival.get("tyre_age", 0) or 0)
        own_compound = str((state.get("tyre") or {}).get("compound", "UNKNOWN"))
        rival_compound = str(rival.get("tyre_compound", "UNKNOWN"))
        tyre_note = None
        if rival_compound not in {"UNKNOWN", ""} and own_compound not in {"UNKNOWN", ""}:
            offset = rival_age - own_age
            if own_compound != rival_compound:
                tyre_note = (
                    f"different compound: you {own_compound.lower()} {own_age} laps, "
                    f"him {rival_compound.lower()} {rival_age} laps"
                )
            elif abs(offset) >= 3:
                fresher = "his" if offset < 0 else "yours"
                tyre_note = f"{fresher} are {abs(offset)} laps fresher on the same compound"

        return {
            "available": True,
            "driver": display_name(rival),
            "position": rival.get("position"),
            "relative": None if gap is None else ("ahead" if ahead else "behind"),
            "gap_s": None if gap is None else round(abs(float(gap)), 2),
            "verdict": verdict,
            "gap_change_s_per_lap": rate,
            "laps_to_contact": laps_to_contact,
            "pace_delta_s_per_lap": pace_delta_s,
            "pace_interpretation": (
                None
                if pace_delta_s is None
                else (
                    f"you are {abs(pace_delta_s):.2f}s per lap "
                    + ("slower" if pace_delta_s > 0 else "faster")
                )
            ),
            "your_median_lap": fmt_ms(int(player_median)) if player_median else None,
            "his_median_lap": fmt_ms(int(rival_median)) if rival_median else None,
            "your_trend_s_per_lap": self._pace_trend_s_per_lap(player_laps),
            "his_trend_s_per_lap": self._pace_trend_s_per_lap(rival_laps),
            "sector_deltas_s": sector_deltas or None,
            "losing_most_in": (
                dominant[0].upper() if dominant and float(dominant[1]) > 0 else None
            ),
            "gaining_most_in": (
                dominant[0].upper() if dominant and float(dominant[1]) < 0 else None
            ),
            "signal_conflict": conflict,
            "tyre_context": tyre_note,
            "his_stops": rival.get("pit_stops"),
            "telemetry_restricted": bool(rival.get("restricted")),
            "interpretation": (
                "Positive sector or pace delta means the player is slower. "
                "gap_change_s_per_lap is negative when the gap is shrinking."
            ),
        }

    async def get_race_picture(self) -> dict[str, Any]:
        """One call that says where the race actually stands and what to do.

        Built for a driver who cannot read a dashboard: the single real threat,
        the single real opportunity, whether their own pace is holding, and the
        one thing that follows from it. Everything is derived from the same
        deterministic state the other tools report.
        """
        state = await self.store.snapshot_analysis()
        player = self._resolve_driver(state, "me")
        position = int(state.get("player_position", 0) or 0)
        running = sorted(
            (
                d
                for d in state.get("drivers", [])
                if int(d.get("position", 0) or 0) > 0 and not d.get("is_player")
            ),
            key=lambda item: int(item["position"]),
        )

        own_laps = self._valid_laps(player or {}, 6)
        own_trend = self._pace_trend_s_per_lap(own_laps)
        own_median = median(int(l["lap_ms"]) for l in own_laps) if own_laps else 0

        threats: list[dict[str, Any]] = []
        opportunities: list[dict[str, Any]] = []
        for rival in running:
            gap = rival.get("gap_to_player_s")
            if gap is None or rival.get("pit_lane_timer_active"):
                continue
            rate, laps_to_contact = self._closing_rate_s_per_lap(rival)
            entry = {
                "driver": display_name(rival),
                "position": rival.get("position"),
                "gap_s": round(abs(float(gap)), 2),
                "gap_change_s_per_lap": rate,
                "laps_to_contact": laps_to_contact,
                "tyre": rival.get("tyre_compound"),
                "tyre_age_laps": rival.get("tyre_age"),
                "pit_stops": rival.get("pit_stops"),
            }
            behind = int(rival.get("position", 0) or 0) > position > 0
            if behind and rate is not None and rate <= -0.05:
                threats.append(entry)
            elif not behind and rate is not None and rate <= -0.05:
                opportunities.append(entry)

        threats.sort(key=lambda e: (e["laps_to_contact"] or 1e9, e["gap_s"]))
        opportunities.sort(key=lambda e: (e["laps_to_contact"] or 1e9, e["gap_s"]))

        tyre = state.get("tyre", {}) or {}
        wear = [float(v) for v in (tyre.get("wear") or [0, 0, 0, 0])]
        deg = (state.get("analysis", {}) or {}).get("deg_model", {}) or {}
        recommended = (state.get("strategy", {}) or {}).get("recommended", {}) or {}

        # The single most useful thing to say, chosen deterministically.
        if threats and (threats[0]["laps_to_contact"] or 99) <= 5:
            headline = (
                f"{threats[0]['driver']} is the live threat: {threats[0]['gap_s']}s "
                f"and closing, roughly {threats[0]['laps_to_contact']} laps to contact."
            )
        elif opportunities and (opportunities[0]["laps_to_contact"] or 99) <= 5:
            headline = (
                f"{opportunities[0]['driver']} is catchable: {opportunities[0]['gap_s']}s "
                f"and shrinking, roughly {opportunities[0]['laps_to_contact']} laps."
            )
        elif own_trend is not None and own_trend > 0.15:
            headline = (
                f"Your pace is dropping {own_trend:.2f}s per lap; tyre is the limit, "
                "not track position."
            )
        elif recommended.get("instruction"):
            headline = str(recommended["instruction"])
        else:
            headline = "Race is stable; no threat or opportunity inside five laps."

        return {
            "available": bool(state.get("connected")),
            "lap": state.get("current_lap"),
            "total_laps": state.get("total_laps"),
            "position": position,
            "race_control_phase": state.get("race_control_phase"),
            "headline": headline,
            "your_pace": {
                "median_lap": fmt_ms(int(own_median)) if own_median else None,
                "trend_s_per_lap": own_trend,
                "trend_meaning": (
                    None
                    if own_trend is None
                    else "degrading"
                    if own_trend > 0.08
                    else "improving"
                    if own_trend < -0.08
                    else "steady"
                ),
                "target_lap": (state.get("analysis", {}) or {}).get("target", {}).get("target"),
            },
            "your_tyre": {
                "compound": tyre.get("compound"),
                "age_laps": tyre.get("age_laps"),
                "max_wear_pct": round(max(wear), 1) if wear else None,
                "deg_s_per_lap": deg.get("current_slope_s_per_lap"),
                "projected_cliff_lap": deg.get("projected_cliff_lap"),
            },
            "threat": threats[0] if threats else None,
            "opportunity": opportunities[0] if opportunities else None,
            "other_threats": threats[1:3],
            "strategy": {
                "instruction": recommended.get("instruction"),
                "box_lap": recommended.get("box_lap"),
                "fit_compound": recommended.get("fit_compound"),
                "confidence": (state.get("strategy", {}) or {}).get("confidence"),
            },
            "note": (
                "threat and opportunity are only listed when the gap is measurably "
                "shrinking; laps_to_contact assumes the current rate holds."
            ),
        }

    async def get_position_target(self, target_position: int = 10) -> dict[str, Any]:
        """What it would actually take to reach a given position.

        Answers "what lap time do I need for points" with the arithmetic a
        driver cannot do at speed: who is in the way, the total time to be
        recovered, the laps left to do it in, and therefore the per-lap gain
        required — plus whether that is realistic against their current pace.
        """
        state = await self.store.snapshot_analysis()
        player = self._resolve_driver(state, "me")
        position = int(state.get("player_position", 0) or 0)
        total_laps = int(state.get("total_laps", 0) or 0)
        current_lap = int(state.get("current_lap", 0) or 0)
        laps_remaining = max(0, total_laps - current_lap)
        target = max(1, int(target_position))

        if position <= 0:
            return {"available": False, "reason": "No classified position yet."}
        if position <= target:
            return {
                "available": True,
                "already_there": True,
                "position": position,
                "target_position": target,
                "laps_remaining": laps_remaining,
                "message": (
                    f"Already inside the target at P{position}; the job is holding it."
                ),
            }

        running = sorted(
            (
                d
                for d in state.get("drivers", [])
                if int(d.get("position", 0) or 0) > 0 and not d.get("is_player")
            ),
            key=lambda item: int(item["position"]),
        )
        blocking = [
            d
            for d in running
            if target <= int(d.get("position", 0) or 0) < position
        ]
        # Gaps are relative to the player and negative for cars ahead.
        gaps = [
            abs(float(d["gap_to_player_s"]))
            for d in blocking
            if d.get("gap_to_player_s") is not None
        ]
        time_to_find = max(gaps) if gaps else None

        own_laps = self._valid_laps(player or {}, 5)
        own_median_ms = median(int(l["lap_ms"]) for l in own_laps) if own_laps else 0
        required_s_per_lap = (
            round(time_to_find / laps_remaining, 3)
            if time_to_find is not None and laps_remaining > 0
            else None
        )
        # A per-lap gain beyond roughly a second is not a driving problem; it
        # needs a stop, a safety car or someone else's mistake.
        feasible = (
            None
            if required_s_per_lap is None
            else "yes"
            if required_s_per_lap <= 0.3
            else "marginal"
            if required_s_per_lap <= 0.8
            else "not on pace alone"
        )
        target_lap_ms = (
            int(own_median_ms - required_s_per_lap * 1000)
            if own_median_ms and required_s_per_lap is not None
            else 0
        )

        return {
            "available": True,
            "already_there": False,
            "position": position,
            "target_position": target,
            "positions_needed": position - target,
            "laps_remaining": laps_remaining,
            "cars_in_the_way": [
                {
                    "driver": display_name(d),
                    "position": d.get("position"),
                    "gap_s": (
                        None
                        if d.get("gap_to_player_s") is None
                        else round(abs(float(d["gap_to_player_s"])), 2)
                    ),
                    "tyre": d.get("tyre_compound"),
                    "tyre_age_laps": d.get("tyre_age"),
                    "pit_stops": d.get("pit_stops"),
                }
                for d in blocking
            ],
            "time_to_find_s": None if time_to_find is None else round(time_to_find, 2),
            "required_gain_s_per_lap": required_s_per_lap,
            "your_median_lap": fmt_ms(int(own_median_ms)) if own_median_ms else None,
            "required_lap_time": fmt_ms(target_lap_ms) if target_lap_ms > 0 else None,
            "feasible_on_pace": feasible,
            "note": (
                "Assumes the cars ahead hold their current pace and make no further "
                "stops. A stop by any of them, or a safety car, changes this "
                "completely — check predict_rival_strategy before committing."
            ),
        }

    async def get_rival_car_state(self, driver: str) -> dict[str, Any]:
        """Everything the game publishes about one other car.

        Every telemetry packet carries all 24 cars, so a rival's tyres, wear,
        energy, damage and pit state are answerable directly rather than being
        inferred from lap times.
        """
        state = await self.store.snapshot_analysis()
        match = self._resolve_driver(state, driver)
        if not match:
            return {
                "available": False,
                "reason": f"No car in this session matches '{driver}'.",
            }
        summary = self._car_summary(match, detail=True)
        summary["available"] = True
        if match.get("restricted"):
            summary["note"] = (
                "This car's telemetry is restricted by its owner's privacy "
                "setting, so condition and energy are unavailable."
            )
        return summary

    async def get_field_state(
        self,
        top_n: int = 24,
        around_player: bool = False,
    ) -> dict[str, Any]:
        """Compact one-line state for the whole field, ordered by position.

        Retired and garaged cars are included, listed after the running order,
        because "who is left in this race" is part of the picture.
        """
        state = await self.store.snapshot_analysis()
        drivers = list(state.get("drivers", []))
        running = sorted(
            (d for d in drivers if int(d.get("position", 0) or 0) > 0),
            key=lambda item: int(item["position"]),
        )
        if around_player:
            position = int(state.get("player_position", 0) or 0)
            radius = max(2, int(top_n) // 2)
            running = [
                d
                for d in running
                if abs(int(d.get("position", 0)) - position) <= radius
            ]
        out_of_race = [
            self._car_summary(d)
            for d in drivers
            if int(d.get("position", 0) or 0) <= 0
            and not d.get("is_player")
            and str(d.get("result_label") or "") not in {"", "active", "invalid"}
        ]
        return {
            "session_type": state.get("session_type"),
            "lap": state.get("current_lap"),
            "total_laps": state.get("total_laps"),
            "race_control_phase": state.get("race_control_phase"),
            "player_position": state.get("player_position"),
            "cars": [self._car_summary(d) for d in running[: max(1, int(top_n))]],
            "out_of_race": out_of_race,
        }

    async def get_race_flow(self) -> dict[str, Any]:
        """How the race is developing, beyond the player's own position.

        Answers "what is going on out there": who has stopped and who has not,
        which cars are in the pit lane right now, who has gained or lost places,
        who has retired, and which cars behind are within undercut range.
        """
        state = await self.store.snapshot_analysis()
        mode = str(state.get("mode_profile", "idle"))
        drivers = [d for d in state.get("drivers", []) if d.get("active")]
        player_position = int(state.get("player_position", 0) or 0)
        running = sorted(
            (d for d in drivers if int(d.get("position", 0) or 0) > 0),
            key=lambda item: int(item["position"]),
        )

        def name(driver: dict[str, Any]) -> str:
            return display_name(driver)

        stopped = [d for d in running if int(d.get("pit_stops", 0) or 0) > 0]
        yet_to_stop = [
            d
            for d in running
            if int(d.get("pit_stops", 0) or 0) == 0 and not d.get("is_player")
        ]
        in_pit_lane = [
            {
                "driver": name(d),
                "position": d.get("position"),
                "stationary_time_s": round(
                    int(d.get("pit_stop_timer_ms", 0) or 0) / 1000.0, 1
                ),
            }
            for d in running
            if d.get("pit_lane_timer_active")
        ]
        retired = [
            {"driver": name(d), "result": d.get("result_label")}
            for d in drivers
            if str(d.get("result_label") or "") in {
                "retired", "did not finish", "disqualified", "not classified"
            }
        ]

        # Position change since the grid, from the packet's own grid position.
        movers: list[dict[str, Any]] = []
        for driver in running:
            grid = int(driver.get("grid_position", 0) or 0)
            position = int(driver.get("position", 0) or 0)
            if grid > 0 and position > 0 and grid != position:
                movers.append(
                    {
                        "driver": name(driver),
                        "grid": grid,
                        "position": position,
                        "places_gained": grid - position,
                    }
                )
        movers.sort(key=lambda item: abs(int(item["places_gained"])), reverse=True)

        # Cars close enough behind that a stop by them threatens track position.
        # Both the timing gap and the classified position must agree that the
        # car is behind: a lapped car can show a positive gap while running in
        # front, and would otherwise be reported as an undercut threat.
        undercut_range: list[dict[str, Any]] = []
        for driver in running:
            gap = driver.get("gap_to_player_s")
            position = int(driver.get("position", 0) or 0)
            if gap is None or driver.get("is_player"):
                continue
            if driver.get("pit_lane_timer_active"):
                continue
            if player_position <= 0 or position <= player_position:
                continue
            if 0 < float(gap) <= 25.0:
                undercut_range.append(
                    {
                        "driver": name(driver),
                        "position": position,
                        "gap_behind_s": round(float(gap), 2),
                        "tyre": driver.get("tyre_compound"),
                        "tyre_age_laps": driver.get("tyre_age"),
                        "pit_stops": driver.get("pit_stops"),
                    }
                )
        undercut_range.sort(key=lambda item: float(item["gap_behind_s"]))

        leader = running[0] if running else None
        return {
            "available": bool(running),
            "mode_profile": mode,
            "lap": state.get("current_lap"),
            "total_laps": state.get("total_laps"),
            "race_control_phase": state.get("race_control_phase"),
            "safety_car": state.get("safety_car"),
            "player_position": player_position,
            "leader": name(leader) if leader else None,
            "cars_running": len(running),
            "cars_stopped": len(stopped),
            "cars_yet_to_stop": [
                {
                    "driver": name(d),
                    "position": d.get("position"),
                    "tyre": d.get("tyre_compound"),
                    "tyre_age_laps": d.get("tyre_age"),
                }
                for d in yet_to_stop
            ],
            "in_pit_lane_now": in_pit_lane,
            "retired": retired,
            "biggest_movers": movers[:5],
            "cars_within_undercut_range": undercut_range[:5],
            "note": (
                "Stop counts and tyre ages come from the timing feed. "
                "Use predict_rival_strategy for estimated future stop laps."
            ),
        }

    async def get_flag_status(self) -> dict[str, Any]:
        """Marshal-zone flags around the lap, relative to the player.

        The session packet publishes up to 21 zones with a live flag each. This
        is the only field-wide source of *where* a yellow is, which the previous
        release never read.
        """
        state = await self.store.snapshot_analysis()
        zones = list(state.get("marshal_zones", []) or [])
        track_length = float(state.get("track_length_m", 0) or 0)
        lap_distance = float(state.get("lap_distance_m", 0) or 0)
        active = [zone for zone in zones if zone.get("flag") not in {"none", "green", "unknown"}]
        for zone in active:
            start = float(zone.get("start_m", 0.0))
            ahead = start - lap_distance
            if ahead < 0 and track_length:
                ahead += track_length
            zone["distance_ahead_m"] = round(ahead, 1)
        active.sort(key=lambda zone: float(zone.get("distance_ahead_m", 0.0)))
        return {
            "available": bool(zones),
            "car_flag": state.get("fia_flag"),
            "race_control_phase": state.get("race_control_phase"),
            "zone_count": len(zones),
            "active_zones": active,
            "next_incident_zone": active[0] if active else None,
            "drs_zones": state.get("drs_zones", []),
            "active_aero_zones": state.get("active_aero_zones", []),
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

    async def get_rival_sector_comparison(self, driver: str) -> dict[str, Any]:
        """Compare the latest matched valid player/rival sectors.

        Deltas are player minus rival, so a positive value is time lost by the
        player. Matching by lap sequence avoids mixing an in-lap with a flying lap.
        """
        state = await self.store.snapshot_analysis()
        rival = self._resolve_driver(state, driver)
        player = self._resolve_driver(state, "me")
        if not rival or not player:
            return {"available": False, "reason": "Driver timing data is unavailable."}
        player_laps = [
            lap for lap in player.get("lap_history", [])
            if int(lap.get("lap_ms", 0)) > 0 and all(int(lap.get(key, 0)) > 0 for key in ("s1_ms", "s2_ms", "s3_ms"))
        ]
        rival_laps = [
            lap for lap in rival.get("lap_history", [])
            if int(lap.get("lap_ms", 0)) > 0 and all(int(lap.get(key, 0)) > 0 for key in ("s1_ms", "s2_ms", "s3_ms"))
        ]
        if not player_laps or not rival_laps:
            return {"available": False, "reason": f"No complete matched sectors are available for {rival.get('name', driver)} yet."}
        # Prefer the same lap number. Fall back to the latest complete lap from each
        # car because packet history can be restricted for remote cars.
        rival_by_lap = {int(lap.get("lap_num", -1)): lap for lap in rival_laps}
        pair = next(
            ((lap, rival_by_lap[int(lap.get("lap_num", -1))]) for lap in reversed(player_laps) if int(lap.get("lap_num", -1)) in rival_by_lap),
            None,
        )
        if pair is None:
            pair = (player_laps[-1], rival_laps[-1])
        player_lap, rival_lap = pair
        deltas = {
            "s1": round((int(player_lap["s1_ms"]) - int(rival_lap["s1_ms"])) / 1000.0, 3),
            "s2": round((int(player_lap["s2_ms"]) - int(rival_lap["s2_ms"])) / 1000.0, 3),
            "s3": round((int(player_lap["s3_ms"]) - int(rival_lap["s3_ms"])) / 1000.0, 3),
        }
        return {
            "available": True,
            "driver": rival.get("name", driver),
            "player_lap": fmt_ms(int(player_lap.get("lap_ms", 0))),
            "rival_lap": fmt_ms(int(rival_lap.get("lap_ms", 0))),
            "sector_deltas_s": deltas,
            "total_delta_s": round(sum(deltas.values()), 3),
            "interpretation": "Positive sector delta means the player was slower.",
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
                "grid_position",
                "live_delta_s",
                "component_wear",
                "unserved_drive_through_penalties",
                "unserved_stop_go_penalties",
                "pit_stop_should_serve_penalty",
            )
        }

    async def get_tyre_condition(self, detail: bool = False) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        tyre = state["tyre"]
        wear = tyre.get("wear", [0, 0, 0, 0])
        temps = [float(value) for value in tyre.get("inner_temps_c", [0, 0, 0, 0])]
        average_wear = sum(wear) / len(wear) if wear else 0
        front_temp = sum(temps[:2]) / 2 if len(temps) >= 4 else 0
        rear_temp = sum(temps[2:]) / 2 if len(temps) >= 4 else 0
        temperature_unit = str(state.get("temperature_unit", "c")).lower()
        if temperature_unit == "f":
            spoken_temps = [round(value * 9.0 / 5.0 + 32.0, 1) for value in temps]
            spoken_delta = round((front_temp - rear_temp) * 9.0 / 5.0, 1)
            spoken_label = "F"
        else:
            spoken_temps = [round(value, 1) for value in temps]
            spoken_delta = round(front_temp - rear_temp, 1)
            spoken_label = "C"
        deg = state.get("analysis", {}).get("deg_model", {})
        return {
            "compound": tyre["compound"],
            "age_laps": tyre["age_laps"],
            "average_wear_pct": round(average_wear, 1),
            "max_wear_pct": round(max(wear or [0]), 1),
            "wheel_order": ["FL", "FR", "RL", "RR"],
            "wear": wear if detail else None,
            "temperature_unit": spoken_label,
            "inner_temps": spoken_temps if detail else None,
            "front_to_rear_temp_delta": spoken_delta,
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

    async def get_energy_plan(self) -> dict[str, Any]:
        """Lap-level deploy/harvest guidance from the current battery state.

        This is deterministic guidance from ERS store and attack context, not a
        per-corner map (which would need track geometry the telemetry does not
        provide). 2026 sessions use Manual Override terminology.
        """
        state = await self.store.snapshot_analysis()
        ers = float(state.get("ers_pct", 0.0))
        regs_2026 = bool(state.get("regulations_2026"))
        aid = "Manual Override" if regs_2026 else "DRS"
        aid_available = bool(
            state.get("overtake_available") if regs_2026 else state.get("drs_allowed")
        )
        if ers < 15.0:
            mode = "harvest"
            recommendation = f"Harvest this lap; hold {aid} until the battery recovers."
        elif ers > 80.0:
            mode = "deploy"
            recommendation = f"Battery is full — deploy freely and use {aid} where available."
        else:
            mode = "balanced"
            recommendation = "Deploy onto the main straights and harvest through the slow sections."
        return {
            "store_pct": round(ers, 1),
            "recommended_mode": mode,
            "overtaking_aid": aid,
            "aid_available": aid_available,
            "attack_window_open": bool(aid_available and ers >= 30.0),
            "recommendation": recommendation,
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
            # The car-status packet publishes every car's energy store, so the
            # rival's battery is measured rather than assumed. It is absent only
            # when that car's telemetry is restricted online.
            "rival_ers_pct": target.get("ers_pct"),
            # None, not False, when the car withholds telemetry: "unknown" and
            # "he has no boost" are different calls to make.
            "rival_overtake_available": (
                None if target.get("restricted") else bool(target.get("overtake_available"))
            ),
            "rival_damage": {
                key: value
                for key, value in (target.get("damage") or {}).items()
                if value
            } or None,
            "rival_telemetry_restricted": bool(target.get("restricted")),
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
            "rival_ers_pct": target.get("ers_pct"),
            "rival_overtake_available": (
                None if target.get("restricted") else bool(target.get("overtake_available"))
            ),
            "rival_telemetry_restricted": bool(target.get("restricted")),
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

    async def get_practice_focus(self) -> dict[str, Any]:
        """Heuristic practice guidance (not the game's programme scoring).

        Suggests what to work on this practice session from lap count, compound
        variety and consistency. Deliberately framed as advice, not a claim to
        mirror F1's internal practice-programme objectives, which telemetry does
        not report directly.
        """
        state = await self.store.snapshot_analysis()
        if state.get("mode_profile") != "practice":
            return {"available": False, "reason": "Not a practice session."}
        laps_done = int(state.get("current_lap", 0))
        consistency = await self.analysis.get_consistency()
        tyre = state.get("tyre", {})
        focus: list[str] = []
        if laps_done < 3:
            focus.append("Track acclimatisation: build clean laps and learn braking points.")
        if int(tyre.get("age_laps", 0)) >= 8:
            focus.append("Long-run race pace: monitor degradation and manage the tyre.")
        else:
            focus.append("Qualifying simulation: a low-fuel push lap on fresh tyres.")
        stddev = consistency.get("lap_time_stddev_s")
        if isinstance(stddev, (int, float)) and stddev > 0.4:
            focus.append(
                f"Consistency: lap-time spread is {stddev:.2f}s — tighten repeatability."
            )
        return {
            "available": True,
            "laps_completed": laps_done,
            "current_compound": tyre.get("compound"),
            "lap_time_stddev_s": stddev,
            "suggested_focus": focus,
            "note": "Heuristic advice, not the game's practice-programme tracker.",
        }

    async def get_session_debrief(self) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        session_uid = int(state.get("session_uid", 0))
        if not session_uid:
            return {"available": False, "reason": "No active session."}
        data = await self.database.session_debrief(session_uid)
        return {
            "available": bool(data.get("valid_lap_count")),
            "track": data.get("track_name"),
            "result_position": data.get("result_position"),
            "fastest_lap": fmt_ms(data["fastest_lap_ms"]) if data.get("fastest_lap_ms") else None,
            "average_lap": fmt_ms(data["average_lap_ms"]) if data.get("average_lap_ms") else None,
            "consistency_stdev_s": (
                round(data["consistency_stdev_ms"] / 1000, 3)
                if data.get("consistency_stdev_ms") is not None
                else None
            ),
            "compounds_used": data.get("compounds_used", []),
            "top_time_losses": data.get("top_time_losses", []),
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

    # Tools that only a planning question needs. Sending the full catalogue on
    # every request bloats the cacheable prefix and widens the model's choice
    # for questions that could never need these, so they are offered only on the
    # deep route.
    DEEP_ONLY_TOOLS = frozenset(
        {
            "what_if",
            "get_championship_scenario",
            "get_setup_learning",
            "get_setup_correlation",
            "generate_setup",
            "get_front_wing_adjustment",
            "get_progress_trend",
            "get_stored_history",
            "get_practice_focus",
            "get_racing_line_analysis",
        }
    )

    def schemas_for_route(self, route: str) -> list[dict[str, Any]]:
        """Tool schemas appropriate to a request route.

        The deep route sees everything. Fast and normal routes drop the
        planning, setup-generation and long-history tools, which no live
        "what's my fuel" or "where is the car ahead" question can need.
        """
        if route == "deep":
            return self.schemas()
        return [
            schema
            for schema in self.schemas()
            if schema["name"] not in self.DEEP_ONLY_TOOLS
        ]

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
                "get_cars_ahead_progress",
                "Get nearest cars ahead with measured live gap trend and latest-lap pace evidence.",
                {"top_n": {"type": "integer", "minimum": 1, "maximum": 12}},
            ),
            (
                "get_pace_verdict",
                (
                    "Judge the player's pace against one rival and say where the "
                    "time is going: closing or not, closing rate, laps to contact, "
                    "sector attribution, pace trend both ways and the tyre offset "
                    "that explains it. Use this for any 'how am I doing against X' "
                    "or 'am I catching X' question instead of reading out lap times."
                ),
                {"driver": {"type": "string"}},
            ),
            (
                "get_position_target",
                (
                    "What it would take to reach a given finishing position: who is "
                    "in the way, the total time to recover, the laps left, the "
                    "per-lap gain required, the lap time that implies, and whether "
                    "it is realistic on pace alone. Use for 'what lap time do I need "
                    "for points' or 'can I still reach Pn'. Defaults to P10, the "
                    "lowest points-paying position."
                ),
                {"target_position": {"type": "integer", "minimum": 1, "maximum": 24}},
            ),
            (
                "get_race_picture",
                (
                    "One call for where the race stands: the single real threat and "
                    "the single real opportunity with laps to contact, whether the "
                    "player's own pace is holding or degrading, tyre life, and the "
                    "strategy consequence. Use this for open questions such as "
                    "'how is the race going', 'where do I stand' or 'any updates'."
                ),
                {},
            ),
            (
                "get_rival_car_state",
                (
                    "Get another car's full published state: tyre compound and "
                    "age, per-wheel wear and temperatures, energy, fuel, damage, "
                    "penalties, stints and live pit-lane status. Use this for any "
                    "question about a specific rival rather than the player."
                ),
                {"driver": {"type": "string"}},
            ),
            (
                "get_field_state",
                (
                    "Get a compact one-line state for every car in the field, in "
                    "running order, including retired and garaged cars."
                ),
                {
                    "top_n": {"type": "integer", "minimum": 1, "maximum": 24},
                    "around_player": {"type": "boolean"},
                },
            ),
            (
                "get_race_flow",
                (
                    "Get how the race is developing across the field: who has "
                    "stopped and who has not, who is in the pit lane now, who has "
                    "gained or lost places, who has retired, and which cars behind "
                    "are within undercut range."
                ),
                {},
            ),
            (
                "get_flag_status",
                (
                    "Get live marshal-zone flags around the lap with the distance "
                    "to the next incident zone, plus DRS and active-aero zones."
                ),
                {},
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
                "get_rival_sector_comparison",
                "Compare the player's latest matched sector times with a named driver; positive delta means player time lost.",
                {"driver": {"type": "string"}},
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
                "get_session_debrief",
                (
                    "Get the deterministic end-of-session summary: pace, "
                    "consistency, tyres, result and the biggest corner losses."
                ),
                {},
            ),
            (
                "get_practice_focus",
                (
                    "Get heuristic practice-session guidance on what to work on "
                    "(acclimatisation, race pace, quali sim, consistency)."
                ),
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
                "get_energy_plan",
                (
                    "Get lap-level battery deploy/harvest guidance and whether "
                    "the overtaking-aid attack window is open."
                ),
                {},
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
