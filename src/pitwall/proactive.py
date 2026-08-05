from __future__ import annotations

import asyncio
import contextlib
import logging
import re
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

log = logging.getLogger(__name__)

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
# Delivery priority. Recorded sessions showed only a third of queued calls ever
# reaching the driver: the queue was strictly first-in-first-out and every
# non-critical call had to wait out the same interval, so a rival stopping or a
# battery warning expired behind a routine progress update. Lower is spoken
# first, and only ``ROUTINE`` waits for the full spacing interval.
# Topics a driver can silence, and the unsolicited call types each covers.
# A standing instruction previously only reached the persona, so it changed the
# wording of a call but never stopped it being made: the recorded session shows
# the engineer agreeing to drop gearbox reminders and then issuing five more.
SUPPRESSIBLE_SUBJECTS: dict[str, frozenset[str]] = {
    "damage": frozenset({"damage", "component_wear"}),
    "gearbox": frozenset({"damage", "component_wear"}),
    "gear": frozenset({"damage", "component_wear"}),
    "engine": frozenset({"damage", "component_wear"}),
    "power unit": frozenset({"damage", "component_wear"}),
    "floor": frozenset({"damage"}),
    "wing": frozenset({"damage"}),
    "reliability": frozenset({"damage", "component_wear"}),
    "fuel": frozenset({"fuel_warning"}),
    "tyre": frozenset({"tyre_wear"}),
    "tire": frozenset({"tyre_wear"}),
    "wear": frozenset({"tyre_wear", "component_wear"}),
    "battery": frozenset({"energy_low"}),
    "ers": frozenset({"energy_low"}),
    "energy": frozenset({"energy_low"}),
    "strategy": frozenset({"strategy_change", "compound_requirement", "driver_check"}),
    "pit": frozenset({"strategy_change", "compound_requirement", "driver_check"}),
    "corner": frozenset({"corner_coaching"}),
    "coaching": frozenset({"corner_coaching"}),
    "line": frozenset({"corner_coaching"}),
    "driving": frozenset({"corner_coaching"}),
    "weather": frozenset({"weather_crossover"}),
    "rain": frozenset({"weather_crossover"}),
    "rival": frozenset({"rival_pace", "rival_pitted", "undercut_threat"}),
    "rivals": frozenset({"rival_pace", "rival_pitted", "undercut_threat"}),
    "gap": frozenset({"rival_pace"}),
    "update": frozenset({"progress_update"}),
    "updates": frozenset({"progress_update"}),
    "progress": frozenset({"progress_update"}),
}
# Calls that concern safety, legality or the car being about to stop. A driver
# may silence a topic; they may not silence a penalty or a red flag.
NEVER_SUPPRESSED = frozenset(
    {
        "race_control", "safety_car_delta", "penalty", "penalty_service",
        "blue_flag", "engine_failure", "sc_restart", "race_start", "lap_deleted",
    }
)

