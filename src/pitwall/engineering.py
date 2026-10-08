"""Deterministic run reviews, matched setup tests and strategic race context."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import uuid
from datetime import UTC, datetime
from itertools import pairwise
from statistics import median, pstdev
from typing import Any

from .database import PitWallDatabase


def number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def setup_values(lap: dict) -> dict:
    return {
        key: value
        for key, value in (lap.get("setup") or {}).items()
        if key not in {"fuel_load", "fuel_kg"}
    }


def setup_key(lap: dict) -> str:
    values = setup_values(lap)
    return (
        hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:12]
        if values
        else "unknown"
    )


def exclusions(lap: dict, *, strict: bool = False) -> list[str]:
    reasons = list(lap.get("learning_exclusions") or [])
    if not lap.get("valid") or (number(lap.get("lap_time_ms")) or 0) <= 0:
        reasons.append("invalid_lap")
    if lap.get("pit_status"):
        reasons.append("pit_lap")
    if lap.get("flag_context"):
        reasons.append("neutralised_lap")
    if strict:
        if not lap.get("context_observed") or not lap.get("traffic_observed"):
            reasons.append("missing_condition_or_traffic_evidence")
        if (number(lap.get("trace_coverage")) or 0) < 0.9:
            reasons.append("incomplete_lap_telemetry")
        if setup_key(lap) == "unknown":
            reasons.append("unknown_setup")
        if str(lap.get("compound", "UNKNOWN")).upper() in {"UNKNOWN", "NONE", ""}:
            reasons.append("unknown_compound")
        if str(lap.get("weather", "Unknown")).lower() in {"unknown", "", "none"}:
            reasons.append("unknown_weather")
        for field in ("tyre_age_start", "fuel_start_kg", "track_temp_c", "air_temp_c"):
            if number(lap.get(field)) is None:
                reasons.append(f"missing_{field}")
        for field in ("track_temp_c", "air_temp_c"):
            bounds = lap.get(f"{field}_range")
            if (
                isinstance(bounds, list)
                and len(bounds) == 2
                and all(number(item) is not None for item in bounds)
                and abs(bounds[1] - bounds[0]) > 3
            ):
                reasons.append(f"changing_{field}")
        if (number(lap.get("fuel_start_kg")) or 0) <= 0:
            reasons.append("unknown_fuel")
    return sorted(set(reasons))


def _summary_lap_id(lap: dict) -> str:
    return (
        str(lap["id"])
        if lap.get("id") is not None
        else f"epoch-{lap.get('timeline_epoch', 0)}-lap-{lap.get('lap_num')}"
    )


def _isolated_slow_lap_ids(laps: list[dict]) -> set[str]:
    """Flag a temporary pace excursion only when measured context stays stable.

    The label describes an observation, not its cause: a cooldown, driving
    error or obstruction cannot be distinguished from lap times alone.
    """
    result = set()
    for before, current, after in zip(laps, laps[1:], laps[2:]):
        trio = (before, current, after)
        if any(exclusions(lap) for lap in trio):
            continue
        if not all(
            lap.get("context_observed") and lap.get("traffic_observed") for lap in trio
        ):
            continue
        if not all(
            number(lap.get("lap_num")) is not None
            and number(lap.get("lap_time_ms")) is not None
            for lap in trio
        ):
            continue
        if not (
            before["lap_num"] + 1 == current["lap_num"]
            and current["lap_num"] + 1 == after["lap_num"]
        ):
            continue
        if any(
            len({lap.get(field) for lap in trio}) != 1
            for field in ("timeline_epoch", "run_serial", "compound", "weather")
        ):
            continue
        if any(
            current.get(field) is None
            for field in ("timeline_epoch", "run_serial", "compound", "weather")
        ):
            continue
        if str(current["compound"]).lower() == "unknown" or str(
            current["weather"]
        ).lower() in {"unknown", "", "none"}:
            continue
        setups = {setup_key(lap) for lap in trio}
        if len(setups) != 1 or "unknown" in setups:
            continue
        stable = True
        for field in ("air_temp_c", "track_temp_c"):
            values = []
            for lap in trio:
                measured_range = lap.get(f"{field}_range")
                if (
                    isinstance(measured_range, (list, tuple))
                    and len(measured_range) == 2
                    and all(number(value) is not None for value in measured_range)
                    and float(measured_range[0]) <= float(measured_range[1])
                ):
                    values.extend(float(value) for value in measured_range)
                elif number(lap.get(field)) is not None:
                    values.append(float(lap[field]))
                else:
                    stable = False
            if not values or max(values) - min(values) > 3:
                stable = False
        fuel = [number(lap.get("fuel_start_kg")) for lap in trio]
        age = [number(lap.get("tyre_age_start")) for lap in trio]
        if any(value is None or value <= 0 for value in fuel) or any(
            value is None or value < 0 for value in age
        ):
            continue
        if any(abs(right - left) > 3 for left, right in pairwise(fuel)):
            continue
        if any(not 0 <= right - left <= 2 for left, right in pairwise(age)):
            continue
        baseline = (before["lap_time_ms"] + after["lap_time_ms"]) / 2
        returned = abs(before["lap_time_ms"] - after["lap_time_ms"]) <= max(
            1000, baseline * 0.01
        )
        if (
            stable
            and returned
            and current["lap_time_ms"] - baseline > max(2500, baseline * 0.03)
        ):
            result.add(_summary_lap_id(current))
    return result


def run_summary(laps: list[dict]) -> dict:
    transient = _isolated_slow_lap_ids(laps)
    clean = [
        lap
        for lap in laps
        if not exclusions(lap) and _summary_lap_id(lap) not in transient
    ]
    # A median/MAD screen rejects unusually slow or short laps without deleting
    # them from the report. It is deliberately conservative for short runs.
    if len(clean) >= 4:
        center = median(lap["lap_time_ms"] for lap in clean)
        mad = median(abs(lap["lap_time_ms"] - center) for lap in clean)
        clean = [
            lap
            for lap in clean
            if abs(lap["lap_time_ms"] - center) <= max(2500, 4 * mad)
        ]
    times = [lap["lap_time_ms"] / 1000 for lap in clean]
    slopes = [
        (b["lap_time_ms"] - a["lap_time_ms"])
        / 1000
        / (b["tyre_age_start"] - a["tyre_age_start"])
        for index, a in enumerate(clean)
        for b in clean[index + 1 :]
        if number(a.get("tyre_age_start")) is not None
        and number(b.get("tyre_age_start")) is not None
        and b["tyre_age_start"] > a["tyre_age_start"]
    ]
    temperatures = [
        float(temp)
        for lap in clean
        for temp in lap.get("temps_end", [])
        if number(temp) is not None and 0 < float(temp) < 200
    ]
    corners = [corner for lap in clean for corner in lap.get("corner_metrics", [])]
    spin = sum(bool(corner.get("wheelspin")) for corner in corners)
    locks = sum(bool(corner.get("wheel_lock")) for corner in corners)
    spread = round(pstdev(times), 3) if len(times) > 1 else None
    trend = round(median(slopes), 3) if len(clean) >= 3 and len(slopes) >= 3 else None
    if len(clean) < 3:
        takeaway = "Too few clean laps to judge the run."
        next_test = "Repeat the setup for at least three clean timed laps on the same compound and comparable starting fuel."
    elif spread is not None and spread > 1:
        takeaway = "Lap variation is large enough to mask a small setup gain."
        next_test = "Repeat this setup in clear air before changing it; aim for consistent braking and exits."
    elif spin or locks:
        takeaway = f"Observed {spin} corner wheelspin and {locks} locking flags across the clean laps."
        next_test = "Repeat the affected corners with smoother inputs, then test one relevant setup setting at a time."
    elif trend is not None and trend > 0.15:
        takeaway = "The run slowed as the tyres aged; fuel and conditions also affect this trend."
        next_test = "Repeat a longer run on the same compound and starting fuel to confirm the pace trend."
    else:
        takeaway = "The clean laps provide a baseline for a controlled setup test."
        next_test = "Change one setup setting, keep compound and starting fuel comparable, then compare matched laps."
    excluded = [
        {
            "lap": lap.get("lap_num"),
            "lap_id": lap.get("id"),
            "timeline_epoch": lap.get("timeline_epoch", 0),
            "reasons": exclusions(lap)
            or (
                ["isolated_slow_lap"]
                if _summary_lap_id(lap) in transient
                else ["pace_outlier"]
            ),
        }
        for lap in laps
        if lap not in clean
    ]
    return {
        "lap_count": len(laps),
        "clean_lap_count": len(clean),
        "clean_lap_ids": [lap.get("id") for lap in clean],
        "median_pace_s": round(median(times), 3) if times else None,
        "best_lap_s": round(min(times), 3) if times else None,
        "consistency_stdev_s": spread,
        "observed_pace_trend_s_per_lap": trend,
        "degradation_caveat": "Observed pace trend, not isolated tyre degradation; fuel, driving and conditions can change it.",
        "tyre_inner_temp_range_c": [
            round(min(temperatures), 1),
            round(max(temperatures), 1),
        ]
        if temperatures
        else None,
        "balance": {
            "wheelspin_corner_flags": spin,
            "locking_corner_flags": locks,
            "available": bool(corners),
            "caveat": "Handling indicators only; telemetry does not establish a setup cause.",
        },
        "excluded_laps": excluded,
        "pace_filter": {
            "isolated_slow_lap_ids": sorted(transient),
            "rule": "An isolated slow lap is excluded only between consecutive clean laps returning to similar pace with stable recorded setup, compound, weather, fuel and temperatures. It remains in its original run; the cause is not inferred.",
            "slow_threshold_s": 2.5,
            "slow_threshold_fraction": 0.03,
            "return_tolerance_s": 1.0,
            "return_tolerance_fraction": 0.01,
        },
        "takeaway": takeaway,
        "next_test": next_test,
    }


def build_runs(laps: list[dict]) -> list[dict]:
    runs: list[dict] = []
    previous: dict | None = None
    for lap in laps:
        signature = (
            lap.get("run_serial"),
            lap.get("timeline_epoch", 0),
            lap.get("compound"),
            setup_key(lap),
        )
        previous_signature = (
            (
                previous.get("run_serial"),
                previous.get("timeline_epoch", 0),
                previous.get("compound"),
                setup_key(previous),
            )
            if previous
            else None
        )
        new_tyres = previous and (number(lap.get("tyre_age_start")) or 0) < (
            number(previous.get("tyre_age_start")) or 0
        )
        if previous and "pit_lap" in (previous.get("learning_exclusions") or []):
            new_tyres = False
        legacy_exit = (
            previous
            and not lap.get("run_serial")
            and previous.get("pit_status")
            and not lap.get("pit_status")
        )
        if signature != previous_signature or new_tyres or legacy_exit:
            old_setup = setup_values(previous or {})
            new_setup = setup_values(lap)
            runs.append(
                {
                    "id": str(lap.get("id", f"run-{len(runs) + 1}")),
                    "number": len(runs) + 1,
                    "compound": lap.get("compound", "UNKNOWN"),
                    "setup_id": setup_key(lap),
                    "setup": new_setup,
                    "setup_changes": {
                        key: {"from": old_setup.get(key), "to": new_setup.get(key)}
                        for key in sorted(old_setup.keys() | new_setup.keys())
                        if previous and old_setup.get(key) != new_setup.get(key)
                    },
                    "run_serial": lap.get("run_serial"),
                    "laps": [],
                }
            )
        runs[-1]["laps"].append(lap)
        previous = lap
    for run in runs:
        run["summary"] = run_summary(run["laps"])
        run["lap_range"] = [run["laps"][0]["lap_num"], run["laps"][-1]["lap_num"]]
    return runs


MATCH_LIMITS = {
    "tyre_age_laps": 1,
    "fuel_kg": 3.0,
    "track_temp_c": 3.0,
    "air_temp_c": 3.0,
    "traffic_gap_s": 1.5,
    "minimum_pairs": 3,
}

# Same material-length tolerance used by telemetry.comparison.classify_compatibility.
CROSS_SESSION_LENGTH_TOLERANCE_M = 15.0


def recorded_track_length(session: dict) -> float | None:
    """Read only the known live-catalogue signature, not a guessed circuit length."""
    parts = str(session.get("track_layout_signature") or "").split(":")
    if (
        len(parts) != 4
        or parts[0] != "f1"
        or number(parts[2]) != session.get("track_id")
    ):
        return None
    length = number(parts[3])
    return length if length is not None and length > 0 else None


def compare_runs(a: dict, b: dict, *, match_session_mode: bool = False) -> dict:
    """One-to-one nearest-condition matching. Positive deltas mean B is slower."""
    from .engineering_groups import conditions_summary, context_notes

    rejected: dict[str, list] = {"a": [], "b": []}
    eligible = {}
    for side, run in (("a", a), ("b", b)):
        mixed_setup = len({setup_key(lap) for lap in run["laps"]}) > 1
        clean_ids = set(run["summary"]["clean_lap_ids"])
        summary_exclusions = {
            item.get("lap_id"): item["reasons"]
            for item in run["summary"]["excluded_laps"]
        }
        eligible[side] = []
        for lap in run["laps"]:
            reasons = exclusions(lap, strict=True)
            if match_session_mode and str(lap.get("mode_profile") or "").lower() in {
                "",
                "unknown",
                "idle",
                "none",
            }:
                reasons.append("unknown_session_mode")
            if mixed_setup:
                reasons.append("multiple_setups_in_group")
            if lap.get("id") not in clean_ids and not reasons:
                reasons.extend(summary_exclusions.get(lap.get("id"), ["pace_outlier"]))
            if reasons:
                rejected[side].append(
                    {
                        "lap": lap["lap_num"],
                        "lap_id": lap.get("id"),
                        "timeline_epoch": lap.get("timeline_epoch", 0),
                        "reasons": reasons,
                    }
                )
            else:
                eligible[side].append(lap)
    candidates = []
    for left in eligible["a"]:
        for right in eligible["b"]:
            if (
                left["compound"] != right["compound"]
                or left["weather"] != right["weather"]
                or (
                    match_session_mode
                    and str(left.get("mode_profile") or "").lower()
                    != str(right.get("mode_profile") or "").lower()
                )
            ):
                continue
            differences = [
                abs(number(left[key]) - number(right[key])) / limit
                for key, limit in (
                    ("tyre_age_start", 1),
                    ("fuel_start_kg", 3),
                    ("track_temp_c", 3),
                    ("air_temp_c", 3),
                )
            ]
            if max(differences) <= 1:
                candidates.append(
                    (sum(differences), str(left["id"]), str(right["id"]), left, right)
                )
    pairs, used_a, used_b = [], set(), set()
    for _, left_id, right_id, left, right in sorted(
        candidates, key=lambda item: item[:3]
    ):
        if left_id in used_a or right_id in used_b:
            continue
        used_a.add(left_id)
        used_b.add(right_id)
        sectors = []
        for key in ("s1_ms", "s2_ms", "s3_ms"):
            left_sector, right_sector = number(left.get(key)), number(right.get(key))
            sectors.append(
                (right_sector - left_sector) / 1000
                if left_sector is not None
                and right_sector is not None
                and left_sector > 0
                and right_sector > 0
                else None
            )
        pairs.append(
            {
                "a_lap": left["lap_num"],
                "b_lap": right["lap_num"],
                "a_lap_id": left_id,
                "b_lap_id": right_id,
                "delta_s": round(
                    (right["lap_time_ms"] - left["lap_time_ms"]) / 1000, 3
                ),
                "sector_deltas_s": [
                    round(value, 3) if value is not None else None for value in sectors
                ],
                "tyre_ages": [left["tyre_age_start"], right["tyre_age_start"]],
                "fuel_kg": [left["fuel_start_kg"], right["fuel_start_kg"]],
                "track_temp_c": [left["track_temp_c"], right["track_temp_c"]],
                "air_temp_c": [left["air_temp_c"], right["air_temp_c"]],
                "context_notes": {
                    "a": left.get("context_notes", []),
                    "b": right.get("context_notes", []),
                },
                "traffic_evidence": {
                    "a": left.get("traffic_evidence"),
                    "b": right.get("traffic_evidence"),
                },
            }
        )
    for side, used in (("a", used_a), ("b", used_b)):
        rejected[side].extend(
            {
                "lap": lap["lap_num"],
                "lap_id": lap.get("id"),
                "timeline_epoch": lap.get("timeline_epoch", 0),
                "reasons": ["no_unused_condition_match"],
            }
            for lap in eligible[side]
            if str(lap["id"]) not in used
        )
    delta = round(median(pair["delta_s"] for pair in pairs), 3) if pairs else None
    enough = len(pairs) >= MATCH_LIMITS["minimum_pairs"]
    same_setup = a["setup_id"] == b["setup_id"] and a["setup_id"] not in {
        "mixed",
        "unknown",
    }
    sectors = [
        round(median(pair["sector_deltas_s"][i] for pair in pairs), 3)
        if pairs and all(pair["sector_deltas_s"][i] is not None for pair in pairs)
        else None
        for i in range(3)
    ]
    return {
        "mode": "setup",
        "a_run": a["number"],
        "b_run": b["number"],
        "pairs": pairs,
        "excluded": rejected,
        "criteria": {
            **MATCH_LIMITS,
            **({"session_mode": "same recorded mode"} if match_session_mode else {}),
        },
        "enough_evidence": enough,
        "same_setup": same_setup,
        "median_delta_s": delta,
        "sector_deltas_s": sectors,
        "conditions": {
            "a": conditions_summary(a["laps"]),
            "b": conditions_summary(b["laps"]),
        },
        "notes": {"a": context_notes(a["laps"]), "b": context_notes(b["laps"])},
        "delta_definition": "B minus A; negative means B was quicker.",
        "conclusion": (
            f"B was {'quicker' if delta < 0 else 'slower' if delta > 0 else 'equal'} by {abs(delta):.3f} s on the median matched lap."
            if enough
            else f"Only {len(pairs)} matched pairs; collect at least three before judging the test."
        ),
        "caveats": [
            "Observational comparison; matched conditions do not prove setup caused the difference."
        ]
        + (
            [
                "Each setup-test group must contain one recorded setup. Use stint comparison for groups with setup changes."
            ]
            if any(len({setup_key(lap) for lap in run["laps"]}) > 1 for run in (a, b))
            else []
        )
        + (["Both runs have the same recorded setup."] if same_setup else []),
    }


def strategic_rivals(state: dict) -> dict:
    strategy = state.get("strategy") or {}
    plan = strategy.get("recommended") or {}
    player_time = number(plan.get("projected_time_s"))
    if state.get("mode_profile") != "race" or player_time is None:
        return {
            "available": False,
            "reason": "A race and a current finish projection are needed.",
            "rivals": [],
        }
    rivals = []
    projections = strategy.get("rival_finish_projections") or []
    for rival in projections:
        finish = number(rival.get("finish_time_s"))
        if finish is None:
            continue
        delta = finish - player_time
        estimated_position = (
            1
            + sum(
                (number(other.get("finish_time_s")) or math.inf) < finish
                for other in projections
            )
            + (player_time < finish)
        )
        rivals.append(
            {
                "driver": rival.get("driver"),
                "position": rival.get("position"),
                "projected_position": estimated_position,
                "projected_gap_s": round(delta, 3),
                "current_gap_s": rival.get("current_gap_s"),
                "compound": rival.get("compound"),
                "tyre_age": rival.get("tyre_age"),
                "estimated_stops_remaining": rival.get("likely_remaining_stops"),
                "confidence": rival.get("confidence", "low"),
                "gap_assumed": bool(rival.get("gap_assumed")),
                "reason": "Closest projected finish after estimated stops, tyre pace and pending penalties.",
            }
        )
    rivals.sort(
        key=lambda item: (abs(item["projected_gap_s"]), item.get("position") or 99)
    )
    return {
        "available": bool(rivals),
        "rivals": rivals[:5],
        "player_projected_position": plan.get(
            "expected_position", plan.get("projected_position")
        ),
        "player_stops_remaining": plan.get("stops_remaining"),
        "caveat": "Forecasts depend on the selected plan and estimated rival stops. Stops owed are estimates, not observed obligations; missing gaps lower confidence.",
    }


class EngineeringService:
    def __init__(self, database: PitWallDatabase):
        self.database = database

    async def report(self, session_id: str) -> dict:
        return await asyncio.to_thread(self._report, session_id)

    async def compare(
        self,
        session_id: str,
        a: str,
        b: str,
        *,
        source: str = "runs",
        mode: str = "setup",
        b_session_id: str | None = None,
        b_source: str | None = None,
    ) -> dict:
        return await asyncio.to_thread(
            self._compare,
            session_id,
            a,
            b,
            source,
            mode,
            b_session_id or session_id,
            b_source or source,
        )

    def _compare(
        self,
        session_id: str,
        a: str,
        b: str,
        source: str,
        mode: str,
        b_session_id: str,
        b_source: str,
    ) -> dict:
        from .engineering_groups import compare_groups

        if source not in {"runs", "groups"} or b_source not in {"runs", "groups"}:
            raise ValueError(
                "Choose automatic runs or saved lap groups for each selection."
            )
        if mode not in {"setup", "stint"}:
            raise ValueError("Choose setup or stint comparison.")
        cross_session = session_id != b_session_id
        report_a = self._report(session_id)
        report_b = self._report(b_session_id) if cross_session else report_a
        if cross_session and (
            report_a["track_id"] is None
            or report_b["track_id"] is None
            or report_a["track_id"] < 0
            or report_b["track_id"] < 0
            or report_a["track_id"] != report_b["track_id"]
        ):
            raise ValueError(
                "Cross-session comparisons require the same known track in both recordings."
            )
        selected = []
        selections = {}
        for side, report, kind, identity in (
            ("a", report_a, source, a),
            ("b", report_b, b_source, b),
        ):
            item = next((item for item in report[kind] if item["id"] == identity), None)
            if item is None:
                raise KeyError(
                    f"Selection {side.upper()} does not belong to its selected session."
                )
            if not item["laps"]:
                raise ValueError(
                    "Choose a nonempty recorded run or lap group on each side."
                )
            selected.append(item)
            selections[side] = {
                key: report[key]
                for key in (
                    "session_id",
                    "track_id",
                    "track_name",
                    "session_type",
                    "mode_profile",
                    "started_at",
                    "packet_format",
                    "track_layout_signature",
                )
            }
            lengths = [
                value
                for lap in item["laps"]
                if (value := number(lap.get("track_length_m"))) is not None
                and value > 0
            ]
            if not lengths and report.get("track_length_m") is not None:
                lengths = [report["track_length_m"]]
            selections[side].update(
                source=kind,
                id=identity,
                name=item.get("name") or f"Run {item['number']}",
                lap_ids=[lap["id"] for lap in item["laps"]],
                notes=dict(item.get("notes") or {}),
                session_notes={
                    key: report["notes"].get(key, "")
                    for key in ("objective", "conclusion")
                },
                track_length_range_m=[min(lengths), max(lengths)] if lengths else None,
            )
        if set(selections["a"]["lap_ids"]) & set(selections["b"]["lap_ids"]):
            raise ValueError("Choose two selections without overlapping recorded laps.")
        if cross_session:
            lengths = [
                value
                for side in selections.values()
                for value in side["track_length_range_m"] or []
            ]
            if (
                lengths
                and max(lengths) - min(lengths) > CROSS_SESSION_LENGTH_TOLERANCE_M
            ):
                raise ValueError(
                    "Recorded track lengths differ by more than 15 metres; these layouts cannot be compared."
                )
        result = (
            compare_runs(*selected, match_session_mode=cross_session)
            if mode == "setup"
            else compare_groups(*selected)
        )
        result.update(cross_session=cross_session, selections=selections)
        if cross_session:
            formats = [selections[side]["packet_format"] for side in ("a", "b")]
            known_lengths = all(
                selections[side]["track_length_range_m"] for side in ("a", "b")
            )
            result["comparison_compatibility"] = {
                "same_track_id": True,
                "track_length_tolerance_m": CROSS_SESSION_LENGTH_TOLERANCE_M,
                "track_length_status": "recorded_within_tolerance"
                if known_lengths
                else "unavailable",
                "packet_format_status": "unavailable"
                if not all(formats)
                else "same"
                if formats[0] == formats[1]
                else "different",
                "game_version": "unverified",
                "car_formula": "unverified",
                "car_performance": "unverified",
            }
            if not known_lengths:
                result["caveats"].append(
                    "Recorded layout length is unavailable for at least one selection; the track ID matches but layout length could not be checked."
                )
            if all(formats) and formats[0] != formats[1]:
                result["caveats"].append(
                    f"Different UDP packet formats ({formats[0]} and {formats[1]}). Protocol format does not identify the game version or car formula."
                )
            modes = {
                side: sorted(
                    {
                        str(lap.get("mode_profile") or "unknown").lower()
                        for lap in item["laps"]
                    }
                )
                for side, item in zip(("a", "b"), selected)
            }
            result["session_modes"] = modes
            if modes["a"] != modes["b"]:
                result["caveats"].append(
                    f"Different session modes: {' / '.join(modes['a'])} versus {' / '.join(modes['b'])}. "
                    + (
                        "Setup comparisons require the same recorded mode."
                        if mode == "setup"
                        else "These runs are descriptive and their session conditions are not matched."
                    )
                )
            result["caveats"].append(
                "Different saved sessions on the same track. Recorded conditions and each session's notes are used; "
                "matching does not establish identical track grip, car performance or game version."
            )
            result["caveats"].append(
                "Game version, car formula and car-performance compatibility were not verified by these recordings."
            )
        return result

    def _report(self, session_id: str) -> dict:
        from .engineering_groups import (
            apply_lap_notes,
            build_groups,
            conditions_summary,
        )

        with self.database._connect() as db:
            row = db.execute(
                "SELECT * FROM recorded_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            session = dict(row)
            rows = db.execute(
                """SELECT r.* FROM recorded_laps r JOIN session_cars c ON c.id=r.session_car_id
                WHERE c.session_id=? AND c.is_player=1 ORDER BY r.timeline_epoch,r.lap_number,r.created_at""",
                (session_id,),
            ).fetchall()
        laps = []
        for row in rows:
            context = json.loads(row["engineering_json"] or "{}")
            context.update(
                id=row["id"],
                lap_num=row["lap_number"],
                lap_time_ms=row["lap_time_ms"] or 0,
                valid=bool(row["valid"]),
                timeline_epoch=row["timeline_epoch"],
            )
            context.setdefault("mode_profile", session["mode_profile"])
            context.setdefault("session_type", session["session_type"])
            for key, column in (
                ("compound", "tyre_compound"),
                ("tyre_age_start", "tyre_age_laps"),
                ("fuel_start_kg", "fuel_start_kg"),
                ("weather", "weather_class"),
                ("pit_status", "pit_context"),
                ("flag_context", "flag_context"),
            ):
                context.setdefault(key, row[column])
            laps.append(context)
        notes = json.loads(session["engineering_notes_json"] or "{}")
        lap_notes = []
        for saved in notes.get("lap_notes", []):
            # A note recorded during a lap binds only to that timeline and lap.
            # Flashbacks and restarts never redirect it to another attempt.
            pending = {
                (item["timeline_epoch"], item["lap_num"])
                for item in saved.get("pending_laps", [])
            }
            targets = set(saved["lap_ids"])
            targets.update(
                lap["id"]
                for lap in laps
                if (lap["timeline_epoch"], lap["lap_num"]) in pending
            )
            lap_notes.append({**saved, "lap_ids": sorted(targets)})
        laps = apply_lap_notes(laps, lap_notes)
        runs = build_runs(laps)
        for run in runs:
            run["notes"] = notes.get("runs", {}).get(run["id"], {})
            run["conditions"] = conditions_summary(run["laps"])
        groups = build_groups(laps, notes.get("groups", []))
        for group in groups:
            group["notes"] = notes.get("runs", {}).get(group["id"], {})
        from .udp import TRACKS

        track = TRACKS.get(session["track_id"], f"Track {session['track_id']}")
        incidents = [
            {
                "lap": lap["lap_num"],
                "items": exclusions(lap)
                + [event["type"] for event in lap.get("incidents", [])],
                "events": lap.get("incidents", []),
            }
            for lap in laps
            if exclusions(lap) or lap.get("incidents")
        ]
        return {
            "session_id": session_id,
            "track_id": session["track_id"],
            "track_name": track,
            "session_type": session["session_type"],
            "mode_profile": session["mode_profile"],
            "packet_format": session.get("packet_format") or None,
            "track_layout_signature": str(session.get("track_layout_signature") or "")[
                :180
            ]
            or None,
            "track_length_m": recorded_track_length(session),
            "status": session["status"],
            "started_at": session["started_at"],
            "notes": notes,
            "runs": runs,
            "laps": laps,
            "groups": groups,
            "lap_notes": lap_notes,
            "incidents": incidents,
            "conclusions": [
                f"Run {run['number']}: {run['summary']['takeaway']}" for run in runs
            ],
            "limitations": [
                "Older recordings may lack run boundaries, setup, traffic or temperature evidence; unavailable fields are not inferred.",
                "Pace trends include fuel, driving and condition effects. Invalid, pit, flagged and observed traffic laps are excluded.",
            ],
        }

    async def save_notes(
        self, session_id: str, run_id: str | None, objective: str, conclusion: str
    ) -> dict:
        async with self.database._lock:
            return await asyncio.to_thread(
                self._save_notes, session_id, run_id, objective, conclusion
            )

    def _save_notes(
        self, session_id: str, run_id: str | None, objective: str, conclusion: str
    ) -> dict:
        report = self._report(session_id)
        if run_id is not None and not any(
            run["id"] == run_id for run in report["runs"] + report["groups"]
        ):
            raise KeyError(run_id)
        with self.database._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT engineering_notes_json FROM recorded_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            notes = json.loads(row[0] or "{}")
            item = {"objective": objective.strip(), "conclusion": conclusion.strip()}
            if run_id is None:
                notes.update(item)
            else:
                notes.setdefault("runs", {})[run_id] = item
            db.execute(
                "UPDATE recorded_sessions SET engineering_notes_json=? WHERE id=?",
                (json.dumps(notes), session_id),
            )
        return item

    async def save_groups(self, session_id: str, groups: list[dict]) -> dict:
        from .engineering_groups import validate_groups

        async with self.database._lock:
            report = await self.report(session_id)
            definitions = validate_groups(report["laps"], groups) if groups else []
            if any(
                group["id"] in {run["id"] for run in report["runs"]}
                for group in definitions
            ):
                raise ValueError(
                    "Custom group identifiers must differ from automatic run identifiers."
                )
            await asyncio.to_thread(
                self._update_metadata,
                session_id,
                lambda notes: notes.update(groups=definitions),
            )
        return await self.report(session_id)

    def _update_metadata(self, session_id: str, change):
        # Notes and groups share a document. Read-modify-write inside a SQLite
        # transaction so another UI/voice request cannot drop unrelated edits.
        with self.database._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT engineering_notes_json FROM recorded_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            notes = json.loads(row[0] or "{}")
            result = change(notes)
            db.execute(
                "UPDATE recorded_sessions SET engineering_notes_json=? WHERE id=?",
                (json.dumps(notes, ensure_ascii=False), session_id),
            )
        return result

    async def save_lap_note(
        self,
        session_id: str,
        lap_ids: list[str],
        text: str,
        category: str = "other",
        exclude_from_pace: bool = False,
        source: str = "driver",
        note_id: str | None = None,
        pending_laps: list[dict] | None = None,
    ) -> dict:
        if category not in {
            "traffic",
            "mistake",
            "balance",
            "conditions",
            "mechanical",
            "other",
        }:
            raise ValueError("Choose a supported note category.")
        if source not in {"driver", "engineer"}:
            raise ValueError("A note must identify its source.")
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("A lap note needs 1–2000 characters.")
        if not isinstance(exclude_from_pace, bool):
            raise TypeError("Exclusion must be a boolean.")
        pending_laps = pending_laps or []
        if (
            len(lap_ids) > 5000
            or len(set(lap_ids)) != len(lap_ids)
            or len(pending_laps) > 100
        ):
            raise ValueError("Choose unique laps within the selected session.")
        for target in pending_laps:
            if (
                set(target) != {"lap_num", "timeline_epoch"}
                or any(type(target[k]) is not int for k in target)
                or target["lap_num"] < 1
                or target["timeline_epoch"] < 0
            ):
                raise ValueError("Pending notes need an exact lap number and timeline.")
        if not lap_ids and not pending_laps:
            raise ValueError("Choose at least one lap for this note.")
        async with self.database._lock:
            report = await self.report(session_id)
            if not set(lap_ids).issubset({lap["id"] for lap in report["laps"]}):
                raise ValueError("Every selected lap must belong to this session.")
            now = datetime.now(UTC).isoformat()
            item = {
                "id": note_id or f"note-{uuid.uuid4().hex}",
                "lap_ids": list(lap_ids),
                "text": text.strip(),
                "category": category,
                "exclude_from_pace": exclude_from_pace,
                "source": source,
                "created_at": now,
                "updated_at": now,
                "pending_laps": pending_laps,
            }

            def save(notes):
                items = notes.setdefault("lap_notes", [])
                existing = next(
                    (entry for entry in items if entry["id"] == note_id), None
                )
                if note_id and existing is None:
                    raise KeyError(note_id)
                if existing is not None:
                    item["created_at"] = existing["created_at"]
                    items[items.index(existing)] = item
                else:
                    if len(items) >= 1000:
                        raise ValueError(
                            "This session already has 1000 notes; edit an existing note."
                        )
                    items.append(item)
                return item

            return await asyncio.to_thread(self._update_metadata, session_id, save)

    async def delete_lap_note(self, session_id: str, note_id: str) -> None:
        def delete(notes):
            items = notes.get("lap_notes", [])
            filtered = [item for item in items if item["id"] != note_id]
            if len(items) == len(filtered):
                raise KeyError(note_id)
            notes["lap_notes"] = filtered

        async with self.database._lock:
            await asyncio.to_thread(self._update_metadata, session_id, delete)


def report_text(report: dict) -> str:
    def metric(value) -> str:
        # Format missing measurements at their source. Replacing "None" in
        # the finished report would also rewrite driver-authored notes/names.
        if value is None:
            return "Unavailable"
        if isinstance(value, (list, tuple)):
            return "[" + ", ".join(metric(item) for item in value) + "]"
        return str(value)

    lines = [
        f"Your Pit Box session report — {report['track_name']}",
        f"{report['session_type']} | {report['started_at']} | {report['status']}",
        f"Objective: {report['notes'].get('objective', '')}",
        "",
    ]
    for run in report["runs"]:
        summary = run["summary"]
        lines += [
            f"Run {run['number']} | {run['compound']} | laps {run['lap_range'][0]}–{run['lap_range'][1]}",
            f"Clean laps: {summary['clean_lap_count']} / {summary['lap_count']}",
            f"Median pace: {metric(summary['median_pace_s'])} s; consistency SD: {metric(summary['consistency_stdev_s'])} s",
            f"Observed pace trend: {metric(summary['observed_pace_trend_s_per_lap'])} s/lap (fuel and conditions included)",
            f"Tyre inner temperatures: {metric(summary['tyre_inner_temp_range_c'])} C",
            f"Setup: {json.dumps(run['setup'], sort_keys=True)}",
            f"Changes: {json.dumps(run['setup_changes'], sort_keys=True)}",
            f"Handling: {json.dumps(summary['balance'])}",
            f"Measured conditions: {json.dumps(run.get('conditions', {}), ensure_ascii=False)}",
            f"Takeaway: {summary['takeaway']}",
            f"Next test: {summary['next_test']}",
            f"Run objective: {run['notes'].get('objective', '')}",
            f"Run conclusion: {run['notes'].get('conclusion', '')}",
            "",
        ]
    if report.get("groups"):
        lines += ["Custom lap groups:"]
        for group in report["groups"]:
            lines += [
                f"{group['name']} | laps "
                + ", ".join(
                    f"{lap['lap_num']} (timeline {lap.get('timeline_epoch', 0)})"
                    for lap in group["laps"]
                ),
                f"Clean laps: {group['summary']['clean_lap_count']} / {group['summary']['lap_count']}; median pace: {metric(group['summary']['median_pace_s'])} s",
                f"Measured conditions: {json.dumps(group.get('conditions', {}), ensure_ascii=False)}",
                f"Group objective: {group.get('notes', {}).get('objective', '')}",
                f"Group conclusion: {group.get('notes', {}).get('conclusion', '')}",
            ]
    lines += ["", "Lap context notes (reported, not measured):"]
    by_id = {lap["id"]: lap for lap in report.get("laps", [])}
    for note in report.get("lap_notes", []):
        targets = [
            f"lap {by_id[key]['lap_num']} (timeline {by_id[key]['timeline_epoch']})"
            for key in note["lap_ids"]
            if key in by_id
        ]
        if not targets:
            targets = [
                f"pending lap {item['lap_num']} (timeline {item['timeline_epoch']})"
                for item in note.get("pending_laps", [])
            ]
        lines.append(
            f"{', '.join(targets)} | {note['source']}-reported {note['category']} | {'excluded from pace' if note['exclude_from_pace'] else 'context only'}: {note['text']}"
        )
    lines += [
        "Incidents and exclusions:",
        *[
            f"Lap {item['lap']}: {', '.join(item['items'])}"
            for item in report["incidents"]
        ],
        "",
        f"Session conclusion: {report['notes'].get('conclusion', '')}",
        "",
        *report["limitations"],
    ]
    return "\n".join(lines) + "\n"
