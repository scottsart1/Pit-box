"""The canonical-turn machinery behind per-corner causal setup analysis.

Born from the 2026-08-12 follow-up session: the 2026 Motion packet packs
g-forces as int16 milli-g, which merged corner segmentation into one
lap-length zone per lap, and zone extents varied so much between laps that
"time in corner" measured the segmentation, not the driver. These tests pin
the three layers of the fix: unit normalisation, canonical turn models with
fixed measurement windows, and mechanism classification that only blames the
car when the telemetry supports it.
"""

import pytest

from pitwall.analysis import AnalysisEngine
from pitwall.setup_insights import (
    analyze_turn,
    build_turn_model,
    characterize_lock,
    clean_trace,
    measure_turns,
    resolve_adjustments,
)


def _point(d: float, t: float, **overrides) -> dict:
    point = {
        "d": d,
        "t": t,
        "speed": 200,
        "throttle": 1.0,
        "brake": 0.0,
        "gear": 7,
        "lat_g": 0.5,
        "long_g": 0.1,
        "slip": [0.0, 0.0, 0.0, 0.0],
    }
    point.update(overrides)
    return point


def _steady_trace(length_m: float = 1000.0, step_m: float = 5.0, speed_mps: float = 50.0):
    points = []
    d = 0.0
    while d <= length_m:
        points.append(_point(d, d / speed_mps))
        d += step_m
    return points


def test_millig_traces_are_normalised_and_modern_traces_untouched():
    legacy = [_point(i * 5.0, i * 0.1, lat_g=1650.0, long_g=-4000.0) for i in range(30)]
    cleaned = clean_trace(legacy)
    assert cleaned[0]["lat_g"] == pytest.approx(1.65)
    assert cleaned[0]["long_g"] == pytest.approx(-4.0)
    # The shared originals must not be mutated.
    assert legacy[0]["lat_g"] == 1650.0

    modern = [_point(i * 5.0, i * 0.1, lat_g=3.8) for i in range(30)]
    assert clean_trace(modern)[0]["lat_g"] == pytest.approx(3.8)


def test_turn_model_needs_support_and_clips_overlaps():
    def zone(entry, apex, exit_):
        return {"entry_m": entry, "apex_m": apex, "exit_m": exit_}

    laps = []
    for i in range(10):
        jitter = (i % 3) - 1.0
        rows = [
            zone(450.0 + jitter, 500.0 + jitter, 900.0),
            zone(850.0, 950.0 + jitter, 1050.0),
        ]
        if i == 0:
            rows.append(zone(4990.0, 5000.0, 5050.0))
        laps.append(rows)
    model = build_turn_model(laps)
    assert len(model) == 2, "the one-lap phantom zone must not become a turn"
    first, second = model
    assert first["turn"] == 1 and second["turn"] == 2
    # Overlapping medians are clipped at the apex midpoint (~725).
    assert first["exit_m"] <= 725.5
    assert second["entry_m"] >= 724.5
    assert first["exit_m"] <= second["entry_m"]


def test_turn_model_refuses_thin_evidence():
    laps = [[{"entry_m": 100.0, "apex_m": 150.0, "exit_m": 200.0}]] * 5
    assert build_turn_model(laps) == []


def test_measured_time_is_interpolated_over_the_fixed_window():
    trace = _steady_trace()
    model = [{"turn": 1, "entry_m": 102.5, "apex_m": 150.0, "exit_m": 202.5}]
    rows = measure_turns(trace, model)
    assert len(rows) == 1
    # 100 m at 50 m/s, boundaries interpolated between 5 m samples.
    assert rows[0]["time_in_corner_s"] == pytest.approx(2.0, abs=0.001)
    assert rows[0]["name"] == "Turn 1"


def test_recording_holes_skip_the_turn_instead_of_guessing():
    trace = [p for p in _steady_trace() if not (120.0 <= p["d"] <= 180.0)]
    model = [{"turn": 1, "entry_m": 100.0, "apex_m": 150.0, "exit_m": 200.0}]
    assert measure_turns(trace, model) == []


def test_loss_and_cause_attach_against_the_pb_pass():
    model = [{"turn": 1, "entry_m": 100.0, "apex_m": 150.0, "exit_m": 200.0}]
    pb_rows = measure_turns(_steady_trace(speed_mps=50.0), model)
    # Without a reference pass there is no loss to claim.
    assert measure_turns(_steady_trace(speed_mps=40.0), model)[0]["loss_vs_pb_s"] is None

    slow_with_ref = measure_turns(_steady_trace(speed_mps=40.0), model, pb_rows)
    assert slow_with_ref[0]["loss_vs_pb_s"] == pytest.approx(0.5, abs=0.01)
    assert slow_with_ref[0]["loss_vs_pb_s"] == pytest.approx(
        slow_with_ref[0]["time_in_corner_s"] - pb_rows[0]["time_in_corner_s"],
        abs=0.001,
    )
    assert slow_with_ref[0]["cause"] != ""


def _instance(
    time_s: float,
    *,
    lock: bool = False,
    spin: bool = False,
    brake_point_m: float = 450.0,
    apex_speed: float = 100.0,
) -> dict:
    return {
        "apex_m": 500.0,
        "entry_m": 440.0,
        "exit_m": 560.0,
        "time_in_corner_s": time_s,
        "wheel_lock": lock,
        "wheelspin": spin,
        "brake_point_m": brake_point_m,
        "min_speed_kph": apex_speed,
        "apex_speed_kph": apex_speed,
        "throttle_on_m": 510.0,
    }


