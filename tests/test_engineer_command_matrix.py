"""Exercise actual radio/tool outputs against independently specified facts."""

import time

import pytest

from pitwall.brain import EngineerBrain
from pitwall.config import settings


async def seed_radio(stack, monkeypatch):
    store, database, _, _, _, tools = stack
    brain = EngineerBrain(store, tools, database)

    async def no_provider(*args, **kwargs):
        pytest.fail("This command must be answered locally, without a provider call")

    monkeypatch.setattr(brain, "_run", no_provider)
    for group in (1, 2, 5, 6, 7, 10, 16):
        await store.mark_packet(2026, 26, 54321, packet_id=group)
    await store.update(
        regulations_2026=True, session_type="Race", mode_profile="race",
        current_lap=8, total_laps=20, player_position=3, player_car_index=0,
        fuel_laps_delta=-0.4, fuel_kg=25, ers_pct=62,
        overtake_available=True, overtake_active=False,
        active_aero_mode=0, active_aero_available=True,
        weather="Clear", weather_forecast=[{"time_offset_min": 15, "rain_pct": 20}],
        rain_next_15_pct=20, corner_cutting_warnings=2, penalties_s=3,
        damage={"front_left_wing": 6, "floor": 4},
        tyre={"compound": "MEDIUM", "age_laps": 5, "wear": [21, 22, 24, 28],
              "inner_temps_c": [92, 94, 96, 98]},
    )
    return store, tools, brain


@pytest.mark.asyncio
@pytest.mark.parametrize("command,fragments", [
    ("fuel status", ["minus 0.4 laps"]),
    ("battery", ["62 percent", "Overtake Mode is available"]),
    ("active aero", ["Cornering Mode", "Straight Line Mode is available"]),
    ("what position am I in", ["P3"]),
    ("what tyres am I on", ["Mediums", "5 laps", "rear-right", "28 percent"]),
    ("tyre temperatures", ["92/94 C", "96/98 C"]),
    ("weather forecast", ["Clear", "20 percent"]),
    ("damage report", ["6 percent", "4 percent"]),
    ("how many penalties do I have", ["warnings 2", "3 seconds"]),
])
async def test_real_radio_lookup_matches_live_fixture(stack, monkeypatch, command, fragments):
    store, _, brain = await seed_radio(stack, monkeypatch)
    answer = await brain.ask(command)
    for fragment in fragments:
        assert fragment in answer
    state = await store.snapshot_analysis()
    assert state["llm_provider"] == "local"
    assert state["radio_log"][-1]["text"] == answer


@pytest.mark.asyncio
@pytest.mark.parametrize("command,group,forbidden", [
    ("fuel status", 7, "0.4"), ("battery", 7, "62"),
    ("tyre temperatures", 6, "92"), ("what tyres am I on", 10, "28 percent"),
    ("what position am I in", 2, "P3"), ("weather forecast", 1, "20 percent"),
    ("damage report", 10, "6 percent"), ("active aero", 16, "Cornering Mode"),
])
async def test_live_socket_does_not_make_old_fields_current(stack, monkeypatch, command, group, forbidden):
    store, _, brain = await seed_radio(stack, monkeypatch)
    await store.mutate(lambda state: state.packet_group_freshness.update({str(group): time.time() - 100}))
    # An unrelated packet really arrives, keeping the receiver connected.
    await store.mark_packet(2026, 26, 54321, packet_id=0)
    answer = await brain.ask(command)
    assert "stale" in answer
    assert forbidden not in answer


@pytest.mark.asyncio
async def test_missing_overtake_flags_are_unknown_not_false(stack, monkeypatch):
    store, tools, brain = await seed_radio(stack, monkeypatch)
    await store.mutate(lambda state: state.packet_group_freshness.pop("16"))
    answer = await brain.ask("battery")
    assert "62 percent" in answer and "unknown" in answer
    assert "not available" not in answer
    report = await tools.get_ers_report()
    assert report["store_pct"] == 62
    assert report["overtake_available"] is None
    assert report["active_aero_mode"] is None
    assert (await tools.get_energy_plan())["attack_window_open"] is None


@pytest.mark.asyncio
async def test_paused_values_are_labelled_and_no_live_pace_advice(stack, monkeypatch):
    store, tools, brain = await seed_radio(stack, monkeypatch)
    await store.update(game_paused=True, connected=False)
    assert (await brain.ask("fuel status")).startswith("Paused, last confirmed:")
    assert (await tools.get_pace_mode_options())["available"] is False


