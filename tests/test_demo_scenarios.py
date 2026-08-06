"""The invented races the demos are built on.

The Hungaroring scenario exists to make one strategic call genuinely hard.
If any of its numbers drift, the demo still runs and still looks fine — it
just stops showing a decision worth watching, which is a failure nothing else
would catch.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.demo_broadcast import Race  # noqa: E402
from tools.scenarios import HUNGARORING, SCENARIOS, SILVERSTONE  # noqa: E402


def _race(scenario):
    return Race(4, scenario)


def _named(race):
    return {name: index for index, (name, *_rest) in enumerate(race.field)}


@pytest.mark.parametrize("scenario", list(SCENARIOS.values()), ids=lambda s: s.key)
def test_every_packet_serializes(scenario):
    race = _race(scenario)
    for builder in (
        race.session_packet, race.participants_packet, race.lap_packet,
        race.telemetry_packet, race.status_packet, race.motion_packet,
        race.damage_packet,
    ):
        assert len(builder()) > 0


@pytest.mark.parametrize("scenario", list(SCENARIOS.values()), ids=lambda s: s.key)
def test_positions_are_a_permutation(scenario):
    race = _race(scenario)
    assert sorted(race.position) == list(range(1, len(race.field) + 1))


def test_the_default_scenario_is_still_silverstone():
    # The documentation screenshots are captured from the default, and the
    # screenshots README describes that race specifically.
    race = Race()
    assert race.scenario is SILVERSTONE
    assert race.scenario.track_id == 7
    assert race.lap == 34


# ---------------------------------------------------------------------------
# The Hungaroring strategy demo
# ---------------------------------------------------------------------------


def test_the_player_is_fourth_with_fifteen_laps_left():
    race = _race(HUNGARORING)
    assert race.position[race.player] == 4
    assert HUNGARORING.total_laps - race.lap == 15


def _model_wear_rate(compound: str) -> float:
    """The rate the strategy engine itself prices this compound at here."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from pitwall.strategy import DEFAULT_WEAR_PER_LAP, TRACK_TYRE_SEVERITY

    severity = TRACK_TYRE_SEVERITY.get(HUNGARORING.track_id, 1.0)
    return DEFAULT_WEAR_PER_LAP[compound] * severity


def test_the_players_tyre_state_is_consistent_with_the_wear_model():
    """The scenario must obey the model's own physics, not fight them.

    An earlier draft put the player on 24-lap-old mediums. At the modelled
    3.46%/lap for mediums here that car would already be near 97% worn, so
    the state was impossible rather than merely awkward — and the engine
    rightly refused to consider a one-stop, which is the option the demo
    exists to weigh.
    """
    race = _race(HUNGARORING)
    rate = _model_wear_rate(race.compound[race.player])
    implied = rate * race.age[race.player]
    actual = max(race.wear[race.player])
    assert abs(implied - actual) < 12.0, (
        f"{race.age[race.player]} laps at {rate:.2f}%/lap implies {implied:.0f}% "
        f"wear, scenario says {actual:.0f}%"
    )


def test_the_one_stop_can_actually_reach_the_flag():
    # If this projection passes 100% the engineer correctly reports the tyres
    # will not last, staying out stops being an option, and the demo becomes a
    # scripted box-now with no decision in it.
    race = _race(HUNGARORING)
    rate = _model_wear_rate(race.compound[race.player])
    laps_left = HUNGARORING.total_laps - race.lap
    projected = max(race.wear[race.player]) + rate * laps_left
    assert projected < 96.0, f"one-stop projects {projected:.0f}% wear: not feasible"
    # ...but not comfortable either, or there is nothing to weigh against it.
    assert projected > 78.0, f"one-stop projects only {projected:.0f}%: too easy"


def test_the_car_behind_is_faster_now_but_degrading_quicker():
    # The threat has to be real and also have a shelf life, or "hold him off"
    # is either hopeless or free.
    race = _race(HUNGARORING)
    behind = race.order[race.position[race.player]]
    assert _model_wear_rate(race.compound[behind]) > _model_wear_rate(
        race.compound[race.player]
    )
    assert race.age[behind] < race.age[race.player]


def test_the_car_behind_is_antonelli_on_fresher_tyres():
    # The threat is what makes staying out cost something.
    race = _race(HUNGARORING)
    behind = race.order[race.position[race.player]]
    names = [name for name, *_rest in race.field]
    assert names[behind] == "ANTONELLI"
    assert race.age[behind] < race.age[race.player] - 10
    assert race.compound[behind] != race.compound[race.player]


