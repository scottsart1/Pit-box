"""Network discovery and F1 UDP health primitives for Your Pit Box 4.2.

This module is deliberately independent of FastAPI, the UDP receiver, and the
database.  The receiver can use the header and health types on its hot path;
the application layer can use the discovery/recommendation types without
granting administrator privileges or depending on a Windows-only package.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import platform
import re
import socket
import statistics
import struct
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

F1_2026_PACKET_FORMAT = 2026
F1_HEADER_SIZE = 29
UINT32_MASK = (1 << 32) - 1
UINT32_HALF_RANGE = 1 << 31
_F1_HEADER = struct.Struct("<HBBBBBQfIIBB")

_PRIVATE_NETWORKS = tuple(
    ipaddress.IPv4Network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


class AddressScope(str, Enum):
    """Operational IPv4 classes used by Connection Center."""

    PRIVATE = "private"
    LOOPBACK = "loopback"
    UNSPECIFIED = "unspecified"
    LINK_LOCAL = "link_local"
    MULTICAST = "multicast"
    BROADCAST = "broadcast"
    PUBLIC = "public"
    RESERVED = "reserved"


class AdapterKind(str, Enum):
    ETHERNET = "ethernet"
    WIFI = "wifi"
    VPN = "vpn"
    VIRTUAL = "virtual"
    LOOPBACK = "loopback"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class IPv4Classification:
    address: str
    scope: AddressScope
    console_candidate: bool
    external: bool
    explanation: str


def classify_ipv4(address: str | ipaddress.IPv4Address) -> IPv4Classification:
    """Classify an IPv4 address without consulting host or route state."""

    parsed = ipaddress.ip_address(str(address).strip())
    if not isinstance(parsed, ipaddress.IPv4Address):
        raise TypeError(f"IPv4 address required, got {address!r}")
    text = str(parsed)
    if parsed.is_unspecified:
        return IPv4Classification(
            text,
            AddressScope.UNSPECIFIED,
            False,
            False,
            "bind wildcard; not a destination address",
        )
    if parsed.is_loopback:
        return IPv4Classification(
            text, AddressScope.LOOPBACK, False, False, "this computer only"
        )
    if parsed == ipaddress.IPv4Address("255.255.255.255"):
        return IPv4Classification(
            text, AddressScope.BROADCAST, False, False, "limited broadcast"
        )
    if parsed.is_multicast:
        return IPv4Classification(
            text, AddressScope.MULTICAST, False, False, "multicast destination"
        )
    if parsed.is_link_local:
        return IPv4Classification(
            text,
            AddressScope.LINK_LOCAL,
            False,
            False,
            "self-assigned link-local address; DHCP may have failed",
        )
    if any(parsed in network for network in _PRIVATE_NETWORKS):
        return IPv4Classification(
            text,
            AddressScope.PRIVATE,
            True,
            False,
            "private LAN address",
        )
    if parsed.is_global:
        return IPv4Classification(
            text,
            AddressScope.PUBLIC,
            False,
            True,
            "public/external address",
        )
    return IPv4Classification(
        text,
        AddressScope.RESERVED,
        False,
        False,
        "reserved or special-purpose address",
    )


def classify_adapter_kind(name: str, description: str = "") -> AdapterKind:
    value = f"{name} {description}".casefold()
    if "loopback" in value:
        return AdapterKind.LOOPBACK
    if any(
        token in value
        for token in ("wireguard", "tailscale", "openvpn", " vpn", "tap-", "tun")
    ):
        return AdapterKind.VPN
    if any(
        token in value
        for token in (
            "hyper-v",
            "virtual",
            "vmware",
            "virtualbox",
            "vbox",
            "docker",
            "wsl",
        )
    ):
        return AdapterKind.VIRTUAL
    if any(token in value for token in ("wi-fi", "wifi", "wireless", "wlan")):
        return AdapterKind.WIFI
    if any(token in value for token in ("ethernet", "gigabit", "lan")):
        return AdapterKind.ETHERNET
    return AdapterKind.UNKNOWN


@dataclass(frozen=True, slots=True)
class IPv4Interface:
    adapter_id: str
    name: str
    address: str
    prefix_length: int = 24
    description: str = ""
    is_up: bool = True
    has_default_gateway: bool = False
    metric: int | None = None
    kind: AdapterKind = AdapterKind.UNKNOWN

    def __post_init__(self) -> None:
        parsed = ipaddress.ip_address(self.address)
        if not isinstance(parsed, ipaddress.IPv4Address):
            raise TypeError("IPv4Interface requires an IPv4 address")
        if not 0 <= int(self.prefix_length) <= 32:
            raise ValueError("prefix_length must be between 0 and 32")
        object.__setattr__(self, "address", str(parsed))
        object.__setattr__(self, "prefix_length", int(self.prefix_length))
        if self.metric is not None:
            object.__setattr__(self, "metric", max(0, int(self.metric)))

    @property
    def classification(self) -> IPv4Classification:
        return classify_ipv4(self.address)

    @property
    def network(self) -> ipaddress.IPv4Network:
        return ipaddress.IPv4Network(
            f"{self.address}/{self.prefix_length}", strict=False
        )


@dataclass(frozen=True, slots=True)
class RankedIPv4Interface:
    interface: IPv4Interface
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InterfaceRecommendation:
    ranked: tuple[RankedIPv4Interface, ...]
    recommended: IPv4Interface | None
    confidence: str
    warnings: tuple[str, ...] = ()


def recommend_ipv4_interface(
    interfaces: Iterable[IPv4Interface],
    *,
    pinned_adapter_id: str | None = None,
    pinned_address: str | None = None,
    prior_working_adapter_ids: Collection[str] = (),
    prior_working_addresses: Collection[str] = (),
) -> InterfaceRecommendation:
    """Rank adapters and honour a user's pinned adapter without silent fallback."""

    prior_ids = {str(value) for value in prior_working_adapter_ids}
    prior_addresses = {
        str(ipaddress.IPv4Address(value)) for value in prior_working_addresses
    }
    ranked: list[RankedIPv4Interface] = []
    for interface in interfaces:
        reasons: list[str] = []
        score = 0.0
        classification = interface.classification
        if interface.is_up:
            score += 40
            reasons.append("adapter is up")
        else:
            score -= 500
            reasons.append("adapter is down")
        if classification.scope is AddressScope.PRIVATE:
            score += 100
            reasons.append("private IPv4")
        elif classification.scope is AddressScope.LINK_LOCAL:
            score -= 150
            reasons.append("self-assigned link-local IPv4")
        else:
            score -= 250
            reasons.append(classification.explanation)
        if interface.has_default_gateway:
            score += 35
            reasons.append("default route")
        if interface.kind in {AdapterKind.ETHERNET, AdapterKind.WIFI}:
            score += 25
            reasons.append(interface.kind.value)
        elif interface.kind in {AdapterKind.VPN, AdapterKind.VIRTUAL}:
            score -= 60
            reasons.append(f"{interface.kind.value} adapter")
        elif interface.kind is AdapterKind.LOOPBACK:
            score -= 300
        if interface.metric is not None:
            score += max(0.0, 20.0 - min(interface.metric, 200) / 10.0)
            reasons.append(f"route metric {interface.metric}")
        if interface.adapter_id in prior_ids or interface.address in prior_addresses:
            score += 300
            reasons.append("valid F1 traffic observed previously")
        if pinned_adapter_id and interface.adapter_id == pinned_adapter_id:
            score += 1_000
            reasons.append("pinned by user")
        ranked.append(RankedIPv4Interface(interface, round(score, 2), tuple(reasons)))

    ranked.sort(
        key=lambda item: (
            item.score,
            -int(item.interface.metric or 0),
            item.interface.adapter_id,
            item.interface.address,
        ),
        reverse=True,
    )
    warnings: list[str] = []
    recommended: IPv4Interface | None = None
    confidence = "none"

    if pinned_adapter_id:
        pinned = [
            item.interface
            for item in ranked
            if item.interface.adapter_id == pinned_adapter_id
        ]
        if not pinned:
            warnings.append(
                f"Pinned adapter {pinned_adapter_id!r} is not currently present; "
                "no replacement was selected automatically."
            )
        else:
            pinned_address_normalized = (
                str(ipaddress.IPv4Address(pinned_address)) if pinned_address else None
            )
            recommended = next(
                (
                    interface
                    for interface in pinned
                    if interface.address == pinned_address_normalized
                ),
                pinned[0],
            )
            if pinned_address:
                old_address = pinned_address_normalized
                assert old_address is not None
                if recommended.address != old_address:
                    warnings.append(
                        f"The pinned adapter address changed from {old_address} "
                        f"to {recommended.address}; update the console destination."
                    )
            if not recommended.is_up:
                warnings.append("The pinned adapter is currently down.")
            if not recommended.classification.console_candidate:
                warnings.append(
                    "The pinned adapter does not currently have a console-reachable private IPv4 address."
                )
            confidence = (
                "high"
                if recommended.is_up
                and recommended.classification.console_candidate
                and recommended.has_default_gateway
                else "low"
            )
    else:
        recommended_item = next(
            (
                item
                for item in ranked
                if item.interface.is_up
                and item.interface.classification.console_candidate
            ),
            None,
        )
        if recommended_item:
            recommended = recommended_item.interface
            prior = (
                recommended.adapter_id in prior_ids
                or recommended.address in prior_addresses
            )
            confidence = (
                "high" if prior or recommended.has_default_gateway else "medium"
            )
        elif ranked:
            warnings.append("No active private-LAN IPv4 interface was found.")

    return InterfaceRecommendation(
        tuple(ranked), recommended, confidence, tuple(warnings)
    )


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    interfaces: tuple[IPv4Interface, ...]
    source: str
    warnings: tuple[str, ...] = ()