@pytest.mark.asyncio
async def test_session_change_cannot_reuse_previous_fuel_or_energy(stack, monkeypatch):
    store, tools, brain = await seed_radio(stack, monkeypatch)
    await store.mark_packet(2026, 26, 67890, packet_id=1)
    assert "unavailable" in await brain.ask("fuel status")
    assert (await tools.get_fuel_state())["available"] is False
    assert (await tools.get_ers_report())["store_pct"] is None


@pytest.mark.asyncio
async def test_empty_forecast_does_not_mean_zero_rain_risk(stack, monkeypatch):
    store, _, brain = await seed_radio(stack, monkeypatch)
    await store.update(weather_forecast=[], rain_next_15_pct=0)
    answer = await brain.ask("weather forecast")
    assert "no rain forecast" in answer
    assert "0 percent" not in answer


@pytest.mark.asyncio
@pytest.mark.parametrize("samples,expected", [
    ([], (None, None, None)),
    ([{"time_offset_min": 5, "rain_pct": 0}], (None, None, 5)),
    ([{"time_offset_min": 30, "rain_pct": 0}], (None, 0, 30)),
    ([{"time_offset_min": 15, "rain_pct": 0}, {"time_offset_min": 30, "rain_pct": 0}], (0, 0, 30)),
])
async def test_session_overview_does_not_convert_missing_forecast_to_zero(stack, monkeypatch, samples, expected):
    store, tools, _ = await seed_radio(stack, monkeypatch)
    await store.update(weather_forecast=samples, rain_next_15_pct=0, rain_next_30_pct=0)
    result = await tools.get_session_overview()
    assert (result["rain_next_15_pct"], result["rain_next_30_pct"], result["forecast_horizon_min"]) == expected
    assert result["forecast_available"] is bool(samples)


@pytest.mark.asyncio
async def test_race_update_does_not_claim_zero_rain_when_forecast_is_missing(stack, monkeypatch):
    store, _, brain = await seed_radio(stack, monkeypatch)
    # Exercise the legacy composite renderer directly; normal routing currently
    # delegates race updates to the model and is covered by the overview tests.
    monkeypatch.setattr(brain, "_defers_to_model", lambda *args: False)
    await store.update(weather_forecast=[], rain_next_15_pct=0)
    answer = await brain.ask("race update")
    assert "rain forecast unavailable" in answer
    assert "rain risk 0" not in answer
    assert "0 percent" not in answer


@pytest.mark.asyncio
async def test_later_forecast_sample_is_not_a_fifteen_minute_prediction(stack, monkeypatch):
    store, _, brain = await seed_radio(stack, monkeypatch)
    await store.update(weather_forecast=[{"time_offset_min": 30, "rain_pct": 0}], rain_next_15_pct=0)
    answer = await brain.ask("weather forecast")
    assert "no 15-minute rain probability" in answer
    assert "rain15 unknown%" in await brain.situation_header(include_strategy=False)


@pytest.mark.asyncio
async def test_final_lap_fuel_estimate_includes_current_lap_and_names_assumption(stack, monkeypatch):
    store, tools, _ = await seed_radio(stack, monkeypatch)
    await store.update(current_lap=20)
    result = await tools.get_pace_mode_options()
    assert result["laps_remaining"] == 1
    assert result["estimated_cost_s_per_lap"] == round(0.4 * settings.strategy_fuel_save_s_per_lap, 3)
    assert "not measured" in result["cost_basis"]
    await store.update(fuel_laps_delta=1.0)
    result = await tools.get_pace_mode_options()
    assert "mix" not in result["recommendation"]


@pytest.mark.asyncio
async def test_low_battery_does_not_disable_legacy_drs(stack, monkeypatch):
    store, tools, _ = await seed_radio(stack, monkeypatch)
    await store.update(regulations_2026=False, ers_pct=10, drs_allowed=True)
    result = await tools.get_energy_plan()
    assert result["aid_available"] is True
    assert "independent" in result["recommendation"]
    assert "hold DRS" not in result["recommendation"]


