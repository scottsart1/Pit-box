"""Wet-weather strategy: what the track is doing, not what the sky is doing.

Rain intensity is an *input* to a race track; grip is the *state*. Deciding a
tyre from the weather label alone ("it is raining, fit wets") gets both edges of
a wet race wrong, because the track lags the sky in both directions:

* rain starts and the surface is still quick on slicks for a lap or two;
* rain stops and the surface stays wet long after the cloud has gone;
* a shower that ends inside the remaining distance never justifies the stop it
  would justify in a race with an hour to run.

So this module carries one continuous state variable — ``wetness`` — estimated
from four independent channels and projected forward over the laps that are
actually left, and prices every available compound against that projection. The
recommendation is then whichever tyre spends the least time over the rest of
the race, which is the question the driver is really asking.

The wetness scale is not a claim about water depth. It is an internal
coordinate anchored to the two figures wet-weather strategy is actually run on:

* ``WETNESS_SLICK_INTER`` — the slick/intermediate crossover, conventionally
  taken as the point where the achievable lap time reaches 112% of dry pace;
* ``WETNESS_INTER_WET`` — the intermediate/full-wet crossover, at 118%.

``test_rain_model.py`` pins both, so the curves below can be reshaped freely as
long as the crossovers still land where wet races are actually called.

Lap-time evidence is the channel that outranks the others, because it measures
the track instead of predicting it. Two things make it usable rather than
misleading. A lap can be slow for reasons that have nothing to do with weather,
so a lap whose loss sits in one sector while the others hold dry pace is read as
a mistake and dropped, not as rain. And the field is the control group: when
half a dozen cars lose time together the track changed, and when only one car
did, that car did.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from itertools import pairwise
from statistics import median
from typing import Any, TypedDict

from .tyre_learning import exclusion_reason, finite

# --- The wetness scale -----------------------------------------------------
# Anchored to the two crossovers wet strategy is called on. See the module
# docstring; the calibration is asserted in the tests.
WETNESS_DRY = 0.0
WETNESS_SLICK_INTER = 0.35
WETNESS_INTER_WET = 0.75
WETNESS_FLOODED = 1.0

# Unavoidable cost of a wet surface, as a fraction of dry lap time, for a car on
# the *ideal* tyre for that wetness. Piecewise linear through anchors rather
# than a fitted curve: every point here is a judgement, and a curve would only
# disguise that. The 0.35 and 0.75 anchors are set so that the total penalty —
# surface plus the residual tyre mismatch at the crossover — is exactly 12% and
# 18% of dry pace.
_TRACK_PENALTY_ANCHORS: tuple[tuple[float, float], ...] = (
    (0.00, 0.00000),
    (0.15, 0.05000),
    (0.35, 0.10876),
    (0.55, 0.14000),
    (0.75, 0.16125),
    (1.00, 0.25000),
)

# Cost of running the wrong tyre for the conditions, again as a fraction of dry
# lap time. Zero for the right tyre at the wetness it was designed for.
#
# The dry-track figure for an intermediate is the familiar one: roughly 8% off,
# or 7 s on a 90 s lap. Slicks are flat in the damp and then fall off a cliff,
# which is why the exponent is 4 rather than 2 — a quadratic makes slicks merely
# poor in a downpour when they are in fact undriveable.
#
# Full wets are deliberately expensive everywhere short of standing water. That
# is not a thumb on the scale: it is why the tyre is almost never fitted in
# practice, and a model that makes wets merely a little slower than inters in
# heavy rain will keep calling a stop for them that no pit wall would make.
_SLICK_MISMATCH_K = 0.749
_SLICK_MISMATCH_POWER = 4.0
_INTER_DRY_MISMATCH = 0.085
_INTER_IDEAL_WETNESS = 0.55
_INTER_FLOOD_MISMATCH = 0.0949
_WET_DRY_MISMATCH = 0.300

# Where the surface settles if the current sky keeps doing what it is doing.
# EA's rain percentage is the chance of rain, not its intensity. It weights
# future scenarios only; each weather category keeps its physical equilibrium.
# "Heavy rain" deliberately settles just *below* the intermediate/full-wet
# crossover. It is a common weather state and the tyre teams reach for in it is
# the intermediate; putting its equilibrium above the crossover would have the
# model calling full wets in conditions where no pit wall fits them. Getting to
# full wets takes a storm, or a driver reporting standing water.
_EQUILIBRIUM_WETNESS = {
    "clear": 0.0,
    "light cloud": 0.0,
    "overcast": 0.0,
    "light rain": 0.52,
    "heavy rain": 0.72,
    "storm": 0.95,
}

# The track soaks fast and dries slowly, which is the whole reason a weather
# label is not a tyre call. These are per-lap fractions of the remaining gap to
# equilibrium: a track reaches most of the way to soaked in about three laps and
# takes nine or so to dry out.
_SOAK_RATE_PER_LAP = 0.45
_DRY_RATE_PER_LAP = 0.16

# Drying is a heat-and-traffic process. A hot surface boils the line off; a
# packed field sweeps it. Both are bounded well short of the point where they
# would dominate the estimate on their own.
_DRY_TEMP_REFERENCE_C = 25.0
_DRY_TEMP_SENSITIVITY = 0.035
_DRY_TEMP_BOUNDS = (0.55, 1.60)
_DRY_TRAFFIC_BOUNDS = (0.80, 1.25)

WET_COMPOUNDS = frozenset({"INTER", "WET"})
DRY_COMPOUNDS = frozenset({"SOFT", "MEDIUM", "HARD"})

# Cars whose lap times are no longer evidence about the track. Matched on the
# label rather than the raw result-status id so a car with no status packet yet
# still counts as running, which is what it is.
RETIRED_RESULT_LABELS = frozenset(
    {"retired", "did not finish", "disqualified", "not classified"}
)

# A lap whose time loss is concentrated in one sector is a mistake, not weather:
# rain slows every sector, a spin slows one. Expressed as a fraction of the
# reference sector time.
_INCIDENT_SECTOR_EXCESS = 0.06

# How much of the fresh-tyre out-lap is lost to a cold wet tyre, in seconds.
# Charged against every compound change so a marginal switch does not look free.
WET_CHANGE_WARMUP_S = 2.5

# Pace evidence outranks the forecast, but only once there is enough of it.
_PACE_FULL_CONFIDENCE_SAMPLES = 6

# Converts a wetness error into the lap-time error it would cause, so the fit's
# prior and its observations are weighed in the same units. Without it the prior
# term — which ranges over the whole 0..1 wetness scale — swamps residuals that
# live in hundredths of a lap, and the weather forecast wins every argument with
# the cars actually on track.
_PRIOR_PACE_SCALE = 0.20

# Observations this far apart in predicted lap time cannot describe one track.
_PACE_FIT_TOLERANCE = 0.05

# How much the gap between the compound groups counts against each car's
# absolute pace. Every absolute ratio is only as good as the dry benchmark it
# was measured against, and that benchmark carries whatever fuel load, traffic
# and track evolution the car had on its reference lap. The gap between two
# groups lapping the same track in the same minute carries none of that, so it
# is the stronger measurement and is weighted accordingly.
_SPLIT_GAP_WEIGHT = 12.0


def _interpolate(anchors: Sequence[tuple[float, float]], x: float) -> float:
    if x <= anchors[0][0]:
        return anchors[0][1]
    for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
        if x <= x1:
            span = x1 - x0
            return y0 if span <= 0 else y0 + (y1 - y0) * (x - x0) / span
    return anchors[-1][1]


def clamp_wetness(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def track_pace_penalty(wetness: float) -> float:
    """Lap time lost to the surface itself, as a fraction of dry pace."""
    return _interpolate(_TRACK_PENALTY_ANCHORS, clamp_wetness(wetness))


def compound_mismatch(compound: str, wetness: float) -> float:
    """Lap time lost to running ``compound`` at this wetness, as a fraction."""
    wet = clamp_wetness(wetness)
    name = str(compound).upper()
    if name == "WET":
        return _WET_DRY_MISMATCH * (1.0 - wet) ** 2
    if name == "INTER":
        if wet <= _INTER_IDEAL_WETNESS:
            gap = (_INTER_IDEAL_WETNESS - wet) / _INTER_IDEAL_WETNESS
            return _INTER_DRY_MISMATCH * gap**2
        flood = (wet - _INTER_IDEAL_WETNESS) / (1.0 - _INTER_IDEAL_WETNESS)
        return _INTER_FLOOD_MISMATCH * flood**2
    # Everything else is a slick. The dry compounds differ from each other in
    # ways COMPOUND_DELTA already prices; in the wet they behave alike, and
    # charging them twice for the same difference would double-count it.
    return _SLICK_MISMATCH_K * wet**_SLICK_MISMATCH_POWER


def lap_penalty_fraction(compound: str, wetness: float) -> float:
    """Total lap time lost at this wetness on this tyre, against dry pace."""
    return track_pace_penalty(wetness) + compound_mismatch(compound, wetness)


def best_compound_for(wetness: float, available: Iterable[str] | None = None) -> str:
    candidates = [str(item).upper() for item in (available or ())] or [
        "MEDIUM",
        "INTER",
        "WET",
    ]
    return min(candidates, key=lambda item: compound_mismatch(item, wetness))


def equilibrium_wetness(weather: str, rain_pct: float | None = None) -> float:
    """Where the surface settles if the observed weather category persists.

    ``rain_pct`` is accepted for compatibility with older callers and saved lap
    data, but deliberately ignored. EA's ``m_rainPercentage`` is a forecast
    probability and cannot supply a missing observation or scale water depth.
    """
    return _EQUILIBRIUM_WETNESS.get(str(weather).strip().lower(), 0.0)


def drying_factor(track_temp_c: float | None, active_cars: int = 0) -> float:
    """How fast the line clears, relative to a 25 °C track and an empty one."""
    temp = finite(track_temp_c)
    scale = 1.0
    if temp is not None and temp > 0:
        scale = 1.0 + (temp - _DRY_TEMP_REFERENCE_C) * _DRY_TEMP_SENSITIVITY
        scale = max(_DRY_TEMP_BOUNDS[0], min(_DRY_TEMP_BOUNDS[1], scale))
    traffic = 0.80 + max(0, int(active_cars)) * 0.020
    traffic = max(_DRY_TRAFFIC_BOUNDS[0], min(_DRY_TRAFFIC_BOUNDS[1], traffic))
    return scale * traffic


def step_wetness(
    current: float,
    target: float,
    *,
    laps: float = 1.0,
    track_temp_c: float | None = None,
    active_cars: int = 0,
) -> float:
    """Move the surface toward ``target`` over ``laps``, soaking fast, drying slow."""
    wet = clamp_wetness(current)
    goal = clamp_wetness(target)
    steps = max(0.0, float(laps))
    if steps <= 0 or abs(goal - wet) < 1e-6:
        return wet
    if goal > wet:
        rate = _SOAK_RATE_PER_LAP
    else:
        rate = _DRY_RATE_PER_LAP * drying_factor(track_temp_c, active_cars)
    rate = max(0.0, min(0.95, rate))
    # Exponential approach, so a half-lap step and two of them agree.
    remaining = (1.0 - rate) ** steps
    return clamp_wetness(goal + (wet - goal) * remaining)


# --- Channel B: the surface, integrated from the weather it has seen --------


def wetness_from_history(
    laps: Sequence[dict[str, Any]],
    *,
    weather: str,
    rain_pct: float | None = None,
    track_temp_c: float | None,
    active_cars: int = 0,
) -> tuple[float, int]:
    """Integrate soak and drying across the weather each completed lap saw.

    Returns the estimate and how many laps of weather went into it. With no lap
    history at all the honest answer is the current equilibrium, which is what a
    weather label on its own would have said — the point being that one lap of
    history is already enough to start disagreeing with it.

    ``rain_pct`` remains accepted for compatibility, but is not an observation.
    """
    samples = [lap for lap in laps if lap.get("weather")]
    current_target = equilibrium_wetness(weather)
    if not samples:
        return current_target, 0
    # Seed from the oldest lap's own conditions rather than from dry, so a race
    # that was already wet when the history window opens does not have to soak
    # from zero all over again.
    first = samples[0]
    wet = equilibrium_wetness(str(first.get("weather", "")))
    for lap in samples:
        target = equilibrium_wetness(str(lap.get("weather", "")))
        wet = step_wetness(
            wet,
            target,
            track_temp_c=lap.get("track_temp_c", track_temp_c),
            active_cars=active_cars,
        )
    # Then one more step for the lap in progress, which has no record yet.
    wet = step_wetness(
        wet, current_target, track_temp_c=track_temp_c, active_cars=active_cars
    )
    return wet, len(samples)


# --- Channel C: the lap times ----------------------------------------------


def _sector_shape_is_an_incident(
    lap: dict[str, Any], reference_sectors: Sequence[float] | None
) -> bool:
    """True when the lap's loss sits in one sector while the others held pace.

    Weather slows a whole lap. A spin, a lock-up or a trip through the gravel
    slows one sector and leaves the rest at dry pace, so reading that lap as
    evidence of rain is how a dry race ends up on intermediates.
    """
    if not reference_sectors or len(reference_sectors) != 3:
        return False
    sectors = [finite(lap.get(key)) for key in ("s1_ms", "s2_ms", "s3_ms")]
    if any(value is None or value <= 0 for value in sectors):
        return False
    ratios = [
        float(value) / float(ref)
        for value, ref in zip(sectors, reference_sectors)
        if ref and ref > 0
    ]
    if len(ratios) != 3:
        return False
    return (max(ratios) - median(ratios)) > _INCIDENT_SECTOR_EXCESS


def _reference_sectors(laps: Sequence[dict[str, Any]]) -> list[float] | None:
    columns: list[list[float]] = [[], [], []]
    for lap in laps:
        values = [finite(lap.get(key)) for key in ("s1_ms", "s2_ms", "s3_ms")]
        if any(value is None or value <= 0 for value in values):
            continue
        for index, value in enumerate(values):
            columns[index].append(float(value))
    if any(len(column) < 2 for column in columns):
        return None
    return [float(median(column)) for column in columns]


def _neutralised(record: dict[str, Any]) -> bool:
    return (
        record.get("safety_car") not in (None, "none", "")
        or bool(record.get("red_flag_active"))
        or record.get("race_control_phase") in {
            "safety_car", "safety_car_ending", "vsc", "vsc_ending",
            "formation", "red_flag",
        }
        or "neutralised_lap" in (record.get("learning_exclusions") or [])
    )


def _field_pace_block_reason(state: dict[str, Any]) -> str | None:
    if _neutralised(state):
        return "neutralised_current_state"
    completed = state.get("completed_laps") or []
    if completed and _neutralised(completed[-1]):
        return "awaiting_clean_lap_after_neutralisation"
    return None


def player_pace_observation(
    state: dict[str, Any],
    dry_base_lap_s: float | None,
    *,
    incident_laps: Iterable[int] = (),
) -> dict[str, Any] | None:
    """The driver's own recent pace against their dry benchmark.

    Their own laps are the most immediate evidence and the least trustworthy —
    a driver on the limit in the wet produces a lap time that is partly the
    track and partly the last corner. Laps that were invalidated, run through
    the pit lane, reported as a mistake, or shaped like an incident are dropped
    first.

    What is left is read from the *latest* clean lap rather than a median of the
    last few, because a median across a window that straddles the arrival of
    rain reports the weather from before it. Smoothing here would hide exactly
    the change this channel exists to catch. Robustness comes instead from the
    filters above, from the sample count that scales this channel's confidence,
    and from the rest of the field being measured the same way — one car can
    have a moment, six cannot have it together.
    """
    laps = [lap for lap in state.get("completed_laps", []) if finite(lap.get("lap_time_ms"))]
    if not laps:
        return None
    dry = [
        lap
        for lap in laps
        if equilibrium_wetness(str(lap.get("weather", ""))) <= 0.0
        and exclusion_reason(lap) is None
    ]
    # The benchmark has to be a *dry* lap. Passing in the driver's current rolling
    # pace makes the ratio 1.0 by construction in a wet race — the channel then
    # reports "the track is exactly as wet as it is", which is no evidence at
    # all, and worse, drags the joint fit toward dry.
    benchmark = "dry_laps"
    if dry:
        base: float | None = float(median([float(lap["lap_time_ms"]) / 1000.0 for lap in dry]))
    else:
        clean = [
            float(lap["lap_time_ms"]) / 1000.0
            for lap in laps
            if exclusion_reason(lap) is None
        ]
        if clean:
            base, benchmark = min(clean), "session_best"
        else:
            base, benchmark = finite(dry_base_lap_s), "rolling_pace"
    if base is None or base <= 0:
        return None
    reference_sectors = _reference_sectors(dry[-8:]) if dry else None
    excluded = {int(value) for value in incident_laps}
    usable: list[dict[str, Any]] = []
    dropped = 0
    for lap in laps[-3:]:
        if exclusion_reason(lap) is not None:
            dropped += 1
            continue
        if int(lap.get("lap_num", 0) or 0) in excluded:
            dropped += 1
            continue
        if _sector_shape_is_an_incident(lap, reference_sectors):
            dropped += 1
            continue
        usable.append(lap)
    observation = {
        "source": "player",
        "compound": str(state.get("tyre", {}).get("compound", "UNKNOWN")).upper(),
        "ratio": None,
        "samples": 0,
        "dropped_laps": dropped,
        "dry_base_lap_s": round(base, 3),
        "benchmark": benchmark,
    }
    if not usable:
        return observation
    latest = usable[-1]
    # The last completed lap may precede a tyre change. Current tyres cannot
    # retroactively identify the tyre that produced its lap time. Old captures
    # without lap-compound metadata retain the previous current-tyre fallback.
    observation["compound"] = str(latest.get("compound") or observation["compound"]).upper()
    observation["ratio"] = round(float(latest["lap_time_ms"]) / 1000.0 / base, 4)
    observation["samples"] = len(usable)
    observation["recent_median_ratio"] = round(
        float(median(float(lap["lap_time_ms"]) / 1000.0 for lap in usable)) / base, 4
    )
    return observation


def _driver_compound_at(driver: dict[str, Any], lap_num: int) -> str:
    for stint in driver.get("tyre_stints", []) or []:
        start = int(stint.get("start_lap", 0) or 0)
        end = int(stint.get("end_lap", 0) or 0)
        if start <= lap_num <= (end or lap_num):
            return str(stint.get("compound", "UNKNOWN")).upper()
    return str(driver.get("tyre_compound", "UNKNOWN")).upper()


def field_pace_observations(
    state: dict[str, Any],
    *,
    dry_until_lap: int | None,
    sample_cars: int = 8,
) -> list[dict[str, Any]]:
    """What the rest of the field is doing, grouped by the tyre they are on.

    This is the reference the pit wall actually uses. One car losing four
    seconds is a driver having a moment; six cars losing four seconds is the
    track, and a group on intermediates lapping quicker than a group on slicks
    is the crossover being observed rather than predicted.

    Cars in the pit lane, out of the race, or on an out-lap are excluded, and
    each car's pace is measured against its own dry benchmark so a slow car in
    the dry does not read as a wet track.
    """
    observations: list[dict[str, Any]] = []
    # The game's rival history contains lap times but no historical SC/VSC
    # flags. A field-wide slowdown is therefore not independent evidence while
    # neutralised, or during the first lap after a recorded interruption. This
    # conservative gate cannot reconstruct unknown older per-rival flags.
    if _field_pace_block_reason(state):
        return observations
    player_idx = int(state.get("player_car_index", -1))
    for driver in state.get("drivers", []):
        if not driver.get("active") or int(driver.get("car_idx", -1)) == player_idx:
            continue
        if driver.get("restricted") or driver.get("pit_lane_timer_active"):
            continue
        if str(driver.get("result_label", "")) in RETIRED_RESULT_LABELS:
            continue
        history = [
            lap
            for lap in driver.get("lap_history", []) or []
            if finite(lap.get("lap_ms")) and float(lap.get("lap_ms", 0)) > 0
        ]
        if len(history) < 2:
            continue
        dry_laps = [
            float(lap["lap_ms"]) / 1000.0
            for lap in history
            if dry_until_lap
            and 1 < int(lap.get("lap_num", 0) or 0) <= dry_until_lap
            and int(lap.get("valid_flags", 0) or 0) & 1
        ]
        if len(dry_laps) >= 2:
            base = float(median(dry_laps))
            benchmark = "dry_window"
        else:
            # No lap in the recorded window is known to have been dry, which is
            # the normal state of a race that was already wet when the history
            # begins — and precisely when the field is most worth reading. Fall
            # back to the car's own fastest lap. If that lap was itself wet the
            # ratio understates the track, so this benchmark can only ever
            # report the surface as drier than it is; the halved confidence
            # further down is what keeps it from asserting a dry track on its
            # own.
            valid = [
                float(lap["lap_ms"]) / 1000.0
                for lap in history
                if int(lap.get("valid_flags", 0) or 0) & 1
                and float(lap.get("lap_ms", 0) or 0) > 0
            ]
            if len(valid) < 2:
                continue
            base = min(valid)
            benchmark = "session_best"
        recent = [
            lap
            for lap in history[-3:]
            if int(lap.get("valid_flags", 0) or 0) & 1
            and int(lap.get("lap_num", 0) or 0) > (dry_until_lap or 0)
        ]
        if not recent:
            continue
        # The latest lap, for the same reason the player's is: averaging across
        # the lap the weather changed reports the weather before it. Each car
        # contributes one current reading and the robustness comes from there
        # being several of them.
        latest = recent[-1]
        latest_lap = int(latest.get("lap_num", 0) or 0)
        observations.append(
            {
                "source": "field",
                "car_idx": int(driver.get("car_idx", -1)),
                "name": str(driver.get("name", "")),
                "compound": _driver_compound_at(driver, latest_lap),
                "ratio": round((float(latest["lap_ms"]) / 1000.0) / base, 4),
                "samples": len(recent),
                "dry_base_lap_s": round(base, 3),
                "benchmark": benchmark,
            }
        )
    # A stable, representative sample rather than a random one: the slowest and
    # fastest readings are the ones most likely to be traffic or a mistake.
    observations.sort(key=lambda item: float(item["ratio"]))
    if len(observations) > sample_cars:
        start = (len(observations) - sample_cars) // 2
        observations = observations[start : start + sample_cars]
    return observations


def compound_split(observations: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """Observed pace difference between the compounds the field is running.

    When cars are split across compounds this needs no model at all: the two
    groups are lapping the same track in the same conditions, so the difference
    between their medians *is* the crossover, measured. It is the single most
    reliable signal in a wet race and the one a weather-driven call never sees.
    """
    groups: dict[str, list[float]] = {}
    for item in observations:
        ratio = finite(item.get("ratio"))
        if ratio is None:
            continue
        key = "WET" if str(item["compound"]).upper() in WET_COMPOUNDS else "DRY"
        groups.setdefault(str(item["compound"]).upper(), []).append(ratio)
        groups.setdefault(f"_{key}", []).append(ratio)
    wet_side = groups.get("_WET", [])
    dry_side = groups.get("_DRY", [])
    if len(wet_side) < 2 or len(dry_side) < 2:
        return None
    wet_median = float(median(wet_side))
    dry_median = float(median(dry_side))
    return {
        "wet_shod_ratio": round(wet_median, 4),
        "slick_shod_ratio": round(dry_median, 4),
        # Positive means the wet-tyre runners are quicker.
        "wet_advantage_fraction": round(dry_median - wet_median, 4),
        "wet_shod_cars": len(wet_side),
        "slick_shod_cars": len(dry_side),
        "inter_cars": len(groups.get("INTER", [])),
        "full_wet_cars": len(groups.get("WET", [])),
    }


def fit_wetness(
    observations: Sequence[dict[str, Any]],
    prior: float,
    *,
    prior_weight: float = 1.0,
) -> tuple[float, float, int]:
    """Fit one wetness to every car's measured pace at once.

    Each observation says "a car on this compound is lapping at this fraction of
    its dry pace". A single wetness has to explain all of them, and when the
    field is split across compounds only a narrow band of wetness can — which is
    what makes a split field so much more informative than a full one. The prior
    from the weather dynamics keeps the fit anchored when the evidence is thin
    or, for a car on full wets, genuinely ambiguous.

    Returns the estimate, a 0..1 confidence, and the number of laps behind it.
    """
    usable = [item for item in observations if finite(item.get("ratio")) is not None]
    samples = sum(int(item.get("samples", 1) or 1) for item in usable)
    if not usable:
        return clamp_wetness(prior), 0.0, 0
    # When the field is split across compounds, how far apart the two groups are
    # is a measurement in its own right — and a better one than either group's
    # absolute pace, because it is taken on the same laps of the same track and
    # so cannot inherit an error in anybody's dry benchmark.
    split = compound_split(usable)
    gap_pair: tuple[str, str] | None = None
    if split:
        gap_pair = (
            "MEDIUM",
            "WET" if int(split["full_wet_cars"]) > int(split["inter_cars"]) else "INTER",
        )

    best_wetness = clamp_wetness(prior)
    best_error = math.inf
    best_fit_error = math.inf
    for step in range(0, 101):
        candidate = step / 100.0
        # Both terms in lap-time units; see _PRIOR_PACE_SCALE.
        prior_error = prior_weight * (_PRIOR_PACE_SCALE * (candidate - prior)) ** 2
        fit_error = 0.0
        for item in usable:
            predicted = 1.0 + lap_penalty_fraction(str(item["compound"]), candidate)
            weight = min(3.0, float(item.get("samples", 1) or 1))
            fit_error += weight * (predicted - float(item["ratio"])) ** 2
        if split and gap_pair:
            predicted_gap = lap_penalty_fraction(
                gap_pair[0], candidate
            ) - lap_penalty_fraction(gap_pair[1], candidate)
            fit_error += _SPLIT_GAP_WEIGHT * (
                predicted_gap - float(split["wet_advantage_fraction"])
            ) ** 2
        error = prior_error + fit_error
        if error < best_error:
            best_error, best_fit_error, best_wetness = error, fit_error, candidate
    confidence = min(1.0, samples / _PACE_FULL_CONFIDENCE_SAMPLES)
    # Observations that no single wetness can explain are not evidence of
    # anything; they are noise, traffic, or cars driving to different targets.
    # Only the fit residual judges that — including the prior here would let a
    # disagreeing forecast discredit measurements that agree with each other.
    residual = math.sqrt(max(0.0, best_fit_error) / max(1, len(usable)))
    confidence *= max(0.0, 1.0 - residual / _PACE_FIT_TOLERANCE)
    return best_wetness, max(0.0, min(1.0, confidence)), samples


# --- Channel D: the driver -------------------------------------------------

# What the driver says about the surface, mapped onto the same scale. These are
# the phrases a driver actually uses on the radio, not a vocabulary they have to
# learn. Confidence is below 1.0 throughout: a driver reports the corner they
# just took, and the pit wall is asking about the lap.
GRIP_REPORT_WETNESS = {
    "flooded": (0.95, 0.85),
    "soaked": (0.72, 0.75),
    "damp": (0.38, 0.65),
    "drying": (0.22, 0.70),
    "dry_line": (0.10, 0.80),
}

_GRIP_FEEDBACK_DECAY_LAPS = 4.0


def driver_grip_observation(state: dict[str, Any]) -> dict[str, Any] | None:
    """The driver's read on the surface, faded out over the laps since."""
    feedback = state.get("driver_grip_feedback", {}) or {}
    category = str(feedback.get("category", ""))
    anchor = GRIP_REPORT_WETNESS.get(category)
    reported_lap = int(feedback.get("lap", 0) or 0)
    if not anchor or reported_lap <= 0:
        return None
    age = max(0, int(state.get("current_lap", 0) or 0) - reported_lap)
    decay = max(0.0, 1.0 - age / _GRIP_FEEDBACK_DECAY_LAPS)
    if decay <= 0.0:
        return None
    wetness, confidence = anchor
    weight = confidence * decay * max(0.0, min(1.0, float(feedback.get("confidence", 1.0))))
    return {
        "category": category,
        "wetness": wetness,
        "weight": round(weight, 3),
        "lap": reported_lap,
        "laps_ago": age,
    }


