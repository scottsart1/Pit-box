"""User-selected lap groups and descriptive, evidence-aware stint comparisons.

Grouping changes the view of a recording, never its measured lap evidence.
Suggested groups stay chronological and use changes in recorded conditions,
not lap pace, so a fast-lap cluster cannot masquerade as an experimental stint.
"""

from __future__ import annotations

import copy
import hashlib
import json
from itertools import groupby, pairwise
from statistics import median, pstdev

from .engineering import (
    build_runs,
    exclusions,
    number,
    run_summary,
    setup_key,
    setup_values,
)

MAX_GROUPS = 12
NOTE_CATEGORIES = {"traffic", "mistake", "balance", "conditions", "mechanical", "other"}
_RANGE_FIELDS = {
    "tyre_age_laps": "tyre_age_start",
    "fuel_kg": "fuel_start_kg",
    "track_temp_c": "track_temp_c",
    "air_temp_c": "air_temp_c",
}


def apply_lap_notes(laps: list[dict], notes: list[dict]) -> list[dict]:
    """Attach attributed reports and explicit exclusions without editing telemetry.

    Reapplication replaces the derived note fields. Removing or correcting a note
    can therefore restore a lap, while measured exclusions always survive.
    """
    by_lap: dict[str, list[dict]] = {}
    for note in notes:
        for lap_id in set(note.get("lap_ids") or []):
            by_lap.setdefault(str(lap_id), []).append(note)
    result = []
    for original in laps:
        lap = copy.deepcopy(original)
        reasons = [
            reason
            for reason in lap.get("learning_exclusions", [])
            if not str(reason).startswith(("driver_reported_", "engineer_reported_"))
        ]
        lap["context_notes"] = [
            {
                key: copy.deepcopy(value)
                for key, value in note.items()
                if key not in {"lap_ids", "pending_laps"}
            }
            for note in by_lap.get(str(lap.get("id")), [])
        ]
        for note in lap["context_notes"]:
            if note.get("exclude_from_pace") is not True:
                continue
            source = "engineer" if note.get("source") == "engineer" else "driver"
            category = note.get("category")
            if category not in NOTE_CATEGORIES:
                category = "other"
            reasons.append(f"{source}_reported_{category}")
        lap["learning_exclusions"] = list(dict.fromkeys(reasons))
        result.append(lap)
    return result


def validate_groups(laps: list[dict], definitions: list[dict]) -> list[dict]:
    """Normalize group definitions, rejecting ambiguous or foreign lap selections."""
    if not isinstance(definitions, list) or not 1 <= len(definitions) <= MAX_GROUPS:
        raise ValueError(f"Define between 1 and {MAX_GROUPS} lap groups.")
    known = {str(lap["id"]): index for index, lap in enumerate(laps)}
    if len(known) != len(laps):
        raise ValueError("The recording contains duplicate lap identities.")
    group_ids: set[str] = set()
    used: set[str] = set()
    normalized = []
    for definition in definitions:
        if not isinstance(definition, dict):
            raise ValueError("Each group must have an ID, a name and selected laps.")  # noqa: TRY004 - API validation errors share one exception type.
        group_id, name = definition.get("id"), definition.get("name")
        if not isinstance(group_id, str) or not group_id.strip() or len(group_id) > 180:
            raise ValueError("Each group needs an ID of at most 180 characters.")
        group_id = group_id.strip()
        if group_id in group_ids:
            raise ValueError("Group IDs must be unique.")
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError("Each group needs a name of at most 80 characters.")
        lap_ids = definition.get("lap_ids")
        if not isinstance(lap_ids, list) or not lap_ids:
            raise ValueError(f"Select at least one lap for {name.strip()}.")
        if any(not isinstance(item, str) for item in lap_ids):
            raise ValueError("Select laps by their recorded IDs.")
        selected = set(lap_ids)
        if len(selected) != len(lap_ids):
            raise ValueError("A lap may appear only once in a group.")
        if selected - known.keys():
            raise ValueError("Selected laps must belong to this recording.")
        if selected & used:
            raise ValueError("A lap may belong to only one custom group.")
        group_ids.add(group_id)
        used.update(selected)
        normalized.append(
            {
                "id": group_id,
                "name": name.strip(),
                "lap_ids": sorted(selected, key=known.__getitem__),
            }
        )
    return normalized


