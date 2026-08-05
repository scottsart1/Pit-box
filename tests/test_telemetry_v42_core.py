from __future__ import annotations

import numpy as np
import pytest

from pitwall.telemetry import (
    DEFAULT_METRICS,
    Availability,
    ConfidenceComponents,
    DetectionStatus,
    DistanceWindow,
    FieldValue,
    GapRule,
    Provenance,
    SignalKind,
    SignalSpec,
    align_distance_traces,
    build_distance_grid,
    build_segment_model,
    clean_distance_axis,
    combine_confidence,
    compute_time_delta,
    derive_value,
    detect_extremum_event,
    detect_sustained_event,
    make_segment,
    propagate_availability,
    resample_distance,
    stable_segment_id,
)


def test_availability_and_provenance_propagate_without_magic_values() -> None:
    speed = FieldValue.observed(71.2, source_id="packet:42")
    distance = FieldValue.observed(1_240.0, source_id="packet:43")
    result = derive_value(
        1_248.5,
        [speed, distance],
        algorithm="brake_event_v2",
        required_inputs=("speed", "distance"),
    )
    assert result.availability is Availability.DERIVED
    assert result.value == 1_248.5
    assert result.provenance == (Provenance.PACKET, Provenance.DERIVATION)
    assert result.source_ids == ("packet:42", "packet:43", "brake_event_v2")

    unavailable = FieldValue[float].unavailable("brake is absent", required_inputs=("brake",))
    missing_result = derive_value(
        None,
        [distance, unavailable],
        algorithm="brake_event_v2",
        required_inputs=("distance", "brake"),
    )
    assert missing_result.availability is Availability.UNAVAILABLE
    assert missing_result.value is None
    assert "brake is absent" in (missing_result.reason or "")
    assert propagate_availability([Availability.OBSERVED, Availability.STALE]) is Availability.STALE
    assert propagate_availability([Availability.OBSERVED], estimated=True) is Availability.ESTIMATED


def test_confidence_is_bounded_and_weak_components_are_not_hidden() -> None:
    strong = ConfidenceComponents().score
    weak = ConfidenceComponents(data_coverage=0.25, causal_support=0.4).score
    assert strong == 1.0
    assert 0.0 < weak < strong
    assert combine_confidence([1.0, 0.25], method="minimum") == 0.25
    assert combine_confidence([1.5, -2.0], method="product") == 0.0


def test_distance_cleaner_keeps_final_monotonic_timeline_epoch() -> None:
    distance = np.array([0.0, 1.0, 2.0, 3.0, 1.0, 2.0, 2.1, 3.0, np.nan])
    speed = np.arange(distance.size, dtype=float)
    clean = clean_distance_axis(distance, {"speed": speed})
    assert clean.timeline_epoch == 1
    assert clean.discontinuities == 1
    # 2.0 and 2.1 are within jitter tolerance, so the later observation wins.
    np.testing.assert_allclose(clean.distance_m, [1.0, 2.1, 3.0])
    np.testing.assert_allclose(clean.signals["speed"], [4.0, 6.0, 7.0])
    assert np.all(np.diff(clean.distance_m) > 0)


def test_resampling_obeys_per_signal_no_bridge_rules_and_step_semantics() -> None:
    clean = clean_distance_axis(
        [0.0, 1.0, 10.0, 11.0],
        {
            "speed": [10.0, 11.0, 20.0, 21.0],
            "gear": [2.0, 2.0, 4.0, 4.0],
        },
    )
    grid = build_distance_grid(0.0, 11.0, 1.0)
    result = resample_distance(
        clean,
        grid,
        specs={
            "speed": SignalSpec(SignalKind.CONTINUOUS, GapRule(2.0, 0.50)),
            "gear": SignalSpec(SignalKind.STEP, GapRule(12.0, 0.9)),
        },
    )
    assert np.isnan(result.signals["speed"][5])
    assert result.signals["speed"][0] == 10.0
    assert result.signals["speed"][10] == 20.0
    assert result.signals["gear"][5] == 2.0
    assert result.signals["gear"][10] == 4.0
    assert not result.usable["speed"]
    assert result.usable["gear"]


