"""The race lap target is a pace the driver can sustain.

It was the median of the last four valid laps, so after a safety car it was a
safety-car lap time, spoken and shown for laps afterwards; then it was pulled
to the car ahead's last lap, whatever that car was on.
"""

from __future__ import annotations

from pitwall.analysis import TARGET_MAX_PULL_MS, AnalysisEngine


def _lap(number, ms, **extra):
    return {"lap_num": number, "lap_time_ms": ms, "valid": True, "pit_status": 0, **extra}


def _state(laps, *, ahead=None, phase="green", tyre=("MEDIUM", 10)):
    drivers = [{"car_idx": 0, "position": 5, "lap_history": []}]
    if ahead:
        drivers.append({"car_idx": 1, "position": 4, **ahead})
    return {
        "mode_profile": "race", "player_car_index": 0, "player_position": 5, "race_control_phase": phase,
        "tyre": {"compound": tyre[0], "age_laps": tyre[1]}, "completed_laps": laps, "drivers": drivers,
    }


def _ahead(last, *, compound="MEDIUM", age=10, history=(91_000, 91_100, 90_900)):
    return {"last_lap_ms": last, "tyre_compound": compound, "tyre_age": age,
            "lap_history": [{"lap_ms": ms, "valid_flags": 1} for ms in history]}


def test_a_safety_car_lap_is_not_race_pace():
    laps = [_lap(n, 91_000) for n in range(1, 6)]
    laps += [_lap(n, 125_000, learning_exclusions=["neutralised_lap"], safety_car="full") for n in range(6, 9)]
    target = AnalysisEngine.compute_target(_state(laps))
    assert target["target_ms"] == 91_000
    assert "clean laps" in target["basis"]


def test_a_lap_in_traffic_is_still_race_pace():
    laps = [_lap(n, 92_000, learning_exclusions=["traffic"]) for n in range(1, 5)]
    assert AnalysisEngine.compute_target(_state(laps))["target_ms"] == 92_000


def test_a_car_on_new_softs_does_not_set_the_target():
    laps = [_lap(n, 91_500) for n in range(1, 6)]
    fresh = _ahead(90_400, compound="SOFT", age=1)
    assert AnalysisEngine.compute_target(_state(laps, ahead=fresh))["target_ms"] == 91_500


def test_a_car_on_inters_does_not_set_a_slick_target():
    laps = [_lap(n, 91_500) for n in range(1, 6)]
    inters = _ahead(91_000, compound="INTER", history=(91_000, 91_100, 90_900))
    assert AnalysisEngine.compute_target(_state(laps, ahead=inters))["target_ms"] == 91_500


def test_the_car_ahead_can_pull_the_target_by_eight_tenths_at_most():
    laps = [_lap(n, 92_000) for n in range(1, 6)]
    quick = _ahead(90_000, history=(90_000, 90_050, 89_950))
    target = AnalysisEngine.compute_target(_state(laps, ahead=quick))
    assert target["target_ms"] == 92_000 - TARGET_MAX_PULL_MS
    assert target["basis"] == "pace of the car ahead, capped"


def test_a_comparable_car_ahead_pulls_the_target():
    laps = [_lap(n, 91_500) for n in range(1, 6)]
    close = _ahead(91_200, history=(91_200, 91_250, 91_150))
    assert AnalysisEngine.compute_target(_state(laps, ahead=close))["target_ms"] == 91_100


def test_an_out_lap_ahead_does_not_set_the_target():
    laps = [_lap(n, 91_500) for n in range(1, 6)]
    out_lap = _ahead(110_000, history=(91_000, 91_100, 110_000))
    assert AnalysisEngine.compute_target(_state(laps, ahead=out_lap))["target_ms"] == 91_500


def test_under_the_safety_car_the_car_ahead_sets_nothing():
    laps = [_lap(n, 91_500) for n in range(1, 6)]
    close = _ahead(91_200, history=(91_200, 91_250, 91_150))
    assert AnalysisEngine.compute_target(_state(laps, ahead=close, phase="safety_car"))["target_ms"] == 91_500