# --- Putting it together ---------------------------------------------------


def estimate_wetness(
    state: dict[str, Any],
    *,
    dry_base_lap_s: float | None = None,
) -> dict[str, Any]:
    """Blend every channel into one read on the surface, with its evidence."""
    weather = str(state.get("weather", "Unknown"))
    rain_pct = _current_rain_probability(state)
    track_temp = finite(state.get("track_temp_c"))
    active_cars = int(state.get("active_cars", 0) or 0)
    laps = list(state.get("completed_laps", []))

    declared = equilibrium_wetness(weather)
    dynamic, history_laps = wetness_from_history(
        laps,
        weather=weather,
        track_temp_c=track_temp,
        active_cars=active_cars,
    )

    dry_until = _last_dry_lap(laps)
    observations: list[dict[str, Any]] = []
    player = player_pace_observation(
        state, dry_base_lap_s, incident_laps=_incident_laps(state)
    )
    if (
        player
        and player.get("ratio") is not None
        # A ratio against the driver's own rolling pace is 1.0 by construction
        # and says nothing about the track, so it is reported but never fitted.
        and player.get("benchmark") != "rolling_pace"
    ):
        observations.append(player)
    field = field_pace_observations(state, dry_until_lap=dry_until)
    observations.extend(field)

    measured, pace_confidence, pace_samples = fit_wetness(observations, dynamic)
    # A benchmark taken from a session that may never have been dry is weaker
    # evidence than one taken from laps known to be dry: if every lap of the race
    # so far was wet, every car's best lap was wet too and the ratios understate
    # the track. Halved rather than discarded, because it is still a real
    # measurement of a real change.
    if observations and all(
        item.get("benchmark") == "session_best" for item in observations
    ):
        pace_confidence *= 0.5
    grip = driver_grip_observation(state)

    # The dynamics carry the estimate until the lap times can outvote them, and
    # the driver nudges whatever the numbers settled on. Weighting rather than
    # overriding, so no single channel can run away with a race on its own.
    wetness = dynamic
    if pace_confidence > 0.0:
        wetness += (measured - dynamic) * min(0.85, pace_confidence)
    if grip:
        wetness += (grip["wetness"] - wetness) * min(0.45, float(grip["weight"]))
    wetness = clamp_wetness(wetness)

    # How wrong the declared conditions have proven about this track.
    #
    # Measured against the *surface model* rather than the raw weather label,
    # which matters: a track below the equilibrium its label implies is usually
    # just rain that has not soaked in yet, and correcting for that would cancel
    # the lag this module exists to model. The surface model already contains
    # the lag, so whatever the lap times still disagree with it about is a real
    # error in the equilibrium — this rain is not wetting this track the way the
    # label says. Carried into the projection, it stops a forecast dragging the
    # track back to a wetness the field is demonstrably not lapping at.
    settled_laps = _laps_at_current_conditions(laps, declared)
    bias = 0.0
    if pace_confidence >= 0.35:
        bias = (wetness - dynamic) * min(1.0, pace_confidence)

    return {
        "wetness": round(wetness, 3),
        "equilibrium_bias": round(bias, 3),
        "settled_laps": settled_laps,
        "declared_wetness": round(declared, 3),
        "dynamic_wetness": round(dynamic, 3),
        "measured_wetness": round(measured, 3) if pace_samples else None,
        "pace_confidence": round(pace_confidence, 3),
        "pace_samples": pace_samples,
        "history_laps": history_laps,
        "weather": weather,
        "rain_pct": rain_pct,
        "track_temp_c": int(track_temp) if track_temp is not None else None,
        "player_pace": player,
        "field_pace_cars": len(field),
        "field_pace_exclusion": _field_pace_block_reason(state),
        "field_reference_ratio": round(
            float(median([float(item["ratio"]) for item in field])), 4
        )
        if field
        else None,
        "compound_split": compound_split([*field, *( [player] if player and player.get("ratio") else [] )]),
        "driver_report": grip,
        "trend": _wetness_trend(laps, weather),
    }