def test_alignment_uses_one_shared_distance_grid_and_reports_common_coverage() -> None:
    candidate = clean_distance_axis(
        [0, 10, 20, 30],
        {"time_s": [0, 1.0, 2.0, 3.0], "speed": [10, 11, 12, 13]},
    )
    reference = clean_distance_axis(
        [5, 15, 25, 35],
        {"time_s": [0.4, 1.4, 2.4, 3.4], "speed": [10, 11, 12, 13]},
    )
    aligned = align_distance_traces(
        candidate,
        reference,
        spacing_m=5,
        specs={
            "time_s": SignalSpec(SignalKind.CONTINUOUS, GapRule(12.0)),
            "speed": SignalSpec(SignalKind.CONTINUOUS, GapRule(12.0)),
        },
    )
    np.testing.assert_allclose(aligned.distance_m, [5, 10, 15, 20, 25, 30])
    assert aligned.common_coverage["time_s"] == 1.0


def test_time_delta_segment_sum_reconciles_to_lap_delta() -> None:
    distance = np.linspace(0.0, 100.0, 101)
    reference = distance / 10.0
    candidate = reference + 0.2 * distance / 100.0
    segments = (
        make_segment("test", 0, "First", 0.0, 50.0),
        make_segment("test", 1, "Second", 50.0, 100.0),
    )
    result = compute_time_delta(distance, candidate, reference, segments=segments)
    assert result.lap_delta_s == pytest.approx(0.2)
    assert [segment.delta_s for segment in result.segment_deltas] == pytest.approx([0.1, 0.1])
    assert result.reconciled
    assert result.reconciliation_error_s == pytest.approx(0.0, abs=1e-12)
    assert result.sign_convention == "positive_candidate_later"


def test_hysteretic_event_detector_rejects_blip_and_returns_sustained_range() -> None:
    distance = np.arange(0.0, 21.0)
    brake = np.zeros_like(distance)
    brake[2] = 0.2  # one-sample noise
    brake[10:15] = [0.11, 0.3, 0.7, 0.4, 0.1]
    event = detect_sustained_event(
        distance,
        brake,
        kind="brake_onset",
        enter_threshold=0.10,
        exit_threshold=0.05,
        min_duration_m=3.0,
        merge_gap_m=0.5,
        search_window=DistanceWindow(0.0, 20.0),
    )
    assert event.status is DetectionStatus.DETECTED
    assert event.distance_m == 10.0
    assert event.range_m == (10.0, 14.0)
    assert event.evidence_sample_range == (10, 14)
    assert 0.0 < event.confidence <= 1.0


def test_extremum_event_retains_minimum_speed_plateau_uncertainty() -> None:
    event = detect_extremum_event(
        [0, 1, 2, 3, 4, 5],
        [40, 35, 30.1, 30.0, 30.15, 34],
        kind="minimum_speed",
        plateau_tolerance=0.2,
    )
    assert event.status is DetectionStatus.DETECTED
    assert event.value == 30.0
    assert event.range_m == (2.0, 4.0)
    assert event.distance_m == 3.0


def test_segment_ids_and_models_are_deterministic_and_label_independent() -> None:
    first = stable_segment_id("spa_gp", 3, 100.1, 200.2)
    second = stable_segment_id("spa_gp", 3, 100.2, 200.1)
    assert first == second
    segment_a = make_segment(
        "spa_gp",
        3,
        "Old label",
        100.1,
        200.2,
        phases={"apex": (140.0, 160.0)},
        direction="right",
    )
    segment_b = make_segment("spa_gp", 3, "New label", 100.2, 200.1)
    assert segment_a.id == segment_b.id
    model_a = build_segment_model("spa-model", 1, [segment_a])
    model_b = build_segment_model("spa-model", 1, [segment_a])
    assert model_a.id == model_b.id
    assert model_a.checksum == model_b.checksum


def test_metric_registry_enforces_inputs_coverage_units_and_versions() -> None:
    metric = DEFAULT_METRICS.get("brake_onset_distance")
    assert metric.unit == "m"
    assert metric.algorithm_version == "brake_event_v2"
    assert metric.no_bridge_gap_m == 3.0
    observed = {
        "brake": FieldValue.observed(0.5),
        "distance_m": FieldValue.observed(1200.0),
    }
    available = DEFAULT_METRICS.availability(
        "brake_onset_distance",
        observed,
        coverage={"brake": 0.99, "distance_m": 1.0},
    )
    assert available.availability is Availability.DERIVED
    low = DEFAULT_METRICS.availability(
        "brake_onset_distance",
        observed,
        coverage={"brake": 0.5, "distance_m": 1.0},
    )
    assert low.availability is Availability.UNAVAILABLE
    assert low.low_coverage_inputs == ("brake",)
