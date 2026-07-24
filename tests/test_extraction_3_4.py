"""Regressions for the 3.4 extraction work.

These cover telemetry and analysis that the app already received or computed but
previously discarded, plus two session-classification defects.
"""

from __future__ import annotations

import time

import pytest

from pitwall.analysis import AnalysisEngine
from pitwall.proactive import DAMAGE_FAULT_KEYS, DAMAGE_KEYS, ProactiveEngineer
from pitwall.strategy import StrategyEngine
from pitwall.udp import SESSION_MODE_BY_TYPE_ID, classify_session, mode_profile


def _classify(raw_type_id: int, total_laps: int = 20) -> str:
    _, mode, _, _ = classify_session(
        raw_type_id=raw_type_id,
        total_laps=total_laps,
        current_lap=1,
        session_time_left_s=1_800,
        session_duration_s=1_800,
        session_length_id=4,
    )
    return mode


# --- session classification -------------------------------------------------


def test_sprint_race_is_not_classified_as_a_grand_prix() -> None:
    """Race 2/3 are sprint races and must not inherit grand-prix rules."""
    assert _classify(15) == "race"
    assert _classify(16) == "sprint"
    assert _classify(17) == "sprint"


def test_one_shot_sprint_shootout_is_a_qualifying_session() -> None:
    """The "One-Shot Sprint Shoot" label is truncated and never matched "shootout"."""
    assert _classify(14) == "qualifying"
    for type_id in (10, 11, 12, 13):
        assert _classify(type_id) == "qualifying"


def test_label_fallback_still_recognises_truncated_shootout() -> None:
    assert mode_profile("One-Shot Sprint Shoot") == "qualifying"
    assert mode_profile("Sprint Shootout 2") == "qualifying"


def test_every_known_session_type_has_a_mode() -> None:
    for type_id in range(19):
        assert SESSION_MODE_BY_TYPE_ID.get(type_id), f"missing mode for {type_id}"


def test_two_compound_rule_does_not_apply_to_a_sprint() -> None:
    """A sprint has no mandatory tyre change, so demanding one is wrong advice."""
    sprint = {"mode_profile": "sprint", "tyre": {"compound": "MEDIUM"}}
    race = {"mode_profile": "race", "tyre": {"compound": "MEDIUM"}}
    assert StrategyEngine._compound_rule(sprint)["applies"] is False
    assert StrategyEngine._compound_rule(race)["applies"] is True


# --- quantified corner coaching ---------------------------------------------


def test_reference_deltas_are_signed_against_the_personal_best() -> None:
    metric = {"brake_point_m": 208.0, "min_speed_kph": 116.0, "throttle_on_m": 250.0}
    reference = {"brake_point_m": 200.0, "min_speed_kph": 124.0, "throttle_on_m": 240.0}
    deltas = AnalysisEngine._reference_deltas(metric, reference)
    assert deltas["brake_point_delta_m"] == 8.0
    assert deltas["apex_speed_delta_kph"] == -8.0
    assert deltas["throttle_on_delta_m"] == 10.0


def test_corner_instruction_quotes_the_measured_numbers() -> None:
    instruction = AnalysisEngine.corner_instruction(
        {
            "name": "Corner 3",
            "cause": "late brake / overslow",
            "brake_point_delta_m": 8.0,
            "apex_speed_delta_kph": -6.0,
        }
    )
    assert "8 metres" in instruction
    assert "6 km/h" in instruction


def test_corner_instruction_falls_back_without_deltas() -> None:
    """A corner with no personal-best match still produces usable coaching."""
    instruction = AnalysisEngine.corner_instruction(
        {"name": "Corner 3", "cause": "late brake / overslow"}
    )
    assert "Corner 3" in instruction
    assert "metres" not in instruction


def test_corner_instruction_suppresses_noise_level_deltas() -> None:
    instruction = AnalysisEngine.corner_instruction(
        {
            "name": "Corner 7",
            "cause": "low apex speed",
            "apex_speed_delta_kph": -0.4,
        }
    )
    assert "km/h" not in instruction


# --- sector times -----------------------------------------------------------