def surface_is_wet(state: dict[str, Any]) -> bool:
    """Whether the track itself is past the slick crossover.

    The one predicate for "are we in a wet race", shared by everything that
    needs to gate on it, so a strategy hold and the call that would release it
    can never disagree about what wet means. Prefers the wetness the strategy
    engine already published this tick and falls back to the conditions when
    there is no snapshot yet.
    """
    published = (state.get("strategy", {}) or {}).get("weather_crossover") or {}
    if "wetness" in published:
        return float(published["wetness"] or 0.0) >= WETNESS_SLICK_INTER
    return (
        equilibrium_wetness(str(state.get("weather", "Unknown")))
        >= WETNESS_SLICK_INTER
    )


def _current_rain_probability(state: dict[str, Any]) -> int:
    """Offset-zero forecast chance, for reporting only; zero is a valid value.

    Never substitute a future sample for the present, or treat this forecast
    as the observed ``PacketSessionData.weather`` category.
    """
    for sample in state.get("weather_forecast", []) or []:
        if finite(sample.get("time_offset_min")) == 0:
            return int(max(0.0, min(100.0, finite(sample.get("rain_pct")) or 0.0)))
    return int(max(0.0, min(100.0, finite(state.get("rain_now_pct")) or 0.0)))


def _laps_at_current_conditions(
    laps: Sequence[dict[str, Any]], declared: float, tolerance: float = 0.08
) -> int:
    """How many trailing laps were run in the conditions on track now."""
    count = 0
    for lap in reversed(laps):
        equilibrium = equilibrium_wetness(str(lap.get("weather", "")))
        if abs(equilibrium - declared) > tolerance:
            break
        count += 1
    return count


