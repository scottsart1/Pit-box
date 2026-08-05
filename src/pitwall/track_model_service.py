"""Persisted track-model construction over recorded full-field motion traces.

The geometry algorithms remain pure in :mod:`pitwall.telemetry.track_model`.
This module owns only source selection, durable file/catalog writes, and bounded
API projections.  A failed quality gate is deliberately returned to the caller
without activating or cataloguing a model.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import struct
import uuid
import zlib
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .telemetry.segments import SegmentModel, build_segment_model, make_segment
from .telemetry.track_model import (
    TrackBuildConfig,
    TrackModel,
    TrackModelOutcome,
    TrackQualityReport,
    Trajectory,
    TrajectoryQuality,
    build_track_model,
)
from .trace_store import TraceSlice, TraceStore, TraceStoreError

_MODEL_MAGIC = b"PWM42"
_MODEL_FORMAT_VERSION = 1
_MODEL_HEADER = struct.Struct("<5sBII32s")
_MAX_MODEL_BYTES = 64 * 1024 * 1024
_DEFAULT_REVIEW_SEGMENTS = 10


class TrackModelServiceError(RuntimeError):
    code = "track_model_error"


class SessionNotFoundError(TrackModelServiceError):
    code = "session_not_found"


class TrackIdentityUnavailableError(TrackModelServiceError):
    code = "track_identity_unavailable"


class TrackModelNotFoundError(TrackModelServiceError):
    code = "track_model_not_found"


class TrackModelCorruptError(TrackModelServiceError):
    code = "track_model_corrupt"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized[:40] or "layout"


def _quality_dict(quality: TrackQualityReport) -> dict[str, Any]:
    value = asdict(quality)
    value["outcome"] = quality.outcome.value
    return value


def _quality_from_dict(value: dict[str, Any]) -> TrackQualityReport:
    trajectories = tuple(
        TrajectoryQuality(**item) for item in value.get("trajectories", [])
    )
    return TrackQualityReport(
        outcome=TrackModelOutcome(str(value["outcome"])),
        publishable=bool(value["publishable"]),
        quality_score=float(value["quality_score"]),
        clean_trajectories=int(value["clean_trajectories"]),
        rejected_trajectories=int(value["rejected_trajectories"]),
        coverage_ratio=float(value["coverage_ratio"]),
        length_m=float(value["length_m"]) if value.get("length_m") is not None else None,
        closure_error_m=(
            float(value["closure_error_m"])
            if value.get("closure_error_m") is not None
            else None
        ),
        source_closure_m=(
            float(value["source_closure_m"])
            if value.get("source_closure_m") is not None
            else None
        ),
        median_residual_m=(
            float(value["median_residual_m"])
            if value.get("median_residual_m") is not None
            else None
        ),
        p95_residual_m=(
            float(value["p95_residual_m"])
            if value.get("p95_residual_m") is not None
            else None
        ),
        continuity_score=float(value["continuity_score"]),
        max_heading_step_deg=(
            float(value["max_heading_step_deg"])
            if value.get("max_heading_step_deg") is not None
            else None
        ),
        self_crossings=int(value["self_crossings"]),
        reasons=tuple(str(item) for item in value.get("reasons", [])),
        trajectories=trajectories,
    )


def _geometry_checksum(model: TrackModel) -> str:
    metadata = json.dumps(
        {
            "track_key": model.track_key,
            "version": model.version,
            "algorithm_version": model.algorithm_version,
            "points": int(model.centerline.shape[0]),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    normalized = np.round(model.centerline, 6).astype("<f8", copy=False).tobytes()
    return hashlib.sha256(metadata + normalized).hexdigest()


def _model_payload(model: TrackModel) -> bytes:
    value = {
        "format": "Pit Wall track model",
        "schema_version": _MODEL_FORMAT_VERSION,
        "id": model.id,
        "track_key": model.track_key,
        "version": model.version,
        "algorithm_version": model.algorithm_version,
        "length_m": model.length_m,
        "checksum": model.checksum,
        "centerline": model.centerline.tolist(),
        "cumulative_s_m": model.cumulative_s_m.tolist(),
        "tangents": model.tangents.tolist(),
        "normals": model.normals.tolist(),
        "quality": _quality_dict(model.quality),
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _encode_model(model: TrackModel) -> bytes:
    raw = _model_payload(model)
    compressed = zlib.compress(raw, level=6)
    return _MODEL_HEADER.pack(
        _MODEL_MAGIC,
        _MODEL_FORMAT_VERSION,
        len(compressed),
        len(raw),
        hashlib.sha256(raw).digest(),
    ) + compressed


def _decode_model(data: bytes) -> TrackModel:
    if len(data) < _MODEL_HEADER.size:
        raise TrackModelCorruptError("track model header is truncated")
    magic, version, compressed_size, raw_size, digest = _MODEL_HEADER.unpack_from(data)
    if magic != _MODEL_MAGIC or version != _MODEL_FORMAT_VERSION:
        raise TrackModelCorruptError("unsupported Pit Wall track model format")
    if raw_size > _MAX_MODEL_BYTES or compressed_size > _MAX_MODEL_BYTES:
        raise TrackModelCorruptError("track model declares an unsafe size")
    compressed = data[_MODEL_HEADER.size :]
    if len(compressed) != compressed_size:
        raise TrackModelCorruptError("track model payload length does not match its header")
    try:
        raw = zlib.decompress(compressed)
    except zlib.error as exc:
        raise TrackModelCorruptError("track model payload cannot be decompressed") from exc
    if len(raw) != raw_size or hashlib.sha256(raw).digest() != digest:
        raise TrackModelCorruptError("track model payload checksum failed")
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise TypeError("payload is not an object")
        model = TrackModel(
            id=str(value["id"]),
            track_key=str(value["track_key"]),
            version=int(value["version"]),
            algorithm_version=str(value["algorithm_version"]),
            centerline=np.asarray(value["centerline"], dtype=np.float64),
            cumulative_s_m=np.asarray(value["cumulative_s_m"], dtype=np.float64),
            tangents=np.asarray(value["tangents"], dtype=np.float64),
            normals=np.asarray(value["normals"], dtype=np.float64),
            length_m=float(value["length_m"]),
            quality=_quality_from_dict(value["quality"]),
            checksum=str(value["checksum"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TrackModelCorruptError("track model metadata is invalid") from exc
    if _geometry_checksum(model) != model.checksum:
        raise TrackModelCorruptError("track model geometry checksum failed")
    return model


class TrackModelService:
    """Build and serve quality-gated track models from saved lap traces."""

    def __init__(
        self,
        database_path: str | Path,
        trace_store: TraceStore,
        data_root: str | Path,
        *,
        build_config: TrackBuildConfig | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.trace_store = trace_store
        self.data_root = Path(data_root).resolve()
        self.model_root = (self.data_root / "track-models").resolve()
        if not self.model_root.is_relative_to(self.data_root):
            raise ValueError("track model root escapes the configured data root")
        self.model_root.mkdir(parents=True, exist_ok=True)
        self.build_config = build_config or TrackBuildConfig()
        self._lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _resolve_relative(self, relative_path: str) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise TrackModelCorruptError("track model catalog path is unsafe")
        resolved = (self.data_root / relative).resolve()
        if not resolved.is_relative_to(self.model_root):
            raise TrackModelCorruptError("track model path escapes its configured root")
        return resolved

    def _relative_model_path(
        self,
        track_id: int,
        layout_signature: str,
        model: TrackModel,
    ) -> str:
        layout_hash = hashlib.sha256(layout_signature.encode()).hexdigest()[:8]
        directory = f"{track_id}-{_slug(layout_signature)}-{layout_hash}"
        return (
            Path("track-models")
            / directory
            / f"v{model.version}-{model.checksum[:12]}.pwm"
        ).as_posix()

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _session_row(self, session_id: str) -> sqlite3.Row:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM recorded_sessions WHERE id=?", (str(session_id),)
            ).fetchone()
        if row is None:
            raise SessionNotFoundError(f"session '{session_id}' does not exist")
        if row["track_id"] is None:
            raise TrackIdentityUnavailableError(
                "the session does not contain a track identifier"
            )
        return row

    @staticmethod
    def _layout_signature(session: sqlite3.Row) -> str:
        supplied = str(session["track_layout_signature"] or "").strip()
        return supplied or f"track-{int(session['track_id'])}-layout-unknown"

    def _active_row(self, track_id: int, layout_signature: str) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute(
                """
                SELECT * FROM track_models
                WHERE track_id=? AND layout_signature=? AND active=1
                ORDER BY model_version DESC LIMIT 1
                """,
                (track_id, layout_signature),
            ).fetchone()

    def _next_version(self, track_id: int, layout_signature: str) -> int:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT COALESCE(MAX(model_version), 0) + 1
                FROM track_models WHERE track_id=? AND layout_signature=?
                """,
                (track_id, layout_signature),
            ).fetchone()
        return int(row[0])

    def _candidate_rows(self, session_id: str, limit: int) -> list[sqlite3.Row]:
        with self._connect() as db:
            return db.execute(
                """
                SELECT l.id AS lap_id, l.trace_manifest_id, l.quality_score,
                       l.coverage_ratio, c.is_player
                FROM recorded_laps l
                JOIN session_cars c ON c.id=l.session_car_id
                JOIN trace_manifests tm ON tm.id=l.trace_manifest_id
                WHERE c.session_id=? AND l.valid=1 AND l.pit_context=0
                  AND l.flag_context=0 AND tm.state='ready'
                ORDER BY COALESCE(l.quality_score, 0) DESC,
                         COALESCE(l.coverage_ratio, 0) DESC,
                         c.is_player DESC,
                         COALESCE(l.lap_time_ms, 2147483647), l.id
                LIMIT ?
                """,
                (session_id, max(limit * 2, limit)),
            ).fetchall()

    def _load_trajectories(
        self, session_id: str, limit: int
    ) -> tuple[list[Trajectory], list[dict[str, str]]]:
        trajectories: list[Trajectory] = []
        rejected: list[dict[str, str]] = []
        for row in self._candidate_rows(session_id, limit):
            lap_id = str(row["lap_id"])
            manifest_id = str(row["trace_manifest_id"])
            try:
                trace = self._read_world_trace(manifest_id)
                x = trace.series["world_x"]
                z = trace.series["world_z"]
                valid = (
                    trace.axis_available
                    & x.available
                    & z.available
                    & np.isfinite(x.values)
                    & np.isfinite(z.values)
                )
                points = np.column_stack((x.values, z.values)).astype(
                    np.float64, copy=False
                )
                trajectories.append(Trajectory(lap_id, points, valid_mask=valid))
            except (
                FileNotFoundError,
                KeyError,
                OSError,
                TraceStoreError,
                ValueError,
            ) as exc:
                rejected.append({"lap_id": lap_id, "reason": str(exc)})
            if len(trajectories) >= limit:
                break
        return trajectories, rejected

    def _read_world_trace(self, manifest_id: str) -> TraceSlice:
        """Read honest world geometry across current and legacy typed layouts.

        Full-field archives use a dedicated motion group. Existing player
        archives put world coordinates in telemetry, while early development
        manifests may use another single group. Missing groups are compatible;
        corrupt chunks are intentionally not masked by trying another source.
        """

        fields = ("world_x", "world_z")
        for sample_group in ("motion", "telemetry"):
            try:
                return self.trace_store.read_range(
                    manifest_id,
                    fields=fields,
                    sample_group=sample_group,
                )
            except KeyError:
                continue
        return self.trace_store.read_range(manifest_id, fields=fields)

    @staticmethod
    def _default_segments(model: TrackModel, count: int) -> SegmentModel:
        width = model.length_m / count
        segments = [
            make_segment(
                model.id,
                ordinal,
                f"Review {ordinal + 1}",
                ordinal * width,
                model.length_m if ordinal == count - 1 else (ordinal + 1) * width,
                confidence=max(0.35, min(0.85, model.quality.quality_score * 0.85)),
                source="equal_distance_review_v1",
            )
            for ordinal in range(count)
        ]
        return build_segment_model(
            model.id,
            1,
            segments,
            source="equal_distance_review_v1",
        )

    def _persist(
        self,
        track_id: int,
        layout_signature: str,
        model: TrackModel,
        segment_model: SegmentModel,
    ) -> None:
        relative = self._relative_model_path(track_id, layout_signature, model)
        path = self._resolve_relative(relative)
        self._atomic_write(path, _encode_model(model))
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "UPDATE track_models SET active=0 WHERE track_id=? AND layout_signature=?",
                    (track_id, layout_signature),
                )
                db.execute(
                    """
                    INSERT INTO track_models(
                        id, track_id, layout_signature, model_version,
                        algorithm_version, relative_path, length_m,
                        quality_score, checksum, active, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        model.id,
                        track_id,
                        layout_signature,
                        model.version,
                        model.algorithm_version,
                        relative,
                        model.length_m,
                        model.quality.quality_score,
                        model.checksum,
                        _utc_now(),
                    ),
                )
                db.execute(
                    "UPDATE segment_models SET active=0 WHERE track_model_id=?",
                    (model.id,),
                )
                db.execute(
                    """
                    INSERT INTO segment_models(
                        id, track_model_id, version, source, checksum, active, created_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        segment_model.id,
                        model.id,
                        segment_model.version,
                        segment_model.source,
                        segment_model.checksum,
                        _utc_now(),
                    ),
                )
                for segment in segment_model.segments:
                    phase_json = json.dumps(
                        {
                            "brake": None,
                            "turn_in": None,
                            "apex": None,
                            "exit": None,
                            "source": segment.source,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    db.execute(
                        """
                        INSERT INTO segments(
                            id, segment_model_id, ordinal, label, start_m, end_m,
                            phase_json, direction, confidence
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            segment.id,
                            segment_model.id,
                            segment.ordinal,
                            segment.label,
                            segment.start_m,
                            segment.end_m,
                            phase_json,
                            segment.direction,
                            segment.confidence,
                        ),
                    )
                db.commit()
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def _row_summary(self, row: sqlite3.Row, *, file_state: str = "ready") -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "track_id": int(row["track_id"]),
            "layout_signature": str(row["layout_signature"]),
            "model_version": int(row["model_version"]),
            "algorithm_version": str(row["algorithm_version"]),
            "length_m": float(row["length_m"]),
            "quality_score": float(row["quality_score"]),
            "checksum": str(row["checksum"]),
            "active": bool(row["active"]),
            "created_at": str(row["created_at"]),
            "file_state": file_state,
        }

    def _read_row_model(self, row: sqlite3.Row) -> TrackModel:
        path = self._resolve_relative(str(row["relative_path"]))
        try:
            model = _decode_model(path.read_bytes())
        except FileNotFoundError as exc:
            raise TrackModelCorruptError("catalogued track model file is missing") from exc
        if (
            model.id != row["id"]
            or model.checksum != row["checksum"]
            or model.version != int(row["model_version"])
            or model.algorithm_version != row["algorithm_version"]
        ):
            raise TrackModelCorruptError("track model file does not match its catalog row")
        return model

    def _segment_projection(self, model_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT * FROM segment_models
                WHERE track_model_id=? AND active=1
                ORDER BY version DESC LIMIT 1
                """,
                (model_id,),
            ).fetchone()
            if row is None:
                return None
            segments = [
                {
                    **dict(item),
                    "phase": json.loads(str(item["phase_json"])),
                }
                for item in db.execute(
                    "SELECT * FROM segments WHERE segment_model_id=? ORDER BY ordinal",
                    (str(row["id"]),),
                ).fetchall()
            ]
        for item in segments:
            item.pop("phase_json", None)
        return {
            "id": str(row["id"]),
            "version": int(row["version"]),
            "source": str(row["source"]),
            "checksum": str(row["checksum"]),
            "segments": segments,
        }

    async def build_for_session(
        self,
        session_id: str,
        *,
        force: bool = False,
        max_laps: int = 12,
        review_segments: int = _DEFAULT_REVIEW_SEGMENTS,
    ) -> dict[str, Any]:
        if not 3 <= int(max_laps) <= 24:
            raise ValueError("max_laps must be between 3 and 24")
        if not 8 <= int(review_segments) <= 15:
            raise ValueError("review_segments must be between 8 and 15")
        async with self._lock:
            return await asyncio.to_thread(
                self._build_for_session_sync,
                str(session_id),
                bool(force),
                int(max_laps),
                int(review_segments),
            )

    def _build_for_session_sync(
        self,
        session_id: str,
        force: bool,
        max_laps: int,
        review_segments: int,
    ) -> dict[str, Any]:
        session = self._session_row(session_id)
        track_id = int(session["track_id"])
        layout_signature = self._layout_signature(session)
        active = self._active_row(track_id, layout_signature)
        if active is not None and not force:
            model = self._read_row_model(active)
            return {
                "schema_version": 1,
                "status": "reused",
                "session_id": session_id,
                "model": self._row_summary(active),
                "quality": _quality_dict(model.quality),
                "segment_model": self._segment_projection(model.id),
                "source_lap_ids": [
                    item.trajectory_id for item in model.quality.trajectories
                ],
                "source_rejections": [],
            }

        trajectories, source_rejections = self._load_trajectories(session_id, max_laps)
        version = self._next_version(track_id, layout_signature)
        track_key = f"f1_2026:{track_id}:{layout_signature}"
        result = build_track_model(
            track_key,
            version,
            trajectories,
            config=self.build_config,
        )
        if result.outcome is not TrackModelOutcome.PUBLISHED or result.model is None:
            return {
                "schema_version": 1,
                "status": TrackModelOutcome.MAP_CALIBRATION_REQUIRED.value,
                "session_id": session_id,
                "model": None,
                "quality": _quality_dict(result.quality),
                "segment_model": None,
                "source_lap_ids": [item.id for item in trajectories],
                "source_rejections": source_rejections,
            }

        model = result.model
        segment_model = self._default_segments(model, review_segments)
        self._persist(track_id, layout_signature, model, segment_model)
        with self._connect() as db:
            row = db.execute("SELECT * FROM track_models WHERE id=?", (model.id,)).fetchone()
        assert row is not None
        response = {
            "schema_version": 1,
            "status": TrackModelOutcome.PUBLISHED.value,
            "session_id": session_id,
            "model": self._row_summary(row),
            "quality": _quality_dict(model.quality),
            "segment_model": self._segment_projection(model.id),
            "source_lap_ids": [item.id for item in trajectories],
            "source_rejections": source_rejections,
        }
        return response

    async def session_status(self, session_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._session_status_sync, str(session_id))

    def _session_status_sync(self, session_id: str) -> dict[str, Any]:
        session = self._session_row(session_id)
        track_id = int(session["track_id"])
        layout_signature = self._layout_signature(session)
        active = self._active_row(track_id, layout_signature)
        with self._connect() as db:
            candidate_count = int(
                db.execute(
                    """
                    SELECT COUNT(*) FROM recorded_laps l
                    JOIN session_cars c ON c.id=l.session_car_id
                    JOIN trace_manifests tm ON tm.id=l.trace_manifest_id
                    WHERE c.session_id=? AND l.valid=1 AND l.pit_context=0
                      AND l.flag_context=0 AND tm.state='ready'
                    """,
                    (session_id,),
                ).fetchone()[0]
            )
        model_summary = None
        segment_model = None
        if active is not None:
            file_state = "ready"
            try:
                self._read_row_model(active)
            except TrackModelCorruptError:
                file_state = "corrupt"
            model_summary = self._row_summary(active, file_state=file_state)
            segment_model = self._segment_projection(str(active["id"]))
        return {
            "schema_version": 1,
            "session_id": session_id,
            "track_id": track_id,
            "layout_signature": layout_signature,
            "status": (
                "ready"
                if model_summary is not None and model_summary["file_state"] == "ready"
                else "corrupt"
                if model_summary is not None
                else "map_calibration_required"
            ),
            "candidate_laps": candidate_count,
            "minimum_clean_laps": self.build_config.min_clean_trajectories,
            "model": model_summary,
            "segment_model": segment_model,
        }

    async def get_model(
        self, model_id: str, *, max_points: int = 1600
    ) -> dict[str, Any]:
        if not 32 <= int(max_points) <= 20_000:
            raise ValueError("max_points must be between 32 and 20000")
        return await asyncio.to_thread(
            self._get_model_sync, str(model_id), int(max_points)
        )

    def _get_model_sync(self, model_id: str, max_points: int) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM track_models WHERE id=?", (model_id,)).fetchone()
        if row is None:
            raise TrackModelNotFoundError(f"track model '{model_id}' does not exist")
        model = self._read_row_model(row)
        count = model.centerline.shape[0]
        if count > max_points:
            indices = np.linspace(0, count - 1, max_points, dtype=int)
        else:
            indices = np.arange(count)
        points = [
            {
                "s_m": float(model.cumulative_s_m[index]),
                "x_m": float(model.centerline[index, 0]),
                "z_m": float(model.centerline[index, 1]),
            }
            for index in indices
        ]
        return {
            "schema_version": 1,
            "model": self._row_summary(row),
            "quality": _quality_dict(model.quality),
            "geometry": {
                "coordinate_system": "game_world_xz",
                "closed": True,
                "source_points": count,
                "returned_points": len(points),
                "points": points,
            },
            "segment_model": self._segment_projection(model.id),
        }
