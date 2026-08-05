from __future__ import annotations

import json
import struct
import subprocess

import pytest

from pitwall.networking import (
    AdapterKind,
    AddressScope,
    DiscoveryResult,
    F1PacketHeader,
    HeaderErrorCode,
    IPv4Interface,
    PacketHealthTracker,
    build_redacted_diagnostics,
    classify_ipv4,
    discover_ipv4_interfaces,
    inspect_2026_header,
    parse_2026_header,
    parse_windows_interface_json,
    recommend_ipv4_interface,
)


def packet_bytes(
    *,
    packet_format: int = 2026,
    packet_id: int = 6,
    session_uid: int = 42,
    frame: int = 100,
    overall: int | None = None,
    body: bytes = b"payload",
) -> bytes:
    header = struct.pack(
        "<HBBBBBQfIIBB",
        packet_format,
        25,
        1,
        0,
        1,
        packet_id,
        session_uid,
        12.5,
        frame,
        frame if overall is None else overall,
        3,
        255,
    )
    return header + body


def header(
    frame: int,
    *,
    overall: int | None = None,
    session_uid: int = 42,
    packet_id: int = 6,
) -> F1PacketHeader:
    return parse_2026_header(
        packet_bytes(
            frame=frame,
            overall=overall,
            session_uid=session_uid,
            packet_id=packet_id,
        )
    )


@pytest.mark.parametrize(
    ("address", "scope", "candidate"),
    [
        ("192.168.1.42", AddressScope.PRIVATE, True),
        ("10.2.3.4", AddressScope.PRIVATE, True),
        ("172.31.1.2", AddressScope.PRIVATE, True),
        ("127.0.0.1", AddressScope.LOOPBACK, False),
        ("0.0.0.0", AddressScope.UNSPECIFIED, False),
        ("169.254.9.8", AddressScope.LINK_LOCAL, False),
        ("224.0.0.1", AddressScope.MULTICAST, False),
        ("255.255.255.255", AddressScope.BROADCAST, False),
        ("8.8.8.8", AddressScope.PUBLIC, False),
        ("198.51.100.10", AddressScope.RESERVED, False),
    ],
)
def test_ipv4_classification_is_operational(
    address: str, scope: AddressScope, candidate: bool
) -> None:
    result = classify_ipv4(address)
    assert result.scope is scope
    assert result.console_candidate is candidate


def test_ranking_prefers_prior_working_adapter_and_honours_pin() -> None:
    ethernet = IPv4Interface(
        "eth-guid",
        "Ethernet",
        "192.168.1.42",
        is_up=True,
        has_default_gateway=True,
        metric=20,
        kind=AdapterKind.ETHERNET,
    )
    wifi = IPv4Interface(
        "wifi-guid",
        "Wi-Fi",
        "192.168.1.77",
        is_up=True,
        has_default_gateway=True,
        metric=10,
        kind=AdapterKind.WIFI,
    )
    vpn = IPv4Interface(
        "vpn-guid",
        "Work VPN",
        "10.20.0.5",
        is_up=True,
        has_default_gateway=True,
        metric=1,
        kind=AdapterKind.VPN,
    )

    prior = recommend_ipv4_interface(
        [wifi, vpn, ethernet], prior_working_adapter_ids={"eth-guid"}
    )
    assert prior.recommended == ethernet
    assert prior.confidence == "high"
    assert "valid F1 traffic observed previously" in prior.ranked[0].reasons

    pinned = recommend_ipv4_interface(
        [ethernet, wifi],
        pinned_adapter_id="wifi-guid",
        pinned_address="192.168.1.10",
        prior_working_adapter_ids={"eth-guid"},
    )
    assert pinned.recommended == wifi
    assert any("changed from 192.168.1.10" in item for item in pinned.warnings)


def test_pinned_adapter_prefers_its_exact_prior_address_when_still_present() -> None:
    old = IPv4Interface("wifi-guid", "Wi-Fi", "192.168.1.10", kind=AdapterKind.WIFI)
    secondary = IPv4Interface(
        "wifi-guid", "Wi-Fi", "192.168.1.99", kind=AdapterKind.WIFI
    )

    result = recommend_ipv4_interface(
        [secondary, old],
        pinned_adapter_id="wifi-guid",
        pinned_address="192.168.1.10",
    )

    assert result.recommended == old
    assert not result.warnings


