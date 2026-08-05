"""Versioned HTTP surface for persisted track models and review segments."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from ..track_model_service import (
    SessionNotFoundError,
    TrackIdentityUnavailableError,
    TrackModelCorruptError,
    TrackModelNotFoundError,
    TrackModelService,
    TrackModelServiceError,
)


class TrackModelBuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False
    max_laps: int = Field(default=12, ge=3, le=24)
    review_segments: int = Field(default=10, ge=8, le=15)


class TrackModelResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: Literal[1] = 1


def _raise_service_error(exc: TrackModelServiceError) -> None:
    if isinstance(exc, (SessionNotFoundError, TrackModelNotFoundError)):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, TrackIdentityUnavailableError):
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
    elif isinstance(exc, TrackModelCorruptError):
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_409_CONFLICT
    raise HTTPException(
        status_code=code,
        detail={"code": exc.code, "message": str(exc)},
    ) from exc


def create_track_models_router(service: TrackModelService) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["track-models"])

    @router.get(
        "/sessions/{session_id}/track-model",
        response_model=TrackModelResponse,
    )
    async def session_track_model(session_id: str) -> dict[str, Any]:
        try:
            return await service.session_status(session_id)
        except TrackModelServiceError as exc:
            _raise_service_error(exc)
            raise AssertionError("unreachable") from exc

    @router.post(
        "/sessions/{session_id}/track-model/build",
        response_model=TrackModelResponse,
    )
    async def build_session_track_model(
        session_id: str,
        request: TrackModelBuildRequest,
    ) -> dict[str, Any]:
        try:
            return await service.build_for_session(
                session_id,
                force=request.force,
                max_laps=request.max_laps,
                review_segments=request.review_segments,
            )
        except TrackModelServiceError as exc:
            _raise_service_error(exc)
            raise AssertionError("unreachable") from exc

    @router.get("/track-models/{model_id}", response_model=TrackModelResponse)
    async def track_model(
        model_id: str,
        max_points: Annotated[int, Query(ge=32, le=20_000)] = 1600,
    ) -> dict[str, Any]:
        try:
            return await service.get_model(model_id, max_points=max_points)
        except TrackModelServiceError as exc:
            _raise_service_error(exc)
            raise AssertionError("unreachable") from exc

    return router

