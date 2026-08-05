from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pitwall.trace_store import TraceStore, TraceStoreError


def _append_lap(store: TraceStore, car: str = "car_07") -> None:
    store.append_samples(
        car,
        "telemetry",
        [
            {"distance": 0.0, "speed": 100.0, "brake": None, "gear": 3},
            {"distance": 0.5, "speed": 102.0, "brake": 0.2, "gear": 3},
            {"distance": 1.0, "speed": 104.0, "brake": 0.4, "gear": 4},
        ],
        axis_field="distance",
        axis_unit="m",
        field_metadata={
            "speed": {"unit": "m/s", "provenance": "observed", "dtype": "float32"},
            "brake": {"unit": "ratio", "provenance": "observed"},
            "gear": {"unit": "gear", "dtype": "int8"},
        },
    )
    # A second batch lacks brake entirely; its availability mask must say so.
    store.append_samples(
        car,
        "telemetry",
        {
            "distance": [1.5, 2.0],
            "speed": [106.0, 108.0],
            "gear": [4, 4],
        },
        axis_field="distance",
        axis_unit="m",
    )


def test_trace_store_round_trip_range_availability_and_verification(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces", cache_max_bytes=1024 * 1024)
    _append_lap(store)
    manifest = store.finalize_lap("lap_12", session_car_id="car_07")

    assert manifest.sample_count == 5
    assert manifest.chunks[0].relative_path.startswith("chunks/")
    assert not manifest.chunks[0].relative_path.startswith("/")
    report = store.verify_manifest(manifest.id)
    assert report.valid
    assert report.checked_samples == 5

    sliced = store.read_range(
        manifest.id,
        fields=["speed", "brake", "gear"],
        start=0.5,
        end=1.5,
    )
    assert sliced.axis_unit == "m"
    assert sliced.axis_values.tolist() == [0.5, 1.0, 1.5]
    assert sliced.series["speed"].values.dtype == np.dtype("float32")
    assert sliced.series["brake"].available.tolist() == [True, True, False]
    assert sliced.to_dict()["series"]["brake"]["values"] == [
        pytest.approx(0.2),
        pytest.approx(0.4),
        None,
    ]
    assert store.cache_info()["bytes"] <= store.cache_info()["max_bytes"]


def test_trace_store_rejects_discontinuity_and_path_traversal(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces")
    store.append_samples(
        "car",
        "motion",
        {"distance": [0.0, 2.0, 1.0], "x": [1.0, 2.0, 3.0]},
        axis_field="distance",
    )
    with pytest.raises(TraceStoreError, match="not monotonic"):
        store.finalize_lap("lap_bad", session_car_id="car")
    with pytest.raises(ValueError):
        store.load_manifest("../outside")


def test_trace_store_recovers_valid_temp_and_reports_orphan(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces")
    _append_lap(store)
    manifest = store.finalize_lap("lap_1", session_car_id="car_07")
    chunk = store.root / manifest.chunks[0].relative_path
    temporary = Path(f"{chunk}.tmp")
    chunk.replace(temporary)

    recovery = store.recover_pending_writes()
    assert manifest.chunks[0].relative_path in recovery.promoted
    assert store.verify_manifest(manifest.id).valid

    orphan = chunk.with_name("orphan.pwt")
    orphan.write_bytes(chunk.read_bytes())
    recovery = store.recover_pending_writes()
    assert orphan.relative_to(store.root).as_posix() in recovery.orphan_chunks


def test_trace_store_delete_requires_exact_preview_token(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces")
    _append_lap(store)
    manifest = store.finalize_lap("lap_2", session_car_id="car_07")

    with pytest.raises(PermissionError):
        store.delete_manifest(manifest.id, "not-a-token")
    preview = store.prepare_delete(manifest.id)
    removed = store.delete_manifest(manifest.id, preview.transaction_token)
    assert set(removed) == set(preview.relative_paths)
    with pytest.raises(FileNotFoundError):
        store.load_manifest(manifest.id)


def test_trace_store_cache_never_exceeds_byte_budget(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces", cache_max_bytes=64)
    _append_lap(store)
    manifest = store.finalize_lap("lap_small_cache", session_car_id="car_07")
    result = store.read_range(manifest.id, fields=["speed"])
    assert len(result.axis_values) == 5
    assert store.cache_info()["bytes"] <= 64


def test_verify_bypasses_cache_and_detects_on_disk_tampering(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces")
    _append_lap(store)
    manifest = store.finalize_lap("lap_tamper", session_car_id="car_07")
    store.read_range(manifest.id, fields=["speed"])
    chunk = store.root / manifest.chunks[0].relative_path
    value = bytearray(chunk.read_bytes())
    value[-1] ^= 0xFF
    chunk.write_bytes(value)
    report = store.verify_manifest(manifest.id)
    assert not report.valid
    assert "checksum mismatch" in report.errors[0]


def test_missing_manifest_file_is_named_not_a_bare_os_error(tmp_path):
    """A catalogued manifest whose file is gone must be diagnosable.

    Raising the bare FileNotFoundError escaped the API as a 500 with a stack
    trace, so a session with one unreadable lap looked like a broken server
    rather than a lap that needs reprocessing.
    """
    from pitwall.trace_store import (
        TraceManifestMissing,
        TraceStore,
        TraceStoreError,
    )

    store = TraceStore(tmp_path)

    with pytest.raises(TraceManifestMissing) as caught:
        store.load_manifest("tm_absent")

    # Callers catch the store's base error; this must not bypass them.
    assert isinstance(caught.value, TraceStoreError)
    # Still an OS error, so existing recovery paths that catch
    # FileNotFoundError keep working unchanged.
    assert isinstance(caught.value, FileNotFoundError)
    message = str(caught.value)
    assert "tm_absent" in message
    assert "reprocess" in message.lower(), "the error must say what to do next"