def _boundary_strength(before: dict, after: dict) -> int:
    """Rank observed events; missing context never becomes an inferred event."""
    score = 0
    if before.get("timeline_epoch", 0) != after.get("timeline_epoch", 0):
        score += 1000
    for field, weight in (("compound", 100), ("run_serial", 70), ("weather", 30)):
        left, right = before.get(field), after.get(field)
        if left is not None and right is not None and left != right:
            score += weight
    left_setup, right_setup = setup_key(before), setup_key(after)
    if (
        left_setup != "unknown"
        and right_setup != "unknown"
        and left_setup != right_setup
    ):
        score += 60
    left_age, right_age = (
        number(before.get("tyre_age_start")),
        number(after.get("tyre_age_start")),
    )
    if left_age is not None and right_age is not None and right_age < left_age:
        score += 50
    if before.get("pit_status") and not after.get("pit_status"):
        score += 40
    return score


def suggest_groups(laps: list[dict], count: int) -> list[dict]:
    """Select exactly N contiguous groups, prioritizing events then balanced spans.

    Equal-strength boundaries prefer the centre of the largest available span.
    This keeps suggestions stable, avoids one-lap shards when events are equal,
    and needs only O(N * count) work for large archived sessions.
    """
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or not 2 <= count <= MAX_GROUPS
    ):
        raise ValueError(f"Choose between 2 and {MAX_GROUPS} suggested groups.")
    if count > len(laps):
        raise ValueError("There must be at least one recorded lap per group.")
    scores = {
        index: _boundary_strength(laps[index - 1], laps[index])
        for index in range(1, len(laps))
    }
    boundaries = [0, len(laps)]
    while len(boundaries) < count + 1:
        candidates = []
        for start, end in pairwise(boundaries):
            for split in range(start + 1, end):
                candidates.append(
                    (
                        scores[split],
                        min(split - start, end - split),
                        end - start,
                        -split,
                    )
                )
        split = -max(candidates)[-1]
        boundaries.append(split)
        boundaries.sort()
    definitions = [
        {
            "id": "group-"
            + hashlib.sha256(
                json.dumps([str(lap["id"]) for lap in laps[start:end]]).encode()
            ).hexdigest()[:16],
            "name": f"Stint {chr(65 + index)}",
            "lap_ids": [str(lap["id"]) for lap in laps[start:end]],
        }
        for index, (start, end) in enumerate(pairwise(boundaries))
    ]
    return validate_groups(laps, definitions)


def context_notes(laps: list[dict]) -> list[dict]:
    """Return each applicable saved report once, retaining source and scope."""
    notes = []
    seen = {}
    for lap in laps:
        for note in lap.get("context_notes", []):
            key = note.get("id") or (
                note.get("source"),
                note.get("text"),
                tuple(note.get("lap_ids", [])),
            )
            if key not in seen:
                item = copy.deepcopy(note)
                item["lap_ids"] = []
                notes.append(item)
                seen[key] = item
            if lap.get("id") not in seen[key]["lap_ids"]:
                seen[key]["lap_ids"].append(lap.get("id"))
    return notes