def test_the_gap_behind_is_inside_the_undercut_window():
    race = _race(HUNGARORING)
    behind = race.order[race.position[race.player]]
    gap = race.gap_to_leader[behind] - race.gap_to_leader[race.player]
    assert 3.0 <= gap <= 5.0, gap


def test_the_car_ahead_is_a_reachable_overcut_target():
    race = _race(HUNGARORING)
    ahead = race.order[race.position[race.player] - 2]
    gap = race.gap_to_leader[race.player] - race.gap_to_leader[ahead]
    assert 5.0 <= gap <= 8.0, gap
    # Also on an old set, so the overcut has something to work on.
    assert race.age[ahead] >= 18


def test_teammates_share_a_team_and_the_key_pairings_are_real():
    # The demo shows team names on screen. A wrong one is the first thing
    # anyone who follows the sport would notice, and this grid previously put
    # the player in the wrong car.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from pitwall.identity import team_name

    by_name = {d.name: d for d in HUNGARORING.drivers}
    for first, second, expected in (
        ("RUSSELL", "ANTONELLI", "Mercedes"),
        ("LECLERC", "HAMILTON", "Ferrari"),
        ("NORRIS", "PIASTRI", "McLaren"),
        ("ALONSO", "STROLL", "Aston Martin"),
    ):
        assert by_name[first].team_id == by_name[second].team_id
        assert team_name(by_name[first].team_id) == expected


def test_every_team_fields_exactly_two_cars():
    counts: dict[int, int] = {}
    for driver in HUNGARORING.drivers:
        counts[driver.team_id] = counts.get(driver.team_id, 0) + 1
    assert sorted(counts.values()) == [2] * 10, counts


def test_race_numbers_are_unique():
    numbers = [d.race_number for d in HUNGARORING.drivers]
    assert len(set(numbers)) == len(numbers)


def test_the_demo_barely_drifts_over_a_capture():
    """Laps must keep completing, and the drift must stay small.

    Both ways of freezing the moment were tried and both cost more than they
    saved. Pinning the lap while the car circulates leaves the live trace
    redrawing over itself into a scribble. Slowing the sim until no lap
    completes leaves the degradation and lap-target panels reading "No valid
    laps" — a demo that makes the product look like it has no data.

    Recording only after the answers are fetched brought the capture down to
    about 145 seconds, at which point the race advances under two laps and
    the problem stops being one.
    """
    capture_seconds = 150.0
    lap_seconds = HUNGARORING.base_lap_s / HUNGARORING.time_scale
    drift = capture_seconds / lap_seconds
    assert drift < 2.5, f"{drift:.1f} laps of drift over a capture is visible"
    # Laps have to complete, or the analysis panels have nothing to show.
    assert lap_seconds < capture_seconds


def test_scenarios_run_at_real_time():
    for scenario in SCENARIOS.values():
        assert scenario.time_scale == 1.0, scenario.key
        assert scenario.hold_lap is False, scenario.key


def test_no_rain_is_forecast():
    # A weather crossover would answer the tyre question for us and the demo
    # would stop being about degradation.
    assert all(rain <= 10 for _minutes, _weather, rain in HUNGARORING.forecast)


def test_it_is_a_hot_track():
    # Degradation is the whole premise.
    assert HUNGARORING.track_temperature >= 45


# ---------------------------------------------------------------------------
# The running-order guard
# ---------------------------------------------------------------------------


def test_a_running_order_with_a_duplicate_gap_is_refused():
    # Writing the order positionally is how two cars silently share a gap,
    # which drops the player a place and makes the wrong car the one behind.
    broken = HUNGARORING.__class__(
        **{
            **{f.name: getattr(HUNGARORING, f.name) for f in HUNGARORING.__dataclass_fields__.values()},
            "running_order": tuple(
                (name, 16.1 if name in {"RUSSELL", "ANTONELLI"} else gap)
                for name, gap in HUNGARORING.running_order
            ),
        }
    )
    with pytest.raises(ValueError, match="share a gap"):
        Race(4, broken)


def test_a_running_order_naming_an_unknown_driver_is_refused():
    broken = HUNGARORING.__class__(
        **{
            **{f.name: getattr(HUNGARORING, f.name) for f in HUNGARORING.__dataclass_fields__.values()},
            "running_order": (("NOBODY", 0.0),) + HUNGARORING.running_order[1:],
        }
    )
    with pytest.raises(ValueError, match="not in the field"):
        Race(4, broken)
