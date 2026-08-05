"""Deterministic evidence graph, causal findings, and bounded ranking."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from .availability import Availability
from .confidence import ConfidenceComponents


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


class FindingType(StrEnum):
    BRAKE_TOO_EARLY = "brake_too_early"
    BRAKE_TOO_LATE = "brake_too_late"
    MINIMUM_SPEED_TOO_EARLY = "minimum_speed_too_early"
    MINIMUM_SPEED_TOO_LATE = "minimum_speed_too_late"
    MINIMUM_SPEED_TOO_LOW = "minimum_speed_too_low"
    THROTTLE_TOO_LATE = "throttle_too_late"
    LINE_DISPLACEMENT = "line_displacement"
    STEERING_CORRECTION = "steering_correction"
    GEAR_MISMATCH = "gear_mismatch"
    INCONSISTENT_EXECUTION = "inconsistent_execution"
    STRENGTH = "strength"


@dataclass(frozen=True, slots=True)
class MetricFact:
    key: str
    candidate: float | None
    reference: float | None
    unit: str
    confidence: float = 1.0
    availability: Availability = Availability.DERIVED
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "confidence", _bounded(self.confidence))

    @property
    def available(self) -> bool:
        return (
            self.candidate is not None
            and self.reference is not None
            and self.availability not in {Availability.UNAVAILABLE, Availability.STALE}
        )

    @property
    def delta(self) -> float | None:
        if not self.available:
            return None
        assert self.candidate is not None and self.reference is not None
        return float(self.candidate - self.reference)


@dataclass(frozen=True, slots=True)
class SegmentEvidence:
    segment_id: str
    label: str
    measured_loss_s: float
    facts: Mapping[str, MetricFact]
    repeatability: float = 0.0
    sample_count: int = 1
    data_coverage: float = 1.0
    model_quality: float = 1.0
    compatibility_weight: float = 1.0
    track_boundary_confidence: float = 0.0
    line_outcome_supported: bool = False
    gear_outcome_supported: bool = True
    segment_percentile: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "repeatability",
            "data_coverage",
            "model_quality",
            "compatibility_weight",
            "track_boundary_confidence",
        ):
            object.__setattr__(self, name, _bounded(getattr(self, name)))
        if self.sample_count < 0:
            raise ValueError("sample_count cannot be negative")
        object.__setattr__(self, "facts", MappingProxyType(dict(self.facts)))


@dataclass(frozen=True, slots=True)
class EvidenceNode:
    id: str
    kind: str
    segment_id: str
    fact_keys: tuple[str, ...]
    confidence: float
    actionable: bool


@dataclass(frozen=True, slots=True)
class EvidenceEdge:
    upstream_id: str
    downstream_id: str
    relation: str
    confidence: float


@dataclass(frozen=True, slots=True)
class EvidenceGraph:
    nodes: tuple[EvidenceNode, ...]
    edges: tuple[EvidenceEdge, ...]

    def predecessors(self, node_id: str) -> tuple[EvidenceNode, ...]:
        upstream = {
            edge.upstream_id for edge in self.edges if edge.downstream_id == node_id
        }
        return tuple(node for node in self.nodes if node.id in upstream)

    def causal_chain(self, node_id: str) -> tuple[str, ...]:
        """Return one deterministic upstream-to-node chain."""

        by_id = {node.id: node for node in self.nodes}
        incoming = {
            edge.downstream_id: edge.upstream_id
            for edge in sorted(self.edges, key=lambda item: (item.downstream_id, item.upstream_id))
        }
        chain = [node_id]
        seen = {node_id}
        while chain[-1] in incoming and incoming[chain[-1]] not in seen:
            parent = incoming[chain[-1]]
            if parent not in by_id:
                break
            chain.append(parent)
            seen.add(parent)
        return tuple(reversed(chain))


@dataclass(frozen=True, slots=True)
class CoachingFinding:
    id: str
    finding_type: FindingType
    segment_id: str
    segment_label: str
    phase: str
    measured_loss_s: float
    attributed_low_s: float
    attributed_high_s: float
    confidence: float
    repeatability: float
    opportunity_score: float
    facts: tuple[MetricFact, ...]
    evidence_node_ids: tuple[str, ...]
    action: str
    drill: str | None = None
    positive: bool = False
    algorithm_version: str = "coaching_rules_v1"


@dataclass(frozen=True, slots=True)
class RankedFinding:
    rank: int
    finding: CoachingFinding
    adjusted_score: float


@dataclass(frozen=True, slots=True)
class CoachingResult:
    graph: EvidenceGraph
    findings: tuple[CoachingFinding, ...]
    ranked: tuple[RankedFinding, ...]


@dataclass(frozen=True, slots=True)
class CoachingConfig:
    brake_distance_threshold_m: float = 5.0
    min_speed_timing_threshold_m: float = 5.0
    min_speed_threshold_mps: float = 1.0
    throttle_distance_threshold_m: float = 5.0
    line_offset_threshold_m: float = 0.5
    line_boundary_confidence_min: float = 0.70
    steering_correction_threshold: float = 1.0
    consistency_mad_threshold_s: float = 0.08
    strength_gain_threshold_s: float = 0.05
    target_loss_s: float = 0.25
    max_ranked: int = 3

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            if float(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")


def opportunity_score(
    time_loss_s: float,
    *,
    target_loss_s: float,
    confidence: float,
    repeatability: float,
    actionability: float = 1.0,
    compatibility_weight: float = 1.0,
) -> float:
    if target_loss_s <= 0:
        raise ValueError("target_loss_s must be positive")
    score = (
        _bounded(max(0.0, time_loss_s) / target_loss_s)
        * _bounded(confidence)
        * (0.5 + 0.5 * _bounded(repeatability))
        * _bounded(actionability)
        * _bounded(compatibility_weight)
    )
    return _bounded(score)


_FAMILIES = {
    FindingType.BRAKE_TOO_EARLY: "braking",
    FindingType.BRAKE_TOO_LATE: "braking",
    FindingType.MINIMUM_SPEED_TOO_EARLY: "corner_speed",
    FindingType.MINIMUM_SPEED_TOO_LATE: "corner_speed",
    FindingType.MINIMUM_SPEED_TOO_LOW: "corner_speed",
    FindingType.THROTTLE_TOO_LATE: "throttle",
    FindingType.LINE_DISPLACEMENT: "line",
    FindingType.STEERING_CORRECTION: "rotation",
    FindingType.GEAR_MISMATCH: "gear",
    FindingType.INCONSISTENT_EXECUTION: "consistency",
    FindingType.STRENGTH: "strength",
}


def rank_findings(
    findings: Iterable[CoachingFinding],
    *,
    limit: int = 3,
    same_segment_penalty: float = 0.55,
    same_family_penalty: float = 0.65,
) -> tuple[RankedFinding, ...]:
    """Greedy bounded ranking with explicit segment/root-cause diversity."""

    remaining = list(findings)
    selected: list[RankedFinding] = []
    segment_counts: dict[str, int] = {}
    family_counts: dict[str, int] = {}
    for rank in range(1, max(0, limit) + 1):
        if not remaining:
            break
        scored: list[tuple[float, CoachingFinding]] = []
        for finding in remaining:
            family = _FAMILIES[finding.finding_type]
            adjusted = (
                finding.opportunity_score
                * same_segment_penalty ** segment_counts.get(finding.segment_id, 0)
                * same_family_penalty ** family_counts.get(family, 0)
            )
            scored.append((_bounded(adjusted), finding))
        adjusted, chosen = max(
            scored,
            key=lambda item: (
                item[0],
                item[1].confidence,
                -item[1].measured_loss_s if item[1].positive else item[1].measured_loss_s,
                item[1].id,
            ),
        )
        selected.append(RankedFinding(rank, chosen, adjusted))
        remaining.remove(chosen)
        segment_counts[chosen.segment_id] = segment_counts.get(chosen.segment_id, 0) + 1
        family = _FAMILIES[chosen.finding_type]
        family_counts[family] = family_counts.get(family, 0) + 1
    return tuple(selected)


@dataclass(slots=True)
class _Draft:
    finding_type: FindingType
    phase: str
    fact_keys: tuple[str, ...]
    action: str
    drill: str | None
    severity: float
    causal_support: float
    positive: bool = False


def _fact(evidence: SegmentEvidence, key: str) -> MetricFact | None:
    fact = evidence.facts.get(key)
    return fact if fact is not None and fact.available else None


def _confidence(evidence: SegmentEvidence, draft: _Draft) -> float:
    facts = [evidence.facts[key] for key in draft.fact_keys if key in evidence.facts]
    detector = min((fact.confidence for fact in facts), default=0.0)
    sample_strength = min(1.0, evidence.sample_count / 5.0) if evidence.sample_count else 0.0
    return ConfidenceComponents(
        data_coverage=evidence.data_coverage,
        detector_stability=detector,
        model_quality=evidence.model_quality,
        causal_support=draft.causal_support,
        compatibility_weight=evidence.compatibility_weight,
        sample_strength=sample_strength,
    ).score


def build_coaching_evidence(
    evidence: SegmentEvidence,
    config: CoachingConfig | None = None,
) -> CoachingResult:
    """Evaluate deterministic rule families and construct their causal graph."""

    cfg = config or CoachingConfig()
    drafts: list[_Draft] = []

    brake = _fact(evidence, "brake_onset_m")
    if brake and brake.delta is not None:
        if brake.delta <= -cfg.brake_distance_threshold_m:
            drafts.append(
                _Draft(
                    FindingType.BRAKE_TOO_EARLY,
                    "braking",
                    (brake.key,),
                    "Move the initial brake point later in small, repeatable steps while keeping the release smooth.",
                    "Move the marker five metres at a time and compare minimum-speed timing.",
                    abs(brake.delta) / cfg.brake_distance_threshold_m,
                    0.90,
                )
            )
        elif brake.delta >= cfg.brake_distance_threshold_m:
            drafts.append(
                _Draft(
                    FindingType.BRAKE_TOO_LATE,
                    "braking",
                    (brake.key,),
                    "Begin braking slightly earlier so the car is settled before turn-in.",
                    "Move the marker five metres earlier and preserve the same release shape.",
                    abs(brake.delta) / cfg.brake_distance_threshold_m,
                    0.85,
                )
            )

    min_position = _fact(evidence, "minimum_speed_distance_m")
    if min_position and min_position.delta is not None:
        if min_position.delta <= -cfg.min_speed_timing_threshold_m:
            drafts.append(
                _Draft(
                    FindingType.MINIMUM_SPEED_TOO_EARLY,
                    "apex",
                    (min_position.key,),
                    "Keep the car decelerating toward the apex instead of completing the slowdown early.",
                    None,
                    abs(min_position.delta) / cfg.min_speed_timing_threshold_m,
                    0.75,
                )
            )
        elif min_position.delta >= cfg.min_speed_timing_threshold_m:
            drafts.append(
                _Draft(
                    FindingType.MINIMUM_SPEED_TOO_LATE,
                    "apex",
                    (min_position.key,),
                    "Finish rotation earlier so minimum speed is reached nearer the apex.",
                    None,
                    abs(min_position.delta) / cfg.min_speed_timing_threshold_m,
                    0.75,
                )
            )

    min_speed = _fact(evidence, "minimum_speed_mps")
    if min_speed and min_speed.delta is not None and min_speed.delta <= -cfg.min_speed_threshold_mps:
        drafts.append(
            _Draft(
                FindingType.MINIMUM_SPEED_TOO_LOW,
                "apex",
                (min_speed.key,),
                "Release enough brake to retain more minimum speed without widening the exit.",
                None,
                abs(min_speed.delta) / cfg.min_speed_threshold_mps,
                0.75,
            )
        )

    throttle = _fact(evidence, "throttle_pickup_m")
    if throttle and throttle.delta is not None and throttle.delta >= cfg.throttle_distance_threshold_m:
        drafts.append(
            _Draft(
                FindingType.THROTTLE_TOO_LATE,
                "exit",
                (throttle.key,),
                "Commit to the first sustained throttle earlier once the car is rotated.",
                "Repeat the corner while targeting one clean throttle application.",
                throttle.delta / cfg.throttle_distance_threshold_m,
                0.75,
            )
        )

    line = _fact(evidence, "line_offset_m")
    line_evidence = (
        evidence.track_boundary_confidence >= cfg.line_boundary_confidence_min
        or evidence.line_outcome_supported
    )
    if (
        line
        and line.delta is not None
        and abs(line.delta) >= cfg.line_offset_threshold_m
        and line_evidence
    ):
        drafts.append(
            _Draft(
                FindingType.LINE_DISPLACEMENT,
                "line",
                (line.key,),
                "Use the available track width to move the line toward the repeatably faster outcome.",
                None,
                abs(line.delta) / cfg.line_offset_threshold_m,
                0.70 if evidence.line_outcome_supported else 0.60,
            )
        )

    steering = _fact(evidence, "steering_corrections")
    if steering and steering.delta is not None and steering.delta >= cfg.steering_correction_threshold:
        drafts.append(
            _Draft(
                FindingType.STEERING_CORRECTION,
                "rotation",
                (steering.key,),
                "Use one cleaner steering build and unwind instead of correcting after turn-in.",
                "Prioritise a stable entry and count post-turn-in corrections.",
                steering.delta / cfg.steering_correction_threshold,
                0.70,
            )
        )

    gear = _fact(evidence, "gear_at_apex")
    if gear and gear.delta is not None and gear.delta != 0 and evidence.gear_outcome_supported:
        drafts.append(
            _Draft(
                FindingType.GEAR_MISMATCH,
                "apex",
                (gear.key,),
                "Test the reference gear while preserving the same line and throttle point.",
                None,
                min(2.0, abs(gear.delta)),
                0.60,
            )
        )

    consistency = _fact(evidence, "segment_time_mad_s")
    if (
        consistency
        and consistency.candidate is not None
        and consistency.candidate >= cfg.consistency_mad_threshold_s
        and evidence.sample_count >= 3
    ):
        reference_mad = consistency.reference or 0.0
        if consistency.candidate > reference_mad:
            drafts.append(
                _Draft(
                    FindingType.INCONSISTENT_EXECUTION,
                    "segment",
                    (consistency.key,),
                    "Prioritise repeating the same marker and control sequence before chasing more peak speed.",
                    "Run five laps using one fixed brake and turn-in marker.",
                    consistency.candidate / cfg.consistency_mad_threshold_s,
                    0.90,
                )
            )

    if evidence.measured_loss_s <= -cfg.strength_gain_threshold_s or (
        evidence.segment_percentile is not None and evidence.segment_percentile >= 0.80
    ):
        strength_facts = tuple(
            fact.key for fact in evidence.facts.values() if fact.available
        )[:2]
        drafts.append(
            _Draft(
                FindingType.STRENGTH,
                "segment",
                strength_facts,
                "Preserve this control timing and line; it is a repeatable strength.",
                None,
                max(1.0, abs(evidence.measured_loss_s) / cfg.strength_gain_threshold_s),
                0.90,
                positive=True,
            )
        )

    nodes: list[EvidenceNode] = []
    node_for_type: dict[FindingType, str] = {}
    for index, draft in enumerate(drafts):
        node_id = f"ev_{evidence.segment_id}_{draft.finding_type}_{index}"
        node_for_type[draft.finding_type] = node_id
        nodes.append(
            EvidenceNode(
                node_id,
                str(draft.finding_type),
                evidence.segment_id,
                draft.fact_keys,
                _confidence(evidence, draft),
                True,
            )
        )

    edges: list[EvidenceEdge] = []
    brake_node = node_for_type.get(FindingType.BRAKE_TOO_EARLY) or node_for_type.get(FindingType.BRAKE_TOO_LATE)
    minimum_nodes = [
        node_for_type[kind]
        for kind in (
            FindingType.MINIMUM_SPEED_TOO_EARLY,
            FindingType.MINIMUM_SPEED_TOO_LATE,
            FindingType.MINIMUM_SPEED_TOO_LOW,
        )
        if kind in node_for_type
    ]
    if brake_node:
        edges.extend(
            EvidenceEdge(brake_node, node, "likely_contributed", 0.75)
            for node in minimum_nodes
        )
    throttle_node = node_for_type.get(FindingType.THROTTLE_TOO_LATE)
    if throttle_node and minimum_nodes:
        edges.append(EvidenceEdge(minimum_nodes[0], throttle_node, "likely_contributed", 0.70))
    elif throttle_node and brake_node:
        edges.append(EvidenceEdge(brake_node, throttle_node, "associated_with", 0.55))

    loss = max(0.0, float(evidence.measured_loss_s))
    weighted = [draft for draft in drafts if not draft.positive]
    total_weight = sum(max(0.01, draft.severity * _confidence(evidence, draft)) for draft in weighted)
    findings: list[CoachingFinding] = []
    for index, draft in enumerate(drafts):
        confidence = _confidence(evidence, draft)
        if draft.positive:
            high = low = 0.0
            score = opportunity_score(
                abs(min(0.0, evidence.measured_loss_s)),
                target_loss_s=cfg.target_loss_s,
                confidence=confidence,
                repeatability=evidence.repeatability,
                actionability=0.55,
                compatibility_weight=evidence.compatibility_weight,
            )
        else:
            high = (
                loss * max(0.01, draft.severity * confidence) / total_weight
                if total_weight > 0
                else 0.0
            )
            low = high * 0.5 * confidence
            score = opportunity_score(
                high,
                target_loss_s=cfg.target_loss_s,
                confidence=confidence,
                repeatability=evidence.repeatability,
                compatibility_weight=evidence.compatibility_weight,
            )
        node_id = f"ev_{evidence.segment_id}_{draft.finding_type}_{index}"
        findings.append(
            CoachingFinding(
                id=f"finding_{evidence.segment_id}_{draft.finding_type}_{index}",
                finding_type=draft.finding_type,
                segment_id=evidence.segment_id,
                segment_label=evidence.label,
                phase=draft.phase,
                measured_loss_s=evidence.measured_loss_s,
                attributed_low_s=min(loss, low),
                attributed_high_s=min(loss, high),
                confidence=confidence,
                repeatability=evidence.repeatability,
                opportunity_score=score,
                facts=tuple(evidence.facts[key] for key in draft.fact_keys if key in evidence.facts),
                evidence_node_ids=(node_id,),
                action=draft.action,
                drill=draft.drill,
                positive=draft.positive,
            )
        )
    result_findings = tuple(findings)
    return CoachingResult(
        EvidenceGraph(tuple(nodes), tuple(edges)),
        result_findings,
        rank_findings(result_findings, limit=cfg.max_ranked),
    )
