"""Versioned, recoverable raw UDP capture storage for Pit Wall.

The capture format deliberately stores the datagram bytes before parsing.  Files
are made from independently compressed blocks so a damaged or incomplete tail
does not make the earlier session unreadable.  A clean close writes an indexed
footer and atomically renames the temporary file into place.

This module has no dependency on the live UDP receiver.  That keeps the format
testable in isolation and lets live ingestion and replay share the same immutable
``CapturedDatagram`` value later.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import struct
import time
import zlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Self

FILE_MAGIC = b"PWCAP42\0"
BLOCK_MAGIC = b"PWBL"
FOOTER_MAGIC = b"PWFT"
FORMAT_VERSION = 1

_FILE_HEADER = struct.Struct("<8sHHII")
_BLOCK_HEADER = struct.Struct("<4sIIIIQQ")
_FRAME_HEADER = struct.Struct("<QQ4sHII")
_FOOTER_HEADER = struct.Struct("<4sII")

_MAX_METADATA_BYTES = 4 * 1024 * 1024
_MAX_BLOCK_BYTES = 64 * 1024 * 1024
_MAX_COMPRESSED_BLOCK_BYTES = _MAX_BLOCK_BYTES + 1 * 1024 * 1024
_MAX_DATAGRAM_BYTES = 65_535


class CaptureFormatError(ValueError):
    """Raised when a capture cannot be safely decoded."""


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _crc32(value: bytes) -> int:
    return zlib.crc32(value) & 0xFFFFFFFF


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    value = stream.read(length)
    if len(value) != length:
        raise EOFError(f"expected {length} bytes, found {len(value)}")
    return value


def _decompress_block(compressed: bytes, expected_length: int) -> bytes:
    """Bound decompression by the length declared in the validated header."""

    decompressor = zlib.decompressobj()
    try:
        raw = decompressor.decompress(compressed, expected_length + 1)
        if len(raw) > expected_length or decompressor.unconsumed_tail:
            raise CaptureFormatError("capture block expands beyond its declared size")
        raw += decompressor.flush(expected_length + 1 - len(raw))
    except zlib.error as exc:
        raise CaptureFormatError("capture block decompression failed") from exc
    if not decompressor.eof or decompressor.unused_data:
        raise CaptureFormatError("capture block has an incomplete or trailing compressed stream")
    return raw


@dataclass(frozen=True, slots=True)
class CapturedDatagram:
    """One received UDP datagram and the clocks/source observed by Pit Wall."""

    monotonic_ns: int
    wall_ns: int
    source_host: str
    source_port: int
    data: bytes

    def __post_init__(self) -> None:
        if self.monotonic_ns < 0 or self.wall_ns < 0:
            raise ValueError("capture timestamps must be non-negative")
        if not 0 <= self.source_port <= 65_535:
            raise ValueError("source_port must be between 0 and 65535")
        try:
            address = ipaddress.ip_address(self.source_host)
        except ValueError as exc:
            raise ValueError("source_host must be an IPv4 address") from exc
        if address.version != 4:
            raise ValueError("PWCAP v1 stores IPv4 source addresses only")
        if len(self.data) > _MAX_DATAGRAM_BYTES:
            raise ValueError("datagram exceeds the maximum UDP payload size")

    @property
    def source(self) -> tuple[str, int]:
        return self.source_host, self.source_port


@dataclass(frozen=True, slots=True)
class CaptureBlockIndex:
    ordinal: int
    offset: int
    payload_offset: int
    compressed_bytes: int
    raw_bytes: int
    frame_count: int
    datagram_bytes: int
    first_monotonic_ns: int
    last_monotonic_ns: int
    raw_crc32: int

    def footer_dict(self) -> dict[str, int]:
        return {
            "ordinal": self.ordinal,
            "offset": self.offset,
            "compressed_bytes": self.compressed_bytes,
            "raw_bytes": self.raw_bytes,
            "frame_count": self.frame_count,
            "datagram_bytes": self.datagram_bytes,
            "first_monotonic_ns": self.first_monotonic_ns,
            "last_monotonic_ns": self.last_monotonic_ns,
            "raw_crc32": self.raw_crc32,
        }


@dataclass(slots=True)
class CaptureScanReport:
    path: Path
    metadata: dict[str, Any]
    blocks: list[CaptureBlockIndex] = field(default_factory=list)
    footer: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    valid_data_end: int = 0
    file_size: int = 0

    @property
    def packet_count(self) -> int:
        return sum(block.frame_count for block in self.blocks)

    @property
    def datagram_bytes(self) -> int:
        return sum(block.datagram_bytes for block in self.blocks)

    @property
    def clean_close(self) -> bool:
        return bool(self.footer and self.footer.get("clean_close"))

    @property
    def recovered(self) -> bool:
        return bool(self.footer and self.footer.get("recovered"))

    @property
    def valid(self) -> bool:
        return not self.errors and self.footer is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "format_version": self.metadata.get("format_version"),
            "metadata": self.metadata,
            "packet_count": self.packet_count,
            "datagram_bytes": self.datagram_bytes,
            "block_count": len(self.blocks),
            "blocks": [asdict(block) for block in self.blocks],
            "clean_close": self.clean_close,
            "recovered": self.recovered,
            "valid": self.valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "valid_data_end": self.valid_data_end,
            "file_size": self.file_size,
        }


class CaptureWriter:
    """Append original datagrams to a temporary PWCAP and publish atomically."""

    def __init__(
        self,
        path: str | Path,
        *,
        metadata: Mapping[str, Any] | None = None,
        block_target_bytes: int = 1 * 1024 * 1024,
        block_target_frames: int = 512,
        compression_level: int = 6,
    ) -> None:
        self.path = Path(path)
        self.temp_path = Path(f"{self.path}.tmp")
        self.block_target_bytes = max(4_096, min(_MAX_BLOCK_BYTES, int(block_target_bytes)))
        self.block_target_frames = max(1, int(block_target_frames))
        if not 0 <= compression_level <= 9:
            raise ValueError("compression_level must be between 0 and 9")
        self.compression_level = compression_level
        self.metadata = dict(metadata or {})
        self.metadata.update(
            {
                "format": "PWCAP",
                "format_version": FORMAT_VERSION,
                "created_wall_ns": int(self.metadata.get("created_wall_ns", time.time_ns())),
            }
        )
        self._stream: BinaryIO | None = None
        self._content_hash = hashlib.sha256()
        self._pending = bytearray()
        self._pending_count = 0
        self._pending_datagram_bytes = 0
        self._pending_first_ns: int | None = None
        self._pending_last_ns: int | None = None
        self._blocks: list[CaptureBlockIndex] = []
        self._closed = False
        self._open()

    def _open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            raise FileExistsError(f"capture already exists: {self.path}")
        if self.temp_path.exists():
            raise FileExistsError(
                f"unfinished capture already exists: {self.temp_path}; recover or remove it first"
            )
        self._stream = self.temp_path.open("xb")
        metadata_bytes = _canonical_json(self.metadata)
        if len(metadata_bytes) > _MAX_METADATA_BYTES:
            self._stream.close()
            self.temp_path.unlink(missing_ok=True)
            raise ValueError("capture metadata is too large")
        header = _FILE_HEADER.pack(
            FILE_MAGIC,
            FORMAT_VERSION,
            0,
            len(metadata_bytes),
            _crc32(metadata_bytes),
        )
        self._write_content(header)
        self._write_content(metadata_bytes)

    def _write_content(self, value: bytes) -> None:
        if self._stream is None:
            raise RuntimeError("capture writer is closed")
        self._stream.write(value)
        self._content_hash.update(value)

    def write(
        self,
        data: bytes | bytearray | memoryview,
        source: tuple[str, int],
        *,
        monotonic_ns: int | None = None,
        wall_ns: int | None = None,
    ) -> None:
        """Queue one byte-identical datagram into the current compressed block."""

        if self._closed:
            raise RuntimeError("capture writer is closed")
        payload = bytes(data)
        frame = CapturedDatagram(
            monotonic_ns=time.monotonic_ns() if monotonic_ns is None else int(monotonic_ns),
            wall_ns=time.time_ns() if wall_ns is None else int(wall_ns),
            source_host=str(source[0]),
            source_port=int(source[1]),
            data=payload,
        )
        packed_ip = ipaddress.ip_address(frame.source_host).packed
        frame_header = _FRAME_HEADER.pack(
            frame.monotonic_ns,
            frame.wall_ns,
            packed_ip,
            frame.source_port,
            len(payload),
            _crc32(payload),
        )
        if self._pending and len(self._pending) + len(frame_header) + len(payload) > _MAX_BLOCK_BYTES:
            self.flush_block()
        self._pending.extend(frame_header)
        self._pending.extend(payload)
        self._pending_count += 1
        self._pending_datagram_bytes += len(payload)
        if self._pending_first_ns is None:
            self._pending_first_ns = frame.monotonic_ns
        self._pending_last_ns = frame.monotonic_ns
        if (
            len(self._pending) >= self.block_target_bytes
            or self._pending_count >= self.block_target_frames
        ):
            self.flush_block()

    append_datagram = write

    def flush_block(self) -> None:
        if not self._pending_count:
            return
        if self._stream is None:
            raise RuntimeError("capture writer is closed")
        raw = bytes(self._pending)
        compressed = zlib.compress(raw, self.compression_level)
        offset = self._stream.tell()
        raw_crc = _crc32(raw)
        first_ns = int(self._pending_first_ns or 0)
        last_ns = int(self._pending_last_ns or first_ns)
        header = _BLOCK_HEADER.pack(
            BLOCK_MAGIC,
            len(raw),
            len(compressed),
            self._pending_count,
            raw_crc,
            first_ns,
            last_ns,
        )
        self._write_content(header)
        payload_offset = self._stream.tell()
        self._write_content(compressed)
        self._blocks.append(
            CaptureBlockIndex(
                ordinal=len(self._blocks),
                offset=offset,
                payload_offset=payload_offset,
                compressed_bytes=len(compressed),
                raw_bytes=len(raw),
                frame_count=self._pending_count,
                datagram_bytes=self._pending_datagram_bytes,
                first_monotonic_ns=first_ns,
                last_monotonic_ns=last_ns,
                raw_crc32=raw_crc,
            )
        )
        self._pending.clear()
        self._pending_count = 0
        self._pending_datagram_bytes = 0
        self._pending_first_ns = None
        self._pending_last_ns = None

    def _footer_payload(self, *, clean_close: bool, recovered: bool = False) -> bytes:
        return _canonical_json(
            {
                "format_version": FORMAT_VERSION,
                "clean_close": clean_close,
                "recovered": recovered,
                "packet_count": sum(block.frame_count for block in self._blocks),
                "datagram_bytes": sum(block.datagram_bytes for block in self._blocks),
                "content_sha256": self._content_hash.hexdigest(),
                "blocks": [block.footer_dict() for block in self._blocks],
            }
        )

    def close(self) -> Path:
        """Write the clean footer, fsync, and atomically publish the capture."""

        if self._closed:
            return self.path
        self.flush_block()
        if self._stream is None:
            raise RuntimeError("capture writer is closed")
        footer = self._footer_payload(clean_close=True)
        self._stream.write(_FOOTER_HEADER.pack(FOOTER_MAGIC, len(footer), _crc32(footer)))
        self._stream.write(footer)
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self._stream.close()
        self._stream = None
        if self.path.exists():
            raise FileExistsError(
                f"capture destination appeared while recording: {self.path}; temp file retained"
            )
        os.replace(self.temp_path, self.path)
        self._closed = True
        return self.path

    def abort(self, *, flush_valid_block: bool = True) -> Path:
        """Simulate/record an unclean stop while leaving a recoverable temp file."""

        if self._closed:
            return self.temp_path
        if flush_valid_block:
            self.flush_block()
        if self._stream is not None:
            self._stream.flush()
            os.fsync(self._stream.fileno())
            self._stream.close()
            self._stream = None
        self._closed = True
        return self.temp_path

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is None:
            self.close()
        else:
            self.abort()


def _decode_frames(raw: bytes, expected_count: int) -> list[CapturedDatagram]:
    frames: list[CapturedDatagram] = []
    offset = 0
    while offset < len(raw):
        if len(raw) - offset < _FRAME_HEADER.size:
            raise CaptureFormatError("truncated datagram frame header")
        (
            monotonic_ns,
            wall_ns,
            packed_ip,
            source_port,
            payload_length,
            payload_crc,
        ) = _FRAME_HEADER.unpack_from(raw, offset)
        offset += _FRAME_HEADER.size
        if payload_length > _MAX_DATAGRAM_BYTES:
            raise CaptureFormatError("datagram frame declares an invalid payload length")
        end = offset + payload_length
        if end > len(raw):
            raise CaptureFormatError("truncated datagram payload")
        payload = raw[offset:end]
        offset = end
        if _crc32(payload) != payload_crc:
            raise CaptureFormatError("datagram CRC mismatch")
        frames.append(
            CapturedDatagram(
                monotonic_ns=monotonic_ns,
                wall_ns=wall_ns,
                source_host=str(ipaddress.ip_address(packed_ip)),
                source_port=source_port,
                data=payload,
            )
        )
    if len(frames) != expected_count:
        raise CaptureFormatError(
            f"block frame count mismatch: expected {expected_count}, decoded {len(frames)}"
        )
    return frames


def _read_header(stream: BinaryIO) -> tuple[dict[str, Any], bytes]:
    packed = _read_exact(stream, _FILE_HEADER.size)
    magic, version, _flags, metadata_length, metadata_crc = _FILE_HEADER.unpack(packed)
    if magic != FILE_MAGIC:
        raise CaptureFormatError("not a PWCAP file")
    if version != FORMAT_VERSION:
        raise CaptureFormatError(f"unsupported PWCAP version: {version}")
    if metadata_length > _MAX_METADATA_BYTES:
        raise CaptureFormatError("capture metadata length exceeds the safety limit")
    metadata_bytes = _read_exact(stream, metadata_length)
    if _crc32(metadata_bytes) != metadata_crc:
        raise CaptureFormatError("capture metadata CRC mismatch")
    try:
        metadata = json.loads(metadata_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureFormatError("capture metadata is not valid JSON") from exc
    if not isinstance(metadata, dict):
        raise CaptureFormatError("capture metadata must be an object")
    return metadata, packed + metadata_bytes


def scan_capture(path: str | Path) -> CaptureScanReport:
    """Scan through the last valid independent block without trusting the footer."""

    source = Path(path)
    report = CaptureScanReport(path=source, metadata={})
    report.file_size = source.stat().st_size
    with source.open("rb") as stream:
        try:
            metadata, header_bytes = _read_header(stream)
        except (EOFError, CaptureFormatError) as exc:
            report.errors.append(str(exc))
            return report
        report.metadata = metadata
        report.valid_data_end = stream.tell()
        content_hash = hashlib.sha256(header_bytes)
        while True:
            record_offset = stream.tell()
            magic = stream.read(4)
            if not magic:
                report.errors.append("capture ended without a footer")
                break
            if len(magic) != 4:
                report.errors.append("capture ends inside a record marker")
                break
            if magic == BLOCK_MAGIC:
                try:
                    rest = _read_exact(stream, _BLOCK_HEADER.size - 4)
                    packed_header = magic + rest
                    (
                        _magic,
                        raw_length,
                        compressed_length,
                        frame_count,
                        raw_crc,
                        first_ns,
                        last_ns,
                    ) = _BLOCK_HEADER.unpack(packed_header)
                    if (
                        raw_length > _MAX_BLOCK_BYTES
                        or compressed_length > _MAX_COMPRESSED_BLOCK_BYTES
                    ):
                        raise CaptureFormatError("capture block exceeds the safety limit")
                    payload_offset = stream.tell()
                    compressed = _read_exact(stream, compressed_length)
                    raw = _decompress_block(compressed, raw_length)
                    if len(raw) != raw_length:
                        raise CaptureFormatError("capture block raw length mismatch")
                    if _crc32(raw) != raw_crc:
                        raise CaptureFormatError("capture block CRC mismatch")
                    frames = _decode_frames(raw, frame_count)
                except (EOFError, CaptureFormatError) as exc:
                    report.errors.append(f"block {len(report.blocks)} at {record_offset}: {exc}")
                    break
                report.blocks.append(
                    CaptureBlockIndex(
                        ordinal=len(report.blocks),
                        offset=record_offset,
                        payload_offset=payload_offset,
                        compressed_bytes=compressed_length,
                        raw_bytes=raw_length,
                        frame_count=frame_count,
                        datagram_bytes=sum(len(frame.data) for frame in frames),
                        first_monotonic_ns=first_ns,
                        last_monotonic_ns=last_ns,
                        raw_crc32=raw_crc,
                    )
                )
                report.valid_data_end = stream.tell()
                content_hash.update(packed_header)
                content_hash.update(compressed)
                continue
            if magic == FOOTER_MAGIC:
                try:
                    rest = _read_exact(stream, _FOOTER_HEADER.size - 4)
                    _magic, payload_length, payload_crc = _FOOTER_HEADER.unpack(magic + rest)
                    if payload_length > _MAX_METADATA_BYTES:
                        raise CaptureFormatError("capture footer exceeds the safety limit")
                    payload = _read_exact(stream, payload_length)
                    if _crc32(payload) != payload_crc:
                        raise CaptureFormatError("capture footer CRC mismatch")
                    footer = json.loads(payload)
                    if not isinstance(footer, dict):
                        raise CaptureFormatError("capture footer must be an object")
                except (EOFError, UnicodeDecodeError, json.JSONDecodeError, CaptureFormatError) as exc:
                    report.errors.append(f"footer at {record_offset}: {exc}")
                    break
                report.footer = footer
                if footer.get("content_sha256") != content_hash.hexdigest():
                    report.errors.append("capture content SHA-256 mismatch")
                if int(footer.get("packet_count", -1)) != report.packet_count:
                    report.errors.append("capture footer packet count mismatch")
                expected_index = [block.footer_dict() for block in report.blocks]
                if footer.get("blocks") != expected_index:
                    report.errors.append("capture footer block index mismatch")
                trailing = stream.read(1)
                if trailing:
                    report.errors.append("unexpected bytes follow the capture footer")
                break
            report.errors.append(
                f"unknown record marker {magic!r} at byte offset {record_offset}"
            )
            break
    return report


class CaptureReader:
    """Validated block reader for clean or recoverable PWCAP files."""

    def __init__(self, path: str | Path, *, require_footer: bool = True) -> None:
        self.path = Path(path)
        self.report = scan_capture(self.path)
        fatal = list(self.report.errors)
        if not require_footer:
            fatal = [error for error in fatal if error != "capture ended without a footer"]
        if fatal:
            raise CaptureFormatError("; ".join(fatal))

    @property
    def metadata(self) -> dict[str, Any]:
        return dict(self.report.metadata)

    def iter_frames(self) -> Iterator[CapturedDatagram]:
        with self.path.open("rb") as stream:
            for block in self.report.blocks:
                stream.seek(block.payload_offset)
                compressed = _read_exact(stream, block.compressed_bytes)
                raw = _decompress_block(compressed, block.raw_bytes)
                yield from _decode_frames(raw, block.frame_count)

    def __iter__(self) -> Iterator[CapturedDatagram]:
        return self.iter_frames()


# Architecture-facing names retained alongside the concise public names.
RawCaptureWriter = CaptureWriter
RawCaptureReader = CaptureReader


def inspect_capture(path: str | Path) -> dict[str, Any]:
    return scan_capture(path).to_dict()


def validate_capture(path: str | Path) -> CaptureScanReport:
    """Fully validate block, frame, footer CRCs and the content checksum."""

    return scan_capture(path)


def _write_recovery_footer(
    stream: BinaryIO,
    *,
    blocks: list[CaptureBlockIndex],
    content_sha256: str,
) -> None:
    payload = _canonical_json(
        {
            "format_version": FORMAT_VERSION,
            "clean_close": False,
            "recovered": True,
            "packet_count": sum(block.frame_count for block in blocks),
            "datagram_bytes": sum(block.datagram_bytes for block in blocks),
            "content_sha256": content_sha256,
            "blocks": [block.footer_dict() for block in blocks],
        }
    )
    stream.write(_FOOTER_HEADER.pack(FOOTER_MAGIC, len(payload), _crc32(payload)))
    stream.write(payload)


def recover_capture(
    source_path: str | Path,
    destination_path: str | Path | None = None,
) -> CaptureScanReport:
    """Publish all complete blocks from an unclean/truncated capture.

    The source is never modified in place.  A recovered footer records that the
    archive may contain a tail gap and the destination is atomically replaced.
    """

    source = Path(source_path)
    report = scan_capture(source)
    if report.valid_data_end < _FILE_HEADER.size:
        raise CaptureFormatError("capture has no valid recoverable header")
    if destination_path is None:
        source_text = str(source)
        destination = (
            Path(source_text[:-4]) if source_text.endswith(".tmp") else source.with_name(f"{source.stem}.recovered{source.suffix}")
        )
    else:
        destination = Path(destination_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination != source:
        raise FileExistsError(f"recovery destination already exists: {destination}")
    temp = Path(f"{destination}.recovering")
    temp.unlink(missing_ok=True)
    digest = hashlib.sha256()
    remaining = report.valid_data_end
    with source.open("rb") as incoming, temp.open("xb") as outgoing:
        while remaining:
            chunk = incoming.read(min(1 * 1024 * 1024, remaining))
            if not chunk:
                raise CaptureFormatError("source became truncated during recovery")
            outgoing.write(chunk)
            digest.update(chunk)
            remaining -= len(chunk)
        _write_recovery_footer(
            outgoing,
            blocks=report.blocks,
            content_sha256=digest.hexdigest(),
        )
        outgoing.flush()
        os.fsync(outgoing.fileno())
    os.replace(temp, destination)
    if source != destination and source == Path(f"{destination}.tmp"):
        source.unlink(missing_ok=True)
    return scan_capture(destination)


def _redact_metadata(value: Any, sensitive_keys: set[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "<redacted>"
                if key.casefold() in sensitive_keys
                else _redact_metadata(item, sensitive_keys)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_metadata(item, sensitive_keys) for item in value]
    return value


def anonymize_capture(
    source_path: str | Path,
    destination_path: str | Path,
    *,
    datagram_transform: Callable[[bytes], bytes] | None = None,
    sensitive_metadata_keys: set[str] | None = None,
) -> CaptureScanReport:
    """Create a transport-redacted copy, optionally transforming packet payloads.

    Without a game-version parser it is unsafe to claim participant names inside
    datagrams were removed, so payload redaction is an explicit caller-supplied
    transform.  Source endpoint metadata is always removed.
    """

    reader = CaptureReader(source_path)
    keys = sensitive_metadata_keys or {
        "adapter",
        "bind_host",
        "hostname",
        "receive_bind",
        "source",
        "source_host",
        "source_ipv4",
        "source_port",
        "username",
    }
    metadata = _redact_metadata(reader.metadata, {key.casefold() for key in keys})
    metadata["privacy_mode"] = (
        "custom-payload-redacted" if datagram_transform else "transport-redacted-only"
    )
    metadata["anonymized_wall_ns"] = time.time_ns()
    with CaptureWriter(destination_path, metadata=metadata) as writer:
        for frame in reader:
            payload = datagram_transform(frame.data) if datagram_transform else frame.data
            writer.write(
                payload,
                ("0.0.0.0", 0),
                monotonic_ns=frame.monotonic_ns,
                wall_ns=frame.wall_ns,
            )
    return scan_capture(destination_path)
