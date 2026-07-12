from __future__ import annotations

from copy import deepcopy
from typing import Any

_FALLBACK_TRACKS = {
    0: "Melbourne", 1: "Paul Ricard", 2: "Shanghai", 3: "Sakhir",
    4: "Catalunya", 5: "Monaco", 6: "Montreal", 7: "Silverstone",
    8: "Hockenheim", 9: "Hungaroring", 10: "Spa", 11: "Monza",
    12: "Singapore", 13: "Suzuka", 14: "Abu Dhabi", 15: "Texas",
    16: "Brazil", 17: "Austria", 18: "Sochi", 19: "Mexico",
    20: "Baku", 21: "Sakhir Short", 22: "Silverstone Short",
    23: "Texas Short", 24: "Suzuka Short", 25: "Hanoi",
    26: "Zandvoort", 27: "Imola", 28: "Portimão", 29: "Jeddah",
    30: "Miami", 31: "Las Vegas", 32: "Losail",
    39: "Silverstone (Reverse)", 40: "Austria (Reverse)",
    41: "Zandvoort (Reverse)", 42: "Madrid",
}

try:
    from f1.packets import TRACKS as _PACKAGE_TRACKS
except Exception:  # pragma: no cover - defensive offline fallback
    TRACKS = _FALLBACK_TRACKS
else:
    TRACKS = {**_FALLBACK_TRACKS, **dict(_PACKAGE_TRACKS)}


TRACK_ARCHETYPE_BY_NAME = {
    "Monaco": "high_downforce",
    "Singapore": "high_downforce",
    "Hungaroring": "high_downforce",
    "Zandvoort": "high_downforce",
    "Madrid": "high_downforce",
    "Monza": "low_drag",
    "Spa": "low_drag",
    "Baku": "low_drag",
    "Las Vegas": "low_drag",
    "Jeddah": "low_drag",
    "Silverstone": "high_speed",
    "Suzuka": "high_speed",
    "Losail": "high_speed",
    "Austria": "high_speed",
    "Montreal": "traction",
    "Miami": "traction",
    "Mexico": "traction",
    "Brazil": "traction",
    "Abu Dhabi": "traction",
    "Melbourne": "traction",
    "Shanghai": "balanced",
    "Catalunya": "balanced",
    "Texas": "balanced",
    "Imola": "balanced",
}

