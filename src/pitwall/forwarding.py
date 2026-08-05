"""Best-effort, byte-identical UDP fan-out for Pit Wall.

Configuration and DNS resolution happen asynchronously outside the receiver
path.  ``submit`` performs only header inspection, immutable tuple reads, and a
bounded ``put_nowait``; a slow or broken destination can never delay local UDP
ingestion.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import re
import socket
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Self

from .networking import (
    AddressScope,
    HeaderInspection,
    IPv4Interface,
    classify_ipv4,
    inspect_2026_header,
)

_TARGET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class ForwardValidationError(ValueError):
    def __init__(self, code: str, message: str, target_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.target_id = target_id


@dataclass(frozen=True, slots=True)
class ForwardTarget:
    id: str
    label: str
    host: str
    port: int
    enabled: bool = True
    packet_ids: frozenset[int] | None = None
    forward_unknown_packets: bool = False
    allow_public: bool = False
    allow_broadcast_multicast: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", str(self.id).strip())
        object.__setattr__(self, "label", str(self.label).strip())
        object.__setattr__(self, "host", str(self.host).strip())
        object.__setattr__(self, "port", int(self.port))
        if self.packet_ids is not None:
            object.__setattr__(
                self,
                "packet_ids",
                frozenset(int(value) for value in self.packet_ids),
            )


@dataclass(frozen=True, slots=True)
class ResolvedForwardTarget:
    target: ForwardTarget
    resolved_addresses: tuple[str, ...]
    resolved_at_monotonic: float

    @property
    def endpoint(self) -> tuple[str, int]:
        return self.resolved_addresses[0], self.target.port


@dataclass(frozen=True, slots=True)
class ForwardTargetCounters:
    target_id: str
    label: str
    enabled: bool
    resolved_addresses: tuple[str, ...]
    sent_packets: int
    sent_bytes: int
    socket_errors: int
    queue_drops: int
    filtered_packets: int
    last_success_at: float | None
    last_error: str | None


@dataclass(frozen=True, slots=True)
class ForwarderSnapshot:
    running: bool
    accepting: bool
    queue_depth: int
    queue_capacity: int
    queue_high_water: int
    queue_drops: int
    rejected_datagrams: int
    last_drain_at: float | None
    targets: tuple[ForwardTargetCounters, ...]


@dataclass(slots=True)
class _MutableCounters:
    sent_packets: int = 0
    sent_bytes: int = 0
    socket_errors: int = 0
    queue_drops: int = 0
    filtered_packets: int = 0
    last_success_at: float | None = None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class _QueuedDatagram:
    data: bytes
    inspection: HeaderInspection


Resolver = Callable[[str, int], Awaitable[Sequence[str]]]
Sender = Callable[[bytes, tuple[str, int]], Awaitable[int | None]]


def validate_target_shape(target: ForwardTarget) -> None:
    if not _TARGET_ID.fullmatch(target.id):
        raise ForwardValidationError(
            "invalid_id",
            "target id must be 1-64 letters, numbers, dots, underscores, or dashes",
            target.id or None,
        )
    if not target.host:
        raise ForwardValidationError(
            "blank_host", "forward destination host is required", target.id
        )
    if not 1 <= target.port <= 65535:
        raise ForwardValidationError(
            "invalid_port", "forward destination port must be 1-65535", target.id
        )
    if target.packet_ids is not None and any(
        packet_id < 0 or packet_id > 255 for packet_id in target.packet_ids
    ):
        raise ForwardValidationError(
            "invalid_packet_filter",
            "packet ids must be integers from 0 through 255",
            target.id,
        )


def _directed_broadcasts(
    local_interfaces: Iterable[IPv4Interface],
) -> set[ipaddress.IPv4Address]:
    return {item.network.broadcast_address for item in local_interfaces}


def validate_resolved_targets(
    targets: Sequence[ForwardTarget],
    resolved: Mapping[str, Sequence[str]],
    *,
    listen_endpoints: Iterable[tuple[str, int]] = (),
    local_interfaces: Iterable[IPv4Interface] = (),
) -> tuple[ResolvedForwardTarget, ...]:
    """Validate resolved endpoints without performing DNS or socket operations."""

    target_ids: set[str] = set()
    local = tuple(local_interfaces)
    local_addresses = {item.address for item in local}
    broadcasts = _directed_broadcasts(local)
    listeners: list[tuple[str, int]] = []
    for host, port in listen_endpoints:
        try:
            listener_ip = str(ipaddress.IPv4Address(host))
        except ipaddress.AddressValueError:
            continue
        listeners.append((listener_ip, int(port)))

    result: list[ResolvedForwardTarget] = []
    enabled_endpoints: dict[tuple[str, int], str] = {}
    resolved_at = time.monotonic()
    for target in targets:
        validate_target_shape(target)
        if target.id in target_ids:
            raise ForwardValidationError(
                "duplicate_id", f"duplicate forward target id: {target.id}", target.id
            )
        target_ids.add(target.id)
        raw_addresses = resolved.get(target.id) or ()
        addresses: list[str] = []
        for raw_address in raw_addresses:
            try:
                parsed = ipaddress.ip_address(str(raw_address))
            except ValueError as exc:
                raise ForwardValidationError(
                    "unresolvable_host",
                    f"{target.host!r} did not resolve to a valid IPv4 address",
                    target.id,
                ) from exc
            if not isinstance(parsed, ipaddress.IPv4Address):
                continue
            address = str(parsed)
            if address not in addresses:
                addresses.append(address)
        if not addresses:
            raise ForwardValidationError(
                "unresolvable_host",
                f"{target.host!r} did not resolve to IPv4",
                target.id,
            )

        for address in addresses:
            parsed = ipaddress.IPv4Address(address)
            classification = classify_ipv4(parsed)
            is_broadcast = (
                classification.scope is AddressScope.BROADCAST or parsed in broadcasts
            )
            is_multicast = classification.scope is AddressScope.MULTICAST
            if (is_broadcast or is_multicast) and not target.allow_broadcast_multicast:
                kind = "broadcast" if is_broadcast else "multicast"
                raise ForwardValidationError(
                    f"{kind}_not_allowed",
                    f"{kind} forwarding requires the advanced confirmation",
                    target.id,
                )
            if classification.scope is AddressScope.UNSPECIFIED:
                raise ForwardValidationError(
                    "unspecified_destination",
                    "0.0.0.0 is a bind address, not a forwarding destination",
                    target.id,
                )
            if classification.scope is AddressScope.RESERVED:
                raise ForwardValidationError(
                    "reserved_destination",
                    f"{address} is reserved or special-purpose and cannot be used",
                    target.id,
                )
            if classification.scope is AddressScope.PUBLIC and not target.allow_public:
                raise ForwardValidationError(
                    "public_confirmation_required",
                    f"{address} is public; explicit external-address confirmation is required",
                    target.id,
                )
            for listen_host, listen_port in listeners:
                if target.port != listen_port:
                    continue
                exact = address == listen_host
                wildcard_local = listen_host == "0.0.0.0" and (
                    address in local_addresses
                    or ipaddress.IPv4Address(address).is_loopback
                )
                if exact or wildcard_local:
                    raise ForwardValidationError(
                        "self_loop",
                        f"{address}:{target.port} is the local Pit Wall listener",
                        target.id,
                    )

        selected = addresses[0]
        endpoint = (selected, target.port)
        if target.enabled and endpoint in enabled_endpoints:
            other = enabled_endpoints[endpoint]
            raise ForwardValidationError(
                "duplicate_destination",
                f"targets {other!r} and {target.id!r} resolve to the same enabled destination",
                target.id,
            )
        if target.enabled:
            enabled_endpoints[endpoint] = target.id
        result.append(ResolvedForwardTarget(target, tuple(addresses), resolved_at))
    return tuple(result)


async def _default_resolver(host: str, port: int) -> Sequence[str]:
    try:
        parsed = ipaddress.ip_address(host)
    except ValueError:
        loop = asyncio.get_running_loop()
        records = await loop.getaddrinfo(
            host,
            port,
            family=socket.AF_INET,
            type=socket.SOCK_DGRAM,
            proto=socket.IPPROTO_UDP,
        )
        return tuple(dict.fromkeys(str(record[4][0]) for record in records))
    if not isinstance(parsed, ipaddress.IPv4Address):
        return ()
    return (str(parsed),)


async def resolve_forward_targets(
    targets: Sequence[ForwardTarget],
    *,
    resolver: Resolver = _default_resolver,
    listen_endpoints: Iterable[tuple[str, int]] = (),
    local_interfaces: Iterable[IPv4Interface] = (),
) -> tuple[ResolvedForwardTarget, ...]:
    """Resolve all targets concurrently, then validate and publish none on error."""

    for target in targets:
        validate_target_shape(target)

    async def resolve_one(target: ForwardTarget) -> tuple[str, Sequence[str]]:
        try:
            addresses = await resolver(target.host, target.port)
        except (OSError, TimeoutError) as exc:
            raise ForwardValidationError(
                "unresolvable_host",
                f"could not resolve {target.host!r}: {type(exc).__name__}",
                target.id,
            ) from exc
        return target.id, addresses

    pairs = await asyncio.gather(*(resolve_one(target) for target in targets))
    return validate_resolved_targets(
        targets,
        dict(pairs),
        listen_endpoints=listen_endpoints,
        local_interfaces=local_interfaces,
    )


class DatagramForwarder:
    """One bounded sender worker for byte-identical UDP fan-out."""

    def __init__(
        self,
        *,
        queue_size: int = 2048,
        resolver: Resolver = _default_resolver,
        sender: Sender | None = None,
        resolution_ttl_s: float = 300.0,
    ) -> None:
        self.queue: asyncio.Queue[_QueuedDatagram] = asyncio.Queue(
            maxsize=max(1, int(queue_size))
        )
        self.resolver = resolver
        self._custom_sender = sender
        self.resolution_ttl_s = max(1.0, float(resolution_ttl_s))
        self._targets: tuple[ResolvedForwardTarget, ...] = ()
        self._configured_targets: tuple[ForwardTarget, ...] = ()
        self._listen_endpoints: tuple[tuple[str, int], ...] = ()
        self._local_interfaces: tuple[IPv4Interface, ...] = ()
        self._counters: dict[str, _MutableCounters] = {}
        self._worker_task: asyncio.Task[None] | None = None
        self._socket: socket.socket | None = None
        self._accepting = False
        self._queue_high_water = 0
        self._queue_drops = 0
        self._rejected_datagrams = 0
        self._last_drain_at: float | None = None

    @property
    def running(self) -> bool:
        return self._worker_task is not None and not self._worker_task.done()

    @property
    def targets(self) -> tuple[ResolvedForwardTarget, ...]:
        return self._targets

    async def reconfigure(
        self,
        targets: Sequence[ForwardTarget],
        *,
        listen_endpoints: Iterable[tuple[str, int]] = (),
        local_interfaces: Iterable[IPv4Interface] = (),
    ) -> tuple[ResolvedForwardTarget, ...]:
        """Resolve and validate a complete snapshot before one atomic swap."""

        configured = tuple(targets)
        listeners = tuple((str(host), int(port)) for host, port in listen_endpoints)
        local = tuple(local_interfaces)
        resolved = await resolve_forward_targets(
            configured,
            resolver=self.resolver,
            listen_endpoints=listeners,
            local_interfaces=local,
        )
        for target in configured:
            self._counters.setdefault(target.id, _MutableCounters())
        self._configured_targets = configured
        self._listen_endpoints = listeners
        self._local_interfaces = local
        self._targets = resolved
        return resolved

    async def refresh_resolution_if_due(self) -> bool:
        targets = self._targets
        if not targets:
            return False
        oldest = min(target.resolved_at_monotonic for target in targets)
        if time.monotonic() - oldest < self.resolution_ttl_s:
            return False
        await self.reconfigure(
            self._configured_targets,
            listen_endpoints=self._listen_endpoints,
            local_interfaces=self._local_interfaces,
        )
        return True

    async def start(self) -> None:
        if self.running:
            self._accepting = True
            return
        if self._custom_sender is None:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._socket.setblocking(False)
        self._accepting = True
        self._worker_task = asyncio.create_task(
            self._worker(), name="pitwall-datagram-forwarder"
        )

    def _eligible(
        self, target: ResolvedForwardTarget, inspection: HeaderInspection
    ) -> bool:
        configured = target.target
        if not configured.enabled:
            return False
        if not inspection.valid:
            return bool(
                inspection.header is not None
                and configured.forward_unknown_packets
                and (
                    configured.packet_ids is None
                    or inspection.header.packet_id in configured.packet_ids
                )
            )
        assert inspection.header is not None
        return bool(
            configured.packet_ids is None
            or inspection.header.packet_id in configured.packet_ids
        )

    def submit(self, datagram: bytes | bytearray | memoryview) -> bool:
        """Offer one datagram without awaiting, resolving, or sending inline."""

        if not self._accepting or not self.running:
            self._rejected_datagrams += 1
            return False
        data = bytes(datagram)
        inspection = inspect_2026_header(data)
        if inspection.header is None:
            self._rejected_datagrams += 1
            return False
        targets = self._targets
        if not any(self._eligible(target, inspection) for target in targets):
            for target in targets:
                if target.target.enabled:
                    self._counters.setdefault(
                        target.target.id, _MutableCounters()
                    ).filtered_packets += 1
            return False
        item = _QueuedDatagram(data, inspection)
        if self.queue.full():
            try:
                dropped = self.queue.get_nowait()
                self.queue.task_done()
                self._queue_drops += 1
                for target in targets:
                    if self._eligible(target, dropped.inspection):
                        self._counters.setdefault(
                            target.target.id, _MutableCounters()
                        ).queue_drops += 1
            except asyncio.QueueEmpty:
                pass
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            self._queue_drops += 1
            return False
        self._queue_high_water = max(self._queue_high_water, self.queue.qsize())
        return True

    async def _send(self, data: bytes, endpoint: tuple[str, int]) -> int | None:
        if self._custom_sender is not None:
            return await self._custom_sender(data, endpoint)
        if self._socket is None:
            raise RuntimeError("forwarding socket is not open")
        return await asyncio.get_running_loop().sock_sendto(
            self._socket, data, endpoint
        )

    async def _worker(self) -> None:
        while True:
            item = await self.queue.get()
            try:
                targets = self._targets
                for target in targets:
                    counters = self._counters.setdefault(
                        target.target.id, _MutableCounters()
                    )
                    if not self._eligible(target, item.inspection):
                        if target.target.enabled:
                            counters.filtered_packets += 1
                        continue
                    try:
                        await self._send(item.data, target.endpoint)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - isolate each target
                        counters.socket_errors += 1
                        counters.last_error = f"{type(exc).__name__}: {exc}"
                        continue
                    counters.sent_packets += 1
                    counters.sent_bytes += len(item.data)
                    counters.last_success_at = time.time()
                    counters.last_error = None
            finally:
                self.queue.task_done()
                self._last_drain_at = time.time()

    async def close(self, *, drain_timeout_s: float = 2.0) -> None:
        """Stop new submissions, drain briefly, then close all owned resources."""

        self._accepting = False
        task = self._worker_task
        if task is None:
            if self._socket is not None:
                self._socket.close()
                self._socket = None
            return
        try:
            await asyncio.wait_for(
                self.queue.join(), timeout=max(0.0, float(drain_timeout_s))
            )
        except TimeoutError:
            while True:
                try:
                    dropped = self.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                self.queue.task_done()
                self._queue_drops += 1
                for target in self._targets:
                    if self._eligible(target, dropped.inspection):
                        self._counters.setdefault(
                            target.target.id, _MutableCounters()
                        ).queue_drops += 1
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        self._worker_task = None
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def snapshot(self) -> ForwarderSnapshot:
        rows: list[ForwardTargetCounters] = []
        resolved_by_id = {target.target.id: target for target in self._targets}
        configured_by_id = {target.id: target for target in self._configured_targets}
        for target_id in sorted(configured_by_id):
            configured = configured_by_id[target_id]
            resolved = resolved_by_id.get(target_id)
            counters = self._counters.setdefault(target_id, _MutableCounters())
            rows.append(
                ForwardTargetCounters(
                    target_id=target_id,
                    label=configured.label,
                    enabled=configured.enabled,
                    resolved_addresses=(
                        resolved.resolved_addresses if resolved is not None else ()
                    ),
                    sent_packets=counters.sent_packets,
                    sent_bytes=counters.sent_bytes,
                    socket_errors=counters.socket_errors,
                    queue_drops=counters.queue_drops,
                    filtered_packets=counters.filtered_packets,
                    last_success_at=counters.last_success_at,
                    last_error=counters.last_error,
                )
            )
        return ForwarderSnapshot(
            running=self.running,
            accepting=self._accepting,
            queue_depth=self.queue.qsize(),
            queue_capacity=self.queue.maxsize,
            queue_high_water=self._queue_high_water,
            queue_drops=self._queue_drops,
            rejected_datagrams=self._rejected_datagrams,
            last_drain_at=self._last_drain_at,
            targets=tuple(rows),
        )

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