def test_sector_fields_require_a_complete_split() -> None:
    """Partial history would otherwise persist a misleading zero sector."""
    complete = {"completed_laps": [{"lap_num": 4, "s1_ms": 1, "s2_ms": 2, "s3_ms": 3}]}
    partial = {"completed_laps": [{"lap_num": 4, "s1_ms": 1, "s2_ms": 0, "s3_ms": 0}]}
    assert AnalysisEngine.sector_fields(complete, 4) == {
        "s1_ms": 1,
        "s2_ms": 2,
        "s3_ms": 3,
    }
    assert AnalysisEngine.sector_fields(partial, 4) == {}
    assert AnalysisEngine.sector_fields(complete, 9) == {}


def test_lap_summary_carries_sectors_into_timing_fields() -> None:
    engine = AnalysisEngine.__new__(AnalysisEngine)
    summary = engine.build_lap_summary(
        {"lap_num": 3, "lap_time_ms": 95_000, "valid": True},
        [],
        None,
        {"s1_ms": 30_000, "s2_ms": 33_000, "s3_ms": 32_000},
    )
    assert summary["timing_fields"]["s2_ms"] == 33_000


@pytest.mark.asyncio
async def test_sector_backfill_fills_rows_saved_before_history_arrived(stack) -> None:
    _, database, _, _, _, _ = stack
    lap = {
        "session_uid": 2**63 + 7,
        "track_id": 10,
        "track_name": "Spa",
        "session_type": "Race",
        "lap_num": 5,
        "lap_time_ms": 105_000,
        "valid": True,
        "compound": "MEDIUM",
        "trace": [],
        "setup": {},
    }
    await database.save_lap(lap, [])
    stored = await database.recent_laps(10, 5)
    assert stored[0]["s1_ms"] == 0

    updated = await database.backfill_lap_sectors(
        lap["session_uid"],
        [{"lap_num": 5, "s1_ms": 31_000, "s2_ms": 36_000, "s3_ms": 38_000}],
    )
    assert updated == 1
    stored = await database.recent_laps(10, 5)
    assert (stored[0]["s1_ms"], stored[0]["s2_ms"], stored[0]["s3_ms"]) == (
        31_000,
        36_000,
        38_000,
    )

    # Re-running must not overwrite a complete split a second time.
    assert (
        await database.backfill_lap_sectors(
            lap["session_uid"],
            [{"lap_num": 5, "s1_ms": 1, "s2_ms": 2, "s3_ms": 3}],
        )
        == 0
    )


@pytest.mark.asyncio
async def test_backfill_ignores_incomplete_splits(stack) -> None:
    _, database, _, _, _, _ = stack
    assert await database.backfill_lap_sectors(1, [{"lap_num": 2, "s1_ms": 5}]) == 0


# --- session results --------------------------------------------------------


@pytest.mark.asyncio
async def test_session_result_is_recorded_on_final_classification(stack) -> None:
    """ended_at and result_position were declared but never written before 3.4."""
    store, database, _, _, _, _ = stack
    await store.update(
        session_uid=2**63 + 11,
        track_id=10,
        track_name="Spa",
        session_type="Race",
        mode_profile="race",
        total_laps=44,
    )
    await database.upsert_session(await store.snapshot_live())
    sessions = await database.history_query(limit=5)
    assert sessions["sessions"][0]["result_position"] is None
    assert sessions["sessions"][0]["ended_at"] is None

    await store.update(final_classification={"position": 3, "laps": 44})
    await database.upsert_session(await store.snapshot_live())
    row = (await database.history_query(limit=5))["sessions"][0]
    assert row["result_position"] == 3
    assert row["ended_at"] is not None

    # A later write without classification must not erase the recorded result.
    await store.update(final_classification={})
    await database.upsert_session(await store.snapshot_live())
    row = (await database.history_query(limit=5))["sessions"][0]
    assert row["result_position"] == 3


# --- damage signature -------------------------------------------------------


def test_damage_signature_covers_power_unit_and_faults() -> None:
    """Engine, gearbox and ERS/DRS faults were previously invisible to the radio."""
    base = {key: 0 for key in (*DAMAGE_KEYS, *DAMAGE_FAULT_KEYS)}
    baseline = ProactiveEngineer._damage_signature(base)
    for key in ("engine", "gearbox"):
        assert ProactiveEngineer._damage_signature({**base, key: 40}) != baseline
    for key in DAMAGE_FAULT_KEYS:
        assert ProactiveEngineer._damage_signature({**base, key: True}) != baseline


def _still_relevant(kind: str, state: dict) -> bool:
    event = {
        "type": kind,
        "session_uid": 1,
        "expires_at": time.time() + 60,
        "payload": {},
    }
    return ProactiveEngineer._event_still_relevant(event, {"session_uid": 1, **state})