def test_missing_pin_does_not_silently_select_another_adapter() -> None:
    available = IPv4Interface(
        "wifi-guid",
        "Wi-Fi",
        "192.168.0.20",
        has_default_gateway=True,
        kind=AdapterKind.WIFI,
    )
    result = recommend_ipv4_interface([available], pinned_adapter_id="missing-guid")
    assert result.recommended is None
    assert result.confidence == "none"
    assert "no replacement was selected" in result.warnings[0]


def test_windows_json_discovery_is_structured() -> None:
    payload = json.dumps(
        [
            {
                "adapter_id": "7",
                "name": "Wi-Fi",
                "description": "Intel Wireless Adapter",
                "status": "Up",
                "metric": 25,
                "has_default_gateway": True,
                "addresses": [{"address": "192.168.50.20", "prefix_length": 24}],
            },
            {
                "adapter_id": "8",
                "name": "Broken",
                "status": "Down",
                "addresses": [{"address": "not-an-ip", "prefix_length": 24}],
            },
        ]
    )
    interfaces = parse_windows_interface_json(payload)
    assert len(interfaces) == 1
    assert interfaces[0].adapter_id == "7"
    assert interfaces[0].kind is AdapterKind.WIFI
    assert interfaces[0].has_default_gateway is True


def test_windows_discovery_timeout_uses_safe_fallback() -> None:
    fallback_interface = IPv4Interface(
        "fallback", "Fallback", "192.168.2.4", kind=AdapterKind.UNKNOWN
    )

    def timed_out(command, **kwargs):  # type: ignore[no-untyped-def]
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    result = discover_ipv4_interfaces(
        timeout_s=0.1,
        platform_name="Windows",
        runner=timed_out,
        fallback=lambda: (fallback_interface,),
    )
    assert result.source == "stdlib-fallback"
    assert result.interfaces == (fallback_interface,)
    assert "exceeded 0.1s" in result.warnings[0]


def test_minimal_header_inspection_is_exactly_29_bytes() -> None:
    data = packet_bytes(packet_id=16, session_uid=99, frame=123, overall=120)
    inspection = inspect_2026_header(data)
    assert inspection.valid is True
    assert inspection.header is not None
    assert inspection.header.packet_id == 16
    assert inspection.header.session_uid == 99
    assert inspection.header.frame_identifier == 123
    assert inspection.header.overall_frame_identifier == 120

    short = inspect_2026_header(data[:28])
    assert short.valid is False
    assert short.error_code is HeaderErrorCode.TOO_SHORT
    wrong_format = inspect_2026_header(packet_bytes(packet_format=2025))
    assert wrong_format.valid is False
    assert wrong_format.header is not None
    assert wrong_format.error_code is HeaderErrorCode.UNSUPPORTED_FORMAT


def test_packet_health_reorder_closes_provisional_gap() -> None:
    tracker = PacketHealthTracker(reorder_window_s=0.2)
    source = ("192.168.1.61", 54022)
    key = tracker.observe_header(header(100), source, received_monotonic=1.0)
    tracker.observe_header(header(101), source, received_monotonic=1.01)
    tracker.observe_header(header(103), source, received_monotonic=1.03)
    provisional = tracker.snapshot(key, now_monotonic=1.04)
    assert provisional is not None
    assert provisional.provisional_gaps == 1
    assert provisional.confirmed_lost == 0

    tracker.observe_header(header(102), source, received_monotonic=1.05)
    repaired = tracker.snapshot(key, now_monotonic=1.06)
    assert repaired is not None
    assert repaired.provisional_gaps == 0
    assert repaired.confirmed_lost == 0
    assert repaired.out_of_order == 1


def test_packet_health_confirms_gap_then_corrects_very_late_arrival() -> None:
    tracker = PacketHealthTracker(reorder_window_s=0.1)
    source = ("192.168.1.61", 54022)
    key = tracker.observe_header(header(10), source, received_monotonic=2.0)
    tracker.observe_header(header(12), source, received_monotonic=2.01)
    confirmed = tracker.snapshot(key, now_monotonic=2.2)
    assert confirmed is not None
    assert confirmed.confirmed_lost == 1
    assert confirmed.provisional_gaps == 0

    tracker.observe_header(header(11), source, received_monotonic=2.21)
    repaired = tracker.snapshot(key, now_monotonic=2.22)
    assert repaired is not None
    assert repaired.confirmed_lost == 0
    assert repaired.late_after_confirmation == 1
    assert repaired.out_of_order == 1


