"""A stalled engineer has to say what is holding it.

Fourteen calls queued and nothing spoken looked exactly like a quiet race. The
reason each call was held was recorded on the call and shown to nobody, so the
only way to tell a wedged voice controller from an uneventful stint was to read
the source. These pin the diagnosis reaching the surface.
"""

from __future__ import annotations

import time

import pytest

from pitwall.proactive import ProactiveEngineer


def _event(**overrides):
    event = {
        "type": "progress_update",
        "payload": {},
        "queued_at": time.time(),
        "session_uid": 0,
        "expires_at": time.time() + 600,
        "deliver_by": time.time() + 600,
        "critical": False,
    }
    event.update(overrides)
    return event


def test_the_current_block_reason_is_not_buried_under_the_first_one():
    """The deduplicated history answers a different question.

    A call blocked once on a closed speech session and every time since on an
    unsafe driving phase still listed the speech session last, so the record
    pointed at a state that had long since cleared.
    """
    event = _event()
    ProactiveEngineer._mark_blocked(event, "engineer busy: speech session open")
    ProactiveEngineer._mark_blocked(event, "unsafe driving phase (speed 240)")
    ProactiveEngineer._mark_blocked(event, "unsafe driving phase (speed 250)")

    assert event["blocked_reason"].startswith("unsafe driving phase")
    assert event["blocked_at"] > 0
    # The history is still complete, for the saved record.
    assert "engineer busy: speech session open" in event["blocked_reasons"]


def test_a_stalled_queue_reports_the_longest_waiting_call(monkeypatch):
    """The call stuck longest is the one that explains the stall."""
    engineer = ProactiveEngineer.__new__(ProactiveEngineer)
    now = time.time()
    old = _event(queued_at=now - 120, blocked_reason="engineer busy: speech session open", blocked_at=now - 1)
    recent = _event(queued_at=now - 2, blocked_reason="minimum interval", blocked_at=now - 1)
    engineer.pending = [recent, old]

    blockage = engineer._queue_blockage()

    assert blockage["blocked_reason"] == "engineer busy: speech session open"
    assert blockage["blocked_calls"] == 2
    assert blockage["blocked_for_s"] >= 0.0


def test_an_empty_queue_reports_no_blockage():
    engineer = ProactiveEngineer.__new__(ProactiveEngineer)
    engineer.pending = []

    assert engineer._queue_blockage() == {
        "blocked_reason": "",
        "blocked_for_s": 0.0,
        "blocked_calls": 0,
    }


def test_a_queue_that_is_simply_moving_reports_no_reason():
    """A call queued this instant and not yet judged is not 'blocked'."""
    engineer = ProactiveEngineer.__new__(ProactiveEngineer)
    engineer.pending = [_event()]

    blockage = engineer._queue_blockage()

    assert blockage["blocked_reason"] == ""
    assert blockage["blocked_calls"] == 0


class _StubVoice:
    def __init__(self, reason: str, busy: bool = True) -> None:
        self._reason = reason
        self.is_busy = busy
        self.realtime_active = False

    @property
    def busy_reason(self) -> str:
        return self._reason


def _engineer_with_voice(voice) -> ProactiveEngineer:
    engineer = ProactiveEngineer.__new__(ProactiveEngineer)
    engineer.voice = voice
    engineer._safe_since = 0.0
    return engineer


@pytest.mark.parametrize(
    "reason",
    ["speaking", "speech session open", "wake window armed for another 8s"],
)
def test_a_busy_engineer_names_which_latch_is_holding_it(reason):
    """"Driver or engineer busy" covered seven unrelated states.

    Six of them are the voice controller's and one is the driver's, and telling
    them apart is the whole diagnosis when calls stop arriving.
    """
    engineer = _engineer_with_voice(_StubVoice(reason))
    event = _event()

    assert engineer._safe_to_speak({"connected": True}, event) is False
    assert event["blocked_reason"] == f"engineer busy: {reason}"


def test_the_driver_holding_the_radio_is_not_reported_as_an_engineer_fault():
    engineer = _engineer_with_voice(_StubVoice("", busy=False))
    event = _event()

    blocked = engineer._safe_to_speak(
        {"connected": True, "ptt_pressed": True}, event
    )

    assert blocked is False
    assert event["blocked_reason"] == "driver holding push-to-talk"


def test_an_unsafe_driving_window_carries_the_numbers_that_closed_it():
    engineer = _engineer_with_voice(_StubVoice("", busy=False))
    event = _event()

    blocked = engineer._safe_to_speak(
        {
            "connected": True,
            "speed_kph": 240,
            "brake": 0.9,
            "lateral_g": 3.1,
            "throttle": 0.0,
        },
        event,
    )

    assert blocked is False
    reason = event["blocked_reason"]
    assert reason.startswith("unsafe driving phase")
    for value in ("240", "0.90", "3.10"):
        assert value in reason, reason


def test_a_disconnected_game_is_reported_as_such():
    engineer = _engineer_with_voice(_StubVoice("", busy=False))
    event = _event()

    assert engineer._safe_to_speak({"connected": False}, event) is False
    assert event["blocked_reason"] == "disconnected or paused"
