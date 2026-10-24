"""Fast paths that must produce exactly what the slower code produced."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from f1.packets import resolve  # noqa: E402
from tools.demo_broadcast import Race  # noqa: E402
from tools.scenarios import SILVERSTONE  # noqa: E402

from pitwall import networking  # noqa: E402
from pitwall.state import StateStore  # noqa: E402
from pitwall.udp import F1DatagramProtocol, normalize_wheels  # noqa: E402

JSON = {"separators": (",", ":"), "ensure_ascii": False}


def _reference_normalize(values):
    raw = list(values)
    if len(raw) != 4:
        return raw
    return [raw[2], raw[3], raw[0], raw[1]]


def test_normalize_wheels_reorders_exactly_as_before():
    import ctypes

    cases = (
        [1, 2, 3, 4],
        (1.5, 2.5, 3.5, 4.5),
        range(4),
        "abcd",
        (ctypes.c_float * 4)(1, 2, 3, 4),
        [1, 2, 3],
        [],
        (1, 2, 3, 4, 5),
    )
    for values in cases:
        assert normalize_wheels(values) == _reference_normalize(values), values
    # An iterator has no length: it is consumed once, as before.
    assert normalize_wheels(iter([1, 2, 3, 4])) == [3, 4, 1, 2]
    assert normalize_wheels(iter([1, 2])) == [1, 2]


def test_source_parts_is_remembered_without_changing_its_answer():
    networking._SOURCE_PARTS_CACHE.clear()
    assert networking._source_parts(("192.168.1.20", 20777)) == ("192.168.1.20", 20777)
    assert networking._source_parts(("192.168.1.20", 20777)) == ("192.168.1.20", 20777)
    assert networking._source_parts("10.0.0.5") == ("10.0.0.5", 0)
    assert networking._source_parts(("10.0.0.5", 99999)) == ("10.0.0.5", 65535)
    assert ("192.168.1.20", 20777) in networking._SOURCE_PARTS_CACHE
    with pytest.raises(TypeError):
        networking._source_parts(("::1", 20777))
    with pytest.raises(ValueError):
        networking._source_parts(("not-an-address", 1))
    for index in range(networking._SOURCE_PARTS_CACHE_LIMIT + 5):
        networking._source_parts((f"10.1.{index // 256}.{index % 256}", 1))
    assert len(networking._SOURCE_PARTS_CACHE) <= networking._SOURCE_PARTS_CACHE_LIMIT


async def _populated_store() -> StateStore:
    store = StateStore()
    protocol = F1DatagramProtocol(store)
    race = Race(4, SILVERSTONE)
    race.lap = SILVERSTONE.start_lap
    race.distance = [d * 0.2 for d in race.distance]
    for _ in range(60):
        race.advance(warm_up=True)
        for builder in (
            race.session_packet, race.participants_packet, race.lap_packet,
            race.motion_packet, race.telemetry_packet, race.status_packet,
            race.damage_packet,
        ):
            await protocol._handle(resolve(builder()), None)
    return store


@pytest.mark.asyncio
async def test_the_live_frame_json_is_the_copied_snapshot_encoded():
    store = await _populated_store()
    snapshot = await store.snapshot_live()
    assert snapshot["drivers"] and snapshot["traces"], "the fixture must populate the state"
    assert await store.snapshot_live_json() == json.dumps(snapshot, **JSON)
    # The snapshot with copies stays independent of the live state.
    snapshot["drivers"][0]["name"] = "edited"
    snapshot["traces"].clear()
    again = await store.snapshot_live()
    assert again["drivers"][0]["name"] != "edited" and again["traces"]
