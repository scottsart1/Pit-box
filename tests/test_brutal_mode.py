"""Brutal mode: a toggle that makes the engineer blunt and sweary about pace.

The mode changes tone and adds one deterministic call. It must never change a
number, never override the safety anchor, and never fire at a car that is
garaged, retired, or crawling behind a safety car.
"""

from __future__ import annotations

import types

import pytest

from pitwall import brain as brain_module
from pitwall.proactive import (
    ROAST_LINES_BOTH,
    ROAST_LINES_BOTTOM_FIVE,
    ROAST_LINES_SLOW_LAP,
    ProactiveEngineer,
)
from pitwall.settings_service import SETTINGS_SPEC, coerce


class _Brain:
    def __init__(self, database):
        self.database = database

    async def proactive(self, event):
        return "narrated"

    async def record_spoken_call(self, text):
        return None


class _Voice:
    is_busy = False

    async def speak_text(self, text):
        return True


class _Setup:
    async def learn_current_session(self):
        return True


class _Strategy:
    async def recompute(self):
        return {}


async def _engineer(stack):
    store, database, _, _, _, _ = stack
    engineer = ProactiveEngineer(
        store,
        _Brain(database),  # type: ignore[arg-type]
        _Voice(),  # type: ignore[arg-type]
        _Setup(),  # type: ignore[arg-type]
        _Strategy(),  # type: ignore[arg-type]
    )

    async def no_refresh(self, state):
        return state

    engineer._refresh_strategy_if_needed = types.MethodType(no_refresh, engineer)
    await store.update(
        connected=True,
        session_uid=7,
        game_paused=False,
        mode_profile="race",
        current_lap=6,
        total_laps=40,
        speed_kph=240,
        fuel_laps_delta=2.0,
        player_position=10,
        active_cars=20,
    )
    await store.mutate(
        lambda state: (
            state.proactive.update({"enabled": True, "cadence_laps": 10}),
            state.analysis.update(
                {
                    "last_lap_analyzed": 5,
                    "progress": {
                        "lap_num": 5,
                        "position": 10,
                        "lap_time": "1:31.200",
                        "target": "1:30.400",
                        "delta_to_target_s": 0.8,
                    },
                    "target": {"target": "1:30.400"},
                }
            ),
        )
    )
    await engineer._reset_for_session(7)
    return store, engineer


def _roasts(engineer):
    return [item for item in engineer.pending if item["type"] == "pace_roast"]


# --------------------------------------------------------------------------
# Persona
# --------------------------------------------------------------------------


def test_brutal_brief_sits_between_custom_persona_and_safety_anchor(monkeypatch):
    monkeypatch.setattr(brain_module.settings, "engineer_name", "Mark")
    monkeypatch.setattr(brain_module.settings, "engineer_persona", "Mention the title fight.")
    monkeypatch.setattr(brain_module.settings, "radio_verbosity", "standard")
    monkeypatch.setattr(brain_module.settings, "brutal_mode", True)
    composed = brain_module.compose_persona(brain_module.PERSONA)
    assert "Brutal mode is ON" in composed
    assert "No slurs" in composed
    assert composed.index("Mention the title fight.") < composed.index("Brutal mode is ON")
    # The safety anchor is still the last word, and the base rules survive.
    assert composed.rstrip().endswith(brain_module._SAFETY_ANCHOR)
    assert "Never invent a number" in composed


def test_brutal_off_leaves_the_persona_untouched(monkeypatch):
    monkeypatch.setattr(brain_module.settings, "engineer_name", "Mark")
    monkeypatch.setattr(brain_module.settings, "engineer_persona", "")
    monkeypatch.setattr(brain_module.settings, "radio_verbosity", "standard")
    monkeypatch.setattr(brain_module.settings, "brutal_mode", False)
    assert brain_module.compose_persona(brain_module.PERSONA) == brain_module.PERSONA


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def test_brutal_mode_is_a_live_dashboard_setting():
    spec = SETTINGS_SPEC["brutal_mode"]
    assert spec["type"] == "bool"
    assert spec["group"] == "Engineer"
    assert not spec.get("restart"), "the DRIVE toggle must apply immediately"
    assert coerce("brutal_mode", "true") is True
    assert coerce("brutal_mode", False) is False
    with pytest.raises(ValueError):
        coerce("brutal_mode", "sometimes")


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slow_lap_earns_a_roast_once_per_lap(stack, monkeypatch):
    store, engineer = await _engineer(stack)
    monkeypatch.setattr("pitwall.proactive.settings.brutal_mode", True)
    await engineer._detect(await store.snapshot_analysis())
    roasts = _roasts(engineer)
    assert len(roasts) == 1
    payload = roasts[0]["payload"]
    assert payload["reason"] == "slow_lap"
    assert payload["delta_to_target_s"] == 0.8
    assert payload["lap"] == 5
    assert payload["bottom_five"] is False

    # Another detection pass on the same analysed lap does not roast again.
    await engineer._detect(await store.snapshot_analysis())
    assert len(_roasts(engineer)) == 1


@pytest.mark.asyncio
async def test_bottom_five_earns_a_roast_even_on_a_lap_at_target(stack, monkeypatch):
    store, engineer = await _engineer(stack)
    monkeypatch.setattr("pitwall.proactive.settings.brutal_mode", True)
    await store.update(player_position=17, active_cars=20)
    await store.mutate(
        lambda state: state.analysis["progress"].update({"delta_to_target_s": -0.1})
    )
    await engineer._detect(await store.snapshot_analysis())
    roasts = _roasts(engineer)
    assert len(roasts) == 1
    payload = roasts[0]["payload"]
    assert payload["reason"] == "bottom_five"
    assert payload["position"] == 17
    assert payload["active_cars"] == 20
    assert payload["delta_to_target_s"] is None


