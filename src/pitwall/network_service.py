"""Application-agnostic lifecycle and projection layer for UDP networking.

``NetworkService`` deliberately knows nothing about FastAPI, the race state
store, or database repositories.  It wraps an injected datagram protocol,
observes the original bytes before delegating them, and owns the independent
health/forwarding lifecycle needed by the 4.2 Connection Center.
"""

from __future__ import annotations

import asyncio
import errno
import ipaddress
import re
import socket
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol, cast

from .forwarding import (
    DatagramForwarder,
    ForwardTarget,
    ForwardTargetCounters,
)
from .network_profiles import NetworkProfileRepository, StoredNetworkProfile
from .networking import (
    DiscoveryResult,
    F1PacketHeader,
    InterfaceRecommendation,
    PacketHealthReport,
    PacketHealthTracker,
    build_redacted_diagnostics,
    discover_ipv4_interfaces,
    inspect_2026_header,
    recommend_ipv4_interface,
)


class ListenerState(str, Enum):
    OFF = "off"
    LISTENING = "listening"
    RECEIVING = "receiving"
    STALE = "stale"
    ERROR = "error"


class NetworkServiceError(RuntimeError):
    """Base error carrying a stable machine-readable API code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ListenerBindError(NetworkServiceError):
    def __init__(self, code: str, message: str, host: str, port: int) -> None:
        super().__init__(code, message)
        self.host = host
        self.port = port


class ForwardTargetExists(NetworkServiceError):
    def __init__(self, target_id: str) -> None:
        super().__init__(
            "forward_target_exists",
            f"A forwarding target with id {target_id!r} already exists.",
        )
        self.target_id = target_id


class ForwardTargetNotFound(NetworkServiceError):
    def __init__(self, target_id: str) -> None:
        super().__init__(
            "forward_target_not_found",
            f"Forwarding target {target_id!r} does not exist.",
        )
        self.target_id = target_id


@dataclass(frozen=True, slots=True)
class ListenerSnapshot:
    state: ListenerState
    bind_host: str
    port: int
    started_at: datetime | None
    last_valid_packet_age_ms: int | None
    error: str | None


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    depth: int
    capacity: int
    high_water: int
    drops: int
    last_drain_age_ms: int | None = None


@dataclass(frozen=True, slots=True)
class ManagedForwardTarget:
    target: ForwardTarget
    counters: ForwardTargetCounters


@dataclass(frozen=True, slots=True)
class NetworkSnapshot:
    listener: ListenerSnapshot
    discovery: DiscoveryResult
    recommendation: InterfaceRecommendation
    packet_health: PacketHealthReport
    source: Mapping[str, object] | None
    game: Mapping[str, object] | None
    forwarders: tuple[ManagedForwardTarget, ...]
    queues: Mapping[str, QueueSnapshot]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NetworkDiagnosis:
    checks: tuple[Mapping[str, object], ...]
    actions: tuple[str, ...]
    generated_at: datetime
    redacted_report: Mapping[str, object]


class ClosableDatagramTransport(Protocol):
    def close(self) -> None: ...

    def is_closing(self) -> bool: ...

    def get_extra_info(self, name: str, default: Any = None) -> Any: ...


ProtocolFactory = Callable[[], asyncio.DatagramProtocol]
EndpointCreator = Callable[
    [Callable[[], asyncio.DatagramProtocol], str, int],
    Awaitable[tuple[ClosableDatagramTransport, asyncio.DatagramProtocol]],
]
InterfaceDiscoverer = Callable[[], DiscoveryResult]
BindProbe = Callable[[str, int], str | None]


async def _create_endpoint(
    factory: Callable[[], asyncio.DatagramProtocol],
    host: str,
    port: int,
) -> tuple[ClosableDatagramTransport, asyncio.DatagramProtocol]:
    transport, protocol = await asyncio.get_running_loop().create_datagram_endpoint(
        factory,
        local_addr=(host, port),
        family=socket.AF_INET,
    )
    return cast(ClosableDatagramTransport, transport), protocol


def _probe_udp_bind(host: str, port: int) -> str | None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind((host, port))
    except OSError as exc:
        return _bind_error_code(exc)
    finally:
        probe.close()
    return None


def _bind_error_code(exc: OSError) -> str:
    code = exc.errno or getattr(exc, "winerror", None)
    if code in {errno.EADDRINUSE, 10048}:
        return "bind_conflict"
    if code in {errno.EACCES, errno.EPERM, 10013}:
        return "bind_permission_denied"
    if code in {errno.EADDRNOTAVAIL, 10049}:
        return "bind_address_unavailable"
    if isinstance(exc, socket.gaierror):
        return "invalid_bind_host"
    return "bind_failed"


def _bind_error_message(code: str, host: str, port: int) -> str:
    endpoint = f"{host}:{port}"
    if code == "bind_conflict":
        return (
            f"UDP {endpoint} is already in use. Stop the other telemetry receiver "
            "or choose a different UDP port."
        )
    if code == "bind_permission_denied":
        return (
            f"Windows denied access to UDP {endpoint}. Check the selected port "
            "and local security software."
        )
    if code == "bind_address_unavailable":
        return (
            f"{host} is not assigned to this computer. Select a listed local "
            "adapter address or use 0.0.0.0."
        )
    if code == "invalid_bind_host":
        return "The UDP bind host must be a valid local IPv4 address."
    return f"Pit Wall could not listen on UDP {endpoint}."


def _normalize_bind(host: str, port: int) -> tuple[str, int]:
    try:
        parsed = ipaddress.ip_address(str(host).strip())
    except ValueError as exc:
        raise ListenerBindError(
            "invalid_bind_host",
            "The UDP bind host must be a valid IPv4 address such as 0.0.0.0.",
            str(host),
            int(port),
        ) from exc
    if not isinstance(parsed, ipaddress.IPv4Address):
        raise ListenerBindError(
            "invalid_bind_host",
            "IPv6 UDP bind addresses are not supported by the F1 receiver.",
            str(host),
            int(port),
        )
    if parsed.is_multicast or parsed == ipaddress.IPv4Address("255.255.255.255"):
        raise ListenerBindError(
            "invalid_bind_host",
            "A multicast or broadcast address cannot be used as the listener bind.",
            str(parsed),
            int(port),
        )
    normalized_port = int(port)
    if not 1 <= normalized_port <= 65_535:
        raise ListenerBindError(
            "invalid_bind_port",
            "The UDP listener port must be between 1 and 65535.",
            str(parsed),
            normalized_port,
        )
    return str(parsed), normalized_port


class _ObservedProtocol(asyncio.DatagramProtocol):
    """Thin byte-observing adapter around the application's real protocol."""

    def __init__(
        self,
        service: NetworkService,
        delegate: asyncio.DatagramProtocol,
    ) -> None:
        self.service = service
        self.delegate = delegate
        self.transport: asyncio.BaseTransport | None = None
        self.intentional_close = False

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport
        callback = getattr(self.delegate, "connection_made", None)
        if callback:
            callback(transport)

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.service._observe_datagram(data, addr)
        callback = getattr(self.delegate, "datagram_received", None)
        if callback:
            try:
                callback(data, addr)
            except Exception as exc:  # noqa: BLE001 - isolate receiver callback
                self.service._record_delegate_error(exc)
        self.service._sample_receiver_queue(self.delegate)

    def error_received(self, exc: Exception) -> None:
        self.service._record_transport_error(exc)
        callback = getattr(self.delegate, "error_received", None)
        if callback:
            callback(exc)

    def connection_lost(self, exc: Exception | None) -> None:
        callback = getattr(self.delegate, "connection_lost", None)
        if callback:
            callback(exc)
        self.service._connection_lost(self, exc, intentional=self.intentional_close)


