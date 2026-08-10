from __future__ import annotations

import pytest

from pitwall.capture import CaptureReader, CaptureWriter
from pitwall.capture_service import CaptureService


@pytest.mark.asyncio
async def test_capture_service_preserves_original_datagrams_off_the_hot_path(
    tmp_path,
) -> None:
    service = CaptureService(tmp_path, queue_size=64)
    relative = await service.start(
        relative_path="2026/session-one.pwcap",
        metadata={"receive_bind": {"host": "0.0.0.0", "port": 20777}},
    )
    payloads = [bytes([index]) * (29 + index) for index in range(1, 21)]
    for index, payload in enumerate(payloads):
        assert service.submit(
            payload,
            ("192.168.1.61", 50_000),
            monotonic_ns=1_000_000 + index,
            wall_ns=2_000_000 + index,
        )
    path = await service.stop()
    assert path == tmp_path / relative
    frames = list(CaptureReader(path))
    assert [frame.data for frame in frames] == payloads
    assert all(frame.source == ("192.168.1.61", 50_000) for frame in frames)
    snapshot = service.snapshot()
    assert snapshot.state == "off"
    assert snapshot.packets_written == len(payloads)
    assert snapshot.bytes_written == sum(map(len, payloads))
    assert snapshot.queue_drops == 0


@pytest.mark.asyncio
async def test_capture_service_rejects_paths_outside_capture_root(tmp_path) -> None:
    service = CaptureService(tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        await service.start(relative_path="../outside.pwcap")


@pytest.mark.asyncio
async def test_capture_service_start_and_stop_are_idempotent(tmp_path) -> None:
    service = CaptureService(tmp_path)
    first = await service.start(relative_path="one.pwcap")
    second = await service.start(relative_path="ignored.pwcap")
    assert second == first
    assert await service.stop() == tmp_path / first
    assert await service.stop() is None


@pytest.mark.asyncio
async def test_capture_service_recovers_complete_blocks_after_unclean_stop(
    tmp_path,
) -> None:
    unfinished = tmp_path / "2026" / "interrupted.pwcap"
    writer = CaptureWriter(unfinished, block_target_frames=1)
    writer.write(
        b"packet",
        ("192.168.1.61", 50_000),
        monotonic_ns=1,
        wall_ns=2,
    )
    temporary = writer.abort()
    assert temporary.exists()

    service = CaptureService(tmp_path)
    recovery = await service.recover_pending()

    assert recovery.unresolved == ()
    assert len(recovery.recovered) == 1
    report = recovery.recovered[0]
    assert report.recovered is True
    assert report.packet_count == 1
    assert [frame.data for frame in CaptureReader(report.path)] == [b"packet"]
    assert not temporary.exists()


@pytest.mark.asyncio
async def test_capture_service_stops_admission_at_configured_size_limit(tmp_path) -> None:
    service = CaptureService(tmp_path, max_file_bytes=10, queue_size=8)
    await service.start(relative_path="limited.pwcap")
    assert service.submit(b"12345678", ("127.0.0.1", 20_777))
    assert service.submit(b"abcdefgh", ("127.0.0.1", 20_777))
    await service.queue.join()

    snapshot = service.snapshot()
    assert snapshot.state == "limit_reached"
    assert snapshot.last_error == "capture_file_size_limit_reached"
    assert snapshot.queue_drops == 2
    assert service.submit(b"later", ("127.0.0.1", 20_777)) is False
    assert await service.stop() is not None


@pytest.mark.asyncio
async def test_unrecoverable_temp_files_are_quarantined_once_not_rewarned_forever(
    tmp_path,
) -> None:
    """A dead capture stub must be parked, not re-reported at every startup.

    Real installs accumulated eight 0-byte .pwcap.tmp stubs (launches that
    lost the port race and died before writing a header) and warned about all
    of them at every launch. The content can never become recoverable, so the
    file is moved — never deleted — under unrecoverable/ and stops being
    scanned.
    """
    stub = tmp_path / "2026" / "capture-dead.pwcap.tmp"
    stub.parent.mkdir(parents=True)
    stub.touch()

    service = CaptureService(tmp_path)
    first = await service.recover_pending()

    assert len(first.unresolved) == 1
    path, reason = first.unresolved[0]
    assert path == "2026/capture-dead.pwcap.tmp"
    assert "moved to" in reason
    assert not stub.exists()
    quarantined = tmp_path / "unrecoverable" / "2026" / "capture-dead.pwcap.tmp"
    assert quarantined.exists()

    # The whole point: the next launch is clean.
    second = await service.recover_pending()
    assert second.unresolved == ()
    assert second.recovered == ()
