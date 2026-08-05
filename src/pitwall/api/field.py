"""Versioned HTTP contracts for persisted full-field analysis."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import Field

from ..api_models import ApiModel, VersionedResponse
from ..field_service import (
    DriverNotFoundError,
    FieldAnalysisService,
    FieldServiceError,
    SessionNotFoundError,
)

Availability = Literal["observed", "derived", "estimated", "stale", "unavailable"]


class MetricValue(ApiModel):
    value: str | float | bool | None = None
    unit: str | None = None
    availability: Availability
    reason: str | None = None
    n: int = Field(default=0, ge=0)


class DriverIdentity(ApiModel):
    car_id: str
    car_index: int = Field(ge=0, le=23)
    identity_revision: int = Field(ge=0)
    display_name: str
    team_id: int | None = None
    driver_id: int | None = None
    race_number: int | None = None
    is_player: bool
    is_ai: bool | None = None


class DriverReference(ApiModel):
    car_id: str
    car_index: int = Field(ge=0, le=23)
    identity_revision: int = Field(ge=0)
    display_name: str


class ContextSchema(ApiModel):
    version: Literal[1] = 1
    bits: dict[str, str]
    zero_means: str


class SessionProjection(ApiModel):
    track_id: int | None = None
    session_type: str | None = None
    status: str
    started_at: str | None = None
    ended_at: str | None = None
    quality_score: float | None = None


class ClassificationEntry(DriverIdentity):
    position: MetricValue
    gap_ms: MetricValue
    last_lap_ms: MetricValue
    best_lap_ms: MetricValue
    median_clean_pace_ms: MetricValue
    laps_recorded: MetricValue
    pit_context_runs: MetricValue
    compound: MetricValue
    tyre_age_laps: MetricValue
    status: MetricValue
    freshness_ms: MetricValue


class FieldSummaryResponse(VersionedResponse):
    session_id: str
    session: SessionProjection
    classification_availability: Availability
    classification_reason: str | None = None
    cars_observed: int = Field(ge=0)
    lap_rows: int = Field(ge=0)
    classification: list[ClassificationEntry]
    context_schema: ContextSchema
    truncated: bool
    warnings: list[str]


class PaceCell(ApiModel):
    lap_id: str | None = None
    raw_lap_time_s: float | None = None
    lap_time_s: float | None = None
    delta_to_lap_median_s: float | None = None
    performance_percentile: float | None = Field(default=None, ge=0, le=1)
    coverage: float = Field(ge=0, le=1)
    context_mask: int = Field(ge=0)
    context: list[str]
    included: bool
    availability: Availability
    reason: str | None = None


class PaceMatrixResponse(VersionedResponse):
    session_id: str
    availability: Availability
    reason: str | None = None
    drivers: list[DriverReference]
    lap_numbers: list[int]
    cells: list[list[PaceCell]]
    lap_median_s: list[float | None]
    lap_mad_s: list[float | None]
    n_by_lap: list[int]
    coverage_by_driver: dict[str, float]
    total_valid: int = Field(ge=0)
    total_cells: int = Field(ge=0)
    context_schema: ContextSchema
    include_context: bool
    source_rows: int = Field(ge=0)
    truncated: bool


class SegmentReference(ApiModel):
    segment_id: str
    label: str


class CornerMatrixResponse(VersionedResponse):
    session_id: str
    availability: Availability
    reason: str | None = None
    drivers: list[DriverReference]
    segments: list[SegmentReference]
    median_time_s: list[list[float | None]]
    delta_to_field_median_s: list[list[float | None]]
    performance_percentile: list[list[float | None]]
    rank: list[list[float | None]]
    valid_mask: list[list[bool]]
    field_median_s: list[float | None]
    field_mad_s: list[float | None]
    n_by_segment: list[int]
    sample_count: list[list[int]]
    coverage_by_driver: dict[str, float]
    source_rows: int = Field(ge=0)
    truncated: bool


class PositionPoint(ApiModel):
    lap_number: int = Field(ge=0)
    position: int = Field(ge=1, le=24)
    availability: Availability
    context_mask: int = Field(ge=0)


class PositionSeries(DriverIdentity):
    availability: Availability
    reason: str | None = None
    n: int = Field(ge=0)
    points: list[PositionPoint]


class PositionResponse(VersionedResponse):
    session_id: str
    availability: Availability
    reason: str | None = None
    series: list[PositionSeries]
    events: list[dict[str, Any]]
    cars_with_data: int = Field(ge=0)
    cars_observed: int = Field(ge=0)
    context_schema: ContextSchema
    truncated: bool


class Stint(ApiModel):
    ordinal: int = Field(ge=1)
    compound: str
    start_lap: int = Field(ge=0)
    end_lap: int = Field(ge=0)
    lap_count: int = Field(ge=1)
    clean_lap_count: int = Field(ge=0)
    median_clean_pace_s: MetricValue
    pace_slope_s_per_lap: MetricValue
    fuel_context: MetricValue


class DriverStints(DriverIdentity):
    availability: Availability
    reason: str | None = None
    n: int = Field(ge=0)
    stints: list[Stint]


class StintsResponse(VersionedResponse):
    session_id: str
    availability: Availability
    reason: str | None = None
    drivers: list[DriverStints]
    cars_with_data: int = Field(ge=0)
    cars_observed: int = Field(ge=0)
    truncated: bool


class DriverLap(ApiModel):
    lap_id: str
    lap_number: int = Field(ge=0)
    lap_time_ms: int | None = Field(default=None, ge=0)
    valid: bool
    coverage: float = Field(ge=0, le=1)
    quality: float = Field(ge=0, le=1)
    compound: str | None = None
    tyre_age_laps: int | None = Field(default=None, ge=0)
    weather_class: str | None = None
    context_mask: int = Field(ge=0)
    context: list[str]


class StrengthList(ApiModel):
    availability: Availability
    reason: str | None = None
    n: int = Field(ge=0)
    items: list[dict[str, Any]]


class DriverDetailResponse(VersionedResponse):
    session_id: str
    driver: DriverIdentity
    laps: list[DriverLap]
    summary: dict[str, int | float | None]
    stints: list[Stint]
    strengths: StrengthList
    weaknesses: StrengthList
    context_schema: ContextSchema
    truncated: bool


def _raise_service_error(exc: FieldServiceError) -> None:
    response_status = (
        status.HTTP_404_NOT_FOUND
        if isinstance(exc, (SessionNotFoundError, DriverNotFoundError))
        else status.HTTP_409_CONFLICT
    )
    raise HTTPException(
        status_code=response_status,
        detail={"code": exc.code, "message": str(exc)},
    ) from exc


def create_field_router(service: FieldAnalysisService) -> APIRouter:
    """Create a router without importing or mutating application globals."""

    router = APIRouter(prefix="/api/v1/sessions", tags=["field"])

    async def field_summary(session_id: str) -> dict[str, Any]:
        try:
            return await service.summary(session_id)
        except FieldServiceError as exc:
            _raise_service_error(exc)
            raise AssertionError("unreachable") from exc

    router.add_api_route(
        "/{session_id}/field",
        field_summary,
        methods=["GET"],
        response_model=FieldSummaryResponse,
    )
    router.add_api_route(
        "/{session_id}/field/classification",
        field_summary,
        methods=["GET"],
        response_model=FieldSummaryResponse,
    )

    @router.get("/{session_id}/field/pace", response_model=PaceMatrixResponse)
    async def pace(
        session_id: str, include_context: bool = False
    ) -> dict[str, Any]:
        try:
            return await service.pace(
                session_id, include_context=include_context
            )
        except FieldServiceError as exc:
            _raise_service_error(exc)
            raise AssertionError("unreachable") from exc

    @router.get("/{session_id}/field/corners", response_model=CornerMatrixResponse)
    async def corners(session_id: str) -> dict[str, Any]:
        try:
            return await service.corners(session_id)
        except FieldServiceError as exc:
            _raise_service_error(exc)
            raise AssertionError("unreachable") from exc

    @router.get("/{session_id}/field/positions", response_model=PositionResponse)
    async def positions(session_id: str) -> dict[str, Any]:
        try:
            return await service.positions(session_id)
        except FieldServiceError as exc:
            _raise_service_error(exc)
            raise AssertionError("unreachable") from exc

    @router.get("/{session_id}/field/stints", response_model=StintsResponse)
    async def stints(session_id: str) -> dict[str, Any]:
        try:
            return await service.stints(session_id)
        except FieldServiceError as exc:
            _raise_service_error(exc)
            raise AssertionError("unreachable") from exc

    @router.get(
        "/{session_id}/field/drivers/{car_id}",
        response_model=DriverDetailResponse,
    )
    async def driver(session_id: str, car_id: str) -> dict[str, Any]:
        try:
            return await service.driver(session_id, car_id)
        except FieldServiceError as exc:
            _raise_service_error(exc)
            raise AssertionError("unreachable") from exc

    return router


__all__ = ["create_field_router"]
