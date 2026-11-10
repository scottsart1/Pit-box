from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import re
import time
from collections import deque
from typing import Any

from . import rain
from .analysis import fmt_ms
from .brain import EngineerBrain, _plan_call_index, _radio_sentences, hold_material_change
from .config import settings
from .grounding import lap_counts, place_counts, unsupported
from .session_guard import session_key, session_scoped
from .setup_advisor import SetupAdvisor
from .state import StateStore
from .strategy import COVER_RANGE_BOUNDS_S, StrategyEngine
from .tools import TelemetryTools
from .tyre_limits import WEAR_CALL_PCT, WEAR_CALL_REARM_PCT, operational_limit_pct, safety_limit_pct
from .voice import NativeVoiceController

log = logging.getLogger(__name__)

# Starts the note a call carries when its template replaced the narration.
TEMPLATE_SPOKEN = "template spoken: "

# How often the race is looked at, and how often the queue is drained.
#
# Detection is deliberately faster than the old shared 0.35s loop: session
# packets carrying marshal zones and safety-car status arrive at 2 Hz, so
# 0.15s samples every one of them without burning CPU on duplicates. Delivery
# can be lazier because it blocks on speech anyway, and a tighter loop would
# only spin while the engineer talks.
DETECT_INTERVAL_S = 0.15
DELIVER_INTERVAL_S = 0.10

# How far ahead a marshal-zone yellow is worth calling. Roughly six seconds at
# racing speed: far enough to lift or move, near enough that it is still true
# when the driver arrives.
YELLOW_LOOKAHEAD_M = 600.0

# Fuel margin, in laps, below which the driver is told to save. The same line
# the fuel model draws between "save fuel" and "on target"
# (AnalysisEngine.compute_fuel_model). The warning used to fire below +0.3
# laps and drop the sign, so a deliberate light-fuel margin of +0.2 was read
# out as "short by 0.2 laps, lift and coast" every few minutes while every
# other path, the fuel model included, said the fuel was on target.
FUEL_SAVE_DELTA_LAPS = -0.1
# The margin the game reports wanders by tenths within a lap. A shortfall that
# has been called stays called until the margin is back to non-negative, a
# tenth clear of the line above, so a margin hovering around -0.1 is not
# re-announced every time it touches the line.
FUEL_RECOVERED_DELTA_LAPS = 0.0

# Race-control phases as the radio says them. The template used to read the
# internal value aloud: "Race control: vsc ending."
RACE_CONTROL_LABELS = {
    "green": "Green flag",
    "safety_car": "Safety car",
    "safety_car_ending": "Safety car in this lap",
    "vsc": "VSC",
    "vsc_ending": "VSC ending",
    "formation": "Formation lap",
    "formation_ending": "Formation lap ending",
}

# Power-unit components as they are said, not as they are stored: the old
# template read "MGUH" and "TC" aloud.
COMPONENT_LABELS = {
    "ice": "ICE", "mguk": "MGU-K", "mguh": "MGU-H",
    "es": "Energy store", "ce": "Control electronics", "tc": "Turbo",
}

# Repeat control at delivery. A call is compared with the last delivered call
# of its own type by what it says materially (see _material_signature), and
# only calls of different types are compared by their wording.
REPEAT_HORIZON_S = 120.0
# The same race-control phase announced twice this close together is the phase
# flapping between the safety-car event and the session packet, not news.
RACE_CONTROL_REPEAT_S = 20.0
# How many times a narrated call may go back to the queue because the race
# moved on while it was being worded, before it is dropped.
MAX_REQUEUES = 2

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
# LapData.result_status values after which the player is no longer racing:
# did not finish, disqualified, not classified, retired. Mirrors
# udp.RETIRED_RESULT_STATUS without importing the packet parser here.
OUT_OF_RACE_RESULT_STATUS = frozenset({4, 5, 6, 7})
FINISHED_RESULT_STATUS = 3
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
    "strategy": frozenset({"strategy_change", "compound_requirement", "driver_check", "pit_window_open"}),
    "pit": frozenset({"strategy_change", "compound_requirement", "driver_check", "pit_window_open"}),
    "corner": frozenset({"corner_coaching"}),
    "coaching": frozenset({"corner_coaching"}),
    "line": frozenset({"corner_coaching"}),
    "driving": frozenset({"corner_coaching"}),
    "weather": frozenset({"weather_crossover", "weather_stand_down", "weather_box_reminder"}),
    "rain": frozenset({"weather_crossover", "weather_stand_down", "weather_box_reminder"}),
    "rival": frozenset({"rival_pace", "rival_pitted", "rival_pitting", "undercut_threat"}),
    "rivals": frozenset({"rival_pace", "rival_pitted", "rival_pitting", "undercut_threat"}),
    "gap": frozenset({"rival_pace"}),
    "update": frozenset({"progress_update"}),
    "updates": frozenset({"progress_update"}),
    "progress": frozenset({"progress_update"}),
    # Brutal-mode pace calls. The toggle on DRIVE is the normal way to stop
    # them; "stop roasting me" over the radio works too.
    "roast": frozenset({"pace_roast"}),
    "roasting": frozenset({"pace_roast"}),
    "swearing": frozenset({"pace_roast"}),
    "brutal": frozenset({"pace_roast"}),
}
# Calls that concern safety, legality or the car being about to stop. A driver
# may silence a topic; they may not silence a penalty or a red flag.
NEVER_SUPPRESSED = frozenset(
    {
        "race_control", "safety_car_delta", "penalty", "penalty_service",
        "blue_flag", "engine_failure", "sc_restart", "race_start", "lap_deleted",
    }
)

# The damage components a component subject covers. A damage call is
# silenced by these only when everything new in it is that component.
DAMAGE_COMPONENT_SUBJECTS: dict[str, frozenset[str]] = {
    "gearbox": frozenset({"gearbox"}),
    "gear": frozenset({"gearbox"}),
    "engine": frozenset({"engine"}),
    "power unit": frozenset({"engine"}),
    "floor": frozenset({"floor", "diffuser"}),
    "wing": frozenset({"front_left_wing", "front_right_wing", "rear_wing"}),
}

FLASH, CRITICAL, IMPORTANT, ROUTINE = -1, 0, 1, 2

# Throttle above which the car is taken to be on a straight, for the strict
# speaking window. Part throttle through a fast sweep is not a straight.
# A car behind is called catching only inside this gap, at this rate a lap or
# faster on the lap-long trend the gap tool reads. Judgements: three seconds is
# DRS and dirty-air range plus a lap's error; two tenths is above lap noise.
RIVAL_CATCHING_RANGE_S = 3.0
RIVAL_CATCHING_RATE_S = 0.2
# How often the catching call reads the field's gap histories.
RIVAL_TREND_CHECK_INTERVAL_S = 5.0
# Stops off the player's tyre and stop number that open the pit window.
RIVAL_WINDOW_STOPS = 3
SAFE_TO_SPEAK_MIN_THROTTLE = 0.45

# Calls a red flag does not make stale: legality and the state of the car.
# Their detectors fire once (a penalty when the total rises, damage when it
# enters a new band), so a call dropped at the suspension was never made.
SURVIVES_SUSPENSION = frozenset(
    {"penalty", "penalty_service", "warning", "damage", "engine_failure"}
)

# Calls that must reach the driver while the fact is still true, and that are
# therefore never sent to the model for wording.
#
# Narration costs an LLM round trip (up to proactive_narration_timeout_s) before
# a single word is spoken. Measured over a real race, queue-to-spoken averaged
# 31.6s and peaked at 46.6s: a 45s virtual safety car was announced 4.5s after
# it had already ended. Worse than late, it was sometimes wrong — the model was
# handed a payload describing a return to green and announced "Red flag
# confirmed - stay out of the pit lane."
#
# For these events the deterministic template is not a fallback, it is the
# better answer: every fact is already in the payload, the wording is fixed and
# correct, and it can be spoken immediately. Interpretation still happens, as a
# normal CRITICAL call a few seconds later, where being slower costs nothing.
FLASH_EVENTS = frozenset(
    {
        "race_control",
        "sc_restart",
        "yellow_ahead",
        "blue_flag",
        "safety_car_delta",
    }
)
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
    "weather_stand_down": CRITICAL,
    "weather_box_reminder": CRITICAL,
    "compound_requirement": IMPORTANT,
    "undercut_threat": IMPORTANT,
    "rival_pitted": IMPORTANT,
    "rival_pitting": IMPORTANT,
    "pit_window_open": IMPORTANT,
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
    # Important rather than routine: a roast that waits out the routine
    # spacing interval arrives a lap late and lands as a non sequitur.
    "pace_roast": IMPORTANT,
    "progress_update": ROUTINE,
    "corner_coaching": ROUTINE,
}
# The weather call, as levels. A call is queued when the level rises or what
# it says changes, never on a timer, so a verdict that moves from "watching" to
# "box now" is always said, and an unchanged one never twice.
WEATHER_NONE, WEATHER_WATCH, WEATHER_ASK, WEATHER_STANDBY, WEATHER_BOX = range(5)
WEATHER_LEVEL_LABELS = ("none", "watch", "ask", "stand by", "box")
WET_TYRES = frozenset({"INTER", "WET"})
# Wet enough, below the slick crossover, that a driver on slicks should hear
# the conditions are being watched.
WEATHER_WATCH_WETNESS = 0.15
# Chance, summed over the forecast scenarios, that the surface passes the
# slick crossover within this many laps: rain coming is worth a heads-up.
WEATHER_HEADS_UP_CHANCE = 0.6
WEATHER_HEADS_UP_LAPS = 3
# On wet tyres, the heads-up that slicks are coming is given when the
# projected surface falls through the crossover within this many laps.
WEATHER_DRYING_HEADS_UP_LAPS = 5
# A box call that was heard and not acted on is said again only when the case
# for it has grown: half as much time again two laps on, or this much more
# water. At most this many box calls for one compound in one spell.
WEATHER_BOX_REPEAT_MIN_LAPS = 2
WEATHER_BOX_REPEAT_MARGIN_GROWTH = 1.5
WEATHER_BOX_REPEAT_WETNESS_RISE = 0.10
WEATHER_BOX_MAX_CALLS = 3
# A box call for the weather is said once more this far before the pit entry,
# as a fraction of the lap, on the lap it was made.
WEATHER_BOX_REMINDER_WINDOW = 0.12

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

# Brutal-mode pace calls. Spoken verbatim when narration is off or the model
# fails, and the register the model is asked to match when it is on. Each
# line carries the fact that earned it: the position for a bottom-five call,
# the lap delta for a slow lap. Selected by lap number so consecutive roasts
# differ and the same lap always produces the same line.
#
# Profanity is aimed at the driving. Nothing here is about who the driver is.
ROAST_LINES_BOTTOM_FIVE: tuple[str, ...] = (
    "Bitch, do you mind driving faster? You are P{position} of {cars}.",
    "P{position} of {cars}. That is the bottom five. Get your fucking foot down.",
    "You are P{position}. There are {behind} cars behind you and they are shit. Drive.",
    "Bottom five, P{position}. Stop pissing about and put a lap together.",
    "P{position} of {cars}. My nan could hold that position. Faster. Now.",
    "We are P{position}. If you are waiting for permission to push, this is it. Fucking go.",
)
ROAST_LINES_SLOW_LAP: tuple[str, ...] = (
    "Bitch, do you mind driving faster? That lap was {delta:.1f} slower than target.",
    "{delta:.1f} seconds off target. Where the fuck did that go? Hit {target}.",
    "That was shit. {delta:.1f} off the target of {target}. Sort it out this lap.",
    "{delta:.1f} slow. The car is fine, the tyres are fine, so it is you. Push.",
    "Target is {target}. You did {lap_time}. That is {delta:.1f} of pure laziness.",
    "Another {delta:.1f} down the drain. Stop driving like a bitch and hit {target}.",
)
ROAST_LINES_BOTH: tuple[str, ...] = (
    "Bitch, do you mind driving faster? P{position} and {delta:.1f} off target.",
    "P{position} of {cars} and {delta:.1f} slow. That is not a bad lap, that is a habit. Push.",
    "Bottom five and {delta:.1f} off {target}. Get your arse into the throttle.",
    "P{position}, {delta:.1f} down on target. Every car behind you is laughing. Fucking drive.",
)


# What the narrator is given for each call: the facts its template speaks and
# a few that support them. The queue record itself (deadlines, session keys,
# blocked reasons) is bookkeeping, and the embedded strategy plan carries some
# sixty keys (Monte Carlo quantiles, stint models, position probabilities,
# projected points) that a no-tool call allowed three numbers had to choose
# from. Nested values are reduced by the projections below.
_NARRATION_FACTS: dict[str, tuple[str, ...]] = {
    "progress_update": (
        "lap", "mode_profile", "progress", "target", "racing_line", "qualifying", "gaps", "strategy",
    ),
    "race_control": ("from", "to", "strategy", "prepared", "neutralisation", "red_flag_restart"),
    "sc_restart": ("phase", "position", "ers_pct", "front_temps_c"),
    "yellow_ahead": ("flag", "distance_ahead_m"),
    "driver_check": (
        "laps_to_window", "box_lap", "current_wear_pct", "degradation_s_per_lap",
        "projected_finish_wear_pct", "candidate_plans",
    ),
    "tyre_wear": ("wear_fl_fr_rl_rr", "strategy"),
    "lap_deleted": ("lap", "sector"),
    "quali_clear_air": ("lap", "clear_air_estimate", "closest_gap_s", "target", "ers_pct", "tyre_temps"),
    "blue_flag": ("flag", "position"),
    "penalty_service": ("drive_through", "stop_go", "serve_at_stop", "strategy"),
    "undercut_threat": ("driver", "position", "tyre", "gap_to_player_s", "instruction", "strategy"),
    "rival_pitted": ("driver", "position", "tyre", "gap_to_player_s", "strategy"),
    "rival_pitting": (
        "driver", "position", "tyre", "gap_to_player_s", "side", "seen_in_lane",
        "cover_holds_next_lap", "cover_margin_s", "cover_by", "clear_air_laps", "pace_needed_s", "also",
    ),
    "pit_window_open": ("cars", "compound", "first_lap", "last_lap", "box_lap"),
    "safety_car_delta": ("phase", "safety_car_delta_s", "threshold_s"),
    "corner_coaching": (
        "corner_no", "name", "average_loss_s", "cause", "instruction",
        "brake_point_delta_m", "apex_speed_delta_kph", "throttle_on_delta_m",
    ),
    "fuel_warning": ("fuel_laps_delta", "burn_kg_per_lap", "lift_and_coast_m_per_lap", "recommendation"),
    "damage": ("max_damage_pct", "new_faults", "escalated", "changed", "repairable", *DAMAGE_KEYS, *DAMAGE_FAULT_KEYS),
    "compound_requirement": ("laps_remaining", "used", "eligible", "strategy"),
    "weather_crossover": (
        "level_label", "direction", "current_compound", "awaiting_confirmation",
        "rain_15_pct", "wetness", "trend", "compound", "box_lap", "worth_stopping",
        "ask_driver", "driver_question", "reason", "forecast",
    ),
    "weather_stand_down": ("current_compound", "reason", "wetness"),
    "weather_box_reminder": ("compound",),
    "penalty": ("penalties_s", "added_s", "total_s"),
    "warning": ("corner_cutting_warnings",),
    "race_start": ("grid_position", "clean_side", "ers_pct", "front_temps_c"),
    "energy_low": ("ers_pct", "regulations_2026"),
    "component_wear": ("component", "wear_pct", "components"),
    "engine_failure": ("blown", "seized"),
    "rival_pace": ("driver", "gap_to_player_s", "closing", "gap_change_s_per_lap", "laps_to_contact"),
    "pace_roast": (
        "lap", "reason", "position", "active_cars", "bottom_five", "delta_to_target_s", "target", "lap_time",
    ),
}
# A pit plan as a call speaks it: the instruction (already worded with the
# radio's stop guards), the stop itself, why, and what would change it.
_PLAN_FACTS = (
    "instruction", "box_lap", "fit_compound", "stops_remaining",
    "stop_required_reason", "tyre_reason", "call_changes_if",
)
_PROGRESS_FACTS = (
    "lap_num", "lap_time", "delta_to_target_s", "target", "tyre_compound", "tyre_age",
    "max_wear_pct", "deg_s_per_lap", "fuel_delta_laps", "top_opportunity", "line_opportunity",
)
_QUALIFYING_FACTS = (
    "session_best", "player_best", "theoretical_best", "target", "required_delta_s", "top_best_laps",
)
_TARGET_FACTS = ("target", "target_lap", "theoretical", "basis")


def _plan_facts(plan: Any, state: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, dict) or not plan:
        return {}
    facts = {key: plan[key] for key in _PLAN_FACTS if plan.get(key) not in (None, "", [])}
    confidence = (state.get("strategy", {}) or {}).get("confidence")
    if facts and confidence:
        facts["plan_confidence"] = confidence
    return facts


def _opportunity_text(value: Any) -> Any:
    """A coaching opportunity as its instruction: the finding dict is not radio."""
    if isinstance(value, dict):
        return str(value.get("instruction") or "").strip() or None
    return value


