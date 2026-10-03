"""Tyre degradation learned from the whole field, not from one car.

The strategy engine already learns the player's own fuel-corrected degradation
slope (``tyre_learning.stint_pace_model``). Every other car on track was
modelled with a hand-written constant: ``_project_rival_finish_times`` passes
an empty history and clears live analysis, so every rival fell through to
``DEFAULT_DEG[compound] * TRACK_TYRE_SEVERITY[track]``. On lap 30 of a race
where nineteen cars have been running the player's compounds on the player's
surface in the player's conditions, none of that evidence reached the model.

This module estimates a per-compound degradation slope from the field's own
session-history laps.

Identification
--------------
The obvious estimator - regress lap time on tyre age - is not identified.
Inside any single stint, tyre age, fuel mass and track evolution all advance
one lap at a time, so their effects cannot be told apart. Pooling more cars
does not fix it: if every car runs a compound exactly once, each car's tyre age
is a fixed offset from the lap number, that offset is absorbed by the car's own
pace level, and age remains collinear with lap number for the whole pool.

The estimator used here is a two-way fixed-effects (within) regression, fitted
across every dry compound at once::

    lap_time[car, lap] = alpha[car] + gamma[lap]
                       + beta * tyre_age[car, lap]
                       + sum_c delta[c] * tyre_age[car, lap] * 1[compound = c]
                       + e

``alpha`` absorbs each car's intrinsic pace, and ``gamma`` absorbs everything
shared by the field on a given lap - fuel load, track evolution, air and track
temperature, wind, a safety car, a shower. No rival fuel telemetry is required,
because fuel mass is common to the field at a given lap and ``gamma`` takes it.

Fitting every compound together is not a convenience, it is what makes the
regression identified at all. Inside one stint, tyre age advances exactly one
lap at a time, so for a car running a compound once, ``age = lap - start + 1``
is precisely a car effect plus a lap effect and the fixed effects annihilate
it. What breaks that additivity is the pit stop: age resets to zero while the
lap counter carries on, so a field whose cars have stopped on different laps
carries a sawtooth in tyre age that no combination of car and lap effects can
reproduce. That sawtooth is the entire identifying signal.

The consequence is worth stating plainly: before the first stops nothing is
identified, however many cars and laps are supplied, and this module reports
that rather than returning a number. ``retained_age_variance`` is the fraction
of the age regressor's variance surviving both sets of fixed effects, and it is
the honest measure of how much the field has actually told us.

``beta`` is the degradation of the reference compound and each ``delta[c]`` is
another compound's difference from it. The contrasts survive even where the
level is fragile, which is what lets the field replace the hand-written
``_DEFAULT_DEG_STEP_RATIO`` extrapolation used for compounds nobody has run.

What this does not do
---------------------
It does not learn absolute pace, which belongs to the car and the driver, and
it does not learn a rival's *strategy*. A rival's stop lap is a decision, not a
tyre limit, so stop laps are never used as evidence of tyre life.

Laps spent following another car measure dirty air, not tyre life, so laps
taken within ``DEFAULT_CLEAN_AIR_GAP_S`` of the car ahead are excluded. Gaps
are reconstructed from cumulative elapsed time, which compares cars that have
completed the same number of laps; a car being lapped is close to traffic that
this reconstruction cannot see, and those laps remain a known confounder.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

# A lap taken closer than this to the car ahead is measuring aerodynamic wake,
# not tyre life. Published following-car data puts the lap-time penalty near
# half a second at a 0.6 s gap, decaying to nothing by roughly 2.5 s.
DEFAULT_CLEAN_AIR_GAP_S = 2.5

# An open stint's end lap is reported as 255 by the session-history packet.
OPEN_STINT_END_LAP = 255

# Degradation outside this band is a data artifact rather than a tyre. Matches
# the bounds the personal learner already applies, so the two estimates remain
# comparable and can be blended.
SLOPE_BOUNDS = (-0.1, 1.5)

# Minimum evidence before a field slope is offered at all. Cars are what carry
# independent information here; laps within one car's stint are correlated.
MIN_CARS = 3
MIN_RUNS = 3
MIN_OBSERVATIONS = 12

# Below this fraction of surviving age variance, the field has not separated
# tyre age from the lap itself and there is nothing to report.
MIN_RETAINED_AGE_VARIANCE = 0.02

# Residuals beyond this many robust deviations are mistakes, incidents or
# traffic the gap filter missed, not degradation.
OUTLIER_TRIM_SIGMA = 3.0

# No field estimate is offered as tighter than this, whatever its regression
# says, because the confounders this module cannot see are not in that number.
SIGMA_FLOOR = 0.02
# Used when a clustered standard error could not be formed at all.
SIGMA_UNKNOWN = 0.06

RESULT_LABELS_OUT = frozenset(
    {"retired", "did not finish", "disqualified", "not classified"}
)

DRY_COMPOUNDS = frozenset({"SOFT", "MEDIUM", "HARD"})


@dataclass(frozen=True, slots=True)
class FieldLap:
    """One completed lap by one car, with the tyre state that produced it."""

    car_idx: int
    lap_num: int
    lap_time_s: float
    compound: str
    tyre_age: int
    run_id: tuple[int, int]


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def neutralised_lap_numbers(completed_laps: Sequence[dict[str, Any]]) -> set[int]:
    """Lap numbers the player recorded as neutralised.

    A safety car is a property of the session, not of one car, so the player's
    own lap records identify the laps on which nobody was racing. Rival session
    history carries no race-control state of its own.
    """
    blocked: set[int] = set()
    for lap in completed_laps or ():
        number = _finite(lap.get("lap_num"))
        if number is None or not number.is_integer():
            continue
        exclusions = lap.get("learning_exclusions") or ()
        neutralised = "neutralised_lap" in {str(item) for item in exclusions}
        if (
            neutralised
            or lap.get("red_flag_active")
            or str(lap.get("safety_car") or "none") not in {"none", ""}
            or str(lap.get("race_control_phase") or "green")
            in {
                "safety_car",
                "safety_car_ending",
                "vsc",
                "vsc_ending",
                "formation",
                "red_flag",
            }
        ):
            blocked.add(int(number))
    return blocked


def _stint_index(stints: Sequence[dict[str, Any]]) -> list[tuple[int, int, str, int]]:
    """``(start_lap, end_lap, compound, run_ordinal)`` for usable stints."""
    resolved: list[tuple[int, int, str, int]] = []
    for ordinal, stint in enumerate(stints or ()):
        start = _finite(stint.get("start_lap"))
        end = _finite(stint.get("end_lap"))
        compound = str(stint.get("compound") or "").upper()
        if start is None or end is None or not compound or compound == "UNKNOWN":
            continue
        if not start.is_integer() or not end.is_integer() or start < 1:
            continue
        resolved.append((int(start), int(end), compound, ordinal))
    return resolved


def _elapsed_by_lap(lap_history: Sequence[dict[str, Any]]) -> dict[int, float]:
    """Cumulative race time at the end of each lap, while the history is whole.

    A gap or a zero in the history makes every later cumulative total wrong, so
    accumulation stops at the first break rather than silently closing it.
    """
    elapsed: dict[int, float] = {}
    total = 0.0
    expected = 1
    for lap in sorted(
        lap_history or (),
        key=lambda item: _finite(item.get("lap_num")) or 0.0,
    ):
        number = _finite(lap.get("lap_num"))
        milliseconds = _finite(lap.get("lap_ms"))
        if number is None or not number.is_integer() or int(number) != expected:
            break
        if milliseconds is None or milliseconds <= 0:
            break
        total += milliseconds / 1000.0
        elapsed[int(number)] = total
        expected += 1
    return elapsed


def clean_air_laps(
    elapsed: dict[int, dict[int, float]],
    minimum_gap_s: float = DEFAULT_CLEAN_AIR_GAP_S,
) -> set[tuple[int, int]]:
    """``(car_idx, lap_num)`` pairs begun with clear track ahead.

    The gap that matters for a lap is the one the car started it with, so lap
    ``L`` is judged by the ordering at the end of lap ``L - 1``. The leader of
    any given lap always has clear air.
    """
    clean: set[tuple[int, int]] = set()
    laps: set[int] = set()
    for per_car in elapsed.values():
        laps.update(per_car)
    for lap in laps:
        standings = sorted(
            (times[lap], car) for car, times in elapsed.items() if lap in times
        )
        for index, (time_s, car) in enumerate(standings):
            ahead = standings[index - 1][0] if index else None
            if ahead is None or time_s - ahead >= minimum_gap_s:
                # The gap at the end of this lap is the air the *next* lap
                # starts in.
                clean.add((car, lap + 1))
    return clean


def field_laps(
    state: dict[str, Any],
    minimum_gap_s: float = DEFAULT_CLEAN_AIR_GAP_S,
) -> tuple[list[FieldLap], dict[str, int]]:
    """Usable tyre-life observations from every car the session reports.

    Excluded, and counted: out-laps and in-laps, neutralised laps, invalid
    laps, laps with no identifiable stint, restricted or retired cars, and laps
    run in another car's wake.
    """
    excluded: dict[str, int] = {}

    def drop(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    # The player's own car is deliberately not part of the field. Its laps are
    # already the personal estimate, and precision-weighting two sources that
    # share observations would count the same laps twice and report a
    # confidence neither of them earned.
    player = _finite(state.get("player_car_index"))
    player_idx = int(player) if player is not None else None
    drivers = [
        driver
        for driver in state.get("drivers", []) or ()
        if not driver.get("restricted")
        and not driver.get("is_player")
        and str(driver.get("result_label", "")).lower() not in RESULT_LABELS_OUT
        and (player_idx is None or _finite(driver.get("car_idx")) != player_idx)
    ]
    elapsed = {
        int(driver["car_idx"]): _elapsed_by_lap(driver.get("lap_history") or ())
        for driver in drivers
        if _finite(driver.get("car_idx")) is not None
    }
    clean = clean_air_laps(elapsed, minimum_gap_s)
    blocked = neutralised_lap_numbers(state.get("completed_laps") or ())

    observations: list[FieldLap] = []
    for driver in drivers:
        car = _finite(driver.get("car_idx"))
        if car is None:
            continue
        car_idx = int(car)
        stints = _stint_index(driver.get("tyre_stints") or ())
        if not stints:
            continue
        for lap in driver.get("lap_history") or ():
            number = _finite(lap.get("lap_num"))
            milliseconds = _finite(lap.get("lap_ms"))
            flags = _finite(lap.get("valid_flags", 1))
            if number is None or not number.is_integer() or milliseconds is None:
                drop("unusable_lap_record")
                continue
            lap_num = int(number)
            if milliseconds <= 0 or flags is None or not int(flags) & 1:
                drop("invalid_lap")
                continue
            if lap_num in blocked:
                drop("neutralised")
                continue
            match = next(
                (item for item in stints if item[0] <= lap_num <= item[1]
                 or (item[1] == OPEN_STINT_END_LAP and lap_num >= item[0])),
                None,
            )
            if match is None:
                drop("no_identified_stint")
                continue
            start_lap, end_lap, compound, ordinal = match
            if compound not in DRY_COMPOUNDS:
                drop("not_a_dry_compound")
                continue
            age = lap_num - start_lap + 1
            if age <= 1:
                # The first lap of a set is an out-lap on a cold tyre, and on
                # lap one it is a standing start. Neither measures wear.
                drop("out_lap")
                continue
            if end_lap != OPEN_STINT_END_LAP and lap_num == end_lap:
                # The lap a stint ends on is the in-lap and carries pit entry.
                drop("in_lap")
                continue
            if (car_idx, lap_num) not in clean:
                drop("dirty_air")
                continue
            observations.append(
                FieldLap(
                    car_idx=car_idx,
                    lap_num=lap_num,
                    lap_time_s=milliseconds / 1000.0,
                    compound=compound,
                    tyre_age=age,
                    run_id=(car_idx, ordinal),
                )
            )
    return observations, excluded


def _group_demean(values: np.ndarray, codes: np.ndarray, groups: int) -> np.ndarray:
    """Subtract group means, column-wise for a 2-D design."""
    counts = np.bincount(codes, minlength=groups).astype(float)
    if values.ndim == 1:
        sums = np.bincount(codes, weights=values, minlength=groups)
        means = np.divide(sums, counts, out=np.zeros_like(sums), where=counts > 0)
        return values - means[codes]
    means = np.zeros((groups, values.shape[1]), dtype=float)
    for column in range(values.shape[1]):
        sums = np.bincount(codes, weights=values[:, column], minlength=groups)
        means[:, column] = np.divide(
            sums, counts, out=np.zeros_like(sums), where=counts > 0
        )
    return values - means[codes]


def _within_transform(
    y: np.ndarray,
    x: np.ndarray,
    car_codes: np.ndarray,
    lap_codes: np.ndarray,
    cars: int,
    laps: int,
    iterations: int = 500,
    tolerance: float = 1e-12,
) -> tuple[np.ndarray, np.ndarray]:
    """Project car and lap fixed effects out of the response and the design.

    Alternating within-group demeaning converges to the same residuals as
    regressing on the full dummy design, without building it.
    """
    y_res = y.astype(float).copy()
    x_res = np.atleast_2d(x.astype(float).copy())
    if x_res.shape[0] != y_res.shape[0]:
        x_res = x_res.T
    for _ in range(iterations):
        previous = x_res.copy()
        for codes, groups in ((car_codes, cars), (lap_codes, laps)):
            y_res = _group_demean(y_res, codes, groups)
            x_res = _group_demean(x_res, codes, groups)
        if float(np.max(np.abs(x_res - previous))) < tolerance:
            break
    return y_res, x_res


def _cluster_robust_covariance(
    x_res: np.ndarray,
    residuals: np.ndarray,
    car_codes: np.ndarray,
    cars: int,
    bread: np.ndarray,
) -> np.ndarray | None:
    """Sandwich covariance clustered by car.

    Ten laps from one driver are one driver's evidence, not ten independent
    observations, and an unclustered error would advertise a precision the
    field has not supplied.
    """
    clusters = int(np.count_nonzero(np.bincount(car_codes, minlength=cars)))
    if clusters < 2:
        return None
    scores = np.zeros((cars, x_res.shape[1]), dtype=float)
    for column in range(x_res.shape[1]):
        scores[:, column] = np.bincount(
            car_codes, weights=x_res[:, column] * residuals, minlength=cars
        )
    meat = scores.T @ scores * clusters / (clusters - 1)
    covariance = bread @ meat @ bread
    return covariance if np.all(np.isfinite(covariance)) else None


def _unavailable(compound: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "compound": compound,
        "slope_s_per_lap": None,
        "standard_error_s_per_lap": None,
        "relative_slope_s_per_lap": None,
        "identified": False,
        "identification": reason,
        "source": "field_two_way_fixed_effects",
        **extra,
    }


def _coverage(observations: Sequence[FieldLap], compound: str) -> dict[str, Any]:
    rows = [item for item in observations if item.compound == compound]
    return {
        "sample_size": len(rows),
        "car_count": len({item.car_idx for item in rows}),
        "run_count": len({item.run_id for item in rows}),
    }


def estimate_degradation(observations: Sequence[FieldLap]) -> dict[str, Any]:
    """Two-way fixed-effects degradation for every dry compound at once.

    Returns an unidentified result for every compound rather than a number
    whenever the field's tyre ages remain a car effect plus a lap effect - the
    situation before anybody has stopped.
    """
    rows = list(observations)
    compounds = sorted({item.compound for item in rows})
    coverage = {name: _coverage(rows, name) for name in compounds}
    if not rows:
        return {"compounds": {}, "reference_compound": None}

    # The best-supported compound anchors the level; the others are estimated
    # as differences from it, which keeps the contrasts interpretable.
    reference = max(compounds, key=lambda name: coverage[name]["sample_size"])
    others = [name for name in compounds if name != reference]

    cars = sorted({item.car_idx for item in rows})
    laps = sorted({item.lap_num for item in rows})
    car_lookup = {car: index for index, car in enumerate(cars)}
    lap_lookup = {lap: index for index, lap in enumerate(laps)}

    y = np.array([item.lap_time_s for item in rows], dtype=float)
    age = np.array([float(item.tyre_age) for item in rows], dtype=float)
    columns = [age] + [
        age * np.array([item.compound == name for item in rows], dtype=float)
        for name in others
    ]
    design = np.column_stack(columns)
    car_codes = np.array([car_lookup[item.car_idx] for item in rows], dtype=np.int64)
    lap_codes = np.array([lap_lookup[item.lap_num] for item in rows], dtype=np.int64)
    total_age_variance = float(np.sum((age - age.mean()) ** 2))

    def fail(reason: str, **extra: Any) -> dict[str, Any]:
        return {
            "compounds": {
                name: _unavailable(name, reason, **coverage[name], **extra)
                for name in compounds
            },
            "reference_compound": reference,
        }

    if total_age_variance <= 0:
        return fail("no_age_variation")

    keep = np.ones(len(rows), dtype=bool)
    fit: dict[str, Any] | None = None
    for attempt in range(2):
        y_res, x_res = _within_transform(
            y[keep], design[keep], car_codes[keep], lap_codes[keep], len(cars), len(laps)
        )
        retained = float(np.sum(x_res[:, 0] ** 2)) / total_age_variance
        if retained < MIN_RETAINED_AGE_VARIANCE:
            return fail(
                "age_not_separable_from_lap",
                retained_age_variance=round(retained, 6),
            )
        gram = x_res.T @ x_res
        # A near-singular design means two compounds' age columns move
        # together; solving it anyway would split one slope arbitrarily.
        scale = np.sqrt(np.diag(gram))
        if np.any(scale <= 0):
            return fail("compound_age_columns_degenerate")
        correlation = gram / np.outer(scale, scale)
        conditioning = float(np.linalg.cond(correlation))
        if not math.isfinite(conditioning) or conditioning > 1e4:
            return fail(
                "compound_slopes_not_separable",
                design_condition_number=round(conditioning, 1),
                retained_age_variance=round(retained, 6),
            )
        bread = np.linalg.inv(gram)
        beta = bread @ (x_res.T @ y_res)
        residuals = y_res - x_res @ beta
        covariance = _cluster_robust_covariance(
            x_res, residuals, car_codes[keep], len(cars), bread
        )
        fit = {
            "beta": beta,
            "covariance": covariance,
            "retained": retained,
            "conditioning": conditioning,
            "used": int(np.count_nonzero(keep)),
        }
        if attempt:
            break
        deviation = float(np.median(np.abs(residuals - np.median(residuals))))
        if deviation <= 0:
            break
        limit = OUTLIER_TRIM_SIGMA * 1.4826 * deviation
        trimmed = keep.copy()
        trimmed[np.flatnonzero(keep)[np.abs(residuals) > limit]] = False
        if trimmed.sum() < MIN_OBSERVATIONS or trimmed.sum() == keep.sum():
            break
        keep = trimmed

    assert fit is not None
    beta = np.asarray(fit["beta"], dtype=float)
    covariance = fit["covariance"]
    results: dict[str, dict[str, Any]] = {}
    for name in compounds:
        contrast = np.zeros(len(beta), dtype=float)
        contrast[0] = 1.0
        if name in others:
            contrast[1 + others.index(name)] = 1.0
        slope = float(contrast @ beta)
        relative = 0.0 if name == reference else float(beta[1 + others.index(name)])
        counts = coverage[name]
        if (
            counts["sample_size"] < MIN_OBSERVATIONS
            or counts["car_count"] < MIN_CARS
            or counts["run_count"] < MIN_RUNS
        ):
            results[name] = _unavailable(
                name,
                "insufficient_field_coverage",
                retained_age_variance=round(float(fit["retained"]), 6),
                **counts,
            )
            continue
        if not SLOPE_BOUNDS[0] <= slope <= SLOPE_BOUNDS[1]:
            results[name] = _unavailable(
                name,
                "implausible_slope",
                rejected_slope_s_per_lap=round(slope, 4),
                retained_age_variance=round(float(fit["retained"]), 6),
                **counts,
            )
            continue
        standard_error: float | None = None
        if covariance is not None:
            variance = float(contrast @ covariance @ contrast)
            if variance > 0 and math.isfinite(variance):
                standard_error = math.sqrt(variance)
        ages = [item.tyre_age for item in rows if item.compound == name]
        lap_numbers = [item.lap_num for item in rows if item.compound == name]
        results[name] = {
            "compound": name,
            "slope_s_per_lap": round(max(0.0, slope), 4),
            "standard_error_s_per_lap": (
                round(standard_error, 6) if standard_error is not None else None
            ),
            "relative_slope_s_per_lap": round(relative, 4),
            "relative_to": reference,
            "identified": True,
            "identification": "within_lap_age_dispersion",
            "retained_age_variance": round(float(fit["retained"]), 6),
            "design_condition_number": round(float(fit["conditioning"]), 1),
            "pooled_observations": len(rows),
            "pooled_observations_used": int(fit["used"]),
            "age_span_laps": max(ages) - min(ages),
            "lap_span": [min(lap_numbers), max(lap_numbers)],
            "source": "field_two_way_fixed_effects",
            "assumptions": [
                "Car and lap fixed effects absorb intrinsic pace and everything shared by the field on a lap, including fuel load, track evolution and weather.",
                "Degradation is identified by tyre age resetting at stops while the lap counter continues; before any stop nothing is identified.",
                "Laps within a car are correlated; the standard error is clustered by car.",
                "Laps taken in another car's wake are excluded, but a car being lapped remains an unmodelled confounder.",
                "A rival's stop lap is a decision and is never used as evidence of tyre life.",
            ],
            **counts,
        }
    return {"compounds": results, "reference_compound": reference}


def field_degradation(
    state: dict[str, Any],
    minimum_gap_s: float = DEFAULT_CLEAN_AIR_GAP_S,
) -> dict[str, Any]:
    """Per-compound field degradation for the session in ``state``."""
    observations, excluded = field_laps(state, minimum_gap_s)
    estimate = estimate_degradation(observations)
    compounds = estimate["compounds"]
    identified = [name for name, item in compounds.items() if item.get("identified")]
    return {
        "compounds": compounds,
        "identified_compounds": identified,
        "reference_compound": estimate["reference_compound"],
        "observation_count": len(observations),
        "contributing_cars": len({item.car_idx for item in observations}),
        "excluded_laps": excluded,
        "clean_air_gap_s": minimum_gap_s,
        "basis": "two_way_fixed_effects_on_field_session_history",
    }


@dataclass(frozen=True, slots=True)
class Evidence:
    """One source's estimate, with the spread that says how far to trust it."""

    value: float
    sigma: float
    source: str
    sample_size: int = 0

    def precision(self) -> float:
        return 1.0 / (self.sigma**2)


