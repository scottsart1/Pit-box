"""Bounded, versioned live-state WebSocket projections.

The legacy ``/ws`` endpoint intentionally remains outside this module.  This
router exposes a subscription-first protocol which sends only browser-ready
projections and coalesces fast state changes into the latest value at the
requested rate.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import time
from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from ..api_models import LiveSubscription
from ..network_service import NetworkService
from ..state import StateStore

_TOPICS = frozenset(
    {
        "session",
        "player",
        "classification",
        "flags",
        "network",
        "strategy",
        "engineer",
    }
)
_MAX_DRIVERS = 24
_MAX_MARSHAL_ZONES = 32
_MAX_RADIO_ROWS = 20


def _finite_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def _bounded_value(
    value: Any,
    *,
    depth: int = 0,
    max_depth: int = 4,
    max_items: int = 40,
) -> Any:
    """Return a JSON-safe value with deterministic depth and collection caps."""

    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:2_000]
    if depth >= max_depth:
        return None
    if isinstance(value, Mapping):
        return {
            str(key)[:120]: _bounded_value(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
            )
            for key, item in list(value.items())[:max_items]
        }
    if isinstance(value, (list, tuple)):
        return [
            _bounded_value(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
            )
            for item in value[:max_items]
        ]
    return str(value)[:500]


def _select(source: Mapping[str, Any], names: Iterable[str]) -> dict[str, Any]:
    return {name: _bounded_value(source.get(name)) for name in names}


def _session_projection(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return _select(
        snapshot,
        (
            "connected",
            "telemetry_stale",
            "packet_format",
            "game_year",
            "session_uid",
            "restart_epoch",
            "timeline_epoch",
            "source_mode",
            "state_revision",
            "session_type",
            "mode_profile",
            "raw_session_type_id",
            "session_length_label",
            "track_id",
            "track_name",
            "track_length_m",
            "session_time_left_s",
            "session_duration_s",
            "total_laps",
            "current_lap",
            "current_lap_time_ms",
            "current_lap_invalid",
            "sector",
            "weather",
            "rain_next_15_pct",
            "rain_next_30_pct",
            "rain_next_60_pct",
            "track_temp_c",
            "air_temp_c",
            "game_paused",
            "active_cars",
        ),
    )


def _player_projection(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    projected = _select(
        snapshot,
        (
            "player_car_index",
            "player_position",
            "grid_position",
            "speed_kph",
            "gear",
            "throttle",
            "brake",
            "steer",
            "lateral_g",
            "longitudinal_g",
            "world_x",
            "world_y",
            "world_z",
            "forward_x",
            "forward_z",
            "lap_distance_m",
            "live_delta_s",
            "live_delta_reference",
            "fuel_kg",
            "fuel_laps_delta",
            "fuel_remaining_laps",
            "ers_pct",
            "ers_mode",
            "drs_allowed",
            "active_aero_available",
            "active_aero_mode",
            "overtake_available",
            "overtake_active",
            "pit_status",
            "pit_lane_time_ms",
            "penalties_s",
            "warnings",
            "fia_flag",
            "tyre",
            "damage",
            "component_wear",
            "availability",
            "packet_group_freshness",
        ),
    )
    projected["wheel_slip_ratio"] = [
        _finite_number(item) for item in list(snapshot.get("wheel_slip_ratio") or [])[:4]
    ]
    projected["wheel_slip_angle"] = [
        _finite_number(item) for item in list(snapshot.get("wheel_slip_angle") or [])[:4]
    ]
    return projected


_DRIVER_FIELDS = (
    "car_idx",
    "name",
    "team",
    "team_id",
    "race_number",
    "ai_controlled",
    "is_teammate",
    "is_player",
    "active",
    "position",
    "grid_position",
    "current_lap",
    "gap_to_player_s",
    "delta_to_front_s",
    "delta_to_leader_s",
    "last_lap_ms",
    "best_lap_ms",
    "tyre_compound",
    "tyre_age",
    "pit_stops",
    "pit_status",
    "status",
    "result_status",
    "result_label",
    "fuel_kg",
    "fuel_remaining_laps",
    "ers_pct",
    "drs_allowed",
    "fia_flag",
    "speed_kph",
    "drs_open",
    "world_x",
    "world_z",
    "lap_distance_m",
    "penalties_s",
    "restricted",
)


def _classification_projection(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    drivers: list[dict[str, Any]] = []
    source = snapshot.get("drivers")
    if isinstance(source, list):
        for raw in source[:_MAX_DRIVERS]:
            if isinstance(raw, Mapping):
                drivers.append(_select(raw, _DRIVER_FIELDS))
    return {
        "field_count": len(drivers),
        "fresh_count": sum(
            1
            for driver in drivers
            if driver.get("active") and not driver.get("restricted")
        ),
        "drivers": drivers,
    }


def _flags_projection(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    projected = _select(
        snapshot,
        (
            "fia_flag",
            "safety_car",
            "safety_car_delta_s",
            "safety_car_delta_valid",
            "safety_car_periods",
            "virtual_safety_car_periods",
            "red_flag_active",
            "red_flag_count",
            "race_control_phase",
            "race_control_changed_at",
            "pit_speed_limit_kph",
        ),
    )
    zones = snapshot.get("marshal_zones")
    projected["marshal_zones"] = (
        _bounded_value(zones[:_MAX_MARSHAL_ZONES])
        if isinstance(zones, list)
        else []
    )
    grid = snapshot.get("restart_grid")
    projected["restart_grid"] = (
        _bounded_value(grid[:_MAX_DRIVERS]) if isinstance(grid, list) else []
    )
    return projected


def _strategy_projection(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    strategy = snapshot.get("strategy")
    if not isinstance(strategy, Mapping):
        strategy = {}
    result = {
        "current": _bounded_value(strategy, max_depth=5, max_items=30),
        "override": _bounded_value(snapshot.get("strategy_override")),
        "hold": _bounded_value(snapshot.get("strategy_hold")),
        "risk_appetite": _bounded_value(snapshot.get("strategy_risk_appetite")),
    }
    plans = result["current"].get("plans") if isinstance(result["current"], dict) else None
    if isinstance(plans, list):
        result["current"]["plans"] = plans[:3]
    return result


def _engineer_projection(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    projected = _select(
        snapshot,
        (
            "engineer_status",
            "radio_indicator",
            "radio_source",
            "radio_queue_depth",
            "radio_last_transcript",
            "radio_latency",
            "wake_enabled",
            "wake_status",
            "wake_armed",
            "wake_phrase",
            "ptt_pressed",
            "ptt_status",
            "proactive",
        ),
    )
    rows = snapshot.get("radio_log")
    projected["radio_log"] = (
        _bounded_value(rows[-_MAX_RADIO_ROWS:]) if isinstance(rows, list) else []
    )
    # Error details can contain provider responses or environment values.  The
    # live client only needs to know that a diagnostic is available.
    projected["has_error"] = bool(snapshot.get("last_error"))
    return projected


async def _network_projection(service: NetworkService | None) -> dict[str, Any]:
    if service is None:
        return {"available": False, "listener": {"state": "unavailable"}}
    try:
        snapshot = await service.snapshot()
    except (OSError, RuntimeError, ValueError):
        # Do not stream exception text: OS/network errors may contain local
        # paths, adapter identifiers, or other diagnostic-only details.
        return {"available": False, "listener": {"state": "error"}}

    packets = []
    for item in snapshot.packet_health.packets[:32]:
        packets.append(
            {
                "packet_id": item.key.packet_id,
                "status": item.status,
                "last_age_ms": _finite_number(item.last_age_ms),
                "observed_hz_10s": _finite_number(item.observed_hz_10s),
                "received": item.received,
                "confirmed_lost": item.confirmed_lost,
                "provisional_gap": item.provisional_gaps,
                "out_of_order": item.out_of_order,
                "duplicates": item.duplicates,
            }
        )
    return {
        "available": True,
        "listener": {
            "state": snapshot.listener.state.value,
            "bind_host": snapshot.listener.bind_host,
            "port": snapshot.listener.port,
            "last_valid_packet_age_ms": snapshot.listener.last_valid_packet_age_ms,
            "has_error": bool(snapshot.listener.error),
        },
        "source": _bounded_value(snapshot.source),
        "game": _bounded_value(snapshot.game),
        "packets": packets,
        "queues": {
            str(name): {
                "depth": item.depth,
                "capacity": item.capacity,
                "high_water": item.high_water,
                "drops": item.drops,
                "last_drain_age_ms": item.last_drain_age_ms,
            }
            for name, item in list(snapshot.queues.items())[:8]
        },
        "forwarders": [
            {
                "id": item.target.id,
                "label": item.target.label,
                "enabled": item.target.enabled,
                "packets_sent": item.counters.sent_packets,
                "bytes_sent": item.counters.sent_bytes,
                "queue_drops": item.counters.queue_drops,
                "socket_errors": item.counters.socket_errors,
            }
            for item in snapshot.forwarders[:20]
        ],
        "warnings": [str(item)[:500] for item in snapshot.warnings[:20]],
    }


def _project_topics(
    snapshot: Mapping[str, Any],
    topics: Iterable[str],
    *,
    network: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    projectors = {
        "session": _session_projection,
        "player": _player_projection,
        "classification": _classification_projection,
        "flags": _flags_projection,
        "strategy": _strategy_projection,
        "engineer": _engineer_projection,
    }
    result: dict[str, Any] = {}
    for topic in topics:
        if topic == "network":
            result[topic] = dict(network or {})
        elif topic in projectors:
            result[topic] = projectors[topic](snapshot)
    return result


def _classification_delta(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, Any]:
    old_rows = {
        int(row.get("car_idx", -1)): row
        for row in previous.get("drivers", [])
        if isinstance(row, Mapping)
    }
    new_rows = {
        int(row.get("car_idx", -1)): row
        for row in current.get("drivers", [])
        if isinstance(row, Mapping)
    }
    return {
        "changed": [
            row for key, row in sorted(new_rows.items()) if old_rows.get(key) != row
        ],
        "removed": sorted(set(old_rows) - set(new_rows)),
        "field_count": current.get("field_count", 0),
        "fresh_count": current.get("fresh_count", 0),
    }


def _changed_topics(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, Any]:
    changed: dict[str, Any] = {}
    for topic, value in current.items():
        old_value = previous.get(topic)
        if old_value == value:
            continue
        changed[topic] = (
            _classification_delta(old_value, value)
            if topic == "classification"
            and isinstance(old_value, Mapping)
            and isinstance(value, Mapping)
            else {"current": value}
        )
    return changed


def _error_payload(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message[:500]}


async def _first_subscription(
    websocket: WebSocket, timeout_s: float
) -> LiveSubscription | None:
    try:
        raw = await asyncio.wait_for(websocket.receive_json(), timeout=timeout_s)
        return LiveSubscription.model_validate(raw)
    except TimeoutError:
        await websocket.send_json(
            {
                "schema_version": 1,
                "type": "subscription.error",
                "sequence": 1,
                "server_time_ms": time.time_ns() // 1_000_000,
                "session_uid": None,
                "payload": _error_payload(
                    "subscription_timeout",
                    "Send a valid subscribe message before telemetry can be streamed.",
                ),
            }
        )
    except (ValidationError, json.JSONDecodeError, TypeError, ValueError):
        await websocket.send_json(
            {
                "schema_version": 1,
                "type": "subscription.error",
                "sequence": 1,
                "server_time_ms": time.time_ns() // 1_000_000,
                "session_uid": None,
                "payload": _error_payload(
                    "invalid_subscription",
                    "The first message must be a valid subscribe request.",
                ),
            }
        )
    except WebSocketDisconnect:
        return None
    with contextlib.suppress(RuntimeError, WebSocketDisconnect):
        await websocket.close(code=1008, reason="valid subscription required")
    return None


def create_live_router(
    store: StateStore,
    *,
    network_service: NetworkService | None = None,
    configured_max_hz: int = 10,
    handshake_timeout_s: float = 5.0,
) -> APIRouter:
    """Create ``/api/v1/live/ws`` bound to explicit runtime services."""

    if not 1 <= configured_max_hz <= 30:
        raise ValueError("configured_max_hz must be between 1 and 30")
    if handshake_timeout_s <= 0:
        raise ValueError("handshake_timeout_s must be positive")

    router = APIRouter(tags=["live"])

    @router.websocket("/api/v1/live/ws")
    async def live_state(websocket: WebSocket) -> None:
        await websocket.accept()
        subscription = await _first_subscription(websocket, handshake_timeout_s)
        if subscription is None:
            return

        sequence = 0
        receive_task: asyncio.Task[Any] | None = None
        topics = tuple(dict.fromkeys(subscription.topics))
        effective_hz = min(subscription.max_hz, configured_max_hz)
        interval_s = 1.0 / effective_hz
        network_state = (
            await _network_projection(network_service)
            if "network" in topics
            else None
        )
        last_network_poll = time.monotonic()

        async def send(
            message_type: str,
            payload: Mapping[str, Any],
            snapshot: Mapping[str, Any],
            **metadata: Any,
        ) -> None:
            nonlocal sequence
            sequence += 1
            session_uid = snapshot.get("session_uid")
            await websocket.send_json(
                {
                    "schema_version": 1,
                    "type": message_type,
                    "sequence": sequence,
                    "server_time_ms": time.time_ns() // 1_000_000,
                    "session_uid": str(session_uid) if session_uid else None,
                    "state_revision": int(snapshot.get("state_revision") or 0),
                    **metadata,
                    "payload": payload,
                }
            )

        async def complete_snapshot(reason: str) -> tuple[dict[str, Any], int]:
            nonlocal network_state, last_network_poll
            current = await store.snapshot_live()
            if "network" in topics:
                network_state = await _network_projection(network_service)
                last_network_poll = time.monotonic()
            projections = _project_topics(current, topics, network=network_state)
            await send(
                "live.snapshot",
                {
                    "reason": reason,
                    "subscription": {
                        "topics": list(topics),
                        "requested_max_hz": subscription.max_hz,
                        "effective_max_hz": effective_hz,
                    },
                    "topics": projections,
                },
                current,
            )
            return projections, int(current.get("state_revision") or 0)

        previous, previous_revision = await complete_snapshot("initial_subscription")
        loop = asyncio.get_running_loop()
        next_emit_at = loop.time() + interval_s
        receive_task = asyncio.create_task(websocket.receive_json())

        try:
            while True:
                timeout = max(0.0, next_emit_at - loop.time())
                done, _ = await asyncio.wait({receive_task}, timeout=timeout)
                if receive_task in done:
                    try:
                        control = receive_task.result()
                    except WebSocketDisconnect:
                        break
                    except (json.JSONDecodeError, TypeError, ValueError):
                        current = await store.snapshot_live()
                        await send(
                            "control.error",
                            _error_payload(
                                "invalid_control", "Control messages must be JSON objects."
                            ),
                            current,
                        )
                        receive_task = asyncio.create_task(websocket.receive_json())
                        continue

                    if not isinstance(control, Mapping):
                        current = await store.snapshot_live()
                        await send(
                            "control.error",
                            _error_payload(
                                "invalid_control", "Control messages must be JSON objects."
                            ),
                            current,
                        )
                    elif control.get("type") == "subscribe":
                        try:
                            subscription = LiveSubscription.model_validate(control)
                        except ValidationError:
                            current = await store.snapshot_live()
                            await send(
                                "control.error",
                                _error_payload(
                                    "invalid_subscription",
                                    "The replacement subscription is invalid.",
                                ),
                                current,
                            )
                        else:
                            topics = tuple(dict.fromkeys(subscription.topics))
                            effective_hz = min(
                                subscription.max_hz, configured_max_hz
                            )
                            interval_s = 1.0 / effective_hz
                            previous, previous_revision = await complete_snapshot(
                                "resubscribed"
                            )
                            next_emit_at = loop.time() + interval_s
                    elif control.get("type") in {
                        "snapshot",
                        "snapshot.request",
                        "resync",
                    }:
                        after = control.get("after_sequence")
                        reason = (
                            "client_sequence_gap"
                            if isinstance(after, int) and after != sequence
                            else "client_request"
                        )
                        previous, previous_revision = await complete_snapshot(reason)
                        next_emit_at = loop.time() + interval_s
                    elif control.get("type") == "ping":
                        current = await store.snapshot_live()
                        await send("pong", {}, current)
                    else:
                        current = await store.snapshot_live()
                        await send(
                            "control.error",
                            _error_payload(
                                "unknown_control",
                                "Use subscribe, snapshot.request, resync, or ping.",
                            ),
                            current,
                        )
                    receive_task = asyncio.create_task(websocket.receive_json())
                    continue

                current = await store.snapshot_live()
                revision = int(current.get("state_revision") or 0)
                if revision < previous_revision:
                    previous, previous_revision = await complete_snapshot(
                        "state_revision_reset"
                    )
                    next_emit_at = loop.time() + interval_s
                    continue

                now = time.monotonic()
                if "network" in topics and now - last_network_poll >= 0.5:
                    network_state = await _network_projection(network_service)
                    last_network_poll = time.monotonic()
                projections = _project_topics(current, topics, network=network_state)
                changed = _changed_topics(previous, projections)
                if changed:
                    skipped = max(0, revision - previous_revision - 1)
                    await send(
                        "live.delta",
                        {
                            "changed_topics": list(changed),
                            "topics": changed,
                        },
                        current,
                        base_revision=previous_revision,
                        coalesced_revisions=skipped,
                    )
                    previous = projections
                    previous_revision = revision
                next_emit_at = loop.time() + interval_s
        except WebSocketDisconnect:
            pass
        finally:
            if receive_task is not None and not receive_task.done():
                receive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect):
                    await receive_task

    return router


__all__ = ["create_live_router"]
