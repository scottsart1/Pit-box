"""The dashboard socket must never queue frames faster than a client takes them.

The previous loop sent a full snapshot every 250 ms unconditionally. send_json
returns once the frame reaches the transport, not once the browser has it, so a
slow dashboard applied no backpressure at all and frames accumulated for the
length of the session. On an 8 GB machine an hour into a real race that ended
in ten MemoryErrors in 83 seconds, each killing the dashboard.
"""

from __future__ import annotations

import sys
from pathlib import Path

from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pitwall import app as app_module  # noqa: E402
from pitwall.app import app  # noqa: E402


def test_a_second_frame_only_arrives_after_the_first_is_acknowledged() -> None:
    with TestClient(app).websocket_connect("/ws") as ws:
        first = ws.receive_json()
        assert isinstance(first, dict)

        # No ack yet. The server must be waiting on us, not queueing: nothing
        # further may be produced until we say we have rendered.
        ws.send_text(".")
        second = ws.receive_json()
        assert isinstance(second, dict)


def test_a_dashboard_that_stops_acknowledging_is_dropped_not_buffered_for(monkeypatch) -> None:
    # The whole point: an unresponsive client must cost one closed socket, not
    # unbounded memory. Shortened so the test does not wait the real timeout.
    monkeypatch.setattr(app_module, "WEBSOCKET_ACK_TIMEOUT_S", 0.4)
    monkeypatch.setattr(app_module, "WEBSOCKET_PUSH_INTERVAL_S", 0.01)

    import pytest

    with pytest.raises(Exception):
        with TestClient(app).websocket_connect("/ws") as ws:
            ws.receive_json()
            # Never acknowledge. The server should close rather than keep
            # producing; receiving again must therefore fail rather than
            # hand back an endless backlog.
            for _ in range(50):
                ws.receive_json()


def test_memory_exhaustion_drops_the_connection_instead_of_crashing(monkeypatch) -> None:
    """A MemoryError while serializing a frame must cost one closed socket.

    Seen in a real race: two MemoryErrors in json.dumps of the live snapshot,
    each surfacing as an unhandled ASGI exception. The engine survived, but the
    handler must treat it as a droppable frame, not a crash.
    """

    async def exhausted() -> dict:
        raise MemoryError

    monkeypatch.setattr(app_module.store, "snapshot_live", exhausted)

    import pytest

    with pytest.raises(Exception) as excinfo, TestClient(app).websocket_connect("/ws") as ws:
        ws.receive_json()
    # The socket closing is expected; the MemoryError leaking out is not.
    assert not isinstance(excinfo.value, MemoryError)


def test_send_racing_a_client_close_ends_quietly(monkeypatch) -> None:
    """uvicorn raises RuntimeError when a send races the client's close.

    That is a disconnect, not a server fault: it must not surface as an
    ASGI exception.
    """
    from starlette.websockets import WebSocket

    async def send_after_close(self, data) -> None:
        raise RuntimeError(
            "Unexpected ASGI message 'websocket.send', after sending 'websocket.close'."
        )

    monkeypatch.setattr(WebSocket, "send_json", send_after_close)

    import pytest

    with pytest.raises(Exception) as excinfo, TestClient(app).websocket_connect("/ws") as ws:
        ws.receive_json()
    assert not isinstance(excinfo.value, RuntimeError)
