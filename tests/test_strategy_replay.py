"""A race replayed through the engine and the radio gives the calls it should, in order.

The unit tests check functions; these check the sequence of calls over a whole
race, fed as real packets through the same stack the app builds. The races are
generated from a recipe (tools/replay_demo.py), not committed captures.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tools import strategy_replay

# Heavy rain from lap 2 of 8: the inter stop clearly pays, so the whole
# protocol is exercised in a short race. Every car stops for inters at the
# end of lap 3.
WET = {"laps": 8, "rain_from_lap": 2, "rain": "Heavy rain", "forecast_chance": 50}
DRY = {"laps": 6}


def _replay(packets):
    return asyncio.run(strategy_replay.replay(packets))


@pytest.fixture(scope="module")
def wet():
    return _replay(strategy_replay.synthetic_packets(**WET))


@pytest.fixture(scope="module")
def dry_capture(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("replay") / "dry.pwcap"
    return strategy_replay.save_capture(strategy_replay.synthetic_packets(**DRY), path)


@pytest.fixture(scope="module")
def dry(dry_capture):
    return _replay(strategy_replay.capture_packets(dry_capture))


def _weather_said(timeline):
    return [entry for entry in timeline.said() if entry["type"].startswith("weather")]


def test_a_synthetic_wet_race_is_called_in_order(wet):
    """Stand by (asking about the grip), box, the pit-entry reminder; then nothing until it dries."""
    said = _weather_said(wet)
    assert [(entry["type"], entry.get("level")) for entry in said] == [
        ("weather_crossover", 3), ("weather_crossover", 4), ("weather_box_reminder", None),
    ], said
    stand_by, box, reminder = said
    assert stand_by["text"].endswith("How is the grip out there?")
    assert box["text"].startswith("Box this lap for inters.") and box["lap"] == 3
    assert reminder["lap"] == box["lap"] and reminder["text"] == "Box, box. Inters on."
    assert strategy_replay.check(wet.entries, [
        {"first": {"type": "weather_crossover", "level": 4}, "by_lap": 3},
        {"never": {"text_contains": "Stay out — boxing costs"}, "while": {"weather_level_at_least": 3}},
        {"count": {"type": "weather_crossover", "level": 4}, "at_most": 2},
        {"never": {"type": "weather_stand_down"}},
        {"every": {"type": "rival_pitting"}, "satisfies": {"relevance_at_least": 2}},
    ]) == []


def test_nothing_is_said_about_the_field_s_stops_while_the_driver_is_in_the_lane(wet):
    box_lap = next(entry["lap"] for entry in _weather_said(wet) if entry.get("level") == 4)
    in_lane = [entry for entry in wet.said()
               if entry["type"] == "rival_pitting" and entry["lap"] == box_lap and entry["distance_m"] > 5400]
    assert in_lane == []


def test_a_synthetic_dry_race_makes_no_weather_call(dry):
    assert _weather_said(dry) == []
    assert all(entry["weather_level"] == 0 for entry in dry.entries)
    assert dry.said(), "the race was replayed: the start and progress calls were made"


def test_the_same_capture_gives_the_same_timeline_twice(dry, dry_capture):
    again = _replay(strategy_replay.capture_packets(dry_capture))
    assert again.as_json() == dry.as_json()


def test_an_expectation_that_fails_says_which_and_why():
    entries = [
        {"kind": "said", "type": "weather_crossover", "level": 4, "lap": 14, "weather_level": 4, "text": "Box"},
        {"kind": "said", "type": "rival_pitting", "relevance": 1, "lap": 15, "weather_level": 0, "text": "In"},
    ]
    failures = strategy_replay.check(entries, [
        {"first": {"type": "weather_crossover", "level": 4}, "by_lap": 13},
        {"count": {"type": "weather_crossover"}, "at_least": 2},
        {"every": {"type": "rival_pitting"}, "satisfies": {"relevance_at_least": 2}},
        {"never": {"text_contains": "box"}, "while": {"weather_level_at_least": 3}},
    ])
    assert len(failures) == 4
    assert "first on lap 14, expected by lap 13" in failures[0]