def _narration_value(key: str, value: Any, state: dict[str, Any]) -> Any:
    if key == "strategy":
        return _plan_facts(value, state)
    if not isinstance(value, (dict, list)):
        return value
    if key == "progress" and isinstance(value, dict):
        facts = {name: value.get(name) for name in _PROGRESS_FACTS}
        facts["top_opportunity"] = _opportunity_text(facts.get("top_opportunity"))
        facts["line_opportunity"] = _opportunity_text(facts.get("line_opportunity"))
        return facts
    if key == "racing_line" and isinstance(value, dict):
        return {"top_opportunity": _opportunity_text(value.get("top_opportunity"))}
    if key in {"qualifying", "target"} and isinstance(value, dict):
        names = _QUALIFYING_FACTS if "session_best" in value or "player_best" in value else _TARGET_FACTS
        facts = {name: value.get(name) for name in names}
        if isinstance(facts.get("top_best_laps"), list):
            facts["top_best_laps"] = [
                {name: item.get(name) for name in ("name", "best_lap", "delta_to_best_s")}
                for item in facts["top_best_laps"][:3]
                if isinstance(item, dict)
            ]
        return facts
    if key == "neutralisation" and isinstance(value, dict):
        return {name: value.get(name) for name in ("phase", "effective_pit_loss_s")}
    if key == "red_flag_restart" and isinstance(value, dict):
        return {name: value.get(name) for name in ("active", "instruction")}
    if key == "forecast" and isinstance(value, list):
        return [
            {name: sample.get(name) for name in ("time_offset_min", "rain_pct")}
            for sample in value[:3]
            if isinstance(sample, dict)
        ]
    if key == "components" and isinstance(value, list):
        return [
            {name: item.get(name) for name in ("component", "wear_pct")}
            for item in value
            if isinstance(item, dict)
        ]
    return value


def _rounded(value: Any) -> Any:
    """Floats as the radio would say them, not as the model computed them."""
    if isinstance(value, bool) or not isinstance(value, (float, dict, list)):
        return value
    if isinstance(value, float):
        return round(value, 3) if abs(value) < 10 else round(value, 2)
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items() if item is not None}
    return [_rounded(item) for item in value]


