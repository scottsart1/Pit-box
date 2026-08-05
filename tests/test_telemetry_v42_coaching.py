from __future__ import annotations

from dataclasses import replace

import pytest

from pitwall.telemetry import (
    FindingType,
    MetricFact,
    SegmentEvidence,
    build_coaching_evidence,
    opportunity_score,
    rank_findings,
)


def _fact(key: str, candidate: float, reference: float, unit: str) -> MetricFact:
    return MetricFact(key, candidate, reference, unit, confidence=0.95, evidence_ids=(f"cmp:{key}",))


def _rich_evidence(**changes: object) -> SegmentEvidence:
    values: dict[str, object] = {
        "segment_id": "turn_09",
        "label": "Turn 9",
        "measured_loss_s": 0.30,
        "repeatability": 0.75,
        "sample_count": 8,
        "data_coverage": 0.98,
        "model_quality": 0.94,
        "compatibility_weight": 1.0,
        "facts": {
            "brake_onset_m": _fact("brake_onset_m", 100.0, 119.0, "m"),
            "minimum_speed_distance_m": _fact("minimum_speed_distance_m", 150.0, 161.0, "m"),
            "minimum_speed_mps": _fact("minimum_speed_mps", 34.0, 37.0, "m/s"),
            "throttle_pickup_m": _fact("throttle_pickup_m", 215.0, 200.0, "m"),
            "line_offset_m": _fact("line_offset_m", 1.2, 0.2, "m"),
            "steering_corrections": _fact("steering_corrections", 4.0, 1.0, "count"),
            "gear_at_apex": _fact("gear_at_apex", 3.0, 4.0, "gear"),
            "segment_time_mad_s": _fact("segment_time_mad_s", 0.12, 0.03, "s"),
        },
    }
    values.update(changes)
    return SegmentEvidence(**values)  # type: ignore[arg-type]


def test_every_finding_type_has_a_diversity_family() -> None:
    """rank_findings indexes _FAMILIES directly, so a new finding type added
    without a family would raise KeyError the first time it was ranked —
    inside the debrief path, after a session the driver cannot repeat."""

    from pitwall.telemetry.coaching import _FAMILIES

    missing = sorted(item.name for item in FindingType if item not in _FAMILIES)
    assert not missing, f"FindingType members missing a diversity family: {missing}"


def test_coaching_rules_emit_required_findings_and_build_causal_chain() -> None:
    result = build_coaching_evidence(_rich_evidence())
    kinds = {finding.finding_type for finding in result.findings}
    assert FindingType.BRAKE_TOO_EARLY in kinds
    assert FindingType.MINIMUM_SPEED_TOO_EARLY in kinds
    assert FindingType.MINIMUM_SPEED_TOO_LOW in kinds
    assert FindingType.THROTTLE_TOO_LATE in kinds
    assert FindingType.STEERING_CORRECTION in kinds
    assert FindingType.GEAR_MISMATCH in kinds
    assert FindingType.INCONSISTENT_EXECUTION in kinds
    # Line advice needs real boundary or repeatably better outcome evidence.
    assert FindingType.LINE_DISPLACEMENT not in kinds

    throttle = next(
        finding for finding in result.findings if finding.finding_type is FindingType.THROTTLE_TOO_LATE
    )
    chain = result.graph.causal_chain(throttle.evidence_node_ids[0])
    assert len(chain) >= 2
    assert any("minimum_speed" in node_id or "brake" in node_id for node_id in chain[:-1])
    assert len(result.ranked) == 3
    assert all(0.0 <= ranked.adjusted_score <= 1.0 for ranked in result.ranked)


def test_causal_attribution_intervals_never_exceed_measured_segment_loss() -> None:
    result = build_coaching_evidence(_rich_evidence())
    negative = [finding for finding in result.findings if not finding.positive]
    attributed_total = sum(finding.attributed_high_s for finding in negative)
    assert attributed_total == pytest.approx(0.30)
    assert all(
        0.0 <= finding.attributed_low_s <= finding.attributed_high_s <= 0.30
        for finding in negative
    )


def test_line_displacement_requires_boundary_or_outcome_evidence() -> None:
    no_support = build_coaching_evidence(_rich_evidence())
    assert FindingType.LINE_DISPLACEMENT not in {
        finding.finding_type for finding in no_support.findings
    }
    with_boundary = build_coaching_evidence(
        _rich_evidence(track_boundary_confidence=0.85)
    )
    assert FindingType.LINE_DISPLACEMENT in {
        finding.finding_type for finding in with_boundary.findings
    }
    with_outcome = build_coaching_evidence(_rich_evidence(line_outcome_supported=True))
    assert FindingType.LINE_DISPLACEMENT in {
        finding.finding_type for finding in with_outcome.findings
    }


def test_late_braking_and_late_minimum_speed_are_distinct_rules() -> None:
    evidence = SegmentEvidence(
        "turn_03",
        "Turn 3",
        0.2,
        {
            "brake_onset_m": _fact("brake_onset_m", 120.0, 110.0, "m"),
            "minimum_speed_distance_m": _fact("minimum_speed_distance_m", 175.0, 165.0, "m"),
        },
        sample_count=6,
        repeatability=0.8,
    )
    kinds = {
        finding.finding_type for finding in build_coaching_evidence(evidence).findings
    }
    assert FindingType.BRAKE_TOO_LATE in kinds
    assert FindingType.MINIMUM_SPEED_TOO_LATE in kinds


def test_positive_strength_card_is_preserved_not_mislabelled_as_an_error() -> None:
    evidence = SegmentEvidence(
        "turn_12",
        "Turn 12",
        -0.12,
        {"throttle_pickup_m": _fact("throttle_pickup_m", 200.0, 200.0, "m")},
        repeatability=0.9,
        sample_count=9,
        segment_percentile=0.95,
    )
    result = build_coaching_evidence(evidence)
    strength = next(
        finding for finding in result.findings if finding.finding_type is FindingType.STRENGTH
    )
    assert strength.positive
    assert strength.attributed_high_s == 0.0
    assert "Preserve" in strength.action


def test_opportunity_is_bounded_and_diversity_penalises_duplicate_root_causes() -> None:
    assert opportunity_score(
        10.0,
        target_loss_s=0.1,
        confidence=2.0,
        repeatability=2.0,
        actionability=2.0,
        compatibility_weight=2.0,
    ) == 1.0
    first_result = build_coaching_evidence(_rich_evidence())
    brake = next(
        finding for finding in first_result.findings if finding.finding_type is FindingType.BRAKE_TOO_EARLY
    )
    same_corner = replace(brake, id="same_corner", opportunity_score=0.99)
    other_corner = replace(
        brake,
        id="other_corner",
        segment_id="turn_10",
        segment_label="Turn 10",
        opportunity_score=0.80,
    )
    ranked = rank_findings([same_corner, brake, other_corner], limit=3)
    assert ranked[0].finding.id == "same_corner"
    assert ranked[1].finding.id == "other_corner"
    assert ranked[2].adjusted_score < ranked[2].finding.opportunity_score
