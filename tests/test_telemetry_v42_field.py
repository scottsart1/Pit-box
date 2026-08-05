from __future__ import annotations

import numpy as np
import pytest

from pitwall.telemetry import (
    CornerRecord,
    PaceRecord,
    build_corner_matrix,
    build_pace_matrix,
    field_percentiles,
    median_absolute_deviation,
    robust_percentile,
)


def test_robust_statistics_ignore_missing_values_and_report_n_coverage() -> None:
    values = [1.0, 2.0, 3.0, np.nan, 100.0]
    assert robust_percentile(values, 0.5) == 2.5
    assert median_absolute_deviation(values) == 1.0
    percentiles = field_percentiles(values, (0.5, 0.9), total_count=8)
    assert percentiles[0].value == 2.5
    assert percentiles[0].n == 4
    assert percentiles[0].coverage == 0.5
    assert percentiles[1].quantile == 0.9


def test_pace_matrix_excludes_invalid_low_coverage_and_context_laps() -> None:
    records = [
        PaceRecord("A", 1, 90.0),
        PaceRecord("B", 1, 91.0),
        PaceRecord("C", 1, 92.0),
        PaceRecord("A", 2, 89.0),
        PaceRecord("B", 2, 90.0),
        PaceRecord("C", 2, 70.0, valid=False),
        PaceRecord("D", 1, 80.0, coverage=0.4),
        PaceRecord("D", 2, 80.0, context_ok=False),
    ]
    matrix = build_pace_matrix(records, min_coverage=0.8, min_cars_per_lap=2)
    assert matrix.drivers == ("A", "B", "C", "D")
    assert matrix.lap_numbers == (1, 2)
    np.testing.assert_allclose(matrix.lap_median_s, [91.0, 89.5])
    assert matrix.n_by_lap.tolist() == [3, 2]
    assert matrix.delta_to_lap_median_s[0, 0] == -1.0
    assert matrix.performance_percentile[0, 0] == 1.0
    assert np.isnan(matrix.lap_times_s[2, 1])
    assert matrix.coverage_by_driver["D"] == 0.0
    assert matrix.total_valid == 5


def test_sparse_lap_is_muted_when_field_n_is_below_threshold() -> None:
    matrix = build_pace_matrix(
        [PaceRecord("A", 1, 90.0), PaceRecord("A", 2, 91.0), PaceRecord("B", 2, 92.0)],
        min_cars_per_lap=2,
    )
    assert matrix.n_by_lap.tolist() == [1, 2]
    assert np.all(np.isnan(matrix.lap_times_s[:, 0]))
    assert np.all(~matrix.valid_mask[:, 0])


def test_corner_matrix_uses_driver_medians_field_rank_and_sample_counts() -> None:
    records = [
        CornerRecord("A", "s1", 10.0, lap_id="a1"),
        CornerRecord("A", "s1", 10.2, lap_id="a2"),
        CornerRecord("B", "s1", 10.5, lap_id="b1"),
        CornerRecord("C", "s1", 11.0, lap_id="c1"),
        CornerRecord("A", "s2", 20.0, lap_id="a1"),
        CornerRecord("B", "s2", 19.5, lap_id="b1"),
        CornerRecord("C", "s2", 25.0, lap_id="c1", coverage=0.4),
    ]
    matrix = build_corner_matrix(records, min_cars_per_segment=2)
    assert matrix.drivers == ("A", "B", "C")
    assert matrix.segment_ids == ("s1", "s2")
    assert matrix.median_time_s[0, 0] == pytest.approx(10.1)
    assert matrix.sample_count[0, 0] == 2
    assert matrix.n_by_segment.tolist() == [3, 2]
    assert matrix.rank[0, 0] == 1.0
    assert matrix.performance_percentile[0, 0] == 1.0
    assert matrix.rank[1, 1] == 1.0
    assert matrix.delta_to_field_median_s[1, 1] == pytest.approx(-0.25)
    assert np.isnan(matrix.median_time_s[2, 1])
    assert matrix.coverage_by_driver["C"] == 0.5


def test_corner_matrix_does_not_rank_a_segment_with_too_few_cars() -> None:
    matrix = build_corner_matrix(
        [CornerRecord("A", "s1", 10.0), CornerRecord("B", "s2", 20.0)],
        min_cars_per_segment=2,
    )
    assert matrix.n_by_segment.tolist() == [1, 1]
    assert not np.any(matrix.valid_mask)
    assert np.all(np.isnan(matrix.rank))
