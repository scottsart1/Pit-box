"""Regressions for the 3.5 feature set.

Grouped by feature area. Each test drives the deterministic layer directly so
the numeric behaviour is pinned independently of any language model.
"""

from __future__ import annotations

import pytest

from pitwall.analysis import AnalysisEngine
from pitwall.database import PitWallDatabase
from pitwall.setup_advisor import SetupAdvisor
from pitwall.state import StateStore
from pitwall.strategy import StrategyEngine
from pitwall.tools import TelemetryTools


def _lap(uid: int, num: int, ms: int, s1: int, s2: int, s3: int, created: float) -> dict:
    return {
        "session_uid": uid,
        "track_id": 10,
        "track_name": "Spa",
        "session_type": "Race",
        "lap_num": num,
        "lap_time_ms": ms,
        "valid": True,
        "compound": "MEDIUM",
        "s1_ms": s1,
        "s2_ms": s2,
        "s3_ms": s3,
        "trace": [],
        "setup": {},
        "created_at": created,
    }


# --- sector bests / theoretical best (item 13) ------------------------------


@pytest.mark.asyncio
async def test_sector_bests_compose_theoretical_from_independent_minima(stack) -> None:
    _, database, _, _, _, _ = stack
    uid = 2**63 + 1
    await database.upsert_session(
        {"session_uid": uid, "track_id": 10, "track_name": "Spa",
         "session_type": "Race", "mode_profile": "race", "total_laps": 20}
    )
    # Best S1 and S3 live on lap 2; best S2 on lap 1. No single lap holds all.
    await database.save_lap(_lap(uid, 1, 105_000, 34_000, 35_500, 35_000, 1.0), [])
    await database.save_lap(_lap(uid, 2, 104_000, 33_500, 36_200, 34_200, 2.0), [])
    data = await database.sector_bests(10)
    assert int(data["sector_bests"]["s1_ms"]["ms"]) == 33_500
    assert int(data["sector_bests"]["s2_ms"]["ms"]) == 35_500
    assert int(data["sector_bests"]["s3_ms"]["ms"]) == 34_200
    assert data["theoretical_best_ms"] == 33_500 + 35_500 + 34_200
    assert data["personal_best_lap_ms"] == 104_000
    # Theoretical is quicker than any real lap, so time is left on the table.
    assert data["time_left_on_table_ms"] == 104_000 - 103_200


@pytest.mark.asyncio
async def test_sector_bests_ignore_zero_sector_legacy_rows(stack) -> None:
    """Pre-3.4 rows stored zero sectors and must not become the minimum."""
    _, database, _, _, _, _ = stack
    uid = 2**63 + 5
    await database.upsert_session(
        {"session_uid": uid, "track_id": 10, "track_name": "Spa",
         "session_type": "Race", "mode_profile": "race", "total_laps": 20}
    )
    await database.save_lap(_lap(uid, 1, 105_000, 0, 0, 0, 1.0), [])
    await database.save_lap(_lap(uid, 2, 104_000, 33_500, 36_000, 34_000, 2.0), [])
    data = await database.sector_bests(10)
    assert int(data["sector_bests"]["s1_ms"]["ms"]) == 33_500


@pytest.mark.asyncio
async def test_sector_bests_theoretical_none_when_a_sector_missing(stack) -> None:
    _, database, _, _, _, _ = stack
    uid = 2**63 + 6
    await database.upsert_session(
        {"session_uid": uid, "track_id": 10, "track_name": "Spa",
         "session_type": "Race", "mode_profile": "race", "total_laps": 20}
    )
    await database.save_lap(_lap(uid, 1, 105_000, 34_000, 0, 35_000, 1.0), [])
    data = await database.sector_bests(10)
    assert data["theoretical_best_ms"] is None
    assert data["time_left_on_table_ms"] is None


# --- progress trend (item 12) -----------------------------------------------


@pytest.mark.asyncio
async def test_progress_trend_orders_sessions_and_signs_improvement(stack) -> None:
    _, database, _, _, _, _ = stack
    for i, (uid, best) in enumerate([(2**63 + 10, 105_000), (2**63 + 11, 104_000)]):
        await database.upsert_session(
            {"session_uid": uid, "track_id": 10, "track_name": "Spa",
             "session_type": "Race", "mode_profile": "race", "total_laps": 20}
        )
        await database.save_lap(_lap(uid, 1, best, 34_000, 36_000, 35_000, float(i)), [])
    data = await database.progress_trend(10)
    assert data["session_count"] == 2
    # Newer session is quicker, so improvement is negative.
    assert data["improvement_ms"] == 104_000 - 105_000
    assert data["sessions"][0]["best_lap_ms"] == 105_000
    assert data["sessions"][-1]["best_lap_ms"] == 104_000


# --- setup correlation (item 14) --------------------------------------------


@pytest.mark.asyncio
async def test_setup_correlation_ranks_runs_by_score(stack) -> None:
    _, database, _, _, _, _ = stack
    uid = 2**63 + 20
    await database.save_setup_run(uid, 10, "Spa", "race", {"front_wing": 30}, {"top_speed": 330}, 1.20)
    await database.save_setup_run(uid, 10, "Spa", "race", {"front_wing": 40}, {"top_speed": 322}, 0.95)
    data = await database.setup_correlation(10, "race")
    assert data["run_count"] == 2
    assert data["best_run"]["score"] == 0.95
    assert data["best_run"]["setup"]["front_wing"] == 40
    assert data["worst_run"]["score"] == 1.20


@pytest.mark.asyncio
async def test_setup_correlation_worst_run_none_with_single_run(stack) -> None:
    _, database, _, _, _, _ = stack
    uid = 2**63 + 21
    await database.save_setup_run(uid, 10, "Spa", "race", {"front_wing": 30}, {}, 1.0)
    data = await database.setup_correlation(10, "race")
    assert data["best_run"] is not None
    assert data["worst_run"] is None


# --- tool surface -----------------------------------------------------------


@pytest.mark.asyncio
async def test_new_analytics_tools_are_registered_and_callable(stack) -> None:
    store, database, _, _, _, tools = stack
    await store.update(track_id=10, track_name="Spa")
    names = {schema["name"] for schema in tools.schemas()}
    for expected in ("get_sector_bests", "get_progress_trend", "get_setup_correlation"):
        assert expected in names
        assert await tools.call(expected, {} if expected != "get_setup_correlation" else {"profile": "all"})
