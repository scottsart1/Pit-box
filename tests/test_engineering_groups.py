from __future__ import annotations

import copy

import pytest

from pitwall.engineering import _isolated_slow_lap_ids, build_runs
from pitwall.engineering_groups import (
    apply_lap_notes,
    build_groups,
    compare_groups,
    conditions_summary,
    suggest_groups,
    validate_groups,
)


def lap(n, **changes):
    item = {
        "id": f"lap-{n}",
        "lap_num": n,
        "timeline_epoch": 0,
        "run_serial": 1,
        "valid": True,
        "lap_time_ms": 90000,
        "compound": "MEDIUM",
        "setup": {"front_wing": 28},
        "weather": "Clear",
        "tyre_age_start": n,
        "fuel_start_kg": 20 - n,
        "air_temp_c": 22,
        "track_temp_c": 30,
        "traffic_observed": True,
        "context_observed": True,
        "learning_exclusions": [],
        "s1_ms": 30000,
        "s2_ms": 30000,
        "s3_ms": 30000,
    }
    item.update(changes)
    return item


def definitions(a=(1, 2, 3), b=(4, 5, 6)):
    return [
        {"id": "a", "name": "My baseline", "lap_ids": [f"lap-{n}" for n in a]},
        {"id": "b", "name": "Hard tyre test", "lap_ids": [f"lap-{n}" for n in b]},
    ]


def test_manual_groups_use_stable_ids_keep_chronology_and_allow_nonadjacent_laps():
    laps = [lap(1), lap(2), lap(3), lap(4, lap_num=1, timeline_epoch=1)]
    original = copy.deepcopy(laps)
    definition = [
        {"id": " personal ", "name": " Braking test ", "lap_ids": ["lap-4", "lap-1"]}
    ]
    groups = build_groups(laps, definition)
    assert groups[0]["lap_ids"] == ["lap-1", "lap-4"]
    assert groups[0]["id"] == "personal"
    assert groups[0]["name"] == "Braking test"
    assert groups[0]["lap_range"] == [1, 1]
    assert groups[0]["conditions"]["timeline_epochs"] == [0, 1]
    assert groups[0]["summary"]["observed_pace_trend_s_per_lap"] is None
    groups[0]["laps"][0]["setup"]["front_wing"] = 50
    assert laps == original
    assert definition[0]["id"] == " personal "


@pytest.mark.parametrize(
    "bad",
    [
        [],
        [{"id": "a", "name": "A", "lap_ids": []}],
        [{"id": "a", "name": "A", "lap_ids": ["foreign"]}],
        [{"id": "a", "name": "A", "lap_ids": ["lap-1", "lap-1"]}],
        [{"id": "a", "name": "A", "lap_ids": [1]}],
        [{"id": "", "name": "A", "lap_ids": ["lap-1"]}],
        [{"id": "a", "name": " ", "lap_ids": ["lap-1"]}],
        [{"id": "a", "name": "a" * 81, "lap_ids": ["lap-1"]}],
        [
            {"id": "a", "name": "A", "lap_ids": ["lap-1"]},
            {"id": "a", "name": "B", "lap_ids": ["lap-2"]},
        ],
        [
            {"id": "a", "name": "A", "lap_ids": ["lap-1"]},
            {"id": "b", "name": "B", "lap_ids": ["lap-1"]},
        ],
    ],
)
def test_invalid_definitions_are_rejected(bad):
    with pytest.raises(ValueError):
        validate_groups([lap(1), lap(2)], bad)


def test_duplicate_canonical_evidence_cannot_be_ambiguously_grouped():
    with pytest.raises(ValueError, match="duplicate lap identities"):
        validate_groups(
            [lap(1), lap(1)], [{"id": "a", "name": "A", "lap_ids": ["lap-1"]}]
        )


@pytest.mark.parametrize("count", [True, False, 0, 1, 13, 3.5, "3"])
def test_invalid_suggestion_counts_are_rejected(count):
    with pytest.raises(ValueError):
        suggest_groups([lap(n) for n in range(1, 21)], count)


