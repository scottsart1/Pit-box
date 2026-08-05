from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pitwall.capture import (
    CapturedDatagram,
    CaptureReader,
    CaptureWriter,
    anonymize_capture,
    recover_capture,
    scan_capture,
)
from pitwall.replay import (
    ReplayController,
    ReplayFaultConfig,
    ReplayFaultHooks,
    build_replay_plan,
)


def _frames(count: int = 6) -> list[CapturedDatagram]:
    return [
        CapturedDatagram(
            monotonic_ns=1_000_000_000 + index * 10_000_000,
            wall_ns=2_000_000_000 + index * 10_000_000,
            source_host="192.168.1.61",
            source_port=50_000 + index,
            data=f"packet-{index}".encode(),
        )
        for index in range(count)
    ]


def test_pwcap_round_trip_is_indexed_blocked_and_atomic(tmp_path: Path) -> None:
    path = tmp_path / "session.pwcap.zst"
    frames = _frames(5)
    writer = CaptureWriter(
        path,
        metadata={"app_version": "test", "receive_bind": "0.0.0.0:20777"},
        block_target_frames=2,
    )
    for frame in frames:
        writer.write(
            frame.data,
            frame.source,
            monotonic_ns=frame.monotonic_ns,
            wall_ns=frame.wall_ns,
        )
    assert not path.exists()
    assert Path(f"{path}.tmp").exists()
    writer.close()

    report = scan_capture(path)
    assert report.valid
    assert report.clean_close
    assert report.packet_count == 5
    assert len(report.blocks) == 3
    assert report.footer is not None
    assert report.footer["content_sha256"]
    assert list(CaptureReader(path)) == frames
    assert path.exists() and not Path(f"{path}.tmp").exists()


def test_pwcap_scan_and_recovery_keep_every_complete_block(tmp_path: Path) -> None:
    clean = tmp_path / "clean.pwcap"
    with CaptureWriter(clean, block_target_frames=2) as writer:
        for frame in _frames(5):
            writer.write(
                frame.data,
                frame.source,
                monotonic_ns=frame.monotonic_ns,
                wall_ns=frame.wall_ns,
            )
    clean_report = scan_capture(clean)
    second_block = clean_report.blocks[1]
    # End inside block two: block one remains independently verifiable.
    damaged = tmp_path / "damaged.pwcap.tmp"
    damaged.write_bytes(clean.read_bytes()[: second_block.offset + 17])

    damaged_report = scan_capture(damaged)
    assert damaged_report.errors
    assert damaged_report.packet_count == 2
    recovered = recover_capture(damaged)

    assert recovered.valid
    assert recovered.recovered
    assert not recovered.clean_close
    assert recovered.packet_count == 2
    assert [frame.data for frame in CaptureReader(tmp_path / "damaged.pwcap")] == [
        b"packet-0",
        b"packet-1",
    ]


def test_pwcap_anonymize_redacts_transport_but_preserves_payload(tmp_path: Path) -> None:
    source = tmp_path / "source.pwcap"
    destination = tmp_path / "anonymous.pwcap"
    with CaptureWriter(
        source,
        metadata={"adapter": "Private Wi-Fi", "track": "Spa"},
    ) as writer:
        frame = _frames(1)[0]
        writer.write(
            frame.data,
            frame.source,
            monotonic_ns=frame.monotonic_ns,
            wall_ns=frame.wall_ns,
        )

    report = anonymize_capture(source, destination)
    copied = list(CaptureReader(destination))
    assert report.valid
    assert report.metadata["adapter"] == "<redacted>"
    assert report.metadata["track"] == "Spa"
    assert report.metadata["privacy_mode"] == "transport-redacted-only"
    assert copied[0].source == ("0.0.0.0", 0)
    assert copied[0].data == b"packet-0"


def test_invalid_capture_is_diagnosable_without_throwing_from_scan(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.pwcap"
    invalid.write_bytes(b"not a capture")
    report = scan_capture(invalid)
    assert not report.valid
    assert report.errors


def test_capture_writer_never_overwrites_an_existing_archive(tmp_path: Path) -> None:
    path = tmp_path / "existing.pwcap"
    path.write_bytes(b"keep me")
    with pytest.raises(FileExistsError):
        CaptureWriter(path)
    assert path.read_bytes() == b"keep me"


def test_seeded_replay_faults_and_hooks_are_reproducible() -> None:
    frames = _frames()
    faults = ReplayFaultConfig(
        seed=77,
        loss_rate=0.2,
        duplicate_rate=0.4,
        reorder_rate=1.0,
        reorder_window=3,
        jitter_ns=500,
    )
    first = build_replay_plan(frames, faults=faults)
    second = build_replay_plan(frames, faults=faults)
    signature = lambda plan: [
        (packet.capture_index, packet.duplicate_ordinal, packet.scheduled_offset_ns)
        for packet in plan.packets
    ]
    assert signature(first) == signature(second)
    assert first.dropped_packet_count == second.dropped_packet_count

    hooks = ReplayFaultHooks(
        drop=lambda _frame, index, _rng: index == 2,
        duplicate_count=lambda _frame, index, _rng: 2 if index == 1 else 0,
        jitter=lambda _frame, index, _rng: index * 10,
        reorder_key=lambda _frame, index, _rng: -index,
    )
    hooked = build_replay_plan(
        frames,
        faults=ReplayFaultConfig(seed=5, reorder_window=3),
        hooks=hooks,
    )
    assert hooked.dropped_packet_count == 1
    assert hooked.duplicate_packet_count == 2
    assert hooked.packets[0].capture_index == 1
    assert all(packet.source == "replay" for packet in hooked.packets)


@pytest.mark.asyncio
async def test_replay_can_pause_step_then_resume_at_accelerated_speed() -> None:
    plan = build_replay_plan(_frames(4))
    emitted = []
    first_emission = asyncio.Event()

    async def sink(packet):  # type: ignore[no-untyped-def]
        emitted.append(packet)
        first_emission.set()

    controller = ReplayController(plan, sink, speed=10_000, initially_paused=True)
    task = asyncio.create_task(controller.run())
    await asyncio.sleep(0)
    assert emitted == []

    controller.step()
    await asyncio.wait_for(first_emission.wait(), timeout=0.5)
    assert len(emitted) == 1
    assert controller.paused

    controller.resume()
    stats = await asyncio.wait_for(task, timeout=1.0)
    assert stats.emitted == 4
    assert [packet.capture_index for packet in emitted] == [0, 1, 2, 3]