def conditions_summary(laps: list[dict]) -> dict:
    """Keep measured ranges and qualitative traffic reports visibly distinct."""
    result = {
        "lap_count": len(laps),
        "compounds": sorted({str(lap.get("compound") or "UNKNOWN") for lap in laps}),
        "weather": sorted({str(lap.get("weather") or "Unknown") for lap in laps}),
        "setup_ids": sorted({setup_key(lap) for lap in laps}),
        "timeline_epochs": sorted({lap.get("timeline_epoch", 0) for lap in laps}),
        "field_coverage": {},
    }
    for output, field in _RANGE_FIELDS.items():
        values = []
        observed_laps = 0
        for lap in laps:
            measured_range = (
                lap.get(f"{field}_range")
                if field in {"air_temp_c", "track_temp_c"}
                else None
            )
            if (
                isinstance(measured_range, (list, tuple))
                and len(measured_range) == 2
                and all(number(value) is not None for value in measured_range)
                and float(measured_range[0]) <= float(measured_range[1])
            ):
                values.extend(float(value) for value in measured_range)
                observed_laps += 1
            elif (value := number(lap.get(field))) is not None:
                values.append(value)
                observed_laps += 1
        result[output] = (
            [round(min(values), 3), round(max(values), 3)] if values else None
        )
        result["field_coverage"][output] = {
            "observed_laps": observed_laps,
            "missing_laps": len(laps) - observed_laps,
        }
    observed = sum(bool(lap.get("traffic_observed")) for lap in laps)
    result["traffic"] = {
        "observed_laps": observed,
        "unknown_laps": len(laps) - observed,
        "measured_affected_laps": sum(
            "traffic" in (lap.get("learning_exclusions") or []) for lap in laps
        ),
        "reported_laps": sum(
            any(
                note.get("category") == "traffic"
                for note in lap.get("context_notes", [])
            )
            for lap in laps
        ),
        "caveat": "Measured proximity flags show nearby cars. Driver and engineer traffic notes are qualitative reports, not measured time loss.",
    }
    gaps = [
        gap
        for lap in laps
        if (gap := number((lap.get("traffic_evidence") or {}).get("min_gap_ahead_s")))
        is not None
        and gap >= 0
    ]
    result["traffic"]["min_gap_ahead_s"] = min(gaps) if gaps else None
    result["traffic"]["close_following_observed_laps"] = sum(
        bool((lap.get("traffic_evidence") or {}).get("close_following_observed"))
        for lap in laps
    )
    result["traffic"]["basis"] = sorted(
        {
            str((lap.get("traffic_evidence") or {}).get("basis"))
            for lap in laps
            if (lap.get("traffic_evidence") or {}).get("basis")
        }
    )
    return result


def _group_summary(laps: list[dict]) -> dict:
    # Screen each continuous recorded run independently: a deliberately mixed
    # medium/hard group must not discard its slower compound as a pace outlier.
    cohorts = []
    for run in build_runs(laps):
        weather_cohorts = [
            list(items)
            for _, items in groupby(run["laps"], key=lambda lap: lap.get("weather"))
        ]
        if len(weather_cohorts) == 1:
            cohorts.append(run)
        else:
            cohorts.extend({"summary": run_summary(items)} for items in weather_cohorts)
    if len(cohorts) == 1:
        return cohorts[0]["summary"]
    summaries = [cohort["summary"] for cohort in cohorts]
    clean_ids = {lap_id for summary in summaries for lap_id in summary["clean_lap_ids"]}
    clean = [lap for lap in laps if lap["id"] in clean_ids]
    times = [lap["lap_time_ms"] / 1000 for lap in clean]
    temperature_ranges = [
        summary["tyre_inner_temp_range_c"]
        for summary in summaries
        if summary["tyre_inner_temp_range_c"]
    ]
    return {
        "lap_count": len(laps),
        "clean_lap_count": len(clean),
        "clean_lap_ids": [lap["id"] for lap in clean],
        "median_pace_s": round(median(times), 3) if times else None,
        "best_lap_s": round(min(times), 3) if times else None,
        "consistency_stdev_s": round(pstdev(times), 3) if len(times) > 1 else None,
        "observed_pace_trend_s_per_lap": None,
        "degradation_caveat": "Pace trend is unavailable across different runs, tyre sets, setups or timelines.",
        "tyre_inner_temp_range_c": [
            min(item[0] for item in temperature_ranges),
            max(item[1] for item in temperature_ranges),
        ]
        if temperature_ranges
        else None,
        "balance": {
            "wheelspin_corner_flags": sum(
                summary["balance"]["wheelspin_corner_flags"] for summary in summaries
            ),
            "locking_corner_flags": sum(
                summary["balance"]["locking_corner_flags"] for summary in summaries
            ),
            "available": any(summary["balance"]["available"] for summary in summaries),
            "caveat": "Handling indicators only; telemetry does not establish a setup cause.",
        },
        "excluded_laps": [
            item for summary in summaries for item in summary["excluded_laps"]
        ],
        "pace_filter": {
            "isolated_slow_lap_ids": [
                lap_id
                for summary in summaries
                for lap_id in summary.get("pace_filter", {}).get(
                    "isolated_slow_lap_ids", []
                )
            ],
            "rule": "Pace filtering is applied within each recorded run and weather cohort. Different compounds or sustained condition changes are not discarded merely for having a different pace.",
        },
        "takeaway": "This group spans recorded run changes; its median describes the selected clean laps."
        if len(clean) >= 3
        else "Too few clean laps to judge this group.",
        "next_test": "Compare the selected groups alongside their conditions and saved notes; keep a single tyre set and setup to assess a pace trend.",
    }


