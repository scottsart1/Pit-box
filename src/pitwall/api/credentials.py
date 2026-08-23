"""FastAPI router for setting engine API keys from the Connection Center.

One route family serves every supported provider (OpenAI, Anthropic, DeepSeek,
Kimi, custom endpoint); `/api/v1/credentials/openai` keeps its historical shape
as the openai instance of the family. Two rules shape this router:

  * **Reads never disclose a key.** The status response carries a mask built
    by `credentials.mask_key`, never the value.
  * **Writes are loopback only**, independent of the LAN token. The dashboard
    is designed to be opened from a phone or tablet mid-session; setting a
    credential that bills the driver's account should still require being at
    the PC. A LAN client gets 403 with an explanation rather than a silent
    failure.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request, status

from .. import credentials
from ..api_models import (
    CredentialOverviewResponse,
    CredentialStatusResponse,
    CredentialTestResponse,
    CredentialUpdateRequest,
)
from ..config import settings
from ..web_security import is_loopback_host

_LOCAL_ONLY = (
    "API keys can only be changed from the computer running Your Pit Box, "
    "not over the network."
)


def _require_local(request: Request) -> None:
    client = request.client
    host = client.host if client else ""
    if not is_loopback_host(host):
        raise HTTPException(status.HTTP_403_FORBIDDEN, _LOCAL_ONLY)


def _spec_or_404(provider: str) -> credentials.ProviderSpec:
    try:
        return credentials.provider_spec(provider)
    except credentials.CredentialError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


def _status_response(
    spec: credentials.ProviderSpec, detail: str = ""
) -> CredentialStatusResponse:
    current = credentials.current_status(spec.id)
    return CredentialStatusResponse(
        configured=current.configured,
        masked=current.masked,
        source=current.source,
        detail=detail,
        provider=spec.id,
        label=spec.label,
    )


def create_credentials_router(
    on_change: Callable[[], None] | None = None,
) -> APIRouter:
    """Build the router.

    `on_change` rebinds the live provider clients so a key saved mid-session
    takes effect without restarting Your Pit Box.
    """
    router = APIRouter(prefix="/api/v1/credentials", tags=["credentials"])

    @router.get("", response_model=CredentialOverviewResponse)
    async def read_overview() -> CredentialOverviewResponse:
        """One call for the whole credential panel: every provider at once."""
        providers: dict[str, dict[str, object]] = {}
        for provider_id, spec in credentials.PROVIDERS.items():
            current = credentials.current_status(provider_id)
            entry = current.to_dict()
            entry["label"] = spec.label
            entry["key_hint"] = spec.key_hint
            providers[provider_id] = entry
        return CredentialOverviewResponse(
            providers=providers,
            active_provider=settings.llm_provider,
            fallback_provider=settings.llm_fallback_provider,
            resolved_provider=(
                settings.llm_provider
                if settings.llm_provider != "auto"
                else next(iter(settings.configured_llm_providers), "openai")
            ),
            voice_ready=bool(settings.api_key),
            custom_endpoint_ready=settings.custom_llm_ready,
        )

    @router.get("/{provider}", response_model=CredentialStatusResponse)
    async def read_status(provider: str) -> CredentialStatusResponse:
        spec = _spec_or_404(provider)
        current = credentials.current_status(spec.id)
        detail = ""
        if current.source == "environment":
            detail = (
                "This key comes from a system environment variable. Saving a "
                "new key here applies straight away, but the environment "
                "variable takes priority again the next time Your Pit Box starts. "
                f"Remove {spec.env_var} from your system environment to make a "
                "saved key stick."
            )
        return _status_response(spec, detail)

    @router.put("/{provider}", response_model=CredentialStatusResponse)
    async def update_key(
        provider: str,
        payload: CredentialUpdateRequest,
        request: Request,
    ) -> CredentialStatusResponse:
        _require_local(request)
        spec = _spec_or_404(provider)
        try:
            cleaned = credentials.validate_key(payload.api_key)
        except credentials.CredentialError as exc:
            # Literal 422: Starlette renamed the constant, and the name differs
            # across the versions this app is installed against.
            raise HTTPException(422, str(exc)) from exc

        # Check before writing, so a bad key is never persisted over a good one.
        if payload.verify:
            ok, detail = await credentials.verify_key(cleaned, provider=spec.id)
            if not ok:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, detail)

        credentials.save_key(cleaned, provider=spec.id, on_change=on_change)
        return _status_response(spec, "API key saved.")

    @router.delete("/{provider}", response_model=CredentialStatusResponse)
    async def delete_key(provider: str, request: Request) -> CredentialStatusResponse:
        _require_local(request)
        spec = _spec_or_404(provider)
        credentials.clear_key(provider=spec.id, on_change=on_change)
        return _status_response(spec, "API key removed.")

    @router.post("/{provider}/test", response_model=CredentialTestResponse)
    async def test_key(provider: str, request: Request) -> CredentialTestResponse:
        _require_local(request)
        spec = _spec_or_404(provider)
        ok, detail = await credentials.verify_key(provider=spec.id)
        return CredentialTestResponse(ok=ok, detail=detail)

    return router


__all__ = ["create_credentials_router"]
