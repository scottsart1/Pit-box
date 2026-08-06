"""FastAPI router for setting the OpenAI API key from the Connection Center.

Two rules shape this router:

  * **Reads never disclose the key.** The status response carries a mask built
    by `credentials.mask_key`, never the value.
  * **Writes are loopback only**, independent of the LAN token. The dashboard
    is designed to be opened from a phone or tablet mid-session; setting the
    credential that bills the driver's OpenAI account should still require
    being at the PC. A LAN client gets 403 with an explanation rather than a
    silent failure.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request, status

from .. import credentials
from ..api_models import (
    CredentialStatusResponse,
    CredentialTestResponse,
    CredentialUpdateRequest,
)
from ..web_security import is_loopback_host

_LOCAL_ONLY = (
    "The OpenAI API key can only be changed from the computer running Pit "
    "Wall, not over the network."
)


def _require_local(request: Request) -> None:
    client = request.client
    host = client.host if client else ""
    if not is_loopback_host(host):
        raise HTTPException(status.HTTP_403_FORBIDDEN, _LOCAL_ONLY)


def _status_response(detail: str = "") -> CredentialStatusResponse:
    current = credentials.current_status()
    return CredentialStatusResponse(
        configured=current.configured,
        masked=current.masked,
        source=current.source,
        detail=detail,
    )


def create_credentials_router(
    on_change: Callable[[], None] | None = None,
) -> APIRouter:
    """Build the router.

    `on_change` rebinds the live OpenAI clients so a key saved mid-session
    takes effect without restarting Pit Wall.
    """
    router = APIRouter(prefix="/api/v1/credentials", tags=["credentials"])

    @router.get("/openai", response_model=CredentialStatusResponse)
    async def read_status() -> CredentialStatusResponse:
        current = credentials.current_status()
        detail = ""
        if current.source == "environment":
            detail = (
                "This key comes from a system environment variable. Saving a "
                "new key here applies straight away, but the environment "
                "variable takes priority again the next time Pit Wall starts. "
                "Remove OPENAI_API_KEY from your system environment to make a "
                "saved key stick."
            )
        return _status_response(detail)

    @router.put("/openai", response_model=CredentialStatusResponse)
    async def update_key(
        payload: CredentialUpdateRequest,
        request: Request,
    ) -> CredentialStatusResponse:
        _require_local(request)
        try:
            cleaned = credentials.validate_key(payload.api_key)
        except credentials.CredentialError as exc:
            # Literal 422: Starlette renamed the constant, and the name differs
            # across the versions this app is installed against.
            raise HTTPException(422, str(exc)) from exc

        # Check before writing, so a bad key is never persisted over a good one.
        if payload.verify:
            ok, detail = await credentials.verify_key(cleaned)
            if not ok:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail)

        credentials.save_key(cleaned, on_change=on_change)
        return _status_response("API key saved.")

    @router.delete("/openai", response_model=CredentialStatusResponse)
    async def delete_key(request: Request) -> CredentialStatusResponse:
        _require_local(request)
        credentials.clear_key(on_change=on_change)
        return _status_response("API key removed.")

    @router.post("/openai/test", response_model=CredentialTestResponse)
    async def test_key(request: Request) -> CredentialTestResponse:
        _require_local(request)
        ok, detail = await credentials.verify_key()
        return CredentialTestResponse(ok=ok, detail=detail)

    return router


__all__ = ["create_credentials_router"]
