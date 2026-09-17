import asyncio
import socket
from types import SimpleNamespace

import pytest

from pitwall.network_service import _create_endpoint


@pytest.mark.asyncio
async def test_real_listener_requests_burst_headroom():
    transport, _ = await _create_endpoint(asyncio.DatagramProtocol, "127.0.0.1", 0)
    try:
        receiver = transport.get_extra_info("socket")
        # Linux/Android may clamp to rmem_max. Do not require the exact request.
        assert receiver.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF) >= 128 * 1024
    finally:
        transport.close()
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("endpoint failed"), asyncio.CancelledError()])
async def test_socket_is_closed_when_endpoint_setup_fails(monkeypatch, error):
    seen = []

    async def fail(factory, *, sock):
        seen.append(sock)
        raise error

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: SimpleNamespace(create_datagram_endpoint=fail))
    with pytest.raises(type(error)):
        await _create_endpoint(asyncio.DatagramProtocol, "127.0.0.1", 0)
    assert seen[0].fileno() == -1