# Safe, complete starting points. These are not claims of an online "meta";
# they are internally consistent foundations which the personal learning layer
# then adjusts from recorded pace, wear, temperatures and handling feedback.
BASELINES: dict[str, dict[str, float | int]] = {
    "high_downforce": {
        "front_wing": 44,
        "rear_wing": 42,
        "on_throttle": 35,
        "off_throttle": 48,
        "front_camber": -3.20,
        "rear_camber": -1.80,
        "front_toe": 0.06,
        "rear_toe": 0.16,
        "front_suspension": 31,
        "rear_suspension": 13,
        "front_anti_roll_bar": 14,
        "rear_anti_roll_bar": 8,
        "front_suspension_height": 24,
        "rear_suspension_height": 52,
        "brake_pressure": 98,
        "brake_bias": 55,
        "engine_braking": 45,
        "rear_left_tyre_pressure": 21.0,
        "rear_right_tyre_pressure": 21.0,
        "front_left_tyre_pressure": 24.2,
        "front_right_tyre_pressure": 24.2,
        "ballast": 6,
        "fuel_load": 0,
    },
    "low_drag": {
        "front_wing": 20,
        "rear_wing": 15,
        "on_throttle": 40,
        "off_throttle": 52,
        "front_camber": -3.10,
        "rear_camber": -1.70,
        "front_toe": 0.04,
        "rear_toe": 0.12,
        "front_suspension": 29,
        "rear_suspension": 12,
        "front_anti_roll_bar": 13,
        "rear_anti_roll_bar": 7,
        "front_suspension_height": 23,
        "rear_suspension_height": 50,
        "brake_pressure": 99,
        "brake_bias": 55,
        "engine_braking": 48,
        "rear_left_tyre_pressure": 21.2,
        "rear_right_tyre_pressure": 21.2,
        "front_left_tyre_pressure": 24.4,
        "front_right_tyre_pressure": 24.4,
        "ballast": 6,
        "fuel_load": 0,
    },
    "high_speed": {
        "front_wing": 34,
        "rear_wing": 29,
        "on_throttle": 38,
        "off_throttle": 50,
        "front_camber": -3.25,
        "rear_camber": -1.85,
        "front_toe": 0.05,
        "rear_toe": 0.14,
        "front_suspension": 33,
        "rear_suspension": 14,
        "front_anti_roll_bar": 15,
        "rear_anti_roll_bar": 8,
        "front_suspension_height": 23,
        "rear_suspension_height": 51,
        "brake_pressure": 99,
        "brake_bias": 55,
        "engine_braking": 46,
        "rear_left_tyre_pressure": 21.1,
        "rear_right_tyre_pressure": 21.1,
        "front_left_tyre_pressure": 24.3,
        "front_right_tyre_pressure": 24.3,
        "ballast": 6,
        "fuel_load": 0,
    },
    "traction": {
        "front_wing": 35,
        "rear_wing": 33,
        "on_throttle": 30,
        "off_throttle": 48,
        "front_camber": -3.10,
        "rear_camber": -1.70,
        "front_toe": 0.05,
        "rear_toe": 0.15,
        "front_suspension": 28,
        "rear_suspension": 10,
        "front_anti_roll_bar": 12,
        "rear_anti_roll_bar": 6,
        "front_suspension_height": 25,
        "rear_suspension_height": 53,
        "brake_pressure": 98,
        "brake_bias": 55,
        "engine_braking": 42,
        "rear_left_tyre_pressure": 20.8,
        "rear_right_tyre_pressure": 20.8,
        "front_left_tyre_pressure": 24.0,
        "front_right_tyre_pressure": 24.0,
        "ballast": 6,
        "fuel_load": 0,
    },
    "balanced": {
        "front_wing": 31,
        "rear_wing": 28,
        "on_throttle": 35,
        "off_throttle": 50,
        "front_camber": -3.15,
        "rear_camber": -1.75,
        "front_toe": 0.05,
        "rear_toe": 0.14,
        "front_suspension": 30,
        "rear_suspension": 12,
        "front_anti_roll_bar": 13,
        "rear_anti_roll_bar": 7,
        "front_suspension_height": 24,
        "rear_suspension_height": 52,
        "brake_pressure": 99,
        "brake_bias": 55,
        "engine_braking": 45,
        "rear_left_tyre_pressure": 21.0,
        "rear_right_tyre_pressure": 21.0,
        "front_left_tyre_pressure": 24.2,
        "front_right_tyre_pressure": 24.2,
        "ballast": 6,
        "fuel_load": 0,
    },
}


def track_name(track_id: int) -> str:
    return str(TRACKS.get(int(track_id), f"Track {track_id}"))


def track_archetype(track_id: int) -> str:
    return TRACK_ARCHETYPE_BY_NAME.get(track_name(track_id), "balanced")


def foundational_setup(track_id: int, profile: str) -> dict[str, float | int]:
    profile = profile.lower()
    archetype = track_archetype(track_id)
    setup = deepcopy(BASELINES[archetype])
    if profile == "quali":
        setup["front_wing"] = int(setup["front_wing"]) + 1
        setup["on_throttle"] = int(setup["on_throttle"]) + 5
        setup["front_suspension"] = int(setup["front_suspension"]) + 2
        setup["rear_suspension"] = int(setup["rear_suspension"]) + 1
        setup["front_anti_roll_bar"] = int(setup["front_anti_roll_bar"]) + 1
        setup["brake_pressure"] = 100
        for field in (
            "front_left_tyre_pressure",
            "front_right_tyre_pressure",
            "rear_left_tyre_pressure",
            "rear_right_tyre_pressure",
        ):
            setup[field] = round(float(setup[field]) + 0.2, 1)
    elif profile == "race":
        setup["on_throttle"] = max(10, int(setup["on_throttle"]) - 2)
        setup["rear_suspension"] = max(1, int(setup["rear_suspension"]) - 1)
        setup["rear_anti_roll_bar"] = max(1, int(setup["rear_anti_roll_bar"]) - 1)
        setup["brake_pressure"] = min(99, int(setup["brake_pressure"]))
        for field in (
            "front_left_tyre_pressure",
            "front_right_tyre_pressure",
            "rear_left_tyre_pressure",
            "rear_right_tyre_pressure",
        ):
            setup[field] = round(float(setup[field]) - 0.1, 1)
    return setup


