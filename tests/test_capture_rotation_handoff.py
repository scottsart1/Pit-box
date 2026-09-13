"""Observed 202-packet rotation gap, exercised with deterministic barriers."""

import asyncio

import pytest

from pitwall.capture import CaptureReader, scan_capture
from pitwall.capture_lifecycle import SessionCaptureCoordinator
from pitwall.capture_service import CaptureService


class PausedCatalog:
    def __init__(self, *, pause=False):
        self.pause = pause
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.records = []

    async def register_raw_capture(self, session_id, path, report, **kwargs):
        assert report.valid and report.clean_close
        self.records.append((session_id, path))
        if self.pause and len(self.records) == 1:
            self.entered.set()
            await self.release.wait()
        return str(len(self.records))


def offer(service, label):
    return service.submit(label.encode(), ("127.0.0.1", 29999),
                          monotonic_ns=123456, wall_ns=234567)


def recorded(root, catalog):
    result = []
    for session, path in catalog.records:
        reader = CaptureReader(root / path)
        assert reader.metadata["session_id"] == session
        frames = list(reader)
        assert all(frame.monotonic_ns == 123456 and frame.wall_ns == 234567
                   and frame.source == ("127.0.0.1", 29999) for frame in frames)
        result.append((session, [frame.data.decode() for frame in frames]))
    return result


@pytest.mark.asyncio
async def test_catalog_pause_preserves_all_202_packets_with_correct_session(tmp_path):
    service = CaptureService(tmp_path, queue_size=512)
    catalog = PausedCatalog(pause=True)
    coordinator = SessionCaptureCoordinator(service, catalog, tmp_path)
    await coordinator.start()
    assert offer(service, "startup")
    coordinator.observe_session("uid-44-epoch-1")
    await asyncio.wait_for(catalog.entered.wait(), 5)
    accepted = [offer(service, f"during-{i}") for i in range(202)]
    catalog.release.set()
    await coordinator.wait_idle()
    assert offer(service, "after")
    await coordinator.stop()
    assert all(accepted)
    assert recorded(tmp_path, catalog) == [
        (None, ["startup"]),
        ("uid-44-epoch-1", [*[f"during-{i}" for i in range(202)], "after"]),
    ]
    snapshot = service.snapshot()
    assert snapshot.packets_queued == snapshot.packets_written == 204
    assert snapshot.queue_drops == snapshot.write_errors == 0


@pytest.mark.asyncio
async def test_writer_open_pause_buffers_in_order_and_shutdown_drains(tmp_path, monkeypatch):
    service = CaptureService(tmp_path, queue_size=32)
    catalog = PausedCatalog()
    coordinator = SessionCaptureCoordinator(service, catalog, tmp_path)
    await coordinator.start()
    entered, release = asyncio.Event(), asyncio.Event()
    original = service._open_writer
    async def blocked_open(**kwargs):
        entered.set()
        await release.wait()
        await original(**kwargs)
    monkeypatch.setattr(service, "_open_writer", blocked_open)
    assert offer(service, "old")
    coordinator.observe_session("new")
    await asyncio.wait_for(entered.wait(), 5)
    accepted = [offer(service, f"buffered-{i}") for i in range(20)]
    stopping = asyncio.create_task(coordinator.stop())
    release.set()
    await asyncio.wait_for(stopping, 5)
    assert all(accepted)
    assert recorded(tmp_path, catalog) == [(None, ["old"]),
        ("new", [f"buffered-{i}" for i in range(20)])]
    assert service.snapshot().queue_drops == 0
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.asyncio
async def test_rapid_uid_epoch_changes_preserve_boundaries_before_worker_runs(tmp_path):
    service = CaptureService(tmp_path, queue_size=32)
    catalog = PausedCatalog()
    coordinator = SessionCaptureCoordinator(service, catalog, tmp_path)
    await coordinator.start()
    assert offer(service, "startup")
    for session, payload in [("44:1", "a"), ("55:1", "b"), ("44:2", "c")]:
        coordinator.observe_session(session)
        assert offer(service, payload)
    await coordinator.stop()
    assert recorded(tmp_path, catalog) == [(None, ["startup"]),
        ("44:1", ["a"]), ("55:1", ["b"]), ("44:2", ["c"])]
    assert coordinator.snapshot().rotations_completed == 3


@pytest.mark.asyncio
async def test_full_packet_queue_counts_loss_without_dropping_session_boundary(tmp_path, monkeypatch):
    service = CaptureService(tmp_path, queue_size=2)
    catalog = PausedCatalog()
    coordinator = SessionCaptureCoordinator(service, catalog, tmp_path)
    await coordinator.start()
    entered, release = asyncio.Event(), asyncio.Event()
    original = service._open_writer
    async def blocked_open(**kwargs):
        entered.set()
        await release.wait()
        await original(**kwargs)
    monkeypatch.setattr(service, "_open_writer", blocked_open)
    coordinator.observe_session("a")
    await asyncio.wait_for(entered.wait(), 5)
    accepted = [offer(service, label) for label in ("a-1", "a-2", "a-overflow")]
    coordinator.observe_session("b")
    admitted_b_early = offer(service, "b-before-boundary-space")
    release.set()
    await asyncio.wait_for(coordinator.wait_idle(), 5)
    assert offer(service, "b-after")
    await coordinator.stop()
    assert accepted == [True, True, False]
    assert admitted_b_early is False
    assert recorded(tmp_path, catalog) == [(None, []), ("a", ["a-1", "a-2"]),
                                           ("b", ["b-after"])]
    snapshot = service.snapshot()
    assert snapshot.queue_drops == 2
    assert snapshot.packets_written == snapshot.packets_queued == 3
    assert snapshot.write_errors == 0
    assert snapshot.queue_high_water <= snapshot.queue_capacity


