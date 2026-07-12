import pytest


@pytest.mark.asyncio
async def test_lap_analysis_segments_and_persists(stack):
    store, database, _, _, analysis, _ = stack
    trace = []
    for i in range(200):
        d = i * 10.0
        brake = 0.8 if 60 <= i <= 75 or 135 <= i <= 148 else 0.0
        throttle = 0.0 if brake else (0.4 if 76 <= i <= 82 or 149 <= i <= 155 else 1.0)
        speed = (
            300 - (i - 60) * 8
            if 60 <= i <= 72
            else 120 + (i - 72) * 8
            if 72 < i <= 90
            else 280
        )
        if 135 <= i <= 145:
            speed = 280 - (i - 135) * 12
        if 145 < i <= 165:
            speed = 160 + (i - 145) * 6
        trace.append(
            {
                "t": i * 0.1,
                "d": d,
                "speed": max(80, speed),
                "brake": brake,
                "throttle": throttle,
                "gear": 5,
                "lat_g": 1.8 if brake else 0.2,
                "slip": [0, 0, 0, 0],
            }
        )
    lap = {
        "session_uid": 1,
        "track_id": 10,
        "track_name": "Spa",
        "session_type": "Race",
        "mode_profile": "race",
        "lap_num": 2,
        "lap_time_ms": 105000,
        "valid": True,
        "compound": "MEDIUM",
        "tyre_age_start": 1,
        "tyre_age_end": 2,
        "wear_start": [4, 4, 4, 4],
        "wear_end": [7, 7, 7, 7],
        "temps_end": [95, 96, 93, 94],
        "fuel_start_kg": 30,
        "fuel_end_kg": 28,
        "position": 5,
        "pit_status": 0,
        "pit_lane_time_ms": 0,
        "setup": {},
        "trace": trace,
        "created_at": 1.0,
    }
    await store.update(
        track_id=10, track_name="Spa", session_type="Race", total_laps=20, current_lap=3
    )
    await store.mutate(lambda state: state.completed_laps.append(lap))
    result = await analysis.process_lap(lap)
    assert result["last_lap_analyzed"] == 2
    assert len(result["corner_metrics"]) >= 2
    review = await database.track_review(10)
    assert review["laps"]
