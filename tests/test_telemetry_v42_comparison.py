from __future__ import annotations

import numpy as np
import pytest

from pitwall.telemetry import (
    ComparisonInput,
    CompatibilityClass,
    LapContext,
    build_comparison_result,
    classify_compatibility,
    comparison_input_hash,
    make_segment,
)


def _context(**changes: object) -> LapContext:
    values: dict[str, object] = {
        "game_version": "2026.1",
        "physics_version": "p1",
        "packet_format": 2026,
        "track_id": 10,
        "layout_signature": "spa:7004",
        "track_length_m": 7004.0,
        "car_class": "f1_equal",
        "equal_performance": True,
        "session_type": "time_trial",
        "weather_class": "dry",
        "tyre_compound": "SOFT",
        "tyre_age_laps": 2,
        "fuel_kg": 10.0,
        "track_model_version": 3,
        "coverage_ratio": 0.99,
    }
    values.update(changes)
    return LapContext(**values)  # type: ignore[arg-type]


def test_compatibility_classification_has_four_explicit_levels() -> None:
    strict = classify_compatibility(_context(), _context())
    assert strict.classification is CompatibilityClass.STRICT
    assert strict.compatibility_weight == 1.0
    assert strict.allows_coaching

    caveated = classify_compatibility(_context(), _context(tyre_compound="MEDIUM"))
    assert caveated.classification is CompatibilityClass.COMPARABLE_WITH_CAVEATS
    assert any(issue.code == "tyre_compound_mismatch" for issue in caveated.issues)
    assert caveated.allows_coaching

    context_only = classify_compatibility(_context(), _context(weather_class="wet"))
    assert context_only.classification is CompatibilityClass.CONTEXT_ONLY
    assert not context_only.allows_coaching

    incompatible = classify_compatibility(_context(), _context(track_id=11))
    assert incompatible.classification is CompatibilityClass.INCOMPATIBLE
    assert incompatible.compatibility_weight == 0.0


def test_low_coverage_reference_is_incompatible_not_silently_extrapolated() -> None:
    report = classify_compatibility(_context(), _context(coverage_ratio=0.5))
    assert report.classification is CompatibilityClass.INCOMPATIBLE
    assert "insufficient_coverage" in {issue.code for issue in report.issues}


def test_comparison_hash_is_order_independent_but_settings_sensitive() -> None:
    first = ComparisonInput(
        "lap_a",
        "lap",
        "lap_b",
        "aaa",
        "bbb",
        "track-v3",
        "segments-v5",
        "analysis_4.2.0",
        {"spacing_m": 0.5, "thresholds": {"brake": 0.1, "throttle": 0.2}},
    )
    reordered = ComparisonInput(
        "lap_a",
        "lap",
        "lap_b",
        "aaa",
        "bbb",
        "track-v3",
        "segments-v5",
        "analysis_4.2.0",
        {"thresholds": {"throttle": 0.2, "brake": 0.1}, "spacing_m": 0.5},
    )
    changed = ComparisonInput(
        "lap_a",
        "lap",
        "lap_b",
        "aaa",
        "bbb",
        "track-v3",
        "segments-v5",
        "analysis_4.2.0",
        {"spacing_m": 1.0, "thresholds": {"brake": 0.1, "throttle": 0.2}},
    )
    assert comparison_input_hash(first) == comparison_input_hash(reordered)
    assert comparison_input_hash(first) != comparison_input_hash(changed)


def test_comparison_result_is_deterministic_and_reconciles_segments() -> None:
    inputs = ComparisonInput(
        "lap_a",
        "lap",
        "lap_b",
        "aaa",
        "bbb",
        "track-v3",
        "segments-v5",
        "analysis_4.2.0",
        {"spacing_m": 1.0},
    )
    report = classify_compatibility(_context(), _context())
    distance = np.arange(0.0, 101.0)
    reference = distance / 20.0
    candidate = reference + 0.4 * distance / 100.0
    segments = [
        make_segment("test", 0, "A", 0.0, 40.0),
        make_segment("test", 1, "B", 40.0, 100.0),
    ]
    first = build_comparison_result(
        inputs,
        report,
        distance,
        candidate,
        reference,
        segments,
        model_quality=0.9,
    )
    second = build_comparison_result(
        inputs,
        report,
        distance,
        candidate,
        reference,
        segments,
        model_quality=0.9,
    )
    assert first == second
    assert first.comparison_id.startswith("cmp_")
    assert first.lap_delta_s == pytest.approx(0.4)
    assert sum(result.delta_s or 0.0 for result in first.segment_results) == pytest.approx(0.4)
    assert first.reconciled
    assert first.quality_score == pytest.approx(0.9)
