"""Deterministic run reviews, matched setup tests and strategic race context."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
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
        if str(lap.get("compound", "UNKNOWN")).upper() == "UNKNOWN":
            reasons.append("unknown_compound")
        if str(lap.get("weather", "Unknown")).lower() in {"unknown", "", "none"}:
            reasons.append("unknown_weather")
        for field in ("tyre_age_start", "fuel_start_kg", "track_temp_c", "air_temp_c"):
            if number(lap.get(field)) is None:
                reasons.append(f"missing_{field}")
        if (number(lap.get("fuel_start_kg")) or 0) <= 0:
            reasons.append("unknown_fuel")
    return sorted(set(reasons))


def run_summary(laps: list[dict]) -> dict:
    clean = [lap for lap in laps if not exclusions(lap)]
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
        {"lap": lap.get("lap_num"), "reasons": exclusions(lap) or ["pace_outlier"]}
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


def compare_runs(a: dict, b: dict) -> dict:
    """One-to-one nearest-condition matching. Positive deltas mean B is slower."""
    rejected: dict[str, list] = {"a": [], "b": []}
    eligible = {}
    for side, run in (("a", a), ("b", b)):
        clean_ids = set(run["summary"]["clean_lap_ids"])
        eligible[side] = []
        for lap in run["laps"]:
            reasons = exclusions(lap, strict=True)
            if lap.get("id") not in clean_ids and not reasons:
                reasons.append("pace_outlier")
            if reasons:
                rejected[side].append({"lap": lap["lap_num"], "reasons": reasons})
            else:
                eligible[side].append(lap)
    candidates = []
    for left in eligible["a"]:
        for right in eligible["b"]:
            if (
                left["compound"] != right["compound"]
                or left["weather"] != right["weather"]
            ):
                continue
            differences = [
                abs(left[key] - right[key]) / limit
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
        sectors = [
            (right.get(key, 0) - left.get(key, 0)) / 1000
            if left.get(key, 0) > 0 and right.get(key, 0) > 0
            else None
            for key in ("s1_ms", "s2_ms", "s3_ms")
        ]
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
            }
        )
    for side, used in (("a", used_a), ("b", used_b)):
        rejected[side].extend(
            {"lap": lap["lap_num"], "reasons": ["no_unused_condition_match"]}
            for lap in eligible[side]
            if str(lap["id"]) not in used
        )
    delta = round(median(pair["delta_s"] for pair in pairs), 3) if pairs else None
    enough = len(pairs) >= MATCH_LIMITS["minimum_pairs"]
    same_setup = a["setup_id"] == b["setup_id"]
    sectors = [
        round(median(pair["sector_deltas_s"][i] for pair in pairs), 3)
        if pairs and all(pair["sector_deltas_s"][i] is not None for pair in pairs)
        else None
        for i in range(3)
    ]
    return {
        "a_run": a["number"],
        "b_run": b["number"],
        "pairs": pairs,
        "excluded": rejected,
        "criteria": MATCH_LIMITS,
        "enough_evidence": enough,
        "same_setup": same_setup,
        "median_delta_s": delta,
        "sector_deltas_s": sectors,
        "delta_definition": "B minus A; negative means B was quicker.",
        "conclusion": (
            f"B was {'quicker' if delta < 0 else 'slower' if delta > 0 else 'equal'} by {abs(delta):.3f} s on the median matched lap."
            if enough
            else f"Only {len(pairs)} matched pairs; collect at least three before judging the test."
        ),
        "caveats": [
            "Observational comparison; matched conditions do not prove setup caused the difference."
        ]
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

    def _report(self, session_id: str) -> dict:
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
        runs = build_runs(laps)
        for run in runs:
            run["notes"] = notes.get("runs", {}).get(run["id"], {})
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
            "track_name": track,
            "session_type": session["session_type"],
            "status": session["status"],
            "started_at": session["started_at"],
            "notes": notes,
            "runs": runs,
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
            run["id"] == run_id for run in report["runs"]
        ):
            raise KeyError(run_id)
        with self.database._connect() as db:
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


def report_text(report: dict) -> str:
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
            f"Median pace: {summary['median_pace_s']} s; consistency SD: {summary['consistency_stdev_s']} s",
            f"Observed pace trend: {summary['observed_pace_trend_s_per_lap']} s/lap (fuel and conditions included)",
            f"Tyre inner temperatures: {summary['tyre_inner_temp_range_c']} C",
            f"Setup: {json.dumps(run['setup'], sort_keys=True)}",
            f"Changes: {json.dumps(run['setup_changes'], sort_keys=True)}",
            f"Handling: {json.dumps(summary['balance'])}",
            f"Takeaway: {summary['takeaway']}",
            f"Next test: {summary['next_test']}",
            f"Run objective: {run['notes'].get('objective', '')}",
            f"Run conclusion: {run['notes'].get('conclusion', '')}",
            "",
        ]
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
    return "\n".join(lines).replace("None", "Unavailable") + "\n"