class NetworkService:
    """Own UDP listener health, forwarding configuration, and diagnostics."""

    def __init__(
        self,
        protocol_factory: ProtocolFactory,
        *,
        bind_host: str = "0.0.0.0",
        port: int = 20_777,
        stale_after_ms: int = 1_500,
        endpoint_creator: EndpointCreator = _create_endpoint,
        interface_discoverer: InterfaceDiscoverer = discover_ipv4_interfaces,
        packet_health: PacketHealthTracker | None = None,
        forwarder: DatagramForwarder | None = None,
        bind_probe: BindProbe = _probe_udp_bind,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
        now_utc: Callable[[], datetime] = lambda: datetime.now(UTC),
        pinned_adapter_id: str | None = None,
        pinned_address: str | None = None,
        delegate_tracks_health: bool = False,
        profile_repository: NetworkProfileRepository | None = None,
        profile_id: str = "default",
        profile_label: str = "Default network",
    ) -> None:
        normalized_host, normalized_port = _normalize_bind(bind_host, port)
        self._protocol_factory = protocol_factory
        self._endpoint_creator = endpoint_creator
        self._interface_discoverer = interface_discoverer
        self._health = packet_health or PacketHealthTracker()
        self._forwarder = forwarder or DatagramForwarder()
        self._bind_probe = bind_probe
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._now_utc = now_utc
        self.stale_after_ms = max(1, int(stale_after_ms))
        self._bind_host = normalized_host
        self._port = normalized_port
        self._transport: ClosableDatagramTransport | None = None
        self._protocol: _ObservedProtocol | None = None
        self._started_at: datetime | None = None
        self._last_valid_monotonic: float | None = None
        self._last_source: dict[str, object] | None = None
        self._last_game: dict[str, object] | None = None
        self._listener_error: str | None = None
        self._listener_error_code: str | None = None
        self._listener_lock = asyncio.Lock()
        self._targets_lock = asyncio.Lock()
        self._targets: dict[str, ForwardTarget] = {}
        self._discovery: DiscoveryResult | None = None
        self._discovery_lock = asyncio.Lock()
        self._pinned_adapter_id = pinned_adapter_id
        self._pinned_address = pinned_address
        self._delegate_tracks_health = bool(delegate_tracks_health)
        self._profile_repository = profile_repository
        self._profile_id = str(profile_id)
        self._profile_label = str(profile_label)
        self._persistence_error: str | None = None
        self._profile_persist_task: asyncio.Task[None] | None = None
        self._last_working_at: str | None = None
        self._prior_working_adapter_ids: set[str] = set()
        self._prior_working_addresses: set[str] = set()
        self._receiver_queue_high_water = 0
        self._receiver_queue_drops = 0
        self._receiver_callback_errors = 0

    @property
    def packet_health(self) -> PacketHealthTracker:
        return self._health

    @property
    def forwarder(self) -> DatagramForwarder:
        return self._forwarder

    @property
    def pinned_adapter_id(self) -> str | None:
        return self._pinned_adapter_id

    @property
    def prior_working_adapter_ids(self) -> frozenset[str]:
        return frozenset(self._prior_working_adapter_ids)

    @property
    def configured_targets(self) -> tuple[ForwardTarget, ...]:
        return tuple(self._targets.values())

    def pin_adapter(self, adapter_id: str | None, address: str | None = None) -> None:
        """Pin an adapter in memory (primarily for injected/test services).

        Product mutations should call :meth:`set_pinned_adapter`, which also
        commits the change to the configured profile repository.
        """
        self._pinned_adapter_id = adapter_id
        self._pinned_address = address

    async def set_pinned_adapter(
        self, adapter_id: str | None, address: str | None = None
    ) -> None:
        previous = self._pinned_adapter_id, self._pinned_address
        self.pin_adapter(adapter_id, address)
        try:
            await self.persist_profile()
        except Exception:
            self._pinned_adapter_id, self._pinned_address = previous
            raise

    def _stored_profile(
        self,
        *,
        targets: Mapping[str, ForwardTarget] | None = None,
        bind_host: str | None = None,
        port: int | None = None,
    ) -> StoredNetworkProfile:
        return StoredNetworkProfile(
            id=self._profile_id,
            label=self._profile_label,
            bind_host=bind_host or self._bind_host,
            udp_port=self._port if port is None else int(port),
            pinned_adapter_id=self._pinned_adapter_id,
            pinned_address=self._pinned_address,
            last_working_at=self._last_working_at,
            prior_working_adapter_ids=tuple(
                sorted(self._prior_working_adapter_ids)
            ),
            prior_working_addresses=tuple(sorted(self._prior_working_addresses)),
            targets=tuple(
                (self._targets if targets is None else targets).values()
            ),
        )

    async def persist_profile(
        self,
        *,
        targets: Mapping[str, ForwardTarget] | None = None,
        bind_host: str | None = None,
        port: int | None = None,
    ) -> None:
        if self._profile_repository is None:
            return
        try:
            await self._profile_repository.save(
                self._stored_profile(
                    targets=targets, bind_host=bind_host, port=port
                )
            )
        except Exception as exc:
            self._persistence_error = (
                "Network profile changes could not be saved: "
                f"{type(exc).__name__}"
            )
            raise NetworkServiceError(
                "network_profile_save_failed", self._persistence_error
            ) from exc
        self._persistence_error = None

    def _schedule_profile_persist(self) -> None:
        """Persist a newly proven adapter without touching the receive path."""

        if self._profile_repository is None:
            return
        running = self._profile_persist_task
        if running is not None and not running.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(
            self.persist_profile(), name="pitwall-network-profile-save"
        )

        def consume_result(done: asyncio.Task[None]) -> None:
            if not done.cancelled():
                done.exception()

        task.add_done_callback(consume_result)
        self._profile_persist_task = task

    async def load_persisted_profile(self) -> bool:
        """Load one profile after migrations, before opening the UDP socket."""

        if self._profile_repository is None:
            return False
        stored = await self._profile_repository.load(self._profile_id)
        if stored is None:
            return False
        host, port = _normalize_bind(stored.bind_host, stored.udp_port)
        discovery = await self.interfaces()
        targets = {target.id: target for target in stored.targets}
        async with self._targets_lock:
            await self._forwarder.reconfigure(
                tuple(targets.values()),
                listen_endpoints=((host, port),),
                local_interfaces=discovery.interfaces,
            )
            self._targets = targets
        self._bind_host = host
        self._port = port
        self._profile_label = stored.label
        self._pinned_adapter_id = stored.pinned_adapter_id
        self._pinned_address = stored.pinned_address
        self._last_working_at = stored.last_working_at
        self._prior_working_adapter_ids = set(
            stored.prior_working_adapter_ids
        )
        self._prior_working_addresses = set(stored.prior_working_addresses)
        self._persistence_error = None
        return True

    async def interfaces(self, *, refresh: bool = False) -> DiscoveryResult:
        """Ranked adapters, cached once platform discovery actually succeeds.

        A degraded fallback result is returned but deliberately not cached. The
        fallback reports a single unclassified address with no gateway or
        adapter kind, so caching it would strand the Connection Center on a
        transient failure - a slow first PowerShell start, a busy machine -
        with no way back short of a manual refresh.
        """
        if self._discovery is not None and not refresh:
            return self._discovery
        async with self._discovery_lock:
            if self._discovery is None or refresh:
                discovery = await asyncio.to_thread(self._interface_discoverer)
                if discovery.source == "stdlib-fallback":
                    return discovery
                self._discovery = discovery
            return self._discovery

    def _recommendation(self, discovery: DiscoveryResult) -> InterfaceRecommendation:
        return recommend_ipv4_interface(
            discovery.interfaces,
            pinned_adapter_id=self._pinned_adapter_id,
            pinned_address=self._pinned_address,
            prior_working_adapter_ids=self._prior_working_adapter_ids,
            prior_working_addresses=self._prior_working_addresses,
        )

    def listener_snapshot(self) -> ListenerSnapshot:
        now = self._monotonic()
        age_ms = (
            None
            if self._last_valid_monotonic is None
            else max(0, round((now - self._last_valid_monotonic) * 1_000))
        )
        if self._listener_error:
            state = ListenerState.ERROR
        elif self._transport is None:
            state = ListenerState.OFF
        elif age_ms is None:
            state = ListenerState.LISTENING
        elif age_ms > self.stale_after_ms:
            state = ListenerState.STALE
        else:
            state = ListenerState.RECEIVING
        return ListenerSnapshot(
            state=state,
            bind_host=self._bind_host,
            port=self._port,
            started_at=self._started_at,
            last_valid_packet_age_ms=age_ms,
            error=self._listener_error,
        )

    async def start_listener(
        self,
        bind_host: str | None = None,
        port: int | None = None,
    ) -> ListenerSnapshot:
        host, normalized_port = _normalize_bind(
            self._bind_host if bind_host is None else bind_host,
            self._port if port is None else port,
        )
        async with self._listener_lock:
            if (
                self._transport is not None
                and not self._transport.is_closing()
                and host == self._bind_host
                and normalized_port == self._port
            ):
                return self.listener_snapshot()

            discovery = await self.interfaces()
            async with self._targets_lock:
                # Validate a prospective bind against every configured target
                # before disrupting a healthy existing listener.  Reconfigure
                # publishes atomically only after all DNS and loop checks pass.
                await self._forwarder.reconfigure(
                    tuple(self._targets.values()),
                    listen_endpoints=((host, normalized_port),),
                    local_interfaces=discovery.interfaces,
                )
                if self._transport is not None:
                    await self._stop_transport_locked()

                self._bind_host = host
                self._port = normalized_port
                self._started_at = None
                self._last_valid_monotonic = None
                self._last_source = None
                self._last_game = None
                try:
                    await self._forwarder.start()
                    delegate = self._protocol_factory()

                    def protocol_factory() -> asyncio.DatagramProtocol:
                        return _ObservedProtocol(self, delegate)

                    transport, protocol = await self._endpoint_creator(
                        protocol_factory, host, normalized_port
                    )
                except OSError as exc:
                    await self._forwarder.close()
                    code = _bind_error_code(exc)
                    message = _bind_error_message(code, host, normalized_port)
                    self._listener_error_code = code
                    self._listener_error = message
                    self._transport = None
                    self._protocol = None
                    raise ListenerBindError(
                        code, message, host, normalized_port
                    ) from exc

                self._transport = transport
                self._protocol = cast(_ObservedProtocol, protocol)
                sockname = transport.get_extra_info("sockname", (host, normalized_port))
                self._port = int(sockname[1]) if sockname else normalized_port
                self._started_at = self._now_utc()
                self._listener_error = None
                self._listener_error_code = None
                self._receiver_queue_high_water = 0
                self._receiver_queue_drops = 0
                self._receiver_callback_errors = 0
                try:
                    await self.persist_profile(bind_host=host, port=self._port)
                except NetworkServiceError:
                    # Reception remains more important than preference storage;
                    # the Connection Center exposes the persistence warning.
                    pass
                return self.listener_snapshot()

    async def stop_listener(self) -> ListenerSnapshot:
        async with self._listener_lock:
            try:
                await self.persist_profile()
            except NetworkServiceError:
                pass
            await self._stop_transport_locked()
            await self._forwarder.close()
            self._listener_error = None
            self._listener_error_code = None
            self._started_at = None
            self._last_valid_monotonic = None
            self._last_source = None
            self._last_game = None
            return self.listener_snapshot()

    async def _stop_transport_locked(self) -> None:
        protocol = self._protocol
        transport = self._transport
        self._protocol = None
        self._transport = None
        if protocol is not None:
            protocol.intentional_close = True
            drain = getattr(protocol.delegate, "drain_before_close", None)
            if drain is not None:
                try:
                    await drain()
                except Exception as exc:  # noqa: BLE001 - shutdown remains bounded
                    self._record_delegate_error(exc)
        if transport is not None and not transport.is_closing():
            transport.close()
            await asyncio.sleep(0)

    def _observe_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        now = self._monotonic()
        # The production F1 protocol records a pending observation and marks
        # the real parser result on its consumer task. Lightweight injected
        # protocols used by diagnostics/tests can retain the service-owned
        # accounting path. Never count one datagram twice.
        inspection = (
            inspect_2026_header(data)
            if self._delegate_tracks_health
            else self._health.observe_datagram(
                data,
                addr,
                received_monotonic=now,
                received_wall=self._wall_time(),
                parsed=True,
            )
        )
        self._forwarder.submit(data)
        if not inspection.valid or inspection.header is None:
            return
        header = inspection.header
        self._last_valid_monotonic = now
        self._last_source = {"ip": str(addr[0]), "port": int(addr[1])}
        self._last_game = self._game_metadata(header)
        self._remember_working_interface(str(addr[0]))

    @staticmethod
    def _game_metadata(header: F1PacketHeader) -> dict[str, object]:
        return {
            "packet_format": header.packet_format,
            "game_year": header.game_year,
            "game_major_version": header.game_major_version,
            "game_minor_version": header.game_minor_version,
            "packet_version": header.packet_version,
            "session_uid": str(header.session_uid),
        }

    def _remember_working_interface(self, source_ip: str) -> None:
        discovery = self._discovery
        if discovery is None:
            return
        try:
            source = ipaddress.IPv4Address(source_ip)
        except ipaddress.AddressValueError:
            return
        matches = [
            interface
            for interface in discovery.interfaces
            if source in interface.network and interface.is_up
        ]
        if len(matches) == 1:
            match = matches[0]
            newly_working = (
                match.adapter_id not in self._prior_working_adapter_ids
                or match.address not in self._prior_working_addresses
            )
            pinned_address_changed = (
                self._pinned_adapter_id == match.adapter_id
                and self._pinned_address != match.address
            )
            self._prior_working_adapter_ids.add(match.adapter_id)
            self._prior_working_addresses.add(match.address)
            self._last_working_at = self._now_utc().isoformat()
            if self._pinned_adapter_id == match.adapter_id:
                self._pinned_address = match.address
            if newly_working or pinned_address_changed:
                self._schedule_profile_persist()

    def _sample_receiver_queue(self, delegate: asyncio.DatagramProtocol) -> None:
        queue = getattr(delegate, "packet_queue", None)
        if queue is None:
            return
        try:
            self._receiver_queue_high_water = max(
                self._receiver_queue_high_water, int(queue.qsize())
            )
            self._receiver_queue_drops = max(
                self._receiver_queue_drops,
                int(getattr(delegate, "receiver_queue_drops", 0)),
            )
        except (AttributeError, TypeError, ValueError):
            return

    def _record_delegate_error(self, exc: Exception) -> None:
        self._receiver_callback_errors += 1
        self._listener_error = f"Receiver callback failed: {type(exc).__name__}"

    def _record_transport_error(self, exc: Exception) -> None:
        self._listener_error = f"UDP transport error: {type(exc).__name__}"

    def _connection_lost(
        self,
        protocol: _ObservedProtocol,
        exc: Exception | None,
        *,
        intentional: bool,
    ) -> None:
        if protocol is not self._protocol:
            return
        self._transport = None
        self._protocol = None
        if not intentional:
            self._listener_error_code = "listener_lost"
            self._listener_error = "The UDP listener stopped unexpectedly" + (
                f": {type(exc).__name__}" if exc else "."
            )

    async def create_forward_target(
        self,
        *,
        label: str,
        host: str,
        port: int,
        target_id: str | None = None,
        enabled: bool = True,
        packet_ids: Iterable[int] | None = None,
        forward_unknown_packets: bool = False,
        allow_public: bool = False,
        allow_broadcast_multicast: bool = False,
    ) -> ManagedForwardTarget:
        if not str(label).strip():
            raise NetworkServiceError(
                "invalid_forward_label", "A forwarding target label is required."
            )
        generated_id = target_id or self._new_target_id(label)
        target = ForwardTarget(
            id=generated_id,
            label=label,
            host=host,
            port=port,
            enabled=enabled,
            packet_ids=None if packet_ids is None else frozenset(packet_ids),
            forward_unknown_packets=forward_unknown_packets,
            allow_public=allow_public,
            allow_broadcast_multicast=allow_broadcast_multicast,
        )
        async with self._targets_lock:
            if target.id in self._targets:
                raise ForwardTargetExists(target.id)
            candidate = {**self._targets, target.id: target}
            await self._commit_targets(candidate)
        return self._managed_target(target.id)

    async def update_forward_target(
        self,
        target_id: str,
        **changes: object,
    ) -> ManagedForwardTarget:
        async with self._targets_lock:
            current = self._targets.get(target_id)
            if current is None:
                raise ForwardTargetNotFound(target_id)
            allowed = {
                "label",
                "host",
                "port",
                "enabled",
                "packet_ids",
                "forward_unknown_packets",
                "allow_public",
                "allow_broadcast_multicast",
            }
            unknown = set(changes) - allowed
            if unknown:
                raise NetworkServiceError(
                    "invalid_forward_patch",
                    f"Unsupported forwarding fields: {', '.join(sorted(unknown))}.",
                )
            if "packet_ids" in changes and changes["packet_ids"] is not None:
                changes["packet_ids"] = frozenset(
                    cast(Iterable[int], changes["packet_ids"])
                )
            updated = replace(current, **changes)
            if not updated.label:
                raise NetworkServiceError(
                    "invalid_forward_label", "A forwarding target label is required."
                )
            candidate = {**self._targets, target_id: updated}
            await self._commit_targets(candidate)
        return self._managed_target(target_id)

    async def delete_forward_target(self, target_id: str) -> None:
        async with self._targets_lock:
            if target_id not in self._targets:
                raise ForwardTargetNotFound(target_id)
            candidate = {
                key: value for key, value in self._targets.items() if key != target_id
            }
            await self._commit_targets(candidate)

    async def _commit_targets(
        self, candidate: Mapping[str, ForwardTarget]
    ) -> None:
        previous = self._targets
        await self._apply_targets(candidate)
        self._targets = dict(candidate)
        try:
            await self.persist_profile(targets=candidate)
        except NetworkServiceError:
            # Keep durable and in-memory configuration coherent when SQLite is
            # unavailable. Re-publish the last known configuration atomically.
            self._targets = previous
            await self._apply_targets(previous)
            raise

    async def _apply_targets(self, targets: Mapping[str, ForwardTarget]) -> None:
        discovery = await self.interfaces()
        listener = self.listener_snapshot()
        endpoints: tuple[tuple[str, int], ...] = ()
        if listener.state is not ListenerState.OFF and self._transport is not None:
            endpoints = ((listener.bind_host, listener.port),)
        await self._forwarder.reconfigure(
            tuple(targets.values()),
            listen_endpoints=endpoints,
            local_interfaces=discovery.interfaces,
        )

    @staticmethod
    def _new_target_id(label: str) -> str:
        prefix = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")
        prefix = prefix[:40] or "forwarder"
        return f"{prefix}_{uuid.uuid4().hex[:8]}"

    def _managed_target(self, target_id: str) -> ManagedForwardTarget:
        target = self._targets[target_id]
        counters = {
            item.target_id: item for item in self._forwarder.snapshot().targets
        }.get(target_id)
        if counters is None:
            counters = ForwardTargetCounters(
                target_id=target.id,
                label=target.label,
                enabled=target.enabled,
                resolved_addresses=(),
                sent_packets=0,
                sent_bytes=0,
                socket_errors=0,
                queue_drops=0,
                filtered_packets=0,
                last_success_at=None,
                last_error=None,
            )
        return ManagedForwardTarget(target, counters)

    def managed_targets(self) -> tuple[ManagedForwardTarget, ...]:
        return tuple(self._managed_target(key) for key in self._targets)

    def _queue_snapshots(self) -> dict[str, QueueSnapshot]:
        forward = self._forwarder.snapshot()
        last_drain_age = (
            None
            if forward.last_drain_at is None
            else max(0, round((self._wall_time() - forward.last_drain_at) * 1_000))
        )
        receiver_depth = 0
        receiver_capacity = 0
        protocol = self._protocol
        queue = getattr(protocol.delegate, "packet_queue", None) if protocol else None
        if queue is not None:
            try:
                receiver_depth = int(queue.qsize())
                receiver_capacity = int(queue.maxsize)
            except (AttributeError, TypeError, ValueError):
                pass
        return {
            "receiver": QueueSnapshot(
                depth=receiver_depth,
                capacity=receiver_capacity,
                high_water=self._receiver_queue_high_water,
                drops=max(
                    self._receiver_queue_drops,
                    int(getattr(protocol.delegate, "receiver_queue_drops", 0))
                    if protocol is not None
                    else 0,
                ),
            ),
            "forwarding": QueueSnapshot(
                depth=forward.queue_depth,
                capacity=forward.queue_capacity,
                high_water=forward.queue_high_water,
                drops=forward.queue_drops,
                last_drain_age_ms=last_drain_age,
            ),
        }

    async def snapshot(self, *, refresh_interfaces: bool = False) -> NetworkSnapshot:
        discovery = await self.interfaces(refresh=refresh_interfaces)
        recommendation = self._recommendation(discovery)
        listener = self.listener_snapshot()
        health = self._health.report(now_monotonic=self._monotonic())
        warnings = [*discovery.warnings, *recommendation.warnings]
        if listener.error:
            warnings.append(listener.error)
        if listener.state is ListenerState.LISTENING:
            warnings.append(
                "Pit Wall is listening, but no valid F1 2026 telemetry has arrived."
            )
        elif listener.state is ListenerState.STALE:
            warnings.append("F1 telemetry was received but is now stale.")
        if health.invalid:
            count = sum(item.received for item in health.invalid)
            warnings.append(f"{count} invalid or incompatible UDP datagrams observed.")
        if any(item.counters.socket_errors for item in self.managed_targets()):
            warnings.append("One or more forwarding targets reported send errors.")
        if self._persistence_error:
            warnings.append(self._persistence_error)
        return NetworkSnapshot(
            listener=listener,
            discovery=discovery,
            recommendation=recommendation,
            packet_health=health,
            source=dict(self._last_source) if self._last_source else None,
            game=dict(self._last_game) if self._last_game else None,
            forwarders=self.managed_targets(),
            queues=self._queue_snapshots(),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    async def diagnose(self) -> NetworkDiagnosis:
        snapshot = await self.snapshot(refresh_interfaces=True)
        checks: list[Mapping[str, object]] = []
        actions: list[str] = []

        interface_ok = snapshot.recommendation.recommended is not None
        checks.append(
            {
                "id": "console_interface",
                "status": "pass" if interface_ok else "fail",
                "message": (
                    "A likely console-reachable private IPv4 adapter is available."
                    if interface_ok
                    else "No active private-LAN IPv4 adapter could be recommended."
                ),
            }
        )
        if not interface_ok:
            actions.append(
                "Connect the PC to the same LAN as the console and disable an unintended VPN route."
            )

        listener = snapshot.listener
        if listener.state is ListenerState.ERROR:
            bind_status = "fail"
            bind_message = listener.error or "The UDP listener failed."
        elif self._transport is not None:
            bind_status = "pass"
            bind_message = f"Pit Wall owns UDP {listener.bind_host}:{listener.port}."
        else:
            bind_code = await asyncio.to_thread(
                self._bind_probe, listener.bind_host, listener.port
            )
            bind_status = "pass" if bind_code is None else "fail"
            bind_message = (
                f"UDP {listener.bind_host}:{listener.port} is available to listen."
                if bind_code is None
                else _bind_error_message(bind_code, listener.bind_host, listener.port)
            )
        checks.append(
            {"id": "udp_bind", "status": bind_status, "message": bind_message}
        )
        if bind_status == "fail":
            actions.append(bind_message)

        receiving = listener.state is ListenerState.RECEIVING
        checks.append(
            {
                "id": "telemetry_reception",
                "status": "pass" if receiving else "warning",
                "message": (
                    "Valid F1 2026 telemetry is arriving."
                    if receiving
                    else "A listener or free port does not prove the console is sending telemetry."
                ),
            }
        )
        if listener.state in {ListenerState.LISTENING, ListenerState.STALE}:
            recommended = snapshot.recommendation.recommended
            destination = (
                recommended.address if recommended else "the recommended PC IPv4"
            )
            actions.append(
                f"Set the PS5 telemetry destination to {destination}:{listener.port}, "
                "select the 2026 packet format, and start an on-track session."
            )

        forwarding_errors = sum(
            item.counters.socket_errors for item in snapshot.forwarders
        )
        checks.append(
            {
                "id": "forwarding",
                "status": "warning" if forwarding_errors else "pass",
                "message": (
                    f"Forwarding targets reported {forwarding_errors} send errors."
                    if forwarding_errors
                    else "Configured forwarding targets have no reported send errors."
                ),
            }
        )
        if forwarding_errors:
            actions.append(
                "Check the address and port of each failing forwarding target."
            )

        redacted = build_redacted_diagnostics(
            discovery=snapshot.discovery,
            recommendation=snapshot.recommendation,
            packet_health=snapshot.packet_health,
            warnings=snapshot.warnings,
        )
        return NetworkDiagnosis(
            checks=tuple(checks),
            actions=tuple(dict.fromkeys(actions)),
            generated_at=self._now_utc(),
            redacted_report=redacted,
        )
