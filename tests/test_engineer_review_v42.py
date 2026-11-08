"""Phase 7 gates: the engineer explains deterministic findings faithfully.

These lock down the contract in section 12.14 of the 4.2 plan: prompts carry
the provenance rules, spoken answers name their comparison basis, unsupported
rival inputs surface as a limitation rather than a fabricated value, and lap
or session end selects diverse findings plus one positive pattern.
"""

from __future__ import annotations

from typing import Any

import pytest

from pitwall.brain import DEBRIEF_PERSONA, PERSONA, compose_persona, persona_addenda
from pitwall.briefing import BriefingEngine


# --------------------------------------------------------------------------
# Prompt contract
# --------------------------------------------------------------------------


# The provenance contract is attached wherever the tools that return stored
# measurements are offered (the deep route, and the A/B comparison).
_REVIEW_TOOLS = {"list_saved_sessions", "get_session_summary", "compare_laps", "get_lap_findings"}


def _review_brief(verbosity: str | None = None) -> str:
    return compose_persona(
        PERSONA,
        verbosity,
        addenda=persona_addenda({}, "review my saved session from yesterday", _REVIEW_TOOLS),
    )


@pytest.mark.parametrize(
    "phrase",
    [
        "observed",
        "derived",
        "estimated",
        "stale",
        "unavailable",
    ],
)
def test_persona_declares_every_provenance_label(phrase: str) -> None:
    """The model cannot honour a provenance contract it was never given."""

    assert phrase in _review_brief()


def test_persona_requires_naming_the_comparison_basis() -> None:
    assert "name the comparison basis" in _review_brief()
    assert "A delta without its reference is" in _review_brief()


def test_persona_forbids_substituting_zero_for_unavailable() -> None:
    assert "Never substitute zero" in _review_brief()


def test_persona_separates_measured_attributed_and_opportunity() -> None:
    assert "Never promote an attributed interval or an opportunity into a measured fact." in _review_brief()


def test_persona_blocks_coaching_rival_technique_without_data() -> None:
    # Any reply to the driver may compare a rival, so this rule is in the
    # question brief, not only in the saved-session contract.
    brief = compose_persona(PERSONA, addenda=persona_addenda({}, "where is Max quicker than me", set()))
    assert "Never coach a rival's technique from data the" in brief
    assert "never fill the gap with the player's own values" in brief


def test_debrief_persona_speaks_findings_with_segment_and_reference() -> None:
    assert "ranked findings" in DEBRIEF_PERSONA
    assert "name the" in DEBRIEF_PERSONA.lower()
    assert "Never sum attributed ranges" in DEBRIEF_PERSONA


def test_custom_persona_cannot_override_the_provenance_contract() -> None:
    """A user persona is sandwiched between the brief and the safety anchor."""

    composed = _review_brief("chatty")
    assert "Never substitute zero" in composed
    assert composed.rstrip().endswith(
        "tool's compound-rule legality and neutralisation state."
    )


# --------------------------------------------------------------------------
# Finding selection for lap/session end
# --------------------------------------------------------------------------


def _finding(
    index: int,
    segment: str,
    *,
    positive: bool = False,
    loss: float = 0.20,
) -> dict[str, Any]:
    return {
        "finding_id": f"f_{index:02d}",
        "type": "brake_too_early",
        "rank": index,
        "segment_id": segment,
        "segment_label": segment.replace("_", " ").title(),
        "measured_loss_s": loss,
        "attributed_low_s": loss * 0.4,
        "attributed_high_s": loss * 0.7,
        "confidence": 0.9,
        "repeatability": 0.75,
        "opportunity_score": 0.6,
        "action": "Carry the brake later in five metre steps.",
        "positive": positive,
        "algorithm_version": "coaching_v2",
        "facts": [],
    }


class _FindingsTools:
    """Minimal stand-in for the tool layer's persisted-finding query."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, int]] = []

    async def get_lap_findings(self, comparison_id: str, limit: int = 3) -> dict[str, Any]:
        self.calls.append((comparison_id, limit))
        return self.payload


def _engine(payload: dict[str, Any]) -> tuple[BriefingEngine, _FindingsTools]:
    tools = _FindingsTools(payload)
    engine = BriefingEngine.__new__(BriefingEngine)
    engine.tools = tools  # type: ignore[attr-defined]
    return engine, tools


@pytest.mark.asyncio
async def test_review_findings_prefers_distinct_segments() -> None:
    """Ten variants of one corner must not fill a spoken debrief."""

    payload = {
        "available": True,
        "findings": [
            _finding(1, "turn_07"),
            _finding(2, "turn_07"),
            _finding(3, "turn_07"),
            _finding(4, "turn_02"),
            _finding(5, "turn_11"),
        ],
        "units": {"measured_loss_s": "s"},
    }
    engine, _ = _engine(payload)

    result = await engine.review_findings("cmp_1", limit=3)

    assert result["available"]
    segments = [item["segment_id"] for item in result["findings"]]
    assert segments == ["turn_07", "turn_02", "turn_11"]
    assert len(set(segments)) == 3


@pytest.mark.asyncio
async def test_review_findings_returns_one_positive_pattern() -> None:
    payload = {
        "available": True,
        "findings": [
            _finding(1, "turn_07"),
            _finding(2, "turn_04", positive=True, loss=-0.15),
            _finding(3, "turn_09"),
        ],
    }
    engine, _ = _engine(payload)

    result = await engine.review_findings("cmp_1", limit=3)

    assert result["positive"] is not None
    assert result["positive"]["segment_id"] == "turn_04"
    # The positive pattern answers a different question and must not consume
    # one of the improvement slots.
    assert all(not item["positive"] for item in result["findings"])
    assert [item["segment_id"] for item in result["findings"]] == ["turn_07", "turn_09"]


@pytest.mark.asyncio
async def test_review_findings_reports_unavailable_without_fabricating() -> None:
    engine, _ = _engine({"available": False, "reason": "comparison not analysed"})

    result = await engine.review_findings("cmp_missing")

    assert result["available"] is False
    assert result["findings"] == []
    assert result["positive"] is None
    assert "comparison not analysed" in result["reason"]


@pytest.mark.asyncio
async def test_review_findings_respects_the_requested_limit() -> None:
    payload = {
        "available": True,
        "findings": [_finding(i, f"turn_{i:02d}") for i in range(1, 9)],
    }
    engine, _ = _engine(payload)

    result = await engine.review_findings("cmp_1", limit=2)

    assert len(result["findings"]) == 2


@pytest.mark.asyncio
async def test_review_findings_never_returns_more_than_the_service_supplied() -> None:
    """A larger limit cannot invent findings that were not measured."""

    payload = {"available": True, "findings": [_finding(1, "turn_07")]}
    engine, _ = _engine(payload)

    result = await engine.review_findings("cmp_1", limit=5)

    assert len(result["findings"]) == 1