def _last_dry_lap(laps: Sequence[dict[str, Any]]) -> int | None:
    """The last lap run in genuinely dry conditions, which is the benchmark."""
    dry = [
        int(lap.get("lap_num", 0) or 0)
        for lap in laps
        if equilibrium_wetness(str(lap.get("weather", ""))) <= 0.0
    ]
    return max(dry) if dry else None


def _incident_laps(state: dict[str, Any]) -> list[int]:
    return [
        int(item.get("lap", 0) or 0)
        for item in state.get("driver_lap_incidents", []) or []
        if int(item.get("lap", 0) or 0) > 0
    ]


def _wetness_trend(
    laps: Sequence[dict[str, Any]],
    weather: str,
) -> str:
    """Whether the surface is getting wetter or drying, from the last few laps."""
    if len(laps) < 2:
        return "steady"
    recent = laps[-3:]
    earlier = equilibrium_wetness(str(recent[0].get("weather", "")))
    now = equilibrium_wetness(weather)
    if now > earlier + 0.05:
        return "wetting"
    if now < earlier - 0.05:
        return "drying"
    return "steady"


class WetnessScenario(TypedDict):
    probability: float
    trajectory: list[float]


def project_wetness_scenarios(
    state: dict[str, Any],
    current_wetness: float,
    remaining_laps: int,
    base_lap_s: float,
    equilibrium_bias: float = 0.0,
) -> list[WetnessScenario]:
    """Project physical rain/no-rain outcomes, with probability kept separate.

    EA supplies marginal chances, not a temporal correlation model. Use one
    shared probability rank through the horizon: rain persists within a sample
    rather than being independently redrawn each lap. Splitting at every
    forecast probability preserves each marginal exactly with at most N+1
    paths, instead of an exponential tree. This persistence assumption is a
    modelling choice, not information provided by the game.

    Offset zero is a forecast too. The observed session weather supersedes it
    until a strictly future sample becomes due. Approximate forecasts add a
    separate persistence outcome, never a weakened version of the rain.
    """
    lap_count = max(0, int(remaining_laps))
    lap_minutes = max(0.2, float(base_lap_s) / 60.0)
    captured = finite(state.get("weather_forecast_session_time_s"))
    now = finite(state.get("session_time_s"))
    age_minutes = (
        max(0.0, now - captured) / 60.0
        if captured is not None and now is not None else 0.0
    )
    forecast = sorted(
        [
            {**sample, "due_min": float(sample["time_offset_min"]) - age_minutes}
            for sample in state.get("weather_forecast", []) or []
            if 0 < (finite(sample.get("time_offset_min")) or 0)
            <= lap_count * lap_minutes + age_minutes
        ],
        key=lambda item: float(item["due_min"]),
    )
    track_temp = finite(state.get("track_temp_c"))
    active_cars = int(state.get("active_cars", 0) or 0)
    now_target = equilibrium_wetness(str(state.get("weather", "Unknown")))

    def probability(sample: dict[str, Any]) -> float:
        pct = finite(sample.get("rain_pct"))
        if pct is None:
            # Older/synthetic samples without a chance retain their category.
            return 1.0 if equilibrium_wetness(str(sample.get("weather", ""))) > 0 else 0.0
        return max(0.0, min(1.0, pct / 100.0))

    def project(rank: float | None) -> list[float]:
        trajectory: list[float] = []
        wet = clamp_wetness(current_wetness)
        sample_index = 0
        current_sample = None
        for index in range(lap_count):
            cursor = index * lap_minutes
            end = (index + 1) * lap_minutes
            while (
                sample_index < len(forecast)
                and float(forecast[sample_index]["due_min"]) <= cursor
            ):
                current_sample = forecast[sample_index]
                sample_index += 1
            while cursor < end:
                boundary = end
                if sample_index < len(forecast):
                    boundary = min(end, float(forecast[sample_index]["due_min"]))
                target, sample_temp = now_target, track_temp
                if rank is not None and current_sample is not None:
                    if rank < probability(current_sample):
                        # Probability chooses a physical rain/no-rain path; it
                        # never scales the selected category's intensity.
                        target = (
                            equilibrium_wetness(str(current_sample.get("weather", "")))
                            or _EQUILIBRIUM_WETNESS["light rain"]
                        )
                    else:
                        target = 0.0
                    sample_temp = finite(current_sample.get("track_temp_c"))
                    if sample_temp is None:
                        sample_temp = track_temp
                # Integrate the old conditions until the actual forecast
                # boundary, then the new conditions for the remaining fraction
                # of the lap. A forecast at the flag cannot affect earlier laps.
                wet = step_wetness(
                    wet, clamp_wetness(target + equilibrium_bias),
                    laps=(boundary - cursor) / lap_minutes,
                    track_temp_c=sample_temp, active_cars=active_cars,
                )
                cursor = boundary
                while (sample_index < len(forecast)
                       and float(forecast[sample_index]["due_min"]) <= cursor):
                    current_sample = forecast[sample_index]
                    sample_index += 1
            trajectory.append(round(wet, 4))
        return trajectory

    if not forecast:
        return [{"probability": 1.0, "trajectory": project(None)}]

    trust = 1.0 if int(state.get("forecast_accuracy", 0) or 0) == 0 else 0.55
    boundaries = sorted({0.0, 1.0, *(probability(sample) for sample in forecast)})
    scenarios: list[WetnessScenario] = [
        {"probability": (upper - lower) * trust, "trajectory": project((lower + upper) / 2)}
        for lower, upper in pairwise(boundaries)
    ]
    if trust < 1.0:
        scenarios.append({"probability": 1.0 - trust, "trajectory": project(None)})
    return scenarios