def test_packet_health_handles_duplicates_wraparound_flashback_and_new_session() -> (
    None
):
    tracker = PacketHealthTracker(reorder_window_s=0.05, flashback_threshold_frames=20)
    source = ("10.0.0.50", 60000)
    key = tracker.observe_header(
        header(0xFFFFFFFF, overall=0xFFFFFFFF),
        source,
        received_monotonic=3.0,
    )
    tracker.observe_header(header(0, overall=0), source, received_monotonic=3.01)
    tracker.observe_header(header(0, overall=0), source, received_monotonic=3.02)
    wrapped = tracker.snapshot(key, now_monotonic=3.03)
    assert wrapped is not None
    assert wrapped.duplicates == 1
    assert wrapped.out_of_order == 0
    assert wrapped.confirmed_lost == 0

    tracker.observe_header(header(500, overall=500), source, received_monotonic=3.04)
    tracker.observe_header(header(400, overall=400), source, received_monotonic=3.05)
    flashed = tracker.snapshot(key, now_monotonic=3.06)
    assert flashed is not None
    assert flashed.flashbacks == 1
    assert flashed.timeline_epoch == 1
    assert flashed.latest_frame_identifier == 400

    new_key = tracker.observe_header(
        header(1, overall=1, session_uid=43),
        source,
        received_monotonic=3.1,
    )
    report = tracker.report(now_monotonic=3.11)
    assert new_key != key
    assert report.session_changes == 1
    new_session = tracker.snapshot(new_key, now_monotonic=3.11)
    assert new_session is not None
    assert new_session.confirmed_lost == 0


def test_packet_health_rates_freshness_jitter_and_invalid_counts() -> None:
    tracker = PacketHealthTracker(freshness_ms={6: 100}, expected_hz={6: 10.0})
    source = ("192.168.3.2", 20777)
    key = tracker.observe_header(header(1), source, received_monotonic=10.0)
    tracker.observe_header(header(2), source, received_monotonic=10.1)
    tracker.observe_header(header(3), source, received_monotonic=10.2)
    fresh = tracker.snapshot(key, now_monotonic=10.25)
    assert fresh is not None
    assert fresh.observed_hz_1s == pytest.approx(10.0)
    assert fresh.observed_hz_10s == pytest.approx(10.0)
    assert fresh.observed_hz_session == pytest.approx(10.0)
    assert fresh.expected_hz == 10.0
    assert fresh.inter_arrival_mean_ms == pytest.approx(100.0)
    assert fresh.inter_arrival_p95_ms == pytest.approx(100.0)
    assert fresh.jitter_ms == pytest.approx(0.0)
    assert fresh.status == "healthy"
    stale = tracker.snapshot(key, now_monotonic=10.4)
    assert stale is not None and stale.status == "stale"

    tracker.observe_datagram(b"short", source, received_monotonic=10.5)
    tracker.observe_datagram(
        packet_bytes(packet_format=2025), source, received_monotonic=10.6
    )
    report = tracker.report(now_monotonic=10.6)
    assert report.invalid[0].received == 2
    assert report.invalid[0].too_short == 1
    assert report.invalid[0].unsupported_format == 1


def test_redacted_diagnostics_do_not_expose_adapter_or_full_ip() -> None:
    interface = IPv4Interface(
        "private-guid-value",
        "Sarth Personal Wi-Fi",
        "192.168.88.42",
        kind=AdapterKind.WIFI,
    )
    discovery = DiscoveryResult((interface,), "windows")
    recommendation = recommend_ipv4_interface(
        [interface], pinned_adapter_id=interface.adapter_id
    )
    tracker = PacketHealthTracker()
    tracker.observe_header(header(1), ("192.168.88.61", 54022), received_monotonic=1.0)
    report = build_redacted_diagnostics(
        discovery=discovery,
        recommendation=recommendation,
        packet_health=tracker.report(now_monotonic=1.1),
        warnings=(
            (
                "Adapter private-guid-value Sarth Personal Wi-Fi changed "
                "from 192.168.88.42 to 192.168.88.99"
            ),
        ),
    )
    encoded = json.dumps(report)
    assert "private-guid-value" not in encoded
    assert "Sarth Personal" not in encoded
    assert "192.168.88.42" not in encoded
    assert "192.168.88.61" not in encoded
    assert "192.168.88.99" not in encoded
    assert "192.168.88.x" in encoded