def build_groups(
    laps: list[dict], definitions: list[dict], lap_notes: list[dict] | None = None
) -> list[dict]:
    if definitions == []:
        return []
    definitions = validate_groups(laps, definitions)
    working = (
        apply_lap_notes(laps, lap_notes)
        if lap_notes is not None
        else copy.deepcopy(laps)
    )
    by_id = {str(lap["id"]): lap for lap in working}
    result = []
    for index, definition in enumerate(definitions):
        selected = [by_id[lap_id] for lap_id in definition["lap_ids"]]
        conditions = conditions_summary(selected)
        setups = conditions["setup_ids"]
        compounds = conditions["compounds"]
        result.append(
            {
                **definition,
                "number": index + 1,
                "source": "custom",
                "laps": selected,
                "lap_range": [selected[0]["lap_num"], selected[-1]["lap_num"]],
                "compound": compounds[0] if len(compounds) == 1 else "MIXED",
                "compounds": compounds,
                "setup_id": setups[0] if len(setups) == 1 else "mixed",
                "setup": setup_values(selected[0]) if len(setups) == 1 else {},
                "setup_changes": {},
                "summary": _group_summary(selected),
                "conditions": conditions,
                "context_notes": context_notes(selected),
                "heterogeneous": any(
                    len(conditions[key]) > 1
                    for key in ("compounds", "setup_ids", "weather", "timeline_epochs")
                ),
            }
        )
    return result


