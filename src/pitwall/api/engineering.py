"""Saved run analysis uses explicit canonical session identifiers."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from ..catalog import session_id
from ..engineering import (
    EngineeringService,
    compare_runs,
    report_text,
    strategic_rivals,
)
from ..state import StateStore


class CompareRunsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a: str = Field(min_length=1, max_length=180)
    b: str = Field(min_length=1, max_length=180)


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
        if body.a == body.b:
            raise HTTPException(422, "Choose two different runs.")
        report = await get_report(key)
        runs = {run["id"]: run for run in report["runs"]}
        if body.a not in runs or body.b not in runs:
            raise HTTPException(404, "Both runs must belong to the selected session.")
        import asyncio

        return await asyncio.to_thread(compare_runs, runs[body.a], runs[body.b])

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
