from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

from .analysis import fmt_ms
from .brain import EngineerBrain
from .config import settings
from .setup_advisor import SetupAdvisor
from .state import StateStore
from .strategy import StrategyEngine
from .voice import NativeVoiceController

# Reliability damage matters as much as aero damage, but gearbox, engine and
# DRS/ERS faults were previously invisible to the radio: they are captured in
# state yet were excluded from both the change signature and the severity test,
# so a failing power unit never produced a call.
DAMAGE_KEYS = (
    "front_left_wing",
    "front_right_wing",
    "rear_wing",
    "floor",
    "diffuser",
    "sidepod",
    "gearbox",
    "engine",
)
DAMAGE_FAULT_KEYS = ("drs_fault", "ers_fault")
# Phases in which a safety-car delta time is being enforced.
NEUTRALISED_PHASES = frozenset(
    {
        "safety_car",
        "vsc",
        "formation",
        "safety_car_ending",
        "vsc_ending",
        "formation_ending",
    }
)


class ProactiveEngineer:
    """Reliable cadence-driven and event-driven unsolicited race radio."""

    def __init__(
        self,
        store: StateStore,
        brain: EngineerBrain,
        voice: NativeVoiceController,
        setup_advisor: SetupAdvisor,
        strategy: StrategyEngine,
    ) -> None:
        self.store = store
        self.brain = brain
        self.voice = voice
        self.setup_advisor = setup_advisor
        self.strategy = strategy
        self.database = brain.database
        self.pending: deque[dict[str, Any]] = deque(maxlen=24)
        self._task: asyncio.Task[None] | None = None
        self._session_uid = 0
        self._last_spoken_at = 0.0
        self._last_lap_queued = 0
        self._last_learning_lap = 0
        self._last_safety_car = "none"
        self._last_race_control_phase = "green"
        self._last_weather_alert = False
        self._last_penalties = 0
        self._last_warnings = 0
        self._last_damage_signature = ""
        self._last_strategy_signature = ""
        self._last_strategy_inputs_signature = ""
        self._last_corner_signature = ""
        self._last_pit_stops: dict[int, int] = {}
        self._last_lap_invalid = False
        self._last_blue_flag = False
        self._last_unserved_penalties = (0, 0)
        self._last_quali_release_lap = 0
        self._cooldowns: dict[str, float] = {}
        self._safe_since = 0.0

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="pitwall-proactive")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def configure(self, enabled: bool, cadence_laps: int) -> dict[str, Any]:
        cadence = max(1, min(10, int(cadence_laps)))
        def apply(state):  # type: ignore[no-untyped-def]
            state.proactive["enabled"] = bool(enabled)
            state.proactive["cadence_laps"] = cadence
            state.proactive["next_due_lap"] = max(cadence, self._last_lap_queued + cadence)
        await self.store.mutate(apply)
        return {"enabled": bool(enabled), "cadence_laps": cadence}

    @staticmethod
    def _qualifying_payload(state: dict[str, Any]) -> dict[str, Any]:
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
        player_best_ms = int(player.get("best_lap_ms", 0)) if player else 0
        target = state.get("analysis", {}).get("target", {})
        target_ms = int(target.get("target_ms", 0) or 0)
        return {
            "session_best": fmt_ms(session_best_ms),
            "session_best_ms": session_best_ms,
            "player_best": fmt_ms(player_best_ms),
            "player_best_ms": player_best_ms,
            "theoretical_best": target.get("theoretical"),
            "target": target.get("target"),
            "target_ms": target_ms,
            "required_delta_s": round(
                (player_best_ms - target_ms) / 1000,
                3,
            )
            if player_best_ms and target_ms
            else None,
            "top_best_laps": [
                {
                    "name": driver.get("name", "Unknown"),
                    "best_lap": fmt_ms(int(driver.get("best_lap_ms", 0))),
                    "delta_to_best_s": round(
                        (int(driver.get("best_lap_ms", 0)) - session_best_ms)
                        / 1000,
                        3,
                    )
                    if session_best_ms
                    else None,
                }
                for driver in drivers[:5]
            ],
        }

    async def queue_test_update(self) -> dict[str, Any]:
        """Queue an immediate progress call for live hardware acceptance testing."""
        state = await self.store.snapshot_analysis()
        if not state.get("connected") or state.get("game_paused"):
            return {
                "ok": False,
                "reason": "Resume a live session before testing proactive radio.",
            }
        session_uid = int(state.get("session_uid", 0))
        if session_uid != self._session_uid:
            await self._reset_for_session(session_uid)
            state = await self.store.snapshot_analysis()
        state = await self._refresh_strategy_if_needed(state)
        analyzed_lap = int(
            state.get("analysis", {}).get("last_lap_analyzed", 0)
            or state.get("current_lap", 0)
        )
        self._enqueue(
            "progress_update",
            {
                "lap": analyzed_lap,
                "progress": state.get("analysis", {}).get("progress", {}),
                "mode_profile": state.get("mode_profile"),
                "strategy": (
                    {}
                    if state.get("mode_profile") == "qualifying"
                    else state.get("strategy", {}).get("recommended", {})
                ),
                "target": state.get("analysis", {}).get("target", {}),
                "racing_line": state.get("analysis", {}).get("racing_line", {}),
                "qualifying": (
                    self._qualifying_payload(state)
                    if state.get("mode_profile") == "qualifying"
                    else {}
                ),
                "gaps": (
                    {}
                    if state.get("mode_profile") == "qualifying"
                    else {
                        "ahead": state.get("gap_ahead_s"),
                        "behind": state.get("gap_behind_s"),
                    }
                ),
                "manual_test": True,
            },
            critical=True,
            cooldown_s=0.0,
            expires_s=60.0,
        )
        await self.store.mutate(
            lambda s: s.proactive.update(
                {
                    "queued": len(self.pending),
                    "delivery_state": "manual test queued",
                }
            )
        )
        return {
            "ok": True,
            "message": "Progress update queued; it will play at the next safe section.",
            "queued": len(self.pending),
        }

    def _enqueue(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        critical: bool = False,
        cooldown_s: float = 60.0,
        expires_s: float | None = None,
    ) -> None:
        now_mono = time.monotonic()
        if now_mono - self._cooldowns.get(event_type, -1e9) < cooldown_s:
            return
        self._cooldowns[event_type] = now_mono
        coalesced = {
            "progress_update", "corner_coaching", "race_control", "weather_crossover",
            "fuel_warning", "tyre_wear", "damage", "strategy_change", "compound_requirement",
            "quali_clear_air", "blue_flag", "penalty_service", "undercut_threat",
            "safety_car_delta",
        }
        if event_type in coalesced:
            self.pending = deque((item for item in self.pending if item.get("type") != event_type), maxlen=24)
        now = time.time()
        event = {
            "type": event_type,
            "critical": critical,
            "payload": payload,
            "queued_at": now,
            "deliver_by": now + (8.0 if critical else settings.proactive_delivery_deadline_s),
            "expires_at": now + (expires_s if expires_s is not None else (45.0 if critical else 180.0)),
            "session_uid": self._session_uid,
        }
        self.pending.appendleft(event) if critical else self.pending.append(event)

    async def _reset_for_session(self, session_uid: int) -> None:
        self._session_uid = int(session_uid)
        self.pending.clear()
        self._last_spoken_at = 0.0
        self._last_lap_queued = 0
        self._last_learning_lap = 0
        self._last_safety_car = "none"
        self._last_race_control_phase = "green"
        self._last_weather_alert = False
        self._last_penalties = self._last_warnings = 0
        self._last_damage_signature = self._last_strategy_signature = ""
        self._last_strategy_inputs_signature = self._last_corner_signature = ""
        self._last_pit_stops = {}
        self._last_lap_invalid = False
        self._last_blue_flag = False
        self._last_unserved_penalties = (0, 0)
        self._last_quali_release_lap = 0
        self._cooldowns.clear()
        self._safe_since = 0.0
        await self.store.mutate(lambda s: s.proactive.update({
            "queued": 0, "last_spoken_lap": 0, "last_call": "",
            "last_queued_lap": 0, "next_due_lap": int(s.proactive.get("cadence_laps", 2)),
            "oldest_wait_s": 0.0, "delivery_state": "waiting for lap data",
        }))

    @staticmethod
    def _damage_signature(damage: dict[str, Any]) -> str:
        return ":".join(
            str(damage.get(key, 0))
            for key in (*DAMAGE_KEYS, *DAMAGE_FAULT_KEYS)
        )

    @staticmethod
    def _event_still_relevant(event: dict[str, Any], state: dict[str, Any]) -> bool:
        if int(event.get("session_uid", 0)) != int(state.get("session_uid", 0)):
            return False
        if time.time() > float(event.get("expires_at", 0)):
            return False
        kind = event.get("type")
        if kind == "race_control":
            return state.get("race_control_phase") != "green" or event.get("payload", {}).get("to") == "green"
        if kind == "weather_crossover":
            return int(state.get("rain_next_15_pct", 0)) >= 55
        if kind == "fuel_warning":
            return float(state.get("fuel_laps_delta", 1.0)) < 0.3
        if kind == "tyre_wear":
            return max(state.get("tyre", {}).get("wear", [0]) or [0]) >= 62
        if kind == "damage":
            d = state.get("damage", {})
            return max([int(d.get(key, 0)) for key in DAMAGE_KEYS] or [0]) > 10 or any(
                bool(d.get(key)) for key in DAMAGE_FAULT_KEYS
            )
        if kind == "safety_car_delta":
            return str(
                state.get("race_control_phase", "green")
            ) in NEUTRALISED_PHASES and float(
                state.get("safety_car_delta_s", 0.0) or 0.0
            ) < settings.proactive_sc_delta_min_s
        if kind == "compound_requirement":
            return bool(state.get("strategy", {}).get("compound_rule", {}).get("change_outstanding"))
        if kind == "blue_flag":
            return state.get("fia_flag") == "blue"
        if kind == "lap_deleted":
            return bool(state.get("current_lap_invalid"))
        if kind == "penalty_service":
            return bool(
                int(state.get("unserved_drive_through_penalties", 0))
                or int(state.get("unserved_stop_go_penalties", 0))
                or state.get("pit_stop_should_serve_penalty")
            )
        return True

    def _safe_to_speak(self, state: dict[str, Any], event: dict[str, Any]) -> bool:
        if not state.get("connected") or state.get("game_paused"):
            self._safe_since = 0.0
            return False
        if state.get("ptt_pressed") or self.voice.is_busy:
            self._safe_since = 0.0
            return False
        now = time.time()
        overdue = now >= float(event.get("deliver_by", now + 1))
        brake = float(state.get("brake", 0))
        lat_g = abs(float(state.get("lateral_g", 0)))
        speed = int(state.get("speed_kph", 0))
        throttle = float(state.get("throttle", 0))
        safe = speed < 75 or (brake <= settings.proactive_max_brake and lat_g <= settings.proactive_max_lateral_g and throttle >= 0.45)
        # Deadline fallback still refuses heavy braking/high-G, but no longer waits
        # forever for full throttle on tracks with short straights.
        if overdue:
            safe = speed < 90 or (brake < 0.35 and lat_g < 1.85)
        if not safe:
            self._safe_since = 0.0
            return False
        if event.get("critical") or overdue:
            return True
        if not self._safe_since:
            self._safe_since = time.monotonic()
        return time.monotonic() - self._safe_since >= settings.proactive_safe_hold_s

    async def _refresh_strategy_if_needed(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("mode_profile") == "qualifying":
            return state
        signature = repr((
            int(state.get("current_lap", 0)), state.get("race_control_phase"),
            state.get("weather"), int(state.get("rain_next_15_pct", 0)),
            state.get("tyre", {}).get("compound"), int(state.get("tyre", {}).get("age_laps", 0)),
            tuple(round(float(x), 1) for x in state.get("tyre", {}).get("wear", [])),
            tuple((d.get("car_idx"), d.get("pit_stops"), d.get("tyre_compound")) for d in state.get("drivers", [])),
        ))
        if signature != self._last_strategy_inputs_signature:
            self._last_strategy_inputs_signature = signature
            await self.strategy.recompute()
            return await self.store.snapshot_analysis()
        return state

    async def _detect(self, state: dict[str, Any]) -> None:
        if not state.get("connected") or state.get("game_paused"):
            return
        state = await self._refresh_strategy_if_needed(state)
        proactive = state.get("proactive", {})
        analyzed_lap = int(state.get("analysis", {}).get("last_lap_analyzed", 0))
        if analyzed_lap > 0 and analyzed_lap != self._last_learning_lap:
            self._last_learning_lap = analyzed_lap
            await self.setup_advisor.learn_current_session()
        if not bool(proactive.get("enabled", settings.proactive_enabled)):
            return

        cadence = max(1, int(proactive.get("cadence_laps", settings.proactive_cadence_laps)))
        due_lap = cadence if self._last_lap_queued == 0 else self._last_lap_queued + cadence
        if analyzed_lap >= due_lap:
            self._last_lap_queued = analyzed_lap
            mode = state.get("mode_profile")
            self._enqueue(
                "progress_update",
                {
                    "lap": analyzed_lap,
                    "mode_profile": mode,
                    "progress": state.get("analysis", {}).get("progress", {}),
                    "strategy": (
                        {}
                        if mode == "qualifying"
                        else state.get("strategy", {}).get("recommended", {})
                    ),
                    "target": state.get("analysis", {}).get("target", {}),
                    "racing_line": state.get("analysis", {}).get("racing_line", {}),
                    "qualifying": (
                        self._qualifying_payload(state)
                        if mode == "qualifying"
                        else {}
                    ),
                    "gaps": (
                        {}
                        if mode == "qualifying"
                        else {
                            "ahead": state.get("gap_ahead_s"),
                            "behind": state.get("gap_behind_s"),
                        }
                    ),
                },
                cooldown_s=0.0,
            )
            await self.store.mutate(lambda s: s.proactive.update({
                "last_queued_lap": analyzed_lap, "next_due_lap": analyzed_lap + cadence,
            }))

        flagged = state.get("analysis", {}).get("flagged_corners", [])
        if flagged:
            top = flagged[0]
            signature = f"{top.get('corner_no')}:{round(float(top.get('average_loss_s', 0)), 2)}"
            mode = state.get("mode_profile", "idle")
            if signature != self._last_corner_signature and (mode == "practice" or (mode == "qualifying" and int(state.get("speed_kph", 0)) < 80) or float(top.get("average_loss_s", 0)) >= 0.30):
                self._last_corner_signature = signature
                self._enqueue("corner_coaching", top, cooldown_s=60.0 if mode in {"race", "sprint"} else 35.0)

        phase, safety = str(state.get("race_control_phase", "green")), str(state.get("safety_car", "none"))
        if phase != self._last_race_control_phase or safety != self._last_safety_car:
            previous = self._last_race_control_phase
            self._last_race_control_phase, self._last_safety_car = phase, safety
            self._enqueue("race_control", {"from": previous, "to": phase, "strategy": state.get("strategy", {}).get("recommended", {}), "neutralisation": state.get("strategy", {}).get("neutralisation", {})}, critical=True, cooldown_s=0.0)

        wet = int(state.get("rain_next_15_pct", 0)) >= 60
        if wet and not self._last_weather_alert:
            self._enqueue("weather_crossover", {"rain_15_pct": state.get("rain_next_15_pct"), "forecast": state.get("weather_forecast", [])}, critical=True, cooldown_s=0.0)
        self._last_weather_alert = wet

        if float(state.get("fuel_laps_delta", 1.0)) < 0.3:
            self._enqueue("fuel_warning", state.get("analysis", {}).get("fuel_model", {}), cooldown_s=90.0)
        wear = max(state.get("tyre", {}).get("wear", [0]) or [0])
        if wear >= 65:
            self._enqueue("tyre_wear", {"wear_fl_fr_rl_rr": state.get("tyre", {}).get("wear"), "strategy": state.get("strategy", {}).get("recommended", {})}, critical=wear >= 82, cooldown_s=60.0)

        penalties, warnings = int(state.get("penalties_s", 0)), int(state.get("corner_cutting_warnings", 0))
        if penalties > self._last_penalties:
            self._enqueue("penalty", {"penalties_s": penalties}, critical=True, cooldown_s=0.0)
        elif warnings >= 2 and warnings > self._last_warnings:
            self._enqueue("warning", {"corner_cutting_warnings": warnings}, critical=True, cooldown_s=0.0)
        self._last_penalties, self._last_warnings = penalties, warnings

        damage = state.get("damage", {})
        signature = self._damage_signature(damage)
        if signature != self._last_damage_signature:
            self._last_damage_signature = signature
            maximum = max([int(damage.get(key, 0)) for key in DAMAGE_KEYS] or [0])
            # A DRS or ERS fault has no percentage but is immediately
            # race-affecting, so it is called regardless of damage level.
            fault = any(bool(damage.get(key)) for key in DAMAGE_FAULT_KEYS)
            if maximum > 10 or fault:
                self._enqueue("damage", damage, critical=True, cooldown_s=0.0)

        invalid = bool(state.get("current_lap_invalid"))
        if (
            invalid
            and not self._last_lap_invalid
            and state.get("mode_profile") == "qualifying"
        ):
            self._enqueue(
                "lap_deleted",
                {
                    "lap": int(state.get("current_lap", 0)),
                    "sector": int(state.get("sector", 0)),
                },
                critical=True,
                cooldown_s=0.0,
                expires_s=30.0,
            )
        self._last_lap_invalid = invalid

        blue = state.get("fia_flag") == "blue"
        if blue and not self._last_blue_flag:
            self._enqueue(
                "blue_flag",
                {"flag": "blue", "position": state.get("player_position")},
                critical=True,
                cooldown_s=0.0,
                expires_s=25.0,
            )
        self._last_blue_flag = blue

        # Safety-car / VSC delta compliance. The delta must stay above the
        # threshold; going under it is a penalty risk, so this repeats on a
        # short cooldown while the breach lasts rather than firing once.
        phase = str(state.get("race_control_phase", "green"))
        if phase in NEUTRALISED_PHASES:
            delta = float(state.get("safety_car_delta_s", 0.0) or 0.0)
            if delta < settings.proactive_sc_delta_min_s:
                self._enqueue(
                    "safety_car_delta",
                    {
                        "phase": phase,
                        "safety_car_delta_s": round(delta, 2),
                        "threshold_s": settings.proactive_sc_delta_min_s,
                    },
                    critical=True,
                    cooldown_s=settings.proactive_sc_delta_cooldown_s,
                    expires_s=10.0,
                )

        unserved = (
            int(state.get("unserved_drive_through_penalties", 0)),
            int(state.get("unserved_stop_go_penalties", 0)),
        )
        if (
            any(
                current > previous
                for current, previous in zip(
                    unserved,
                    self._last_unserved_penalties,
                    strict=True,
                )
            )
            or bool(state.get("pit_stop_should_serve_penalty"))
        ):
            self._enqueue(
                "penalty_service",
                {
                    "drive_through": unserved[0],
                    "stop_go": unserved[1],
                    "serve_at_stop": bool(state.get("pit_stop_should_serve_penalty")),
                    "strategy": state.get("strategy", {}).get("recommended", {}),
                },
                critical=True,
                cooldown_s=15.0,
                expires_s=90.0,
            )
        self._last_unserved_penalties = unserved

        if state.get("mode_profile") == "qualifying":
            current_lap = int(state.get("current_lap", 0))
            speed = int(state.get("speed_kph", 0))
            status = str(
                next(
                    (
                        driver.get("status", "")
                        for driver in state.get("drivers", [])
                        if int(driver.get("car_idx", -1))
                        == int(state.get("player_car_index", -1))
                    ),
                    "",
                )
            )
            nearby = [
                abs(float(driver.get("gap_to_player_s")))
                for driver in state.get("drivers", [])
                if driver.get("gap_to_player_s") not in {None, 0}
                and abs(float(driver.get("gap_to_player_s"))) < 6.0
            ]
            clear_estimate = not nearby or min(nearby) >= 3.0
            prep_state = status in {"out lap", "on track"} and speed < 180
            if (
                current_lap > 0
                and current_lap != self._last_quali_release_lap
                and prep_state
                and clear_estimate
                and not invalid
            ):
                self._last_quali_release_lap = current_lap
                self._enqueue(
                    "quali_clear_air",
                    {
                        "lap": current_lap,
                        "clear_air_estimate": True,
                        "closest_gap_s": min(nearby) if nearby else None,
                        "target": self._qualifying_payload(state),
                        "ers_pct": state.get("ers_pct"),
                        "tyre_temps": state.get("tyre", {}).get("inner_temps_c", []),
                    },
                    cooldown_s=45.0,
                    expires_s=35.0,
                )
            return

        rule = state.get("strategy", {}).get("compound_rule", {})
        laps_remaining = max(0, int(state.get("total_laps", 0)) - int(state.get("current_lap", 0)))
        if rule.get("change_outstanding") and (laps_remaining <= 6 or int(state.get("current_lap", 0)) >= max(1, int(state.get("total_laps", 0)) // 2)):
            self._enqueue("compound_requirement", {"laps_remaining": laps_remaining, "used": rule.get("dry_compounds_used", []), "eligible": rule.get("eligible_next_compounds", []), "strategy": state.get("strategy", {}).get("recommended", {})}, critical=laps_remaining <= 3, cooldown_s=90.0)

        recommended = state.get("strategy", {}).get("recommended", {})
        strategy_signature = f"{recommended.get('box_lap')}:{recommended.get('fit_compound')}:{recommended.get('stops_remaining')}"
        if recommended and strategy_signature != self._last_strategy_signature:
            old = self._last_strategy_signature
            self._last_strategy_signature = strategy_signature
            if old:
                self._enqueue("strategy_change", recommended, cooldown_s=15.0)

        current_stops = {int(d.get("car_idx", -1)): int(d.get("pit_stops", 0)) for d in state.get("drivers", [])}
        if self._last_pit_stops:
            for driver in state.get("drivers", []):
                idx = int(driver.get("car_idx", -1))
                if idx != int(state.get("player_car_index", -1)) and current_stops.get(idx, 0) > self._last_pit_stops.get(idx, 0):
                    gap = driver.get("gap_to_player_s")
                    payload = {
                        "driver": driver.get("name"),
                        "position": driver.get("position"),
                        "tyre": driver.get("tyre_compound"),
                        "gap_to_player_s": gap,
                        "strategy": recommended,
                    }
                    self._enqueue("rival_pitted", payload, cooldown_s=0.0)
                    if gap is not None and 0 < float(gap) <= 25.0:
                        self._enqueue(
                            "undercut_threat",
                            {
                                **payload,
                                "instruction": (
                                    "Car behind has stopped. Push for up to two clean laps "
                                    "while protecting the limiting tyre, then reassess the cover stop."
                                ),
                            },
                            critical=float(gap) <= 12.0,
                            cooldown_s=0.0,
                            expires_s=50.0,
                        )
        self._last_pit_stops = current_stops

    @staticmethod
    def _fallback_text(event: dict[str, Any], state: dict[str, Any]) -> str:
        kind, payload = event.get("type"), event.get("payload", {})
        if kind == "progress_update":
            target = payload.get("target", {})
            line = payload.get("racing_line", {})
            opportunity = (
                line.get("top_opportunity")
                or payload.get("progress", {}).get("top_opportunity")
                or "keep the lap clean"
            )
            if payload.get("mode_profile") == "qualifying":
                quali = payload.get("qualifying", {})
                return (
                    f"Qualifying update: session best "
                    f"{quali.get('session_best') or 'unavailable'}, target "
                    f"{quali.get('target') or 'building'}. {opportunity}."
                )
            strategy = payload.get("strategy", {})
            action = strategy.get("instruction") or "hold the current plan"
            pace = (
                target.get("target")
                or target.get("target_lap")
                or "target unavailable"
            )
            return (
                f"Lap {payload.get('lap')} update: target {pace}; "
                f"{action} {opportunity}."
            )
        if kind == "race_control":
            return f"Race control: {payload.get('to', 'change')}. {payload.get('strategy', {}).get('instruction', 'Stand by for the revised plan.')}"
        if kind == "strategy_change":
            return str(payload.get("instruction", "Strategy has changed; check the dashboard."))
        if kind == "tyre_wear":
            return f"Tyre warning, wear is {payload.get('wear_fl_fr_rl_rr')}. {payload.get('strategy', {}).get('instruction', 'Protect the limiting tyre.')}"
        if kind == "lap_deleted":
            return "That qualifying lap is invalid. Reset, recharge, and build the next attempt."
        if kind == "quali_clear_air":
            target = payload.get("target", {})
            return (
                "Clear-air estimate is good for the next push lap. "
                f"Target {target.get('target') or 'is still building'}; prepare the tyres and battery."
            )
        if kind == "blue_flag":
            return "Blue flag. Let the faster car through cleanly without compromising the next corner."
        if kind == "penalty_service":
            if payload.get("drive_through"):
                return "Drive-through penalty outstanding. Serve it within the game deadline; do not combine it with a tyre stop."
            if payload.get("stop_go"):
                return "Stop-go penalty outstanding. Confirm the serving window and avoid further infringements."
            return "Penalty is marked to be served at the stop; the strategy has been updated."
        if kind == "undercut_threat":
            return str(payload.get("instruction") or "Car behind has boxed for the undercut. Push now and stand by for the cover call.")
        if kind == "safety_car_delta":
            return (
                f"You are under the safety-car delta at "
                f"{payload.get('safety_car_delta_s')} seconds. Back off now to stay legal."
            )
        if kind == "corner_coaching":
            return str(
                payload.get("instruction")
                or f"Repeated time loss at {payload.get('name', 'the same corner')}; rebuild that corner next lap."
            )
        if kind == "fuel_warning":
            delta = state.get("fuel_laps_delta")
            return (
                f"Fuel is short by {abs(float(delta)):.1f} laps. Start lifting and coasting on the long straights."
                if isinstance(delta, (int, float))
                else "Fuel is short of race distance. Start lifting and coasting on the long straights."
            )
        if kind == "damage":
            if payload.get("ers_fault"):
                return "ERS fault reported. Expect reduced deployment; we are reassessing the plan."
            if payload.get("drs_fault"):
                return "DRS fault reported. Do not rely on the overtaking aid this lap."
            for key, label in (("engine", "Engine"), ("gearbox", "Gearbox")):
                if int(payload.get(key, 0) or 0) > 10:
                    return f"{label} damage detected. Short-shift where you can and report any change in feel."
            return "Aero damage detected. Report the balance change and we will assess a wing adjustment."
        if kind == "compound_requirement":
            return "You still owe a second dry compound. We must fit a different compound before the finish."
        if kind == "weather_crossover":
            return (
                f"Rain risk is {payload.get('rain_15_pct')} percent within fifteen minutes. "
                "Stand by for a crossover call."
            )
        if kind == "penalty":
            return f"You have picked up a {payload.get('penalties_s')} second penalty. Keep it clean from here."
        if kind == "warning":
            return (
                f"That is {payload.get('corner_cutting_warnings')} track-limit warnings. "
                "Tighten the exits before this becomes a penalty."
            )
        if kind == "rival_pitted":
            return str(
                payload.get("instruction")
                or f"{payload.get('driver', 'A rival')} has boxed. We are recalculating the response."
            )
        return "Engineer update available on the dashboard."

    async def _deliver(self, state: dict[str, Any]) -> None:
        while self.pending and not self._event_still_relevant(self.pending[0], state):
            discarded = self.pending.popleft()
            await self.database.save_proactive_call(state, discarded, "expired or superseded", False)
        if not self.pending:
            return
        event = self.pending[0]
        if not self._safe_to_speak(state, event):
            return
        if not event.get("critical") and time.monotonic() - self._last_spoken_at < settings.proactive_min_interval_s:
            return
        event = self.pending.popleft()
        await self.store.mutate(lambda s: s.proactive.update({"queued": len(self.pending), "delivery_state": "generating"}))
        try:
            text = await asyncio.wait_for(self.brain.proactive(event), timeout=max(8.0, settings.openai_timeout_s))
        except Exception:
            text = self._fallback_text(event, state)
        delivered = False
        try:
            delivered = await self.voice.speak_text(text)
        finally:
            await self.database.save_proactive_call(state, event, text, delivered)
        if delivered:
            self._last_spoken_at = time.monotonic()
            await self.store.mutate(lambda s: s.proactive.update({
                "last_spoken_lap": s.current_lap, "last_call": text,
                "queued": len(self.pending), "delivery_state": "delivered",
            }))

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(0.35)
            try:
                state = await self.store.snapshot_analysis()
                session_uid = int(state.get("session_uid", 0))
                if session_uid != self._session_uid:
                    await self._reset_for_session(session_uid)
                    state = await self.store.snapshot_analysis()
                await self._detect(state)
                oldest = max(0.0, time.time() - float(self.pending[0].get("queued_at", time.time()))) if self.pending else 0.0
                await self.store.mutate(lambda s: s.proactive.update({
                    "queued": len(self.pending), "oldest_wait_s": round(oldest, 1),
                    "delivery_state": "queued" if self.pending else "waiting",
                }))
                await self._deliver(await self.store.snapshot_analysis())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.store.update(last_error=f"Proactive engineer recovered from: {exc}")
