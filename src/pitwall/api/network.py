"""FastAPI router factory for the versioned Connection Center API."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response, status

from ..api_models import (
    DiagnoseResponse,
    ForwardTargetCreate,
    ForwardTargetPatch,
    ForwardTargetResponse,
    InterfacesResponse,
    ListenerResponse,
    ListenerStartRequest,
    NetworkInterfaceResponse,
    NetworkStatusResponse,
    PacketHealthResponse,
)
from ..forwarding import ForwardValidationError
from ..network_service import (
    ForwardTargetExists,
    ForwardTargetNotFound,
    ListenerBindError,
    ListenerSnapshot,
    ManagedForwardTarget,
    NetworkService,
    NetworkServiceError,
    NetworkSnapshot,
)

PACKET_NAMES = {
    0: "motion",
    1: "session",
    2: "lap_data",
    3: "event",
    4: "participants",
    5: "car_setups",
    6: "car_telemetry",
    7: "car_status",
    8: "final_classification",
    9: "lobby_info",
    10: "car_damage",
    11: "session_history",
    12: "tyre_sets",
    13: "motion_extended",
    14: "time_trial",
    15: "lap_positions",
}


def _error_detail(exc: Exception, code: str) -> dict[str, object]:
    detail: dict[str, object] = {"code": code, "message": str(exc)}
    target_id = getattr(exc, "target_id", None)
    if target_id:
        detail["target_id"] = target_id
    return detail


def _raise_service_error(exc: Exception) -> None:
    if isinstance(exc, ForwardValidationError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_error_detail(exc, exc.code),
        ) from exc
    if isinstance(exc, ListenerBindError):
        response_status = (
            status.HTTP_409_CONFLICT
            if exc.code == "bind_conflict"
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(
            status_code=response_status,
            detail={
                **_error_detail(exc, exc.code),
                "bind_host": exc.host,
                "port": exc.port,
            },
        ) from exc
    if isinstance(exc, ForwardTargetExists):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_error_detail(exc, exc.code),
        ) from exc
    if isinstance(exc, ForwardTargetNotFound):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error_detail(exc, exc.code),
        ) from exc
    if isinstance(exc, NetworkServiceError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_error_detail(exc, exc.code),
        ) from exc
    raise exc


def _listener_response(listener: ListenerSnapshot) -> ListenerResponse:
    return ListenerResponse(
        state=listener.state.value,
        bind_host=listener.bind_host,
        port=listener.port,
        started_at=listener.started_at,
        last_valid_packet_age_ms=listener.last_valid_packet_age_ms,
        error=listener.error,
    )


def _forwarder_response(item: ManagedForwardTarget) -> ForwardTargetResponse:
    target = item.target
    counters = item.counters
    return ForwardTargetResponse(
        id=target.id,
        label=target.label,
        enabled=target.enabled,
        host=target.host,
        port=target.port,
        resolved_address=(
            counters.resolved_addresses[0] if counters.resolved_addresses else None
        ),
        packet_ids=("all" if target.packet_ids is None else sorted(target.packet_ids)),
        forward_unknown_packets=target.forward_unknown_packets,
        packets_sent=counters.sent_packets,
        bytes_sent=counters.sent_bytes,
        queue_drops=counters.queue_drops,
        socket_errors=counters.socket_errors,
        last_success=(
            datetime.fromtimestamp(counters.last_success_at, tz=UTC)
            if counters.last_success_at is not None
            else None
        ),
        last_error=counters.last_error,
    )


def _interface_response(
    snapshot: NetworkSnapshot,
) -> InterfacesResponse:
    ranked = {
        (item.interface.adapter_id, item.interface.address): item
        for item in snapshot.recommendation.ranked
    }
    rows: list[NetworkInterfaceResponse] = []
    for interface in snapshot.discovery.interfaces:
        item = ranked[(interface.adapter_id, interface.address)]
        rows.append(
            NetworkInterfaceResponse(
                id=interface.adapter_id,
                name=interface.name,
                description=interface.description,
                ipv4=interface.address,
                prefix_length=interface.prefix_length,
                kind=interface.kind.value,
                operational=interface.is_up,
                default_gateway=interface.has_default_gateway,
                metric=interface.metric,
                pinned="pinned by user" in item.reasons,
                previously_worked=(
                    "valid F1 traffic observed previously" in item.reasons
                ),
                score=item.score,
                reasons=list(item.reasons),
            )
        )
    recommended = snapshot.recommendation.recommended
    return InterfacesResponse(
        interfaces=rows,
        recommended_ipv4=recommended.address if recommended else None,
        recommended_adapter_id=recommended.adapter_id if recommended else None,
        warnings=list(
            dict.fromkeys(
                [
                    *snapshot.discovery.warnings,
                    *snapshot.recommendation.warnings,
                ]
            )
        ),
    )


def _status_response(snapshot: NetworkSnapshot) -> NetworkStatusResponse:
    recommended = snapshot.recommendation.recommended
    recommendation: dict[str, object] | None = None
    if recommended:
        ranked = next(
            (
                item
                for item in snapshot.recommendation.ranked
                if item.interface == recommended
            ),
            None,
        )
        recommendation = {
            "console_destination_ipv4": recommended.address,
            "adapter_id": recommended.adapter_id,
            "confidence": snapshot.recommendation.confidence,
            "reasons": list(ranked.reasons) if ranked else [],
        }
    packets = [
        PacketHealthResponse(
            packet_id=item.key.packet_id,
            packet_name=PACKET_NAMES.get(item.key.packet_id),
            source_ip=item.key.source_ip,
            source_port=item.key.source_port,
            session_uid=str(item.key.session_uid),
            observed_hz_1s=item.observed_hz_1s,
            observed_hz_10s=item.observed_hz_10s,
            observed_hz_session=item.observed_hz_session,
            last_age_ms=(
                None if item.last_age_ms is None else max(0, round(item.last_age_ms))
            ),
            received=item.received,
            valid=item.valid_parsed,
            invalid=item.parse_errors,
            provisional_gap=item.provisional_gaps,
            confirmed_lost=item.confirmed_lost,
            out_of_order=item.out_of_order,
            duplicates=item.duplicates,
            interarrival_mean_ms=item.inter_arrival_mean_ms,
            interarrival_p95_ms=item.inter_arrival_p95_ms,
            interarrival_max_ms=item.inter_arrival_max_ms,
            jitter_ms=item.jitter_ms,
            status=item.status,
        )
        for item in snapshot.packet_health.packets
    ]
    queues = {
        name: {
            "depth": item.depth,
            "capacity": item.capacity,
            "high_water": item.high_water,
            "drops": item.drops,
            "last_drain_age_ms": item.last_drain_age_ms,
        }
        for name, item in snapshot.queues.items()
    }
    return NetworkStatusResponse(
        listener=_listener_response(snapshot.listener),
        recommendation=recommendation,
        source=dict(snapshot.source) if snapshot.source else None,
        game=dict(snapshot.game) if snapshot.game else None,
        packets=packets,
        forwarders=[_forwarder_response(item) for item in snapshot.forwarders],
        queues=queues,
        warnings=list(snapshot.warnings),
    )


def _packet_ids(value: str | list[int]) -> list[int] | None:
    return None if value == "all" else value


def create_network_router(service: NetworkService) -> APIRouter:
    """Create a router bound to one service; no global application state required."""

    router = APIRouter(prefix="/api/v1/network", tags=["network"])

    @router.get("/interfaces", response_model=InterfacesResponse)
    async def get_interfaces() -> InterfacesResponse:
        snapshot = await service.snapshot(refresh_interfaces=True)
        return _interface_response(snapshot)

    @router.get("/status", response_model=NetworkStatusResponse)
    async def get_status() -> NetworkStatusResponse:
        return _status_response(await service.snapshot())

    @router.post("/listener/start", response_model=ListenerResponse)
    async def start_listener(request: ListenerStartRequest) -> ListenerResponse:
        try:
            listener = await service.start_listener(request.bind_host, request.port)
        except Exception as exc:  # converted to bounded API errors below
            _raise_service_error(exc)
            raise AssertionError("unreachable") from exc
        return _listener_response(listener)

    @router.post("/listener/stop", response_model=ListenerResponse)
    async def stop_listener() -> ListenerResponse:
        return _listener_response(await service.stop_listener())

    @router.get("/forwarders", response_model=list[ForwardTargetResponse])
    async def list_forwarders() -> list[ForwardTargetResponse]:
        return [_forwarder_response(item) for item in service.managed_targets()]

    @router.post(
        "/forwarders",
        response_model=ForwardTargetResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_forwarder(
        request: ForwardTargetCreate,
    ) -> ForwardTargetResponse:
        try:
            item = await service.create_forward_target(
                target_id=request.id,
                label=request.label,
                enabled=request.enabled,
                host=request.host,
                port=request.port,
                packet_ids=_packet_ids(request.packet_ids),
                forward_unknown_packets=request.forward_unknown_packets,
                allow_public=request.confirm_public_address,
            )
        except Exception as exc:  # converted to bounded API errors below
            _raise_service_error(exc)
            raise AssertionError("unreachable") from exc
        return _forwarder_response(item)

    @router.patch("/forwarders/{target_id}", response_model=ForwardTargetResponse)
    async def patch_forwarder(
        target_id: str,
        request: ForwardTargetPatch,
    ) -> ForwardTargetResponse:
        changes = request.model_dump(exclude_unset=True)
        if any(value is None for value in changes.values()):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "null_forward_field",
                    "message": "Forwarding fields cannot be set to null.",
                    "target_id": target_id,
                },
            )
        if any(
            field in changes and not str(changes[field]).strip()
            for field in ("label", "host")
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "code": "blank_forward_field",
                    "message": "Forwarding label and host cannot be blank.",
                    "target_id": target_id,
                },
            )
        confirmation_present = "confirm_public_address" in changes
        confirmation = bool(changes.pop("confirm_public_address", False))
        if "packet_ids" in changes:
            if changes["packet_ids"] == []:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail={
                        "code": "empty_packet_filter",
                        "message": (
                            "packet_ids must be 'all' or contain at least one ID."
                        ),
                        "target_id": target_id,
                    },
                )
            changes["packet_ids"] = _packet_ids(changes["packet_ids"])
        if confirmation_present:
            changes["allow_public"] = confirmation
        elif "host" in changes:
            changes["allow_public"] = False
        try:
            item = await service.update_forward_target(target_id, **changes)
        except Exception as exc:  # converted to bounded API errors below
            _raise_service_error(exc)
            raise AssertionError("unreachable") from exc
        return _forwarder_response(item)

    @router.delete("/forwarders/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_forwarder(target_id: str) -> Response:
        try:
            await service.delete_forward_target(target_id)
        except Exception as exc:  # converted to bounded API errors below
            _raise_service_error(exc)
            raise AssertionError("unreachable") from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/diagnose", response_model=DiagnoseResponse)
    async def diagnose() -> DiagnoseResponse:
        report = await service.diagnose()
        return DiagnoseResponse(
            checks=[dict(item) for item in report.checks],
            actions=list(report.actions),
            generated_at=report.generated_at,
        )

    return router


__all__ = ["create_network_router"]
