from __future__ import annotations

import asyncio
import bisect
import copy
import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any, Literal

WHEEL_LABELS = ("FL", "FR", "RL", "RR")
SnapshotProfile = Literal["full", "analysis", "live"]


@dataclass(slots=True)
class TyreState:
    compound: str = "UNKNOWN"
    actual_compound: int = 0
    age_laps: int = 0
    # All wheel arrays in Pit Wall are normalized to FL, FR, RL, RR.
    wear: list[float] = field(default_factory=lambda: [0.0] * 4)
    damage: list[int] = field(default_factory=lambda: [0] * 4)
    blisters: list[int] = field(default_factory=lambda: [0] * 4)
    inner_temps_c: list[float] = field(default_factory=lambda: [0.0] * 4)
    surface_temps_c: list[float] = field(default_factory=lambda: [0.0] * 4)
    pressures_psi: list[float] = field(default_factory=lambda: [0.0] * 4)


@dataclass(slots=True)
class DriverState:
    """Per-car state for the whole field.

    Every F1 26 telemetry packet carries all 24 cars. Earlier releases read only
    ``player_car_index`` from the damage, status, telemetry and motion packets,
    so questions about a rival's tyres, fuel, energy or damage could not be
    answered even though the data had already arrived. Fields sourced from a
    rival's car are ``None``/empty when that car's telemetry is ``restricted``
    (online privacy setting), never silently zero.
    """

    car_idx: int
    name: str = "Unknown"
    # ``team`` stays empty until a verified team-id name table exists for the
    # current title; ``team_id`` is the authoritative value from the packet and
    # is what teammate detection uses.
    team: str = ""
    team_id: int = -1
    driver_id: int = -1
    race_number: int = 0
    ai_controlled: bool = True
    is_teammate: bool = False
    is_player: bool = False
    active: bool = False
    position: int = 0
    grid_position: int = 0
    current_lap: int = 0
    gap_to_player_s: float | None = None
    delta_to_front_s: float | None = None
    delta_to_leader_s: float | None = None
    last_lap_ms: int = 0
    best_lap_ms: int = 0
    tyre_compound: str = "UNKNOWN"
    tyre_actual_compound: int = 0
    tyre_age: int = 0
    pit_stops: int = 0
    restricted: bool = False
    status: str = ""
    result_status: int = 0
    result_label: str = ""
    # Live pit-lane state. ``pit_stop_timer_ms`` is the stationary time of a stop
    # in progress, which is what makes a rival's stop observable as it happens.
    pit_status: int = 0
    pit_lane_timer_active: bool = False
    pit_lane_time_ms: int = 0
    pit_stop_timer_ms: int = 0
    # Condition, from the per-car damage packet.
    tyre_wear: list[float] = field(default_factory=lambda: [0.0] * 4)
    tyre_damage: list[int] = field(default_factory=lambda: [0] * 4)
    blisters: list[int] = field(default_factory=lambda: [0] * 4)
    damage: dict[str, Any] = field(default_factory=dict)
    component_wear: dict[str, float] = field(default_factory=dict)
    # Energy and fuel, from the per-car status packet.
    fuel_kg: float | None = None
    fuel_remaining_laps: float | None = None
    ers_pct: float | None = None
    ers_mode: int = 0
    ers_deployed_lap_j: float = 0.0
    ers_harvested_lap_j: float = 0.0
    drs_allowed: bool = False
    fia_flag: str = "none"
    front_brake_bias: int = 0
    fuel_mix: int = 0
    # Live telemetry, from the per-car telemetry packets.
    speed_kph: int = 0
    drs_open: bool = False
    tyre_inner_temps_c: list[float] = field(default_factory=lambda: [0.0] * 4)
    tyre_surface_temps_c: list[float] = field(default_factory=lambda: [0.0] * 4)
    overtake_active: bool = False
    overtake_available: bool = False
    # Track position, from the motion packet. Used for real traffic and rejoin
    # estimates rather than timing-gap approximations.
    world_x: float = 0.0
    world_z: float = 0.0
    lap_distance_m: float = 0.0
    total_distance_m: float = 0.0
    speed_trap_kph: float = 0.0
    speed_trap_lap: int = 0
    penalties_s: int = 0
    warnings: int = 0
    lap_history: list[dict[str, Any]] = field(default_factory=list)
    tyre_stints: list[dict[str, Any]] = field(default_factory=list)
    position_history: list[dict[str, int]] = field(default_factory=list)
    # Per-lap deployment evidence and completed stationary pit-stop times. These
    # histories are bounded by the UDP handlers and omitted from the 4 Hz live
    # snapshot; briefings use the full analysis snapshot.
    energy_lap_history: list[dict[str, Any]] = field(default_factory=list)
    overtake_used_this_lap: bool = False
    pit_stop_history: list[dict[str, Any]] = field(default_factory=list)
    current_pit_stop_max_ms: int = 0
    # Lightweight live gap samples used for evidence-based closing/falling-back
    # answers. This is independent of restricted session-history packets.
    gap_history: list[dict[str, float | int]] = field(default_factory=list)
    history_updated_at: float = 0.0