def _mean_trajectory(scenarios: Sequence[WetnessScenario]) -> list[float]:
    """Display summary only: a mean surface cannot price nonlinear tyre costs."""
    if not scenarios:
        return []
    return [
        round(sum(item["probability"] * item["trajectory"][i] for item in scenarios), 4)
        for i in range(len(scenarios[0]["trajectory"]))
    ]


def project_wetness(
    state: dict[str, Any],
    current_wetness: float,
    remaining_laps: int,
    base_lap_s: float,
    equilibrium_bias: float = 0.0,
) -> list[float]:
    """Probability-weighted mean for display; use scenarios to price strategy."""
    return _mean_trajectory(
        project_wetness_scenarios(
            state, current_wetness, remaining_laps, base_lap_s, equilibrium_bias
        )
    )


def expected_lap_penalties(
    compounds: Iterable[str], scenarios: Sequence[WetnessScenario]
) -> dict[str, list[float]]:
    """E[tyre cost at wetness], not tyre cost at E[wetness]."""
    lap_count = len(scenarios[0]["trajectory"]) if scenarios else 0
    return {
        compound: [
            sum(
                item["probability"] * lap_penalty_fraction(compound, item["trajectory"][i])
                for item in scenarios
            )
            for i in range(lap_count)
        ]
        for compound in compounds
    }


