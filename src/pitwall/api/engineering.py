"""Saved run analysis uses explicit canonical session identifiers."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from ..catalog import session_id
from ..engineering import (
    EngineeringService,
    report_text,
    strategic_rivals,
)
from ..state import StateStore


class CompareRunsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a: str = Field(min_length=1, max_length=180)
    b: str = Field(min_length=1, max_length=180)
    source: Literal["runs", "groups"] = "runs"
    mode: Literal["setup", "stint"] = "setup"
    b_session_id: str | None = Field(default=None, min_length=1, max_length=180)
    b_source: Literal["runs", "groups"] | None = None


class LapGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=180)
    name: str = Field(min_length=1, max_length=80)
    lap_ids: list[str] = Field(min_length=1, max_length=5000)


class SaveGroupsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    groups: list[LapGroup] = Field(max_length=12)


class SuggestGroupsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    count: int = Field(ge=2, le=12, strict=True)


class LapNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note_id: str | None = Field(default=None, min_length=1, max_length=180)
    lap_ids: list[str] = Field(min_length=1, max_length=5000)
    text: str = Field(min_length=1, max_length=2000)
    category: Literal[
        "traffic", "mistake", "balance", "conditions", "mechanical", "other"
    ] = "other"
    exclude_from_pace: bool = Field(default=False, strict=True)


class RunNotesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str | None = Field(default=None, max_length=180)
    objective: str = Field(default="", max_length=2000)
    conclusion: str = Field(default="", max_length=4000)


def create_engineering_router(
    service: EngineeringService, store: StateStore
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["engineering"])

    async def get_report(key: str) -> dict:
        try:
            return await service.report(key)
        except KeyError as exc:
            raise HTTPException(404, "Saved session or run was not found.") from exc

    @router.get("/engineering/live")
    async def live() -> dict:
        snapshot = await store.peek("session_uid", "restart_epoch")
        if not snapshot["session_uid"]:
            return {
                "available": False,
                "reason": "Start a session and complete a lap to record your first run.",
            }
        try:
            report = await service.report(
                session_id(snapshot["session_uid"], snapshot["restart_epoch"])
            )
        except KeyError:
            return {
                "available": False,
                "reason": "The first session recording is still being saved.",
            }
        return {"available": True, **report}

    @router.get("/engineering/rivals")
    async def rivals() -> dict:
        return strategic_rivals(await store.snapshot_analysis())

    @router.get("/sessions/{key}/engineering")
    async def report(key: str) -> dict:
        return await get_report(key)

    @router.post("/sessions/{key}/engineering/compare")
    async def compare(key: str, body: CompareRunsRequest) -> dict:
        try:
            return await service.compare(key, **body.model_dump())
        except KeyError as exc:
            raise HTTPException(
                404, "Saved session or selection was not found in the selected session."
            ) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/sessions/{key}/engineering/groups/suggest")
    async def suggest(key: str, body: SuggestGroupsRequest) -> dict:
        import asyncio

        from ..engineering_groups import suggest_groups

        report = await get_report(key)
        try:
            groups = await asyncio.to_thread(suggest_groups, report["laps"], body.count)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "groups": groups,
            "method": "chronological",
            "explanation": "Continuous lap groups, prioritising session timeline, tyre and setup changes. Review the boundaries before saving.",
        }

    @router.put("/sessions/{key}/engineering/groups")
    async def groups(key: str, body: SaveGroupsRequest) -> dict:
        try:
            return await service.save_groups(
                key, [item.model_dump() for item in body.groups]
            )
        except KeyError as exc:
            raise HTTPException(404, "Saved session was not found.") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.patch("/sessions/{key}/engineering/lap-notes")
    async def lap_notes(key: str, body: LapNoteRequest) -> dict:
        try:
            await service.save_lap_note(key, **body.model_dump(), source="driver")
        except KeyError as exc:
            raise HTTPException(404, "Saved session or note was not found.") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return await get_report(key)

    @router.delete("/sessions/{key}/engineering/lap-notes/{note_id}")
    async def delete_lap_note(key: str, note_id: str) -> dict:
        try:
            await service.delete_lap_note(key, note_id)
        except KeyError as exc:
            raise HTTPException(404, "Saved session or note was not found.") from exc
        return await get_report(key)

    @router.patch("/sessions/{key}/engineering/notes")
    async def notes(key: str, body: RunNotesRequest) -> dict:
        try:
            return await service.save_notes(
                key, body.run_id, body.objective, body.conclusion
            )
        except KeyError as exc:
            raise HTTPException(404, "Saved session or run was not found.") from exc

    @router.get("/sessions/{key}/engineering/export")
    async def export(key: str, format: Literal["text", "json"] = "text") -> Response:
        report = await get_report(key)
        import json

        body = (
            report_text(report)
            if format == "text"
            else json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
        )
        suffix = "txt" if format == "text" else "json"
        # A fixed basename avoids reflecting arbitrary path text into a header.
        return Response(
            body,
            media_type="text/plain" if format == "text" else "application/json",
            headers={
                "Content-Disposition": f'attachment; filename="YourPitBox-session-report.{suffix}"',
                "Cache-Control": "no-store",
            },
        )

    return router
