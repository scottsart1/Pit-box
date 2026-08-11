"""Send one real race-control event into a running demo broadcast.

For the marketing cut that shows the red-flag banner, the flag has to arrive
the way a real one does: as an event packet through the UDP socket, parsed by
the same code that parses the game. This sends exactly that — nothing is
poked into the UI.

    python -m tools.race_control_inject --port 20777 --code RDFL
    python -m tools.race_control_inject --port 20777 --code SCAR   # safety car

Uses the demo broadcaster's session identity so the running session accepts
the event as its own.
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import f1.packets as P  # noqa: E402

from tools.demo_broadcast import _header  # noqa: E402

CODES = ("RDFL", "SCAR", "VSCD", "CHQF", "GREN")


def build_event(code: str, frame: int = 900_000) -> bytes:
    packet = P.PacketEventData()
    packet.header = _header(3, frame, 1_000.0)
    packet.event_string_code = tuple(code.encode("ascii"))
    return bytes(packet.pack())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=20777)
    parser.add_argument("--code", choices=CODES, default="RDFL")
    parser.add_argument("--repeat", type=int, default=3,
                        help="UDP is lossy; send the event a few times")
    args = parser.parse_args(argv)

    payload = build_event(args.code)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for _ in range(max(1, args.repeat)):
            sock.sendto(payload, (args.host, args.port))
            time.sleep(0.15)
    finally:
        sock.close()
    print(f"sent {args.code} x{args.repeat} to {args.host}:{args.port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