def test_suggestions_honor_events_and_count_without_clustering_by_pace():
    laps = [lap(n) for n in range(1, 7)] + [
        lap(n, compound="HARD", run_serial=2, tyre_age_start=n - 6)
        for n in range(7, 13)
    ]
    original = copy.deepcopy(laps)
    two = suggest_groups(laps, 2)
    assert [item["lap_ids"] for item in two] == [
        [f"lap-{n}" for n in range(1, 7)],
        [f"lap-{n}" for n in range(7, 13)],
    ]
    for count in range(3, 13):
        groups = suggest_groups(laps, count)
        assert len(groups) == count
        assert all(group["lap_ids"] for group in groups)
        assert [lap_id for group in groups for lap_id in group["lap_ids"]] == [
            lap["id"] for lap in laps
        ]
        assert groups == suggest_groups(laps, count)
    for n, item in enumerate(laps):
        item["lap_time_ms"] = 200000 if n % 2 else 40000
    assert suggest_groups(laps, 2) == two
    assert original != laps  # The only modifications here were made by this test.
    with pytest.raises(ValueError, match="at least one recorded lap"):
        suggest_groups(laps[:3], 4)


def test_timeline_boundaries_take_priority_and_neutral_spans_split_evenly():
    laps = [lap(n, timeline_epoch=0 if n <= 3 else 1) for n in range(1, 13)]
    assert suggest_groups(laps, 2)[0]["lap_ids"] == ["lap-1", "lap-2", "lap-3"]
    uniform = suggest_groups([lap(n) for n in range(1, 13)], 4)
    assert [len(group["lap_ids"]) for group in uniform] == [3, 3, 3, 3]


def test_mixed_compounds_do_not_mistake_slower_subcohort_for_outliers():
    laps = [lap(n) for n in range(1, 7)] + [
        lap(n, compound="HARD", lap_time_ms=98000, tyre_age_start=n - 6)
        for n in range(7, 10)
    ]
    group = build_groups(
        laps,
        [
            {
                "id": "mixed",
                "name": "Selected training laps",
                "lap_ids": [item["id"] for item in laps],
            }
        ],
    )[0]
    assert group["summary"]["clean_lap_count"] == 9
    assert group["compound"] == "MIXED"
    assert group["heterogeneous"] is True
    assert group["summary"]["observed_pace_trend_s_per_lap"] is None
    assert group["conditions"]["compounds"] == ["HARD", "MEDIUM"]


def test_outlier_screening_still_excludes_anomalies_within_a_run():
    laps = [lap(n, lap_time_ms=110000 if n == 4 else 90000) for n in range(1, 5)]
    group = build_groups(
        laps, [{"id": "a", "name": "A", "lap_ids": [item["id"] for item in laps]}]
    )[0]
    assert group["summary"]["clean_lap_count"] == 3
    assert group["summary"]["excluded_laps"] == [
        {"lap": 4, "lap_id": "lap-4", "timeline_epoch": 0, "reasons": ["pace_outlier"]}
    ]


def test_reported_notes_exclude_only_when_explicit_and_never_overwrite_measured_conditions():
    laps = [lap(1, learning_exclusions=["traffic"]), lap(2), lap(3)]
    original = copy.deepcopy(laps)
    notes = [
        {
            "id": "n1",
            "lap_ids": ["lap-1", "lap-2"],
            "source": "driver",
            "category": "traffic",
            "text": "Held up",
            "exclude_from_pace": True,
        },
        {
            "id": "n2",
            "lap_ids": ["lap-3"],
            "source": "engineer",
            "category": "conditions",
            "text": "Driver says air felt warmer",
            "exclude_from_pace": False,
        },
    ]
    applied = apply_lap_notes(laps, notes)
    assert laps == original
    assert applied[0]["learning_exclusions"] == ["traffic", "driver_reported_traffic"]
    assert applied[1]["learning_exclusions"] == ["driver_reported_traffic"]
    assert applied[2]["learning_exclusions"] == []
    assert applied[2]["air_temp_c"] == 22
    assert apply_lap_notes(applied, notes) == applied
    removed = apply_lap_notes(applied, [])
    assert removed[0]["learning_exclusions"] == ["traffic"]
    assert removed[1]["learning_exclusions"] == []
    assert all(item["context_notes"] == [] for item in removed)
    conditions = conditions_summary(applied)
    assert conditions["traffic"]["measured_affected_laps"] == 1
    assert conditions["traffic"]["reported_laps"] == 2


def test_driver_mistake_note_is_reconsidered_on_each_analysis_and_attributed_in_results():
    laps = [lap(n, lap_time_ms=92000 if n == 2 else 90000) for n in range(1, 7)]
    notes = [
        {
            "id": "driver-error",
            "lap_ids": ["lap-2"],
            "text": "I missed the apex",
            "source": "driver",
            "category": "mistake",
            "exclude_from_pace": True,
        }
    ]
    groups = build_groups(laps, definitions(), notes)
    result = compare_groups(*groups)
    assert result["clean_lap_counts"] == {"a": 2, "b": 3}
    assert result["enough_evidence"] is False
    assert result["excluded"]["a"][0]["reasons"] == ["driver_reported_mistake"]
    assert result["notes"]["a"] == notes
    notes[0]["exclude_from_pace"] = False
    corrected = compare_groups(*build_groups(laps, definitions(), notes))
    assert corrected["clean_lap_counts"] == {"a": 3, "b": 3}
    assert corrected["enough_evidence"] is True
    assert corrected["notes"]["a"][0]["exclude_from_pace"] is False


