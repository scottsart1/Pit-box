"""Deterministic PWCAP replay scheduling and developer CLI.

Replay emits immutable packets tagged with ``source='replay'``.  A caller can
inject them into the same post-receive path as live traffic, or the CLI can send
the recorded bytes to a local UDP listener for end-to-end testing.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import random
import socket
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .capture import (
    CapturedDatagram,
    CaptureReader,
    anonymize_capture,
    inspect_capture,
    validate_capture,
)

DropHook = Callable[[CapturedDatagram, int, random.Random], bool]
DuplicateHook = Callable[[CapturedDatagram, int, random.Random], int]
JitterHook = Callable[[CapturedDatagram, int, random.Random], int]
ReorderKeyHook = Callable[[CapturedDatagram, int, random.Random], float]


@dataclass(frozen=True, slots=True)
class ReplayFaultConfig:
    """Seeded, reproducible packet faults for resilience tests."""

    seed: int = 0
    loss_rate: float = 0.0
    duplicate_rate: float = 0.0
    reorder_rate: float = 0.0
    reorder_window: int = 1
    jitter_ns: int = 0

    def __post_init__(self) -> None:
        for name in ("loss_rate", "duplicate_rate", "reorder_rate"):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.reorder_window < 1:
            raise ValueError("reorder_window must be at least 1")
        if self.jitter_ns < 0:
            raise ValueError("jitter_ns must be non-negative")


@dataclass(frozen=True, slots=True)
class ReplayFaultHooks:
    """Optional fault callbacks; supplied callbacks override matching rates."""

    drop: DropHook | None = None
    duplicate_count: DuplicateHook | None = None
    jitter: JitterHook | None = None
    reorder_key: ReorderKeyHook | None = None


@dataclass(frozen=True, slots=True)
class ReplayPacket:
    datagram: CapturedDatagram
    capture_index: int
    scheduled_offset_ns: int
    duplicate_ordinal: int = 0
    source: str = "replay"

    @property
    def data(self) -> bytes:
        return self.datagram.data

    @property
    def source_endpoint(self) -> tuple[str, int]:
        return self.datagram.source


@dataclass(slots=True)
class ReplayPlan:
    packets: list[ReplayPacket]
    source_packet_count: int
    dropped_packet_count: int
    duplicate_packet_count: int
    reordered_packet_count: int
    seed: int


@dataclass(slots=True)
class ReplayStats:
    planned: int = 0
    emitted: int = 0
    source_packets: int = 0
    dropped_by_faults: int = 0
    duplicates_by_faults: int = 0
    reordered_by_faults: int = 0
    stopped: bool = False
    emitted_capture_indexes: list[int] = field(default_factory=list)


def _faulted_packets(
    frames: Sequence[CapturedDatagram],
    config: ReplayFaultConfig,
    hooks: ReplayFaultHooks,
) -> tuple[list[ReplayPacket], int, int]:
    if not frames:
        return [], 0, 0
    rng = random.Random(config.seed)
    base_ns = frames[0].monotonic_ns
    packets: list[ReplayPacket] = []
    dropped = 0
    duplicates = 0
    for index, frame in enumerate(frames):
        should_drop = (
            hooks.drop(frame, index, rng)
            if hooks.drop is not None
            else rng.random() < config.loss_rate
        )
        if should_drop:
            dropped += 1
            continue
        jitter = (
            int(hooks.jitter(frame, index, rng))
            if hooks.jitter is not None
            else rng.randint(-config.jitter_ns, config.jitter_ns)
            if config.jitter_ns
            else 0
        )
        offset = max(0, int(frame.monotonic_ns - base_ns) + jitter)
        extra = (
            max(0, int(hooks.duplicate_count(frame, index, rng)))
            if hooks.duplicate_count is not None
            else 1
            if rng.random() < config.duplicate_rate
            else 0
        )
        packets.append(
            ReplayPacket(
                datagram=frame,
                capture_index=index,
                scheduled_offset_ns=offset,
            )
        )
        for duplicate_ordinal in range(1, extra + 1):
            packets.append(
                ReplayPacket(
                    datagram=frame,
                    capture_index=index,
                    scheduled_offset_ns=offset,
                    duplicate_ordinal=duplicate_ordinal,
                )
            )
            duplicates += 1
    return packets, dropped, duplicates


def _reorder_packets(
    packets: list[ReplayPacket],
    config: ReplayFaultConfig,
    hooks: ReplayFaultHooks,
) -> tuple[list[ReplayPacket], int]:
    if len(packets) < 2 or config.reorder_window <= 1:
        return packets, 0
    rng = random.Random(config.seed ^ 0x50574341)
    reordered: list[ReplayPacket] = []
    window_size = config.reorder_window
    for start in range(0, len(packets), window_size):
        window = list(packets[start : start + window_size])
        original = list(window)
        if hooks.reorder_key is not None:
            window.sort(
                key=lambda packet: hooks.reorder_key(
                    packet.datagram,
                    packet.capture_index,
                    rng,
                )
            )
        elif len(window) > 1 and rng.random() < config.reorder_rate:
            rng.shuffle(window)
            if window == original:
                window[0], window[-1] = window[-1], window[0]
        reordered.extend(window)
    moved = sum(
        left.capture_index != right.capture_index
        or left.duplicate_ordinal != right.duplicate_ordinal
        for left, right in zip(packets, reordered)
    )
    return reordered, moved


def build_replay_plan(
    frames: Iterable[CapturedDatagram],
    *,
    faults: ReplayFaultConfig | None = None,
    hooks: ReplayFaultHooks | None = None,
) -> ReplayPlan:
    """Build a reproducible delivery plan without reading the wall clock."""

    source = list(frames)
    config = faults or ReplayFaultConfig()
    configured_hooks = hooks or ReplayFaultHooks()
    packets, dropped, duplicates = _faulted_packets(source, config, configured_hooks)
    packets, reordered = _reorder_packets(packets, config, configured_hooks)
    return ReplayPlan(
        packets=packets,
        source_packet_count=len(source),
        dropped_packet_count=dropped,
        duplicate_packet_count=duplicates,
        reordered_packet_count=reordered,
        seed=config.seed,
    )


class ReplayController:
    """Async real-time/accelerated replay with pause, resume, step and stop."""

    def __init__(
        self,
        plan: ReplayPlan,
        sink: Callable[[ReplayPacket], Any | Awaitable[Any]],
        *,
        speed: float = 1.0,
        initially_paused: bool = False,
    ) -> None:
        if speed <= 0:
            raise ValueError("speed must be greater than zero")
        self.plan = plan
        self.sink = sink
        self.speed = float(speed)
        self._paused = bool(initially_paused)
        self._step_budget = 0
        self._stop_requested = False
        self._state_changed = asyncio.Event()
        self._running = False
        self._origin: float | None = None
        self._pause_started: float | None = None

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        if self._paused:
            return
        self._paused = True
        if self._running:
            self._pause_started = asyncio.get_running_loop().time()
        self._state_changed.set()

    def resume(self) -> None:
        if not self._paused:
            return
        now = asyncio.get_running_loop().time()
        if self._origin is not None and self._pause_started is not None:
            self._origin += now - self._pause_started
        self._pause_started = None
        self._paused = False
        self._step_budget = 0
        self._state_changed.set()

    def step(self, count: int = 1) -> None:
        if count < 1:
            raise ValueError("step count must be positive")
        self._paused = True
        if self._running and self._pause_started is None:
            self._pause_started = asyncio.get_running_loop().time()
        self._step_budget += int(count)
        self._state_changed.set()

    def stop(self) -> None:
        self._stop_requested = True
        self._state_changed.set()

    async def _wait_for_packet(self, packet: ReplayPacket) -> bool:
        loop = asyncio.get_running_loop()
        while not self._stop_requested:
            if self._paused:
                if self._step_budget:
                    self._step_budget -= 1
                    return True
                self._state_changed.clear()
                await self._state_changed.wait()
                continue
            if self._origin is None:
                self._origin = loop.time()
            target = self._origin + packet.scheduled_offset_ns / 1_000_000_000 / self.speed
            delay = target - loop.time()
            if delay <= 0:
                return True
            self._state_changed.clear()
            try:
                await asyncio.wait_for(self._state_changed.wait(), timeout=delay)
            except TimeoutError:
                return True
        return False

    async def run(self) -> ReplayStats:
        if self._running:
            raise RuntimeError("replay controller is already running")
        self._running = True
        loop = asyncio.get_running_loop()
        self._origin = loop.time()
        if self._paused:
            self._pause_started = self._origin
        stats = ReplayStats(
            planned=len(self.plan.packets),
            source_packets=self.plan.source_packet_count,
            dropped_by_faults=self.plan.dropped_packet_count,
            duplicates_by_faults=self.plan.duplicate_packet_count,
            reordered_by_faults=self.plan.reordered_packet_count,
        )
        try:
            for packet in self.plan.packets:
                if not await self._wait_for_packet(packet):
                    stats.stopped = True
                    break
                result = self.sink(packet)
                if inspect.isawaitable(result):
                    await result
                stats.emitted += 1
                stats.emitted_capture_indexes.append(packet.capture_index)
        finally:
            self._running = False
        return stats


async def replay_capture(
    path: str | Path,
    sink: Callable[[ReplayPacket], Any | Awaitable[Any]],
    *,
    speed: float = 1.0,
    faults: ReplayFaultConfig | None = None,
    hooks: ReplayFaultHooks | None = None,
) -> ReplayStats:
    reader = CaptureReader(path)
    plan = build_replay_plan(reader, faults=faults, hooks=hooks)
    return await ReplayController(plan, sink, speed=speed).run()


async def _play_udp(path: Path, host: str, port: int, speed: float) -> ReplayStats:
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)

    async def send(packet: ReplayPacket) -> None:
        await loop.sock_sendto(sock, packet.data, (host, port))

    try:
        return await replay_capture(path, send, speed=speed)
    finally:
        sock.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect, validate and replay PWCAP files")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect_parser = commands.add_parser("inspect")
    inspect_parser.add_argument("path", type=Path)
    validate_parser = commands.add_parser("validate")
    validate_parser.add_argument("path", type=Path)
    anonymize_parser = commands.add_parser("anonymize")
    anonymize_parser.add_argument("source", type=Path)
    anonymize_parser.add_argument("destination", type=Path)
    play_parser = commands.add_parser("play")
    play_parser.add_argument("path", type=Path)
    play_parser.add_argument("--host", default="127.0.0.1")
    play_parser.add_argument("--port", type=int, default=20777)
    play_parser.add_argument("--speed", type=float, default=1.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _build_parser().parse_args(argv)
    if arguments.command == "inspect":
        print(json.dumps(inspect_capture(arguments.path), indent=2, sort_keys=True))
        return 0
    if arguments.command == "validate":
        report = validate_capture(arguments.path)
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0 if report.valid else 1
    if arguments.command == "anonymize":
        report = anonymize_capture(arguments.source, arguments.destination)
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0 if report.valid else 1
    if not 1 <= arguments.port <= 65_535:
        raise SystemExit("--port must be between 1 and 65535")
    stats = asyncio.run(
        _play_udp(arguments.path, arguments.host, arguments.port, arguments.speed)
    )
    print(json.dumps(asdict(stats), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
