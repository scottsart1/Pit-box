"""Same-device controls for a read-only release checker."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, StrictBool

from ..update_service import UpdateService
from ..web_security import _authority, _same_origin, is_loopback_host


class UpdateChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool | None = None
    dismiss: StrictBool = False


def create_updates_router(service: UpdateService) -> APIRouter:
    def guard(request: Request, response: Response) -> None:
        response.headers["cache-control"] = "no-store"
        authority = _authority(request.headers.get("host", ""), request.url.scheme)
        if not request.client or not is_loopback_host(request.client.host) or not authority or not is_loopback_host(authority[0]):
            raise HTTPException(403, "Use this device's own dashboard for update checks")
        if request.headers.get("sec-fetch-site", "").lower() == "cross-site" or not _same_origin(dict(request.headers), request.scope):
            raise HTTPException(403, "Use the app for update checks")

    router = APIRouter(prefix="/api/v1/updates", tags=["updates"], dependencies=[Depends(guard)])

    @router.get("")
    async def status():
        return service.status()

    @router.post("/check")
    async def check():
        return await service.check()

    @router.post("")
    async def preferences(choice: UpdateChoice):
        try:
            return await service.preferences(enabled=choice.enabled, dismiss=choice.dismiss)
        except OSError:
            raise HTTPException(503, "Update preferences were not saved") from None

    return router