@pytest.mark.asyncio
async def test_slow_and_bottom_five_together_is_one_call(stack, monkeypatch):
    store, engineer = await _engineer(stack)
    monkeypatch.setattr("pitwall.proactive.settings.brutal_mode", True)
    await store.update(player_position=20, active_cars=20)
    await engineer._detect(await store.snapshot_analysis())
    roasts = _roasts(engineer)
    assert len(roasts) == 1
    assert roasts[0]["payload"]["reason"] == "both"


@pytest.mark.asyncio
async def test_no_roast_when_the_mode_is_off(stack, monkeypatch):
    store, engineer = await _engineer(stack)
    monkeypatch.setattr("pitwall.proactive.settings.brutal_mode", False)
    await store.update(player_position=20, active_cars=20)
    await engineer._detect(await store.snapshot_analysis())
    assert _roasts(engineer) == []


@pytest.mark.asyncio
async def test_a_lap_within_tolerance_in_midfield_is_left_alone(stack, monkeypatch):
    store, engineer = await _engineer(stack)
    monkeypatch.setattr("pitwall.proactive.settings.brutal_mode", True)
    await store.mutate(
        lambda state: state.analysis["progress"].update({"delta_to_target_s": 0.2})
    )
    await engineer._detect(await store.snapshot_analysis())
    assert _roasts(engineer) == []


@pytest.mark.asyncio
async def test_no_roast_under_a_safety_car_or_in_practice(stack, monkeypatch):
    store, engineer = await _engineer(stack)
    monkeypatch.setattr("pitwall.proactive.settings.brutal_mode", True)
    await store.update(race_control_phase="safety_car")
    await engineer._detect(await store.snapshot_analysis())
    assert _roasts(engineer) == []

    await store.update(race_control_phase="green", mode_profile="practice")
    await engineer._detect(await store.snapshot_analysis())
    assert _roasts(engineer) == []


@pytest.mark.asyncio
async def test_a_small_field_has_no_bottom_five(stack, monkeypatch):
    store, engineer = await _engineer(stack)
    monkeypatch.setattr("pitwall.proactive.settings.brutal_mode", True)
    await store.update(player_position=4, active_cars=4)
    await store.mutate(
        lambda state: state.analysis["progress"].update({"delta_to_target_s": 0.0})
    )
    await engineer._detect(await store.snapshot_analysis())
    assert _roasts(engineer) == []


@pytest.mark.asyncio
async def test_switching_the_mode_off_drops_a_queued_roast(stack, monkeypatch):
    store, engineer = await _engineer(stack)
    monkeypatch.setattr("pitwall.proactive.settings.brutal_mode", True)
    await engineer._detect(await store.snapshot_analysis())
    assert len(_roasts(engineer)) == 1
    monkeypatch.setattr("pitwall.proactive.settings.brutal_mode", False)
    selected = engineer._select(await store.snapshot_analysis())
    assert selected is None or selected["type"] != "pace_roast"
    assert _roasts(engineer) == []


@pytest.mark.asyncio
async def test_the_driver_can_silence_the_roasts_by_radio(stack, monkeypatch):
    store, engineer = await _engineer(stack)
    monkeypatch.setattr("pitwall.proactive.settings.brutal_mode", True)
    await store.update(
        standing_instructions=[
            {"rule": "Do not raise roasting unless the driver asks."}
        ]
    )
    await engineer._detect(await store.snapshot_analysis())
    assert _roasts(engineer) == []


# --------------------------------------------------------------------------
# Spoken lines
# --------------------------------------------------------------------------


def test_roast_lines_carry_the_fact_that_earned_them():
    slow = ProactiveEngineer.roast_text(
        {"reason": "slow_lap", "lap": 5, "delta_to_target_s": 0.8,
         "target": "1:30.400", "lap_time": "1:31.200"}
    )
    assert "0.8" in slow
    bottom = ProactiveEngineer.roast_text(
        {"reason": "bottom_five", "lap": 5, "position": 17, "active_cars": 20,
         "delta_to_target_s": None}
    )
    assert "P17" in bottom
    both = ProactiveEngineer.roast_text(
        {"reason": "both", "lap": 5, "position": 20, "active_cars": 20,
         "delta_to_target_s": 1.25, "target": "1:30.400"}
    )
    assert "P20" in both
    assert "1.2" in both or "1.3" in both


def test_roast_lines_rotate_by_lap_and_never_leak_a_placeholder():
    seen = set()
    for lap in range(1, 20):
        for reason, extra in (
            ("slow_lap", {"delta_to_target_s": 0.6}),
            ("bottom_five", {"position": 18, "active_cars": 20}),
            ("both", {"position": 18, "active_cars": 20, "delta_to_target_s": 0.6}),
        ):
            text = ProactiveEngineer.roast_text({"reason": reason, "lap": lap, **extra})
            assert "{" not in text and "}" not in text
            assert text.strip()
            seen.add(text)
    assert len(seen) >= len(ROAST_LINES_SLOW_LAP) + len(ROAST_LINES_BOTTOM_FIVE) + len(ROAST_LINES_BOTH)


def test_the_example_line_is_in_the_register():
    assert any("Bitch, do you mind driving faster?" in line for line in ROAST_LINES_SLOW_LAP)
    assert any("Bitch, do you mind driving faster?" in line for line in ROAST_LINES_BOTTOM_FIVE)


def test_fallback_text_routes_a_roast_event_to_the_roast_lines():
    event = {
        "type": "pace_roast",
        "payload": {"reason": "slow_lap", "lap": 3, "delta_to_target_s": 0.9, "target": "1:30.0"},
    }
    assert ProactiveEngineer._fallback_text(event, {}) == ProactiveEngineer.roast_text(event["payload"])
