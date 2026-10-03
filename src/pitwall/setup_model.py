from __future__ import annotations

from copy import deepcopy
from typing import Any

from .setup_reference import reference_for_track

_FALLBACK_TRACKS = {
    0: "Melbourne",
    1: "Paul Ricard",
    2: "Shanghai",
    3: "Sakhir",
    4: "Catalunya",
    5: "Monaco",
    6: "Montreal",
    7: "Silverstone",
    8: "Hockenheim",
    9: "Hungaroring",
    10: "Spa",
    11: "Monza",
    12: "Singapore",
    13: "Suzuka",
    14: "Abu Dhabi",
    15: "Texas",
    16: "Brazil",
    17: "Austria",
    18: "Sochi",
    19: "Mexico",
    20: "Baku",
    21: "Sakhir Short",
    22: "Silverstone Short",
    23: "Texas Short",
    24: "Suzuka Short",
    25: "Hanoi",
    26: "Zandvoort",
    27: "Imola",
    28: "Portimão",
    29: "Jeddah",
    30: "Miami",
    31: "Las Vegas",
    32: "Losail",
    39: "Silverstone (Reverse)",
    40: "Austria (Reverse)",
    41: "Zandvoort (Reverse)",
    42: "Madrid",
}

try:
    from f1.packets import TRACKS as _PACKAGE_TRACKS
except ImportError:  # pragma: no cover - defensive offline fallback
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


def track_name(track_id: int) -> str:
    return str(TRACKS.get(int(track_id), f"Track {track_id}"))


def track_archetype(track_id: int) -> str:
    return TRACK_ARCHETYPE_BY_NAME.get(track_name(track_id), "balanced")


def foundational_setup(
    track_id: int, profile: str, *, condition: str = "dry", style: str = "stable"
) -> dict[str, float | int]:
    """Return the published reference without inventing profile offsets.

    Profile differences are used only when the source publishes them. A race
    reference does not become a measured time-trial setup by adding arbitrary
    clicks. Missing references return an empty mapping; the advisor exposes
    the catalogue's explicit reason.
    """
    return deepcopy(
        reference_for_track(
            track_id, condition=condition, style=style, profile=profile
        ).get("setup", {})
    )


def setup_effects(setup: dict[str, Any], track_id: int) -> dict[str, Any]:
    """Leave pace and wear unchanged until setup effects have measured support.

    These neutral values preserve the strategy API. They mean no calibrated
    correction is available, not that every setup has identical performance.
    Measured stint pace and per-wheel wear remain the strategy's evidence.
    """
    return {
        "track_archetype": track_archetype(track_id),
        "baseline": foundational_setup(track_id, "hybrid"),
        "calibrated": False,
        "source": "uncalibrated_neutral",
        "front_wear_multiplier": 1.0,
        "rear_wear_multiplier": 1.0,
        "wheel_wear_multipliers": [1.0, 1.0, 1.0, 1.0],
        "lap_time_delta_s": 0.0,
        "rotation_effect": 0.0,
        "traction_effect": 0.0,
        "notes": [
            (
                "Setup pace and tyre-wear effects are not calibrated from matched laps. "
                "No numerical setup gain or wear correction is applied; use measured "
                "stint evidence and a controlled A/B test."
            )
        ],
    }
