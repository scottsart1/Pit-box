from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from pitwall.capture import CaptureReader, CaptureScanReport
from pitwall.capture_lifecycle import SessionCaptureCoordinator
from pitwall.capture_service import CaptureService


@dataclass
class CatalogStub:
    captures: list[tuple[str | None, str, CaptureScanReport]] = field(
        default_factory=list
    )

    async def register_raw_capture(
        self,
        session_key: str | None,
        relative_path: str,
        report: CaptureScanReport,
        *,
        privacy_mode: str = "private",
    ) -> str:
        assert privacy_mode == "private"
        self.captures.append((session_key, relative_path, report))
        return f"capture-{len(self.captures)}"


@pytest.mark.asyncio
async def test_capture_rotates_and_catalogues_against_each_session(tmp_path) -> None:
    service = CaptureService(tmp_path, queue_size=32)
    catalog = CatalogStub()
    coordinator = SessionCaptureCoordinator(service, catalog, tmp_path)
    await coordinator.start(metadata={"capture_mode": "balanced"})

    service.submit(b"startup", ("192.168.1.61", 50_000))
    coordinator.observe_session("session-a")
    await coordinator.wait_idle()
    service.submit(b"a", ("192.168.1.61", 50_000))
    coordinator.observe_session("session-b")
    await coordinator.wait_idle()
    service.submit(b"b", ("192.168.1.61", 50_000))
    await coordinator.stop()

    assert [item[0] for item in catalog.captures] == [None, "session-a", "session-b"]
    payloads = [
        [frame.data for frame in CaptureReader(tmp_path / item[1])]
        for item in catalog.captures
    ]
    assert payloads == [[b"startup"], [b"a"], [b"b"]]
    assert coordinator.snapshot().rotations_completed == 2