def _cluster(rows: list[dict]) -> dict:
    return {"turn": 1, "apex_m": 500.0, "rows": rows}


def test_one_event_is_a_moment_two_are_a_pattern():
    base = [_instance(3.0)] + [_instance(3.4) for _ in range(5)]
    single = [dict(r) for r in base]
    single[1]["wheelspin"] = True
    finding = analyze_turn(_cluster(single))
    assert finding["mechanism"] != "exit-wheelspin"

    double = [dict(r) for r in base]
    double[1]["wheelspin"] = True
    double[2]["wheelspin"] = True
    finding = analyze_turn(_cluster(double))
    assert finding["mechanism"] == "exit-wheelspin"


def test_slow_apex_with_matched_entries_blames_the_car():
    rows = [_instance(3.0, apex_speed=110.0)] + [
        _instance(3.4, apex_speed=104.0, brake_point_m=452.0) for _ in range(5)
    ]
    finding = analyze_turn(_cluster(rows))
    assert finding["mechanism"] == "mid-corner-grip"
    assert finding["setup_addressable"] is True


def test_slow_apex_with_early_braking_blames_the_driver():
    rows = [_instance(3.0, apex_speed=110.0)] + [
        _instance(3.4, apex_speed=104.0, brake_point_m=430.0) for _ in range(5)
    ]
    finding = analyze_turn(_cluster(rows))
    assert finding["mechanism"] == "overslowed-entry"
    assert finding["setup_addressable"] is False


def test_slow_apex_with_unknown_braking_stays_unattributed():
    rows = [_instance(3.0, apex_speed=110.0)] + [
        _instance(3.4, apex_speed=104.0) for _ in range(5)
    ]
    for row in rows:
        row["brake_point_m"] = None
    finding = analyze_turn(_cluster(rows))
    assert finding["mechanism"] == "inconsistent-line"
    assert finding["adjustments"] if False else finding["setup_addressable"] is False


def test_lock_axle_is_read_from_the_slip_channels():
    front = [[_point(0, 0, brake=0.6, slip=[-0.3, -0.2, 0.0, 0.0])] * 5]
    rear = [[_point(0, 0, brake=0.6, slip=[0.0, 0.0, -0.3, -0.2])] * 5]
    both = [[_point(0, 0, brake=0.6, slip=[-0.3, 0.0, -0.3, 0.0])] * 5]
    assert characterize_lock(front) == "front"
    assert characterize_lock(rear) == "rear"
    assert characterize_lock(both) == "both"
    assert characterize_lock([[_point(0, 0)]]) is None


def test_conflicting_corners_do_not_cancel_silently():
    findings = [
        {
            "label": "Turn 2 (800 m)",
            "median_loss_s": 0.5,
            "samples": 10,
            "adjustments": {"front_wing": +1},
        },
        {
            "label": "Turn 7 (2900 m)",
            "median_loss_s": 0.2,
            "samples": 10,
            "adjustments": {"front_wing": -1},
        },
    ]
    net, notes = resolve_adjustments(findings)
    assert net == {"front_wing": +1}, "the bigger measured loss wins"
    assert len(notes) == 1 and "Turn 7" in notes[0] and "Turn 2" in notes[0]


@pytest.mark.asyncio
async def test_rebuild_builds_a_model_and_canonical_rows(stack):
    _, database, _, _, analysis, _ = stack

    def race_lap_trace(slow_factor: float) -> list[dict]:
        # 2 km lap with two genuine braking zones at ~500 m and ~1500 m.
        points = []
        t = 0.0
        d = 0.0
        while d <= 2000.0:
            in_corner = 450.0 <= d <= 600.0 or 1450.0 <= d <= 1600.0
            speed = 90 if in_corner else 250
            step = 5.0
            t += step / (speed / 3.6) * slow_factor
            points.append(
                _point(
                    d,
                    t,
                    speed=speed,
                    throttle=0.0 if in_corner else 1.0,
                    brake=0.8 if in_corner else 0.0,
                    lat_g=2.5 if in_corner else 0.2,
                )
            )
            d += step
        return points

    for lap_num in range(1, 10):
        await database.save_lap(
            {
                "session_uid": 900,
                "track_id": 10,
                "track_name": "Spa",
                "session_type": "Race",
                "lap_num": lap_num,
                "lap_time_ms": 100_000 + lap_num * 137,
                "valid": True,
                "compound": "MEDIUM",
                "trace": race_lap_trace(1.0 + 0.01 * lap_num),
            },
            [],
        )
    rebuilt = await database.rebuild_track_corners(10, analysis.segment_corners)
    assert rebuilt == 9
    model = await database.load_preference("turn_model:10", None)
    assert isinstance(model, list) and len(model) == 2
    rows = await database.corner_rows_for_track(10, 40)
    assert rows and all(row["name"].startswith("Turn ") for row in rows)
    turns = {row["corner_no"] for row in rows}
    assert turns == {1, 2}
    # Second run is a no-op: the stored model is the marker.
    assert await database.rebuild_track_corners(10, analysis.segment_corners) == 0
