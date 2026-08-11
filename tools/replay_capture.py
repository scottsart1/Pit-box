"""Replay a recorded PWCAP capture into a running Your Pit Box over UDP.

The synthetic generators produce plausible races; this replays a real one,
byte for byte, from the driver's own session. It is the only way to ask "would
the change have behaved differently in the race that actually happened", which
is the question that matters after a session goes wrong.

    python -m tools.replay_capture ~/PitWallData/captures/2026/capture-*.pwcap \
        --host 127.0.0.1 --port 20777 --speed 20

``--speed`` is time compression: 20 replays an hour of racing in three minutes.
Datagrams are sent in their recorded order with their recorded spacing, so
packet interleaving, session transitions and flashbacks all reproduce exactly.

Nothing here is imported by the application.
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pitwall.capture import CaptureReader  # noqa: E402


def replay(
    path: Path,
    host: str,
    port: int,
    speed: float,
    *,
    limit: int | None = None,
    start_offset_s: float = 0.0,
    progress_every: int = 5000,
) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sent = 0
    first_ns: int | None = None
    started = time.monotonic()
    try:
        for frame in CaptureReader(path):
            if first_ns is None:
                first_ns = frame.monotonic_ns
            offset_s = (frame.monotonic_ns - first_ns) / 1e9
            if offset_s < start_offset_s:
                continue
            target = started + (offset_s - start_offset_s) / max(0.01, speed)
            delay = target - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            sock.sendto(frame.data, (host, port))
            sent += 1
            if progress_every and sent % progress_every == 0:
                print(
                    f"  {sent} datagrams · {offset_s:7.1f}s of race time",
                    flush=True,
                )
            if limit and sent >= limit:
                break
    finally:
        sock.close()
    return sent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=20777)
    parser.add_argument("--speed", type=float, default=20.0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--start-offset", type=float, default=0.0,
        help="skip this many seconds of recorded race time before sending",
    )
    args = parser.parse_args(argv)

    print(f"Replaying {args.capture.name} to {args.host}:{args.port} at {args.speed}x")
    sent = replay(
        args.capture,
        args.host,
        args.port,
        args.speed,
        limit=args.limit,
        start_offset_s=args.start_offset,
    )
    print(f"Done: {sent} datagrams sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
