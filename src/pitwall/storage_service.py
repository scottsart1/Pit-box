"""Read-only storage accounting and safe retention previews."""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .capture_service import CaptureService


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    max_bytes: int
    max_age_days: int
    minimum_free_bytes: int


def _parse_utc(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class StorageService:
    """Account catalogued artifacts without mutating or glob-deleting them."""

    def __init__(
        self,
        database_path: Path,
        data_root: Path,
        *,
        policy: RetentionPolicy,
        capture_service: CaptureService | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.data_root = Path(data_root).resolve()
        self.policy = policy
        self.capture_service = capture_service

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _rows(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT s.id, s.starred, s.status,
                       COALESCE(s.ended_at, s.started_at, s.created_at) AS session_at,
                       COALESCE((
                           SELECT SUM(tc.byte_count)
                           FROM trace_chunks tc
                           JOIN trace_manifests tm ON tm.id=tc.manifest_id
                           WHERE tm.session_id=s.id
                       ), 0) AS trace_bytes,
                       COALESCE((
                           SELECT SUM(rc.byte_count)
                           FROM raw_captures rc
                           WHERE rc.session_id=s.id
                       ), 0) AS capture_bytes
                FROM recorded_sessions s
                ORDER BY session_at ASC, s.id ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def _capture_totals(self) -> tuple[int, int]:
        """Return all catalogued and currently unassigned capture bytes."""

        with self._connect() as db:
            row = db.execute(
                """
                SELECT COALESCE(SUM(byte_count), 0) AS total_bytes,
                       COALESCE(SUM(CASE WHEN session_id IS NULL
                                         THEN byte_count ELSE 0 END), 0)
                           AS unassigned_bytes
                FROM raw_captures
                """
            ).fetchone()
        return int(row["total_bytes"]), int(row["unassigned_bytes"])

    def _active_capture_bytes(self) -> int:
        if self.capture_service is None:
            return 0
        snapshot = self.capture_service.snapshot()
        return int(snapshot.active_file_bytes) if self.capture_service.running else 0

    def _status_sync(self) -> dict[str, Any]:
        rows = self._rows()
        trace_bytes = sum(int(row["trace_bytes"] or 0) for row in rows)
        catalogued_capture_bytes, unassigned_capture_bytes = self._capture_totals()
        active_capture_bytes = self._active_capture_bytes()
        capture_bytes = catalogued_capture_bytes + active_capture_bytes
        database_bytes = (
            self.database_path.stat().st_size if self.database_path.exists() else 0
        )
        disk = shutil.disk_usage(self.data_root)
        warnings: list[str] = []
        if disk.free < self.policy.minimum_free_bytes:
            warnings.append("free_disk_below_configured_minimum")
        if trace_bytes + capture_bytes > self.policy.max_bytes:
            warnings.append("capture_budget_exceeded")
        return {
            "schema_version": 1,
            "database_bytes": database_bytes,
            "trace_bytes": trace_bytes,
            "capture_bytes": capture_bytes,
            "catalogued_capture_bytes": catalogued_capture_bytes,
            "active_capture_bytes": active_capture_bytes,
            "unassigned_capture_bytes": unassigned_capture_bytes,
            "managed_bytes": trace_bytes + capture_bytes,
            "disk_free_bytes": disk.free,
            "disk_total_bytes": disk.total,
            "session_count": len(rows),
            "starred_session_count": sum(bool(row["starred"]) for row in rows),
            "policy": {
                "max_bytes": self.policy.max_bytes,
                "max_age_days": self.policy.max_age_days,
                "minimum_free_bytes": self.policy.minimum_free_bytes,
            },
            "warnings": warnings,
        }

    async def status(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._status_sync)

    def _preview_sync(self, now: datetime) -> dict[str, Any]:
        rows = self._rows()
        trace_bytes = sum(int(row["trace_bytes"] or 0) for row in rows)
        catalogued_capture_bytes, unassigned_capture_bytes = self._capture_totals()
        active_capture_bytes = self._active_capture_bytes()
        managed = trace_bytes + catalogued_capture_bytes + active_capture_bytes
        selected: dict[str, dict[str, Any]] = {}
        for row in rows:
            if bool(row["starred"]) or str(row["status"]) == "recording":
                continue
            timestamp = _parse_utc(row["session_at"])
            age_days = (now - timestamp).total_seconds() / 86_400 if timestamp else None
            if age_days is not None and age_days > self.policy.max_age_days:
                selected[str(row["id"])] = {
                    **row,
                    "age_days": round(age_days, 1),
                    "reasons": ["older_than_policy"],
                }

        remaining = managed - sum(
            int(row["trace_bytes"] or 0) + int(row["capture_bytes"] or 0)
            for row in selected.values()
        )
        if remaining > self.policy.max_bytes:
            for row in rows:
                key = str(row["id"])
                if bool(row["starred"]) or str(row["status"]) == "recording":
                    continue
                if key in selected:
                    continue
                candidate = selected.setdefault(
                    key,
                    {**row, "age_days": None, "reasons": []},
                )
                if "over_size_budget" not in candidate["reasons"]:
                    candidate["reasons"].append("over_size_budget")
                remaining -= int(row["trace_bytes"] or 0) + int(
                    row["capture_bytes"] or 0
                )
                if remaining <= self.policy.max_bytes:
                    break

        candidates = []
        for item in selected.values():
            trace_bytes = int(item["trace_bytes"] or 0)
            capture_bytes = int(item["capture_bytes"] or 0)
            candidates.append(
                {
                    "session_id": str(item["id"]),
                    "session_at": item["session_at"],
                    "age_days": item["age_days"],
                    "trace_bytes": trace_bytes,
                    "capture_bytes": capture_bytes,
                    "bytes_affected": trace_bytes + capture_bytes,
                    "reasons": list(item["reasons"]),
                }
            )
        return {
            "schema_version": 1,
            "generated_at": now.isoformat(),
            "automatic_deletion": False,
            "requires_per_session_confirmation": True,
            "managed_bytes_before": managed,
            "managed_bytes_after_estimate": max(0, remaining),
            "bytes_affected": sum(item["bytes_affected"] for item in candidates),
            "candidates": candidates,
            "protected": {
                "starred": sum(bool(row["starred"]) for row in rows),
                "recording": sum(str(row["status"]) == "recording" for row in rows),
                "unassigned_capture_bytes": unassigned_capture_bytes,
                "active_capture_bytes": active_capture_bytes,
            },
        }

    async def preview_retention(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._preview_sync, datetime.now(UTC))


__all__ = ["RetentionPolicy", "StorageService"]
