"""The engineer must stop raising a subject when told to, and stay stopped.

Taken from a recorded Texas session in which the driver asked four different
ways for gearbox and engine reminders to stop, was told "Understood. No further
gearbox reminders." twice, and still received six more damage calls — several
of them recommending a pit stop for an "inspection" that does not exist.
"""

from __future__ import annotations

import pytest

from pitwall.brain import EngineerBrain
from pitwall.proactive import (
    NEVER_SUPPRESSED,
    ProactiveEngineer,
)


def _rule(utterance: str):
    return EngineerBrain._standing_instruction(utterance)


@pytest.mark.parametrize(
    "utterance,expected_subject",
    [
        ("Do not tell me anything about engine damage.", "engine damage"),
        (
            "I fucking told you to shut the fuck up about engine and gear damage",
            "engine and gear damage",
        ),
        ("please stop annoying me about my gearbox damage", "gearbox damage"),
        (
            "can you please confirm that I do not want reminders regarding my gearbox",
            "gearbox",
        ),
        ("stop telling me about the gearbox", "gearbox"),
        ("shut up about engine damage", "engine damage"),
        ("enough about the tyres", "tyres"),
        ("stop going on about my floor damage", "floor damage"),
    ],
)
def test_real_driver_phrasings_are_understood(utterance, expected_subject):
    """A fixed phrase list missed every one of these."""
    rule = _rule(utterance)
    assert rule is not None, utterance
    assert expected_subject in rule


@pytest.mark.parametrize(
    "utterance",
    [
        "what do you think about the strategy",
        "tell me about the gap ahead",
        "how about we box next lap",
        "any news about the weather",
        "give me an update about the tyres",
    ],
)
def test_ordinary_radio_is_not_mistaken_for_a_suppression(utterance):
    assert _rule(utterance) is None, utterance


def test_suppressed_subjects_round_trip():
    rules = [{"rule": _rule("shut up about engine damage")}]
    assert EngineerBrain.suppressed_subjects(rules) == ["engine damage"]


@pytest.mark.parametrize(
    "utterance,silenced,still_allowed",
    [
        ("stop telling me about the gearbox", "damage", "tyre_wear"),
        ("shut up about engine damage", "component_wear", "fuel_warning"),
        ("no more about the fuel", "fuel_warning", "damage"),
        ("stop going on about the tyres", "tyre_wear", "damage"),
        ("enough about corner coaching", "corner_coaching", "damage"),
    ],
)
def test_an_instruction_actually_silences_the_queue(utterance, silenced, still_allowed):
    """Persona text alone only changed wording; the call was still made."""
    rules = [{"rule": _rule(utterance)}]
    assert ProactiveEngineer.is_suppressed(silenced, rules) is True, silenced
    assert ProactiveEngineer.is_suppressed(still_allowed, rules) is False, still_allowed


@pytest.mark.parametrize("event_type", sorted(NEVER_SUPPRESSED))
def test_safety_and_legality_calls_can_never_be_silenced(event_type):
    """A driver may silence a topic, not a red flag or a penalty."""
    broad = [{"rule": "Do not raise damage engine gearbox fuel tyres anything unless"}]
    assert ProactiveEngineer.is_suppressed(event_type, broad) is False


def test_no_instructions_suppresses_nothing():
    for event_type in ("damage", "tyre_wear", "progress_update"):
        assert ProactiveEngineer.is_suppressed(event_type, []) is False
        assert ProactiveEngineer.is_suppressed(event_type, None) is False


def test_damage_is_reported_by_band_not_on_every_change():
    """Six critical calls in fifteen laps came from a per-change signature."""
    engineer = ProactiveEngineer.__new__(ProactiveEngineer)
    engineer._reported_damage_band = 0
    engineer._reported_damage_faults = set()

    fired: list[int] = []

    def would_fire(maximum: int, faults=()) -> bool:
        band = (maximum // 20) * 20
        new_fault = any(f not in engineer._reported_damage_faults for f in faults)
        if (band > engineer._reported_damage_band and maximum > 10) or new_fault:
            engineer._reported_damage_band = max(band, engineer._reported_damage_band)
            engineer._reported_damage_faults.update(faults)
            return True
        return False

    # Damage creeping up one percent at a time, as it does in a real race.
    for maximum in (11, 12, 12, 13, 14, 15, 18, 19):
        if would_fire(maximum):
            fired.append(maximum)
    assert fired == [], "a stable sub-band value must not re-fire"

    assert would_fire(21) is True, "crossing into a new band is news"
    assert would_fire(22) is False
    assert would_fire(45) is True, "a large escalation is news"
    # A newly appearing fault is always news, whatever the band.
    assert would_fire(45, ("ers_fault",)) is True
    assert would_fire(46, ("ers_fault",)) is False


def test_damage_advice_never_recommends_an_impossible_repair():
    """The telemetry exposes a front-wing pit adjustment and nothing else.

    The recorded session repeatedly told the driver to "box this lap for
    immediate gearbox inspection", which is not an action the game or the
    telemetry supports.
    """
    for payload, must_mention in (
        ({"gearbox": 12}, "gearbox"),
        ({"engine": 14}, "engine"),
        ({"floor": 22}, "floor"),
    ):
        text = ProactiveEngineer._fallback_text({"type": "damage", "payload": payload}, {})
        lowered = text.lower()
        assert must_mention in lowered
        assert "cannot be repaired" in lowered
        for forbidden in ("box this lap", "box lap", "inspection", "pit for"):
            assert forbidden not in lowered, f"{payload} -> {text!r}"

    # Front-wing damage is the one case where a stop can actually fix something.
    wing = ProactiveEngineer._fallback_text(
        {"type": "damage", "payload": {"front_left_wing": 34}}, {}
    )
    assert "front-wing" in wing.lower()
    assert "change at a stop" in wing.lower()


def test_persona_forbids_calling_an_unavailable_repair():
    from pitwall.brain import PERSONA

    # Flattened: the brief is hard-wrapped, so phrases span line breaks.
    lowered = " ".join(PERSONA.lower().split())
    assert "only pit-stop repair this telemetry exposes is the front wing" in lowered
    assert "repeating an unactionable damage report is worse than silence" in lowered
    assert "there is no" in lowered and "inspection" in lowered
