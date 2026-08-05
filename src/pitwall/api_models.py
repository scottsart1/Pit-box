from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VersionedResponse(ApiModel):
    schema_version: Literal[1] = 1


class ListenerStartRequest(ApiModel):
    bind_host: str = "0.0.0.0"
    port: Annotated[int, Field(ge=1, le=65_535)] = 20_777

    @field_validator("bind_host")
    @classmethod
    def non_blank_bind_host(cls, value: str) -> str:
        host = value.strip()
        if not host:
            raise ValueError("bind_host cannot be blank")
        return host


class ForwardTargetCreate(ApiModel):
    id: str | None = None
    label: str
    enabled: bool = True
    host: str
    port: Annotated[int, Field(ge=1, le=65_535)]
    packet_ids: Literal["all"] | list[int] = "all"
    forward_unknown_packets: bool = False
    confirm_public_address: bool = False

    @field_validator("id", "label", "host")
    @classmethod
    def non_blank_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("value cannot be blank")
        return normalized

    @field_validator("packet_ids")
    @classmethod
    def valid_packet_ids(cls, value: Literal["all"] | list[int]):
        if value == "all":
            return value
        normalized = sorted({int(packet_id) for packet_id in value})
        if not normalized:
            raise ValueError("packet_ids must be 'all' or contain at least one ID")
        if any(packet_id < 0 or packet_id > 255 for packet_id in normalized):
            raise ValueError("packet IDs must be between 0 and 255")
        return normalized


class ForwardTargetPatch(ApiModel):
    label: str | None = None
    enabled: bool | None = None
    host: str | None = None
    port: Annotated[int | None, Field(ge=1, le=65_535)] = None
    packet_ids: Literal["all"] | list[int] | None = None
    forward_unknown_packets: bool | None = None
    confirm_public_address: bool = False


class NetworkInterfaceResponse(ApiModel):
    id: str
    name: str
    description: str
    ipv4: str
    prefix_length: int | None = None
    kind: str
    operational: bool
    default_gateway: bool
    metric: int | None = None
    pinned: bool = False
    previously_worked: bool = False
    score: float
    reasons: list[str]


class InterfacesResponse(VersionedResponse):
    interfaces: list[NetworkInterfaceResponse]
    recommended_ipv4: str | None
    recommended_adapter_id: str | None
    warnings: list[str] = Field(default_factory=list)
    # False when platform discovery was unavailable and the safe socket-derived
    # fallback answered instead. The fallback cannot report adapter kind,
    # gateway or metric, so a client should present it as provisional and ask
    # again rather than treating it as the final adapter list.
    discovery_authoritative: bool = True


class ListenerResponse(ApiModel):
    state: Literal["off", "listening", "receiving", "stale", "error"]
    bind_host: str
    port: int
    started_at: datetime | None = None
    last_valid_packet_age_ms: int | None = None
    error: str | None = None


class PacketHealthResponse(ApiModel):
    packet_id: int
    packet_name: str | None = None
    source_ip: str
    source_port: int
    session_uid: str
    observed_hz_1s: float
    observed_hz_10s: float
    observed_hz_session: float
    last_age_ms: int | None
    received: int
    valid: int
    invalid: int
    provisional_gap: int
    confirmed_lost: int
    out_of_order: int
    duplicates: int
    interarrival_mean_ms: float | None
    interarrival_p95_ms: float | None
    interarrival_max_ms: float | None
    jitter_ms: float | None
    status: str


class ForwardTargetResponse(ApiModel):
    id: str
    label: str
    enabled: bool
    host: str
    port: int
    resolved_address: str | None = None
    packet_ids: Literal["all"] | list[int]
    forward_unknown_packets: bool
    packets_sent: int
    bytes_sent: int
    queue_drops: int
    socket_errors: int
    last_success: datetime | None = None
    last_error: str | None = None


class NetworkStatusResponse(VersionedResponse):
    listener: ListenerResponse
    recommendation: dict[str, object] | None = None
    source: dict[str, object] | None = None
    game: dict[str, object] | None = None
    packets: list[PacketHealthResponse] = Field(default_factory=list)
    forwarders: list[ForwardTargetResponse] = Field(default_factory=list)
    queues: dict[str, dict[str, int | float | None]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class DiagnoseResponse(VersionedResponse):
    checks: list[dict[str, object]]
    actions: list[str]
    generated_at: datetime


class LiveSubscription(ApiModel):
    type: Literal["subscribe"] = "subscribe"
    topics: list[
        Literal[
            "session",
            "player",
            "classification",
            "flags",
            "network",
            "strategy",
            "engineer",
        ]
    ] = Field(default_factory=lambda: ["session", "player", "classification"])
    max_hz: Annotated[int, Field(ge=1, le=30)] = 10


class TraceRangeQuery(ApiModel):
    fields: list[str]
    axis: Literal["distance", "time"] = "distance"
    from_value: float | None = None
    to_value: float | None = None
    max_points: Annotated[int, Field(ge=32, le=20_000)] = 1_600


class ReferenceSelection(ApiModel):
    kind: Literal[
        "lap",
        "session_pb",
        "all_time_pb",
        "recent_representative",
        "theoretical_best",
        "field_driver",
        "field_percentile",
        "saved_benchmark",
    ]
    lap_id: str | None = None
    key: str | None = None


class ComparisonCreate(ApiModel):
    candidate_lap_id: str
    reference: ReferenceSelection
    segment_model: str = "default"
    allow_caveated_reference: bool = False