def test_damage_relevance_accepts_power_unit_and_fault_events() -> None:
    """The relevance filter must not discard the events the signature now raises."""
    assert _still_relevant("damage", {"damage": {"engine": 45}}) is True
    assert _still_relevant("damage", {"damage": {"ers_fault": True}}) is True
    assert (
        _still_relevant("damage", {"damage": {key: 0 for key in DAMAGE_KEYS}}) is False
    )


# --- safety car delta -------------------------------------------------------


def test_safety_car_delta_relevance_tracks_phase_and_delta() -> None:
    under = {"race_control_phase": "vsc", "safety_car_delta_s": -0.8}
    compliant = {"race_control_phase": "vsc", "safety_car_delta_s": 1.4}
    green = {"race_control_phase": "green", "safety_car_delta_s": -0.8}
    assert _still_relevant("safety_car_delta", under) is True
    assert _still_relevant("safety_car_delta", compliant) is False
    assert _still_relevant("safety_car_delta", green) is False


# --- fallback radio text ----------------------------------------------------


def test_every_proactive_event_type_has_specific_fallback_text() -> None:
    """A failed model call must still produce a useful call, not a generic notice."""
    generic = "Engineer update available on the dashboard."
    payloads: dict[str, dict] = {
        "progress_update": {"lap": 12, "target": {"target": "1:44.100"}},
        "corner_coaching": {"name": "Corner 4", "instruction": "Rebuild turn 4."},
        "race_control": {"to": "safety_car"},
        "weather_crossover": {"rain_15_pct": 70},
        "fuel_warning": {},
        "tyre_wear": {"wear_fl_fr_rl_rr": [70, 71, 66, 65]},
        "penalty": {"penalties_s": 5},
        "warning": {"corner_cutting_warnings": 3},
        "damage": {"front_left_wing": 40},
        "lap_deleted": {"lap": 4},
        "blue_flag": {"flag": "blue"},
        "penalty_service": {"drive_through": 1},
        "quali_clear_air": {"target": {"target": "1:16.900"}},
        "compound_requirement": {"laps_remaining": 5},
        "strategy_change": {"instruction": "Box lap 20 for hards."},
        "rival_pitted": {"driver": "Norris"},
        "undercut_threat": {"driver": "Norris"},
        "safety_car_delta": {"safety_car_delta_s": -0.6, "phase": "vsc"},
    }
    state = {"fuel_laps_delta": -0.6}
    for kind, payload in payloads.items():
        text = ProactiveEngineer._fallback_text(
            {"type": kind, "payload": payload}, state
        )
        assert text and text != generic, f"{kind} has no specific fallback text"


# --- participants: team id and teammate -------------------------------------


@pytest.mark.asyncio
async def test_participants_populate_team_id_and_teammate() -> None:
    """team_id was available in the packet but never read, leaving team empty."""
    from f1.packets import PacketHeader, PacketParticipantsData

    from pitwall.state import StateStore
    from pitwall.udp import F1DatagramProtocol

    store = StateStore()
    protocol = F1DatagramProtocol(store)

    head = PacketHeader()
    head.packet_format = 2026
    head.game_year = 25
    head.packet_version = 1
    head.packet_id = 4
    head.session_uid = 123
    head.player_car_index = 0

    packet = PacketParticipantsData()
    packet.header = head
    packet.num_active_cars = 3
    for index, team_id in enumerate((5, 5, 7)):
        packet.participants[index].team_id = team_id
        packet.participants[index].your_telemetry = 1
    await protocol._handle(packet)

    # Read the store directly: the serialised snapshot filters out drivers that
    # have no classified position yet.
    drivers = store.state.drivers
    assert drivers[1].team_id == 5
    # Same team as the player (index 0) is the teammate; the player is not.
    assert drivers[1].is_teammate is True
    assert drivers[0].is_teammate is False
    assert drivers[2].is_teammate is False


def test_damage_fallback_distinguishes_power_unit_from_aero() -> None:
    def text(payload: dict) -> str:
        return ProactiveEngineer._fallback_text(
            {"type": "damage", "payload": payload}, {}
        )

    assert "ERS" in text({"ers_fault": True})
    assert "DRS" in text({"drs_fault": True})
    assert "Engine" in text({"engine": 40})
    assert "Gearbox" in text({"gearbox": 40})
    assert "Aero" in text({"front_left_wing": 40})
