"""Local, same-origin controls for optional usage reporting."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, StrictBool

from ..usage_reporting import UsageReporting
from ..web_security import _authority, _same_origin, is_loopback_host


class UsageChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


def create_usage_router(service: UsageReporting) -> APIRouter:
    def guard(request: Request, response: Response) -> None:
        response.headers["cache-control"] = "no-store"
        authority = _authority(request.headers.get("host", ""), request.url.scheme)
        if not request.client or not is_loopback_host(request.client.host) or not authority or not is_loopback_host(authority[0]):
            raise HTTPException(403, "Use this device's own dashboard for usage reporting")
        if request.headers.get("sec-fetch-site", "").lower() == "cross-site" or not _same_origin(dict(request.headers), request.scope):
            raise HTTPException(403, "Use the app to change this choice")

    router = APIRouter(prefix="/api/v1/usage", tags=["privacy"], dependencies=[Depends(guard)])

    @router.get("")
    async def status():
        return service.status()

    @router.post("")
    async def choose(choice: UsageChoice, request: Request):
        try:
            return await service.set_enabled(choice.enabled)
        except OSError:
            raise HTTPException(503, "Choice was not saved. Free some space and try again.") from None

    @router.post("/active")
    async def active(request: Request):
        service.record("app_used")
        return {"ok": True}

    return router
