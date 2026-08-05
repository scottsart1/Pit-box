from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest


def _packet(index: int) -> SimpleNamespace:
    return SimpleNamespace(
        key=SimpleNamespace(
            packet_id=index,
            source_ip="192.168.1.61",
            source_port=54022,
        ),
        status="healthy",
        observed_hz_10s=59.81,
        expected_hz=60.0,
        last_age_ms=12.34,
        received=1000 + index,
        valid_parsed=999 + index,
        parse_errors=1,
        provisional_gaps=2,
        confirmed_lost=3,
        duplicates=4,
        out_of_order=5,
        jitter_ms=1.234,
    )


class FakeNetworkService:
    async def snapshot(self) -> SimpleNamespace:
        listener = SimpleNamespace(
            state=SimpleNamespace(value="receiving"),
            bind_host="0.0.0.0",
            port=20777,
            last_valid_packet_age_ms=12,
            error=None,
        )
        recommendation = SimpleNamespace(
            recommended=SimpleNamespace(
                address="192.168.1.42", adapter_id="wifi-guid"
            ),
            confidence="high",
            warnings=(),
        )
        forwarders = tuple(
            SimpleNamespace(
                target=SimpleNamespace(
                    id=f"target_{index}",
                    label=f"Target {index}",
                    enabled=True,
                    host="127.0.0.1",
                    port=20778 + index,
                ),
                counters=SimpleNamespace(
                    sent_packets=index,
                    sent_bytes=index * 20,
                    queue_drops=0,
                    socket_errors=0,
                    last_error=None,
                ),
            )
            for index in range(18)
        )
        return SimpleNamespace(
            listener=listener,
            recommendation=recommendation,
            packet_health=SimpleNamespace(
                packets=tuple(_packet(index) for index in range(18)),
                invalid=(SimpleNamespace(received=2),),
            ),
            source={"ip": "192.168.1.61", "port": 54022},
            game={"packet_format": 2026, "session_uid": "44"},
            queues={
                "receiver": SimpleNamespace(
                    depth=1,
                    capacity=256,
                    high_water=9,
                    drops=0,
                    last_drain_age_ms=1,
                )
            },
            forwarders=forwarders,
            warnings=("example warning",),
        )


class FakeCatalog:
    async def list_sessions(self, *, limit: int) -> dict[str, Any]:
        return {
            "items": [
                {
                    "id": f"ses_{index}",
                    "display_name": f"Session {index}",
                    "track_id": 7,
                    "session_type": "Race",
                    "mode_profile": "race",
                    "started_at": "2026-08-05T12:00:00Z",
                    "ended_at": None,
                    "status": "complete",
                    "capture_mode": "balanced",
                    "starred": False,
                    "quality_score": 0.9,
                    "drivers_observed": 20,
                    "lap_count": 40,
                    "size_bytes": 2000,
                    "large_unbounded_field": [0] * 100,
                }
                for index in range(limit + 3)
            ],
            "has_more": True,
            "next_cursor": "opaque-cursor",
        }

    async def get_session(self, key: str) -> dict[str, Any] | None:
        if key == "missing":
            return None
        return {
            "id": key,
            "display_name": "Test race",
            "track_id": 7,
            "track_layout_signature": "f1:2026:7:5000",
            "session_type": "Race",
            "mode_profile": "race",
            "started_at": "2026-08-05T12:00:00Z",
            "ended_at": "2026-08-05T13:30:00Z",
            "status": "complete",
            "packet_format": 2026,
            "capture_mode": "balanced",
            "quality_score": 0.92,
            "starred": True,
            "tags": ["league"],
            "participants": [
                {
                    "id": f"car_{index}",
                    "car_index": index,
                    "identity_revision": 0,
                    "display_name": f"Driver {index}",
                    "race_number": index + 1,
                    "team_id": 1,
                    "is_ai": 0,
                    "is_player": index == 0,
                    "identity_confidence": 0.95,
                    "private_detail": "not returned",
                }
                for index in range(30)
            ],
            "derived": {
                "comparisons": 4,
                "jobs": [
                    {
                        "id": f"job_{index}",
                        "kind": "reprocess",
                        "state": "queued",
                        "progress": 0,
                        "created_at": "2026-08-05T14:00:00Z",
                    }
                    for index in range(8)
                ],
            },
        }

    async def get_quality(self, key: str) -> dict[str, Any] | None:
        if key == "missing":
            return None
        return {
            "session_id": key,
            "status": "complete",
            "quality_score": 0.87,
            "participants_observed": 20,
            "laps": {"total": 40, "valid": 35, "mean_coverage": 0.94},
            "trace_manifests": {"ready": 38},
            "raw_captures": {"count": 1, "packets": 50000},
            "packet_health_available": True,
            "warnings": [f"warning {index}" for index in range(15)],
        }