_WINDOWS_DISCOVERY_SCRIPT = r"""
$items = Get-NetIPConfiguration -ErrorAction Stop | ForEach-Object {
  $cfg = $_
  $metric = Get-NetIPInterface -AddressFamily IPv4 -InterfaceIndex $cfg.InterfaceIndex -ErrorAction SilentlyContinue |
    Sort-Object InterfaceMetric | Select-Object -First 1 -ExpandProperty InterfaceMetric
  [pscustomobject]@{
    adapter_id = [string]$cfg.InterfaceIndex
    name = [string]$cfg.InterfaceAlias
    description = [string]$cfg.InterfaceDescription
    status = [string]$cfg.NetAdapter.Status
    metric = $metric
    has_default_gateway = [bool]($cfg.IPv4DefaultGateway)
    addresses = @($cfg.IPv4Address | ForEach-Object {
      [pscustomobject]@{ address = [string]$_.IPAddress; prefix_length = [int]$_.PrefixLength }
    })
  }
}
$items | ConvertTo-Json -Depth 5 -Compress
""".strip()


def parse_windows_interface_json(payload: str) -> tuple[IPv4Interface, ...]:
    """Parse the structured output of the PowerShell discovery command."""

    decoded = json.loads(payload or "[]")
    records = decoded if isinstance(decoded, list) else [decoded]
    result: list[IPv4Interface] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        name = str(record.get("name") or "Unknown adapter")
        description = str(record.get("description") or "")
        adapter_id = str(record.get("adapter_id") or name)
        status = str(record.get("status") or "").casefold()
        is_up = status in {"up", "connected", "unknown", ""}
        metric_value = record.get("metric")
        metric = int(metric_value) if metric_value not in {None, ""} else None
        addresses = record.get("addresses") or []
        if isinstance(addresses, (str, Mapping)):
            addresses = [addresses]
        for address_record in addresses:
            if isinstance(address_record, Mapping):
                address = str(address_record.get("address") or "")
                prefix = int(address_record.get("prefix_length") or 24)
            else:
                address = str(address_record)
                prefix = 24
            try:
                result.append(
                    IPv4Interface(
                        adapter_id=adapter_id,
                        name=name,
                        description=description,
                        address=address,
                        prefix_length=prefix,
                        is_up=is_up,
                        has_default_gateway=bool(
                            record.get("has_default_gateway", False)
                        ),
                        metric=metric,
                        kind=classify_adapter_kind(name, description),
                    )
                )
            except (TypeError, ValueError):
                continue
    unique = {
        (item.adapter_id, item.address, item.prefix_length): item for item in result
    }
    return tuple(unique.values())