@pytest.mark.asyncio
async def test_rotation_capacity_overflow_rejects_until_latest_session_boundary(tmp_path):
    service = CaptureService(tmp_path, queue_size=32)
    catalog = PausedCatalog(pause=True)
    coordinator = SessionCaptureCoordinator(service, catalog, tmp_path, queue_size=1)
    await coordinator.start()
    coordinator.observe_session("a")
    await asyncio.wait_for(catalog.entered.wait(), 5)
    assert offer(service, "a")
    coordinator.observe_session("b")
    assert offer(service, "b")
    coordinator.observe_session("c")
    c_admitted = offer(service, "c-must-not-enter-b")
    coordinator.observe_session("d")
    d_admitted = offer(service, "d-before-boundary")
    catalog.release.set()
    await asyncio.wait_for(coordinator.wait_idle(), 5)
    assert offer(service, "d-after")
    await coordinator.stop()
    assert c_admitted is d_admitted is False
    assert recorded(tmp_path, catalog) == [(None, []), ("a", ["a"]),
                                           ("b", ["b"]), ("d", ["d-after"])]
    assert coordinator.snapshot().rotation_drops == 2
    assert service.snapshot().queue_drops == 2


@pytest.mark.asyncio
async def test_service_stop_waits_for_boundary_and_closes_final_writer(tmp_path):
    service = CaptureService(tmp_path, queue_size=32)
    await service.start(metadata={"session_id": "a"})
    assert offer(service, "a")
    previous = service.request_rotation(metadata={"session_id": "b"})
    assert offer(service, "b")
    final = await service.stop()
    paths = [await previous, final]
    assert [[frame.data for frame in CaptureReader(path)] for path in paths] == [[b"a"], [b"b"]]
    assert all(scan_capture(path).valid and scan_capture(path).clean_close for path in paths)
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.asyncio
async def test_timed_out_stop_settles_pending_boundaries_and_queue(tmp_path, monkeypatch):
    service = CaptureService(tmp_path, queue_size=1)
    await service.start(metadata={"session_id": "startup"})
    entered, release = asyncio.Event(), asyncio.Event()
    original = service._open_writer
    async def blocked_open(**kwargs):
        entered.set()
        await release.wait()
        await original(**kwargs)
    monkeypatch.setattr(service, "_open_writer", blocked_open)
    opening = service.request_rotation(metadata={"session_id": "a"})
    await asyncio.wait_for(entered.wait(), 5)
    assert offer(service, "a-buffered")
    pending = service.request_rotation(metadata={"session_id": "b"})
    await asyncio.wait_for(service.stop(drain_timeout_s=0), 5)
    release.set()
    assert opening.done() and pending.done()
    assert all(isinstance(value, Exception) for value in await asyncio.gather(
        opening, pending, return_exceptions=True))
    assert service.queue.empty()
    await asyncio.wait_for(service.queue.join(), 1)
    assert service.snapshot().queue_drops == 1
    assert not service.running
    assert service._pending_boundaries == 0
    assert not service._boundary_tasks


@pytest.mark.asyncio
async def test_rotation_open_failure_is_visible_in_write_errors(tmp_path, monkeypatch):
    service = CaptureService(tmp_path, queue_size=4)
    await service.start(metadata={"session_id": "startup"})
    async def failed_open(**kwargs):
        raise OSError("synthetic disk open failure")
    monkeypatch.setattr(service, "_open_writer", failed_open)
    completed = service.request_rotation(metadata={"session_id": "a"})
    with pytest.raises(OSError, match="synthetic disk"):
        await completed
    snapshot = service.snapshot()
    await service.stop()
    assert snapshot.state == "error"
    assert snapshot.write_errors == 1
    assert "synthetic disk" in snapshot.last_error


@pytest.mark.asyncio
async def test_normal_stop_drains_pending_boundary_puts_in_order(tmp_path):
    service = CaptureService(tmp_path, queue_size=1)
    await service.start(metadata={"session_id": "startup"})
    completions = [service.request_rotation(metadata={"session_id": name})
                   for name in ("a", "b", "c")]
    final = await service.stop()
    paths = [*(await asyncio.gather(*completions)), final]
    assert [CaptureReader(path).metadata["session_id"] for path in paths] == [
        "startup", "a", "b", "c"]
    assert all(scan_capture(path).valid and scan_capture(path).clean_close for path in paths)
    await asyncio.wait_for(service.queue.join(), 1)
    assert not service._boundary_tasks
    assert service._pending_boundaries == 0
    assert service.snapshot().queue_drops == service.snapshot().write_errors == 0