def _finding(index: int) -> dict[str, Any]:
    return {
        "finding_id": f"finding_{index}",
        "type": "brake_too_early",
        "rank": index + 1,
        "segment_id": "turn_7",
        "segment_label": "Turn 7",
        "phase": "braking",
        "measured_loss_s": 0.18,
        "attributed_low_s": 0.09,
        "attributed_high_s": 0.15,
        "confidence": 0.91,
        "repeatability": 0.76,
        "opportunity_score": 0.55,
        "action": "Brake five metres later.",
        "drill": "Move the marker in small steps.",
        "positive": False,
        "algorithm_version": "brake_v2",
        "facts": [
            {
                "key": f"fact_{fact}",
                "candidate": 10.0,
                "reference": 5.0,
                "delta": 5.0,
                "unit": "m",
                "confidence": 0.9,
                "availability": "derived",
                "evidence_ids": ["hidden-in-compact-projection"],
            }
            for fact in range(9)
        ],
        "evidence": [f"node_{node}" for node in range(12)],
    }


class FakeComparisonService:
    async def list_references(self, lap_id: str) -> dict[str, Any]:
        return {
            "candidate_lap_id": lap_id,
            "items": [
                {
                    "lap_id": f"lap_{index}",
                    "lap_number": index,
                    "lap_time_ms": 90000 + index,
                    "driver": "Driver",
                    "session_id": "ses_1",
                    "is_player": True,
                    "suggested": index == 0,
                    "compatibility": {
                        "class": "strict",
                        "compatibility_weight": 1.0,
                        "allows_coaching": True,
                        "caveats": [],
                        "issues": [],
                    },
                    "reasons": ["same track/layout"],
                }
                for index in range(25)
            ],
        }

    async def create_comparison(
        self,
        candidate: str,
        *,
        reference_kind: str,
        reference_lap_id: str | None,
        allow_caveated_reference: bool,
    ) -> dict[str, Any]:
        return {
            "comparison_id": "cmp_1",
            "candidate": {"lap_id": candidate},
            "reference": {
                "kind": reference_kind,
                "lap_id": reference_lap_id or "resolved_pb",
            },
            "compatibility": {
                "class": "strict",
                "compatibility_weight": 1.0,
                "allows_coaching": True,
                "caveats": [],
            },
            "algorithm_bundle": "analysis_4.2.0",
            "coverage_ratio": 0.98,
            "quality_score": 0.93,
            "lap_delta_s": 0.42,
            "sign_convention": "positive means candidate arrived later",
            "segments": [
                {
                    "segment_id": f"segment_{index}",
                    "label": f"Segment {index}",
                    "ordinal": index,
                    "start_m": index * 100,
                    "end_m": (index + 1) * 100,
                    "delta_s": 0.01,
                    "coverage": 0.99,
                    "model_source": "test",
                    "large_metrics": [0] * 100,
                }
                for index in range(20)
            ],
            "findings": [_finding(index) for index in range(5)],
        }

    async def get_findings(self, comparison_id: str) -> dict[str, Any]:
        return {
            "comparison_id": comparison_id,
            "findings": [_finding(index) for index in range(12)],
        }


class FakeFieldService:
    async def summary(self, session_id: str) -> dict[str, Any]:
        return {
            "cars_observed": 24,
            "lap_rows": 80,
            "classification_availability": "unavailable",
            "classification_reason": "official saved classification unavailable",
            "classification": [
                {
                    "car_id": f"car_{index}",
                    "display_name": f"Driver {index}",
                    "position": {"value": None, "availability": "unavailable"},
                    "best_lap_ms": {"value": 90000, "availability": "derived"},
                    "median_clean_pace_ms": {
                        "value": 90500,
                        "availability": "derived",
                    },
                    "laps_recorded": {"value": 4, "availability": "observed"},
                    "compound": {"value": "MEDIUM", "availability": "observed"},
                    "status": {"value": None, "availability": "unavailable"},
                }
                for index in range(30)
            ],
            "warnings": [],
            "truncated": False,
        }

    async def driver(self, session_id: str, car_id: str) -> dict[str, Any]:
        item = {
            "segment_id": "turn_7",
            "label": "Turn 7",
            "median_time_s": 6.2,
            "delta_to_field_median_s": -0.12,
            "performance_percentile": 0.9,
            "rank": 2,
            "sample_n": 4,
            "field_n": 18,
        }
        return {
            "driver": {"car_id": car_id, "display_name": "Driver 1"},
            "summary": {"lap_count": 5, "comparable_lap_count": 4},
            "strengths": {
                "availability": "derived",
                "reason": None,
                "n": 4,
                "items": [item] * 5,
            },
            "weaknesses": {
                "availability": "derived",
                "reason": None,
                "n": 4,
                "items": [item] * 5,
            },
            "truncated": False,
        }

    async def corners(self, session_id: str) -> dict[str, Any]:
        return {
            "availability": "derived",
            "reason": None,
            "drivers": [
                {"car_id": "car_1", "display_name": "Driver 1"},
                {"car_id": "car_2", "display_name": "Driver 2"},
            ],
            "segments": [
                {"segment_id": "turn_7", "label": "Turn 7"},
                {"segment_id": "turn_8", "label": "Turn 8"},
            ],
            "median_time_s": [[6.2, 8.0], [6.1, 8.2]],
            "delta_to_field_median_s": [[0.05, -0.1], [-0.05, 0.1]],
            "performance_percentile": [[0.4, 0.9], [0.8, 0.2]],
            "rank": [[2.0, 1.0], [1.0, 2.0]],
            "valid_mask": [[True, True], [True, True]],
            "field_median_s": [6.15, 8.1],
            "n_by_segment": [2, 2],
            "sample_count": [[3, 4], [5, 6]],
            "source_rows": 8,
            "truncated": False,
        }


