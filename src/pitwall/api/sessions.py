"""Versioned Library and saved-session HTTP contracts."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import Field, field_validator, model_validator

from ..api_models import ApiModel, VersionedResponse
from ..catalog import (
    ActiveSessionDeleteError,
    DeletePreviewError,
    SessionCatalog,
)

JobDispatcher = Callable[[dict[str, Any]], Awaitable[None] | None]


class SessionPatchRequest(ApiModel):
    display_name: str | None = Field(default=None, max_length=120)
    tags: list[str] | None = Field(default=None, max_length=20)
    starred: bool | None = None

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = sorted({tag.strip() for tag in value if tag.strip()})
        if any(len(tag) > 40 for tag in normalized):
            raise ValueError("each tag must be at most 40 characters")
        return normalized

    @model_validator(mode="after")
    def require_change(self) -> SessionPatchRequest:
        if not self.model_fields_set:
            raise ValueError("provide display_name, tags, or starred")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("session metadata fields cannot be null")
        return self


class ReprocessRequest(ApiModel):
    algorithm_bundle: str = Field(default="analysis_4.2.0", min_length=1, max_length=80)

    @field_validator("algorithm_bundle")
    @classmethod
    def normalize_bundle(cls, value: str) -> str:
        return value.strip()


class SessionListResponse(VersionedResponse):
    items: list[dict[str, Any]]
    next_cursor: str | None
    has_more: bool


class SessionDetailResponse(VersionedResponse):
    session: dict[str, Any]


class SessionLapsResponse(VersionedResponse):
    session_id: str
    items: list[dict[str, Any]]


class SessionQualityResponse(VersionedResponse):
    session_id: str
    status: str
    quality_score: float | None
    participants_observed: int
    laps: dict[str, int | float]
    trace_manifests: dict[str, int]
    raw_captures: dict[str, int]
    packet_health_available: bool
    warnings: list[str]


class ReprocessResponse(VersionedResponse):
    session_id: str
    reused: bool
    job: dict[str, Any]


class DeletePreviewResponse(VersionedResponse):
    phase: Literal["preview"] = "preview"
    session_id: str
    confirmation_token: str
    expires_at: str
    irreversible: bool
    impact: dict[str, Any]


class DeleteResultResponse(VersionedResponse):
    phase: Literal["deleted"] = "deleted"
    session_id: str
    deleted: bool
    records: dict[str, Any]
    removed_artifacts: list[str]
    missing_artifacts: list[str]
    cleanup_errors: list[str]


def _not_found(session_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "session_not_found",
            "message": f"Saved session '{session_id}' was not found.",
            "session_id": session_id,
        },
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        try:
            return _json_safe(json.loads(value.decode("utf-8")))
        except (UnicodeDecodeError, ValueError):
            return value.hex()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def create_sessions_router(
    catalog: SessionCatalog,
    *,
    trace_root: Path | None = None,
    capture_root: Path | None = None,
    enqueue_reprocess: JobDispatcher | None = None,
) -> APIRouter:
    """Create a Library router bound to explicit repositories and paths."""

    router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])

    @router.get("", response_model=SessionListResponse)
    async def list_sessions(
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: str | None = None,
        track_id: int | None = None,
        session_type: str | None = None,
        starred: bool | None = None,
        search: Annotated[str | None, Query(max_length=120)] = None,
    ) -> SessionListResponse:
        try:
            page = await catalog.list_sessions(
                limit=limit,
                cursor=cursor,
                track_id=track_id,
                session_type=session_type,
                starred=starred,
                search=search,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "invalid_session_filter", "message": str(exc)},
            ) from exc
        return SessionListResponse(**_json_safe(page))

    @router.get("/{session_id}", response_model=SessionDetailResponse)
    async def get_session(session_id: str) -> SessionDetailResponse:
        item = await catalog.get_session(session_id)
        if item is None:
            raise _not_found(session_id)
        return SessionDetailResponse(session=_json_safe(item))

    @router.patch("/{session_id}", response_model=SessionDetailResponse)
    async def patch_session(
        session_id: str,
        request: SessionPatchRequest,
    ) -> SessionDetailResponse:
        changed = await catalog.patch_session(
            session_id,
            display_name=request.display_name,
            tags=request.tags,
            starred=request.starred,
        )
        if not changed:
            raise _not_found(session_id)
        item = await catalog.get_session(session_id)
        if item is None:  # Defensive against external deletion between operations.
            raise _not_found(session_id)
        return SessionDetailResponse(session=_json_safe(item))

    @router.get("/{session_id}/laps", response_model=SessionLapsResponse)
    async def get_laps(
        session_id: str,
        car_id: str | None = None,
        valid: bool | None = None,
    ) -> SessionLapsResponse:
        if await catalog.get_session(session_id) is None:
            raise _not_found(session_id)
        rows = await catalog.list_laps(session_id, car_key=car_id, valid=valid)
        return SessionLapsResponse(session_id=session_id, items=_json_safe(rows))

    @router.get("/{session_id}/quality", response_model=SessionQualityResponse)
    async def get_quality(session_id: str) -> SessionQualityResponse:
        report = await catalog.get_quality(session_id)
        if report is None:
            raise _not_found(session_id)
        return SessionQualityResponse(**_json_safe(report))

    @router.post(
        "/{session_id}/reprocess",
        response_model=ReprocessResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def reprocess_session(
        session_id: str,
        request: ReprocessRequest | None = None,
    ) -> ReprocessResponse:
        bundle = request.algorithm_bundle if request else "analysis_4.2.0"
        result = await catalog.request_reprocess(
            session_id, algorithm_bundle=bundle
        )
        if result is None:
            raise _not_found(session_id)
        if enqueue_reprocess is not None and not result["reused"]:
            dispatched = enqueue_reprocess(dict(result["job"]))
            if inspect.isawaitable(dispatched):
                await dispatched
        return ReprocessResponse(
            session_id=session_id,
            reused=bool(result["reused"]),
            job=_json_safe(result["job"]),
        )

    @router.delete(
        "/{session_id}",
        response_model=DeletePreviewResponse | DeleteResultResponse,
    )
    async def delete_session(
        session_id: str,
        confirmation_token: Annotated[
            str | None,
            Header(alias="X-Pitwall-Delete-Token", max_length=200),
        ] = None,
    ) -> DeletePreviewResponse | DeleteResultResponse:
        try:
            if confirmation_token is None:
                preview = await catalog.preview_delete(session_id)
                if preview is None:
                    raise _not_found(session_id)
                return DeletePreviewResponse(**preview)
            result = await catalog.delete_session(
                session_id,
                confirmation_token,
                trace_root=trace_root,
                capture_root=capture_root,
            )
            if result is None:
                raise _not_found(session_id)
            return DeleteResultResponse(**result)
        except ActiveSessionDeleteError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "session_still_recording",
                    "message": str(exc),
                    "session_id": session_id,
                },
            ) from exc
        except DeletePreviewError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "delete_preview_invalid",
                    "message": str(exc),
                    "session_id": session_id,
                },
            ) from exc

    return router


__all__ = ["create_sessions_router"]