def test_different_compound_comparison_includes_air_traffic_and_sector_evidence():
    laps = [lap(n) for n in range(1, 4)] + [
        lap(
            n,
            run_serial=2,
            compound="HARD",
            air_temp_c=27,
            track_temp_c=34,
            lap_time_ms=91200,
            s2_ms=31200,
        )
        for n in range(4, 7)
    ]
    result = compare_groups(*build_groups(laps, definitions()))
    assert result["median_delta_s"] == 1.2
    assert result["sector_deltas_s"] == [0, 1.2, 0]
    assert result["enough_evidence"] is True
    assert result["conditions"]["a"]["air_temp_c"] == [22, 22]
    assert result["conditions"]["b"]["air_temp_c"] == [27, 27]
    assert result["conditions"]["b"]["traffic"]["observed_laps"] == 3
    assert any("Different compounds" in item for item in result["caveats"])
    assert any("not matched" in item for item in result["caveats"])
    assert result["pairs"] == []
    assert {item["field"] for item in result["condition_differences"]} >= {
        "compounds",
        "air_temp_c",
        "track_temp_c",
    }


def test_sector_differences_use_one_complete_cohort_for_all_sectors():
    laps = [lap(n) for n in range(1, 7)]
    laps[0].update(s1_ms=None, s2_ms=80000, s3_ms=80000)
    result = compare_groups(*build_groups(laps, definitions()))
    assert result["clean_lap_counts"] == {"a": 3, "b": 3}
    assert result["sector_lap_counts"] == {"a": 2, "b": 3}
    assert result["sector_lap_ids"]["a"] == ["lap-2", "lap-3"]
    assert result["sector_deltas_s"] == [0, 0, 0]
    assert any("cohort may differ" in item for item in result["caveats"])


def test_missing_evidence_and_empty_clean_cohort_are_not_inferred():
    laps = [
        lap(
            n,
            valid=n > 3,
            air_temp_c=None,
            track_temp_c=float("nan"),
            fuel_start_kg=None,
            traffic_observed=False,
            s2_ms=None,
        )
        for n in range(1, 7)
    ]
    result = compare_groups(*build_groups(laps, definitions()))
    assert result["median_delta_s"] is None
    assert result["sector_deltas_s"] == [None, None, None]
    assert result["conditions"]["a"]["air_temp_c"] is None
    assert result["conditions"]["a"]["track_temp_c"] is None
    assert result["conditions"]["a"]["field_coverage"]["air_temp_c"] == {
        "observed_laps": 0,
        "missing_laps": 3,
    }
    assert result["conditions"]["a"]["traffic"]["unknown_laps"] == 3
    assert any("lack recorded traffic evidence" in item for item in result["caveats"])
    assert any("measurements are missing" in item for item in result["caveats"])


def test_overlapping_comparison_groups_are_rejected():
    group = build_groups([lap(1)], [{"id": "a", "name": "A", "lap_ids": ["lap-1"]}])[0]
    with pytest.raises(ValueError, match="same recorded lap"):
        compare_groups(group, copy.deepcopy(group))


def test_no_saved_groups_returns_an_empty_view():
    assert build_groups([], []) == []
    assert build_groups([lap(1)], []) == []


def test_group_ids_follow_membership_so_saved_notes_cannot_migrate_to_other_laps():
    first = suggest_groups([lap(n) for n in range(1, 9)], 2)
    second = suggest_groups([lap(n) for n in range(1, 13)], 2)
    assert first[0]["id"] != second[0]["id"]


def test_temperature_ranges_and_measured_traffic_evidence_are_preserved():
    item = lap(
        1,
        air_temp_c_range=[19, 24],
        track_temp_c_range=[28, 34],
        traffic_evidence={
            "min_gap_ahead_s": 0.7,
            "close_following_observed": True,
            "basis": "measured_proximity",
        },
    )
    result = conditions_summary([item, lap(2, air_temp_c_range=[999, -999])])
    assert result["air_temp_c"] == [19, 24]
    assert result["track_temp_c"] == [28, 34]
    assert result["field_coverage"]["air_temp_c"]["observed_laps"] == 2
    assert result["traffic"]["min_gap_ahead_s"] == 0.7
    assert result["traffic"]["close_following_observed_laps"] == 1
    assert result["traffic"]["basis"] == ["measured_proximity"]