def narration_facts(event: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """The facts an unsolicited call is narrated from, per event type.

    Every fact the call's template speaks is kept, so a narrated call can say
    what the template would; the queue bookkeeping and the bulk of the
    strategy plan are left out. A type without an entry keeps only its plain
    values. A damage call keeps only the components that are damaged.
    """
    kind = str(event.get("type") or "")
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    if kind == "strategy_change":
        facts: dict[str, Any] = _plan_facts(payload, state)
        if payload.get("hold_released_reason"):
            facts["hold_released_reason"] = payload["hold_released_reason"]
    elif kind in _NARRATION_FACTS:
        facts = {
            key: _narration_value(key, payload[key], state)
            for key in _NARRATION_FACTS[kind]
            if key in payload
        }
        if kind == "damage":
            facts = {
                key: value
                for key, value in facts.items()
                if key not in DAMAGE_KEYS + DAMAGE_FAULT_KEYS or value
            }
    else:
        facts = {key: value for key, value in payload.items() if not isinstance(value, (dict, list))}
    # Temperatures are given in the driver's unit, under a key that names it.
    # Raw Celsius under "tyre_temps" let a driver who asked for Fahrenheit hear
    # "fronts at 104" before a push lap.
    fahrenheit = str(state.get("temperature_unit", "c")).lower() == "f"
    for key, stem in (("tyre_temps", "tyre_temps"), ("front_temps_c", "front_temps")):
        values = facts.pop(key, None)
        if values is None:
            continue
        if fahrenheit and isinstance(values, list):
            facts[f"{stem}_f"] = [
                value * 9.0 / 5.0 + 32.0 if isinstance(value, (int, float)) else value
                for value in values
            ]
        else:
            facts[f"{stem}_c"] = values
    return {"type": kind, "critical": bool(event.get("critical")), "facts": _rounded(facts)}


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
        self._deliver_task: asyncio.Task[None] | None = None
        self._session_uid = 0
        self._session_context = (0, 0, 0, 0)
        self._last_spoken_at = 0.0
        self._last_lap_queued = 0
        self._last_learning_lap = 0
        self._last_safety_car = "none"
        self._last_race_control_phase = "green"
        # (lap, zone) pairs already called, so a persisting yellow is
        # announced once rather than on every detection pass.
        self._yellow_called: set[tuple[int, int]] = set()
        # Where the weather call last stood when it was queued (level,
        # compound, box lap, direction, fitted tyre), the box calls the driver
        # actually heard this spell, and the laps a pit-entry reminder was said.
        self._last_weather_call: dict[str, Any] = {"level": WEATHER_NONE}
        self._weather_box_heard: list[dict[str, Any]] = []
        self._weather_reminded: set[tuple[int, str]] = set()
        self._last_penalties = 0
        self._last_warnings = 0
        self._last_damage_signature = ""
        # Damage is reported once per 20% band of each component and once per
        # new fault, rather than on every change to any component.
        self._reported_damage_band = 0
        self._reported_damage_bands: dict[str, int] = {}
        self._reported_damage_faults: set[str] = set()
        self._last_strategy_signature = ""
        self._last_strategy_inputs_signature = ""
        self._last_strategy_continuous_signature = ""
        self._last_recompute_at = float("-inf")
        self._recompute_times: deque[float] = deque()
        self._last_corner_signature = ""
        self._last_pit_stops: dict[int, int] = {}
        self._last_lap_invalid = False
        self._last_blue_flag = False
        self._last_unserved_penalties = (0, 0)
        self._last_quali_release_lap = 0
        # When the catching call last read the field's gap histories.
        self._rival_trend_checked_at = float("-inf")
        # Rival stops: whether each car was in the lane at the last pass, the
        # stops already seen there or called, and what each car stopped from.
        self._rival_in_pit: dict[int, bool] = {}
        self._rival_stops_seen: set[tuple[int, int]] = set()
        self._rival_stop_entry: dict[int, tuple[str, int]] = {}
        self._window_stops: dict[tuple[str, int], dict[int, int]] = {}
        self._window_called: set[tuple[str, int]] = set()
        # Per-component wear band already alerted, so each PU element escalates
        # independently rather than one shared band hiding the others.
        self._component_alert_bands: dict[str, int] = {}
        self._engine_failure_alerted = False
        self._energy_alert_lap = 0
        self._race_start_done = False
        # Last analysed lap that earned a brutal-mode roast, so each lap is
        # judged once however many detection passes see it.
        self._last_roast_lap = 0
        # Stint key -> the box lap the check was asked about. A dict rather
        # than a set so the check re-arms when the window MOVES: in a real race
        # the first (wrong) window was lap 5, the check fired at lap 2, and
        # when the real window became lap 12 the driver was never asked again.
        self._driver_check_stints: dict[tuple[str, int], int] = {}
        # Delivery-time guard against near-identical consecutive calls: three
        # differently-worded "Practice complete, P8" calls went out inside 30
        # seconds because separate event types converged on the same content.
        self._last_spoken_text = ""
        self._last_spoken_type = ""
        # Event type -> (material signature, monotonic time) of the last call
        # of that type the driver actually heard.
        self._delivered_signatures: dict[str, tuple[Any, float]] = {}
        self._cooldowns: dict[str, float] = {}
        self._safe_since = 0.0
        self._wide_safe_since = 0.0
        # Refreshed each detection pass so _enqueue can honour them.
        self._standing_instructions: list[Any] = []

    async def start(self) -> None:
        # Two tasks, deliberately. Detection must keep running while the
        # engineer is mid-sentence; see _run_detect for why.
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._run_detect(), name="pitwall-proactive-detect"
            )
        if self._deliver_task is None or self._deliver_task.done():
            self._deliver_task = asyncio.create_task(
                self._run_deliver(), name="pitwall-proactive-deliver"
            )

    async def stop(self) -> None:
        for name in ("_task", "_deliver_task"):
            task = getattr(self, name)
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            setattr(self, name, None)

    async def configure(self, enabled: bool, cadence_laps: int) -> dict[str, Any]:
        cadence = max(1, min(10, int(cadence_laps)))
        def apply(state):  # type: ignore[no-untyped-def]
            state.proactive["enabled"] = bool(enabled)
            state.proactive["cadence_laps"] = cadence
            state.proactive["next_due_lap"] = max(cadence, self._last_lap_queued + cadence)
        await self.store.mutate(apply)
        if not enabled:
            self._drop_pending("proactive radio disabled")
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
        state = await self.store.snapshot_radio()
        if not state.get("connected") or state.get("game_paused"):
            return {
                "ok": False,
                "reason": "Resume a live session before testing proactive radio.",
            }
        session_uid = int(state.get("session_uid", 0))
        if session_uid != self._session_uid:
            await self._reset_for_session(session_uid)
            state = await self.store.snapshot_radio()
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
    ) -> bool:
        """Queue a call. Returns whether it was queued.

        A detector that records "already reported" state must only do so when
        this returns True: a call refused by the cooldown leaves no trace, so
        recording it anyway marks news as told that the driver never heard.
        """
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
                return True
        if now_mono - self._cooldowns.get(event_type, -1e9) < cooldown_s:
            return False
        if self.is_suppressed(event_type, self._standing_instructions, payload):
            return False
        self._cooldowns[event_type] = now_mono
        # Race control is deliberately NOT coalesced. Every other call here
        # reports a level that a later reading replaces, so the newest is the
        # only one worth speaking. A flag change is not a level, it is an
        # event: "safety car deployed" and "safety car ending" are two separate
        # things the driver must hear, in order. Coalescing them discarded the
        # deployment and spoke only the ending, so twice in one race the driver
        # was told the safety car had finished without ever being told it
        # started. Blue flags are the same: each one is a different car
        # arriving, not a restatement of the last.
        coalesced = {
            "progress_update", "corner_coaching", "weather_crossover",
            "fuel_warning", "tyre_wear", "damage", "strategy_change", "compound_requirement",
            "quali_clear_air", "penalty_service", "undercut_threat",
            "safety_car_delta", "sc_restart", "energy_low", "component_wear",
            # A rival stop that has been superseded by a later one is no longer
            # news. Leaving these uncoalesced filled the queue with stale stops
            # that then aged out unspoken.
            "rival_pace", "engine_failure", "rival_pitted",
            "driver_check", "pace_roast",
            "weather_stand_down", "weather_box_reminder",
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
        is_flash = event_type in FLASH_EVENTS or self._weather_flash(event_type, payload)
        priority = (
            FLASH
            if is_flash
            else CRITICAL
            if critical
            else EVENT_PRIORITY.get(event_type, ROUTINE)
        )
        event = {
            "type": event_type,
            "critical": critical or is_flash,
            "flash": is_flash,
            "priority": priority,
            "payload": payload,
            "queued_at": now,
            "deliver_by": now + (8.0 if critical else settings.proactive_delivery_deadline_s),
            "expires_at": now + (expires_s if expires_s is not None else (45.0 if critical else 180.0)),
            "session_uid": self._session_uid,
            "session_context": self._session_context,
            "blocked_reasons": [],
        }
        if len(self.pending) == self.pending.maxlen:
            dropped = self.pending.pop() if critical else self.pending.popleft()
            dropped["delivery_outcome"] = "queue_full"
            dropped.setdefault("blocked_reasons", []).append("queue full")
            self._discarded.append(dropped)
        self.pending.appendleft(event) if critical else self.pending.append(event)
        return True

    async def _reset_for_session(self, session_uid: int) -> None:
        self._session_uid = int(session_uid)
        self._session_context = session_key(await self.store.peek("session_uid", "restart_epoch", "timeline_epoch", "session_generation"))
        self.pending.clear()
        self._discarded.clear()
        self._last_spoken_at = 0.0
        self._last_lap_queued = 0
        self._last_learning_lap = 0
        self._last_safety_car = "none"
        self._last_race_control_phase = "green"
        # (lap, zone) pairs already called, so a persisting yellow is
        # announced once rather than on every detection pass.
        self._yellow_called: set[tuple[int, int]] = set()
        self._last_weather_call = {"level": WEATHER_NONE}
        self._weather_box_heard = []
        self._weather_reminded = set()
        self._last_penalties = self._last_warnings = 0
        self._last_damage_signature = self._last_strategy_signature = ""
        self._reported_damage_band = 0
        self._reported_damage_bands = {}
        self._reported_damage_faults = set()
        self._last_strategy_inputs_signature = self._last_corner_signature = ""
        self._last_strategy_continuous_signature = ""
        self._last_recompute_at = float("-inf")
        self._recompute_times = deque()
        self._last_pit_stops = {}
        self._last_lap_invalid = False
        self._last_blue_flag = False
        self._last_unserved_penalties = (0, 0)
        self._last_quali_release_lap = 0
        self._rival_trend_checked_at = float("-inf")
        self._rival_in_pit = {}
        self._rival_stops_seen = set()
        self._rival_stop_entry = {}
        self._window_stops = {}
        self._window_called = set()
        self._component_alert_bands = {}
        self._engine_failure_alerted = False
        self._energy_alert_lap = 0
        self._race_start_done = False
        self._last_roast_lap = 0
        self._driver_check_stints = {}
        self._last_spoken_text = self._last_spoken_type = ""
        self._delivered_signatures = {}
        self._cooldowns.clear()
        self._safe_since = 0.0
        self._wide_safe_since = 0.0
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
    def _metres_to_zone(zone: dict[str, Any], state: dict[str, Any]) -> float:
        """Distance along the lap from the car to a marshal zone's start."""
        track_length = float(state.get("track_length_m", 0) or 0)
        lap_distance = float(state.get("lap_distance_m", 0) or 0)
        ahead = float(zone.get("start_m", 0.0)) - lap_distance
        if ahead < 0 and track_length:
            ahead += track_length
        return ahead

    @staticmethod
    def _flagged_zone_ahead(
        payload: dict[str, Any], state: dict[str, Any]
    ) -> tuple[str, float] | None:
        """(flag, metres) for a called zone the car has not yet reached.

        None once the car has reached the zone, the zone has gone green, or the
        track has been neutralised. A yellow is called about six seconds out,
        and a call queued behind a narrated one could otherwise be spoken after
        the car was already in the zone, with the distance measured at
        detection.
        """
        if str(state.get("race_control_phase", "green")) != "green":
            return None
        zone = next(
            (
                item
                for item in state.get("marshal_zones", []) or []
                if int(item.get("index", -1)) == int(payload.get("zone", -2))
            ),
            None,
        )
        if zone is None or str(zone.get("flag", "none")) not in {"yellow", "red"}:
            return None
        ahead = ProactiveEngineer._metres_to_zone(zone, state)
        if not 0.0 <= ahead <= YELLOW_LOOKAHEAD_M:
            return None
        return str(zone.get("flag")), round(ahead, 1)

    def _detect_marshal_yellow(self, state: dict[str, Any]) -> None:
        """Call a yellow in a zone the driver is about to reach.

        Marshal zones have always been parsed and stored, and were read by
        exactly two things: the dashboard, and a tool the model could ask for
        on request. Nothing ever volunteered one, so in the product's whole
        history it has never once told a driver about a yellow flag — on a race
        where 164 of 717 recorded laps carried a flag context.

        Only zones AHEAD of the car matter, and only within a braking-and-
        reacting distance: a yellow on the far side of the lap is not news yet,
        and by the time the driver gets there it may be green again. The zone
        start is a fraction of the lap converted to metres upstream, so the
        wrap-around is handled the same way the on-demand tool already does it.
        """
        if str(state.get("race_control_phase", "green")) != "green":
            # Under a safety car or VSC the whole track is neutralised and the
            # driver has already been told. Zone-by-zone yellows would be noise.
            return
        zones = list(state.get("marshal_zones", []) or [])
        if not zones:
            return
        current_lap = int(state.get("current_lap", 0))

        nearest: dict[str, Any] | None = None
        for zone in zones:
            flag = str(zone.get("flag", "none"))
            if flag not in {"yellow", "red"}:
                continue
            ahead = self._metres_to_zone(zone, state)
            if 0.0 <= ahead <= YELLOW_LOOKAHEAD_M and (
                nearest is None or ahead < nearest["distance_ahead_m"]
            ):
                nearest = {
                    "zone": int(zone.get("index", -1)),
                    "flag": flag,
                    "distance_ahead_m": round(ahead, 1),
                }

        if nearest is None:
            return
        # One call per zone per lap. A yellow that persists is still the same
        # yellow, and repeating it every 0.15s pass would be unusable.
        key = (current_lap, nearest["zone"])
        if key in self._yellow_called:
            return
        self._yellow_called.add(key)
        self._enqueue(
            "yellow_ahead",
            {
                "flag": nearest["flag"],
                "zone": nearest["zone"],
                "distance_ahead_m": nearest["distance_ahead_m"],
                "lap": current_lap,
            },
            critical=True,
            cooldown_s=0.0,
            # Short-lived by design: a yellow the driver has already driven
            # through is not worth saying.
            expires_s=12.0,
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

    @staticmethod
    def _weather_call(state: dict[str, Any]) -> dict[str, Any]:
        """Where the weather call stands now: a level, and what it is about.

        Read from the crossover the strategy engine published, so the radio
        and the Strategy page cannot disagree about it. ``direction`` is
        "to_wet", "to_dry" or "wet_to_wet"; ``fitted`` is the tyre on the car.
        """
        crossover = (state.get("strategy", {}) or {}).get("weather_crossover") or {}
        fitted = str((state.get("tyre", {}) or {}).get("compound") or "UNKNOWN").upper()
        on_wets = fitted in WET_TYRES
        compound = str(crossover.get("compound") or "").upper()
        call: dict[str, Any] = {
            "level": WEATHER_NONE, "compound": "", "box_lap": None,
            "direction": "to_dry" if on_wets else "to_wet", "fitted": fitted,
        }
        if not crossover:
            return call
        if compound in WET_TYRES and not on_wets:
            call["direction"] = "to_wet"
        elif compound and compound not in WET_TYRES and on_wets:
            call["direction"] = "to_dry"
        elif compound in WET_TYRES and on_wets and compound != fitted:
            call["direction"] = "wet_to_wet"
        box_lap = crossover.get("box_lap")
        if (
            crossover.get("worth_stopping") and box_lap is not None
            and compound and compound != fitted
        ):
            current_lap = int(state.get("current_lap", 0) or 0)
            call.update(
                level=WEATHER_BOX if int(box_lap) <= current_lap else WEATHER_STANDBY,
                compound=compound, box_lap=int(box_lap),
            )
            return call
        if crossover.get("ask_driver"):
            call.update(level=WEATHER_ASK, compound=compound)
            return call
        wetness = float(crossover.get("wetness", 0.0) or 0.0)
        if on_wets:
            # On wet tyres the news is the dry line coming, not the rain. A
            # surface still under the slick line while it is getting wetter
            # (just after a stop for inters) is not a dry line coming.
            trajectory = [float(value) for value in crossover.get("trajectory") or []]
            ahead = trajectory[:WEATHER_DRYING_HEADS_UP_LAPS]
            drying_soon = (
                len(ahead) >= 2 and ahead[-1] < ahead[0]
                and any(value < rain.WETNESS_SLICK_INTER for value in ahead)
            )
            wetting = str(crossover.get("trend") or "") == "wetting"
            if (wetness < rain.WETNESS_SLICK_INTER and not wetting) or drying_soon:
                call.update(level=WEATHER_WATCH, direction="to_dry")
            return call
        sky_wet = rain.equilibrium_wetness(str(state.get("weather", ""))) > 0
        coming = sum(
            float(item.get("probability", 0.0) or 0.0)
            for item in crossover.get("scenarios") or []
            if any(
                float(value) >= rain.WETNESS_SLICK_INTER
                for value in (item.get("trajectory") or [])[:WEATHER_HEADS_UP_LAPS]
            )
        )
        if wetness >= WEATHER_WATCH_WETNESS or sky_wet or coming >= WEATHER_HEADS_UP_CHANCE:
            call.update(level=WEATHER_WATCH, direction="to_wet")
        return call

    @staticmethod
    def _weather_call_carries(recommended: dict[str, Any], state: dict[str, Any]) -> bool:
        """Whether the weather call already says this stop, lap and tyre."""
        call = ProactiveEngineer._weather_call(state)
        return (
            call["level"] >= WEATHER_STANDBY
            and call["compound"] == str(recommended.get("fit_compound") or "").upper()
            and call["box_lap"] == recommended.get("box_lap")
        )

    @staticmethod
    def _weather_flash(event_type: str, payload: dict[str, Any] | None) -> bool:
        """A weather call that names the stop now: spoken from its template at once."""
        if event_type in {"weather_stand_down", "weather_box_reminder"}:
            return True
        return event_type == "weather_crossover" and (payload or {}).get("level") == WEATHER_BOX

    def _weather_box_worth_repeating(self, call: dict[str, Any], crossover: dict[str, Any], lap: int) -> bool:
        """Whether a box call already heard this spell has earned saying again."""
        heard = [item for item in self._weather_box_heard if item["compound"] == call["compound"]]
        if not heard:
            return True
        if len(heard) >= WEATHER_BOX_MAX_CALLS:
            return False
        last = heard[-1]
        margin = float(crossover.get("payback_s") or crossover.get("margin_s") or 0.0)
        wetness = float(crossover.get("wetness", 0.0) or 0.0)
        return (
            lap - int(last["lap"]) >= WEATHER_BOX_REPEAT_MIN_LAPS
            and margin >= float(last["margin"]) * WEATHER_BOX_REPEAT_MARGIN_GROWTH
        ) or wetness >= float(last["wetness"]) + WEATHER_BOX_REPEAT_WETNESS_RISE

    def _detect_weather(self, state: dict[str, Any]) -> None:
        """Say the weather call once per change of what it says.

        The old trigger was a one-shot latch on "the track is wet", so a
        verdict that moved from "wet, staying out" to "box now" never raised a
        weather call at all, and a withdrawn stop was never stood down.
        """
        if int(state.get("pit_status", 0) or 0) != 0 or bool(state.get("pit_lane_timer_active")):
            # The driver is making a stop. Until the car is out, the tyre on
            # it and the plan are both in flux: a stand-down read off the old
            # tyre ("Cancel the stop. Stay out on the mediums.") or a stand-by
            # for the stop being made is wrong. Compared afresh at the exit.
            return
        crossover = (state.get("strategy", {}) or {}).get("weather_crossover") or {}
        call = self._weather_call(state)
        last = self._last_weather_call
        level, last_level = int(call["level"]), int(last.get("level", WEATHER_NONE))
        current_lap = int(state.get("current_lap", 0) or 0)
        fitted_changed = bool(last.get("fitted")) and call["fitted"] != last.get("fitted")
        if fitted_changed or level < WEATHER_STANDBY:
            # A stop was made, or the spell eased below a stop: the next box
            # call is a new one.
            self._weather_box_heard = []

        stand_down = (
            last_level >= WEATHER_STANDBY > level and not fitted_changed
        )
        news = False
        if level > last_level:
            news = True
        elif level == last_level and level >= WEATHER_STANDBY:
            if call["compound"] != last.get("compound"):
                news = True
            elif level == WEATHER_STANDBY:
                old = last.get("box_lap")
                news = old is None or call["box_lap"] < int(old) or call["box_lap"] - int(old) >= 2
        elif level == last_level and level in {WEATHER_WATCH, WEATHER_ASK}:
            news = call["direction"] != last.get("direction")
        if news and level == WEATHER_BOX:
            news = self._weather_box_worth_repeating(call, crossover, current_lap)

        payload_common = {
            "level": level,
            "level_label": WEATHER_LEVEL_LABELS[level],
            "direction": call["direction"],
            "current_compound": call["fitted"],
            "wetness": crossover.get("wetness"),
            "reason": crossover.get("reason"),
        }
        if stand_down:
            queued = self._enqueue(
                "weather_stand_down", payload_common,
                critical=True, cooldown_s=0.0, expires_s=30.0,
            )
            if queued:
                self._last_weather_call = dict(call)
        elif news:
            queued = self._enqueue(
                "weather_crossover",
                {
                    **payload_common,
                    "rain_15_pct": state.get("rain_peak_15_pct", state.get("rain_next_15_pct")),
                    "trend": crossover.get("trend"),
                    "compound": call["compound"] or crossover.get("compound"),
                    "box_lap": call["box_lap"],
                    "worth_stopping": crossover.get("worth_stopping"),
                    "awaiting_confirmation": bool(crossover.get("awaiting_confirmation")),
                    "ask_driver": crossover.get("ask_driver"),
                    "driver_question": crossover.get("driver_question"),
                    "margin_s": crossover.get("payback_s", crossover.get("margin_s")),
                    "forecast": state.get("weather_forecast", []),
                },
                critical=True,
                cooldown_s=0.0,
            )
            if queued:
                self._last_weather_call = dict(call)
        elif level < last_level or fitted_changed:
            # Eased without anything to say: remember it, so the next rise
            # is news again.
            self._last_weather_call = dict(call)
        self._detect_weather_box_reminder(state, call)

    def _detect_weather_box_reminder(self, state: dict[str, Any], call: dict[str, Any]) -> None:
        """"Box, box" once, just before the pit entry, on the lap the box call was heard."""
        if call["level"] != WEATHER_BOX or int(state.get("pit_status", 0) or 0):
            return
        current_lap = int(state.get("current_lap", 0) or 0)
        heard = [item for item in self._weather_box_heard if item["compound"] == call["compound"]]
        if not heard or int(heard[-1]["lap"]) != current_lap:
            return
        key = (current_lap, call["compound"])
        if key in self._weather_reminded:
            return
        track_length = float(state.get("track_length_m", 0) or 0)
        if track_length <= 100:
            return
        fraction = float(state.get("lap_distance_m", 0) or 0) / track_length
        neutralisation = (state.get("strategy", {}) or {}).get("neutralisation", {}) or {}
        entry = float(neutralisation.get("pit_entry_fraction") or 0.94)
        if not entry - WEATHER_BOX_REMINDER_WINDOW <= fraction < entry:
            return
        if self._enqueue(
            "weather_box_reminder",
            {"compound": call["compound"], "lap": current_lap},
            critical=True, cooldown_s=0.0, expires_s=8.0,
        ):
            self._weather_reminded.add(key)

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
        #
        # Every component that crossed a band in this pass goes into ONE call.
        # One call per component shared a cooldown, so on an already-worn
        # power unit only the first was queued while all of them were marked
        # as told; and the bands are recorded only once the call is queued, so
        # a crossing held back by the cooldown is called when it ends.
        crossed: list[tuple[str, float, int]] = []
        for key, raw in wear.items():
            value = float(raw)
            band = (int(value) // 10) * 10
            if value >= 70.0 and band > self._component_alert_bands.get(key, 0):
                crossed.append((str(key), value, band))
        if not crossed:
            return
        crossed.sort(key=lambda item: item[1], reverse=True)
        worst_key, worst_value, _ = crossed[0]
        queued = self._enqueue(
            "component_wear",
            {
                "component": worst_key,
                "wear_pct": round(worst_value, 1),
                "components": [
                    {"component": key, "wear_pct": round(value, 1), "band": band}
                    for key, value, band in crossed
                ],
                "all": wear,
            },
            critical=worst_value >= 90.0,
            cooldown_s=120.0,
            expires_s=120.0,
        )
        if queued:
            for key, _value, band in crossed:
                self._component_alert_bands[key] = band

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

    async def _detect_rival_pace(self, state: dict[str, Any]) -> None:
        """Call a car behind that is catching, on the trend the gap tool reads.

        A short wall-clock sample made one car "catching" on the radio and
        "dropping back" in the answer to "is he catching?". The call reads the
        same lap-long trend, with its conflict check, and says how soon.
        """
        if state.get("mode_profile") not in {"race", "sprint"}:
            return
        player_idx = int(state.get("player_car_index", -1))
        close = [
            int(driver.get("car_idx", -1)) for driver in state.get("drivers", [])
            if int(driver.get("car_idx", -1)) != player_idx
            and driver.get("gap_to_player_s") is not None
            and 0 < float(driver["gap_to_player_s"]) <= RIVAL_CATCHING_RANGE_S
        ]
        now = time.monotonic()
        if not close or now - self._rival_trend_checked_at < RIVAL_TREND_CHECK_INTERVAL_S:
            return
        self._rival_trend_checked_at = now
        histories = await self.store.gap_histories(close)
        names = {int(driver.get("car_idx", -1)): driver for driver in state.get("drivers", [])}
        for idx in close:
            driver = {**names[idx], "gap_history": histories.get(idx, [])}
            trend = TelemetryTools._gap_trend(driver)
            rate = trend.get("gap_change_s_per_lap")
            if (
                trend.get("trend") != "closing" or rate is None
                or float(rate) > -RIVAL_CATCHING_RATE_S or trend.get("trend_conflict")
            ):
                continue
            self._enqueue(
                "rival_pace",
                {
                    "driver": driver.get("name"),
                    "gap_to_player_s": round(float(driver["gap_to_player_s"]), 2),
                    "closing": True,
                    "gap_change_s_per_lap": rate,
                    "laps_to_contact": trend.get("laps_to_contact"),
                },
                cooldown_s=30.0,
                expires_s=35.0,
            )

    @staticmethod
    def _rival_relevance(driver: dict[str, Any], state: dict[str, Any], cover: dict[str, Any] | None) -> int:
        """How much a rival's stop matters to this race: 2 or more is radio.

        3 within two places; 2 inside the range a fresh set can bridge, ahead
        or behind, or named in a standing instruction ("tell me when Norris
        stops"); 1 for the team-mate.
        """
        position = int(driver.get("position", 0) or 0)
        player_position = int(state.get("player_position", 0) or 0)
        if not player_position:
            player_idx = int(state.get("player_car_index", -1))
            player_position = next((
                int(item.get("position", 0) or 0) for item in state.get("drivers", [])
                if int(item.get("car_idx", -1)) == player_idx
            ), 0)
        if position and player_position and abs(position - player_position) <= 2:
            return 3
        score = 0
        gap = driver.get("gap_to_player_s")
        reach = float((cover or {}).get("cover_range_s") or COVER_RANGE_BOUNDS_S[0])
        if gap is not None and abs(float(gap)) <= reach:
            score = 2
        name = str(driver.get("name") or "").strip().lower()
        if name and any(name in str(item).lower() for item in state.get("standing_instructions", []) or []):
            score = max(score, 2)
        if driver.get("is_teammate"):
            score = max(score, 1)
        return score

    async def _cover_for(self, car_idx: int) -> dict[str, Any] | None:
        evaluate = getattr(self.strategy, "evaluate_cover", None)
        if evaluate is None:
            return None
        try:
            cover = await evaluate(car_idx)
        except Exception:  # noqa: BLE001 - a call without the arithmetic still names the car
            return None
        return cover if isinstance(cover, dict) and cover.get("available") else None

    async def _detect_rival_stops(self, state: dict[str, Any]) -> None:
        """Call the stop that matters, at pit entry, once.

        Stops were called from the stop counter, a lap late, for every car,
        and two stops in one pass kept the higher car index whichever was
        nearer. The lane is watched instead; the most relevant car is named,
        the others relevant with it are listed, and the rest are left to the
        Strategy page. The counter still catches a stop the lane never showed.
        """
        player_idx = int(state.get("player_car_index", -1))
        racing = state.get("mode_profile") in {"race", "sprint"}
        drivers = [d for d in state.get("drivers", []) if int(d.get("car_idx", -1)) != player_idx]
        player = next((d for d in state.get("drivers", []) if int(d.get("car_idx", -1)) == player_idx), {})
        candidates: list[tuple[dict[str, Any], bool]] = []
        for driver in drivers:
            idx = int(driver.get("car_idx", -1))
            stops = int(driver.get("pit_stops", 0) or 0)
            in_pit = bool(driver.get("pit_lane_timer_active")) or int(driver.get("pit_status", 0) or 0) != 0
            was = self._rival_in_pit.get(idx)
            self._rival_in_pit[idx] = in_pit
            if was is False and in_pit:
                # The counter may move at entry or at exit; either way this
                # stop has been seen.
                self._rival_stops_seen.update({(idx, stops), (idx, stops + 1)})
                self._rival_stop_entry[idx] = (str(driver.get("tyre_compound") or "").upper(),
                                               int(state.get("current_lap", 0) or 0))
                candidates.append((driver, True))
            elif was and not in_pit:
                self._note_window_stop(state, driver, player)
            previous = self._last_pit_stops.get(idx) if self._last_pit_stops else None
            if previous is not None and stops > previous and (idx, stops) not in self._rival_stops_seen:
                self._rival_stops_seen.add((idx, stops))
                candidates.append((driver, False))
        # In the lane the driver's own stop is happening: "box next lap to
        # cover him" is moot, and the stops stay seen, not saved for later.
        in_lane = int(state.get("pit_status", 0) or 0) != 0 or bool(state.get("pit_lane_timer_active"))
        if not racing or not candidates or in_lane:
            return
        ranked = []
        for driver, seen_in_lane in candidates:
            cover = await self._cover_for(int(driver.get("car_idx", -1)))
            relevance = self._rival_relevance(driver, state, cover)
            gap = driver.get("gap_to_player_s")
            ranked.append((relevance, -abs(float(gap)) if gap is not None else -1e9, driver, cover, seen_in_lane))
        relevant = sorted((item for item in ranked if item[0] >= 2), key=lambda item: item[:2], reverse=True)
        if not relevant:
            return
        _, _, driver, cover, seen_in_lane = relevant[0]
        gap = driver.get("gap_to_player_s")
        options = (cover or {}).get("options") or []
        next_lap = options[0] if options else None
        payload = {
            "driver": driver.get("name"),
            "position": driver.get("position"),
            "tyre": driver.get("tyre_compound"),
            "gap_to_player_s": round(float(gap), 2) if gap is not None else None,
            "side": (cover or {}).get("side") or ("behind" if gap is not None and float(gap) > 0 else "ahead"),
            "seen_in_lane": seen_in_lane,
            "cover_holds_next_lap": bool(next_lap and next_lap.get("holds")) if next_lap else None,
            "cover_margin_s": next_lap.get("margin_s") if next_lap else None,
            "cover_by": (cover or {}).get("cover_by"),
            "clear_air_laps": (cover or {}).get("clear_air_laps"),
            "pace_needed_s": (cover or {}).get("pace_needed_s"),
            "also": [item[2].get("name") for item in relevant[1:]],
        }
        self._enqueue(
            "rival_pitting",
            payload,
            critical=bool(payload["cover_holds_next_lap"]),
            cooldown_s=0.0,
            expires_s=25.0,
        )

    def _note_window_stop(self, state: dict[str, Any], driver: dict[str, Any], player: dict[str, Any]) -> None:
        """Count stops off the player's tyre; three opens the window, said once a stint."""
        idx = int(driver.get("car_idx", -1))
        entry = self._rival_stop_entry.pop(idx, None)
        compound = str((state.get("tyre") or {}).get("compound") or "").upper()
        player_stops = int(player.get("pit_stops", 0) or 0)
        if entry is None or entry[0] != compound or not compound:
            return
        if int(driver.get("pit_stops", 0) or 0) != player_stops + 1:
            return
        key = (compound, player_stops)
        stopped = self._window_stops.setdefault(key, {})
        stopped[idx] = entry[1]
        if len(stopped) < RIVAL_WINDOW_STOPS or key in self._window_called:
            return
        if state.get("mode_profile") not in {"race", "sprint"}:
            return
        self._window_called.add(key)
        laps = sorted(stopped.values())
        self._enqueue(
            "pit_window_open",
            {
                "cars": len(stopped),
                "compound": compound,
                "first_lap": laps[0],
                "last_lap": laps[-1],
                "box_lap": ((state.get("strategy") or {}).get("recommended") or {}).get("box_lap"),
            },
            cooldown_s=0.0,
            expires_s=60.0,
        )

    @staticmethod
    def _player(state: dict[str, Any]) -> dict[str, Any] | None:
        player_idx = int(state.get("player_car_index", -1))
        return next(
            (
                driver
                for driver in state.get("drivers", []) or []
                if int(driver.get("car_idx", -1)) == player_idx
            ),
            None,
        )

    @staticmethod
    def _player_out_of_race(state: dict[str, Any]) -> bool:
        """Retired, disqualified, unclassified - or classified at the flag."""
        player = ProactiveEngineer._player(state)
        if player is None:
            return False
        status = int(player.get("result_status", 0) or 0)
        if status in OUT_OF_RACE_RESULT_STATUS:
            return True
        return status == FINISHED_RESULT_STATUS and str(
            state.get("mode_profile", "")
        ) in {"race", "sprint"}

    @staticmethod
    def _player_in_garage(state: dict[str, Any]) -> bool:
        player = ProactiveEngineer._player(state)
        return bool(player) and str(player.get("status", "")) == "garage"

    @staticmethod
    def _car_moving(state: dict[str, Any]) -> bool:
        return int(state.get("speed_kph", 0) or 0) > 30

    @staticmethod
    def _spoken_name(name: Any, default: str) -> str:
        text = str(name or "").strip()
        if not text:
            return default
        return text.title() if text.isupper() else text

    @staticmethod
    def _rival_pitting_text(payload: dict[str, Any]) -> str:
        """The stop, and what it means for us, in the engine's own numbers."""
        name = ProactiveEngineer._spoken_name(payload.get("driver"), "A rival")
        gap = payload.get("gap_to_player_s")
        gap_text = f"{abs(float(gap)):.1f}" if gap is not None else None
        stopping = "is in the pits" if payload.get("seen_in_lane", True) else "has pitted"
        if payload.get("side") == "ahead":
            text = f"{name} ahead {'is in' if payload.get('seen_in_lane', True) else 'has pitted'}."
            if payload.get("pace_needed_s") is not None and payload.get("clear_air_laps"):
                text += (
                    f" Clear air now: {payload['clear_air_laps']} laps to make "
                    f"{float(payload['pace_needed_s']):.1f} seconds."
                )
        else:
            text = f"{name} {stopping}" + (f", {gap_text} behind." if gap_text else ".")
            if payload.get("cover_holds_next_lap") is True and payload.get("cover_margin_s") is not None:
                text += f" Box next lap and you stay ahead by {float(payload['cover_margin_s']):.1f}."
            elif payload.get("cover_holds_next_lap") is False:
                text += " You can't cover it; stay out and run your race."
        also = [ProactiveEngineer._spoken_name(item, "") for item in payload.get("also") or [] if item]
        if also:
            text += f" {' and '.join(also)} {'is' if len(also) == 1 else 'are'} in too."
        return text

    @staticmethod
    def _prepared_neutralisation_call(phase: str, state: dict[str, Any]) -> str:
        """The call prepared on this lap for a safety car or a VSC, said with the flag.

        The published plan is still the green one when the flag changes (the
        live recompute takes a moment), so the flag call would otherwise carry
        the green instruction. Only for the lap it was prepared on, and only
        while the pit entry is still ahead: past it, the stop cannot be made
        this lap and the live recompute decides.
        """
        kind = {"safety_car": "safety_car", "vsc": "vsc"}.get(phase)
        strategy = state.get("strategy") or {}
        ready = strategy.get("if_neutralised") or {}
        call = ready.get(kind) if kind else None
        if not call or int(ready.get("lap", -1)) != int(state.get("current_lap", 0) or 0):
            return ""
        if (strategy.get("neutralisation") or {}).get("phase", "green") != "green":
            return ""
        if StrategyEngine._pit_entry_status(state) == "passed":
            return ""
        return str(call.get("instruction") or "")

    @staticmethod
    def _rejoin_clause(payload: dict[str, Any], state: dict[str, Any]) -> str:
        """Where an imminent stop comes out, from the strategy's rejoin map.

        Said only for a box call on this lap or the next, when the map names
        the car ahead: further out the gaps will have moved before the stop.
        """
        instruction = str(payload.get("instruction") or "")
        box = payload.get("box_lap")
        current = int(state.get("current_lap", 0) or 0)
        if box is None or not instruction.lower().startswith("box") or not 0 <= int(box) - current <= 1:
            return ""
        rows = (state.get("strategy") or {}).get("rejoin_map") or []
        row = next((item for item in rows if item.get("box_lap") == int(box)), None)
        if not row:
            return ""
        if int(row.get("rejoin_position", 0) or 0) == 1 and not row.get("car_ahead"):
            return "You come out in the lead."
        name = str(row.get("car_ahead") or "").strip()
        gap = row.get("gap_ahead_s")
        if not name or gap is None:
            return ""
        name = name.title() if name.isupper() else name
        if row.get("clear_air"):
            return f"You come out behind {name} in clear air."
        return f"You come out {float(gap):.1f} seconds behind {name}."

    @staticmethod
    def _call_signature(recommended: dict[str, Any]) -> tuple[Any, str, int]:
        """What a strategy call says, for telling two calls apart."""
        return (
            recommended.get("box_lap"),
            str(recommended.get("fit_compound") or "").upper(),
            int(recommended.get("stops_remaining", 0) or 0),
        )

    def _drop_pending(self, reason: str, *, keep: frozenset[str] = frozenset()) -> None:
        """Discard everything queued, recording why, so none of it is spoken.

        Calls whose type is in ``keep`` stay queued.
        """
        kept: list[dict[str, Any]] = []
        for item in self.pending:
            if item.get("type") in keep:
                kept.append(item)
                continue
            item["delivery_outcome"] = reason
            item.setdefault("blocked_reasons", []).append(reason)
            self._discarded.append(item)
        self.pending = deque(kept, maxlen=24)

    @staticmethod
    def _red_flag_active(state: dict[str, Any]) -> bool:
        # Neither the pause menu nor a count of earlier red flags is a flag.
        return bool(state.get("red_flag_active")) or state.get("race_control_phase") == "red_flag"

    @staticmethod
    def _event_still_relevant(event: dict[str, Any], state: dict[str, Any]) -> bool:
        if "session_context" in event and tuple(event["session_context"]) != session_key(state):
            return False
        if int(event.get("session_uid", 0)) != int(state.get("session_uid", 0)):
            return False
        if time.time() > float(event.get("expires_at", 0)):
            return False
        if ProactiveEngineer._player_out_of_race(state):
            return False
        kind = event.get("type")
        if kind == "race_control":
            phase = "red_flag" if ProactiveEngineer._red_flag_active(state) else state.get("race_control_phase", "green")
            return event.get("payload", {}).get("to") == phase
        if kind in {"rival_pitting", "weather_crossover", "weather_stand_down"} and (
            int(state.get("pit_status", 0) or 0) != 0 or bool(state.get("pit_lane_timer_active"))
        ):
            # Queued before the driver turned in: their own stop is the news now.
            return False
        if ProactiveEngineer._red_flag_active(state) and kind not in SURVIVES_SUSPENSION:
            # Old box/traffic/pace calls must not accompany suspension advice.
            return False
        if kind == "weather_crossover":
            payload = event.get("payload", {}) or {}
            if payload.get("level") is None:
                # A call queued without a level (older records): still worth
                # saying while the surface is over the crossover, or while the
                # model is still asking for the stop or the driver's read.
                published = (state.get("strategy", {}) or {}).get("weather_crossover") or {}
                return (
                    rain.surface_is_wet(state)
                    or bool(published.get("worth_stopping"))
                    or bool(published.get("ask_driver"))
                )
            # Worth saying while the call stands at least where it was queued.
            # A box call the engine has since withdrawn, or one for a tyre it no
            # longer wants, must not be spoken.
            call = ProactiveEngineer._weather_call(state)
            level = int(payload.get("level", WEATHER_NONE))
            if call["level"] < level:
                return False
            if level >= WEATHER_STANDBY:
                return call["compound"] == str(payload.get("compound") or "").upper()
            return True
        if kind == "weather_stand_down":
            return ProactiveEngineer._weather_call(state)["level"] < WEATHER_STANDBY
        if kind == "weather_box_reminder":
            payload = event.get("payload", {}) or {}
            call = ProactiveEngineer._weather_call(state)
            return (
                call["level"] == WEATHER_BOX
                and call["compound"] == str(payload.get("compound") or "").upper()
                and int(state.get("current_lap", 0) or 0) == int(payload.get("lap", -1))
                and not int(state.get("pit_status", 0) or 0)
            )
        if kind == "fuel_warning":
            return float(state.get("fuel_laps_delta", 1.0)) < FUEL_SAVE_DELTA_LAPS
        if kind == "tyre_wear":
            return max(state.get("tyre", {}).get("wear", [0]) or [0]) >= WEAR_CALL_REARM_PCT
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
        if kind == "sc_restart":
            # Advice for a restart still to come. Said once the race is green,
            # "stay positive on the delta until green" holds the driver back
            # while the field races away.
            return str(state.get("race_control_phase", "green")) == str(
                (event.get("payload", {}) or {}).get("phase")
            )
        if kind == "compound_requirement":
            return bool(state.get("strategy", {}).get("compound_rule", {}).get("change_outstanding"))
        if kind == "blue_flag":
            return state.get("fia_flag") == "blue"
        if kind == "yellow_ahead":
            return ProactiveEngineer._flagged_zone_ahead(event.get("payload", {}) or {}, state) is not None
        if kind == "lap_deleted":
            return bool(state.get("current_lap_invalid"))
        if kind == "penalty_service":
            return bool(
                int(state.get("unserved_drive_through_penalties", 0))
                or int(state.get("unserved_stop_go_penalties", 0))
                or state.get("pit_stop_should_serve_penalty")
            )
        if kind == "strategy_change":
            if EngineerBrain._hold_running(state):
                return False
            # The call that was queued is only worth speaking while it is
            # still the call. Plans move between queueing and a safe moment
            # to speak: "Box lap 14 for softs" and "Box lap 18 for softs"
            # went out thirty seconds apart in one race because the first
            # had waited for a quiet straight.
            payload = event.get("payload", {}) or {}
            current = state.get("strategy", {}).get("recommended", {}) or {}
            return ProactiveEngineer._call_signature(
                payload
            ) == ProactiveEngineer._call_signature(current)
        if kind == "penalty":
            # A flashback takes the penalty back; do not announce it after.
            return int(state.get("penalties_s", 0) or 0) >= int(
                (event.get("payload", {}) or {}).get("penalties_s", 0) or 0
            )
        if kind == "warning":
            return int(state.get("corner_cutting_warnings", 0) or 0) >= int(
                (event.get("payload", {}) or {}).get("corner_cutting_warnings", 0) or 0
            )
        if kind == "driver_check":
            if EngineerBrain._hold_running(state):
                return False
            box_lap = (
                state.get("strategy", {}).get("recommended", {}).get("box_lap")
            )
            current_lap = int(state.get("current_lap", 0) or 0)
            return bool(
                box_lap is not None
                and 1 <= int(box_lap) - current_lap <= 3
                and str(state.get("race_control_phase", "green")) == "green"
            )
        if kind == "pace_roast":
            # Switching brutal mode off drops anything already queued, and a
            # roast is not spoken over a safety car it was queued before.
            return bool(settings.brutal_mode) and str(
                state.get("race_control_phase", "green")
            ) == "green"
        return True

    def _engine_block_reason(self, state: dict[str, Any], *, urgent: bool = False) -> str | None:
        """What stops *every* call this tick, whatever the call is.

        Separated from the per-call checks because the two behave completely
        differently when the queue backs up. These conditions are properties of
        the car and the radio, so if one holds, no call can be spoken and there
        is no point looking at the rest of the queue. Everything in
        ``_safe_to_speak`` varies from call to call, and giving up on the whole
        queue because the first one failed is what silenced the others.

        ``urgent`` is for a FLASH or critical call. It waits only while the
        radio channel is in use (the driver talking, the engineer's audio
        playing), not through the silent parts of a conversation: the reply
        window after the engineer's own question, or the seconds spent
        answering a driver question.
        """
        if not state.get("connected") or (state.get("game_paused") and not self._red_flag_active(state)):
            return "disconnected or paused"
        if state.get("ptt_pressed"):
            return "driver holding push-to-talk"
        # An open speech session makes the controller "busy" for the whole of its
        # lifetime. Treating that as a blanket block suppressed red flags,
        # penalties, damage and safety-car-delta warnings for up to the maximum
        # session length, by which time they had all expired unspoken. A live
        # conversation can be spoken into — the session itself delivers the line
        # — so only genuine capture (the driver talking) blocks a critical call.
        if self.voice.is_busy and not bool(
            getattr(self.voice, "realtime_active", False)
        ):
            if urgent and not bool(getattr(self.voice, "audio_busy", True)):
                return None
            # Name the latch. "Driver or engineer busy" covers the driver
            # holding the radio button and six unrelated states of the voice
            # controller, which is no help at all when the queue has stopped.
            return f"engineer busy: {self.voice.busy_reason or 'unknown'}"
        return None

    def _is_urgent(self, event: dict[str, Any]) -> bool:
        """A FLASH or critical call: a flag, a penalty, damage, a safety car.

        A call the driver talked over is not urgent on its way back: they keyed
        the radio to say something, so it waits for the whole exchange rather
        than cutting in between their question and the answer.
        """
        if event.get("cut_short"):
            return False
        return bool(event.get("flash")) or self._priority_of(event) <= CRITICAL

    @staticmethod
    def _baseline_safe(state: dict[str, Any]) -> bool:
        """Whether the driving itself is calm enough to be spoken into."""
        return int(state.get("speed_kph", 0)) < 75 or (
            float(state.get("brake", 0)) <= settings.proactive_max_brake
            and abs(float(state.get("lateral_g", 0))) <= settings.proactive_max_lateral_g
            and float(state.get("throttle", 0)) >= SAFE_TO_SPEAK_MIN_THROTTLE
        )

    @staticmethod
    def _wide_safe(state: dict[str, Any]) -> bool:
        """The wider straight-line window a battery call may use.

        Never heavy braking or a high-G corner, but it does not insist on the
        full throttle the strict window wants: a call about the battery is worth
        making while the driver is attacking.
        """
        return (
            float(state.get("throttle", 0)) >= 0.25
            and float(state.get("brake", 0)) <= 0.22
            and abs(float(state.get("lateral_g", 0))) <= 1.55
        )

    def _update_safe_window(self, state: dict[str, Any]) -> None:
        """Track how long each safety window has been open, once per tick.

        These are properties of the car, not of any particular call, and they
        have to be measured that way. The clock used to be reset inside the
        per-call check, so a call that failed the strict window reset it for the
        call behind that had a wider one. It was also always measured against
        the *strict* window, so a call could satisfy its own wider window and
        still be told to wait out one it was never asked to meet. The battery
        call carries exactly such a widening, and was delivered nine times in a
        hundred.
        """
        now = time.monotonic()
        baseline = self._baseline_safe(state)
        self._safe_since = (self._safe_since or now) if baseline else 0.0
        wide = baseline or self._wide_safe(state)
        self._wide_safe_since = (self._wide_safe_since or now) if wide else 0.0

    def _safe_to_speak(self, state: dict[str, Any], event: dict[str, Any]) -> bool:
        """Whether *this* call can be spoken now. Callers check the engine first.

        Every condition here varies from call to call: a critical call may be
        spoken into an open conversation, an overdue one accepts a rougher
        stretch of road, and a battery call has a window of its own. That is
        why a blocked call must not stop the queue — the next one may hold a
        relaxation this one does not.
        """
        blocked = self._engine_block_reason(state, urgent=self._is_urgent(event))
        if blocked is not None:
            self._safe_since = self._wide_safe_since = 0.0
            self._mark_blocked(event, blocked)
            return False
        if self._red_flag_active(state):
            if event.get("type") == "race_control" and event.get("payload", {}).get("to") == "red_flag":
                return True
            # A penalty, a drive-through or damage is still owed to the
            # driver; it follows the red-flag call, with the car stopped.
            return event.get("type") in SURVIVES_SUSPENSION and not any(
                held.get("type") == "race_control" and (held.get("payload") or {}).get("to") == "red_flag"
                for held in self.pending
            )
        if bool(getattr(self.voice, "realtime_active", False)) and self._priority_of(
            event
        ) > CRITICAL:
            # Non-critical chatter still waits for the driver to finish talking.
            self._mark_blocked(event, "conversation open")
            return False
        now = time.time()
        overdue = now >= float(event.get("deliver_by", now + 1))
        brake = float(state.get("brake", 0))
        lat_g = abs(float(state.get("lateral_g", 0)))
        speed = int(state.get("speed_kph", 0))
        throttle = float(state.get("throttle", 0))
        safe = self._baseline_safe(state)
        uses_wide = (
            event.get("type") == "energy_low"
            and self._priority_of(event) <= IMPORTANT
        )
        if uses_wide:
            safe = safe or self._wide_safe(state)
        # Deadline fallback still refuses heavy braking/high-G, but no longer waits
        # forever for full throttle on tracks with short straights.
        if overdue:
            safe = speed < 90 or (brake < 0.35 and lat_g < 1.85)
        if not safe:
            # Carry the numbers that closed the window. A driver whose calls
            # never arrive needs to be able to see whether the engineer is
            # waiting for a straight that this lap never offers. The shared
            # clock is not touched here: _update_safe_window owns it.
            self._mark_blocked(
                event,
                f"unsafe driving phase (speed {speed}, throttle {throttle:.2f}, "
                f"brake {brake:.2f}, lateral {lat_g:.2f}g)",
            )
            return False
        if event.get("critical") or overdue:
            return True
        self._update_safe_window(state)
        # Hold the window this call is actually using, not a stricter one it was
        # never asked to meet.
        held_since = self._wide_safe_since if uses_wide else self._safe_since
        ready = bool(held_since) and (
            time.monotonic() - held_since >= settings.proactive_safe_hold_s
        )
        if not ready:
            self._mark_blocked(event, "safe window not held")
        return ready

    @staticmethod
    def _mark_blocked(event: dict[str, Any], reason: str) -> None:
        reasons = event.setdefault("blocked_reasons", [])
        if reason not in reasons:
            reasons.append(reason)
        # The list above is deduplicated history, which is what the saved record
        # wants but the wrong thing to diagnose a stall from: the first reason a
        # call was ever blocked for stays at the end of it forever. Keep the
        # reason that applies *now* separately, so a queue that has stopped
        # moving can say what is holding it rather than what once did.
        # Stamped only when the reason changes. Delivery re-judges the queue
        # ten times a second, so refreshing this on every pass would report
        # every call as having been blocked for no time at all — which is the
        # opposite of what a stalled queue needs to say.
        if event.get("blocked_reason") != reason:
            event["blocked_at"] = time.time()
        event["blocked_reason"] = reason

    @staticmethod
    def _relevance_reason(
        event: dict[str, Any], state: dict[str, Any]
    ) -> str | None:
        if int(event.get("session_uid", 0)) != int(state.get("session_uid", 0)):
            return "superseded by new session"
        if time.time() > float(event.get("expires_at", 0)):
            return "expired"
        if ProactiveEngineer._player_out_of_race(state):
            return "driver is out of the race"
        return None if ProactiveEngineer._event_still_relevant(event, state) else "superseded"

    @staticmethod
    def _strategy_advice_allowed(state: dict[str, Any]) -> bool:
        """Whether an unsolicited call may carry the pit plan at all.

        A driver declines a stop in one of two ways: refusing the call, which
        sets a five-lap hold, or asking for the pit stop not to be raised, which
        saves a standing instruction. Both used to stop only the strategy_change
        call, so the tyre warning and the progress update went on saying "Box
        lap 18 for HARD" through the hold. Safety is not traded away here: the
        hold releases itself on wear past the hard limit, new damage, a flag
        change or rain, and the warnings themselves are still spoken.
        """
        if EngineerBrain._hold_running(state):
            return False
        if not ProactiveEngineer.is_suppressed(
            "strategy_change", state.get("standing_instructions", []) or []
        ):
            return True
        # The standing rule reads "do not raise the pit stop unless the driver
        # asks or it is safety-critical". A limiting tyre past the hard limit,
        # the same line that releases a hold, is safety-critical: from there
        # the warnings carry the plan again.
        wear = max(
            [float(value) for value in (state.get("tyre", {}) or {}).get("wear", [0]) or [0]]
        )
        return wear >= safety_limit_pct((state.get("tyre", {}) or {}).get("compound", ""))

    @staticmethod
    def _spoken_advice(
        recommended: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any]:
        """The plan with its instruction worded as the radio says it.

        The driver-asked answer applies two guards the engine string does not:
        a stop that gains nothing and loses places is "Stay out", and a stop
        for the compound just fitted is never called. The unsolicited calls
        used to read the engine string verbatim, so one plan produced "Stay
        out" when asked and "Box lap 33 for SOFT" in the next flag call. The
        engine's own wording is kept when it says more than a box lap (the
        mandatory-compound and disqualification sentences).
        """
        if not recommended:
            return {}
        current_lap = int(state.get("current_lap", 0) or 0)
        tyre = state.get("tyre", {}) or {}
        engine = str(recommended.get("instruction") or "").strip()
        override = EngineerBrain._strategy_guard_override(recommended, current_lap, tyre)
        box_lap, fit = recommended.get("box_lap"), recommended.get("fit_compound")
        if override is not None:
            spoken = override
        elif engine and box_lap is not None and fit and engine == f"Box lap {box_lap} for {fit}.":
            spoken = EngineerBrain._spoken_strategy_instruction(recommended, current_lap, tyre)
        else:
            return dict(recommended)
        return {**recommended, "instruction": spoken}

    @staticmethod
    def _advice(state: dict[str, Any]) -> dict[str, Any]:
        """The current plan as a proactive call may carry it, or nothing."""
        if not ProactiveEngineer._strategy_advice_allowed(state):
            return {}
        recommended = state.get("strategy", {}).get("recommended", {}) or {}
        return ProactiveEngineer._spoken_advice(recommended, state)

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
            or wear >= safety_limit_pct(state.get("tyre", {}).get("compound", ""))
            or (box_lap is not None and int(box_lap) <= current_lap + 1)
        )
        return ProactiveEngineer._advice(state) if urgent else {}

    @staticmethod
    def _sector_progress(state: dict[str, Any]) -> tuple[int, int] | None:
        """How far the field is into posting this lap's sectors, in fives.

        While rain is falling or due, the wet model reads the field's sectors
        as they arrive. Counting them in fives recomputes as the evidence
        lands without recomputing at the telemetry rate.
        """
        weather = str(state.get("weather", "")).lower()
        if not ("rain" in weather or weather == "storm" or int(state.get("rain_now_pct", 0) or 0) > 20):
            return None
        drivers = state.get("drivers", []) or []
        s1 = sum(1 for driver in drivers if int(driver.get("current_s1_ms", 0) or 0) > 0)
        s2 = sum(1 for driver in drivers if int(driver.get("current_s2_ms", 0) or 0) > 0)
        return s1 // 5, s2 // 5

    async def _refresh_strategy_if_needed(self, state: dict[str, Any]) -> dict[str, Any]:
        if state.get("mode_profile") == "qualifying":
            return state
        tyre = state.get("tyre", {}) or {}
        wear = [float(x) for x in tyre.get("wear", []) or []]
        compound = str(tyre.get("compound") or "").upper()
        # Inputs whose change is an event: the call is recomputed at once.
        discrete = repr((
            int(state.get("current_lap", 0)), state.get("race_control_phase"),
            bool(state.get("red_flag_active")),
            repr(state.get("tyre_sets", [])), state.get("fitted_tyre_set_idx"),
            state.get("weather"), int(state.get("rain_next_15_pct", 0)),
            # The wet model reads the rain falling now, the driver's report on
            # the surface and the lap times behind it, so a change in any of
            # them has to be able to move the call.
            int(state.get("rain_now_pct", 0) or 0),
            int(state.get("last_lap_ms", 0) or 0),
            tuple(sorted((state.get("driver_grip_feedback", {}) or {}).items())),
            tyre.get("compound"), int(tyre.get("age_laps", 0)),
            # Crossing the wear limit changes what is feasible: at once.
            max(wear, default=0.0) >= operational_limit_pct(compound),
            tuple(sorted((state.get("driver_tyre_feedback", {}) or {}).items())),
            state.get("strategy_risk_appetite", "balanced"),
            tuple((d.get("car_idx"), d.get("pit_stops"), d.get("tyre_compound")) for d in state.get("drivers", [])),
        ))
        # Inputs that move continuously. Wear to a tenth of a percent crossed
        # a step on some wheel every second or so, and each step ran the
        # whole planner; by the percent, and no more often than the floor.
        continuous = repr((
            tuple(int(x) for x in wear),
            ProactiveEngineer._sector_progress(state),
        ))
        now = time.monotonic()
        if discrete != self._last_strategy_inputs_signature or (
            continuous != self._last_strategy_continuous_signature
            and now - self._last_recompute_at >= float(settings.strategy_min_recompute_interval_s)
        ):
            self._last_strategy_inputs_signature = discrete
            self._last_strategy_continuous_signature = continuous
            self._last_recompute_at = now
            self._recompute_times.append(now)
            await self.strategy.recompute()
            return await self.store.snapshot_radio()
        return state

    def _recomputes_per_min(self) -> int:
        """Planner runs this engineer asked for in the last minute."""
        now = time.monotonic()
        while self._recompute_times and now - self._recompute_times[0] > 60.0:
            self._recompute_times.popleft()
        return len(self._recompute_times)

    @staticmethod
    def _hold_material_change(
        state: dict[str, Any], hold: dict[str, Any]
    ) -> str | None:
        return hold_material_change(state, hold)

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
        refreshed = await self.store.snapshot_radio()
        return refreshed, reason

    def _detect_driver_check(self, state: dict[str, Any]) -> None:
        if state.get("mode_profile") not in {"race", "sprint"}:
            return
        if str(state.get("race_control_phase", "green")) != "green":
            return
        if self.voice.is_busy or state.get("ptt_pressed"):
            return
        if not self._strategy_advice_allowed(state):
            # Asking about the tyres two laps before a window the driver has
            # just declined presses the stop they said no to. Not recorded as
            # asked, so the check is still made if the window stands once the
            # hold is over.
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
        asked_for = self._driver_check_stints.get(stint_key)
        # Ask once per stint per window. If the recommended box lap has moved
        # by four laps or more since the driver was last asked, that is a new
        # conversation to have, not a repeat of the old one.
        if asked_for is not None and abs(int(box_lap) - asked_for) < 4:
            return
        self._driver_check_stints[stint_key] = int(box_lap)
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

    @staticmethod
    def _bottom_five_position(state: dict[str, Any]) -> tuple[int, int] | None:
        """(position, active cars) when the player is classified in the bottom five.

        Needs a real field: with fewer than six cars everyone is in the bottom
        five, and a position of 0 is a garaged or retired car, not last place.
        """
        position = int(state.get("player_position", 0) or 0)
        cars = int(state.get("active_cars", 0) or 0)
        if cars < 6 or position <= 0 or position > cars:
            return None
        if position > cars - 5:
            return position, cars
        return None

    def _detect_pace_roast(self, state: dict[str, Any], analyzed_lap: int) -> None:
        """Brutal mode: demand pace when the driver is slow or near the back.

        Judged once per analysed lap, from the same progress record the
        two-lap update reads, so the delta the engineer shouts is the delta
        the dashboard shows. Only in a race or sprint (a practice lap is not
        "slow" in any useful sense), only under green (a car crawling behind
        the safety car is doing what it was told) and never at a car that is
        garaged, retired or classified.
        """
        if not bool(settings.brutal_mode):
            return
        if state.get("mode_profile") not in {"race", "sprint"}:
            return
        if analyzed_lap <= 0 or analyzed_lap == self._last_roast_lap:
            return
        if str(state.get("race_control_phase", "green")) != "green":
            return
        if self._player_in_garage(state) or not self._car_moving(state):
            return
        progress = state.get("analysis", {}).get("progress", {}) or {}
        # A stale progress record belongs to an earlier lap and was already
        # judged; roast this lap only once its own analysis has landed.
        if int(progress.get("lap_num", analyzed_lap) or analyzed_lap) != analyzed_lap:
            return
        self._last_roast_lap = analyzed_lap
        delta = progress.get("delta_to_target_s")
        slow = (
            isinstance(delta, (int, float))
            and float(delta) >= float(settings.brutal_pace_tolerance_s)
        )
        bottom = self._bottom_five_position(state)
        if not slow and bottom is None:
            return
        reason = "both" if slow and bottom else "bottom_five" if bottom else "slow_lap"
        self._enqueue(
            "pace_roast",
            {
                "lap": analyzed_lap,
                "reason": reason,
                "position": bottom[0] if bottom else int(state.get("player_position", 0) or 0),
                "active_cars": bottom[1] if bottom else int(state.get("active_cars", 0) or 0),
                "bottom_five": bottom is not None,
                "delta_to_target_s": round(float(delta), 3) if slow else None,
                "target": progress.get("target"),
                "lap_time": progress.get("lap_time"),
                "tolerance_s": float(settings.brutal_pace_tolerance_s),
            },
            cooldown_s=40.0,
            expires_s=45.0,
        )

    async def _detect(self, state: dict[str, Any]) -> None:
        if not state.get("connected") or (state.get("game_paused") and not self._red_flag_active(state)):
            return
        # A hold ends on its own terms whether or not unsolicited calls are
        # on: a red flag or the stop made during a quiet stretch must not
        # leave it binding the radio once the race has moved on.
        state, released_hold_reason = await self._evaluate_strategy_hold(state)
        if not bool(state.get("proactive", {}).get("enabled", settings.proactive_enabled)):
            return
        state = await self._refresh_strategy_if_needed(state)
        self._standing_instructions = state.get("standing_instructions", [])
        proactive = state.get("proactive", {})
        analyzed_lap = int(state.get("analysis", {}).get("last_lap_analyzed", 0))
        if analyzed_lap > 0 and analyzed_lap != self._last_learning_lap:
            self._last_learning_lap = analyzed_lap
            await self.setup_advisor.learn_current_session()
        if not bool(proactive.get("enabled", settings.proactive_enabled)):
            return
        if self._player_out_of_race(state):
            # The driver is a spectator now. In a real race the engineer kept
            # calling "Bortoleto is closing" and "box for a front wing" for
            # three minutes after "I'm retiring the car", then carried the
            # damage and the 40-second penalty into the next session's first
            # call.
            self._drop_pending("driver is out of the race")
            return
        in_garage = self._player_in_garage(state)

        phase = "red_flag" if self._red_flag_active(state) else str(state.get("race_control_phase", "green"))
        safety = str(state.get("safety_car", "none"))
        if phase != self._last_race_control_phase or safety != self._last_safety_car:
            previous = self._last_race_control_phase
            self._last_race_control_phase, self._last_safety_car = phase, safety
            # The flag itself is never suppressible — a driver may silence
            # gearbox reminders, not a safety car. The pit recommendation
            # riding along with it is a different matter: it is ordinary
            # strategy advice, and the driver can decline it.
            #
            # Because the two were welded into one payload, a driver who said
            # "I'm not going to box" and was told "understood, we'll stay out"
            # was then told "Box this lap for softs" one lap later, inside a
            # race-control call that suppression could never reach. The
            # agreement evaporating is a worse failure than the advice being
            # wrong: it teaches the driver that nothing they say is retained.
            strategy_advice = self._advice(state)
            payload = {"from": previous, "to": phase, "strategy": strategy_advice, "neutralisation": state.get("strategy", {}).get("neutralisation", {})}
            # The call prepared for this flag on this lap; it rides the same
            # suppression as the plan advice.
            prepared = self._prepared_neutralisation_call(phase, state) if strategy_advice else ""
            if prepared:
                payload["prepared"] = prepared
            if phase == "red_flag":
                # A suspension change is a new opportunity, not the paid pit
                # stop the driver may previously have declined.
                payload["red_flag_restart"] = state.get("strategy", {}).get("red_flag_restart", {})
                self._drop_pending("race suspended", keep=SURVIVES_SUSPENSION)
                # They are said once the car is stopped, after the red-flag
                # call, which can take a while to come round.
                for held in self.pending:
                    held["expires_at"] = max(float(held.get("expires_at", 0) or 0), time.time() + 300.0)
            self._enqueue("race_control", payload, critical=True, cooldown_s=0.0, expires_s=3600.0 if phase == "red_flag" else None)
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

        if phase == "red_flag":
            # The persistent restart plan remains visible; announce once per
            # suspension, without ordinary racing or pit-entry chatter.
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
        self._detect_pace_roast(state, analyzed_lap)

        flagged = state.get("analysis", {}).get("flagged_corners", [])
        if flagged:
            top = flagged[0]
            signature = f"{top.get('corner_no')}:{round(float(top.get('average_loss_s', 0)), 2)}"
            mode = state.get("mode_profile", "idle")
            if signature != self._last_corner_signature and (mode == "practice" or (mode == "qualifying" and int(state.get("speed_kph", 0)) < 80) or float(top.get("average_loss_s", 0)) >= 0.30):
                self._last_corner_signature = signature
                self._enqueue("corner_coaching", top, cooldown_s=60.0 if mode in {"race", "sprint"} else 35.0)

        # The conditions call comes from the wetness model, not from a rain
        # percentage. A forecast crossing 60% is not news on a track that is
        # already soaked, and a track drying under a 70% forecast is news the
        # percentage alone would never have reported.
        self._detect_weather(state)

        # Fuel, tyre, penalty and damage calls describe a car being driven.
        # In the garage, and on the grid before the car has moved, the game
        # still reports values and some are inherited from the last session:
        # a Sakhir practice opened with "box for a front wing and serve the
        # 40-second penalty", both from the Suzuka race before it, and three
        # fuel warnings were spoken to a car sitting still at the grid.
        car_running = not in_garage and self._car_moving(state)
        fuel_delta = float(state.get("fuel_laps_delta", 1.0))
        if fuel_delta >= FUEL_RECOVERED_DELTA_LAPS:
            # Back to a non-negative margin: the next shortfall is news again.
            self._delivered_signatures.pop("fuel_warning", None)
        if car_running and fuel_delta < FUEL_SAVE_DELTA_LAPS:
            self._enqueue(
                "fuel_warning",
                {
                    **(state.get("analysis", {}).get("fuel_model", {}) or {}),
                    "fuel_laps_delta": round(fuel_delta, 2),
                },
                cooldown_s=90.0,
            )
        wear = max(state.get("tyre", {}).get("wear", [0]) or [0])
        if wear < WEAR_CALL_REARM_PCT:
            # Fresh tyres: the next stint's warnings start from scratch.
            self._delivered_signatures.pop("tyre_wear", None)
        if not in_garage and wear >= WEAR_CALL_PCT:
            limit = safety_limit_pct(state.get("tyre", {}).get("compound", ""))
            self._enqueue("tyre_wear", {"wear_fl_fr_rl_rr": state.get("tyre", {}).get("wear"), "compound": state.get("tyre", {}).get("compound"), "strategy": self._urgent_strategy(state)}, critical=wear >= limit, cooldown_s=60.0)

        penalties, warnings = int(state.get("penalties_s", 0)), int(state.get("corner_cutting_warnings", 0))
        if in_garage:
            pass
        elif penalties > self._last_penalties:
            # penalties_s is the game's running total. Saying only the total
            # told a driver already on 5 s that he had "picked up a 10 second
            # penalty", so he believed he now carried 15.
            self._enqueue(
                "penalty",
                {
                    "penalties_s": penalties,
                    "added_s": penalties - self._last_penalties,
                    "total_s": penalties,
                },
                critical=True,
                cooldown_s=0.0,
            )
        elif warnings >= 2 and warnings > self._last_warnings:
            self._enqueue("warning", {"corner_cutting_warnings": warnings}, critical=True, cooldown_s=0.0)
        self._last_penalties, self._last_warnings = penalties, warnings

        damage = state.get("damage", {})
        values = {key: int(damage.get(key, 0) or 0) for key in DAMAGE_KEYS}
        maximum = max(values.values() or [0])
        # A DRS or ERS fault has no percentage but is immediately race-affecting,
        # so it is called regardless of damage level.
        faults = tuple(key for key in DAMAGE_FAULT_KEYS if damage.get(key))
        # Escalate by 20% band, not on every change. The signature covered every
        # component, so a single percent of floor wear re-fired a *critical*
        # damage call that bypassed the spacing interval — the recorded session
        # has six "box for gearbox inspection" calls in fifteen laps, all
        # reporting the same unactionable damage.
        #
        # The band is kept per component. Judged on the maximum across all of
        # them, a 45% wing beside a 25% floor, or a fresh wing hit after a nose
        # change while the floor carried 30%, never reached a new band and was
        # never called.
        band = (maximum // 20) * 20
        # Damage going down (a front-wing change, a flashback, a fresh car
        # after the garage) re-arms that component, so its next real hit is
        # reported rather than compared against a level that no longer exists.
        # The delivered-call memory is re-armed with it: a new 25% hit after a
        # wing change is a new call, not a repeat of the one before it.
        rearmed = False
        for key, value in values.items():
            if (value // 20) * 20 < self._reported_damage_bands.get(key, 0):
                self._reported_damage_bands[key] = (value // 20) * 20
                rearmed = True
        if rearmed or not self._reported_damage_faults <= set(faults):
            self._delivered_signatures.pop("damage", None)
        self._reported_damage_band = min(self._reported_damage_band, band)
        # A fault that cleared is news again if it comes back.
        self._reported_damage_faults &= set(faults)
        new_faults = [fault for fault in faults if fault not in self._reported_damage_faults]
        changed = [
            key for key, value in values.items()
            if value > 10 and (value // 20) * 20 > self._reported_damage_bands.get(key, 0)
        ]
        escalated = bool(changed)
        if not in_garage and (escalated or new_faults):
            # No cooldown: the band and fault memory is the throttle, and a
            # cooldown in front of it silently refused the second hit of an
            # incident (25% wing, then 65% thirty seconds later) after the
            # tracker had already recorded it as told, so it was never said.
            # The tracker is only advanced once the call is actually queued.
            queued = self._enqueue(
                "damage",
                {
                    **damage,
                    "max_damage_pct": maximum,
                    "new_faults": new_faults,
                    "escalated": escalated,
                    "changed": changed,
                    "repairable": ["front wing"],
                },
                critical=maximum >= 40 or bool(new_faults),
                cooldown_s=0.0,
            )
            if queued:
                self._reported_damage_band = max(band, self._reported_damage_band)
                for key in changed:
                    self._reported_damage_bands[key] = (values[key] // 20) * 20
                self._reported_damage_faults.update(faults)

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

        self._detect_marshal_yellow(state)
        self._detect_race_start(state)
        self._detect_component_wear(state)
        self._detect_energy(state)
        await self._detect_rival_pace(state)
        self._detect_driver_check(state)

        unserved = (
            int(state.get("unserved_drive_through_penalties", 0)),
            int(state.get("unserved_stop_go_penalties", 0)),
        )
        if not in_garage and (
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
                    "strategy": self._advice(state),
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
            self._enqueue("compound_requirement", {"laps_remaining": laps_remaining, "used": rule.get("dry_compounds_used", []), "eligible": rule.get("eligible_next_compounds", []), "strategy": self._advice(state)}, critical=laps_remaining <= 3, cooldown_s=90.0)

        recommended = state.get("strategy", {}).get("recommended", {})
        strategy_signature = f"{recommended.get('box_lap')}:{recommended.get('fit_compound')}:{recommended.get('stops_remaining')}"
        hold = state.get("strategy_hold", {}) or {}
        if released_hold_reason and recommended and not hold.get("raised_after_release"):
            payload = {
                **self._spoken_advice(recommended, state),
                "hold_released_reason": released_hold_reason,
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
            if (
                old and not stability.get("held") and self._urgent_strategy(state)
                and not self._weather_call_carries(recommended, state)
            ):
                self._enqueue("strategy_change", self._spoken_advice(recommended, state), cooldown_s=15.0)

        current_stops = {int(d.get("car_idx", -1)): int(d.get("pit_stops", 0)) for d in state.get("drivers", [])}
        await self._detect_rival_stops(state)
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
            # top_opportunity is sometimes the full finding dict rather than a
            # sentence. The dashboard already guards for this; the radio did
            # not, and read a raw {'start_m': 5331.0, ...} repr to the driver
            # mid-race. Speech gets the instruction or nothing.
            if isinstance(opportunity, dict):
                opportunity = (
                    str(opportunity.get("instruction") or "").strip()
                    or "keep the lap clean"
                )
            opportunity = ProactiveEngineer._sentence(opportunity)
            if payload.get("mode_profile") == "qualifying":
                quali = payload.get("qualifying", {})
                return (
                    f"Qualifying update: session best "
                    f"{quali.get('session_best') or 'unavailable'}, target "
                    f"{quali.get('target') or 'building'}. {opportunity}"
                )
            strategy = payload.get("strategy", {}) or {}
            pace = target.get("target") or target.get("target_lap")
            # "Lap 12: target target unavailable." was the old no-target line.
            heading = (
                f"Lap {payload.get('lap')}: target {pace}."
                if pace
                else f"Lap {payload.get('lap')}: target still building."
            )
            action = ProactiveEngineer._sentence(strategy.get("instruction"))
            return f"{heading} {action or opportunity}"
        if kind == "race_control":
            if payload.get("to") == "red_flag":
                restart = payload.get("red_flag_restart") or {}
                advice = restart.get("instruction") or (
                    "Tyres can be changed during the suspension. Check the available sets for the restart."
                )
                return "Red flag, red flag. " + EngineerBrain.qualify_strategy_text(str(advice), state.get("strategy", {}))
            # Lead with the flag: it is the only part that is always true and
            # always urgent, and this template is now spoken verbatim for FLASH
            # calls rather than being reworded by the model. Advice is appended
            # only when there is some, so a driver who has declined pit advice
            # hears the flag alone instead of a hollow "stand by for the
            # revised plan".
            raw_phase = str(payload.get("to", "change"))
            phase = RACE_CONTROL_LABELS.get(raw_phase, raw_phase.replace("_", " ").capitalize())
            advice = ProactiveEngineer._sentence(
                payload.get("prepared") or (payload.get("strategy") or {}).get("instruction")
            )
            return f"Race control: {phase}." + (f" {advice}" if advice else "")
        if kind == "yellow_ahead":
            # Short on purpose. This is spoken while the driver is arriving at
            # the incident, so it leads with the instruction, not the detail.
            metres = int(float(payload.get("distance_ahead_m", 0)))
            if payload.get("flag") == "red":
                # A red zone is not a yellow with a different colour: "no
                # overtaking" is not the instruction, slowing to stop is.
                return f"Red flag in the sector ahead, {metres} metres. Slow down and be ready to stop."
            return f"Yellow flag, sector ahead, {metres} metres. Lift and no overtaking."
        if kind == "strategy_change":
            changed = payload.get("hold_released_reason")
            prefix = f"Your strategy hold is released because {changed}. " if changed else ""
            # The narrated call carries a low-confidence plan's qualifier, so
            # the template does too: a provisional plan must not sound firm
            # because narration was off or timed out.
            text = prefix + EngineerBrain.qualify_strategy_text(
                str(payload.get("instruction", "Strategy has changed; check the dashboard.")),
                state.get("strategy", {}),
            )
            clause = ProactiveEngineer._rejoin_clause(payload, state)
            return f"{text} {clause}" if clause else text
        if kind == "driver_check":
            laps = int(payload.get("laps_to_window", 2) or 2)
            return (
                f"{laps} laps to the window. How are the rears — holding, "
                "or going away?"
            )
        if kind == "tyre_wear":
            # This is spoken over the radio, so it has to be sayable. Formatting
            # the raw list put "wear is [78.0, 80.5, 71.0, 73.5]" in the
            # engineer's mouth. A driver needs the worst corner and its number,
            # not four decimals in reading order.
            wear = [
                float(value)
                for value in (payload.get("wear_fl_fr_rl_rr") or [])
                if isinstance(value, (int, float))
            ]
            instruction = str(
                payload.get("strategy", {}).get("instruction")
                or "Protect the limiting tyre."
            )
            if len(wear) == 4:
                corners = ("front left", "front right", "rear left", "rear right")
                worst = max(range(4), key=lambda index: wear[index])
                return (
                    f"Tyre warning. {corners[worst].capitalize()} is the limiting "
                    f"corner at {wear[worst]:.0f} percent. {instruction}"
                )
            if wear:
                return (
                    f"Tyre warning, worst corner {max(wear):.0f} percent. "
                    f"{instruction}"
                )
            return f"Tyre warning. {instruction}"
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
            delta = state.get("fuel_laps_delta", payload.get("fuel_laps_delta"))
            if not isinstance(delta, (int, float)):
                return "Fuel is short of race distance. Start lifting and coasting on the long straights."
            if float(delta) >= 0:
                # Never read a surplus out as a shortfall.
                return f"Fuel is plus {float(delta):.1f} laps. No saving needed."
            return (
                f"Fuel is short by {abs(float(delta)):.1f} laps. Start lifting "
                "and coasting on the long straights."
            )
        if kind == "damage":
            # Lead with what is new. A DRS fault reported a minute ago is not
            # the news when the front wing has just gone from 25 to 65 percent.
            faults = (
                set(payload.get("new_faults") or [])
                if "new_faults" in payload
                else {key for key in DAMAGE_FAULT_KEYS if payload.get(key)}
            )
            fault_line = (
                "ERS fault reported. Expect reduced deployment; we are reassessing the plan."
                if "ers_fault" in faults
                else "DRS fault reported. Do not rely on the overtaking aid this lap."
                if "drs_fault" in faults
                else ""
            )
            # Only the front wing can be changed at a stop, so reliability damage
            # is reported as something to manage, never as a reason to box.
            # The call leads with what changed: a floor reported a lap ago is
            # not the news when the wing has just gone to 65 percent. Front-wing
            # damage, the one thing a stop can change, is always said.
            changed = payload.get("changed")
            if not isinstance(changed, list):
                changed = [key for key in DAMAGE_KEYS if int(payload.get(key, 0) or 0) > 10]
            wing = max(
                int(payload.get("front_left_wing", 0) or 0),
                int(payload.get("front_right_wing", 0) or 0),
            )
            wing_line = (
                f"Front-wing damage {wing} percent. That is the one thing we can "
                "change at a stop; say the word if the balance is costing you."
            )
            advice = {
                "engine": ("Engine", "Short-shift where you can; it cannot be repaired at a stop."),
                "gearbox": ("Gearbox", "Shift smoothly; it cannot be repaired at a stop."),
                "floor": ("Floor", "Expect reduced downforce; it cannot be repaired at a stop."),
            }
            reliability = [
                f"{advice[key][0]} damage {int(payload.get(key, 0))} percent. {advice[key][1]}"
                for key in ("engine", "gearbox", "floor")
                if key in changed and int(payload.get(key, 0) or 0) > 10
            ]
            wing_changed = any(key in changed for key in ("front_left_wing", "front_right_wing"))
            if fault_line:
                # A new fault leads; a wing that changed in the same hit is
                # still said, being the one thing a stop can fix.
                return fault_line + (f" {wing_line}" if wing > 10 and wing_changed else "")
            lines = ([wing_line] if wing > 10 and wing_changed else []) + reliability[:1]
            if wing > 10 and not wing_changed:
                lines.append(wing_line)
            if lines:
                return " ".join(lines)
            return "Aero damage detected. Report the balance change and we will assess it."
        if kind == "compound_requirement":
            return "You still owe a second dry compound. We must fit a different compound before the finish."
        if kind == "weather_crossover":
            # Different things to say, and the difference matters to a driver:
            # we are boxing, we are about to and need your read, we need you to
            # tell us something before we decide, or we have looked and we are
            # staying out. A stop leads with the instruction; the reason is
            # cut to its first sentence so the pit entry is not passed while
            # the evidence is read out.
            reason = str(payload.get("reason") or "").strip()
            first = ProactiveEngineer._first_sentence(reason)
            tyre = ProactiveEngineer._compound_plural(payload.get("compound"))
            if payload.get("awaiting_confirmation") and payload.get("compound"):
                return f"{reason} How is the grip out there?".strip()
            if payload.get("ask_driver") and payload.get("driver_question"):
                return (
                    f"{reason} It is close enough that your read decides it. "
                    f"{payload.get('driver_question')}"
                ).strip()
            if payload.get("worth_stopping") and payload.get("compound"):
                box = payload.get("box_lap")
                current = int(state.get("current_lap", 0) or 0)
                where = (
                    f"Box lap {box}"
                    if box is not None and int(box) > current
                    else "Box this lap"
                )
                return f"{where} for {tyre}. {first}".strip()
            if reason:
                if payload.get("level") == WEATHER_WATCH and payload.get("direction") == "to_dry":
                    return f"{reason} I'll call the change to slicks."
                if payload.get("level") == WEATHER_WATCH:
                    return f"{reason} I'll call the stop if it comes."
                return reason
            return (
                f"Rain risk is {payload.get('rain_15_pct')} percent within fifteen minutes. "
                "Stand by for a crossover call."
            )
        if kind == "weather_stand_down":
            current = ProactiveEngineer._compound_plural(payload.get("current_compound")) or "current tyres"
            first = ProactiveEngineer._first_sentence(str(payload.get("reason") or ""))
            return f"Cancel the stop. Stay out on the {current}. {first}".strip()
        if kind == "weather_box_reminder":
            tyre = ProactiveEngineer._compound_plural(payload.get("compound"))
            return f"Box, box. {tyre.capitalize()} on." if tyre else "Box, box."
        if kind == "penalty":
            total = payload.get("total_s", payload.get("penalties_s"))
            added = payload.get("added_s", total)
            if isinstance(added, (int, float)) and isinstance(total, (int, float)) and added < total:
                return (
                    f"Another {added:g} second penalty; that is {total:g} seconds in total. "
                    "Keep it clean from here."
                )
            return f"You have picked up a {total} second penalty. Keep it clean from here."
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
        if kind == "rival_pitting":
            return ProactiveEngineer._rival_pitting_text(payload)
        if kind == "pit_window_open":
            tyre = ProactiveEngineer._compound_plural(payload.get("compound")) or "your tyre"
            first, last = payload.get("first_lap"), payload.get("last_lap")
            laps = f"lap {first}" if first == last else f"laps {first} to {last}"
            ours = f" Ours is lap {payload['box_lap']}." if payload.get("box_lap") is not None else ""
            return f"The window is open: {payload.get('cars')} cars on {tyre} have stopped, {laps}.{ours}"
        if kind == "race_start":
            side = "clean side" if payload.get("clean_side") else "dirty side"
            return (
                f"Lights out from P{payload.get('grid_position')}, {side}. "
                "Get temperature into the tyres and commit to your turn-one line."
            )
        if kind == "sc_restart":
            if payload.get("phase") == "vsc_ending":
                # A VSC has no leader-controlled restart: green comes on the
                # timer and the delta applies until it does.
                return (
                    "VSC ending. Stay positive on the delta until green, then go; "
                    "battery charged and tyres warm."
                )
            return (
                "Restart coming. Charge the battery, keep temperature in the tyres "
                "and brakes, and watch the leader for the go point."
            )
        if kind == "energy_low":
            aid = "Overtake Mode" if payload.get("regulations_2026", state.get("regulations_2026")) else "ERS deployment"
            return (
                f"Battery low at {payload.get('ers_pct')} percent. Harvest a lap "
                f"before using sustained {aid} again."
            )
        if kind == "component_wear":
            components = [
                item for item in (payload.get("components") or [])
                if isinstance(item, dict) and item.get("component")
            ]
            if len(components) > 1:
                named = [
                    f"{ProactiveEngineer._component_label(item['component'])} "
                    f"{float(item.get('wear_pct', 0) or 0):.0f}"
                    for item in components
                ]
                listed = ", ".join(named[:-1]) + f" and {named[-1]}"
                return (
                    f"Power-unit wear: {listed} percent. Manage it to avoid a "
                    "component penalty later in the season."
                )
            return (
                f"{ProactiveEngineer._component_label(payload.get('component', 'engine'))} wear at "
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
            name = ProactiveEngineer._spoken_name(payload.get("driver"), "Car behind")
            gap = abs(float(payload.get("gap_to_player_s", 0.0)))
            rate = payload.get("gap_change_s_per_lap")
            contact = payload.get("laps_to_contact")
            if rate is not None:
                text = f"{name} is catching, {gap:.1f} back, {abs(float(rate)):.1f} a lap"
                if contact is not None:
                    text += f"; with you in about {max(1, round(float(contact)))} laps"
                return text + ". Protect the tyres you will need to defend."
            return (
                f"{name} is catching, {gap:.1f} seconds back "
                "and closing. Protect the tyres you will need to defend."
            )
        if kind == "pace_roast":
            return ProactiveEngineer.roast_text(payload)
        return "Engineer update available on the dashboard."

    @staticmethod
    def _component_label(key: Any) -> str:
        """A power-unit component as it is said, not as it is stored."""
        raw = str(key or "engine")
        return COMPONENT_LABELS.get(
            raw.lower(), raw.upper().replace("_", "-")
        )

    @staticmethod
    def _first_sentence(text: str) -> str:
        """The first sentence of a reason, for a call that must be short."""
        cleaned = " ".join(str(text or "").split())
        if not cleaned:
            return ""
        head, separator, _ = cleaned.partition(". ")
        return head + "." if separator else cleaned

    @staticmethod
    def _compound_plural(compound: Any) -> str:
        """'INTER' -> 'inters', the way the radio names a tyre to fit."""
        name = str(compound or "").strip().lower()
        if not name:
            return ""
        return name if name.endswith("s") else f"{name}s"

    @staticmethod
    def _sentence(text: Any) -> str:
        """One spoken sentence: capitalised, ending in exactly one stop."""
        cleaned = " ".join(str(text or "").split()).rstrip(" .")
        if not cleaned:
            return ""
        if cleaned[-1] not in "!?":
            cleaned += "."
        return cleaned[0].upper() + cleaned[1:]

    @staticmethod
    def roast_text(payload: dict[str, Any]) -> str:
        """The deterministic brutal-mode line for a pace_roast payload."""
        reason = str(payload.get("reason", "slow_lap"))
        delta = payload.get("delta_to_target_s")
        slow = isinstance(delta, (int, float))
        if reason == "both" and slow:
            pool = ROAST_LINES_BOTH
        elif reason in {"bottom_five", "both"}:
            pool = ROAST_LINES_BOTTOM_FIVE
        elif slow:
            pool = ROAST_LINES_SLOW_LAP
        else:
            return "Bitch, do you mind driving faster?"
        position = int(payload.get("position", 0) or 0)
        cars = int(payload.get("active_cars", 0) or 0)
        line = pool[int(payload.get("lap", 0) or 0) % len(pool)]
        return line.format(
            position=position,
            cars=cars,
            behind=max(0, cars - position),
            delta=float(delta) if slow else 0.0,
            target=payload.get("target") or "the target",
            lap_time=payload.get("lap_time") or "that",
        )

    @staticmethod
    def is_suppressed(
        event_type: str,
        standing_instructions: list[Any] | None,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        """Whether the driver has asked for this kind of call to stop.

        Safety and legality calls are never suppressed, however the instruction
        was worded — the driver can silence gearbox reminders, not a red flag.

        With the call's ``payload``, the stored rule's "unless it is
        safety-critical" is honoured too: a tyre at or past the hard wear
        limit, and damage of 40 percent or more or a new fault, are said
        whatever the rule. A component subject ("the gearbox", "the floor")
        silences damage calls only about that component: "stop telling me
        about the gearbox" had also silenced a 70% front wing and an ERS fault.
        """
        if event_type in NEVER_SUPPRESSED:
            return False
        subjects = EngineerBrain.suppressed_subjects(standing_instructions)
        if not subjects:
            return False
        if payload is not None and ProactiveEngineer._safety_critical(event_type, payload):
            return False
        components: set[str] = set()
        for subject in subjects:
            words = set(re.findall(r"[a-z]+", subject))
            # Drivers say "tyres", "updates", "rivals"; the topic table is
            # singular. Compare both forms rather than requiring an exact word.
            words |= {word.rstrip("s") for word in words if word.endswith("s")}
            matched = [
                keyword for keyword, covered in SUPPRESSIBLE_SUBJECTS.items()
                if event_type in covered and (
                    keyword in subject if " " in keyword
                    else keyword in words or keyword.rstrip("s") in words
                )
            ]
            if payload is not None and event_type == "damage":
                named = [keyword for keyword in matched if keyword in DAMAGE_COMPONENT_SUBJECTS]
                if named:
                    # "The gearbox damage" names the gearbox; "damage" there
                    # only says what about it. Read as its own subject it
                    # silenced a 30% front-wing hit as well.
                    for keyword in named:
                        components |= DAMAGE_COMPONENT_SUBJECTS[keyword]
                    continue
            if matched:
                return True
        if components and payload is not None:
            news = payload.get("changed")
            if not isinstance(news, list):
                news = [key for key in DAMAGE_KEYS if int(payload.get(key, 0) or 0) > 10]
            return bool(news) and set(news) <= components and not payload.get("new_faults")
        return False

    @staticmethod
    def _safety_critical(event_type: str, payload: dict[str, Any]) -> bool:
        """A call no standing instruction may silence, whatever its subject."""
        if event_type in {"weather_stand_down", "weather_box_reminder"}:
            return True
        if event_type == "weather_crossover":
            # "Stop telling me about the weather" silences the watching, not
            # the stop it is watching for.
            return payload.get("level") == WEATHER_BOX
        if event_type == "tyre_wear":
            wear = [
                float(value) for value in payload.get("wear_fl_fr_rl_rr") or []
                if isinstance(value, (int, float))
            ]
            return bool(wear) and max(wear) >= safety_limit_pct(payload.get("compound", ""))
        if event_type == "damage":
            news = payload.get("changed")
            if not isinstance(news, list):
                news = list(DAMAGE_KEYS)
            return bool(payload.get("new_faults")) or any(
                int(payload.get(key, 0) or 0) >= 40 for key in news
            )
        return False

    @staticmethod
    def _priority_of(event: dict[str, Any]) -> int:
        # FLASH first. Every FLASH call is also critical, and checking critical
        # first ranked a yellow queued behind a penalty as level with it, so
        # the flag waited for the penalty's narration and playback.
        if event.get("flash"):
            return FLASH
        if event.get("critical"):
            return CRITICAL
        return int(event.get("priority", EVENT_PRIORITY.get(event.get("type"), ROUTINE)))

    async def _narrate(self, event: dict[str, Any], state: dict[str, Any]) -> str:
        """Turn a deterministic event payload into spoken radio.

        The deterministic template is both the safety net and the ground truth:
        narration may change the wording, never the facts. That is checked, not
        assumed: a narration that says a number found nowhere in what the model
        was shown (the situation header and the call's facts) or in the
        template is replaced by the template. Falling back to the template on
        any model failure means an unsolicited call is never lost to a timeout.
        Whenever the template replaces a narration, the call records why, so
        the saved call says which words the driver heard.
        """
        fallback = self._fallback_text(event, state)
        # A call worded again after a requeue starts with a clean record.
        event.pop("narration_rejected", None)
        event.pop("narration_evidence", None)
        if event.get("blocked_reasons"):
            event["blocked_reasons"] = [
                reason for reason in event["blocked_reasons"]
                if not str(reason).startswith(TEMPLATE_SPOKEN)
            ]
        if not settings.proactive_narration_enabled:
            return fallback
        if event.get("flash"):
            # See FLASH_EVENTS: for these the template is the better answer,
            # not the safety net. Waiting on the model made a flag call arrive
            # after the flag had cleared, and once made it say the opposite of
            # what the payload contained.
            return fallback
        try:
            spoken = await asyncio.wait_for(
                self.brain.proactive(event),
                timeout=settings.proactive_narration_timeout_s,
            )
        except Exception as exc:
            log.debug("Proactive narration fell back to the template: %s", exc)
            event.pop("narration_evidence", None)
            self._template_replaced_narration(event, f"narration failed ({type(exc).__name__})")
            return fallback
        evidence = event.pop("narration_evidence", None)
        spoken = " ".join((spoken or "").split())
        payload = event.get("payload") or {}
        asks_driver = event.get("type") == "driver_check" or bool(
            payload.get("ask_driver") and payload.get("driver_question")
        )
        if asks_driver and not spoken.endswith("?"):
            # The call exists to hear the driver's read (the tyre check, the
            # slick-inter crossover), and only a call that ends on its
            # question opens the reply window. A narration that lost or moved
            # the question is replaced by the template.
            self._template_replaced_narration(event, "narration lost its question")
            return fallback
        if not spoken:
            self._template_replaced_narration(event, "narration was empty")
            return fallback
        if isinstance(evidence, str):
            # The template's own numbers count as evidence: it is computed
            # from the same state, so "two laps" or a count it works out are
            # numbers the call is entitled to say.
            payload = event.get("payload") or {}
            try:
                invented = unsupported(
                    spoken,
                    evidence,
                    fallback,
                    laps=lap_counts(
                        state.get("current_lap"),
                        state.get("total_laps"),
                        (state.get("strategy") or {}).get("recommended") or {},
                        state.get("strategy_hold") or {},
                        payload,
                    ),
                    places=place_counts(
                        state.get("player_position"),
                        {"active_cars": state.get("active_cars")},
                        payload,
                    ),
                )
            except Exception as exc:
                # An unchecked narration is not spoken, and a check that
                # cannot run must not cost the driver the call: the call has
                # already left the queue, so the template is said instead.
                log.warning("Proactive narration check failed; template spoken: %s", exc)
                self._template_replaced_narration(event, f"narration check failed ({type(exc).__name__})")
                return fallback
            if invented:
                said = ", ".join(dict.fromkeys(number.text for number in invented))
                reason = f"narration said {said}, which its facts do not contain"
                log.info("Proactive %s narration replaced by its template: %s", event.get("type"), reason)
                self._template_replaced_narration(event, reason)
                return fallback
        if self._restates_declined_stop(event, state, spoken):
            # The narrator is told not to restate the plan while the driver's
            # hold runs, but the plan is still in the radio history it reads.
            # "Box lap 18" straight after "Copy, we stay out" is checked, not
            # left to one prompt line.
            self._template_replaced_narration(event, "narration restated a declined stop")
            return fallback
        return spoken

    # Calls whose own news is a stop (a penalty to serve, a wing to change, a
    # compound still owed, a flag) may name it while the hold runs.
    _STOP_IS_THE_NEWS = frozenset({
        "penalty", "penalty_service", "damage", "engine_failure", "compound_requirement",
        "weather_crossover", "weather_stand_down", "weather_box_reminder",
        "race_control", "sc_restart", "safety_car_delta",
    })

    @classmethod
    def _restates_declined_stop(cls, event: dict[str, Any], state: dict[str, Any], spoken: str) -> bool:
        """Whether a narration calls the stop the driver declined, while the hold runs."""
        if event.get("type") in cls._STOP_IS_THE_NEWS or cls._strategy_advice_allowed(state):
            return False
        recommended = (state.get("strategy") or {}).get("recommended") or {}
        if recommended.get("box_lap") is None:
            return False
        return _plan_call_index(
            _radio_sentences(spoken), recommended, int(state.get("current_lap", 0) or 0)
        ) is not None

    @staticmethod
    def _template_replaced_narration(event: dict[str, Any], reason: str) -> None:
        """Record on the call why its template was spoken instead of the narration.

        The note joins the call's blocked reasons, which the saved call keeps.
        For a call that was sent to the model, a saved record without the note
        means the driver heard the model's words; flag calls and calls made
        with narration off are never sent.
        """
        event["narration_rejected"] = reason
        note = f"{TEMPLATE_SPOKEN}{reason}"
        reasons = event.setdefault("blocked_reasons", [])
        if note not in reasons:
            reasons.append(note)

    def _select(self, state: dict[str, Any]) -> dict[str, Any] | None:
        """Drop irrelevant calls, then pick the most important speakable one.

        Strict first-in-first-out meant a routine progress update at the head of
        the queue could hold up a safety-car delta warning behind it until both
        expired.
        """
        self._prune(state)
        candidates = self._candidates(state)
        return candidates[0] if candidates else None

    def _prune(self, state: dict[str, Any]) -> None:
        """Drop calls that are no longer worth making.

        Kept separate from selection because delivery needs the queue pruned
        even on the ticks where it never gets as far as choosing a call: a stale
        call left in place is re-reported to the database on every pass and goes
        on occupying one of the twenty-four slots that a live call needs.
        """
        standing = state.get("standing_instructions", [])
        kept: list[dict[str, Any]] = []
        for event in list(self.pending):
            if not self._event_still_relevant(event, state):
                continue
            # The driver asked for this subject to be dropped. Honour it here so
            # the call is never spoken, rather than only rewording it.
            if self.is_suppressed(str(event.get("type", "")), standing, event.get("payload") or {}):
                continue
            kept.append(event)
        self.pending = deque(kept, maxlen=24)

    def _candidates(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        """Everything still worth saying, most important first.

        ``_select`` returns the head of this list. Delivery walks the whole of
        it, because a call the driving phase rules out does not rule out the one
        behind it: the next may be critical, overdue, or carry a wider window of
        its own.
        """
        return sorted(
            self.pending,
            key=lambda event: (
                self._priority_of(event),
                float(event.get("queued_at", 0.0)),
            ),
        )

    async def _flush_records(self, state: dict[str, Any]) -> None:
        """Save what was discarded or went stale, then drop the stale calls."""
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
        self._prune(state)

    def _next_speakable(
        self, state: dict[str, Any], *, flash_only: bool = False
    ) -> dict[str, Any] | None:
        """The most important call that can be spoken now, if any.

        Walks the queue in priority order and returns the first call that can
        be spoken. Stopping at the first blocked call was the bug this
        replaces: every condition left in _safe_to_speak varies from call to
        call, so the one at the head being unspeakable said nothing about the
        rest. A battery warning with a deliberately wider window, a critical
        call allowed into an open conversation, an overdue call that accepts a
        rougher stretch of road — each of them sat behind a call that could not
        use its relaxation, and waited there until it expired.
        """
        for candidate in self._candidates(state):
            if flash_only and not candidate.get("flash"):
                continue
            if not self._safe_to_speak(state, candidate):
                continue
            # Only routine chatter waits out the full spacing interval; an
            # important call gets a much shorter one so it is still current
            # when spoken.
            if self._priority_of(candidate) > CRITICAL:
                interval = (
                    settings.proactive_min_interval_s
                    if self._priority_of(candidate) == ROUTINE
                    else settings.proactive_important_interval_s
                )
                if time.monotonic() - self._last_spoken_at < interval:
                    self._mark_blocked(candidate, "minimum interval")
                    continue
            return candidate
        return None

    @session_scoped(raise_on_change=False)
    async def _deliver(self, state: dict[str, Any]) -> None:
        await self._flush_records(state)
        if not self.pending:
            return

        # Nothing can be spoken while the car is disconnected or the radio is
        # held, so the queue is left alone and told why.
        engine_blocked = self._engine_block_reason(state, urgent=True)
        if engine_blocked is not None:
            self._safe_since = self._wide_safe_since = 0.0
            for held in self.pending:
                self._mark_blocked(held, engine_blocked)
            return

        self._update_safe_window(state)
        event = self._next_speakable(state)
        if event is None:
            return
        await self._deliver_event(event, state)

    async def _deliver_event(self, event: dict[str, Any], state: dict[str, Any]) -> None:
        """Word, re-check and speak one call that has been chosen."""
        with contextlib.suppress(ValueError):
            self.pending.remove(event)
        self._refresh_payload(event, state)
        unchanged = self._unchanged_reason(event)
        if unchanged is not None:
            await self._discard(event, unchanged, state)
            return
        await self.store.mutate(lambda s: s.proactive.update({"queued": len(self.pending), "delivery_state": "generating"}))
        advice_before = self._carried_advice(event)
        text = await self._narrate(event, state)
        if not await self.store.matches_session(state):
            return
        if not event.get("flash"):
            # Wording this call can take seconds, and the race does not wait.
            # A FLASH call queued meanwhile goes first: it is short and only
            # worth saying while it is true.
            await self._speak_waiting_flash()
            # Everything checked before narration was checked against the
            # race as it was then. A driver who declined the stop, silenced the
            # subject or keyed the radio while this was being worded must not
            # hear it anyway: "Box this lap for hards" was spoken straight
            # after "Copy, we stay out".
            fresh = await self.store.snapshot_radio()
            # The race moving on is checked, not the clock: the call was within
            # its life when it was chosen, and dropping it for the seconds spent
            # wording it would lose a penalty or damage call that is never
            # detected again.
            stale = self._relevance_reason(
                {**event, "expires_at": max(float(event.get("expires_at", 0) or 0), time.time() + 1.0)},
                fresh,
            )
            if stale is None and self.is_suppressed(
                str(event.get("type", "")), fresh.get("standing_instructions", []), event.get("payload") or {}
            ):
                stale = "silenced by the driver"
            if stale is not None:
                await self._discard(event, f"{stale} while it was being worded", fresh, text)
                return
            self._refresh_payload(event, fresh)
            if self._carried_advice(event) != advice_before:
                # Still worth saying, but not in those words.
                self._requeue(event, "the plan changed while it was being worded")
                return
            blocked = self._engine_block_reason(fresh, urgent=self._is_urgent(event))
            if blocked is None and bool(
                getattr(self.voice, "realtime_active", False)
            ) and self._priority_of(event) > CRITICAL:
                # As in _safe_to_speak: a conversation the driver opened while
                # this was being worded is not talked over by routine news.
                blocked = "conversation open"
            if blocked is not None:
                # Waiting for the driver is not a retry: the call goes back
                # however often they key the radio, until it expires.
                self._requeue(event, blocked, counted=False)
                return
            state = fresh
        repeat = self._repeat_reason(event, text)
        if repeat is not None:
            await self._discard(event, repeat, state, text)
            return
        await self._speak(event, text, state)

    async def _speak_waiting_flash(self) -> None:
        """Speak FLASH calls that became speakable while another was worded."""
        for _ in range(3):
            state = await self.store.snapshot_radio()
            await self._flush_records(state)
            if not self.pending or self._engine_block_reason(state, urgent=True) is not None:
                return
            flash = self._next_speakable(state, flash_only=True)
            if flash is None:
                return
            with contextlib.suppress(ValueError):
                self.pending.remove(flash)
            self._refresh_payload(flash, state)
            unchanged = self._unchanged_reason(flash)
            if unchanged is not None:
                await self._discard(flash, unchanged, state)
                continue
            await self._speak(flash, self._fallback_text(flash, state), state)

    async def _discard(
        self,
        event: dict[str, Any],
        reason: str,
        state: dict[str, Any],
        text: str | None = None,
    ) -> None:
        event["delivery_outcome"] = reason
        self._mark_blocked(event, reason)
        await self.database.save_proactive_call(state, event, text or reason, False)

    def _requeue(
        self, event: dict[str, Any], reason: str, *, counted: bool = True
    ) -> None:
        """Put a still-relevant call back at the front.

        A call reworded because the plan moved goes back a limited number of
        times. A call held for the driver goes back until it expires, like any
        other blocked call.
        """
        self._mark_blocked(event, reason)
        count = int(event.get("requeued", 0) or 0) + (1 if counted else 0)
        if count > MAX_REQUEUES:
            event["delivery_outcome"] = f"{reason}; dropped after {MAX_REQUEUES} retries"
            self._discarded.append(event)
            return
        event["requeued"] = count
        maxlen = getattr(self.pending, "maxlen", None)
        if maxlen is not None and len(self.pending) >= maxlen:
            dropped = self.pending.pop()
            dropped["delivery_outcome"] = "queue_full"
            dropped.setdefault("blocked_reasons", []).append("queue full")
            self._discarded.append(dropped)
        self.pending.insert(0, event)

    async def _speak(self, event: dict[str, Any], text: str, state: dict[str, Any]) -> None:
        delivered = False
        come_back = False
        # A flag call spoken into the reply window after the engineer's own
        # question gives the driver the whole window back afterwards.
        window_was_open = bool(getattr(self.voice, "reply_window_armed", False))
        cuts_before = getattr(self.voice, "speech_cut_count", None)
        try:
            delivered = await self.voice.speak_text(text)
        finally:
            cut = (
                not delivered
                and isinstance(cuts_before, int)
                and int(getattr(self.voice, "speech_cut_count", 0) or 0) > cuts_before
            )
            if delivered:
                event["delivery_outcome"] = "delivered"
            elif cut:
                # The driver keyed the radio over it. A flag, penalty or damage
                # call goes back once, to be said after their exchange if it is
                # still true then; routine news cut short is not repeated.
                come_back = self._is_urgent(event) and not event.get("cut_short")
                event["delivery_outcome"] = (
                    "cut short by the driver; requeued" if come_back
                    else "cut short by the driver"
                )
            else:
                event["delivery_outcome"] = "voice delivery failed"
            await self.database.save_proactive_call(state, event, text, delivered)
        if come_back:
            event["cut_short"] = True
            event.pop("delivery_outcome", None)
            self._requeue(event, "cut short by the driver", counted=False)
            return
        if delivered:
            self._last_spoken_at = time.monotonic()
            self._last_spoken_text = text
            self._last_spoken_type = str(event.get("type", ""))
            payload = event.get("payload") or {}
            if event.get("type") == "weather_crossover" and payload.get("level") == WEATHER_BOX:
                # Only a box call actually heard counts against repeating it.
                self._weather_box_heard.append({
                    "lap": int(state.get("current_lap", 0) or 0),
                    "compound": str(payload.get("compound") or "").upper(),
                    "margin": float(payload.get("margin_s") or 0.0),
                    "wetness": float(payload.get("wetness") or 0.0),
                })
            signature = self._material_signature(event)
            if signature is not None:
                self._delivered_signatures[self._last_spoken_type] = (
                    signature[0], time.monotonic(),
                )
            # Only a call the driver actually heard becomes conversation history.
            await self.brain.record_spoken_call(text)
            await self.store.mutate(lambda s: s.proactive.update({
                "last_spoken_lap": s.current_lap, "last_call": text,
                "queued": len(self.pending), "delivery_state": "delivered",
            }))
            if text.rstrip().endswith("?") or window_was_open:
                # The engineer asked; the driver must be able to answer
                # without saying the wake phrase first.
                open_window = getattr(self.voice, "open_reply_window", None)
                if open_window is not None:
                    await open_window("engineer asked a question")

    @staticmethod
    def _carried_advice(event: dict[str, Any]) -> str:
        """The pit instruction a call would speak, for spotting a change."""
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            return ""
        if event.get("type") == "strategy_change":
            return str(payload.get("instruction") or "")
        strategy = payload.get("strategy")
        if isinstance(strategy, dict):
            return str(strategy.get("instruction") or "")
        return ""

    @staticmethod
    def _material_signature(
        event: dict[str, Any],
    ) -> tuple[tuple[Any, ...], float | None] | None:
        """What a call says that matters, and how long an unchanged one is a repeat.

        Two calls of one type that say materially the same thing are a repeat
        however they are worded; two that differ are news however alike they
        sound. Comparing wording alone dropped "VSC ending" as a duplicate of
        "VSC", a second safety-car delta breach, a 5 s penalty becoming 10 s and
        a front wing going from 25 to 65 percent, while letting an unchanged
        tyre warning back every few minutes. A horizon of None means the call
        is not repeated while the level holds, however long that is.

        Types with no signature here are either one-off edges (a penalty, a
        blue flag, a yellow per lap and zone) or are compared by wording.
        """
        kind = event.get("type")
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            return None
        if kind == "race_control":
            return (str(payload.get("to")),), RACE_CONTROL_REPEAT_S
        if kind == "sc_restart":
            return (str(payload.get("phase")),), RACE_CONTROL_REPEAT_S
        # Tyre and fuel warnings are levels: an unchanged reading is not news
        # on a timer, however long it lasts. _detect clears their memory when
        # the reading recovers, so the next stint or shortfall starts afresh.
        if kind == "tyre_wear":
            wear = [
                float(value)
                for value in payload.get("wear_fl_fr_rl_rr") or []
                if isinstance(value, (int, float))
            ]
            worst = max(wear) if wear else 0.0
            advice = str((payload.get("strategy") or {}).get("instruction") or "")
            return ((int(worst) // 10) * 10, worst >= safety_limit_pct(payload.get("compound", "")), advice), None
        if kind == "fuel_warning":
            delta = payload.get("fuel_laps_delta")
            band = math.floor(float(delta) * 2) if isinstance(delta, (int, float)) else None
            return (band, ""), None
        if kind == "damage":
            faults = tuple(sorted(key for key in DAMAGE_FAULT_KEYS if payload.get(key)))
            # Per component, as the detector reports it: a floor entering a
            # band beside an unchanged 65% wing is new damage, not a repeat.
            bands = tuple(
                (key, (int(payload.get(key, 0) or 0) // 20) * 20)
                for key in DAMAGE_KEYS
                if int(payload.get(key, 0) or 0) > 10
            )
            return (bands, faults), REPEAT_HORIZON_S
        if kind == "component_wear":
            components = payload.get("components") or [
                {
                    "component": payload.get("component"),
                    "band": (int(float(payload.get("wear_pct", 0) or 0)) // 10) * 10,
                }
            ]
            return (
                tuple(sorted(
                    (str(item.get("component")), int(item.get("band", 0) or 0))
                    for item in components
                    if isinstance(item, dict)
                )),
            ), REPEAT_HORIZON_S
        if kind == "penalty_service":
            return (
                int(payload.get("drive_through", 0) or 0),
                int(payload.get("stop_go", 0) or 0),
                bool(payload.get("serve_at_stop")),
            ), REPEAT_HORIZON_S
        if kind == "compound_requirement":
            return (bool(event.get("critical")),), REPEAT_HORIZON_S
        if kind == "weather_crossover":
            if payload.get("level") is not None:
                return (
                    int(payload.get("level", WEATHER_NONE)),
                    str(payload.get("compound") or ""),
                    payload.get("box_lap"),
                    str(payload.get("direction") or ""),
                ), REPEAT_HORIZON_S
            return (
                bool(payload.get("worth_stopping")),
                bool(payload.get("ask_driver")),
                str(payload.get("compound") or ""),
                payload.get("box_lap"),
            ), REPEAT_HORIZON_S
        if kind == "weather_stand_down":
            return (str(payload.get("current_compound") or ""),), REPEAT_HORIZON_S
        if kind == "strategy_change":
            return (
                *ProactiveEngineer._call_signature(payload),
                str(payload.get("instruction") or ""),
                bool(payload.get("hold_released_reason")),
            ), REPEAT_HORIZON_S
        return None

    def _unchanged_reason(self, event: dict[str, Any]) -> str | None:
        """Why this call would only repeat the last one of its type, if it would."""
        spec = self._material_signature(event)
        if spec is None:
            return None
        signature, horizon = spec
        kind = str(event.get("type", ""))
        previous = getattr(self, "_delivered_signatures", {}).get(kind)
        if previous is None:
            return None
        old, spoken_at = previous
        if horizon is not None and time.monotonic() - spoken_at > horizon:
            return None
        same = old == signature
        if not same and kind == "tyre_wear":
            # Pit advice withdrawn (the driver declined the stop) is not news
            # about the tyres; only a new level or new advice is.
            same = old[:-1] == signature[:-1] and not signature[-1]
        if not same and kind == "fuel_warning":
            # Only a deeper shortfall is news. The reported margin wanders by
            # tenths within a lap, and treating every band crossing as news in
            # either direction said "short by 0.5" and "short by 0.6" in turn,
            # every ninety seconds, while the fuel had not really moved.
            same = (
                old[0] is not None
                and signature[0] is not None
                and signature[0] >= old[0]
            )
        return "unchanged since the last call" if same else None

    @staticmethod
    def _exempt_from_wording_check(event: dict[str, Any]) -> bool:
        return bool(
            event.get("flash")
            or event.get("critical")
            or event.get("type") in NEVER_SUPPRESSED
        )

    def _repeat_reason(self, event: dict[str, Any], text: str) -> str | None:
        """Whether the wording repeats the previous call of another type.

        Different event types can converge on the same content: a real session
        heard "Practice complete, P8" three times in thirty seconds, worded
        three ways by the model. The per-type cooldown cannot see that, so the
        wording is compared across types. Safety, legality and FLASH calls are
        never dropped for their wording, and a call of the same type as the
        last one has already been judged by what it says.
        """
        if self._exempt_from_wording_check(event):
            return None
        if (
            self._material_signature(event) is not None
            and getattr(self, "_last_spoken_type", "") == event.get("type")
        ):
            return None
        if self._is_near_repeat(text):
            return "near-duplicate of the previous call"
        return None

    # Events whose payload carries the strategy call alongside their own news.
    _STRATEGY_BEARING = frozenset(
        {
            "rival_pitted", "undercut_threat", "penalty_service",
            "compound_requirement", "race_control",
        }
    )

    def _refresh_payload(self, event: dict[str, Any], state: dict[str, Any]) -> None:
        """Replace strategy advice queued with the event by the current call.

        A call waits in the queue for a safe moment, often a minute or more,
        and the plan can move meanwhile. At Sakhir the driver pitted for hards
        under the safety car and the engine went to "stay out"; the progress
        update spoken ninety seconds later still said "box lap 18 for hards",
        from the payload it had been queued with.
        """
        kind = str(event.get("type", ""))
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return
        if kind == "race_control" and payload.get("to") == "red_flag":
            payload["red_flag_restart"] = state.get("strategy", {}).get("red_flag_restart", {})
        if kind == "yellow_ahead":
            # Say the distance as it is now, not as it was when the call was
            # queued: a car at racing speed covers 80 metres a second.
            zone = self._flagged_zone_ahead(payload, state)
            if zone is not None:
                payload["flag"], payload["distance_ahead_m"] = zone
        if kind == "weather_crossover" and payload.get("level") is not None:
            # A call that waited for a straight says the numbers as they are
            # now: the lap it names and the reason, for the same tyre.
            crossover = (state.get("strategy", {}) or {}).get("weather_crossover") or {}
            call = self._weather_call(state)
            if call["level"] == payload.get("level") and call["compound"] == str(
                payload.get("compound") or ""
            ).upper():
                payload.update(
                    box_lap=call["box_lap"],
                    reason=crossover.get("reason", payload.get("reason")),
                    wetness=crossover.get("wetness", payload.get("wetness")),
                    margin_s=crossover.get("payback_s", payload.get("margin_s")),
                )
        if kind in {"progress_update", "tyre_wear"} and "strategy" in payload:
            # When the driver's hold has just been released, the release call
            # says the plan with the reason it is back. Carried here as well,
            # the box call was said first with no explanation and then again.
            release_pending = any(
                held.get("type") == "strategy_change"
                and (held.get("payload") or {}).get("hold_released_reason")
                for held in self.pending
            )
            payload["strategy"] = {} if release_pending else self._urgent_strategy(state)
        elif kind in self._STRATEGY_BEARING and payload.get("strategy"):
            # An empty strategy was a deliberate omission (suppressed advice);
            # only a call that carried advice gets the current advice, and
            # only while the driver has not declined it since.
            payload["strategy"] = self._advice(state)
        elif kind == "strategy_change":
            current = state.get("strategy", {}).get("recommended", {}) or {}
            if current and self._call_signature(current) == self._call_signature(payload):
                released = payload.get("hold_released_reason")
                payload.update(self._spoken_advice(current, state))
                if released:
                    payload["hold_released_reason"] = released

    _REPEAT_STOPWORDS = frozenset(
        "a an and are at be but for in is it its no not of on or so the "
        "to was we with you your this that has have had".split()
    )

    def _is_near_repeat(self, text: str) -> bool:
        """Whether this call says what the previous one already said.

        Two crude signals, within a short horizon: an identical opening (the
        real repeats all began "Practice complete, P8"), or most of the
        shorter call's content words appearing in the other. It only needs to
        catch same-content-different-wording repeats seconds apart, not act
        as a transcript deduplicator.
        """
        if not self._last_spoken_text:
            return False
        if time.monotonic() - self._last_spoken_at > REPEAT_HORIZON_S:
            return False

        def normalize(value: str) -> list[str]:
            return re.findall(r"[a-z0-9]+", value.lower())

        current_words = normalize(text)
        previous_words = normalize(self._last_spoken_text)
        if not current_words or not previous_words:
            return False
        if " ".join(current_words)[:20] == " ".join(previous_words)[:20]:
            return True
        current = set(current_words) - self._REPEAT_STOPWORDS
        previous = set(previous_words) - self._REPEAT_STOPWORDS
        if not current or not previous:
            return False
        overlap = len(current & previous) / min(len(current), len(previous))
        return overlap >= 0.55

    async def _run_detect(self) -> None:
        """Watch the race. Never waits for anything being said.

        Detection and delivery used to share one task, so nothing was watched
        while the engineer spoke. Delivery blocks for the length of an
        utterance — narration plus playback, measured at 10-16 seconds for a
        race-control call — and every trigger here is edge-based, comparing
        the current reading against the last one seen. An edge that both
        opened and closed inside an utterance was therefore not announced late;
        it was never seen at all. A real race replayed through this code
        contained a safety car lasting 7.7 seconds, comfortably inside one
        sentence.

        Splitting the loops means the only cost of speaking is a delayed
        announcement, never a missed event.
        """
        while True:
            scheduled_at = time.monotonic()
            await asyncio.sleep(DETECT_INTERVAL_S)
            try:
                started_at = time.monotonic()
                scheduler_delay_ms = max(0.0, (started_at - scheduled_at - DETECT_INTERVAL_S) * 1000)
                state = await self.store.snapshot_radio()
                session_uid = int(state.get("session_uid", 0))
                if session_key(state) != self._session_context:
                    await self._reset_for_session(session_uid)
                    state = await self.store.snapshot_radio()
                await self._detect(state)
                oldest = max(0.0, time.time() - float(self.pending[0].get("queued_at", time.time()))) if self.pending else 0.0
                # Why the queue is not moving, published every tick. Without it
                # a stalled engineer is indistinguishable from a quiet race:
                # the count climbs, nothing is spoken, and the reason each call
                # was held sits in memory where nobody can read it.
                blocked = self._queue_blockage()
                await self.store.mutate(lambda s, wait=oldest, b=blocked, delay=scheduler_delay_ms, started=started_at: s.proactive.update({
                    "queued": len(self.pending), "oldest_wait_s": round(wait, 1),
                    "delivery_state": "queued" if self.pending else "waiting",
                    "scheduler_delay_ms": round(delay, 1),
                    "evaluation_ms": round((time.monotonic() - started) * 1000, 1),
                    "strategy_recomputes_per_min": self._recomputes_per_min(),
                    **b,
                }))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.store.update(last_error=f"Proactive engineer recovered from: {exc}")

    def _queue_blockage(self) -> dict[str, Any]:
        """What is holding the queue, and for how long.

        Reports the longest-waiting call's current reason rather than the
        newest, because the call that has been stuck longest is the one that
        explains a stall. A queue that is simply empty, or moving normally,
        reports nothing.
        """
        if not self.pending:
            return {"blocked_reason": "", "blocked_for_s": 0.0, "blocked_calls": 0}
        oldest = min(
            self.pending, key=lambda event: float(event.get("queued_at", 0.0))
        )
        reason = str(oldest.get("blocked_reason", "") or "")
        blocked_at = float(oldest.get("blocked_at", 0.0) or 0.0)
        return {
            "blocked_reason": reason,
            "blocked_for_s": round(max(0.0, time.time() - blocked_at), 1)
            if blocked_at
            else 0.0,
            "blocked_calls": sum(
                1 for event in self.pending if event.get("blocked_reason")
            ),
        }

    async def _run_deliver(self) -> None:
        """Speak what detection has queued, one call at a time.

        Owns narration and playback, so it is the loop that blocks. Kept
        separate from detection for that exact reason.
        """
        while True:
            await asyncio.sleep(DELIVER_INTERVAL_S)
            try:
                if not self.pending and not self._discarded:
                    continue
                await self._deliver(await self.store.snapshot_radio())
            except asyncio.CancelledError:
                # Only a cancellation of this loop ends it. One that escaped
                # from a call cut short (barge-in on its speech) must not
                # silence every later flag, penalty and damage call.
                current = asyncio.current_task()
                if current is None or current.cancelling():
                    raise
                await self.store.update(last_error="Proactive engineer recovered from: call delivery cancelled")
            except Exception as exc:
                await self.store.update(last_error=f"Proactive engineer recovered from: {exc}")