def stint_cost_s(
    compound: str,
    trajectory: Sequence[float],
    base_lap_s: float,
) -> float:
    """Time lost to the weather over a projected stint, in seconds."""
    return sum(
        base_lap_s * lap_penalty_fraction(compound, wetness) for wetness in trajectory
    )


def _cumulative_costs(
    penalties: dict[str, list[float]], base_lap_s: float
) -> dict[str, list[float]]:
    """Running total of weather cost per compound, so any stint is two lookups.

    The box-lap search below prices every compound over every possible split of
    the remaining race. Done directly that is quadratic in race distance on a
    function the live engine calls several times a second; with prefix sums it
    is linear, and the search stops being something to ration.
    """
    table: dict[str, list[float]] = {}
    for compound, lap_penalties in penalties.items():
        running = 0.0
        column = [0.0]
        for penalty in lap_penalties:
            running += base_lap_s * penalty
            column.append(running)
        table[compound] = column
    return table


def evaluate(
    state: dict[str, Any],
    *,
    base_lap_s: float,
    remaining_laps: int,
    pit_loss_s: float,
    available_compounds: Iterable[str] | None = None,
    dry_base_lap_s: float | None = None,
    change_during_suspension: bool = False,
) -> dict[str, Any]:
    """Decide the tyre from projected race time, and say how sure that is.

    Every candidate compound is run over the same projected conditions for the
    laps that are left. A change pays the pit lane and a cold out-lap; staying
    out pays whatever the current tyre costs in the conditions that are coming.
    Whichever is cheaper wins, which is why a worsening shower with a dry
    forecast can still say stay out, and why a drying track says slicks before
    the rain has stopped.
    """
    reading = estimate_wetness(state, dry_base_lap_s=dry_base_lap_s or base_lap_s)
    wetness = float(reading["wetness"])
    current = str(state.get("tyre", {}).get("compound", "UNKNOWN")).upper()
    remaining = max(0, int(remaining_laps))
    scenarios = project_wetness_scenarios(
        state,
        wetness,
        remaining,
        base_lap_s,
        equilibrium_bias=float(reading.get("equilibrium_bias", 0.0) or 0.0),
    )
    trajectory = _mean_trajectory(scenarios)

    candidates = {str(item).upper() for item in (available_compounds or ())}
    # The caller's known stock is a constraint, including an explicitly empty
    # list. Only an omitted inventory permits the legacy conditional fallback.
    if available_compounds is None:
        candidates.update({"INTER", "WET"})
    if current and current != "UNKNOWN":
        candidates.add(current)
    penalties = expected_lap_penalties(sorted(candidates | {current}), scenarios)
    costs = _cumulative_costs(penalties, base_lap_s)
    overhead = (
        # A change made during a suspension is free: the field is stationary and
        # nothing is being raced while the tyre goes on.
        0.0
        if change_during_suspension
        else max(0.0, float(pit_loss_s)) + WET_CHANGE_WARMUP_S
    )
    stay_cost = costs[current][len(trajectory)] if current in costs else 0.0

    def change_cost(compound: str, box_offset: int) -> float:
        """Run to the box lap on the current tyre, then to the flag on the new one.

        ``box_offset`` 0 is a stop at the end of the lap in progress. That lap is
        run on the current compound either way, which is what stops a stop being
        called into the flag: with one lap left a change buys no laps at all and
        costs the whole pit lane.
        """
        split = 0 if change_during_suspension else min(box_offset + 1, len(trajectory))
        return (
            costs[current][split]
            + (costs[compound][len(trajectory)] - costs[compound][split])
            + overhead
        )

    # Which lap to change on is part of the question, not something to settle
    # afterwards. Asking only "change now or never" makes rain that arrives in
    # two laps justify intermediates today, because a stop now does beat never
    # stopping — it just loses to the same stop made when the rain actually
    # arrives. Every box lap from here to the flag is priced, and the call is
    # only made when *this* lap is the best one.
    best_offset, best_compound, best_cost = None, current, stay_cost
    for compound in sorted(candidates):
        if compound == current:
            continue
        for box_offset in range(max(1, len(trajectory))):
            cost = change_cost(compound, box_offset)
            if cost < best_cost - 1e-9:
                best_offset, best_compound, best_cost = box_offset, compound, cost
    margin_s = stay_cost - best_cost

    # What each compound is worth on the lap the call actually names, which is
    # the comparison the driver is owed alongside it. Publishing the change-now
    # numbers beside a call to box in six laps would show evidence that argues
    # against the call sitting next to it.
    option_offset = best_offset or 0
    options = [
        {
            "compound": current,
            "weather_cost_s": round(stay_cost, 2),
            "change_overhead_s": 0.0,
            "total_s": round(stay_cost, 2),
            "is_change": False,
            "benefiting_laps": len(trajectory),
        }
    ]
    for compound in sorted(candidates - {current}):
        at_box = change_cost(compound, option_offset)
        options.append(
            {
                "compound": compound,
                "weather_cost_s": round(at_box - overhead, 2),
                "change_overhead_s": round(overhead, 2),
                "total_s": round(at_box, 2),
                "is_change": True,
                "benefiting_laps": len(trajectory)
                if change_during_suspension
                else max(0, len(trajectory) - option_offset - 1),
            }
        )
    options.sort(key=lambda item: item["total_s"])
    staying = next(item for item in options if not item["is_change"])
    best = next(
        (item for item in options if item["compound"] == best_compound), staying
    )

    uncertainty = _decision_uncertainty_s(
        state, reading, trajectory, base_lap_s, remaining
    )
    near_crossover = _nearest_crossover(wetness)
    is_change = best_offset is not None
    should_change = bool(is_change and best_offset == 0 and margin_s > uncertainty)
    should_ask = bool(
        is_change
        and best_offset == 0
        and 0.0 < margin_s <= uncertainty
        and remaining > 2
        and float(reading["pace_confidence"]) < 0.75
    )
    # A change that pays later but not yet is a lap to name, not a stop to call.
    upcoming = None
    if is_change and best_offset and margin_s > uncertainty:
        upcoming = {
            "lap_offset": int(best_offset),
            "compound": best_compound,
            "gain_s": round(margin_s, 1),
            "projected_wetness": trajectory[min(best_offset, len(trajectory) - 1)],
            "minutes_away": int(round(best_offset * base_lap_s / 60.0)),
            "benefiting_laps": max(0, len(trajectory) - best_offset - 1),
        }

    return {
        "wetness": reading["wetness"],
        "reading": reading,
        "trajectory": trajectory,
        "trajectory_kind": "probability_weighted_mean",
        "scenarios": scenarios,
        "expected_lap_penalties": penalties,
        "projected_end_wetness": trajectory[-1] if trajectory else reading["wetness"],
        "options": options,
        "best_compound": str(best_compound),
        "best_box_lap_offset": best_offset,
        "upcoming_change": upcoming,
        "current_compound": current,
        "margin_s": round(margin_s, 2),
        "uncertainty_s": round(uncertainty, 2),
        "should_change": should_change,
        "should_ask_driver": should_ask,
        "driver_question": _driver_question(near_crossover, reading) if should_ask else None,
        "near_crossover": near_crossover,
        "pit_loss_s": round(float(pit_loss_s), 1),
        "remaining_laps": remaining,
        "reason": _explain(
            reading,
            best,
            staying,
            margin_s,
            uncertainty,
            trajectory,
            should_change,
            # What the conditions alone would ask for, before the pit lane is
            # priced in. "The right tyre" and "not worth the stop" are different
            # answers and a driver is owed the difference.
            pace_best=(
                min(penalties, key=lambda compound: penalties[compound][len(trajectory) // 2])
                if trajectory else best_compound_for(wetness, penalties)
            ),
            upcoming=upcoming,
        ),
    }


def _decision_uncertainty_s(
    state: dict[str, Any],
    reading: dict[str, Any],
    trajectory: Sequence[float],
    base_lap_s: float,
    remaining_laps: int,
) -> float:
    """How big a margin this call needs before it is worth making.

    A tyre change is irreversible and costs the pit lane, so the bar rises with
    everything that could be wrong: a hedged forecast, conditions still moving,
    no lap times to check the sky against, and a surface sitting on a crossover
    where a small error in wetness flips the answer.
    """
    laps = max(1, int(remaining_laps))
    # Baseline doubt: a tenth a lap, which is roughly the noise on a wet lap.
    uncertainty = 0.10 * laps
    if int(state.get("forecast_accuracy", 0) or 0) != 0:
        uncertainty += 0.09 * laps
    pace_confidence = float(reading.get("pace_confidence", 0.0))
    uncertainty += (1.0 - pace_confidence) * 0.14 * laps
    if trajectory:
        swing = max(trajectory) - min(trajectory)
        uncertainty += swing * 0.30 * base_lap_s
    wetness = float(reading["wetness"])
    for crossover in (WETNESS_SLICK_INTER, WETNESS_INTER_WET):
        distance = abs(wetness - crossover)
        if distance < 0.08:
            uncertainty += (0.08 - distance) * 6.0 * base_lap_s / 10.0
    # Evidence caps the doubt: once the field has been measured across two
    # compounds, the crossover is observed and no longer a matter of opinion.
    split = reading.get("compound_split")
    if split and int(split.get("wet_shod_cars", 0)) >= 2 and int(split.get("slick_shod_cars", 0)) >= 2:
        uncertainty *= 0.55
    return max(1.0, uncertainty)


def _nearest_crossover(wetness: float) -> str | None:
    if abs(wetness - WETNESS_SLICK_INTER) <= 0.10:
        return "slick_inter"
    if abs(wetness - WETNESS_INTER_WET) <= 0.10:
        return "inter_wet"
    return None


def _driver_question(crossover: str | None, reading: dict[str, Any]) -> str:
    trend = str(reading.get("trend", "steady"))
    if crossover == "inter_wet":
        return "Is there standing water out there, or is it just heavy spray?"
    if crossover == "slick_inter":
        if trend == "drying":
            return "Is a dry line coming through, or is it still greasy off-line?"
        return "How wet is it out there — is the line still taking a slick?"
    if trend == "drying":
        return "Is the track drying where you are, or is it still fully wet?"
    return "How's the grip out there?"


def _explain(
    reading: dict[str, Any],
    best: dict[str, Any],
    staying: dict[str, Any],
    margin_s: float,
    uncertainty_s: float,
    trajectory: Sequence[float],
    should_change: bool,
    pace_best: str | None = None,
    upcoming: dict[str, Any] | None = None,
) -> str:
    """One sentence a driver can act on, with the evidence behind it."""
    wetness = float(reading["wetness"])
    surface = (
        "flooded"
        if wetness >= 0.85
        else "fully wet"
        if wetness >= WETNESS_INTER_WET
        else "wet"
        if wetness >= WETNESS_SLICK_INTER
        else "damp"
        if wetness >= 0.15
        else "dry"
    )
    end = trajectory[-1] if trajectory else wetness
    if end < wetness - 0.08:
        direction = "drying through the stint"
    elif end > wetness + 0.08:
        direction = "getting wetter"
    else:
        # Nothing in the projection, so fall back to what the last few laps of
        # weather did. "Holding" is only the answer when the track really has
        # been holding, not when there was no forecast to look at.
        direction = {
            "wetting": "getting wetter",
            "drying": "drying",
        }.get(str(reading.get("trend", "steady")), "holding")
    evidence = []
    split = reading.get("compound_split")
    if split:
        advantage = float(split["wet_advantage_fraction"]) * 100.0
        if abs(advantage) >= 0.3:
            faster = "wet tyres" if advantage > 0 else "slicks"
            evidence.append(
                f"{faster} are {abs(advantage):.1f}% a lap quicker across the field"
            )
    elif reading.get("field_reference_ratio"):
        evidence.append(
            f"the field is at {float(reading['field_reference_ratio']) * 100:.0f}% of dry pace"
        )
    report = reading.get("driver_report")
    if report:
        evidence.append(f"you reported it {str(report['category']).replace('_', ' ')}")
    tail = f" ({'; '.join(evidence)})" if evidence else ""

    if should_change:
        return (
            f"Track is {surface} and {direction}; {best['compound']} is worth "
            f"{margin_s:.0f}s over the rest of the race against staying on "
            f"{staying['compound']}{tail}."
        )
    if upcoming:
        # Not yet, but the lap is known — which is the difference between a
        # driver planning a stop and a driver being told to dive in.
        laps = int(upcoming["lap_offset"])
        return (
            f"Track is {surface} and {direction}; not yet, but "
            f"{upcoming['compound']} becomes worth {float(upcoming['gain_s']):.0f}s "
            f"in about {laps} lap{'s' if laps != 1 else ''}{tail}."
        )
    if best["compound"] == staying["compound"]:
        laps_left = int(staying.get("benefiting_laps", 0) or 0)
        if pace_best and pace_best != staying["compound"]:
            # The tyre is wrong for the conditions and staying out is still
            # right, which is only ever true because there is not enough race
            # left to pay for the pit lane. Say that, rather than claiming the
            # wrong tyre is the right one.
            return (
                f"Track is {surface} and {direction}; {pace_best} is the tyre for it, "
                f"but with {laps_left} lap(s) left a stop cannot repay the pit lane. "
                f"Stay out and manage it{tail}."
            )
        return (
            f"Track is {surface} and {direction}; {staying['compound']} is still "
            f"the right tyre for the laps that are left{tail}."
        )
    return (
        f"Track is {surface} and {direction}; {best['compound']} is only "
        f"{margin_s:.0f}s better over the rest of the race and the call needs "
        f"{uncertainty_s:.0f}s to be worth the stop{tail}."
    )
