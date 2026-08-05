"""Versioned storage diagnostics and non-destructive retention preview API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..storage_service import StorageService


def create_storage_router(service: StorageService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/storage", tags=["storage"])

    @router.get("/status")
    async def storage_status() -> dict[str, Any]:
        return await service.status()

    @router.get("/retention/preview")
    async def retention_preview() -> dict[str, Any]:
        return await service.preview_retention()

    return router


__all__ = ["create_storage_router"]
