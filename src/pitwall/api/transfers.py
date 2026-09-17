"""Local management API for the separate, restricted HTTPS peer listener."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from ..peer_transfer import DEFAULT_PORT, PeerTransferService, TransferError
from ..web_security import is_loopback_host, _same_origin, _authority


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartRequest(_Input):
    advertise_host: str | None = Field(default=None, max_length=45)
    port: int = Field(default=DEFAULT_PORT, ge=1024, le=65535)
    device_name: str | None = Field(default=None, max_length=80)


class PairRequest(_Input):
    invitation: str = Field(min_length=20, max_length=4096)
    device_name: str | None = Field(default=None, max_length=80)


class PullRequest(_Input):
    session_ids: list[str] = Field(min_length=1, max_length=500)


def create_transfer_router(service: PeerTransferService, *, usage_record=None) -> APIRouter:
    async def local_only(request: Request, response: Response) -> None:
        response.headers["Cache-Control"] = "no-store"
        if not request.client or not is_loopback_host(request.client.host):
            raise HTTPException(403, detail={"code": "local_only", "message": "Manage paired devices from this device's own dashboard."})
        headers = dict(request.headers)
        authority = _authority(headers.get("host", ""), request.url.scheme)
        if (not authority or not is_loopback_host(authority[0])
                or headers.get("sec-fetch-site", "").casefold() == "cross-site"
                or not _same_origin(headers, request.scope)):
            raise HTTPException(403, detail={"code": "same_origin_required", "message": "Use this device's own Pit Wall dashboard to manage transfers."})
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("content-type", "").startswith("text/plain"):
            raise HTTPException(415, detail="Use the transfer controls in the dashboard.")

    router = APIRouter(prefix="/api/v1/transfers", tags=["transfers"], dependencies=[Depends(local_only)])

    async def invoke(function: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await run_in_threadpool(function, *args, **kwargs)
        except TransferError as exc:
            raise HTTPException(exc.status, detail={"code": exc.code, "message": str(exc)}) from exc

    @router.get("/status")
    async def status() -> dict[str, Any]:
        return service.status()

    @router.post("/start")
    async def start(body: StartRequest) -> dict[str, Any]:
        return await invoke(service.start, **body.model_dump())

    @router.post("/stop")
    async def stop() -> dict[str, Any]:
        return await invoke(service.stop)

    @router.post("/invite")
    async def invite() -> dict[str, Any]:
        return await invoke(service.invite)

    @router.post("/pair")
    async def pair(body: PairRequest) -> dict[str, Any]:
        return await invoke(service.pair, **body.model_dump())

    @router.delete("/peers/{peer_id}")
    async def revoke(peer_id: str) -> dict[str, Any]:
        return await invoke(service.revoke, peer_id)

    @router.get("/peers/{peer_id}/sessions")
    async def sessions(peer_id: str) -> dict[str, Any]:
        return await invoke(service.peer_sessions, peer_id)

    @router.post("/peers/{peer_id}/pull")
    async def pull(peer_id: str, body: PullRequest) -> dict[str, Any]:
        result = await invoke(service.pull, peer_id, body.session_ids)
        if usage_record:
            usage_record("transfer")
        return result

    @router.get("/jobs/{job_id}")
    async def job(job_id: str) -> dict[str, Any]:
        return await invoke(service.job, job_id)

    @router.post("/jobs/{job_id}/retry")
    async def retry(job_id: str) -> dict[str, Any]:
        return await invoke(service.retry, job_id)

    return router


__all__ = ["create_transfer_router"]