def blend(sources: Iterable[Evidence | None]) -> dict[str, Any] | None:
    """Precision-weighted combination of whatever evidence exists.

    A source with a wide spread moves the answer a little; a tight one moves it
    a lot. Nothing is discarded for failing a threshold, which is what lets
    three laps of personal evidence nudge a field estimate instead of either
    overriding it or being ignored until a lap counter crosses a constant.
    """
    usable = [
        item
        for item in sources
        if item is not None
        and math.isfinite(item.value)
        and math.isfinite(item.sigma)
        and item.sigma > 0
    ]
    if not usable:
        return None
    total_precision = sum(item.precision() for item in usable)
    if total_precision <= 0:
        return None
    value = sum(item.value * item.precision() for item in usable) / total_precision
    return {
        "value": value,
        "sigma": math.sqrt(1.0 / total_precision),
        "sources": [item.source for item in usable],
        "weights": {
            item.source: round(item.precision() / total_precision, 4)
            for item in usable
        },
        "sample_size": max((item.sample_size for item in usable), default=0),
    }


def field_evidence(
    field: dict[str, Any] | None, compound: str
) -> Evidence | None:
    """The field's estimate for ``compound`` as blendable evidence.

    The spread is never taken straight from the regression. A clustered
    standard error prices sampling noise and nothing else, while the residual
    confounders named in this module's docstring - lapped traffic the gap
    reconstruction cannot see, rivals managing or pushing their tyres, dirty
    air below the exclusion threshold - are real and unpriced. ``SIGMA_FLOOR``
    is the standing admission that a field estimate is never as sharp as its
    own arithmetic claims.

    The floor is deliberately absolute rather than proportional. A spread that
    scaled with the slope would make a large, cleanly measured degradation
    *less* influential than a small one, which is precisely backwards.
    """
    if not isinstance(field, dict):
        return None
    model = (field.get("compounds") or {}).get(str(compound).upper())
    if not isinstance(model, dict) or not model.get("identified"):
        return None
    value = _finite(model.get("slope_s_per_lap"))
    if value is None:
        return None
    reported = _finite(model.get("standard_error_s_per_lap"))
    sigma = SIGMA_UNKNOWN if reported is None else max(reported, SIGMA_FLOOR)
    return Evidence(
        value=value,
        sigma=sigma,
        source="field_learned",
        sample_size=int(model.get("run_count", 0) or 0),
    )
