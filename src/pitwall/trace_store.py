"""Atomic, typed and bounded local trace storage.

SQLite remains the natural catalog for sessions and derived results, but it is a
poor high-rate array store.  ``TraceStore`` writes numeric columns to compressed,
self-describing chunks and publishes a small manifest only after every chunk is
durable.  The API is synchronous by design; callers on an event loop should run
disk operations in their existing worker/thread boundary.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import struct
import threading
import time
import uuid
import zlib
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

TRACE_MAGIC = b"PWTRC42\0"
TRACE_FORMAT_VERSION = 1
MANIFEST_FORMAT_VERSION = 1

_TRACE_HEADER = struct.Struct("<8sHHIIIII")
_MAX_METADATA_BYTES = 8 * 1024 * 1024
_MAX_RAW_BYTES = 512 * 1024 * 1024
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_ALLOWED_DTYPES = {
    "bool": np.dtype("?"),
    "float32": np.dtype("<f4"),
    "float64": np.dtype("<f8"),
    "int8": np.dtype("i1"),
    "int16": np.dtype("<i2"),
    "int32": np.dtype("<i4"),
    "int64": np.dtype("<i8"),
    "uint8": np.dtype("u1"),
    "uint16": np.dtype("<u2"),
    "uint32": np.dtype("<u4"),
    "uint64": np.dtype("<u8"),
}


class TraceStoreError(RuntimeError):
    """Base error for unsafe, corrupt or incompatible trace operations."""


class TraceFormatError(TraceStoreError):
    """Raised when a trace chunk or manifest fails validation."""


class TraceManifestMissing(TraceStoreError, FileNotFoundError):
    """Raised when a catalogued manifest has no file on disk.

    Distinct from a format error: the record is intact and the session stays
    listable, but this lap's telemetry cannot be read until it is rebuilt.

    It is deliberately still a ``FileNotFoundError`` so existing recovery and
    reconciliation paths that catch the OS error keep working; the added type
    only lets the API turn it into a diagnosable response instead of a 500.
    """


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _crc32(value: bytes) -> int:
    return zlib.crc32(value) & 0xFFFFFFFF


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_identifier(value: str, label: str) -> str:
    text = str(value)
    if not _SAFE_ID.fullmatch(text):
        raise ValueError(f"{label} must contain only letters, numbers, '.', '_' or '-'")
    return text


def _dtype_from_metadata(value: Any) -> np.dtype[Any] | None:
    if value is None:
        return None
    key = str(value).lower().lstrip("<>=|")
    aliases = {
        "?": "bool",
        "b1": "bool",
        "f4": "float32",
        "f8": "float64",
        "i1": "int8",
        "i2": "int16",
        "i4": "int32",
        "i8": "int64",
        "u1": "uint8",
        "u2": "uint16",
        "u4": "uint32",
        "u8": "uint64",
    }
    key = aliases.get(key, key)
    if key not in _ALLOWED_DTYPES:
        raise ValueError(f"unsupported trace dtype: {value}")
    return _ALLOWED_DTYPES[key]


def _little_endian(dtype: np.dtype[Any]) -> np.dtype[Any]:
    if dtype.itemsize <= 1 or dtype.byteorder == "|":
        return dtype
    return dtype.newbyteorder("<")


@dataclass(frozen=True, slots=True)
class TraceChunkInfo:
    ordinal: int
    sample_group: str
    relative_path: str
    axis_field: str
    axis_unit: str
    sample_count: int
    byte_count: int
    checksum_sha256: str
    axis_min: float | None
    axis_max: float | None
    fields: tuple[str, ...]
    coverage: dict[str, float]

    @property
    def checksum(self) -> str:
        return self.checksum_sha256

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TraceChunkInfo:
        return cls(
            ordinal=int(value["ordinal"]),
            sample_group=str(value["sample_group"]),
            relative_path=str(value["relative_path"]),
            axis_field=str(value["axis_field"]),
            axis_unit=str(value.get("axis_unit", "")),
            sample_count=int(value["sample_count"]),
            byte_count=int(value["byte_count"]),
            checksum_sha256=str(value["checksum_sha256"]),
            axis_min=float(value["axis_min"])
            if value.get("axis_min") is not None
            else None,
            axis_max=float(value["axis_max"])
            if value.get("axis_max") is not None
            else None,
            fields=tuple(str(item) for item in value.get("fields", [])),
            coverage={
                str(key): float(item) for key, item in value.get("coverage", {}).items()
            },
        )


@dataclass(frozen=True, slots=True)
class TraceManifest:
    id: str
    lap_id: str
    session_car_id: str
    encoding_version: int
    created_wall_ns: int
    chunks: tuple[TraceChunkInfo, ...]
    schema_version: int = MANIFEST_FORMAT_VERSION

    @property
    def manifest_id(self) -> str:
        return self.id

    @property
    def sample_count(self) -> int:
        return sum(chunk.sample_count for chunk in self.chunks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "Pit Wall trace manifest",
            "schema_version": self.schema_version,
            "id": self.id,
            "lap_id": self.lap_id,
            "session_car_id": self.session_car_id,
            "encoding_version": self.encoding_version,
            "created_wall_ns": self.created_wall_ns,
            "chunks": [asdict(chunk) for chunk in self.chunks],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TraceManifest:
        version = int(value.get("schema_version", 0))
        if version != MANIFEST_FORMAT_VERSION:
            raise TraceFormatError(f"unsupported trace manifest version: {version}")
        manifest_id = _safe_identifier(str(value["id"]), "manifest id")
        return cls(
            id=manifest_id,
            lap_id=str(value["lap_id"]),
            session_car_id=str(value["session_car_id"]),
            encoding_version=int(value["encoding_version"]),
            created_wall_ns=int(value["created_wall_ns"]),
            chunks=tuple(
                TraceChunkInfo.from_dict(item) for item in value.get("chunks", [])
            ),
            schema_version=version,
        )


@dataclass(frozen=True, slots=True)
class TraceSeries:
    unit: str
    provenance: str
    availability: str
    values: np.ndarray[Any, Any]
    available: np.ndarray[Any, Any]
    source_dtype: str

    @property
    def coverage(self) -> float:
        return float(np.mean(self.available)) if len(self.available) else 0.0

    def to_dict(self) -> dict[str, Any]:
        values: list[Any] = []
        for value, present in zip(self.values.tolist(), self.available.tolist()):
            values.append(value if present else None)
        return {
            "unit": self.unit,
            "provenance": self.provenance,
            "availability": self.availability if any(self.available) else "unavailable",
            "coverage": self.coverage,
            "values": values,
        }


@dataclass(frozen=True, slots=True)
class TraceSlice:
    manifest_id: str
    sample_group: str
    axis_name: str
    axis_unit: str
    axis_values: np.ndarray[Any, Any]
    axis_available: np.ndarray[Any, Any]
    series: dict[str, TraceSeries]
    source_sample_count: int

    @property
    def coverage(self) -> float:
        return float(np.mean(self.axis_available)) if len(self.axis_available) else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "manifest_id": self.manifest_id,
            "sample_group": self.sample_group,
            "axis": {
                "name": self.axis_name,
                "unit": self.axis_unit,
                "values": self.axis_values.tolist(),
                "availability": self.axis_available.tolist(),
            },
            "series": {key: value.to_dict() for key, value in self.series.items()},
            "coverage": self.coverage,
            "source_sample_count": self.source_sample_count,
        }


@dataclass(slots=True)
class VerificationReport:
    manifest_id: str
    valid: bool = True
    checked_chunks: int = 0
    checked_samples: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.valid = False
        self.errors.append(message)


@dataclass(slots=True)
class RecoveryReport:
    promoted: list[str] = field(default_factory=list)
    discarded_duplicates: list[str] = field(default_factory=list)
    invalid_temporary_files: list[str] = field(default_factory=list)
    orphan_chunks: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DeletePreview:
    manifest_id: str
    transaction_token: str
    relative_paths: tuple[str, ...]
    bytes_affected: int
    expires_wall_ns: int


@dataclass(slots=True)
class _FieldBuffer:
    dtype: np.dtype[Any]
    unit: str
    provenance: str
    availability: str
    values: list[np.ndarray[Any, Any]] = field(default_factory=list)
    masks: list[np.ndarray[Any, Any]] = field(default_factory=list)


@dataclass(slots=True)
class _PendingGroup:
    session_car_id: str
    sample_group: str
    axis_field: str
    axis_unit: str
    sample_count: int = 0
    fields: dict[str, _FieldBuffer] = field(default_factory=dict)


@dataclass(slots=True)
class _DecodedChunk:
    metadata: dict[str, Any]
    columns: dict[str, np.ndarray[Any, Any]]
    masks: dict[str, np.ndarray[Any, Any]]
    storage_bytes: int


def _infer_axis(names: set[str]) -> str:
    for candidate in ("distance", "d", "time", "t"):
        if candidate in names:
            return candidate
    raise ValueError(
        "axis_field is required when samples have no distance/d/time/t column"
    )


def _rows_to_columns(
    samples: Sequence[Mapping[str, Any]]
    | Mapping[str, Sequence[Any] | np.ndarray[Any, Any]],
) -> tuple[dict[str, list[Any]], int]:
    if isinstance(samples, Mapping):
        columns = {str(name): list(values) for name, values in samples.items()}
        lengths = {len(values) for values in columns.values()}
        if len(lengths) > 1:
            raise ValueError(
                "all trace columns must contain the same number of samples"
            )
        return columns, next(iter(lengths), 0)
    rows = list(samples)
    names = {str(name) for row in rows for name in row}
    return ({name: [row.get(name) for row in rows] for name in names}, len(rows))


def _infer_dtype(values: Sequence[Any], *, axis: bool) -> np.dtype[Any]:
    present = [value for value in values if value is not None]
    if not present:
        return np.dtype("<f8") if axis else np.dtype("<f4")
    if all(isinstance(value, (bool, np.bool_)) for value in present):
        return np.dtype("?")
    if all(
        isinstance(value, (int, np.integer)) and not isinstance(value, bool)
        for value in present
    ):
        return np.dtype("<f8") if axis else np.dtype("<i8")
    if all(isinstance(value, (int, float, np.number)) for value in present):
        return np.dtype("<f8") if axis else np.dtype("<f4")
    raise TypeError("trace columns must contain numeric, boolean or None values")


def _array_and_mask(
    values: Sequence[Any], dtype: np.dtype[Any]
) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any]]:
    mask_values: list[bool] = []
    converted: list[Any] = []
    floating = np.issubdtype(dtype, np.floating)
    for value in values:
        present = value is not None
        if (
            present
            and isinstance(value, (float, np.floating))
            and not math.isfinite(float(value))
        ):
            present = False
        mask_values.append(present)
        if present:
            converted.append(value)
        elif floating:
            converted.append(float("nan"))
        elif np.issubdtype(dtype, np.bool_):
            converted.append(False)
        else:
            converted.append(0)
    return (
        np.asarray(converted, dtype=_little_endian(dtype)),
        np.asarray(mask_values, dtype=np.bool_),
    )


def _missing_array(
    length: int, dtype: np.dtype[Any]
) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any]]:
    if np.issubdtype(dtype, np.floating):
        values = np.full(length, np.nan, dtype=dtype)
    else:
        values = np.zeros(length, dtype=dtype)
    return values, np.zeros(length, dtype=np.bool_)


class TraceStore:
    def __init__(
        self,
        root: str | Path,
        *,
        cache_max_bytes: int = 64 * 1024 * 1024,
        compression_level: int = 6,
    ) -> None:
        if cache_max_bytes < 0:
            raise ValueError("cache_max_bytes must be non-negative")
        if not 0 <= compression_level <= 9:
            raise ValueError("compression_level must be between 0 and 9")
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.cache_max_bytes = int(cache_max_bytes)
        self.compression_level = compression_level
        self._cache: OrderedDict[str, _DecodedChunk] = OrderedDict()
        self._cache_bytes = 0
        self._pending: dict[tuple[str, str], _PendingGroup] = {}
        self._delete_tokens: dict[str, tuple[str, str, int]] = {}
        self._lock = threading.RLock()

    def _resolve_relative(self, relative_path: str | Path) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise TraceStoreError(f"unsafe trace path: {relative_path}")
        resolved = (self.root / relative).resolve()
        if not resolved.is_relative_to(self.root):
            raise TraceStoreError(
                f"trace path escapes the configured root: {relative_path}"
            )
        return resolved

    def _manifest_relative_path(self, manifest_id: str) -> str:
        safe = _safe_identifier(manifest_id, "manifest id")
        return (Path("manifests") / f"{safe}.json").as_posix()

    @staticmethod
    def _field_metadata(
        field_metadata: Mapping[str, Mapping[str, Any]] | None,
        name: str,
    ) -> Mapping[str, Any]:
        return (field_metadata or {}).get(name, {})

    def append_samples(
        self,
        session_car_id: str,
        sample_group: str,
        samples: Sequence[Mapping[str, Any]]
        | Mapping[str, Sequence[Any] | np.ndarray[Any, Any]],
        *,
        axis_field: str | None = None,
        axis_unit: str | None = None,
        field_metadata: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> int:
        """Append one numeric batch to an in-memory lap builder."""

        columns, length = _rows_to_columns(samples)
        if not length:
            return 0
        if not columns:
            raise ValueError("trace samples contain no fields")
        resolved_axis = str(axis_field or _infer_axis(set(columns)))
        if resolved_axis not in columns:
            raise ValueError(f"axis field '{resolved_axis}' is missing from samples")
        key = (str(session_car_id), str(sample_group))
        with self._lock:
            pending = self._pending.get(key)
            if pending is None:
                axis_meta = self._field_metadata(field_metadata, resolved_axis)
                pending = _PendingGroup(
                    session_car_id=str(session_car_id),
                    sample_group=str(sample_group),
                    axis_field=resolved_axis,
                    axis_unit=str(
                        axis_unit
                        if axis_unit is not None
                        else axis_meta.get("unit", "")
                    ),
                )
                self._pending[key] = pending
            elif pending.axis_field != resolved_axis:
                raise ValueError(
                    f"sample group already uses axis '{pending.axis_field}', not '{resolved_axis}'"
                )

            incoming: dict[
                str,
                tuple[np.ndarray[Any, Any], np.ndarray[Any, Any], Mapping[str, Any]],
            ] = {}
            for name, values in columns.items():
                meta = self._field_metadata(field_metadata, name)
                explicit_dtype = _dtype_from_metadata(meta.get("dtype"))
                existing_dtype = (
                    pending.fields.get(name).dtype if name in pending.fields else None
                )
                dtype = (
                    explicit_dtype
                    or existing_dtype
                    or _infer_dtype(values, axis=name == resolved_axis)
                )
                values_array, mask = _array_and_mask(values, dtype)
                incoming[name] = values_array, mask, meta

            all_names = set(pending.fields) | set(incoming)
            for name in sorted(all_names):
                existing = pending.fields.get(name)
                batch = incoming.get(name)
                if existing is None:
                    if batch is None:
                        continue
                    values_array, mask, meta = batch
                    existing = _FieldBuffer(
                        dtype=values_array.dtype,
                        unit=str(meta.get("unit", "")),
                        provenance=str(meta.get("provenance", "observed")),
                        availability=str(meta.get("availability", "observed")),
                    )
                    if pending.sample_count:
                        old_values, old_mask = _missing_array(
                            pending.sample_count, existing.dtype
                        )
                        existing.values.append(old_values)
                        existing.masks.append(old_mask)
                    pending.fields[name] = existing
                if batch is None:
                    values_array, mask = _missing_array(length, existing.dtype)
                else:
                    values_array, mask, meta = batch
                    result_dtype = _little_endian(
                        np.result_type(existing.dtype, values_array.dtype)
                    )
                    if name == pending.axis_field:
                        result_dtype = np.dtype("<f8")
                    if result_dtype != existing.dtype:
                        existing.values = [
                            item.astype(result_dtype) for item in existing.values
                        ]
                        existing.dtype = result_dtype
                    values_array = values_array.astype(existing.dtype, copy=False)
                    if not existing.unit and meta.get("unit"):
                        existing.unit = str(meta["unit"])
                existing.values.append(values_array)
                existing.masks.append(mask)
            pending.sample_count += length
        return length

    def _encode_chunk(
        self,
        pending: _PendingGroup,
        *,
        manifest_id: str,
        lap_id: str,
        ordinal: int,
    ) -> tuple[bytes, dict[str, Any]]:
        columns: dict[str, np.ndarray[Any, Any]] = {}
        masks: dict[str, np.ndarray[Any, Any]] = {}
        for name, buffer in pending.fields.items():
            columns[name] = np.concatenate(buffer.values).astype(
                buffer.dtype, copy=False
            )
            masks[name] = np.concatenate(buffer.masks).astype(np.bool_, copy=False)
        axis = columns[pending.axis_field]
        axis_mask = masks[pending.axis_field]
        valid_axis = axis[axis_mask]
        if len(valid_axis) >= 2 and np.any(np.diff(valid_axis.astype(np.float64)) < 0):
            raise TraceStoreError(
                f"axis '{pending.axis_field}' is not monotonic; split discontinuities into timeline epochs"
            )

        raw = bytearray()
        field_table: dict[str, Any] = {}
        ordered_names = [pending.axis_field] + sorted(
            name for name in columns if name != pending.axis_field
        )
        for name in ordered_names:
            values = np.ascontiguousarray(columns[name])
            mask_bytes = np.packbits(masks[name], bitorder="little").tobytes()
            value_bytes = values.tobytes(order="C")
            value_offset = len(raw)
            raw.extend(value_bytes)
            mask_offset = len(raw)
            raw.extend(mask_bytes)
            present = values[masks[name]]
            stats = {
                "available_count": int(np.sum(masks[name])),
                "coverage": round(float(np.mean(masks[name])), 8),
                "min": float(np.min(present)) if len(present) else None,
                "max": float(np.max(present)) if len(present) else None,
                "mean": float(np.mean(present)) if len(present) else None,
            }
            source = pending.fields[name]
            field_table[name] = {
                "dtype": values.dtype.str,
                "count": len(values),
                "offset": value_offset,
                "nbytes": len(value_bytes),
                "sha256": _sha256(value_bytes),
                "mask_offset": mask_offset,
                "mask_nbytes": len(mask_bytes),
                "mask_sha256": _sha256(mask_bytes),
                "unit": source.unit,
                "provenance": source.provenance,
                "availability": source.availability,
                "stats": stats,
            }
        raw_bytes = bytes(raw)
        if len(raw_bytes) > _MAX_RAW_BYTES:
            raise TraceStoreError("trace chunk exceeds the maximum uncompressed size")
        metadata = {
            "format": "Pit Wall typed trace chunk",
            "format_version": TRACE_FORMAT_VERSION,
            "manifest_id": manifest_id,
            "lap_id": lap_id,
            "session_car_id": pending.session_car_id,
            "sample_group": pending.sample_group,
            "ordinal": ordinal,
            "axis": {"field": pending.axis_field, "unit": pending.axis_unit},
            "sample_count": pending.sample_count,
            "fields": field_table,
            "payload_sha256": _sha256(raw_bytes),
        }
        metadata_bytes = _canonical_json(metadata)
        if len(metadata_bytes) > _MAX_METADATA_BYTES:
            raise TraceStoreError("trace metadata exceeds the maximum size")
        compressed = zlib.compress(raw_bytes, self.compression_level)
        header = _TRACE_HEADER.pack(
            TRACE_MAGIC,
            TRACE_FORMAT_VERSION,
            0,
            len(metadata_bytes),
            len(compressed),
            len(raw_bytes),
            _crc32(metadata_bytes),
            _crc32(raw_bytes),
        )
        return header + metadata_bytes + compressed, metadata

    def _atomic_write(self, path: Path, value: bytes) -> None:
        if not path.resolve().is_relative_to(self.root):
            raise TraceStoreError("atomic write target escapes trace root")
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = Path(f"{path}.tmp")
        if temp.exists():
            raise TraceStoreError(f"pending temporary file requires recovery: {temp}")
        with temp.open("xb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)

    def finalize_lap(
        self,
        lap_id: str,
        *,
        session_car_id: str | None = None,
        manifest_id: str | None = None,
    ) -> TraceManifest:
        """Finalize every pending sample group for one session car."""

        with self._lock:
            cars = {car for car, _group in self._pending}
            selected_car = str(session_car_id) if session_car_id is not None else None
            if selected_car is None:
                if len(cars) != 1:
                    raise TraceStoreError(
                        "session_car_id is required when zero or multiple cars have pending traces"
                    )
                selected_car = next(iter(cars))
            groups = [
                value
                for (car, _group), value in self._pending.items()
                if car == selected_car
            ]
            if not groups:
                raise TraceStoreError(
                    f"no pending trace samples for car '{selected_car}'"
                )
            resolved_id = _safe_identifier(
                manifest_id or f"tm_{uuid.uuid4().hex}", "manifest id"
            )
            if self._resolve_relative(
                self._manifest_relative_path(resolved_id)
            ).exists():
                raise FileExistsError(f"trace manifest already exists: {resolved_id}")
            chunks: list[TraceChunkInfo] = []
            for ordinal, pending in enumerate(
                sorted(groups, key=lambda item: item.sample_group)
            ):
                relative = (
                    Path("chunks")
                    / resolved_id[:5]
                    / resolved_id
                    / f"{ordinal:03d}.pwt"
                ).as_posix()
                path = self._resolve_relative(relative)
                encoded, metadata = self._encode_chunk(
                    pending,
                    manifest_id=resolved_id,
                    lap_id=str(lap_id),
                    ordinal=ordinal,
                )
                self._atomic_write(path, encoded)
                axis_meta = metadata["fields"][pending.axis_field]
                chunks.append(
                    TraceChunkInfo(
                        ordinal=ordinal,
                        sample_group=pending.sample_group,
                        relative_path=relative,
                        axis_field=pending.axis_field,
                        axis_unit=pending.axis_unit,
                        sample_count=pending.sample_count,
                        byte_count=len(encoded),
                        checksum_sha256=_sha256(encoded),
                        axis_min=axis_meta["stats"]["min"],
                        axis_max=axis_meta["stats"]["max"],
                        fields=tuple(metadata["fields"]),
                        coverage={
                            name: float(item["stats"]["coverage"])
                            for name, item in metadata["fields"].items()
                        },
                    )
                )
            manifest = TraceManifest(
                id=resolved_id,
                lap_id=str(lap_id),
                session_car_id=selected_car,
                encoding_version=TRACE_FORMAT_VERSION,
                created_wall_ns=time.time_ns(),
                chunks=tuple(chunks),
            )
            manifest_path = self._resolve_relative(
                self._manifest_relative_path(resolved_id)
            )
            self._atomic_write(manifest_path, _canonical_json(manifest.to_dict()))
            for key in [key for key in self._pending if key[0] == selected_car]:
                del self._pending[key]
            return manifest

    def abort_pending(self, session_car_id: str) -> int:
        """Discard only the unfinalized buffers owned by one session car."""

        selected = str(session_car_id)
        with self._lock:
            keys = [key for key in self._pending if key[0] == selected]
            samples = sum(self._pending[key].sample_count for key in keys)
            for key in keys:
                del self._pending[key]
        return samples

    def discard_unregistered_manifest(self, manifest_id: str) -> list[str]:
        """Remove exact artifacts from a failed pre-catalog manifest write.

        This recovery primitive is deliberately narrower than session deletion:
        it accepts one validated manifest identifier and never follows catalog
        paths or globs supplied by a caller.
        """

        resolved_id = _safe_identifier(str(manifest_id), "manifest id")
        manifest_path = self._resolve_relative(
            self._manifest_relative_path(resolved_id)
        )
        chunk_dir = self._resolve_relative(
            (Path("chunks") / resolved_id[:5] / resolved_id).as_posix()
        )
        removed: list[str] = []
        with self._lock:
            if chunk_dir.exists():
                for path in sorted(chunk_dir.iterdir()):
                    if not path.is_file():
                        continue
                    relative = path.relative_to(self.root).as_posix()
                    path.unlink(missing_ok=True)
                    removed.append(relative)
                    cached = self._cache.pop(relative, None)
                    if cached is not None:
                        self._cache_bytes -= cached.storage_bytes
                try:
                    chunk_dir.rmdir()
                except OSError:
                    pass
                try:
                    chunk_dir.parent.rmdir()
                except OSError:
                    pass
            if manifest_path.exists():
                manifest_path.unlink()
                removed.append(manifest_path.relative_to(self.root).as_posix())
        return removed

    def load_manifest(self, manifest_id: str) -> TraceManifest:
        path = self._resolve_relative(self._manifest_relative_path(manifest_id))
        try:
            raw = path.read_bytes()
            value = json.loads(raw)
        except FileNotFoundError as exc:
            # A catalog row can outlive its file: an interrupted write, a
            # half-restored data root, a manual delete. Raising the bare OS
            # error surfaced a 500 and a stack trace, which is neither
            # listable nor diagnosable. Name it so callers can report the
            # lap as unavailable and point at the missing file.
            raise TraceManifestMissing(
                f"trace manifest {manifest_id} is catalogued but its file is "
                f"missing; the lap's telemetry cannot be read. Reprocess the "
                f"session to rebuild it, or delete the lap."
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TraceFormatError(f"invalid trace manifest: {manifest_id}") from exc
        if not isinstance(value, dict):
            raise TraceFormatError("trace manifest must be a JSON object")
        try:
            manifest = TraceManifest.from_dict(value)
        except (KeyError, TypeError, ValueError) as exc:
            raise TraceFormatError(f"invalid trace manifest: {manifest_id}") from exc
        if manifest.id != manifest_id:
            raise TraceFormatError("trace manifest id does not match its filename")
        for chunk in manifest.chunks:
            self._resolve_relative(chunk.relative_path)
        return manifest

    @staticmethod
    def _decode_chunk_bytes(value: bytes) -> _DecodedChunk:
        if len(value) < _TRACE_HEADER.size:
            raise TraceFormatError("truncated trace chunk header")
        (
            magic,
            version,
            _flags,
            metadata_length,
            compressed_length,
            raw_length,
            metadata_crc,
            raw_crc,
        ) = _TRACE_HEADER.unpack_from(value)
        if magic != TRACE_MAGIC:
            raise TraceFormatError("not a Pit Wall trace chunk")
        if version != TRACE_FORMAT_VERSION:
            raise TraceFormatError(f"unsupported trace chunk version: {version}")
        if metadata_length > _MAX_METADATA_BYTES or raw_length > _MAX_RAW_BYTES:
            raise TraceFormatError("trace chunk declares an unsafe size")
        expected_size = _TRACE_HEADER.size + metadata_length + compressed_length
        if len(value) != expected_size:
            raise TraceFormatError("trace chunk length does not match its header")
        metadata_bytes = value[
            _TRACE_HEADER.size : _TRACE_HEADER.size + metadata_length
        ]
        if _crc32(metadata_bytes) != metadata_crc:
            raise TraceFormatError("trace metadata CRC mismatch")
        try:
            metadata = json.loads(metadata_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TraceFormatError("trace metadata is not valid JSON") from exc
        if not isinstance(metadata, dict):
            raise TraceFormatError("trace metadata must be an object")
        compressed = value[_TRACE_HEADER.size + metadata_length :]
        try:
            raw = zlib.decompress(compressed)
        except zlib.error as exc:
            raise TraceFormatError("trace payload decompression failed") from exc
        if len(raw) != raw_length or _crc32(raw) != raw_crc:
            raise TraceFormatError("trace payload length or CRC mismatch")
        if metadata.get("payload_sha256") != _sha256(raw):
            raise TraceFormatError("trace payload SHA-256 mismatch")
        sample_count = int(metadata.get("sample_count", -1))
        columns: dict[str, np.ndarray[Any, Any]] = {}
        masks: dict[str, np.ndarray[Any, Any]] = {}
        field_table = metadata.get("fields")
        if not isinstance(field_table, dict):
            raise TraceFormatError("trace metadata has no field table")
        for name, spec in field_table.items():
            try:
                dtype = _dtype_from_metadata(spec["dtype"])
                if dtype is None:
                    raise ValueError("missing dtype")
                count = int(spec["count"])
                offset = int(spec["offset"])
                nbytes = int(spec["nbytes"])
                mask_offset = int(spec["mask_offset"])
                mask_nbytes = int(spec["mask_nbytes"])
            except (KeyError, TypeError, ValueError) as exc:
                raise TraceFormatError(
                    f"invalid metadata for trace field '{name}'"
                ) from exc
            if count != sample_count or nbytes != count * dtype.itemsize:
                raise TraceFormatError(
                    f"trace field '{name}' has inconsistent dimensions"
                )
            if mask_nbytes != (count + 7) // 8:
                raise TraceFormatError(f"trace field '{name}' has an invalid mask size")
            if min(offset, nbytes, mask_offset, mask_nbytes) < 0:
                raise TraceFormatError(
                    f"trace field '{name}' has a negative byte range"
                )
            value_end = offset + nbytes
            mask_end = mask_offset + mask_nbytes
            if value_end > len(raw) or mask_end > len(raw):
                raise TraceFormatError(f"trace field '{name}' exceeds the payload")
            value_bytes = raw[offset:value_end]
            mask_bytes = raw[mask_offset:mask_end]
            if spec.get("sha256") != _sha256(value_bytes):
                raise TraceFormatError(f"trace field '{name}' checksum mismatch")
            if spec.get("mask_sha256") != _sha256(mask_bytes):
                raise TraceFormatError(f"trace field '{name}' mask checksum mismatch")
            columns[str(name)] = np.frombuffer(
                value_bytes, dtype=dtype, count=count
            ).copy()
            masks[str(name)] = np.unpackbits(
                np.frombuffer(mask_bytes, dtype=np.uint8),
                bitorder="little",
                count=count,
            ).astype(np.bool_)
        return _DecodedChunk(
            metadata=metadata,
            columns=columns,
            masks=masks,
            storage_bytes=(
                len(metadata_bytes)
                + sum(column.nbytes for column in columns.values())
                + sum(mask.nbytes for mask in masks.values())
            ),
        )

    def _cache_put(self, key: str, decoded: _DecodedChunk) -> None:
        if self.cache_max_bytes <= 0 or decoded.storage_bytes > self.cache_max_bytes:
            return
        previous = self._cache.pop(key, None)
        if previous is not None:
            self._cache_bytes -= previous.storage_bytes
        self._cache[key] = decoded
        self._cache_bytes += decoded.storage_bytes
        while self._cache and self._cache_bytes > self.cache_max_bytes:
            _old_key, old = self._cache.popitem(last=False)
            self._cache_bytes -= old.storage_bytes

    def _load_chunk(
        self,
        info: TraceChunkInfo,
        *,
        use_cache: bool = True,
    ) -> _DecodedChunk:
        path = self._resolve_relative(info.relative_path)
        key = info.relative_path
        if use_cache:
            with self._lock:
                cached = self._cache.pop(key, None)
                if cached is not None:
                    self._cache[key] = cached
                    return cached
        value = path.read_bytes()
        if _sha256(value) != info.checksum_sha256:
            raise TraceFormatError(
                f"trace file checksum mismatch: {info.relative_path}"
            )
        decoded = self._decode_chunk_bytes(value)
        if use_cache:
            with self._lock:
                self._cache_put(key, decoded)
        return decoded

    def read_range(
        self,
        manifest_id: str,
        fields: Sequence[str] | None = None,
        start: float | None = None,
        end: float | None = None,
        *,
        sample_group: str | None = None,
    ) -> TraceSlice:
        manifest = self.load_manifest(manifest_id)
        requested = tuple(dict.fromkeys(str(item) for item in (fields or ())))
        candidates = [
            chunk
            for chunk in manifest.chunks
            if sample_group is None or chunk.sample_group == sample_group
        ]
        if requested:
            candidates = [
                chunk for chunk in candidates if set(requested) <= set(chunk.fields)
            ]
        if not candidates:
            raise KeyError("no trace sample group contains the requested fields")
        chunk = min(candidates, key=lambda item: item.ordinal)
        decoded = self._load_chunk(chunk)
        axis = decoded.columns[chunk.axis_field]
        axis_available = decoded.masks[chunk.axis_field]
        selected = axis_available.copy()
        if start is not None and end is not None and float(start) > float(end):
            raise ValueError("range start must not be greater than range end")
        if start is not None:
            selected &= axis.astype(np.float64) >= float(start)
        if end is not None:
            selected &= axis.astype(np.float64) <= float(end)
        names = requested or tuple(
            name for name in chunk.fields if name != chunk.axis_field
        )
        series: dict[str, TraceSeries] = {}
        for name in names:
            if name == chunk.axis_field:
                continue
            spec = decoded.metadata["fields"][name]
            mask = decoded.masks[name][selected].copy()
            series[name] = TraceSeries(
                unit=str(spec.get("unit", "")),
                provenance=str(spec.get("provenance", "observed")),
                availability=str(spec.get("availability", "observed")),
                values=decoded.columns[name][selected].copy(),
                available=mask,
                source_dtype=str(spec.get("dtype", "")),
            )
        return TraceSlice(
            manifest_id=manifest.id,
            sample_group=chunk.sample_group,
            axis_name=chunk.axis_field,
            axis_unit=chunk.axis_unit,
            axis_values=axis[selected].copy(),
            axis_available=axis_available[selected].copy(),
            series=series,
            source_sample_count=chunk.sample_count,
        )

    def verify_manifest(self, manifest_id: str) -> VerificationReport:
        report = VerificationReport(manifest_id=str(manifest_id))
        try:
            manifest = self.load_manifest(manifest_id)
        except (FileNotFoundError, TraceStoreError, ValueError) as exc:
            report.fail(str(exc))
            return report
        if not manifest.chunks:
            report.fail("trace manifest contains no chunks")
            return report
        for chunk in manifest.chunks:
            try:
                decoded = self._load_chunk(chunk, use_cache=False)
                if int(decoded.metadata.get("sample_count", -1)) != chunk.sample_count:
                    raise TraceFormatError("manifest/chunk sample count mismatch")
                if decoded.metadata.get("manifest_id") != manifest.id:
                    raise TraceFormatError("chunk belongs to a different manifest")
                axis = decoded.columns[chunk.axis_field]
                axis_mask = decoded.masks[chunk.axis_field]
                if len(axis[axis_mask]) >= 2 and np.any(
                    np.diff(axis[axis_mask].astype(np.float64)) < 0
                ):
                    raise TraceFormatError("chunk axis is not monotonic")
            except (OSError, TraceStoreError, KeyError) as exc:
                report.fail(f"{chunk.relative_path}: {exc}")
                continue
            report.checked_chunks += 1
            report.checked_samples += chunk.sample_count
        return report

    def prepare_delete(
        self, manifest_id: str, *, ttl_seconds: int = 300
    ) -> DeletePreview:
        manifest = self.load_manifest(manifest_id)
        manifest_path = self._resolve_relative(
            self._manifest_relative_path(manifest.id)
        )
        paths = tuple(chunk.relative_path for chunk in manifest.chunks) + (
            self._manifest_relative_path(manifest.id),
        )
        bytes_affected = sum(
            self._resolve_relative(relative).stat().st_size for relative in paths
        )
        digest = _sha256(manifest_path.read_bytes())
        token = secrets.token_urlsafe(24)
        expires = time.time_ns() + max(1, int(ttl_seconds)) * 1_000_000_000
        with self._lock:
            self._delete_tokens[token] = (manifest.id, digest, expires)
        return DeletePreview(
            manifest_id=manifest.id,
            transaction_token=token,
            relative_paths=paths,
            bytes_affected=bytes_affected,
            expires_wall_ns=expires,
        )

    def delete_manifest(self, manifest_id: str, transaction_token: str) -> list[str]:
        """Delete exactly one previewed manifest and its catalogued chunks."""

        with self._lock:
            authorization = self._delete_tokens.pop(transaction_token, None)
        if authorization is None:
            raise PermissionError("a valid deletion preview token is required")
        expected_id, expected_digest, expires = authorization
        if expected_id != manifest_id or time.time_ns() > expires:
            raise PermissionError(
                "deletion preview token is expired or for another manifest"
            )
        manifest = self.load_manifest(manifest_id)
        manifest_path = self._resolve_relative(
            self._manifest_relative_path(manifest.id)
        )
        if _sha256(manifest_path.read_bytes()) != expected_digest:
            raise PermissionError("manifest changed after the deletion preview")
        removed: list[str] = []
        for chunk in manifest.chunks:
            path = self._resolve_relative(chunk.relative_path)
            path.unlink(missing_ok=True)
            removed.append(chunk.relative_path)
            with self._lock:
                cached = self._cache.pop(chunk.relative_path, None)
                if cached is not None:
                    self._cache_bytes -= cached.storage_bytes
        manifest_path.unlink()
        removed.append(self._manifest_relative_path(manifest.id))
        for directory in sorted(
            {
                self._resolve_relative(chunk.relative_path).parent
                for chunk in manifest.chunks
            },
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass
        return removed

    def recover_pending_writes(self) -> RecoveryReport:
        """Promote complete temp files and report invalid/orphaned artifacts."""

        report = RecoveryReport()
        temporary = sorted(self.root.rglob("*.tmp"))
        chunk_temps = [
            path for path in temporary if not path.name.endswith(".json.tmp")
        ]
        manifest_temps = [path for path in temporary if path.name.endswith(".json.tmp")]
        for temp in chunk_temps:
            final = Path(str(temp)[:-4])
            relative_temp = temp.relative_to(self.root).as_posix()
            try:
                self._decode_chunk_bytes(temp.read_bytes())
            except (OSError, TraceStoreError) as exc:
                report.invalid_temporary_files.append(f"{relative_temp}: {exc}")
                continue
            if final.exists():
                if _sha256(final.read_bytes()) == _sha256(temp.read_bytes()):
                    temp.unlink()
                    report.discarded_duplicates.append(relative_temp)
                else:
                    report.invalid_temporary_files.append(
                        f"{relative_temp}: final file exists with different content"
                    )
                continue
            os.replace(temp, final)
            report.promoted.append(final.relative_to(self.root).as_posix())
        for temp in manifest_temps:
            final = Path(str(temp)[:-4])
            relative_temp = temp.relative_to(self.root).as_posix()
            try:
                value = json.loads(temp.read_bytes())
                if not isinstance(value, dict):
                    raise TraceFormatError("temporary manifest must be an object")
                manifest = TraceManifest.from_dict(value)
                for chunk in manifest.chunks:
                    if not self._resolve_relative(chunk.relative_path).is_file():
                        raise TraceFormatError(
                            f"referenced chunk is missing: {chunk.relative_path}"
                        )
            except (
                KeyError,
                OSError,
                TypeError,
                UnicodeDecodeError,
                ValueError,
                json.JSONDecodeError,
                TraceStoreError,
            ) as exc:
                report.invalid_temporary_files.append(f"{relative_temp}: {exc}")
                continue
            if final.exists():
                if _sha256(final.read_bytes()) == _sha256(temp.read_bytes()):
                    temp.unlink()
                    report.discarded_duplicates.append(relative_temp)
                else:
                    report.invalid_temporary_files.append(
                        f"{relative_temp}: final manifest exists with different content"
                    )
                continue
            os.replace(temp, final)
            report.promoted.append(final.relative_to(self.root).as_posix())

        referenced: set[str] = set()
        for manifest_path in self.root.glob("manifests/*.json"):
            try:
                value = json.loads(manifest_path.read_bytes())
                if not isinstance(value, dict):
                    raise TraceFormatError("manifest must be an object")
                manifest = TraceManifest.from_dict(value)
            except (
                KeyError,
                OSError,
                TypeError,
                UnicodeDecodeError,
                ValueError,
                json.JSONDecodeError,
                TraceStoreError,
            ):
                continue
            referenced.update(chunk.relative_path for chunk in manifest.chunks)
        for chunk_path in self.root.glob("chunks/**/*.pwt"):
            relative = chunk_path.relative_to(self.root).as_posix()
            if relative not in referenced:
                report.orphan_chunks.append(relative)
        return report

    def cache_info(self) -> dict[str, int]:
        with self._lock:
            return {
                "entries": len(self._cache),
                "bytes": self._cache_bytes,
                "max_bytes": self.cache_max_bytes,
            }