@pytest.mark.asyncio
async def test_current_gap_does_not_guarantee_next_lap_aid_or_restricted_tyre_age(stack, monkeypatch):
    store, tools, _ = await seed_radio(stack, monkeypatch)

    def rival(state):
        driver = state.drivers[1]
        driver.name, driver.active, driver.position = "Rival", True, 2
        driver.gap_to_player_s, driver.restricted = -0.5, True
    await store.mutate(rival)
    await store.update(overtake_available=False)
    result = await tools.get_attack_plan("ahead")
    assert result["assist_available"] is False
    assert result["attack_window"] != "next lap"
    assert result["tyre_age_offset_laps"] is None
    assert result["rival_overtake_available"] is None


@pytest.mark.asyncio
async def test_model_header_explicitly_marks_missing_packet_groups(stack, monkeypatch):
    store, _, brain = await seed_radio(stack, monkeypatch)
    await store.mutate(lambda state: state.packet_group_freshness.pop("7"))
    header = await brain.situation_header(include_strategy=False)
    assert '"fuel_battery_compound":{"status":"unavailable"' in header
    assert '"tyre_temperatures":{"status":"live"' in header
    assert "fuel delta unknown" in header
    assert "Overtake Mode" in header


@pytest.mark.asyncio
async def test_actual_radio_commit_then_clear_removes_the_entire_constraint(stack, monkeypatch):
    store, _, brain = await seed_radio(stack, monkeypatch)
    answer = await brain.ask("Box lap 12 for hards")
    assert "box lap 12 for hards" in answer.lower()
    state = await store.snapshot_analysis()
    assert state["strategy_override"]["next_box_lap"] == 12
    assert state["strategy_override"]["next_compound"] == "HARD"
    answer = await brain.ask("Clear my strategy override")
    assert "automatic ranking restored" in answer
    state = await store.snapshot_analysis()
    assert not state["strategy_override"]["enabled"]
    assert state["strategy_override"]["plan"] == {}
    assert state["strategy_override"]["next_box_lap"] is None
    assert not state["strategy"]["recommended"].get("driver_override", {}).get("active")


@pytest.mark.parametrize("command", [
    "Don't clear strategy override", "Do not cancel my strategy override",
    "Don't unlock strategy", "Do not choose the best strategy",
])
def test_negated_cancellation_never_clears_driver_plan(command):
    assert EngineerBrain._strategy_override_action(command, 8) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [
    "Is rain expected, and should I switch to intermediates now?",
    "It looks wet; should we switch to intermediates now?",
    "Can I clear my strategy override?",
    "Should we clear the strategy override?",
])
async def test_strategy_question_never_commits_or_clears_an_override(stack, monkeypatch, command):
    store, _, brain = await seed_radio(stack, monkeypatch)
    await brain.ask("Box lap 12 for hards")
    before = (await store.snapshot_analysis())["strategy_override"]

    async def advice(*args, **kwargs):
        return "I will assess the options; the current plan remains unchanged."

    monkeypatch.setattr(brain, "_run", advice)
    answer = await brain.ask(command)
    assert "current plan remains unchanged" in answer
    assert (await store.snapshot_analysis())["strategy_override"] == before


def test_red_flag_action_bypasses_normal_pit_stop_speech_guards():
    plan = {"action": "red_flag_tyre_change", "box_lap": 8, "fit_compound": "MEDIUM",
            "instruction": "Change to a fresh medium set during the suspension.",
            "positions_gained_vs_stay_out": 0, "positions_lost_by_stopping": 2}
    answer = EngineerBrain._spoken_strategy_instruction(plan, 8, {"compound": "MEDIUM", "age_laps": 1})
    assert answer == plan["instruction"]
    assert "Box" not in answer and "Stay out" not in answer


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [
    "strategy update",
    "Red flag: can I change tyres, and what is the best restart strategy and alternative?",
    "Give me the full pit strategy and its best alternative.",
])
async def test_actual_red_flag_ask_keeps_alternative_and_inventory_limit_without_live_feed(stack, monkeypatch, command):
    store, _, brain = await seed_radio(stack, monkeypatch)
    await store.update(
        connected=False, red_flag_active=True, race_control_phase="red_flag",
        radio_verbosity="terse", current_lap=10, total_laps=17,
        tyre_sets=[], tyre={"compound": "MEDIUM", "age_laps": 10, "wear": [65] * 4},
        completed_laps=[{"lap_num": 1, "compound": "HARD", "valid": True, "lap_time_ms": 90000}],
    )
    answer = await brain.ask(command)
    assert "Tyres can be changed" in answer
    assert "Best strategy:" in answer
    assert "Alternative:" in answer
    assert "inventory is unknown" in answer
    assert "fresh " not in answer
    assert "box" not in answer.lower()
