"""Durable, non-secret Connection Center profiles.

The repository deliberately persists only the small whitelist represented by
``StoredNetworkProfile`` and ``ForwardTarget``.  It never serializes Settings,
environment variables, socket state, diagnostics, or packet contents.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .forwarding import ForwardTarget


@dataclass(frozen=True, slots=True)
class StoredNetworkProfile:
    id: str
    label: str
    bind_host: str
    udp_port: int
    pinned_adapter_id: str | None = None
    pinned_address: str | None = None
    last_working_at: str | None = None
    prior_working_adapter_ids: tuple[str, ...] = ()
    prior_working_addresses: tuple[str, ...] = ()
    targets: tuple[ForwardTarget, ...] = ()


class NetworkProfileRepository:
    """SQLite repository for one or more named local network profiles."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self._lock = asyncio.Lock()

    async def load(self, profile_id: str) -> StoredNetworkProfile | None:
        async with self._lock:
            return await asyncio.to_thread(self._load_sync, profile_id)

    async def save(self, profile: StoredNetworkProfile) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_sync, profile)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _profile_metadata(profile: StoredNetworkProfile) -> str:
        target_options = {
            target.id: {
                "allow_public": bool(target.allow_public),
                "allow_broadcast_multicast": bool(
                    target.allow_broadcast_multicast
                ),
            }
            for target in profile.targets
            if target.allow_public or target.allow_broadcast_multicast
        }
        return json.dumps(
            {
                "schema_version": 1,
                "prior_working_adapter_ids": list(
                    profile.prior_working_adapter_ids
                ),
                "prior_working_addresses": list(profile.prior_working_addresses),
                "target_options": target_options,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _packet_filter(target: ForwardTarget) -> str:
        value: str | list[int]
        if target.packet_ids is None:
            value = "all"
        else:
            value = sorted(target.packet_ids)
        return json.dumps(value, separators=(",", ":"))

    @staticmethod
    def _safe_metadata(raw: object) -> dict[str, object]:
        try:
            value = json.loads(str(raw or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _string_tuple(value: object) -> tuple[str, ...]:
        if not isinstance(value, list):
            return ()
        return tuple(str(item) for item in value if str(item).strip())

    @staticmethod
    def _target_options(
        metadata: dict[str, object], target_id: str
    ) -> dict[str, object]:
        options = metadata.get("target_options")
        if not isinstance(options, dict):
            return {}
        target_options = options.get(target_id)
        return target_options if isinstance(target_options, dict) else {}

    @staticmethod
    def _parse_packet_filter(raw: object) -> frozenset[int] | None:
        try:
            value = json.loads(str(raw or '"all"'))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if value == "all":
            return None
        if not isinstance(value, list):
            return None
        return frozenset(int(item) for item in value)

    def _load_sync(self, profile_id: str) -> StoredNetworkProfile | None:
        with self._connect() as db:
            profile = db.execute(
                """
                SELECT id, label, adapter_id, last_ipv4, bind_host, udp_port,
                       pinned, last_working_at, config_json
                  FROM network_profiles
                 WHERE id = ?
                """,
                (profile_id,),
            ).fetchone()
            if profile is None:
                return None
            metadata = self._safe_metadata(profile["config_json"])
            targets: list[ForwardTarget] = []
            for row in db.execute(
                """
                SELECT id, label, enabled, host, port, packet_filter_json,
                       forward_unknown
                  FROM forward_targets
                 WHERE profile_id = ?
                 ORDER BY created_at, id
                """,
                (profile_id,),
            ).fetchall():
                options = self._target_options(metadata, str(row["id"]))
                targets.append(
                    ForwardTarget(
                        id=str(row["id"]),
                        label=str(row["label"]),
                        enabled=bool(row["enabled"]),
                        host=str(row["host"]),
                        port=int(row["port"]),
                        packet_ids=self._parse_packet_filter(
                            row["packet_filter_json"]
                        ),
                        forward_unknown_packets=bool(row["forward_unknown"]),
                        allow_public=bool(options.get("allow_public", False)),
                        allow_broadcast_multicast=bool(
                            options.get("allow_broadcast_multicast", False)
                        ),
                    )
                )
            return StoredNetworkProfile(
                id=str(profile["id"]),
                label=str(profile["label"]),
                bind_host=str(profile["bind_host"]),
                udp_port=int(profile["udp_port"]),
                pinned_adapter_id=(
                    str(profile["adapter_id"])
                    if bool(profile["pinned"]) and profile["adapter_id"]
                    else None
                ),
                pinned_address=(
                    str(profile["last_ipv4"])
                    if bool(profile["pinned"]) and profile["last_ipv4"]
                    else None
                ),
                last_working_at=(
                    str(profile["last_working_at"])
                    if profile["last_working_at"]
                    else None
                ),
                prior_working_adapter_ids=self._string_tuple(
                    metadata.get("prior_working_adapter_ids")
                ),
                prior_working_addresses=self._string_tuple(
                    metadata.get("prior_working_addresses")
                ),
                targets=tuple(targets),
            )

    def _save_sync(self, profile: StoredNetworkProfile) -> None:
        now = datetime.now(UTC).isoformat()
        metadata = self._profile_metadata(profile)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """
                INSERT INTO network_profiles(
                    id, label, adapter_id, last_ipv4, bind_host, udp_port,
                    pinned, last_working_at, config_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    label = excluded.label,
                    adapter_id = excluded.adapter_id,
                    last_ipv4 = excluded.last_ipv4,
                    bind_host = excluded.bind_host,
                    udp_port = excluded.udp_port,
                    pinned = excluded.pinned,
                    last_working_at = excluded.last_working_at,
                    config_json = excluded.config_json,
                    updated_at = excluded.updated_at
                """,
                (
                    profile.id,
                    profile.label,
                    profile.pinned_adapter_id,
                    profile.pinned_address,
                    profile.bind_host,
                    profile.udp_port,
                    int(profile.pinned_adapter_id is not None),
                    profile.last_working_at,
                    metadata,
                    now,
                    now,
                ),
            )
            retained_ids: list[str] = []
            for target in profile.targets:
                retained_ids.append(target.id)
                db.execute(
                    """
                    INSERT INTO forward_targets(
                        id, profile_id, label, enabled, host, port,
                        packet_filter_json, forward_unknown, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        profile_id = excluded.profile_id,
                        label = excluded.label,
                        enabled = excluded.enabled,
                        host = excluded.host,
                        port = excluded.port,
                        packet_filter_json = excluded.packet_filter_json,
                        forward_unknown = excluded.forward_unknown,
                        updated_at = excluded.updated_at
                    """,
                    (
                        target.id,
                        profile.id,
                        target.label,
                        int(target.enabled),
                        target.host,
                        target.port,
                        self._packet_filter(target),
                        int(target.forward_unknown_packets),
                        now,
                        now,
                    ),
                )
            if retained_ids:
                placeholders = ",".join("?" for _ in retained_ids)
                db.execute(
                    f"""DELETE FROM forward_targets
                         WHERE profile_id = ? AND id NOT IN ({placeholders})""",
                    (profile.id, *retained_ids),
                )
            else:
                db.execute(
                    "DELETE FROM forward_targets WHERE profile_id = ?",
                    (profile.id,),
                )
            db.commit()


__all__ = ["NetworkProfileRepository", "StoredNetworkProfile"]
