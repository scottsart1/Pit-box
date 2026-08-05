from __future__ import annotations

from pathlib import Path

import pytest

from pitwall.session_assembler import (
    EventStamp,
    FlashbackEvent,
    LapEvent,
    ParticipantEvent,
    SampleEvent,
    SessionAssembler,
    SessionEvent,
)
from pitwall.trace_store import TraceStore


def stamp(
    uid: str,
    frame: int,
    session_time_s: float,
    *,
    monotonic_ns: int | None = None,
) -> EventStamp:
    return EventStamp(
        session_uid=uid,
        frame_identifier=frame,
        overall_frame_identifier=frame,
        session_time_s=session_time_s,
        monotonic_ns=(
            monotonic_ns
            if monotonic_ns is not None
            else 1_000_000_000 + frame * 1_000_000
        ),
        wall_ns=1_800_000_000_000_000_000 + frame * 1_000_000,
    )


def begin(
    assembler: SessionAssembler,
    uid: str = "session-a",
    *,
    player_car_index: int = 0,
    restart: bool = False,
) -> None:
    assembler.consume(
        SessionEvent(
            stamp(uid, 0, 0.0),
            track_id=7,
            layout_signature="spa-main",
            session_type=10,
            packet_format=2026,
            player_car_index=player_car_index,
            restart_evidence=restart,
        )
    )


def participant(
    assembler: SessionAssembler,
    car_index: int,
    name: str,
    *,
    uid: str = "session-a",
    frame: int = 1,
    driver_id: int | None = None,
) -> None:
    assembler.consume(
        ParticipantEvent(
            stamp(uid, frame, frame / 100),
            car_index,
            {
                "name": name,
                "driver_id": driver_id,
                "race_number": car_index + 10,
            },
        )
    )


def sample_event(
    uid: str,
    car_index: int,
    lap_number: int,
    frame: int,
    *,
    monotonic_ns: int | None = None,
    group: str = "telemetry",
    speed: float = 70.0,
    distance: float | None = None,
    retain_all: bool = False,
) -> SampleEvent:
    values: dict[str, float | None] = {
        "lap_distance_m": float(frame if distance is None else distance),
        "speed_mps": speed,
        "brake": None,
    }
    return SampleEvent(
        stamp(uid, frame, frame / 10, monotonic_ns=monotonic_ns),
        car_index=car_index,
        lap_number=lap_number,
        sample_group=group,
        values=values,
        availability={"brake": "unavailable"},
        provenance={"speed_mps": "observed", "brake": "unavailable"},
        units={"lap_distance_m": "m", "speed_mps": "m/s", "brake": "ratio"},
        freshness_ms=250,
        retain_all=retain_all,
    )


def complete_lap(
    assembler: SessionAssembler,
    car_index: int,
    lap_number: int,
    frame: int,
    *,
    uid: str = "session-a",
):
    return assembler.consume(
        LapEvent(
            stamp(uid, frame, frame / 10),
            car_index=car_index,
            completed_lap_number=lap_number,
            next_lap_number=lap_number + 1,
            lap_time_ms=91_234,
            valid=True,
            context={"compound": "soft"},
        )
    )