@pytest.mark.asyncio
async def test_legacy_constructor_keeps_v42_tools_safe_when_unwired(stack) -> None:
    *_, tools = stack

    assert (await tools.get_connection_health())["available"] is False
    assert (await tools.list_saved_sessions())["available"] is False
    assert (await tools.get_data_quality("ses_1"))["available"] is False


@pytest.mark.asyncio
async def test_connection_health_is_bounded_and_keeps_measured_counters(stack) -> None:
    *_, tools = stack
    tools.network_service = FakeNetworkService()

    result = await tools.get_connection_health()

    assert result["receiving"] is True
    assert result["recommendation"]["console_destination_ipv4"] == "192.168.1.42"
    assert result["invalid_datagrams"] == 2
    assert len(result["packets"]) == 16
    assert len(result["forwarders"]) == 16
    assert result["packets"][0]["confirmed_lost"] == 3
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_saved_session_tools_trim_repository_models(stack) -> None:
    *_, tools = stack
    tools.session_catalog = FakeCatalog()
    tools.field_analysis_service = FakeFieldService()

    listed = await tools.list_saved_sessions(2)
    summary = await tools.get_session_summary("ses_1")
    quality = await tools.get_data_quality("ses_1")

    assert len(listed["items"]) == 2
    assert "large_unbounded_field" not in listed["items"][0]
    assert summary["participant_count"] == 30
    assert len(summary["participants"]) == 24
    assert len(summary["recent_jobs"]) == 5
    assert len(summary["field"]["classification"]) == 24
    assert quality["quality_unit"] == "ratio"
    assert len(quality["warnings"]) == 10
    with pytest.raises(ValueError, match="between 1 and 20"):
        await tools.list_saved_sessions(21)


@pytest.mark.asyncio
async def test_comparison_tools_return_bounded_typed_findings(stack) -> None:
    *_, tools = stack
    tools.comparison_service = FakeComparisonService()

    references = await tools.list_reference_laps("lap_candidate", 4)
    comparison = await tools.compare_laps(
        "lap_candidate", "lap", "lap_reference", False
    )
    findings = await tools.get_lap_findings("cmp_1", 5)

    assert len(references["references"]) == 4
    assert references["truncated"] is True
    assert len(comparison["segments"]) == 15
    assert len(comparison["findings"]) == 3
    assert len(comparison["findings"][0]["facts"]) == 6
    assert len(comparison["findings"][0]["evidence"]) == 8
    assert comparison["lap_delta_s"] == 0.42
    assert len(findings["findings"]) == 5
    assert findings["units"]["confidence"] == "ratio"


@pytest.mark.asyncio
async def test_field_tools_use_service_ranks_without_raw_matrices(stack) -> None:
    *_, tools = stack
    tools.field_analysis_service = FakeFieldService()

    strengths = await tools.get_driver_strengths("ses_1", "car_1")
    turn = await tools.get_field_corner_rankings("ses_1", "turn_7", 2)
    overview = await tools.get_field_corner_rankings("ses_1", "all", 20)

    assert strengths["available"] is True
    assert len(strengths["strengths"]["items"]) == 3
    assert [item["rank"] for item in turn["segments"][0]["rankings"]] == [1.0, 2.0]
    assert len(overview["segments"]) == 2
    assert all(len(item["rankings"]) == 1 for item in overview["segments"])
    assert "valid_mask" not in turn


@pytest.mark.asyncio
async def test_v42_tool_schemas_are_strict_and_bounded(stack) -> None:
    *_, tools = stack
    by_name = {item["name"]: item for item in tools.schemas()}
    expected = {
        "get_connection_health",
        "list_saved_sessions",
        "get_session_summary",
        "list_reference_laps",
        "compare_laps",
        "get_lap_findings",
        "get_driver_strengths",
        "get_field_corner_rankings",
        "get_data_quality",
    }

    assert expected <= by_name.keys()
    assert by_name["list_saved_sessions"]["parameters"]["properties"]["limit"][
        "maximum"
    ] == 20
    for name in expected:
        parameters = by_name[name]["parameters"]
        assert parameters["additionalProperties"] is False
        assert set(parameters["required"]) == set(parameters["properties"])