def fallback_ipv4_interfaces() -> tuple[IPv4Interface, ...]:
    """Best-effort stdlib discovery used when Windows APIs are unavailable."""

    addresses: set[str] = set()
    try:
        for info in socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET, socket.SOCK_DGRAM
        ):
            addresses.add(str(info[4][0]))
    except OSError:
        pass
    # On Android the hostname resolves to loopback only, and on Linux it often
    # resolves to 127.0.1.1. Asking the kernel which source address it would
    # route a packet from reveals the LAN interface without sending anything:
    # connect() on a UDP socket only chooses a route.
    if all(address.startswith("127.") for address in addresses):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(("192.0.2.1", 9))
                addresses.add(probe.getsockname()[0])
        except OSError:
            pass
    result = []
    for address in sorted(addresses):
        try:
            classification = classify_ipv4(address)
        except ValueError:
            continue
        result.append(
            IPv4Interface(
                adapter_id=f"fallback:{address}",
                name="Detected IPv4 interface",
                address=address,
                prefix_length=24,
                is_up=True,
                kind=(
                    AdapterKind.LOOPBACK
                    if classification.scope is AddressScope.LOOPBACK
                    else AdapterKind.UNKNOWN
                ),
            )
        )
    return tuple(result)


RunCommand = Callable[..., subprocess.CompletedProcess[str]]