def test_full_field_coalescing_keeps_player_rate_and_emits_trace_store_batches(
    tmp_path: Path,
) -> None:
    emitted = []
    assembler = SessionAssembler(field_trace_hz=20, batch_sink=emitted.append)
    begin(assembler)
    participant(assembler, 0, "Player", driver_id=7)
    participant(assembler, 1, "Rival", driver_id=9)
    participant(assembler, 2, "Observed without trace", driver_id=58)

    base_ns = 2_000_000_000
    for offset in range(10):
        frame = 2 + offset
        event_ns = base_ns + offset * 10_000_000  # 100 Hz input.
        assembler.consume(
            sample_event(
                "session-a",
                0,
                1,
                frame,
                monotonic_ns=event_ns,
                distance=float(offset),
                speed=70.0 + offset,
            )
        )
        assembler.consume(
            sample_event(
                "session-a",
                1,
                1,
                frame,
                monotonic_ns=event_ns,
                distance=float(offset),
                speed=69.0 + offset,
            )
        )

    player_result = complete_lap(assembler, 0, 1, 20)
    rival_result = complete_lap(assembler, 1, 1, 20)
    player_batch = player_result.finalized_batches[0]
    rival_batch = rival_result.finalized_batches[0]
    player_group = player_batch.groups[0]
    rival_group = rival_batch.groups[0]

    assert player_batch.identity.is_player is True
    assert player_group.sample_count == 10
    assert rival_group.sample_count == 2
    assert rival_group.coalesced_samples == 8
    assert [row["lap_distance_m"] for row in rival_group.samples] == [4.0, 9.0]
    assert rival_group.field_metadata["speed_mps"]["availability"] == "observed"
    assert rival_group.field_metadata["brake"]["availability"] == "unavailable"
    assert rival_group.field_metadata["speed_mps"]["freshness_ms"] == 250
    assert player_batch.context["track_id"] == 7
    assert player_batch.context["compound"] == "soft"
    assert len(emitted) == 2

    store = TraceStore(tmp_path / "traces")
    manifest = player_batch.write_to_trace_store(store)
    report = store.verify_manifest(manifest.id)
    assert report.valid is True
    assert manifest.sample_count == 10

    live = assembler.live_snapshot(now_monotonic_ns=base_ns + 100_000_000)
    assert [car.identity.car_index for car in live] == [0, 1, 2]
    assert live[1].groups["telemetry"].fields["speed_mps"].value == 78.0
    assert live[2].groups == {}
    counters = assembler.counters
    assert counters.samples_received == 20
    assert counters.samples_coalesced == 8
    assert counters.samples_dropped == 0


def test_flashback_invalidates_open_and_persisted_branch_without_mixing_samples() -> (
    None
):
    invalidations = []
    assembler = SessionAssembler(invalidation_sink=invalidations.append)
    begin(assembler)
    participant(assembler, 0, "Player", driver_id=7)

    assembler.consume(sample_event("session-a", 0, 1, 10, distance=10.0))
    assembler.consume(sample_event("session-a", 0, 1, 20, distance=20.0))
    completed = complete_lap(assembler, 0, 1, 21).finalized_batches[0]
    assembler.consume(sample_event("session-a", 0, 2, 30, distance=30.0))

    rewind = assembler.consume(
        FlashbackEvent(
            stamp("session-a", 31, 3.1),
            target_overall_frame_identifier=15,
            target_session_time_s=1.5,
            target_lap_number=1,
        )
    )
    assert assembler.timeline_epoch == 1
    assert len(rewind.finalized_batches) == 1
    assert rewind.finalized_batches[0].invalidated is True
    assert rewind.finalized_batches[0].lap_number == 2
    invalidation = rewind.invalidations[0]
    assert invalidation.raw_archive_preserved is True
    assert completed.batch_id in invalidation.affected_batch_ids
    assert rewind.finalized_batches[0].batch_id in invalidation.affected_batch_ids
    remembered = {batch.batch_id: batch for batch in assembler.finalized_batches}
    assert remembered[completed.batch_id].invalidated is True
    assert len(invalidations) == 1

    assembler.consume(sample_event("session-a", 0, 1, 16, distance=16.0, speed=80.0))
    post = complete_lap(assembler, 0, 1, 25).finalized_batches[0]
    assert post.timeline_epoch == 1
    assert post.invalidated is False
    assert post.groups[0].sample_count == 1
    assert post.groups[0].samples[0]["speed_mps"] == 80.0


