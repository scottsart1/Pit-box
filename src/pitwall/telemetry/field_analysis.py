"""Coverage-aware full-field pace and corner aggregates."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np
from numpy.typing import ArrayLike, NDArray

FloatArray = NDArray[np.float64]
BoolArray = NDArray[np.bool_]
IntArray = NDArray[np.int64]


def _readonly_float(values: ArrayLike) -> FloatArray:
    result = np.asarray(values, dtype=np.float64).copy()
    result.setflags(write=False)
    return result


def _readonly_bool(values: ArrayLike) -> BoolArray:
    result = np.asarray(values, dtype=bool).copy()
    result.setflags(write=False)
    return result


def _readonly_int(values: ArrayLike) -> IntArray:
    result = np.asarray(values, dtype=np.int64).copy()
    result.setflags(write=False)
    return result


def robust_percentile(values: ArrayLike, quantile: float) -> float | None:
    """Finite-only percentile with an explicit 0..1 quantile contract."""

    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must be between zero and one")
    finite = np.asarray(values, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return None
    return float(np.percentile(finite, quantile * 100.0, method="linear"))


def median_absolute_deviation(values: ArrayLike) -> float | None:
    finite = np.asarray(values, dtype=np.float64)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return None
    median = float(np.median(finite))
    return float(np.median(np.abs(finite - median)))


@dataclass(frozen=True, slots=True)
class FieldPercentile:
    quantile: float
    value: float | None
    n: int
    coverage: float


def field_percentiles(
    values: ArrayLike,
    quantiles: Iterable[float] = (0.25, 0.50, 0.75, 0.90),
    *,
    total_count: int | None = None,
) -> tuple[FieldPercentile, ...]:
    array = np.asarray(values, dtype=np.float64)
    finite_count = int(np.count_nonzero(np.isfinite(array)))
    denominator = int(total_count if total_count is not None else array.size)
    if denominator < finite_count or denominator < 0:
        raise ValueError("total_count cannot be smaller than finite observation count")
    coverage = finite_count / max(1, denominator)
    return tuple(
        FieldPercentile(float(q), robust_percentile(array, float(q)), finite_count, coverage)
        for q in quantiles
    )


@dataclass(frozen=True, slots=True)
class PaceRecord:
    driver_id: str
    lap_number: int
    lap_time_s: float | None
    lap_id: str | None = None
    valid: bool = True
    coverage: float = 1.0
    context_ok: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "coverage", max(0.0, min(1.0, self.coverage)))
        if self.lap_number < 0:
            raise ValueError("lap number cannot be negative")


@dataclass(frozen=True, slots=True)
class PaceMatrix:
    drivers: tuple[str, ...]
    lap_numbers: tuple[int, ...]
    lap_times_s: FloatArray
    delta_to_lap_median_s: FloatArray
    performance_percentile: FloatArray
    valid_mask: BoolArray
    lap_median_s: FloatArray
    lap_mad_s: FloatArray
    n_by_lap: IntArray
    coverage_by_driver: Mapping[str, float]
    total_valid: int
    total_cells: int


def _performance_percentiles(values: FloatArray) -> FloatArray:
    """Return 1 for fastest, 0 for slowest; ties share a midpoint rank."""

    output = np.full(values.shape, np.nan, dtype=np.float64)
    finite_indices = np.flatnonzero(np.isfinite(values))
    count = finite_indices.size
    if count == 0:
        return output
    if count == 1:
        output[finite_indices[0]] = 1.0
        return output
    finite_values = values[finite_indices]
    for idx, value in zip(finite_indices, finite_values, strict=True):
        faster = int(np.count_nonzero(finite_values < value))
        equal = int(np.count_nonzero(finite_values == value))
        midpoint_rank = faster + (equal - 1) / 2.0
        output[idx] = 1.0 - midpoint_rank / (count - 1)
    return output


def build_pace_matrix(
    records: Iterable[PaceRecord],
    *,
    min_coverage: float = 0.80,
    min_cars_per_lap: int = 1,
) -> PaceMatrix:
    rows = tuple(records)
    drivers = tuple(sorted({record.driver_id for record in rows}))
    lap_numbers = tuple(sorted({record.lap_number for record in rows}))
    driver_index = {driver: idx for idx, driver in enumerate(drivers)}
    lap_index = {lap: idx for idx, lap in enumerate(lap_numbers)}
    grouped: dict[tuple[str, int], list[float]] = {}
    for record in rows:
        if (
            record.valid
            and record.context_ok
            and record.coverage >= min_coverage
            and record.lap_time_s is not None
            and np.isfinite(record.lap_time_s)
            and record.lap_time_s > 0
        ):
            grouped.setdefault((record.driver_id, record.lap_number), []).append(
                float(record.lap_time_s)
            )
    shape = (len(drivers), len(lap_numbers))
    values = np.full(shape, np.nan, dtype=np.float64)
    for (driver, lap), observations in grouped.items():
        values[driver_index[driver], lap_index[lap]] = float(np.median(observations))

    medians = np.full(len(lap_numbers), np.nan, dtype=np.float64)
    mads = np.full(len(lap_numbers), np.nan, dtype=np.float64)
    counts = np.zeros(len(lap_numbers), dtype=np.int64)
    deltas = np.full(shape, np.nan, dtype=np.float64)
    percentiles = np.full(shape, np.nan, dtype=np.float64)
    for column in range(len(lap_numbers)):
        column_values = values[:, column]
        count = int(np.count_nonzero(np.isfinite(column_values)))
        counts[column] = count
        if count < min_cars_per_lap:
            values[:, column] = np.nan
            continue
        medians[column] = float(np.nanmedian(column_values))
        mad = median_absolute_deviation(column_values)
        mads[column] = np.nan if mad is None else mad
        deltas[:, column] = column_values - medians[column]
        percentiles[:, column] = _performance_percentiles(column_values)
    valid_mask = np.isfinite(values)
    coverage = {
        driver: float(np.count_nonzero(valid_mask[idx]) / max(1, len(lap_numbers)))
        for idx, driver in enumerate(drivers)
    }
    return PaceMatrix(
        drivers,
        lap_numbers,
        _readonly_float(values),
        _readonly_float(deltas),
        _readonly_float(percentiles),
        _readonly_bool(valid_mask),
        _readonly_float(medians),
        _readonly_float(mads),
        _readonly_int(counts),
        MappingProxyType(coverage),
        int(np.count_nonzero(valid_mask)),
        int(valid_mask.size),
    )


@dataclass(frozen=True, slots=True)
class CornerRecord:
    driver_id: str
    segment_id: str
    segment_time_s: float | None
    lap_id: str | None = None
    coverage: float = 1.0
    valid: bool = True
    context_ok: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "coverage", max(0.0, min(1.0, self.coverage)))


@dataclass(frozen=True, slots=True)
class CornerMatrix:
    drivers: tuple[str, ...]
    segment_ids: tuple[str, ...]
    median_time_s: FloatArray
    delta_to_field_median_s: FloatArray
    performance_percentile: FloatArray
    rank: FloatArray
    valid_mask: BoolArray
    field_median_s: FloatArray
    field_mad_s: FloatArray
    n_by_segment: IntArray
    sample_count: IntArray
    coverage_by_driver: Mapping[str, float]


def build_corner_matrix(
    records: Iterable[CornerRecord],
    *,
    min_coverage: float = 0.80,
    min_laps_per_driver_segment: int = 1,
    min_cars_per_segment: int = 2,
) -> CornerMatrix:
    rows = tuple(records)
    drivers = tuple(sorted({record.driver_id for record in rows}))
    segments = tuple(sorted({record.segment_id for record in rows}))
    driver_index = {driver: idx for idx, driver in enumerate(drivers)}
    segment_index = {segment: idx for idx, segment in enumerate(segments)}
    grouped: dict[tuple[str, str], list[float]] = {}
    for record in rows:
        if (
            record.valid
            and record.context_ok
            and record.coverage >= min_coverage
            and record.segment_time_s is not None
            and np.isfinite(record.segment_time_s)
            and record.segment_time_s > 0
        ):
            grouped.setdefault((record.driver_id, record.segment_id), []).append(
                float(record.segment_time_s)
            )
    shape = (len(drivers), len(segments))
    values = np.full(shape, np.nan, dtype=np.float64)
    samples = np.zeros(shape, dtype=np.int64)
    for (driver, segment), observations in grouped.items():
        row = driver_index[driver]
        column = segment_index[segment]
        samples[row, column] = len(observations)
        if len(observations) >= min_laps_per_driver_segment:
            values[row, column] = float(np.median(observations))

    field_medians = np.full(len(segments), np.nan, dtype=np.float64)
    field_mads = np.full(len(segments), np.nan, dtype=np.float64)
    counts = np.zeros(len(segments), dtype=np.int64)
    deltas = np.full(shape, np.nan, dtype=np.float64)
    percentiles = np.full(shape, np.nan, dtype=np.float64)
    ranks = np.full(shape, np.nan, dtype=np.float64)
    for column in range(len(segments)):
        column_values = values[:, column]
        finite = np.isfinite(column_values)
        count = int(np.count_nonzero(finite))
        counts[column] = count
        if count < min_cars_per_segment:
            values[:, column] = np.nan
            continue
        field_medians[column] = float(np.nanmedian(column_values))
        mad = median_absolute_deviation(column_values)
        field_mads[column] = np.nan if mad is None else mad
        deltas[:, column] = column_values - field_medians[column]
        percentiles[:, column] = _performance_percentiles(column_values)
        finite_values = column_values[finite]
        for row in np.flatnonzero(finite):
            value = column_values[row]
            faster = int(np.count_nonzero(finite_values < value))
            equal = int(np.count_nonzero(finite_values == value))
            ranks[row, column] = 1.0 + faster + (equal - 1) / 2.0
    valid_mask = np.isfinite(values)
    coverage = {
        driver: float(np.count_nonzero(valid_mask[idx]) / max(1, len(segments)))
        for idx, driver in enumerate(drivers)
    }
    return CornerMatrix(
        drivers,
        segments,
        _readonly_float(values),
        _readonly_float(deltas),
        _readonly_float(percentiles),
        _readonly_float(ranks),
        _readonly_bool(valid_mask),
        _readonly_float(field_medians),
        _readonly_float(field_mads),
        _readonly_int(counts),
        _readonly_int(samples),
        MappingProxyType(coverage),
    )