def discover_ipv4_interfaces(
    *,
    timeout_s: float = 20.0,
    platform_name: str | None = None,
    runner: RunCommand = subprocess.run,
    fallback: Callable[[], tuple[IPv4Interface, ...]] = fallback_ipv4_interfaces,
) -> DiscoveryResult:
    """Discover IPv4 interfaces without elevation and with a hard command timeout.

    The timeout budgets a cold ``powershell.exe`` start, not just the cmdlets.
    On a measured Windows 11 machine the same script returned in 9-17 s because
    process startup dominates, so a 2 s budget failed every time and silently
    degraded the Connection Center to one unclassified socket-derived address.
    The result is cached by the caller, so this cost is paid once per refresh
    rather than per request.
    """

    system = platform_name or platform.system()
    warnings: list[str] = []
    if system.casefold() == "windows":
        try:
            completed = runner(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    _WINDOWS_DISCOVERY_SCRIPT,
                ],
                capture_output=True,
                text=True,
                timeout=max(0.1, float(timeout_s)),
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode == 0:
                interfaces = parse_windows_interface_json(completed.stdout)
                if interfaces:
                    return DiscoveryResult(interfaces, "windows", ())
            warnings.append(
                "Windows interface discovery failed "
                f"(exit code {completed.returncode}); using the safe fallback."
            )
        except subprocess.TimeoutExpired:
            warnings.append(
                f"Windows interface discovery exceeded {max(0.1, float(timeout_s)):.1f}s."
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            warnings.append(f"Windows interface discovery failed: {type(exc).__name__}")
    interfaces = fallback()
    if not interfaces:
        warnings.append("Fallback discovery found no IPv4 interfaces.")
    return DiscoveryResult(interfaces, "stdlib-fallback", tuple(warnings))


class HeaderErrorCode(str, Enum):
    TOO_SHORT = "too_short"
    UNSUPPORTED_FORMAT = "unsupported_format"
    MALFORMED = "malformed"


@dataclass(frozen=True, slots=True)
class F1PacketHeader:
    packet_format: int
    game_year: int
    game_major_version: int
    game_minor_version: int
    packet_version: int
    packet_id: int
    session_uid: int
    session_time: float
    frame_identifier: int
    overall_frame_identifier: int
    player_car_index: int
    secondary_player_car_index: int


@dataclass(frozen=True, slots=True)
class HeaderInspection:
    valid: bool
    header: F1PacketHeader | None = None
    error_code: HeaderErrorCode | None = None
    message: str = ""


class F1HeaderError(ValueError):
    def __init__(self, code: HeaderErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def inspect_2026_header(data: bytes | bytearray | memoryview) -> HeaderInspection:
    """Inspect only the packed 29-byte F1 header; never parse a packet body."""

    view = memoryview(data)
    if len(view) < F1_HEADER_SIZE:
        return HeaderInspection(
            False,
            error_code=HeaderErrorCode.TOO_SHORT,
            message=f"datagram is {len(view)} bytes; header requires {F1_HEADER_SIZE}",
        )
    try:
        values = _F1_HEADER.unpack_from(view, 0)
        header = F1PacketHeader(*values)
    except (struct.error, TypeError, ValueError) as exc:
        return HeaderInspection(
            False,
            error_code=HeaderErrorCode.MALFORMED,
            message=f"header could not be unpacked: {exc}",
        )
    if header.packet_format != F1_2026_PACKET_FORMAT:
        return HeaderInspection(
            False,
            header=header,
            error_code=HeaderErrorCode.UNSUPPORTED_FORMAT,
            message=(
                f"packet format {header.packet_format} is not supported; "
                f"expected {F1_2026_PACKET_FORMAT}"
            ),
        )
    return HeaderInspection(True, header=header)


def parse_2026_header(data: bytes | bytearray | memoryview) -> F1PacketHeader:
    inspection = inspect_2026_header(data)
    if not inspection.valid or inspection.header is None:
        raise F1HeaderError(
            inspection.error_code or HeaderErrorCode.MALFORMED,
            inspection.message or "invalid F1 packet header",
        )
    return inspection.header


@dataclass(frozen=True, slots=True, order=True)
class PacketHealthKey:
    session_uid: int
    packet_id: int
    source_ip: str
    source_port: int = 0


@dataclass(frozen=True, slots=True)
class PacketHealthSnapshot:
    key: PacketHealthKey
    received: int
    valid_parsed: int
    parse_errors: int
    provisional_gaps: int
    confirmed_lost: int
    duplicates: int
    out_of_order: int
    late_after_confirmation: int
    flashbacks: int
    timeline_epoch: int
    latest_frame_identifier: int | None
    latest_overall_frame_identifier: int | None
    observed_hz_1s: float
    observed_hz_10s: float
    observed_hz_session: float
    expected_hz: float | None
    last_age_ms: float | None
    inter_arrival_mean_ms: float | None
    inter_arrival_p95_ms: float | None
    inter_arrival_max_ms: float | None
    jitter_ms: float | None
    status: str
    last_parse_error_code: str | None


@dataclass(frozen=True, slots=True)
class InvalidDatagramSnapshot:
    source_ip: str
    source_port: int
    received: int
    too_short: int
    unsupported_format: int
    malformed: int
    last_error_code: str | None


@dataclass(frozen=True, slots=True)
class PacketHealthReport:
    packets: tuple[PacketHealthSnapshot, ...]
    invalid: tuple[InvalidDatagramSnapshot, ...]
    session_changes: int


@dataclass(slots=True)
class _InvalidDatagramStats:
    received: int = 0
    too_short: int = 0
    unsupported_format: int = 0
    malformed: int = 0
    last_error_code: str | None = None


@dataclass(slots=True)
class _PacketStats:
    received: int = 0
    valid_parsed: int = 0
    parse_errors: int = 0
    provisional: dict[int, float] = field(default_factory=dict)
    confirmed_recent: set[int] = field(default_factory=set)
    confirmed_order: deque[int] = field(default_factory=deque)
    confirmed_lost: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    late_after_confirmation: int = 0
    flashbacks: int = 0
    timeline_epoch: int = 0
    latest_frame: int | None = None
    latest_overall_frame: int | None = None
    first_monotonic: float | None = None
    last_monotonic: float | None = None
    first_wall: float | None = None
    last_wall: float | None = None
    recent_times: deque[float] = field(default_factory=deque)
    intervals: deque[float] = field(default_factory=deque)
    seen: set[int] = field(default_factory=set)
    seen_order: deque[int] = field(default_factory=deque)
    last_parse_error_code: str | None = None


def _source_parts(source: tuple[str, int] | str) -> tuple[str, int]:
    if isinstance(source, tuple):
        host, port = source
    else:
        host, port = source, 0
    parsed = ipaddress.ip_address(str(host))
    if not isinstance(parsed, ipaddress.IPv4Address):
        raise TypeError("packet source must be IPv4")
    return str(parsed), max(0, min(65535, int(port)))


def _forward_delta(previous: int, current: int) -> int | None:
    difference = (int(current) - int(previous)) & UINT32_MASK
    return difference if 0 < difference < UINT32_HALF_RANGE else None


def _percentile_95(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return float(ordered[index])


class PacketHealthTracker:
    """Bounded, per-packet-type UDP sequence and timing accounting.

    ``sequence_tracked_packet_ids`` can disable gap inference for event-driven
    packet types whose frame identifiers are not expected to be consecutive.
    ``None`` keeps the generic default of tracking every packet type.
    """

    def __init__(
        self,
        *,
        reorder_window_s: float = 0.20,
        flashback_threshold_frames: int = 20,
        recent_frame_capacity: int = 4096,
        interval_capacity: int = 4096,
        max_tracked_gap: int = 4096,
        expected_hz: Mapping[int, float] | None = None,
        freshness_ms: Mapping[int, int] | None = None,
        default_freshness_ms: int = 1_500,
        sequence_tracked_packet_ids: Collection[int] | None = None,
    ) -> None:
        self.reorder_window_s = max(0.0, float(reorder_window_s))
        self.flashback_threshold_frames = max(2, int(flashback_threshold_frames))
        self.recent_frame_capacity = max(32, int(recent_frame_capacity))
        self.interval_capacity = max(32, int(interval_capacity))
        self.max_tracked_gap = max(32, int(max_tracked_gap))
        self.expected_hz = {
            int(key): float(value) for key, value in (expected_hz or {}).items()
        }
        self.freshness_ms = {
            int(key): int(value) for key, value in (freshness_ms or {}).items()
        }
        self.default_freshness_ms = max(1, int(default_freshness_ms))
        self.sequence_tracked_packet_ids = (
            None
            if sequence_tracked_packet_ids is None
            else {int(value) for value in sequence_tracked_packet_ids}
        )
        self._stats: dict[PacketHealthKey, _PacketStats] = {}
        self._invalid: dict[tuple[str, int], _InvalidDatagramStats] = {}
        self._active_session_by_source: dict[tuple[str, int], int] = {}
        self._session_changes = 0
        self._lock = threading.RLock()

    def observe_datagram(
        self,
        data: bytes | bytearray | memoryview,
        source: tuple[str, int] | str,
        *,
        received_monotonic: float | None = None,
        received_wall: float | None = None,
        parsed: bool | None = True,
        parse_error_code: str | None = None,
    ) -> HeaderInspection:
        inspection = inspect_2026_header(data)
        if not inspection.valid or inspection.header is None:
            self.record_invalid(
                source, inspection.error_code or HeaderErrorCode.MALFORMED
            )
            return inspection
        self.observe_header(
            inspection.header,
            source,
            received_monotonic=received_monotonic,
            received_wall=received_wall,
            parsed=parsed,
            parse_error_code=parse_error_code,
        )
        return inspection

    def record_invalid(
        self,
        source: tuple[str, int] | str,
        error_code: HeaderErrorCode | str,
    ) -> None:
        source_key = _source_parts(source)
        code = str(error_code.value if isinstance(error_code, Enum) else error_code)
        with self._lock:
            stats = self._invalid.setdefault(source_key, _InvalidDatagramStats())
            stats.received += 1
            if code == HeaderErrorCode.TOO_SHORT.value:
                stats.too_short += 1
            elif code == HeaderErrorCode.UNSUPPORTED_FORMAT.value:
                stats.unsupported_format += 1
            else:
                stats.malformed += 1
            stats.last_error_code = code

    def observe_header(
        self,
        header: F1PacketHeader,
        source: tuple[str, int] | str,
        *,
        received_monotonic: float | None = None,
        received_wall: float | None = None,
        parsed: bool | None = True,
        parse_error_code: str | None = None,
    ) -> PacketHealthKey:
        source_ip, source_port = _source_parts(source)
        key = PacketHealthKey(
            int(header.session_uid), int(header.packet_id), source_ip, source_port
        )
        now = (
            time.monotonic()
            if received_monotonic is None
            else float(received_monotonic)
        )
        wall = time.time() if received_wall is None else float(received_wall)
        with self._lock:
            source_key = (source_ip, source_port)
            previous_session = self._active_session_by_source.get(source_key)
            if previous_session is not None and previous_session != key.session_uid:
                self._session_changes += 1
            self._active_session_by_source[source_key] = key.session_uid

            stats = self._stats.setdefault(key, _PacketStats())
            self._expire_gaps(stats, now)
            stats.received += 1
            if parsed is True:
                stats.valid_parsed += 1
            elif parsed is False:
                stats.parse_errors += 1
                stats.last_parse_error_code = parse_error_code or "parse_error"
            self._record_arrival(stats, now, wall)

            frame = int(header.frame_identifier) & UINT32_MASK
            overall = int(header.overall_frame_identifier) & UINT32_MASK
            if self._is_flashback(stats, overall):
                stats.flashbacks += 1
                stats.timeline_epoch += 1
                stats.provisional.clear()
                stats.seen.clear()
                stats.seen_order.clear()
                stats.latest_frame = frame
                stats.latest_overall_frame = overall
                self._remember_seen(stats, frame)
                return key

            if (
                stats.latest_overall_frame is None
                or _forward_delta(stats.latest_overall_frame, overall) is not None
            ):
                stats.latest_overall_frame = overall

            if stats.latest_frame is None:
                stats.latest_frame = frame
                self._remember_seen(stats, frame)
                return key
            if frame in stats.seen:
                stats.duplicates += 1
                return key

            delta = _forward_delta(stats.latest_frame, frame)
            if delta is not None:
                if delta > 1 and (
                    self.sequence_tracked_packet_ids is None
                    or key.packet_id in self.sequence_tracked_packet_ids
                ):
                    self._add_gap(stats, stats.latest_frame, delta, now)
                stats.latest_frame = frame
                self._remember_seen(stats, frame)
                return key

            if frame in stats.provisional:
                stats.provisional.pop(frame, None)
                stats.out_of_order += 1
            elif frame in stats.confirmed_recent:
                stats.confirmed_recent.discard(frame)
                stats.confirmed_lost = max(0, stats.confirmed_lost - 1)
                stats.out_of_order += 1
                stats.late_after_confirmation += 1
            else:
                stats.out_of_order += 1
            self._remember_seen(stats, frame)
        return key

    def mark_parse_result(
        self,
        key: PacketHealthKey,
        *,
        valid: bool,
        error_code: str | None = None,
    ) -> bool:
        """Record a full-parser result when header observation happened first."""

        with self._lock:
            stats = self._stats.get(key)
            if stats is None:
                return False
            if valid:
                stats.valid_parsed += 1
            else:
                stats.parse_errors += 1
                stats.last_parse_error_code = error_code or "parse_error"
            return True

    def _record_arrival(self, stats: _PacketStats, now: float, wall: float) -> None:
        if stats.first_monotonic is None:
            stats.first_monotonic = now
            stats.first_wall = wall
        if stats.last_monotonic is not None:
            interval = max(0.0, now - stats.last_monotonic)
            stats.intervals.append(interval)
            while len(stats.intervals) > self.interval_capacity:
                stats.intervals.popleft()
        stats.last_monotonic = now
        stats.last_wall = wall
        stats.recent_times.append(now)
        cutoff = now - 10.0
        while stats.recent_times and stats.recent_times[0] < cutoff:
            stats.recent_times.popleft()

    def _is_flashback(self, stats: _PacketStats, overall: int) -> bool:
        previous = stats.latest_overall_frame
        if previous is None or previous == overall:
            return False
        if _forward_delta(previous, overall) is not None:
            return False
        backward = (previous - overall) & UINT32_MASK
        return backward >= self.flashback_threshold_frames

    def _remember_seen(self, stats: _PacketStats, frame: int) -> None:
        stats.seen.add(frame)
        stats.seen_order.append(frame)
        while len(stats.seen_order) > self.recent_frame_capacity:
            expired = stats.seen_order.popleft()
            if expired not in stats.seen_order:
                stats.seen.discard(expired)

    def _add_confirmed_recent(self, stats: _PacketStats, frame: int) -> None:
        stats.confirmed_recent.add(frame)
        stats.confirmed_order.append(frame)
        while len(stats.confirmed_order) > self.recent_frame_capacity:
            expired = stats.confirmed_order.popleft()
            if expired not in stats.confirmed_order:
                stats.confirmed_recent.discard(expired)

    def _add_gap(
        self, stats: _PacketStats, previous: int, delta: int, now: float
    ) -> None:
        missing_count = delta - 1
        untracked = max(0, missing_count - self.max_tracked_gap)
        stats.confirmed_lost += untracked
        start = 1 + untracked
        deadline = now + self.reorder_window_s
        for offset in range(start, delta):
            frame = (previous + offset) & UINT32_MASK
            if frame not in stats.seen:
                stats.provisional.setdefault(frame, deadline)

    def _expire_gaps(self, stats: _PacketStats, now: float) -> None:
        expired = [
            frame for frame, deadline in stats.provisional.items() if deadline <= now
        ]
        for frame in expired:
            stats.provisional.pop(frame, None)
            stats.confirmed_lost += 1
            self._add_confirmed_recent(stats, frame)

    @staticmethod
    def _rate(times: Sequence[float], cutoff: float | None = None) -> float:
        selected = [value for value in times if cutoff is None or value >= cutoff]
        if len(selected) < 2:
            return 0.0
        span = selected[-1] - selected[0]
        return 0.0 if span <= 0 else (len(selected) - 1) / span

    def snapshot(
        self,
        key: PacketHealthKey,
        *,
        now_monotonic: float | None = None,
    ) -> PacketHealthSnapshot | None:
        now = time.monotonic() if now_monotonic is None else float(now_monotonic)
        with self._lock:
            stats = self._stats.get(key)
            if stats is None:
                return None
            self._expire_gaps(stats, now)
            times = list(stats.recent_times)
            intervals = list(stats.intervals)
            age_ms = (
                None
                if stats.last_monotonic is None
                else max(0.0, (now - stats.last_monotonic) * 1_000.0)
            )
            session_rate = 0.0
            if (
                stats.first_monotonic is not None
                and stats.last_monotonic is not None
                and stats.received >= 2
                and stats.last_monotonic > stats.first_monotonic
            ):
                session_rate = (stats.received - 1) / (
                    stats.last_monotonic - stats.first_monotonic
                )
            freshness = self.freshness_ms.get(key.packet_id, self.default_freshness_ms)
            if age_ms is not None and age_ms > freshness:
                status = "stale"
            elif stats.parse_errors or stats.confirmed_lost:
                status = "degraded"
            elif stats.provisional:
                status = "provisional_gap"
            else:
                status = "healthy"
            mean = statistics.fmean(intervals) if intervals else None
            jitter = (
                statistics.pstdev(intervals)
                if len(intervals) >= 2
                else 0.0
                if intervals
                else None
            )
            p95 = _percentile_95(intervals)
            maximum = max(intervals) if intervals else None
            return PacketHealthSnapshot(
                key=key,
                received=stats.received,
                valid_parsed=stats.valid_parsed,
                parse_errors=stats.parse_errors,
                provisional_gaps=len(stats.provisional),
                confirmed_lost=stats.confirmed_lost,
                duplicates=stats.duplicates,
                out_of_order=stats.out_of_order,
                late_after_confirmation=stats.late_after_confirmation,
                flashbacks=stats.flashbacks,
                timeline_epoch=stats.timeline_epoch,
                latest_frame_identifier=stats.latest_frame,
                latest_overall_frame_identifier=stats.latest_overall_frame,
                observed_hz_1s=round(self._rate(times, now - 1.0), 3),
                observed_hz_10s=round(self._rate(times, now - 10.0), 3),
                observed_hz_session=round(session_rate, 3),
                expected_hz=self.expected_hz.get(key.packet_id),
                last_age_ms=None if age_ms is None else round(age_ms, 3),
                inter_arrival_mean_ms=None if mean is None else round(mean * 1_000, 3),
                inter_arrival_p95_ms=None if p95 is None else round(p95 * 1_000, 3),
                inter_arrival_max_ms=None
                if maximum is None
                else round(maximum * 1_000, 3),
                jitter_ms=None if jitter is None else round(jitter * 1_000, 3),
                status=status,
                last_parse_error_code=stats.last_parse_error_code,
            )

    def report(self, *, now_monotonic: float | None = None) -> PacketHealthReport:
        now = time.monotonic() if now_monotonic is None else float(now_monotonic)
        with self._lock:
            keys = sorted(self._stats)
            packets = tuple(
                snapshot
                for key in keys
                if (snapshot := self.snapshot(key, now_monotonic=now)) is not None
            )
            invalid = tuple(
                InvalidDatagramSnapshot(
                    source_ip=source[0],
                    source_port=source[1],
                    received=stats.received,
                    too_short=stats.too_short,
                    unsupported_format=stats.unsupported_format,
                    malformed=stats.malformed,
                    last_error_code=stats.last_error_code,
                )
                for source, stats in sorted(self._invalid.items())
            )
            return PacketHealthReport(packets, invalid, self._session_changes)


def redact_ipv4(address: str) -> str:
    parsed = ipaddress.ip_address(address)
    if not isinstance(parsed, ipaddress.IPv4Address):
        return "[non-ipv4]"
    if parsed.is_loopback:
        return "127.0.0.x"
    octets = str(parsed).split(".")
    if classify_ipv4(parsed).scope in {
        AddressScope.PRIVATE,
        AddressScope.LINK_LOCAL,
    }:
        return ".".join([*octets[:3], "x"])
    return "x.x.x.x"


def _redacted_adapter_id(adapter_id: str) -> str:
    digest = hashlib.sha256(adapter_id.encode("utf-8", "replace")).hexdigest()[:10]
    return f"adapter-{digest}"


_IPV4_IN_TEXT = re.compile(
    r"(?<![0-9.])(?:25[0-5]|2[0-4]\d|1?\d?\d)"
    r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![0-9.])"
)


def _redact_diagnostic_text(
    value: str,
    interfaces: Sequence[IPv4Interface],
) -> str:
    """Remove known interface identifiers and full IPv4s from free-form text."""

    redacted = str(value)
    for interface in interfaces:
        replacements = (
            (interface.adapter_id, _redacted_adapter_id(interface.adapter_id)),
            (interface.name, "[adapter-name]"),
            (interface.description, "[adapter-description]"),
        )
        for original, replacement in replacements:
            if original:
                redacted = redacted.replace(original, replacement)
    return _IPV4_IN_TEXT.sub(lambda match: redact_ipv4(match.group(0)), redacted)


def build_redacted_diagnostics(
    *,
    discovery: DiscoveryResult | None = None,
    recommendation: InterfaceRecommendation | None = None,
    packet_health: PacketHealthReport | None = None,
    warnings: Iterable[str] = (),
) -> dict[str, Any]:
    """Build a shareable report without adapter IDs, names, paths, or full IPs."""

    interfaces = discovery.interfaces if discovery else ()
    recommended_id = (
        recommendation.recommended.adapter_id
        if recommendation and recommendation.recommended
        else None
    )
    interface_rows = [
        {
            "adapter_id": _redacted_adapter_id(item.adapter_id),
            "kind": item.kind.value,
            "address": redact_ipv4(item.address),
            "prefix_length": item.prefix_length,
            "is_up": item.is_up,
            "has_default_gateway": item.has_default_gateway,
            "metric": item.metric,
            "recommended": item.adapter_id == recommended_id,
        }
        for item in interfaces
    ]
    packet_rows: list[dict[str, Any]] = []
    invalid_rows: list[dict[str, Any]] = []
    if packet_health:
        for packet in packet_health.packets:
            row = asdict(packet)
            row["key"]["source_ip"] = redact_ipv4(packet.key.source_ip)
            packet_rows.append(row)
        for invalid in packet_health.invalid:
            row = asdict(invalid)
            row["source_ip"] = redact_ipv4(invalid.source_ip)
            invalid_rows.append(row)
    combined_warnings = [*warnings]
    if discovery:
        combined_warnings.extend(discovery.warnings)
    if recommendation:
        combined_warnings.extend(recommendation.warnings)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "platform": platform.system(),
        "discovery_source": discovery.source if discovery else None,
        "recommendation_confidence": recommendation.confidence
        if recommendation
        else None,
        "interfaces": interface_rows,
        "packet_health": packet_rows,
        "invalid_datagrams": invalid_rows,
        "session_changes": packet_health.session_changes if packet_health else 0,
        "warnings": [
            _redact_diagnostic_text(item, interfaces) for item in combined_warnings
        ],
    }
