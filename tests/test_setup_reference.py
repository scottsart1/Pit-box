from __future__ import annotations

import math

import pytest

from pitwall.setup_reference import CALENDAR_TRACK_IDS, SETUP_FIELDS, reference_for_track


@pytest.mark.parametrize("style", ["stable", "rotation"])
@pytest.mark.parametrize("profile", ["race", "quali", "hybrid"])
def test_calendar_coverage_is_complete_attributed_and_uses_adjustable_controls(style, profile):
    assert len(set(CALENDAR_TRACK_IDS)) == 24
    for track_id in CALENDAR_TRACK_IDS:
        ref = reference_for_track(track_id, style=style, profile=profile)
        assert ref["status"] in {"published", "adapted"}
        assert set(ref["setup"]) == set(SETUP_FIELDS)
        assert len(ref["setup"]) == 20
        assert not {"engine_braking", "ballast", "fuel_load"} & ref["setup"].keys()
        assert all(math.isfinite(value) for value in ref["setup"].values())
        assert all(source["url"].startswith("https://") for source in ref["sources"])
        assert "2026" in ref["game"]
        assert ref["source_context"]["equal_performance"] == "unknown"
        assert ref["reviewed_at"] == "2026-10-02"


def test_asymmetric_china_pressures_preserve_named_wheel_positions():
    # Checked against the independently labelled MeetrsBuzz 2026 China upload.
    setup = reference_for_track(2)["setup"]
    assert setup["front_right_tyre_pressure"] == 25.4
    assert setup["front_left_tyre_pressure"] == 26.9
    assert setup["rear_right_tyre_pressure"] == 21.9
    assert setup["rear_left_tyre_pressure"] == 22.6
    assert setup["brake_bias"] == 56
    assert setup["brake_pressure"] == 99


@pytest.mark.parametrize(
    "track_id,race,quali",
    [
        (0, {"front_wing": 42, "rear_wing": 15, "off_throttle": 60, "brake_pressure": 98},
         {"front_wing": 30, "rear_wing": 0, "off_throttle": 45, "brake_pressure": 98}),
        (17, {"rear_wing": 24, "brake_pressure": 97},
         {"rear_wing": 12, "brake_pressure": 98}),
        (3, {"front_right_tyre_pressure": 26.5}, {"front_right_tyre_pressure": 23.0}),
        (13, {"front_right_tyre_pressure": 25.0, "off_throttle": 50},
         {"front_right_tyre_pressure": 25.5, "off_throttle": 45}),
        (29, {"rear_right_tyre_pressure": 22.0}, {"rear_right_tyre_pressure": 20.5}),
        (19, {"off_throttle": 80}, {"off_throttle": 75}),
        (12, {"front_left_tyre_pressure": 29.5}, {"front_left_tyre_pressure": 26.0}),
        (31, {"brake_pressure": 97}, {"brake_pressure": 100}),
    ],
)
def test_rotation_uses_published_race_and_quali_choices(track_id, race, quali):
    race_ref = reference_for_track(track_id, style="rotation", profile="race")
    quali_ref = reference_for_track(track_id, style="rotation", profile="quali")
    assert race_ref["source_profile"] == "race"
    assert quali_ref["source_profile"] == "quali"
    for field, value in race.items():
        assert race_ref["setup"][field] == value
    for field, value in quali.items():
        assert quali_ref["setup"][field] == value
    assert "parc ferme" in " ".join(quali_ref["notes"])


def test_source_quality_and_range_selection_are_explicit():
    china = reference_for_track(2, style="rotation")
    assert china["status"] == "adapted"
    assert china["verification"] == "author_theory"
    assert china["sources"][0]["published"] is None
    britain = reference_for_track(7, style="rotation")
    assert britain["status"] == "adapted"
    assert britain["source_ranges"] == {"brake_bias": [59, 60]}
    assert britain["setup"]["brake_bias"] == 59
    assert "lower endpoint" in " ".join(britain["notes"])
    assert britain["sources"][0]["published"] == "2026-06-29"
    monaco = reference_for_track(5, style="rotation")
    assert monaco["sources"][0]["published"] is None
    assert "no usable publication date" in " ".join(monaco["notes"])


def test_hybrid_and_stable_quali_do_not_invent_adjustments():
    for style in ("stable", "rotation"):
        assert reference_for_track(0, style=style, profile="hybrid")["setup"] == reference_for_track(0, style=style)["setup"]
        assert reference_for_track(0, style=style, profile="hybrid")["source_profile"] == "race"
    stable_quali = reference_for_track(0, profile="quali")
    assert stable_quali["setup"] == reference_for_track(0)["setup"]
    assert stable_quali["source_profile"] == "race"
    assert "no separate qualifying values" in " ".join(stable_quali["notes"])


def test_different_creators_are_preserved_without_blending():
    stable = reference_for_track(42)
    rotation = reference_for_track(42, style="rotation")
    assert (stable["setup"]["front_wing"], stable["setup"]["rear_wing"]) == (39, 31)
    assert (rotation["setup"]["front_wing"], rotation["setup"]["rear_wing"]) == (50, 28)
    assert stable["setup"]["rear_suspension"] == 4
    assert rotation["setup"]["rear_suspension"] == 41
    assert stable["sources"][0]["author"] == "Matt212"
    assert rotation["sources"][0]["author"] == "Derp3339"


@pytest.mark.parametrize("track_id,wings", [(27, (39, 29)), (39, (27, 18)), (40, (30, 20)), (41, (50, 41))])
def test_extra_tracks_have_their_own_source_rows(track_id, wings):
    setup = reference_for_track(track_id)["setup"]
    assert (setup["front_wing"], setup["rear_wing"]) == wings
    if track_id != 27:
        assert reference_for_track(track_id, style="rotation")["status"] == "unavailable"


@pytest.mark.parametrize("track_id,kwargs", [
    (-1, {}), (1, {}), (21, {}), (999, {}), (True, {}), (0.5, {}),
    (0, {"condition": "wet"}), (0, {"condition": "intermediate"}),
    (0, {"condition": "unknown"}), (0, {"style": "unknown"}),
    (0, {"profile": "unknown"}),
])
def test_unavailable_reference_never_substitutes_dry_or_a_different_circuit(track_id, kwargs):
    ref = reference_for_track(track_id, **kwargs)
    assert ref["status"] == "unavailable"
    assert ref["setup"] == {}
    assert ref["notes"]
    assert ref["reason"] == ref["notes"][0]


def test_results_are_detached_from_the_catalogue_and_other_calls():
    first = reference_for_track(7, style="rotation")
    first["setup"]["rear_wing"] = 50
    first["sources"][0]["url"] = "changed"
    first["source_ranges"]["brake_bias"].append(99)
    first["source_context"]["input"] = "changed"
    first["source_notation"]["suspension_geometry"] = "changed"
    second = reference_for_track(7, style="rotation")
    assert second["setup"]["rear_wing"] == 5
    assert second["sources"][0]["url"].startswith("https://docs.google.com/")
    assert second["source_ranges"]["brake_bias"] == [59, 60]
    assert second["source_context"]["input"].startswith("Primarily wheel")
    assert second["source_notation"]["suspension_geometry"] == "LLLL"
