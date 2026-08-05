"""Versioned trace, reference, comparison, and findings API."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from ..api_models import ComparisonCreate
from ..comparison_service import (
    ComparisonService,
    ComparisonServiceError,
    LapNotFoundError,
    ReferenceCompatibilityError,
    TraceUnavailableError,
    UnsupportedReferenceError,
)


class ApiResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_version: Literal[1] = 1


class TraceAxis(BaseModel):
    name: Literal["distance"]
    unit: Literal["m"]
    values: list[float]


class TraceSeries(BaseModel):
    unit: str
    values: list[float | None]
    availability: Literal["observed", "derived", "estimated", "stale", "unavailable"]
    coverage: float = Field(ge=0, le=1)


class TraceResponse(ApiResponse):
    lap_id: str
    axis: TraceAxis
    series: dict[str, TraceSeries]
    coverage: float = Field(ge=0, le=1)
    downsample: dict[str, int | str]
    source: str


class ReferenceResponse(ApiResponse):
    candidate_lap_id: str
    items: list[dict[str, Any]]


class ComparisonResponse(ApiResponse):
    comparison_id: str
    candidate: dict[str, Any]
    reference: dict[str, Any]
    compatibility: dict[str, Any]
    algorithm_bundle: str
    coverage_ratio: float
    quality_score: float
    lap_delta_s: float | None
    sign_convention: str
    segments: list[dict[str, Any]]
    findings: list[dict[str, Any]]


class FindingsResponse(ApiResponse):
    comparison_id: str
    findings: list[dict[str, Any]]


class ExplanationResponse(ApiResponse):
    comparison_id: str
    text: str
    finding_ids: list[str]
    source: str


def _fields(value: str) -> list[str]:
    fields = [item.strip() for item in value.split(",") if item.strip()]
    if not fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "empty_fields", "message": "At least one trace field is required."},
        )
    if len(fields) > 24:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "too_many_fields", "message": "At most 24 trace fields may be requested."},
        )
    return list(dict.fromkeys(fields))


def _raise_service_error(exc: ComparisonServiceError) -> None:
    if isinstance(exc, LapNotFoundError):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, TraceUnavailableError):
        code = status.HTTP_409_CONFLICT
    elif isinstance(exc, (ReferenceCompatibilityError, UnsupportedReferenceError)):
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
    else:
        code = status.HTTP_404_NOT_FOUND if "does not exist" in str(exc) else 409
    detail: dict[str, Any] = {
        "code": exc.code,
        "message": str(exc),
    }
    if isinstance(exc, ReferenceCompatibilityError):
        detail["compatibility"] = service_compatibility(exc)
    raise HTTPException(status_code=code, detail=detail) from exc


def service_compatibility(exc: ReferenceCompatibilityError) -> dict[str, Any]:
    return {
        "class": exc.report.classification.value,
        "compatibility_weight": exc.report.compatibility_weight,
        "allows_coaching": exc.report.allows_coaching,
        "caveats": list(exc.report.caveats),
        "issues": [
            {
                "code": item.code,
                "message": item.message,
                "severity": item.severity.value,
            }
            for item in exc.report.issues
        ],
    }


def create_analysis_router(service: ComparisonService) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["analysis"])

    @router.get("/laps/{lap_id}/trace", response_model=TraceResponse)
    async def lap_trace(
        lap_id: str,
        fields: str = Query("speed,brake,throttle,steering,gear"),
        axis: Literal["distance"] = "distance",
        from_m: float | None = None,
        to_m: float | None = None,
        max_points: Annotated[int, Query(ge=32, le=20_000)] = 1600,
    ) -> dict[str, Any]:
        del axis
        if from_m is not None and to_m is not None and from_m > to_m:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "invalid_range", "message": "from_m must not exceed to_m."},
            )
        try:
            return await service.get_lap_trace(
                lap_id,
                fields=_fields(fields),
                start_m=from_m,
                end_m=to_m,
                max_points=max_points,
            )
        except ComparisonServiceError as exc:
            _raise_service_error(exc)
            raise AssertionError("unreachable") from exc

    @router.get("/laps/{lap_id}/references", response_model=ReferenceResponse)
    async def references(lap_id: str) -> dict[str, Any]:
        try:
            return await service.list_references(lap_id)
        except ComparisonServiceError as exc:
            _raise_service_error(exc)
            raise AssertionError("unreachable") from exc

    @router.post(
        "/comparisons",
        response_model=ComparisonResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_comparison(request: ComparisonCreate) -> dict[str, Any]:
        try:
            return await service.create_comparison(
                request.candidate_lap_id,
                reference_kind=request.reference.kind,
                reference_lap_id=request.reference.lap_id,
                allow_caveated_reference=request.allow_caveated_reference,
            )
        except ComparisonServiceError as exc:
            _raise_service_error(exc)
            raise AssertionError("unreachable") from exc

    @router.get("/comparisons/{comparison_id}", response_model=ComparisonResponse)
    async def comparison(comparison_id: str) -> dict[str, Any]:
        try:
            return await service.get_comparison(comparison_id)
        except ComparisonServiceError as exc:
            _raise_service_error(exc)
            raise AssertionError("unreachable") from exc

    @router.get("/comparisons/{comparison_id}/trace", response_model=ApiResponse)
    async def comparison_trace(
        comparison_id: str,
        fields: str = Query("speed,delta,brake,throttle,steering,gear"),
        from_m: float | None = None,
        to_m: float | None = None,
        max_points: Annotated[int, Query(ge=32, le=20_000)] = 1600,
    ) -> dict[str, Any]:
        if from_m is not None and to_m is not None and from_m > to_m:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "invalid_range", "message": "from_m must not exceed to_m."},
            )
        try:
            return await service.get_comparison_trace(
                comparison_id,
                fields=_fields(fields),
                start_m=from_m,
                end_m=to_m,
                max_points=max_points,
            )
        except ComparisonServiceError as exc:
            _raise_service_error(exc)
            raise AssertionError("unreachable") from exc

    @router.get(
        "/comparisons/{comparison_id}/findings", response_model=FindingsResponse
    )
    async def findings(comparison_id: str) -> dict[str, Any]:
        try:
            return await service.get_findings(comparison_id)
        except ComparisonServiceError as exc:
            _raise_service_error(exc)
            raise AssertionError("unreachable") from exc

    @router.post(
        "/comparisons/{comparison_id}/explain", response_model=ExplanationResponse
    )
    async def explain(comparison_id: str) -> dict[str, Any]:
        try:
            return await service.explain(comparison_id)
        except ComparisonServiceError as exc:
            _raise_service_error(exc)
            raise AssertionError("unreachable") from exc

    return router


__all__ = ["create_analysis_router"]
