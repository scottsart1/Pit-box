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
    engineer._wide_safe_since = 0.0
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


# --- head-of-line blocking ---------------------------------------------------
#
# Delivery used to stop at the first call it could not speak. Every condition
# left in _safe_to_speak varies from call to call, so the call at the head being
# unspeakable said nothing about the rest — and the ones behind it waited there
# until they expired. Across one driver's recorded history that cost 9% delivery
# on battery warnings, 11% on safety-car delta breaches and 36% on penalties.


def _engineer(voice=None) -> ProactiveEngineer:
    engineer = ProactiveEngineer.__new__(ProactiveEngineer)
    engineer.voice = voice or _StubVoice("", busy=False)
    engineer._safe_since = 0.0
    engineer._wide_safe_since = 0.0
    engineer._last_spoken_at = 0.0
    engineer.pending = []
    return engineer


_FLAT_OUT = {
    "connected": True,
    "speed_kph": 210,
    "brake": 0.0,
    "lateral_g": 0.4,
    "throttle": 0.30,  # attacking, but below the 0.45 the strict window wants
}


def test_a_battery_call_is_not_starved_by_a_call_that_lacks_its_window():
    """energy_low carries a deliberately wider window. It has to be reachable.

    At 30% throttle the strict window is shut, so the rival-pace call in front
    cannot be spoken — but the battery call behind it can, and used never to be
    asked.
    """
    engineer = _engineer()
    now = time.time()
    rival = _event(type="rival_pace", queued_at=now - 30, deliver_by=now + 600)
    battery = _event(type="energy_low", queued_at=now - 5, deliver_by=now + 600)
    engineer.pending = [rival, battery]

    # The car has been at this throttle for a while, so the wide window has been
    # open long enough to satisfy the hold. The strict one has never opened.
    engineer._update_safe_window(_FLAT_OUT)
    engineer._wide_safe_since = time.monotonic() - 5.0
    assert engineer._safe_since == 0.0, "the strict window is shut at this throttle"

    assert engineer._safe_to_speak(_FLAT_OUT, rival) is False
    assert engineer._safe_to_speak(_FLAT_OUT, battery) is True, (
        "the battery call cleared its own window and was still made to wait out "
        "a stricter one it was never asked to meet"
    )
    # And the candidate order still puts the older call first, so only walking
    # past it reaches the battery call.
    assert engineer._candidates(_FLAT_OUT)[0] is rival


def test_an_overdue_call_is_reachable_behind_a_call_that_is_not():
    """Overdue calls accept a rougher stretch of road. Same starvation."""
    engineer = _engineer()
    now = time.time()
    patient = _event(type="rival_pace", queued_at=now - 60, deliver_by=now + 600)
    overdue = _event(type="tyre_wear", queued_at=now - 10, deliver_by=now - 1)
    engineer.pending = [patient, overdue]

    assert engineer._safe_to_speak(_FLAT_OUT, patient) is False
    assert engineer._safe_to_speak(_FLAT_OUT, overdue) is True


def test_a_critical_call_is_reachable_during_an_open_conversation():
    """A conversation blocks chatter, never a critical call."""

    class _Talking(_StubVoice):
        def __init__(self):
            super().__init__("", busy=True)
            self.realtime_active = True

    engineer = _engineer(_Talking())
    calm = {"connected": True, "speed_kph": 40}
    chatter = _event(type="progress_update", queued_at=time.time() - 30)
    urgent = _event(type="penalty", critical=True, queued_at=time.time())

    assert engineer._safe_to_speak(calm, chatter) is False
    assert engineer._safe_to_speak(calm, urgent) is True


def test_the_shared_safe_clock_belongs_to_the_driving_not_to_a_call():
    """One call failing the strict window must not reset the clock for another.

    The clock measures how long the car has been calm. Resetting it inside the
    per-call check meant a call with a wider window kept having the ground taken
    out from under it by the call in front.
    """
    engineer = _engineer()
    calm = {"connected": True, "speed_kph": 40}
    engineer._update_safe_window(calm)
    established = engineer._safe_since
    assert established > 0

    # A call that cannot use the window is judged and rejected...
    engineer._safe_to_speak(_FLAT_OUT, _event(type="rival_pace"))
    # ...but it is the *driving* that owns the clock, and re-reading a calm
    # state must not have restarted it.
    engineer._update_safe_window(calm)
    assert engineer._safe_since == established


def test_a_globally_blocked_engine_is_reported_on_every_waiting_call():
    """When nothing can speak, every call says so — not just the head one."""
    engineer = _engineer(_StubVoice("speech session open", busy=True))
    events = [_event(type=f"e{i}") for i in range(3)]
    engineer.pending = events

    assert engineer._engine_block_reason({"connected": True}) == (
        "engineer busy: speech session open"
    )
    for event in events:
        assert engineer._safe_to_speak({"connected": True}, event) is False
        assert event["blocked_reason"] == "engineer busy: speech session open"


def test_a_disconnected_game_blocks_the_engine_not_just_one_call():
    engineer = _engineer()
    assert engineer._engine_block_reason({"connected": False}) == "disconnected or paused"
    assert engineer._engine_block_reason({"connected": True, "game_paused": True}) == (
        "disconnected or paused"
    )
    assert engineer._engine_block_reason({"connected": True}) is None


def test_a_long_block_is_reported_as_long():
    """Delivery re-judges ten times a second; the wait must still be real."""
    event = _event()
    ProactiveEngineer._mark_blocked(event, "engineer busy: speech session open")
    first = event["blocked_at"]
    for _ in range(20):  # twenty more passes of the same reason
        ProactiveEngineer._mark_blocked(event, "engineer busy: speech session open")
    assert event["blocked_at"] == first, (
        "re-stamping on every pass reports every stall as instantaneous"
    )
    ProactiveEngineer._mark_blocked(event, "unsafe driving phase (speed 240)")
    assert event["blocked_at"] >= first, "a new reason starts its own clock"