CRITICAL, IMPORTANT, ROUTINE = 0, 1, 2
EVENT_PRIORITY = {
    "race_control": CRITICAL,
    "safety_car_delta": CRITICAL,
    "penalty": CRITICAL,
    "penalty_service": CRITICAL,
    "damage": CRITICAL,
    "engine_failure": CRITICAL,
    "blue_flag": CRITICAL,
    "race_start": CRITICAL,
    "sc_restart": CRITICAL,
    "weather_crossover": CRITICAL,
    "compound_requirement": IMPORTANT,
    "undercut_threat": IMPORTANT,
    "rival_pitted": IMPORTANT,
    "rival_pace": IMPORTANT,
    "strategy_change": IMPORTANT,
    "driver_check": IMPORTANT,
    "tyre_wear": IMPORTANT,
    "fuel_warning": IMPORTANT,
    "energy_low": IMPORTANT,
    "lap_deleted": IMPORTANT,
    "component_wear": IMPORTANT,
    "quali_clear_air": IMPORTANT,
    "warning": IMPORTANT,
    "progress_update": ROUTINE,
    "corner_coaching": ROUTINE,
}
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
        self._discarded: deque[dict[str, Any]] = deque(maxlen=64)
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
        # Damage is reported once per 20% band and once per new fault,
        # rather than on every change to any component.
        self._reported_damage_band = 0
        self._reported_damage_faults: set[str] = set()
        self._last_strategy_signature = ""
        self._last_strategy_inputs_signature = ""
        self._last_corner_signature = ""
        self._last_pit_stops: dict[int, int] = {}
        self._last_lap_invalid = False
        self._last_blue_flag = False
        self._last_unserved_penalties = (0, 0)
        self._last_quali_release_lap = 0
        # car_idx -> (gap_s, monotonic timestamp), for rival gap-trend detection.
        self._rival_gap_history: dict[int, tuple[float, float]] = {}
        # Per-component wear band already alerted, so each PU element escalates
        # independently rather than one shared band hiding the others.
        self._component_alert_bands: dict[str, int] = {}
        self._engine_failure_alerted = False
        self._energy_alert_lap = 0
        self._race_start_done = False
        self._driver_check_stints: set[tuple[str, int]] = set()
        self._cooldowns: dict[str, float] = {}
        self._safe_since = 0.0
        # Refreshed each detection pass so _enqueue can honour them.
        self._standing_instructions: list[Any] = []

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
    def _neighbour_gaps(state: dict[str, Any]) -> dict[str, Any]:
        """Gap and name for the car directly ahead and directly behind.

        Progress payloads previously read ``gap_ahead_s``/``gap_behind_s`` from
        the session state; no such fields exist, so every progress call carried
        two nulls. The values are derived from the standings instead, where
        ``gap_to_player_s`` is negative ahead and positive behind.
        """
        result: dict[str, Any] = {"ahead": None, "behind": None}
        position = int(state.get("player_position", 0) or 0)
        if position <= 0:
            return result
        for driver in state.get("drivers", []):
            gap = driver.get("gap_to_player_s")
            if gap is None:
                continue
            slot = int(driver.get("position", 0) or 0)
            if slot == position - 1:
                result["ahead"] = {
                    "driver": driver.get("name"),
                    "gap_s": round(abs(float(gap)), 2),
                    "tyre": driver.get("tyre_compound"),
                    "tyre_age": driver.get("tyre_age"),
                }
            elif slot == position + 1:
                result["behind"] = {
                    "driver": driver.get("name"),
                    "gap_s": round(abs(float(gap)), 2),
                    "tyre": driver.get("tyre_compound"),
                    "tyre_age": driver.get("tyre_age"),
                }
        return result

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
                "strategy": self._urgent_strategy(state),
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
                    else self._neighbour_gaps(state)
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
        # Rival pace is a continuously improving observation. Refresh the
        # payload of the already queued call in place, preserving its original
        # deadline and queue age so repeated samples cannot postpone it forever.
        if event_type == "rival_pace":
            existing = next(
                (item for item in self.pending if item.get("type") == event_type),
                None,
            )
            if existing is not None:
                existing["payload"] = payload
                existing["last_updated_at"] = time.time()
                return
        if now_mono - self._cooldowns.get(event_type, -1e9) < cooldown_s:
            return
        if self.is_suppressed(event_type, self._standing_instructions):
            return
        self._cooldowns[event_type] = now_mono
        coalesced = {
            "progress_update", "corner_coaching", "race_control", "weather_crossover",
            "fuel_warning", "tyre_wear", "damage", "strategy_change", "compound_requirement",
            "quali_clear_air", "blue_flag", "penalty_service", "undercut_threat",
            "safety_car_delta", "sc_restart", "energy_low", "component_wear",
            # A rival stop that has been superseded by a later one is no longer
            # news. Leaving these uncoalesced filled the queue with stale stops
            # that then aged out unspoken.
            "rival_pace", "engine_failure", "rival_pitted",
            "driver_check",
        }
        if event_type in coalesced:
            kept: list[dict[str, Any]] = []
            for item in self.pending:
                if item.get("type") == event_type:
                    item["delivery_outcome"] = "superseded"
                    item.setdefault("blocked_reasons", []).append("superseded")
                    self._discarded.append(item)
                else:
                    kept.append(item)
            self.pending = deque(kept, maxlen=24)
        now = time.time()
        priority = CRITICAL if critical else EVENT_PRIORITY.get(event_type, ROUTINE)
        event = {
            "type": event_type,
            "critical": critical,
            "priority": priority,
            "payload": payload,
            "queued_at": now,
            "deliver_by": now + (8.0 if critical else settings.proactive_delivery_deadline_s),
            "expires_at": now + (expires_s if expires_s is not None else (45.0 if critical else 180.0)),
            "session_uid": self._session_uid,
            "blocked_reasons": [],
        }
        if len(self.pending) == self.pending.maxlen:
            dropped = self.pending.pop() if critical else self.pending.popleft()
            dropped["delivery_outcome"] = "queue_full"
            dropped.setdefault("blocked_reasons", []).append("queue full")
            self._discarded.append(dropped)
        self.pending.appendleft(event) if critical else self.pending.append(event)

    async def _reset_for_session(self, session_uid: int) -> None:
        self._session_uid = int(session_uid)
        self.pending.clear()
        self._discarded.clear()
        self._last_spoken_at = 0.0
        self._last_lap_queued = 0
        self._last_learning_lap = 0
        self._last_safety_car = "none"
        self._last_race_control_phase = "green"
        self._last_weather_alert = False
        self._last_penalties = self._last_warnings = 0
        self._last_damage_signature = self._last_strategy_signature = ""
        self._reported_damage_band = 0
        self._reported_damage_faults = set()
        self._last_strategy_inputs_signature = self._last_corner_signature = ""
        self._last_pit_stops = {}
        self._last_lap_invalid = False
        self._last_blue_flag = False
        self._last_unserved_penalties = (0, 0)
        self._last_quali_release_lap = 0
        self._rival_gap_history = {}
        self._component_alert_bands = {}
        self._engine_failure_alerted = False
        self._energy_alert_lap = 0
        self._race_start_done = False
        self._driver_check_stints = set()
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

    def _detect_race_start(self, state: dict[str, Any]) -> None:
        """One race-start call on the first green flag: grid slot and lap-1 plan."""
        if self._race_start_done or state.get("mode_profile") not in {"race", "sprint"}:
            return
        phase = str(state.get("race_control_phase", "green"))
        current_lap = int(state.get("current_lap", 0))
        grid = int(state.get("grid_position", 0) or 0)
        # Require a real grid slot: at the exact green moment grid_position can
        # still be 0 (not yet received), which would produce a bogus "P0" call.
        if phase == "green" and current_lap <= 1 and grid > 0:
            self._race_start_done = True
            # Odd grid slots are the (usually grippier) racing-line side.
            clean_side = grid % 2 == 1
            self._enqueue(
                "race_start",
                {
                    "grid_position": grid,
                    "clean_side": clean_side,
                    "ers_pct": state.get("ers_pct"),
                    "front_temps_c": state.get("tyre", {}).get("inner_temps_c", [])[:2],
                },
                critical=True,
                cooldown_s=0.0,
                expires_s=20.0,
            )

    def _detect_component_wear(self, state: dict[str, Any]) -> None:
        """Warn as each power-unit component crosses successive wear thresholds,
        and immediately on a blown or seized engine."""
        damage = state.get("damage", {}) or {}
        if (damage.get("engine_blown") or damage.get("engine_seized")) and not self._engine_failure_alerted:
            self._engine_failure_alerted = True
            self._enqueue(
                "engine_failure",
                {
                    "blown": bool(damage.get("engine_blown")),
                    "seized": bool(damage.get("engine_seized")),
                },
                critical=True,
                cooldown_s=0.0,
                expires_s=60.0,
            )
        wear = state.get("component_wear", {}) or {}
        if not wear:
            return
        # Track a per-component band so a second component crossing 70% is not
        # suppressed by the first (a single shared band hid every later one).
        for key, raw in wear.items():
            value = float(raw)
            band = (int(value) // 10) * 10
            if value >= 70.0 and band > self._component_alert_bands.get(key, 0):
                self._component_alert_bands[key] = band
                self._enqueue(
                    "component_wear",
                    {"component": key, "wear_pct": round(value, 1), "all": wear},
                    critical=value >= 90.0,
                    cooldown_s=120.0,
                    expires_s=120.0,
                )

    def _detect_energy(self, state: dict[str, Any]) -> None:
        """Low-battery warning while attacking, at most once per lap."""
        if state.get("mode_profile") not in {"race", "sprint"}:
            return
        ers = float(state.get("ers_pct", 100.0))
        lap = int(state.get("current_lap", 0))
        drs_or_override = bool(state.get("overtake_available") or state.get("drs_allowed"))
        if ers < 15.0 and drs_or_override and lap != self._energy_alert_lap:
            self._energy_alert_lap = lap
            self._enqueue(
                "energy_low",
                {"ers_pct": round(ers, 1), "regulations_2026": state.get("regulations_2026")},
                cooldown_s=25.0,
                expires_s=30.0,
            )

    def _detect_rival_pace(self, state: dict[str, Any]) -> None:
        """Flag a rival closing fast enough to be in range within a few laps."""
        if state.get("mode_profile") not in {"race", "sprint"}:
            return
        now = time.monotonic()
        player_idx = int(state.get("player_car_index", -1))
        for driver in state.get("drivers", []):
            idx = int(driver.get("car_idx", -1))
            gap = driver.get("gap_to_player_s")
            if idx == player_idx or gap is None:
                continue
            gap = float(gap)
            previous = self._rival_gap_history.get(idx)
            if previous is None:
                self._rival_gap_history[idx] = (gap, now)
                continue
            prev_gap, prev_t = previous
            elapsed = now - prev_t
            # Keep the anchor sample until a full comparison window has passed;
            # overwriting it every poll left elapsed at ~one tick, so the trend
            # never accumulated and the call never fired.
            if elapsed < 8.0:
                continue
            self._rival_gap_history[idx] = (gap, now)
            # gap_to_player_s is negative for a car ahead and positive for a car
            # behind; a closing car behind has a shrinking positive gap.
            behind = gap > 0
            closing = abs(gap) < abs(prev_gap) - 0.15
            if behind and closing and abs(gap) <= 3.0:
                self._enqueue(
                    "rival_pace",
                    {
                        "driver": driver.get("name"),
                        "gap_to_player_s": round(gap, 2),
                        "closing": True,
                    },
                    cooldown_s=30.0,
                    expires_s=35.0,
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
            return (
                bool(state.get("safety_car_delta_valid"))
                and str(state.get("race_control_phase", "green"))
                in NEUTRALISED_PHASES
                and float(state.get("safety_car_delta_s", 0.0) or 0.0)
                < settings.proactive_sc_delta_min_s
            )
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
        if kind == "strategy_change":
            hold = state.get("strategy_hold", {}) or {}
            return not bool(hold.get("active"))
        if kind == "driver_check":
            box_lap = (
                state.get("strategy", {}).get("recommended", {}).get("box_lap")
            )
            current_lap = int(state.get("current_lap", 0) or 0)
            return bool(
                box_lap is not None
                and 1 <= int(box_lap) - current_lap <= 3
                and str(state.get("race_control_phase", "green")) == "green"
            )
        return True

    def _safe_to_speak(self, state: dict[str, Any], event: dict[str, Any]) -> bool:
        if not state.get("connected") or state.get("game_paused"):
            self._safe_since = 0.0
            self._mark_blocked(event, "disconnected or paused")
            return False
        # An open speech session makes the controller "busy" for the whole of its
        # lifetime. Treating that as a blanket block suppressed red flags,
        # penalties, damage and safety-car-delta warnings for up to the maximum
        # session length, by which time they had all expired unspoken. A live
        # conversation can be spoken into — the session itself delivers the line
        # — so only genuine capture (the driver talking) blocks a critical call.
        conversation_open = bool(getattr(self.voice, "realtime_active", False))
        engineer_busy = self.voice.is_busy and not conversation_open
        if state.get("ptt_pressed") or engineer_busy:
            self._safe_since = 0.0
            self._mark_blocked(event, "driver or engineer busy")
            return False
        if conversation_open and self._priority_of(event) != CRITICAL:
            # Non-critical chatter still waits for the driver to finish talking.
            self._safe_since = 0.0
            self._mark_blocked(event, "conversation open")
            return False
        now = time.time()
        overdue = now >= float(event.get("deliver_by", now + 1))
        brake = float(state.get("brake", 0))
        lat_g = abs(float(state.get("lateral_g", 0)))
        speed = int(state.get("speed_kph", 0))
        throttle = float(state.get("throttle", 0))
        safe = speed < 75 or (brake <= settings.proactive_max_brake and lat_g <= settings.proactive_max_lateral_g and throttle >= 0.45)
        if event.get("type") == "energy_low" and self._priority_of(event) <= IMPORTANT:
            # Battery calls matter while attacking. They may use a wider straight-
            # line window, but never heavy braking or a high-G corner.
            safe = safe or (
                throttle >= 0.25
                and brake <= 0.22
                and lat_g <= 1.55
            )
        # Deadline fallback still refuses heavy braking/high-G, but no longer waits
        # forever for full throttle on tracks with short straights.
        if overdue:
            safe = speed < 90 or (brake < 0.35 and lat_g < 1.85)
        if not safe:
            self._safe_since = 0.0
            self._mark_blocked(event, "unsafe driving phase")
            return False
        if event.get("critical") or overdue:
            return True
        if not self._safe_since:
            self._safe_since = time.monotonic()
        ready = time.monotonic() - self._safe_since >= settings.proactive_safe_hold_s
        if not ready:
            self._mark_blocked(event, "safe window not held")
        return ready

    @staticmethod
    def _mark_blocked(event: dict[str, Any], reason: str) -> None:
        reasons = event.setdefault("blocked_reasons", [])
        if reason not in reasons:
            reasons.append(reason)

    @staticmethod
    def _relevance_reason(
        event: dict[str, Any], state: dict[str, Any]
    ) -> str | None:
        if int(event.get("session_uid", 0)) != int(state.get("session_uid", 0)):
            return "superseded by new session"
        if time.time() > float(event.get("expires_at", 0)):
            return "expired"
        return None if ProactiveEngineer._event_still_relevant(event, state) else "superseded"

    @staticmethod
    def _urgent_strategy(state: dict[str, Any]) -> dict[str, Any]:
        if state.get("mode_profile") == "qualifying":
            return {}
        recommended = state.get("strategy", {}).get("recommended", {}) or {}
        box_lap = recommended.get("box_lap")
        current_lap = int(state.get("current_lap", 0))
        wear = max([float(v) for v in state.get("tyre", {}).get("wear", [0]) or [0]])
        neutralised = str(state.get("race_control_phase", "green")) != "green"
        urgent = (
            neutralised
            or wear >= 82.0
            or (box_lap is not None and int(box_lap) <= current_lap + 1)
        )
        return recommended if urgent else {}

    async def _refresh_strategy_if_needed(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("mode_profile") == "qualifying":
            return state
        signature = repr((
            int(state.get("current_lap", 0)), state.get("race_control_phase"),
            state.get("weather"), int(state.get("rain_next_15_pct", 0)),
            state.get("tyre", {}).get("compound"), int(state.get("tyre", {}).get("age_laps", 0)),
            tuple(round(float(x), 1) for x in state.get("tyre", {}).get("wear", [])),
            tuple(sorted((state.get("driver_tyre_feedback", {}) or {}).items())),
            state.get("strategy_risk_appetite", "balanced"),
            tuple((d.get("car_idx"), d.get("pit_stops"), d.get("tyre_compound")) for d in state.get("drivers", [])),
        ))
        if signature != self._last_strategy_inputs_signature:
            self._last_strategy_inputs_signature = signature
            await self.strategy.recompute()
            return await self.store.snapshot_analysis()
        return state

    @staticmethod
    def _hold_material_change(
        state: dict[str, Any], hold: dict[str, Any]
    ) -> str | None:
        baseline = hold.get("baseline", {}) or {}
        phase = str(state.get("race_control_phase", "green"))
        if phase != str(baseline.get("race_control_phase", "green")):
            return f"race control changed to {phase.replace('_', ' ')}"
        wet_now = int(state.get("rain_next_15_pct", 0) or 0) >= 55
        if wet_now and not bool(baseline.get("wet")):
            return "the weather crossed the wet-strategy threshold"
        if str(state.get("tyre", {}).get("compound")) != str(
            baseline.get("compound")
        ):
            return "the fitted tyre changed"
        current_damage = state.get("damage", {}) or {}
        for key, old_value in (baseline.get("damage", {}) or {}).items():
            new_value = current_damage.get(key, 0)
            if isinstance(old_value, bool) or isinstance(new_value, bool):
                increased = bool(new_value) and not bool(old_value)
            else:
                increased = float(new_value or 0) > float(old_value or 0) + 1.0
            if increased:
                return f"new {str(key).replace('_', ' ')} damage arrived"
        max_wear = max(
            [float(value) for value in state.get("tyre", {}).get("wear", [0])]
            or [0.0]
        )
        if max_wear >= 82.0 and float(baseline.get("max_wear_pct", 0.0)) < 82.0:
            return f"limiting tyre wear crossed the hard safety limit at {max_wear:.0f}%"
        return None

    async def _evaluate_strategy_hold(
        self, state: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        hold = dict(state.get("strategy_hold", {}) or {})
        if not hold.get("active"):
            return state, None
        change = self._hold_material_change(state, hold)
        current_lap = int(state.get("current_lap", 0) or 0)
        if change is None and current_lap < int(hold.get("until_lap", 0) or 0):
            return state, None
        reason = change or "the five-lap driver hold expired"
        hold.update(
            active=False,
            change_reason=reason,
            released_at=time.time(),
            raised_after_release=False,
        )
        await self.store.update(strategy_hold=hold)
        refreshed = await self.store.snapshot_analysis()
        return refreshed, reason

    def _detect_driver_check(self, state: dict[str, Any]) -> None:
        if state.get("mode_profile") not in {"race", "sprint"}:
            return
        if str(state.get("race_control_phase", "green")) != "green":
            return
        if self.voice.is_busy or state.get("ptt_pressed"):
            return
        recommended = state.get("strategy", {}).get("recommended", {}) or {}
        box_lap = recommended.get("box_lap")
        current_lap = int(state.get("current_lap", 0) or 0)
        if box_lap is None or int(box_lap) - current_lap not in {2, 3}:
            return
        tyre = state.get("tyre", {}) or {}
        stint_key = (
            str(tyre.get("compound", "UNKNOWN")),
            current_lap - int(tyre.get("age_laps", 0) or 0),
        )
        if stint_key in self._driver_check_stints:
            return
        self._driver_check_stints.add(stint_key)
        plans = state.get("strategy", {}).get("plans", []) or []
        self._enqueue(
            "driver_check",
            {
                "laps_to_window": int(box_lap) - current_lap,
                "box_lap": int(box_lap),
                "current_wear_pct": round(
                    max([float(value) for value in tyre.get("wear", [0])] or [0.0]),
                    1,
                ),
                "degradation_s_per_lap": state.get("analysis", {})
                .get("deg_model", {})
                .get("current_slope_s_per_lap"),
                "projected_finish_wear_pct": recommended.get(
                    "projected_finish_wear_pct"
                ),
                "candidate_plans": [
                    {
                        key: plan.get(key)
                        for key in (
                            "box_laps", "compounds", "projected_finish_position",
                            "projected_finish_wear_pct", "points_expected",
                        )
                    }
                    for plan in plans[:2]
                ],
            },
            cooldown_s=0.0,
            expires_s=90.0,
        )

    async def _detect(self, state: dict[str, Any]) -> None:
        if not state.get("connected") or state.get("game_paused"):
            return
        state = await self._refresh_strategy_if_needed(state)
        state, released_hold_reason = await self._evaluate_strategy_hold(state)
        self._standing_instructions = state.get("standing_instructions", [])
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
                    "strategy": self._urgent_strategy(state),
                    "target": state.get("analysis", {}).get("target", {}),
                    "racing_line": state.get("analysis", {}).get("racing_line", {}),
                    "qualifying": (
                        self._qualifying_payload(state)
                        if mode == "qualifying"
                        else {}
                    ),
                    "gaps": (
                        {} if mode == "qualifying" else self._neighbour_gaps(state)
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
            # A transition into an "ending" phase means the restart is imminent:
            # prep battery, temperatures and the expected go point.
            if phase in {"safety_car_ending", "vsc_ending"}:
                self._enqueue(
                    "sc_restart",
                    {
                        "phase": phase,
                        "position": state.get("player_position"),
                        "ers_pct": state.get("ers_pct"),
                        "front_temps_c": state.get("tyre", {}).get("inner_temps_c", [])[:2],
                    },
                    critical=True,
                    cooldown_s=0.0,
                    expires_s=20.0,
                )

        wet = int(state.get("rain_next_15_pct", 0)) >= 60
        if wet and not self._last_weather_alert:
            self._enqueue("weather_crossover", {"rain_15_pct": state.get("rain_next_15_pct"), "forecast": state.get("weather_forecast", [])}, critical=True, cooldown_s=0.0)
        self._last_weather_alert = wet

        if float(state.get("fuel_laps_delta", 1.0)) < 0.3:
            self._enqueue("fuel_warning", state.get("analysis", {}).get("fuel_model", {}), cooldown_s=90.0)
        wear = max(state.get("tyre", {}).get("wear", [0]) or [0])
        if wear >= 65:
            self._enqueue("tyre_wear", {"wear_fl_fr_rl_rr": state.get("tyre", {}).get("wear"), "strategy": self._urgent_strategy(state)}, critical=wear >= 82, cooldown_s=60.0)

        penalties, warnings = int(state.get("penalties_s", 0)), int(state.get("corner_cutting_warnings", 0))
        if penalties > self._last_penalties:
            self._enqueue("penalty", {"penalties_s": penalties}, critical=True, cooldown_s=0.0)
        elif warnings >= 2 and warnings > self._last_warnings:
            self._enqueue("warning", {"corner_cutting_warnings": warnings}, critical=True, cooldown_s=0.0)
        self._last_penalties, self._last_warnings = penalties, warnings

        damage = state.get("damage", {})
        maximum = max([int(damage.get(key, 0)) for key in DAMAGE_KEYS] or [0])
        # A DRS or ERS fault has no percentage but is immediately race-affecting,
        # so it is called regardless of damage level.
        faults = tuple(key for key in DAMAGE_FAULT_KEYS if damage.get(key))
        # Escalate by 20% band, not on every change. The signature covered every
        # component, so a single percent of floor wear re-fired a *critical*
        # damage call that bypassed the spacing interval — the recorded session
        # has six "box for gearbox inspection" calls in fifteen laps, all
        # reporting the same unactionable damage.
        band = (maximum // 20) * 20
        new_fault = any(fault not in self._reported_damage_faults for fault in faults)
        if (band > self._reported_damage_band and maximum > 10) or new_fault:
            self._reported_damage_band = max(band, self._reported_damage_band)
            self._reported_damage_faults.update(faults)
            self._enqueue(
                "damage",
                {**damage, "max_damage_pct": maximum, "repairable": ["front wing"]},
                critical=maximum >= 40 or bool(new_fault),
                cooldown_s=90.0,
            )

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
        if phase in NEUTRALISED_PHASES and bool(
            state.get("safety_car_delta_valid")
        ):
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

        self._detect_race_start(state)
        self._detect_component_wear(state)
        self._detect_energy(state)
        self._detect_rival_pace(state)
        self._detect_driver_check(state)

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
        hold = state.get("strategy_hold", {}) or {}
        if released_hold_reason and recommended and not hold.get("raised_after_release"):
            payload = {
                **recommended,
                "hold_released_reason": released_hold_reason,
                "instruction": recommended.get("instruction"),
            }
            self._enqueue("strategy_change", payload, cooldown_s=0.0)
            hold = dict(hold)
            hold["raised_after_release"] = True
            await self.store.update(strategy_hold=hold)
            self._last_strategy_signature = strategy_signature
        elif hold.get("active"):
            # Track the current signature without speaking it. When the hold
            # releases, the explicit branch above raises the call exactly once.
            self._last_strategy_signature = strategy_signature
        elif recommended and strategy_signature != self._last_strategy_signature:
            old = self._last_strategy_signature
            self._last_strategy_signature = strategy_signature
            stability = state.get("strategy", {}).get("stability", {})
            if old and not stability.get("held") and self._urgent_strategy(state):
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
            pace = target.get("target") or target.get("target_lap") or "target unavailable"
            action = strategy.get("instruction")
            if action:
                return f"Lap {payload.get('lap')}: target {pace}. {action}"
            return f"Lap {payload.get('lap')}: target {pace}. {opportunity}."
        if kind == "race_control":
            return f"Race control: {payload.get('to', 'change')}. {payload.get('strategy', {}).get('instruction', 'Stand by for the revised plan.')}"
        if kind == "strategy_change":
            changed = payload.get("hold_released_reason")
            prefix = f"Your strategy hold is released because {changed}. " if changed else ""
            return prefix + str(
                payload.get("instruction", "Strategy has changed; check the dashboard.")
            )
        if kind == "driver_check":
            laps = int(payload.get("laps_to_window", 2) or 2)
            return (
                f"{laps} laps to the window. How are the rears â€” holding, "
                "or going away?"
            )
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
            # Only the front wing can be changed at a stop, so reliability damage
            # is reported as something to manage, never as a reason to box.
            for key, label, advice in (
                ("engine", "Engine", "Short-shift where you can; it cannot be repaired at a stop."),
                ("gearbox", "Gearbox", "Shift smoothly; it cannot be repaired at a stop."),
                ("floor", "Floor", "Expect reduced downforce; it cannot be repaired at a stop."),
            ):
                if int(payload.get(key, 0) or 0) > 10:
                    return f"{label} damage {int(payload.get(key, 0))} percent. {advice}"
            wing = max(
                int(payload.get("front_left_wing", 0) or 0),
                int(payload.get("front_right_wing", 0) or 0),
            )
            if wing > 10:
                return (
                    f"Front-wing damage {wing} percent. That is the one thing we can "
                    "change at a stop; say the word if the balance is costing you."
                )
            return "Aero damage detected. Report the balance change and we will assess it."
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
        if kind == "race_start":
            side = "clean side" if payload.get("clean_side") else "dirty side"
            return (
                f"Lights out from P{payload.get('grid_position')}, {side}. "
                "Get temperature into the tyres and commit to your turn-one line."
            )
        if kind == "sc_restart":
            return (
                "Restart coming. Charge the battery, keep temperature in the tyres "
                "and brakes, and watch the leader for the go point."
            )
        if kind == "energy_low":
            aid = "Manual Override" if payload.get("regulations_2026") else "DRS"
            return (
                f"Battery low at {payload.get('ers_pct')} percent. Harvest a lap "
                f"before you commit {aid} again."
            )
        if kind == "component_wear":
            return (
                f"{str(payload.get('component', 'engine')).upper()} wear at "
                f"{payload.get('wear_pct')} percent. Manage it to avoid a component "
                "penalty later in the season."
            )
        if kind == "engine_failure":
            state_word = "seized" if payload.get("seized") else "blown"
            return (
                f"Engine {state_word}. Report any power loss immediately; we are "
                "assessing whether to continue."
            )
        if kind == "rival_pace":
            return (
                f"{payload.get('driver', 'Car behind')} is catching, "
                f"{abs(float(payload.get('gap_to_player_s', 0.0))):.1f} seconds back "
                "and closing. Protect the tyres you will need to defend."
            )
        return "Engineer update available on the dashboard."

    @staticmethod
    def is_suppressed(event_type: str, standing_instructions: list[Any] | None) -> bool:
        """Whether the driver has asked for this kind of call to stop.

        Safety and legality calls are never suppressed, however the instruction
        was worded — the driver can silence gearbox reminders, not a red flag.
        """
        if event_type in NEVER_SUPPRESSED:
            return False
        subjects = EngineerBrain.suppressed_subjects(standing_instructions)
        if not subjects:
            return False
        for subject in subjects:
            words = set(re.findall(r"[a-z]+", subject))
            # Drivers say "tyres", "updates", "rivals"; the topic table is
            # singular. Compare both forms rather than requiring an exact word.
            words |= {word.rstrip("s") for word in words if word.endswith("s")}
            for keyword, covered in SUPPRESSIBLE_SUBJECTS.items():
                if event_type not in covered:
                    continue
                if " " in keyword:
                    if keyword in subject:
                        return True
                elif keyword in words or keyword.rstrip("s") in words:
                    return True
        return False

    @staticmethod
    def _priority_of(event: dict[str, Any]) -> int:
        if event.get("critical"):
            return CRITICAL
        return int(event.get("priority", EVENT_PRIORITY.get(event.get("type"), ROUTINE)))

    async def _narrate(self, event: dict[str, Any], state: dict[str, Any]) -> str:
        """Turn a deterministic event payload into spoken radio.

        The deterministic template is both the safety net and the ground truth:
        every number the engineer speaks is already in the payload, so narration
        changes the wording, never the facts. Falling back to the template on any
        model failure means an unsolicited call is never lost to a timeout.
        """
        fallback = self._fallback_text(event, state)
        if not settings.proactive_narration_enabled:
            return fallback
        try:
            spoken = await asyncio.wait_for(
                self.brain.proactive(event),
                timeout=settings.proactive_narration_timeout_s,
            )
        except Exception as exc:
            log.debug("Proactive narration fell back to the template: %s", exc)
            return fallback
        spoken = " ".join((spoken or "").split())
        return spoken or fallback

    def _select(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """Drop irrelevant calls, then pick the most important speakable one.

        Strict first-in-first-out meant a routine progress update at the head of
        the queue could hold up a safety-car delta warning behind it until both
        expired.
        """
        standing = state.get("standing_instructions", [])
        kept: list[dict[str, Any]] = []
        for event in list(self.pending):
            if not self._event_still_relevant(event, state):
                continue
            # The driver asked for this subject to be dropped. Honour it here so
            # the call is never spoken, rather than only rewording it.
            if self.is_suppressed(str(event.get("type", "")), standing):
                continue
            kept.append(event)
        self.pending = deque(kept, maxlen=24)
        if not self.pending:
            return None
        return min(
            self.pending,
            key=lambda event: (self._priority_of(event), float(event.get("queued_at", 0.0))),
        )

    async def _deliver(self, state: dict[str, Any]) -> None:
        while self._discarded:
            discarded = self._discarded.popleft()
            outcome = str(discarded.get("delivery_outcome") or "superseded")
            await self.database.save_proactive_call(
                state, discarded, outcome, False
            )
        for event in list(self.pending):
            reason = self._relevance_reason(event, state)
            if reason is not None:
                event["delivery_outcome"] = reason
                self._mark_blocked(event, reason)
                await self.database.save_proactive_call(
                    state, event, reason, False
                )

        event = self._select(state)
        if event is None:
            return
        if not self._safe_to_speak(state, event):
            return
        # Only routine chatter waits out the full spacing interval; an important
        # call gets a much shorter one so it is still current when spoken.
        if self._priority_of(event) != CRITICAL:
            interval = (
                settings.proactive_min_interval_s
                if self._priority_of(event) == ROUTINE
                else settings.proactive_important_interval_s
            )
            if time.monotonic() - self._last_spoken_at < interval:
                self._mark_blocked(event, "minimum interval")
                return

        with contextlib.suppress(ValueError):
            self.pending.remove(event)
        await self.store.mutate(lambda s: s.proactive.update({"queued": len(self.pending), "delivery_state": "generating"}))
        text = await self._narrate(event, state)
        delivered = False
        try:
            delivered = await self.voice.speak_text(text)
        finally:
            event["delivery_outcome"] = "delivered" if delivered else "voice delivery failed"
            await self.database.save_proactive_call(state, event, text, delivered)
        if delivered:
            self._last_spoken_at = time.monotonic()
            # Only a call the driver actually heard becomes conversation history.
            await self.brain.record_spoken_call(text)
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
                await self.store.mutate(lambda s, wait=oldest: s.proactive.update({
                    "queued": len(self.pending), "oldest_wait_s": round(wait, 1),
                    "delivery_state": "queued" if self.pending else "waiting",
                }))
                await self._deliver(await self.store.snapshot_analysis())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.store.update(last_error=f"Proactive engineer recovered from: {exc}")