def compare_groups(a: dict, b: dict) -> dict:
    """Describe selected clean cohorts without claiming a controlled setup gain."""
    if {str(lap["id"]) for lap in a["laps"]} & {str(lap["id"]) for lap in b["laps"]}:
        raise ValueError("Comparison groups must not contain the same recorded lap.")
    clean = {}
    rejected = {}
    for side, group in (("a", a), ("b", b)):
        ids = set(group["summary"]["clean_lap_ids"])
        clean[side] = [
            lap for lap in group["laps"] if lap["id"] in ids and not exclusions(lap)
        ]
        accepted = {lap["id"] for lap in clean[side]}
        summary_reasons = {
            item.get("lap_id"): item["reasons"]
            for item in group["summary"].get("excluded_laps", [])
            if item.get("lap_id") is not None
        }
        rejected[side] = [
            {
                "lap": lap["lap_num"],
                "lap_id": lap["id"],
                "timeline_epoch": lap.get("timeline_epoch", 0),
                "reasons": exclusions(lap)
                or summary_reasons.get(lap["id"], ["pace_outlier"]),
            }
            for lap in group["laps"]
            if lap["id"] not in accepted
        ]
    medians = {
        side: median(lap["lap_time_ms"] / 1000 for lap in laps) if laps else None
        for side, laps in clean.items()
    }
    delta = (
        round(medians["b"] - medians["a"], 3)
        if all(value is not None for value in medians.values())
        else None
    )
    sector_cohorts = {
        side: [
            lap
            for lap in laps
            if all(
                (number(lap.get(field)) or 0) > 0
                for field in ("s1_ms", "s2_ms", "s3_ms")
            )
        ]
        for side, laps in clean.items()
    }
    sectors = [
        round(
            (
                median(lap[field] for lap in sector_cohorts["b"])
                - median(lap[field] for lap in sector_cohorts["a"])
            )
            / 1000,
            3,
        )
        if all(sector_cohorts.values())
        else None
        for field in ("s1_ms", "s2_ms", "s3_ms")
    ]
    conditions = {
        "a": conditions_summary(a["laps"]),
        "b": conditions_summary(b["laps"]),
    }
    clean_conditions = {side: conditions_summary(laps) for side, laps in clean.items()}
    differences = [
        {"field": field, "a": conditions["a"][field], "b": conditions["b"][field]}
        for field in ("compounds", "weather", "setup_ids", *_RANGE_FIELDS)
        if conditions["a"][field] != conditions["b"][field]
    ]
    enough = all(len(laps) >= 3 for laps in clean.values())
    caveats = [
        "Descriptive stint comparison using selected clean laps; conditions are not matched and the difference does not establish a setup or tyre cause."
    ]
    if conditions["a"]["compounds"] != conditions["b"]["compounds"]:
        caveats.append(
            f"Different compounds: {' / '.join(conditions['a']['compounds'])} versus {' / '.join(conditions['b']['compounds'])}."
        )
    if any(conditions[side]["traffic"]["unknown_laps"] for side in ("a", "b")):
        caveats.append(
            "Some selected laps lack recorded traffic evidence; absence of a flag does not establish clear air."
        )
    if any(
        value["missing_laps"]
        for side in ("a", "b")
        for value in conditions[side]["field_coverage"].values()
    ):
        caveats.append(
            "Some condition measurements are missing; ranges contain only recorded values."
        )
    if any(
        str(value).strip().lower() in {"unknown", "", "none"}
        for side in ("a", "b")
        for field in ("compounds", "weather", "setup_ids")
        for value in conditions[side][field]
    ):
        caveats.append(
            "Some selected laps have no recorded compound, weather or setup identity; an unknown value is not evidence that these conditions matched."
        )
    if any(
        len(conditions[side][key]) > 1
        for side in ("a", "b")
        for key in ("compounds", "setup_ids", "weather", "timeline_epochs")
    ):
        caveats.append(
            "At least one group spans different compounds, setups, weather or timelines; its pace is an aggregate of those laps."
        )
    if any(len(sector_cohorts[side]) != len(clean[side]) for side in ("a", "b")):
        caveats.append(
            "Sector differences use only clean laps with all three sectors recorded; that cohort may differ from the overall pace cohort."
        )
    if not all(len(laps) >= 3 for laps in sector_cohorts.values()):
        caveats.append(
            "Fewer than three complete-sector laps are available in at least one group; sector differences are preliminary."
        )
    notes = {"a": context_notes(a["laps"]), "b": context_notes(b["laps"])}
    if any(notes.values()):
        caveats.append(
            "Saved driver and engineer notes were included. Only notes explicitly marked to exclude a lap change its pace eligibility; reports do not overwrite telemetry."
        )
    conclusion = (
        f"B was {'quicker' if delta < 0 else 'slower' if delta > 0 else 'equal'} by {abs(delta):.3f} s on median selected clean pace."
        if enough and delta is not None
        else f"Only {len(clean['a'])} clean laps in A and {len(clean['b'])} in B; collect at least three in each before judging the comparison."
    )
    return {
        "mode": "stint",
        "a_run": a["number"],
        "b_run": b["number"],
        "a_name": a.get("name", f"Run {a['number']}"),
        "b_name": b.get("name", f"Run {b['number']}"),
        "pairs": [],
        "excluded": rejected,
        "enough_evidence": enough,
        "median_delta_s": delta,
        "median_pace_s": medians,
        "sector_deltas_s": sectors,
        "clean_lap_counts": {side: len(laps) for side, laps in clean.items()},
        "sector_lap_counts": {side: len(laps) for side, laps in sector_cohorts.items()},
        "sector_lap_ids": {
            side: [lap["id"] for lap in laps] for side, laps in sector_cohorts.items()
        },
        "delta_definition": "B minus A; negative means B was quicker. Medians describe selected clean laps, without condition matching.",
        "conditions": conditions,
        "clean_conditions": clean_conditions,
        "condition_differences": differences,
        "notes": notes,
        "conclusion": conclusion,
        "caveats": caveats,
    }