def test_isolated_slow_lap_is_not_a_new_run_and_does_not_distort_the_clean_summary():
    laps = [lap(1), lap(2, lap_time_ms=105000), lap(3)]
    runs = build_runs(laps)
    assert len(runs) == 1
    assert runs[0]["summary"]["clean_lap_count"] == 2
    assert runs[0]["summary"]["excluded_laps"] == [
        {
            "lap": 2,
            "lap_id": "lap-2",
            "timeline_epoch": 0,
            "reasons": ["isolated_slow_lap"],
        }
    ]
    assert runs[0]["summary"]["median_pace_s"] == 90
    assert runs[0]["summary"]["pace_filter"]["isolated_slow_lap_ids"] == ["lap-2"]
    assert laps[1]["learning_exclusions"] == []


def test_live_laps_without_canonical_ids_do_not_alias_each_other():
    laps = [lap(1), lap(2, lap_time_ms=105000), lap(3)]
    for item in laps:
        item.pop("id")
    summary = build_runs(laps)[0]["summary"]
    assert summary["clean_lap_count"] == 2
    assert summary["excluded_laps"][0]["lap"] == 2


@pytest.mark.parametrize(
    "change",
    [
        {"compound": "HARD"},
        {"setup": {"front_wing": 35}},
        {"weather": "Rain"},
        {"air_temp_c": 29},
        {"air_temp_c_range": [22, 28]},
        {"fuel_start_kg": 35},
        {"tyre_age_start": 0},
        {"context_observed": False},
        {"traffic_observed": False},
        {"air_temp_c": None},
        {"run_serial": 2},
        {"timeline_epoch": 1},
        {"lap_num": 8},
    ],
)
def test_context_changes_or_missing_evidence_do_not_become_an_inferred_cooldown(change):
    laps = [lap(1), lap(2, lap_time_ms=105000, **change), lap(3)]
    assert _isolated_slow_lap_ids(laps) == set()


def test_prolonged_pace_change_is_not_an_isolated_excursion():
    laps = [lap(1), lap(2, lap_time_ms=105000), lap(3, lap_time_ms=105000), lap(4)]
    assert _isolated_slow_lap_ids(laps) == set()


def test_large_range_note_scope_is_not_repeated_on_every_lap():
    laps = [lap(n) for n in range(1, 101)]
    note = {
        "id": "all",
        "lap_ids": [item["id"] for item in laps],
        "pending_laps": [{"lap_num": 101, "timeline_epoch": 0}],
        "source": "driver",
        "category": "balance",
        "text": "Understeer",
        "exclude_from_pace": False,
    }
    applied = apply_lap_notes(laps, [note])
    assert all(
        "lap_ids" not in item["context_notes"][0]
        and "pending_laps" not in item["context_notes"][0]
        for item in applied
    )
    group = build_groups(
        applied, [{"id": "a", "name": "All", "lap_ids": note["lap_ids"]}]
    )[0]
    assert group["context_notes"][0]["lap_ids"] == note["lap_ids"]


def test_weather_cohorts_are_not_removed_as_slow_outliers():
    laps = [lap(n) for n in range(1, 7)] + [
        lap(n, weather="Heavy rain", lap_time_ms=115000) for n in range(7, 10)
    ]
    group = build_groups(
        laps,
        [
            {
                "id": "all",
                "name": "Dry then wet",
                "lap_ids": [item["id"] for item in laps],
            }
        ],
    )[0]
    assert group["summary"]["clean_lap_count"] == 9
    assert group["summary"]["observed_pace_trend_s_per_lap"] is None
    assert group["heterogeneous"]


def test_descriptive_comparison_retains_isolated_slow_lap_reason():
    laps = [lap(n, lap_time_ms=105000 if n == 2 else 90000) for n in range(1, 7)]
    result = compare_groups(*build_groups(laps, definitions()))
    assert result["excluded"]["a"][0]["reasons"] == ["isolated_slow_lap"]


def test_unknown_identity_is_disclosed_and_clean_condition_ranges_have_explicit_scope():
    laps = [lap(n, compound=None, weather=None, setup={}) for n in range(1, 7)]
    laps[1].update(valid=False, air_temp_c=99)
    result = compare_groups(*build_groups(laps, definitions()))
    assert any("unknown value is not evidence" in item for item in result["caveats"])
    assert result["conditions"]["a"]["air_temp_c"] == [22, 99]
    assert result["clean_conditions"]["a"]["air_temp_c"] == [22, 22]