@dataclass(slots=True)
class SessionState:
    connected: bool = False
    packet_format: int = 0
    game_year: int = 0
    session_uid: int = 0
    session_type: str = "Waiting for F1 telemetry"
    mode_profile: str = "idle"
    raw_session_type_id: int = 0
    raw_session_type: str = "Unknown"
    session_detection_source: str = "udp"
    session_detection_reason: str = ""
    session_length_id: int = 0
    session_length_label: str = "None"
    num_sessions_in_weekend: int = 0
    weekend_structure: list[int] = field(default_factory=list)
    session_mode_override: str = "auto"
    track_id: int = -1
    track_name: str = "—"
    track_length_m: int = 0
    session_time_left_s: int = 0
    session_duration_s: int = 0
    total_laps: int = 0
    current_lap: int = 0
    current_lap_time_ms: int = 0
    last_lap_ms: int = 0
    lap_distance_m: float = 0.0
    sector: int = 0
    sector2_start_m: float = 0.0
    sector3_start_m: float = 0.0
    current_lap_invalid: bool = False
    grid_position: int = 0
    safety_car_delta_s: float = 0.0
    safety_car_delta_valid: bool = False
    unserved_drive_through_penalties: int = 0
    unserved_stop_go_penalties: int = 0
    pit_stop_should_serve_penalty: bool = False
    # Live delta (seconds) to the reference lap at the current lap distance;
    # negative is ahead of reference. Computed per telemetry tick.
    live_delta_s: float | None = None
    live_delta_reference: str = ""
    # Power-unit component wear (percent used), from the car-damage packet.
    component_wear: dict[str, float] = field(default_factory=dict)
    # Multiplayer lobby roster, when a lobby-info packet is received.
    lobby: dict[str, int] = field(default_factory=dict)
    # Time Trial personal best, session best and rival, with the assists each
    # was set with. Previously the packet had no handler at all.
    time_trial: dict[str, Any] = field(default_factory=dict)
    game_paused: bool = False
    active_cars: int = 0
    player_car_index: int = 0
    player_position: int = 0
    speed_kph: int = 0
    gear: int = 0
    throttle: float = 0.0
    brake: float = 0.0
    steer: float = 0.0
    lateral_g: float = 0.0
    longitudinal_g: float = 0.0
    world_x: float = 0.0
    world_y: float = 0.0
    world_z: float = 0.0
    forward_x: float = 0.0
    forward_z: float = 1.0
    wheel_slip_ratio: list[float] = field(default_factory=lambda: [0.0] * 4)
    wheel_slip_angle: list[float] = field(default_factory=lambda: [0.0] * 4)
    weather: str = "Unknown"
    weather_forecast: list[dict[str, Any]] = field(default_factory=list)
    rain_next_15_pct: int = 0
    rain_next_30_pct: int = 0
    rain_next_60_pct: int = 0
    track_temp_c: int = 0
    air_temp_c: int = 0
    # Marshal-zone flags around the lap, each with a start fraction (0..1) and a
    # flag colour. This is the only field-wide source of *where* a yellow is.
    marshal_zones: list[dict[str, Any]] = field(default_factory=list)
    drs_zones: list[dict[str, float]] = field(default_factory=list)
    active_aero_zones: list[dict[str, Any]] = field(default_factory=list)
    active_aero_track_status: int = 0
    pit_speed_limit_kph: int = 0
    # Session rules and assists that change how the strategy model should behave:
    # parc fermé restricts setup changes, damage rate scales the wear model, and
    # equal performance removes car-pace differences between rivals.
    ai_difficulty: int = 0
    equal_car_performance: bool = False
    parc_ferme_rules: bool = False
    car_damage_rate: int = 0
    tyre_temperature_sim: int = 0
    pit_lane_tyre_sim: bool = False
    forecast_accuracy: int = 0
    game_mode: int = 0
    rule_set: int = 0
    formula: int = 0
    time_of_day_min: int = 0
    network_game: bool = False
    weekend_link_identifier: int = 0
    session_link_identifier: int = 0
    safety_car: str = "none"
    safety_car_periods: int = 0
    virtual_safety_car_periods: int = 0
    red_flag_count: int = 0
    red_flag_active: bool = False
    race_control_phase: str = "green"
    race_control_changed_at: float = 0.0
    last_safety_car_event_type: int = -1
    fuel_kg: float = 0.0
    fuel_laps_delta: float = 0.0
    fuel_remaining_laps: float = 0.0
    ers_pct: float = 0.0
    ers_mode: int = 0
    ers_deployed_lap_j: float = 0.0
    ers_harvested_lap_j: float = 0.0
    drs_allowed: bool = False
    active_aero_mode: int = 0
    active_aero_available: bool = False
    active_aero_activation_distance_m: int = 0
    overtake_available: bool = False
    overtake_active: bool = False
    overtake_activation_distance_m: int = 0
    regulations_2026: bool = False
    driving_wrong_way: bool = False
    tyre: TyreState = field(default_factory=TyreState)
    tyre_sets: list[dict[str, Any]] = field(default_factory=list)
    fitted_tyre_set_idx: int = -1
    car_setup: dict[str, Any] = field(default_factory=dict)
    next_front_wing_value: float = 0.0
    damage: dict[str, Any] = field(default_factory=dict)
    penalties_s: int = 0
    warnings: int = 0
    corner_cutting_warnings: int = 0
    fia_flag: str = "none"
    pit_status: int = 0
    pit_lane_time_ms: int = 0
    packet_rate_hz: float = 0.0
    packets_received: int = 0
    packets_dropped: int = 0
    packet_queue_depth: int = 0
    packet_queue_capacity: int = 0
    last_packet_at: float = 0.0
    ptt_mask: int = 0
    ptt_pressed: bool = False
    ptt_status: str = "unconfigured"
    ptt_release_mode: str = "explicit_or_silence"
    ptt_last_button_status: int = 0
    ptt_mask_events: int = 0
    ptt_release_reason: str = ""
    wake_enabled: bool = True
    wake_status: str = "starting"
    wake_phrase: str = "mark"
    wake_armed: bool = False
    wake_trigger_count: int = 0
    wake_rejected_count: int = 0
    wake_last_transcript: str = ""
    wake_last_reason: str = ""
    wake_config_source: str = "default"
    wake_input_rms: float = 0.0
    wake_noise_rms: float = 0.0
    wake_threshold_rms: float = 0.0
    engineer_status: str = "standing by"
    # Spoken tyre-temperature unit selected by the driver. Telemetry itself
    # remains Celsius.
    temperature_unit: str = "c"
    # Driver-selected spoken length persists across sessions, just like units.
    radio_verbosity: str = "standard"
    # Standing instructions the driver has given the engineer ("stop telling me
    # about engine damage", "don't call gaps in quali"). These were previously
    # answered once and forgotten, so the driver had to repeat them.
    standing_instructions: list[dict[str, Any]] = field(default_factory=list)
    # Signature of the last strategy call actually spoken to the driver.
    strategy_spoken_signature: str = ""
    # A refused pit call pauses automatic strategy-change radio for five laps.
    # Safety/legality calls remain active and material race-state changes can
    # release the hold once with an explicit reason.
    strategy_hold: dict[str, Any] = field(
        default_factory=lambda: {
            "active": False,
            "until_lap": 0,
            "reason": "",
            "set_at": 0.0,
            "set_at_lap": 0,
            "baseline": {},
            "change_reason": "",
            "raised_after_release": False,
        }
    )
    driver_tyre_feedback: dict[str, Any] = field(default_factory=dict)
    strategy_risk_appetite: str = "balanced"
    # Driver-owned plan constraints. When enabled, strategy ranks legal plans
    # inside these constraints instead of silently replacing the driver's call.
    strategy_override: dict[str, Any] = field(
        default_factory=lambda: {
            "enabled": False,
            "locked": False,
            "start_compound": None,
            "next_box_lap": None,
            "next_compound": None,
            "preferred_stops": None,
            "priority": "balanced",
            "source": "",
            "note": "",
            "updated_at": 0.0,
        }
    )
    # Persistent driver preferences used by setup and strategy priors. These are
    # deliberately compact and bounded; telemetry evidence still wins on safety.
    driver_preferences: dict[str, Any] = field(
        default_factory=lambda: {
            "strategy_priority": "balanced",
            "setup_bias": "neutral",
            "rear_stability": 0,
            "rotation": 0,
            "traction": 0,
            "tyre_life": 0,
            "straight_line": 0,
            "strategy_risk_appetite": "balanced",
        }
    )
    # Grid frozen at the most recent red flag, used for restart briefings.
    restart_grid: list[dict[str, Any]] = field(default_factory=list)
    llm_provider: str = ""
    llm_model: str = ""
    llm_last_latency_ms: float = 0.0
    llm_last_tool_rounds: int = 0
    llm_last_error: str = ""
    # Dashboard radio rail: idle, listening (yellow), processing (green),
    # speaking (blue), or error (red).
    radio_indicator: str = "idle"
    radio_source: str = ""
    radio_queue_depth: int = 0
    radio_last_transcript: str = ""
    radio_latency: dict[str, Any] = field(
        default_factory=lambda: {
            "stage": "idle",
            "source": "",
            "route": "",
            "capture_finalized_ms": None,
            "transcript_ms": None,
            "model_ms": None,
            "first_audio_ms": None,
            "complete_ms": None,
            "ack": "",
        }
    )
    last_error: str = ""
    drivers: list[DriverState] = field(
        default_factory=lambda: [DriverState(i) for i in range(24)]
    )
    radio_log: list[dict[str, str]] = field(default_factory=list)
    events_log: list[dict[str, Any]] = field(default_factory=list)
    traces: list[dict[str, Any]] = field(default_factory=list)
    # Completed laps are summaries only. Full traces are passed once through
    # lap_queue and persisted to SQLite by AnalysisEngine.
    completed_laps: list[dict[str, Any]] = field(default_factory=list)
    current_lap_started: dict[str, Any] = field(default_factory=dict)
    analysis: dict[str, Any] = field(
        default_factory=lambda: {
            "last_lap_analyzed": 0,
            "lap_summary": {},
            "corner_metrics": [],
            "flagged_corners": [],
            "deg_model": {},
            "fuel_model": {},
            "target": {},
            "progress": {},
            "racing_line": {},
            "line_history": [],
        }
    )
    strategy: dict[str, Any] = field(default_factory=dict)
    setup_recommendation: dict[str, Any] = field(default_factory=dict)
    feedback: list[dict[str, Any]] = field(default_factory=list)
    proactive: dict[str, Any] = field(
        default_factory=lambda: {
            "enabled": True,
            "cadence_laps": 2,
            "last_spoken_lap": 0,
            "queued": 0,
            "last_call": "",
            "last_queued_lap": 0,
            "next_due_lap": 2,
            "oldest_wait_s": 0.0,
            "delivery_state": "idle",
        }
    )
    final_classification: dict[str, Any] = field(default_factory=dict)
    # Latest generated brief/debrief of each kind for the live and review UI.
    briefings: dict[str, Any] = field(default_factory=dict)


class StateStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.state = SessionState()
        self._packet_times: deque[float] = deque()
        self.lap_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)
        self.event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        # Reference lap for the live delta: sorted (lap_distance_m, time_into_lap_s).
        self._delta_ref: list[tuple[float, float]] = []

    async def set_delta_reference(
        self,
        trace: list[dict[str, Any]],
        label: str,
    ) -> None:
        """Install a reference lap for the live delta.

        Trace ``t`` values are absolute session time, so they are normalised to
        time-into-lap by subtracting the lap's first sample. Distances must be
        monotonic for the interpolation, so out-of-order points are dropped.
        """
        pairs: list[tuple[float, float]] = []
        base_t: float | None = None
        last_d = -1.0
        for point in trace:
            if "d" not in point or "t" not in point:
                continue
            distance = float(point["d"])
            if base_t is None:
                base_t = float(point["t"])
            if distance < last_d:
                continue
            last_d = distance
            pairs.append((distance, float(point["t"]) - base_t))
        async with self._lock:
            self._delta_ref = pairs
            self.state.live_delta_reference = label if pairs else ""
            if not pairs:
                self.state.live_delta_s = None

    def reference_time_at(self, distance: float) -> float | None:
        """Interpolated reference time-into-lap at a lap distance, or None.

        Pure read of the cached reference; safe to call while already holding
        the store lock (e.g. from inside a mutate callback).
        """
        ref = self._delta_ref
        if not ref:
            return None
        if distance <= ref[0][0]:
            return ref[0][1]
        if distance >= ref[-1][0]:
            return ref[-1][1]
        index = bisect.bisect_left(ref, (distance, -math.inf))
        low_d, low_t = ref[index - 1]
        high_d, high_t = ref[index]
        span = high_d - low_d
        if span <= 0:
            return low_t
        fraction = (distance - low_d) / span
        return low_t + fraction * (high_t - low_t)

    def _serialize_locked(self, profile: SnapshotProfile) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for item in fields(self.state):
            name = item.name
            value = getattr(self.state, name)
            if name == "drivers":
                drivers: list[dict[str, Any]] = []
                for driver in value:
                    is_player = int(driver.car_idx) == int(self.state.player_car_index)
                    # Keep every participating car, not only those holding a
                    # timing position. A retired or garaged car reports position
                    # 0, so the previous filter deleted exactly the cars the
                    # engineer needs in order to explain the race: retirements,
                    # cars yet to leave the garage, and the field before the
                    # first Lap Data packet arrives.
                    if not is_player and not driver.active:
                        continue
                    serialized = asdict(driver)
                    if profile == "live":
                        serialized.pop("lap_history", None)
                        serialized.pop("tyre_stints", None)
                        serialized.pop("position_history", None)
                        serialized.pop("gap_history", None)
                        serialized.pop("energy_lap_history", None)
                        serialized.pop("pit_stop_history", None)
                    drivers.append(serialized)
                data[name] = drivers
            elif name == "traces":
                if profile == "analysis":
                    data[name] = []
                elif profile == "live":
                    # Keep the dashboard responsive on long circuits. The graph
                    # does not need every sampled point once the trace is dense.
                    step = max(1, len(value) // 1200)
                    data[name] = copy.deepcopy(value[::step])
                    if value and data[name] and data[name][-1] != value[-1]:
                        data[name].append(copy.deepcopy(value[-1]))
                else:
                    data[name] = copy.deepcopy(value)
            elif name == "completed_laps" and profile == "live":
                # The live dashboard consumes derived analysis/strategy, not the
                # full in-memory lap-summary history. Keep this payload bounded.
                data[name] = []
            elif name == "events_log" and profile == "live":
                data[name] = copy.deepcopy(value[-30:])
            elif name == "radio_log" and profile == "live":
                data[name] = copy.deepcopy(value[-40:])
            elif name == "feedback" and profile == "live":
                data[name] = copy.deepcopy(value[-10:])
            elif name == "strategy" and profile == "live":
                compact = copy.deepcopy(value)
                if isinstance(compact, dict) and len(compact.get("plans", [])) > 3:
                    compact["plans"] = compact["plans"][:3]
                data[name] = compact
            elif is_dataclass(value):
                data[name] = asdict(value)
            else:
                data[name] = copy.deepcopy(value)

        data["telemetry_stale"] = not data["connected"] and bool(data["last_packet_at"])
        data["wheel_labels"] = list(WHEEL_LABELS)
        if profile == "live":
            # A bounded sparkline feed avoids returning the full completed-lap
            # history while still making the dashboard's pace trend useful.
            data["recent_laps"] = copy.deepcopy(self.state.completed_laps[-8:])
        return data

    async def snapshot(self) -> dict[str, Any]:
        """Complete snapshot for tool calls and lower-frequency analysis."""
        async with self._lock:
            return self._serialize_locked("full")

    async def snapshot_analysis(self) -> dict[str, Any]:
        """Analysis snapshot without the in-progress 60 Hz trace."""
        async with self._lock:
            return self._serialize_locked("analysis")

    async def snapshot_live(self) -> dict[str, Any]:
        """Compact dashboard snapshot without per-driver history payloads."""
        async with self._lock:
            return self._serialize_locked("live")

    async def peek(self, *names: str) -> dict[str, Any]:
        """Read a few scalar fields without serialising the whole session.

        Several UDP handlers only need one or two values (the player index, the
        active-car count, the session UID) but were taking a full ``snapshot_live``
        — a deep copy of every driver, trace point and log entry — to get them,
        on packets that arrive at up to 60 Hz.

        Containers are copied so a caller cannot mutate live state by accident;
        scalars are returned directly, which is the common case.
        """
        async with self._lock:
            result: dict[str, Any] = {}
            for name in names:
                value = getattr(self.state, name, None)
                result[name] = (
                    copy.deepcopy(value)
                    if isinstance(value, (dict, list, set))
                    else value
                )
            return result

    async def update(self, **values: Any) -> None:
        async with self._lock:
            for key, value in values.items():
                if hasattr(self.state, key):
                    setattr(self.state, key, value)

    async def mutate(self, callback: Callable[[SessionState], Any]) -> None:
        async with self._lock:
            callback(self.state)

    async def mark_packet(
        self, packet_format: int, game_year: int, session_uid: int
    ) -> None:
        now = time.monotonic()
        async with self._lock:
            if (
                self.state.session_uid
                and session_uid
                and session_uid != self.state.session_uid
            ):
                ptt_mask = self.state.ptt_mask
                ptt_status = self.state.ptt_status
                # Manual session labels and strategy locks are session-scoped.
                # A pre-session override survives the first UID because this reset
                # only runs when replacing one non-zero UID with another.
                driver_preferences = copy.deepcopy(self.state.driver_preferences)
                risk_appetite = str(self.state.strategy_risk_appetite)
                cadence = int(self.state.proactive.get("cadence_laps", 2))
                proactive = {
                    "enabled": bool(self.state.proactive.get("enabled", True)),
                    "cadence_laps": cadence,
                    "last_spoken_lap": 0,
                    "queued": 0,
                    "last_call": "",
                    "last_queued_lap": 0,
                    "next_due_lap": cadence,
                    "oldest_wait_s": 0.0,
                    "delivery_state": "idle",
                }
                self.state = SessionState(
                    ptt_mask=ptt_mask,
                    ptt_status=ptt_status,
                    ptt_release_mode=self.state.ptt_release_mode,
                    ptt_last_button_status=self.state.ptt_last_button_status,
                    ptt_mask_events=self.state.ptt_mask_events,
                    ptt_release_reason=self.state.ptt_release_reason,
                    wake_enabled=self.state.wake_enabled,
                    wake_status=self.state.wake_status,
                    wake_phrase=self.state.wake_phrase,
                    wake_armed=self.state.wake_armed,
                    wake_trigger_count=self.state.wake_trigger_count,
                    wake_rejected_count=self.state.wake_rejected_count,
                    wake_last_transcript=self.state.wake_last_transcript,
                    wake_last_reason=self.state.wake_last_reason,
                    wake_config_source=self.state.wake_config_source,
                    wake_input_rms=self.state.wake_input_rms,
                    wake_noise_rms=self.state.wake_noise_rms,
                    wake_threshold_rms=self.state.wake_threshold_rms,
                    llm_provider=self.state.llm_provider,
                    llm_model=self.state.llm_model,
                    llm_last_latency_ms=self.state.llm_last_latency_ms,
                    llm_last_tool_rounds=self.state.llm_last_tool_rounds,
                    llm_last_error=self.state.llm_last_error,
                    temperature_unit=self.state.temperature_unit,
                    radio_verbosity=self.state.radio_verbosity,
                    strategy_spoken_signature="",
                    proactive=proactive,
                    session_mode_override="auto",
                    driver_preferences=driver_preferences,
                    strategy_risk_appetite=risk_appetite,
                )
                self._packet_times.clear()
                # The live-delta reference belongs to the previous session's
                # track/PB; drop it so a new session does not compare against a
                # stale reference until its own personal best is established.
                self._delta_ref = []

            self._packet_times.append(now)
            cutoff = now - 2.0
            while self._packet_times and self._packet_times[0] < cutoff:
                self._packet_times.popleft()
            self.state.connected = True
            self.state.packet_format = int(packet_format)
            self.state.game_year = int(game_year)
            self.state.session_uid = int(session_uid)
            self.state.last_packet_at = time.time()
            self.state.packets_received += 1
            self.state.packet_rate_hz = round(len(self._packet_times) / 2.0, 1)

    async def mark_disconnected_if_stale(self, stale_after_s: float) -> bool:
        now = time.time()
        async with self._lock:
            if (
                self.state.connected
                and self.state.last_packet_at
                and now - self.state.last_packet_at > stale_after_s
            ):
                self.state.connected = False
                self.state.packet_rate_hz = 0.0
                return True
            return False

    async def set_packet_queue_stats(self, depth: int, capacity: int) -> None:
        async with self._lock:
            self.state.packet_queue_depth = max(0, int(depth))
            self.state.packet_queue_capacity = max(0, int(capacity))

    async def increment_dropped_packets(self, count: int = 1) -> None:
        async with self._lock:
            self.state.packets_dropped += max(1, int(count))

    @staticmethod
    def _append_trace_locked(
        state: SessionState,
        point: dict[str, Any],
        min_distance_m: float = 1.5,
    ) -> None:
        traces = state.traces
        if traces:
            previous = traces[-1]
            distance_change = float(point["d"]) - float(previous["d"])
            time_change = float(point["t"]) - float(previous["t"])
            if distance_change < min_distance_m and time_change < 0.05:
                return
        traces.append(point)
        if len(traces) > 6000:
            del traces[: len(traces) - 6000]

    async def add_trace_point(
        self, point: dict[str, Any], min_distance_m: float = 1.5
    ) -> None:
        async with self._lock:
            self._append_trace_locked(self.state, point, min_distance_m)

    async def update_telemetry_and_trace(
        self,
        *,
        session_time: float,
        speed_kph: int,
        gear: int,
        throttle: float,
        brake: float,
        steer: float,
        inner_temps_c: list[float],
        surface_temps_c: list[float],
        pressures_psi: list[float],
        world_x: float | None = None,
        world_y: float | None = None,
        world_z: float | None = None,
        forward_x: float | None = None,
        forward_z: float | None = None,
    ) -> None:
        """Update telemetry and trace atomically without a 60 Hz deep snapshot."""
        async with self._lock:
            state = self.state
            state.speed_kph = int(speed_kph)
            state.gear = int(gear)
            state.throttle = float(throttle)
            state.brake = float(brake)
            state.steer = float(steer)
            state.tyre.inner_temps_c = list(inner_temps_c)
            state.tyre.surface_temps_c = list(surface_temps_c)
            state.tyre.pressures_psi = list(pressures_psi)
            if world_x is not None:
                state.world_x = float(world_x)
            if world_y is not None:
                state.world_y = float(world_y)
            if world_z is not None:
                state.world_z = float(world_z)
            if forward_x is not None:
                state.forward_x = float(forward_x)
            if forward_z is not None:
                state.forward_z = float(forward_z)
            if (
                state.red_flag_active
                and not state.game_paused
                and int(speed_kph) > 20
                and time.time() - state.race_control_changed_at > 10.0
            ):
                state.red_flag_active = False
                state.race_control_phase = (
                    "safety_car"
                    if state.safety_car == "full"
                    else "vsc"
                    if state.safety_car == "virtual"
                    else "formation"
                    if state.safety_car == "formation"
                    else "green"
                )
                state.race_control_changed_at = time.time()
            self._append_trace_locked(
                state,
                {
                    "t": float(session_time),
                    "d": float(state.lap_distance_m),
                    "speed": int(speed_kph),
                    "throttle": float(throttle),
                    "brake": float(brake),
                    "steer": float(steer),
                    "gear": int(gear),
                    "lat_g": float(state.lateral_g),
                    "long_g": float(state.longitudinal_g),
                    "slip": list(state.wheel_slip_ratio),
                    "x": float(state.world_x),
                    "y": float(state.world_y),
                    "z": float(state.world_z),
                    "fx": float(state.forward_x),
                    "fz": float(state.forward_z),
                },
            )

    async def transition_lap(
        self,
        new_lap: int,
        last_lap_ms: int,
        current_lap_invalid: bool,
        position: int,
        pit_status: int,
        pit_lane_time_ms: int,
    ) -> dict[str, Any] | None:
        completed: dict[str, Any] | None = None
        async with self._lock:
            state = self.state
            old_lap = state.current_lap
            if old_lap and new_lap > old_lap and state.traces:
                start = state.current_lap_started or {}
                completed = {
                    "session_uid": state.session_uid,
                    "track_id": state.track_id,
                    "track_name": state.track_name,
                    "session_type": state.session_type,
                    "mode_profile": state.mode_profile,
                    "lap_num": old_lap,
                    "lap_time_ms": int(last_lap_ms or state.last_lap_ms),
                    "valid": not bool(state.current_lap_invalid),
                    "compound": state.tyre.compound,
                    "tyre_age_end": state.tyre.age_laps,
                    "tyre_age_start": int(
                        start.get("tyre_age", max(0, state.tyre.age_laps - 1))
                    ),
                    "wear_start": start.get("wear", list(state.tyre.wear)),
                    "wear_end": list(state.tyre.wear),
                    "temps_end": list(state.tyre.inner_temps_c),
                    "track_temp_c": int(state.track_temp_c),
                    "air_temp_c": int(state.air_temp_c),
                    "weather": state.weather,
                    "fuel_start_kg": float(start.get("fuel_kg", state.fuel_kg)),
                    "fuel_end_kg": state.fuel_kg,
                    "position": position,
                    "pit_status": pit_status,
                    "pit_lane_time_ms": pit_lane_time_ms,
                    "setup": copy.deepcopy(state.car_setup),
                    "trace": copy.deepcopy(state.traces),
                    "created_at": time.time(),
                }
                summary = {
                    key: copy.deepcopy(value)
                    for key, value in completed.items()
                    if key != "trace"
                }
                state.completed_laps.append(summary)
                state.completed_laps = state.completed_laps[-100:]
                state.traces.clear()

            state.current_lap = max(0, int(new_lap))
            state.last_lap_ms = int(last_lap_ms)
            if new_lap != old_lap:
                state.current_lap_started = {
                    "fuel_kg": state.fuel_kg,
                    "tyre_age": state.tyre.age_laps,
                    "wear": list(state.tyre.wear),
                    "position": position,
                }

        if completed is not None:
            try:
                self.lap_queue.put_nowait(completed)
            except asyncio.QueueFull:
                try:
                    self.lap_queue.get_nowait()
                    self.lap_queue.task_done()
                except asyncio.QueueEmpty:
                    pass
                self.lap_queue.put_nowait(completed)
        return completed

    async def update_completed_lap_summary(
        self,
        lap_num: int,
        values: dict[str, Any],
    ) -> None:
        async with self._lock:
            for lap in reversed(self.state.completed_laps):
                if int(lap.get("lap_num", -1)) == int(lap_num):
                    lap.update(copy.deepcopy(values))
                    break

    async def merge_player_lap_history(self, history: list[dict[str, Any]]) -> None:
        async with self._lock:
            by_lap = {item.get("lap_num"): item for item in history}
            for lap in self.state.completed_laps:
                source = by_lap.get(lap.get("lap_num"))
                if source:
                    lap.update(
                        {
                            "s1_ms": source.get("s1_ms", 0),
                            "s2_ms": source.get("s2_ms", 0),
                            "s3_ms": source.get("s3_ms", 0),
                            "valid_flags": source.get("valid_flags", 0),
                            "valid": bool(source.get("valid_flags", 0) & 1),
                        }
                    )

    async def append_radio(self, role: str, text: str) -> None:
        async with self._lock:
            self.state.radio_log.append({"role": role, "text": text})
            self.state.radio_log = self.state.radio_log[-100:]

    async def append_event(self, event_type: str, payload: dict[str, Any]) -> None:
        queued: dict[str, Any]
        async with self._lock:
            now = time.time()
            event = {"time": now, "type": event_type, "payload": copy.deepcopy(payload)}
            self.state.events_log.append(event)
            self.state.events_log = self.state.events_log[-200:]
            queued = {
                "session_uid": int(self.state.session_uid),
                "track_id": int(self.state.track_id),
                "current_lap": int(self.state.current_lap),
                "event_type": str(event_type),
                "payload": copy.deepcopy(payload),
                "created_at": now,
            }
        try:
            self.event_queue.put_nowait(queued)
        except asyncio.QueueFull:
            # Preserve current race-control information rather than blocking the
            # UDP consumer. Drop the oldest queued event and keep the newest.
            try:
                self.event_queue.get_nowait()
                self.event_queue.task_done()
            except asyncio.QueueEmpty:
                pass
            self.event_queue.put_nowait(queued)

    async def add_feedback(self, category: str, text: str) -> None:
        async with self._lock:
            self.state.feedback.append(
                {"time": time.time(), "category": category, "text": text}
            )
            self.state.feedback = self.state.feedback[-50:]