def test_identity_revision_closes_old_lap_and_session_restart_epochs_are_distinct() -> (
    None
):
    assembler = SessionAssembler()
    begin(assembler)
    participant(assembler, 2, "Alex", driver_id=9)
    assembler.consume(sample_event("session-a", 2, 4, 2))

    changed = assembler.consume(
        ParticipantEvent(
            stamp("session-a", 3, 0.03),
            car_index=2,
            values={"name": "Beth", "driver_id": 58, "race_number": 12},
        )
    )
    assert changed.finalized_batches[0].finalization_reason == "identity_changed"
    assert changed.finalized_batches[0].complete is False
    assert changed.identities[0].identity_revision == 1

    assembler.consume(sample_event("session-a", 2, 4, 4))
    new_identity_batch = complete_lap(assembler, 2, 4, 5).finalized_batches[0]
    assert new_identity_batch.identity.identity_revision == 1
    assert new_identity_batch.identity.id != changed.finalized_batches[0].identity.id

    assembler.consume(sample_event("session-a", 2, 5, 6))
    restarted = assembler.consume(
        SessionEvent(
            stamp("session-a", 0, 0.0),
            player_car_index=0,
            restart_evidence=True,
        )
    )
    assert restarted.finalized_batches[0].finalization_reason == "session_restart"
    assert assembler.session is not None
    assert assembler.session.restart_epoch == 1

    assembler.consume(SessionEvent(stamp("session-b", 0, 0.0)))
    assembler.consume(SessionEvent(stamp("session-a", 0, 0.0)))
    assert assembler.session is not None
    assert assembler.session.game_session_uid == "session-a"
    assert assembler.session.restart_epoch == 2
    assert assembler.counters.restarts == 1
    assert assembler.counters.session_uid_reuses >= 1

    assembler.consume(sample_event("session-a", 0, 1, 50))
    automatic_restart = assembler.consume(SessionEvent(stamp("session-a", 0, 0.0)))
    assert (
        automatic_restart.finalized_batches[0].finalization_reason == "session_restart"
    )
    assert assembler.session.restart_epoch == 3
    assert assembler.counters.restarts == 2


def test_buffers_are_bounded_and_shutdown_finalizes_exactly_once() -> None:
    emitted = []
    assembler = SessionAssembler(
        batch_sink=emitted.append,
        player_max_samples_per_group=3,
        max_samples_per_group=3,
        max_open_laps=2,
        max_groups_per_lap=1,
        max_event_history=4,
    )
    begin(assembler)
    participant(assembler, 0, "Player")
    for frame in range(2, 7):
        assembler.consume(sample_event("session-a", 0, 1, frame))
    assembler.consume(
        sample_event("session-a", 0, 1, 7, group="motion", retain_all=True)
    )
    assembler.consume(sample_event("session-a", 1, 1, 8))
    eviction = assembler.consume(sample_event("session-a", 2, 1, 9))

    assert len(eviction.finalized_batches) == 1
    evicted = eviction.finalized_batches[0]
    assert evicted.finalization_reason == "open_lap_buffer_eviction"
    assert evicted.groups[0].sample_count == 3
    assert [row["overall_frame_identifier"] for row in evicted.groups[0].samples] == [
        4,
        5,
        6,
    ]
    assert assembler.counters.samples_dropped == 3  # Two sample drops plus one group.
    assert len(assembler.event_history) == 4
    assert assembler.counters.event_history_drops > 0

    shutdown_batches = assembler.shutdown()
    emitted_count = len(emitted)
    assert len(shutdown_batches) == 2
    assert all(batch.finalization_reason == "shutdown" for batch in shutdown_batches)
    assert assembler.shutdown() == shutdown_batches
    assert len(emitted) == emitted_count
    assert assembler.quality_report().closed is True
    with pytest.raises(RuntimeError, match="closed"):
        assembler.consume(sample_event("session-a", 0, 2, 10))


