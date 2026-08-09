"""Yellow flags in the driver's path must be called.

Marshal zones have always been parsed and stored, and were read only by the
dashboard and an on-demand tool. Nothing volunteered one, so the product had
never told a driver about a yellow flag. A real Mexico race carried a flag
context on 164 of 717 recorded laps.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pitwall import proactive as proactive_module  # noqa: E402
from pitwall.proactive import FLASH, FLASH_EVENTS, ProactiveEngineer  # noqa: E402


def _engine() -> ProactiveEngineer:
    engine = object.__new__(ProactiveEngineer)
    engine.pending = []
    engine._yellow_called = set()
    engine._session_uid = 1
    engine._cooldowns = {}
    engine._discarded = []
    engine._standing_instructions = []
    calls: list[tuple[str, dict]] = []

    def _enqueue(event_type, payload, **kw):
        calls.append((event_type, payload))

    engine._enqueue = _enqueue  # type: ignore[assignment]
    engine.calls = calls  # type: ignore[attr-defined]
    return engine


def _state(zones, lap_distance, *, phase="green", track_length=4304.0, lap=5):
    return {
        "race_control_phase": phase,
        "marshal_zones": zones,
        "track_length_m": track_length,
        "lap_distance_m": lap_distance,
        "current_lap": lap,
    }


def test_a_yellow_just_ahead_is_called() -> None:
    engine = _engine()
    engine._detect_marshal_yellow(
        _state([{"index": 3, "flag": "yellow", "start_m": 1500.0}], 1200.0)
    )
    assert len(engine.calls) == 1
    kind, payload = engine.calls[0]
    assert kind == "yellow_ahead"
    assert payload["distance_ahead_m"] == 300.0
    assert payload["zone"] == 3


def test_a_yellow_behind_the_car_is_not_called() -> None:
    # Already driven through. Announcing it would be noise at best and would
    # make the driver lift for clear track at worst.
    engine = _engine()
    engine._detect_marshal_yellow(
        _state([{"index": 1, "flag": "yellow", "start_m": 200.0}], 3000.0)
    )
    # 200 is 1504 m ahead once wrapped, which is beyond the lookahead.
    assert engine.calls == []


def test_the_wrap_around_at_the_line_is_handled() -> None:
    # Car near the end of the lap, zone just after the start line: genuinely
    # ahead, and the naive subtraction would make it negative.
    engine = _engine()
    engine._detect_marshal_yellow(
        _state([{"index": 0, "flag": "yellow", "start_m": 100.0}], 4100.0)
    )
    assert len(engine.calls) == 1
    assert engine.calls[0][1]["distance_ahead_m"] == 304.0


def test_a_persisting_yellow_is_called_once_per_lap() -> None:
    engine = _engine()
    zones = [{"index": 2, "flag": "yellow", "start_m": 1500.0}]
    for _ in range(20):
        engine._detect_marshal_yellow(_state(zones, 1200.0, lap=5))
    assert len(engine.calls) == 1, "a standing yellow was announced repeatedly"
    # A new lap past the same incident is worth one more call.
    engine._detect_marshal_yellow(_state(zones, 1200.0, lap=6))
    assert len(engine.calls) == 2


def test_zone_yellows_are_silent_under_a_safety_car() -> None:
    # The whole track is neutralised and the driver has already been told;
    # zone-by-zone calls would be chatter over the top of it.
    engine = _engine()
    engine._detect_marshal_yellow(
        _state([{"index": 2, "flag": "yellow", "start_m": 1500.0}], 1200.0, phase="safety_car")
    )
    assert engine.calls == []


def test_flash_calls_skip_the_model() -> None:
    """The template is the better answer for these, not a fallback.

    Narration cost a round trip before a word was spoken: measured over a real
    race, queue-to-spoken averaged 31.6 s, and a 45 s VSC was announced after it
    had ended. It was also wrong once, announcing a red flag from a payload
    describing a return to green.
    """
    assert "yellow_ahead" in FLASH_EVENTS
    assert "race_control" in FLASH_EVENTS
    assert FLASH < 0, "FLASH must outrank CRITICAL in the delivery queue"