def setup_effects(setup: dict[str, Any], track_id: int) -> dict[str, Any]:
    """Estimate setup interaction with pace, heat and per-wheel wear.

    Effects are deliberately bounded and transparent. Personal measured wear is
    still the primary source; this model supplies a prior and explains why two
    setups should not share the same tyre-life assumption.
    """
    baseline = foundational_setup(track_id, "hybrid")

    def value(name: str) -> float:
        return float(setup.get(name, baseline.get(name, 0.0)) or 0.0)

    front_wing_delta = value("front_wing") - float(baseline["front_wing"])
    rear_wing_delta = value("rear_wing") - float(baseline["rear_wing"])
    balance_delta = front_wing_delta - rear_wing_delta
    on_diff_delta = value("on_throttle") - float(baseline["on_throttle"])
    off_diff_delta = value("off_throttle") - float(baseline["off_throttle"])
    front_arb_delta = value("front_anti_roll_bar") - float(
        baseline["front_anti_roll_bar"]
    )
    rear_arb_delta = value("rear_anti_roll_bar") - float(
        baseline["rear_anti_roll_bar"]
    )
    front_pressure_delta = (
        value("front_left_tyre_pressure")
        + value("front_right_tyre_pressure")
    ) / 2 - (
        float(baseline["front_left_tyre_pressure"])
        + float(baseline["front_right_tyre_pressure"])
    ) / 2
    rear_pressure_delta = (
        value("rear_left_tyre_pressure")
        + value("rear_right_tyre_pressure")
    ) / 2 - (
        float(baseline["rear_left_tyre_pressure"])
        + float(baseline["rear_right_tyre_pressure"])
    ) / 2
    front_camber_delta = abs(value("front_camber")) - abs(
        float(baseline["front_camber"])
    )
    rear_camber_delta = abs(value("rear_camber")) - abs(
        float(baseline["rear_camber"])
    )
    front_toe_delta = value("front_toe") - float(baseline["front_toe"])
    rear_toe_delta = value("rear_toe") - float(baseline["rear_toe"])

    front_wear = (
        1.0
        - 0.006 * front_wing_delta
        + 0.012 * max(0.0, balance_delta)
        + 0.010 * front_arb_delta
        + 0.045 * front_pressure_delta
        + 0.035 * front_camber_delta
        + 0.60 * front_toe_delta
        + 0.004 * max(0.0, off_diff_delta)
    )
    rear_wear = (
        1.0
        - 0.005 * rear_wing_delta
        + 0.008 * max(0.0, -balance_delta)
        + 0.014 * rear_arb_delta
        + 0.050 * rear_pressure_delta
        + 0.040 * rear_camber_delta
        + 0.45 * rear_toe_delta
        + 0.006 * max(0.0, on_diff_delta)
    )
    front_wear = max(0.78, min(1.30, front_wear))
    rear_wear = max(0.78, min(1.35, rear_wear))

    drag_delta_s = 0.012 * (front_wing_delta + rear_wing_delta)
    tyre_pace_delta_s = (
        0.020 * abs(front_pressure_delta)
        + 0.020 * abs(rear_pressure_delta)
        + 0.004 * abs(balance_delta)
    )
    rotation_delta = 0.025 * balance_delta - 0.010 * off_diff_delta
    traction_delta = -0.020 * on_diff_delta - 0.020 * rear_arb_delta

    notes: list[str] = []
    if rear_wear > 1.08:
        notes.append("The setup is expected to increase rear-tyre wear.")
    elif rear_wear < 0.94:
        notes.append("The setup should protect the rear tyres relative to baseline.")
    if front_wear > 1.08:
        notes.append("The setup is expected to increase front scrub/wear.")
    elif front_wear < 0.94:
        notes.append("The setup should protect the front tyres relative to baseline.")
    if drag_delta_s > 0.10:
        notes.append("The wing level carries a meaningful straight-line cost.")
    elif drag_delta_s < -0.10:
        notes.append("The wing level prioritizes straight-line speed over corner support.")

    return {
        "track_archetype": track_archetype(track_id),
        "baseline": baseline,
        "front_wear_multiplier": round(front_wear, 3),
        "rear_wear_multiplier": round(rear_wear, 3),
        "wheel_wear_multipliers": [
            round(front_wear, 3),
            round(front_wear, 3),
            round(rear_wear, 3),
            round(rear_wear, 3),
        ],
        "lap_time_delta_s": round(drag_delta_s + tyre_pace_delta_s, 3),
        "rotation_effect": round(rotation_delta, 3),
        "traction_effect": round(traction_delta, 3),
        "notes": notes,
    }