def test_availability_provenance_coverage_and_freshness_remain_explicit() -> None:
    assembler = SessionAssembler()
    begin(assembler)
    participant(assembler, 0, "Player")
    event_ns = 5_000_000_000
    assembler.consume(
        SampleEvent(
            stamp("session-a", 2, 0.2, monotonic_ns=event_ns),
            car_index=0,
            lap_number=1,
            sample_group="telemetry",
            values={
                "lap_distance_m": 12.5,
                "speed_mps": 65.0,
                "predicted_grip": 0.91,
                "brake": None,
            },
            availability={
                "speed_mps": "observed",
                "predicted_grip": "estimated",
                "brake": "unavailable",
            },
            provenance={
                "speed_mps": "observed",
                "predicted_grip": "estimated",
                "brake": "unavailable",
            },
            units={
                "lap_distance_m": "m",
                "speed_mps": "m/s",
                "predicted_grip": "ratio",
                "brake": "ratio",
            },
            freshness_ms={
                "lap_distance_m": 500,
                "speed_mps": 100,
                "predicted_grip": 200,
                "brake": 100,
            },
        )
    )

    report = assembler.quality_report(now_monotonic_ns=event_ns + 250_000_000)
    fields = report.groups[0].fields
    assert fields["speed_mps"].availability == "stale"
    assert fields["speed_mps"].coverage_ratio == 1.0
    assert fields["predicted_grip"].provenance == "estimated"
    assert fields["brake"].availability == "unavailable"
    assert fields["brake"].coverage_ratio == 0.0

    live = assembler.live_snapshot(now_monotonic_ns=event_ns + 250_000_000)[0]
    assert live.groups["telemetry"].fields["speed_mps"].availability == "stale"
    assert live.groups["telemetry"].fields["brake"].value is None
    batch = complete_lap(assembler, 0, 1, 3).finalized_batches[0]
    metadata = batch.groups[0].field_metadata
    assert metadata["predicted_grip"]["availability"] == "estimated"
    assert metadata["predicted_grip"]["provenance"] == "estimated"
    assert metadata["brake"]["coverage"] == 0.0


def test_implicit_large_rewind_branches_but_small_reorder_does_not() -> None:
    assembler = SessionAssembler(rewind_tolerance_frames=5)
    begin(assembler)
    participant(assembler, 0, "Player")
    assembler.consume(sample_event("session-a", 0, 1, 20, distance=20.0))

    small_reorder = assembler.consume(
        SampleEvent(
            stamp("session-a", 18, 1.9),
            car_index=0,
            lap_number=1,
            sample_group="status",
            values={"ers_percent": 50.0},
        )
    )
    assert small_reorder.invalidations == ()
    assert assembler.timeline_epoch == 0

    rewind = assembler.consume(sample_event("session-a", 0, 1, 10, distance=10.0))
    assert len(rewind.invalidations) == 1
    assert rewind.invalidations[0].reason == "implicit_frame_rewind"
    assert assembler.timeline_epoch == 1
    assert assembler.counters.implicit_flashbacks == 1


def test_event_validation_rejects_ambiguous_or_unbounded_samples() -> None:
    with pytest.raises(ValueError, match="reserved"):
        SampleEvent(
            stamp("session-a", 1, 0.1),
            car_index=0,
            lap_number=1,
            sample_group="telemetry",
            values={"session_time_s": 2.0},
        )
    with pytest.raises(TypeError, match="numeric"):
        SampleEvent(
            stamp("session-a", 1, 0.1),
            car_index=0,
            lap_number=1,
            sample_group="telemetry",
            values={"speed": "fast"},  # type: ignore[dict-item]
        )

    assembler = SessionAssembler(max_fields_per_sample=1)
    begin(assembler)
    with pytest.raises(ValueError, match="maximum is 1"):
        assembler.consume(
            SampleEvent(
                stamp("session-a", 2, 0.2),
                car_index=0,
                lap_number=1,
                sample_group="telemetry",
                values={"speed": 1.0, "brake": 0.0},
            )
        )
