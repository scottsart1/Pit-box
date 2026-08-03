from __future__ import annotations

import asyncio
import copy
import math
from statistics import median, pstdev
from typing import Any

from .database import PitWallDatabase
from .state import StateStore
from .racing_line import compare_lines
from .strategy import StrategyEngine


def fmt_ms(ms: int | float | None) -> str | None:
    if not ms:
        return None
    value = int(ms)
    minutes, remainder = divmod(value, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{minutes}:{seconds:02d}.{milliseconds:03d}"


def theil_sen(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Return robust slope/intercept without a SciPy dependency."""
    if len(points) < 3:
        return None
    slopes: list[float] = []
    for index, (x1, y1) in enumerate(points):
        for x2, y2 in points[index + 1 :]:
            if x2 != x1:
                slopes.append((y2 - y1) / (x2 - x1))
    if not slopes:
        return None
    slope = float(median(slopes))
    intercept = float(median([y - slope * x for x, y in points]))
    return slope, intercept


class AnalysisEngine:
    def __init__(
        self,
        store: StateStore,
        database: PitWallDatabase,
        strategy: StrategyEngine,
    ) -> None:
        self.store = store
        self.database = database
        self.strategy = strategy
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopped.clear()
            self._task = asyncio.create_task(self._worker(), name="pitwall-analysis")

    async def stop(self) -> None:
        self._stopped.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _worker(self) -> None:
        while not self._stopped.is_set():
            lap = await self.store.lap_queue.get()
            try:
                await self.process_lap(lap)
            except Exception as exc:
                await self.store.update(last_error=f"Lap analysis failed: {exc}")
            finally:
                self.store.lap_queue.task_done()

    async def process_lap(self, lap: dict[str, Any]) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        pb = await self.database.get_personal_best(int(lap["track_id"]))
        # Keep the live delta reference pointed at the current personal best so
        # the next lap is compared against it in real time.
        if pb and pb.get("trace"):
            await self.store.set_delta_reference(
                pb["trace"], f"PB {fmt_ms(int(pb.get('lap_time_ms', 0)))}"
            )
        corners = self.segment_corners(lap.get("trace", []), pb)
        racing_line = compare_lines(
            lap.get("trace", []),
            pb.get("trace", []) if pb else None,
        )
        lap_summary = self.build_lap_summary(
            lap,
            corners,
            pb,
            self.sector_fields(state, int(lap["lap_num"])),
        )
        lap_summary["racing_line"] = {
            key: value
            for key, value in racing_line.items()
            if key not in {"current_line", "reference_line", "deviation_samples"}
        }
        timing_fields = lap_summary.get("timing_fields", {})
        lap.update(timing_fields)
        await self.database.save_lap(lap, corners)
        # Laps saved before their session-history packet arrived carry zero
        # sectors; fill them once the game has reported the split.
        await self.database.backfill_lap_sectors(
            int(lap["session_uid"]),
            state.get("completed_laps", []),
        )
        await self.database.save_line_metrics(
            int(lap["session_uid"]),
            int(lap["track_id"]),
            int(lap["lap_num"]),
            {
                key: value
                for key, value in racing_line.items()
                if key not in {"current_line", "reference_line"}
            },
        )
        await self.store.update_completed_lap_summary(
            int(lap["lap_num"]),
            {
                **timing_fields,
                "corner_metrics": corners,
                "line_summary": {
                    key: value
                    for key, value in racing_line.items()
                    if key not in {"current_line", "reference_line", "deviation_samples"}
                },
            },
        )

        state = await self.store.snapshot_analysis()
        deg = self.compute_degradation(state)
        fuel = self.compute_fuel_model(state)
        target = self.compute_target(state)
        history = list(state.get("analysis", {}).get("corner_history", []))
        history.append({"lap_num": lap["lap_num"], "corners": corners})
        history = history[-5:]
        line_history = list(state.get("analysis", {}).get("line_history", []))
        line_history.append(
            {
                "lap_num": int(lap["lap_num"]),
                "mean_abs_deviation_m": racing_line.get("mean_abs_deviation_m"),
                "p95_deviation_m": racing_line.get("p95_deviation_m"),
                "line_score": racing_line.get("line_score"),
                "top_opportunity": racing_line.get("top_opportunity"),
            }
        )
        line_history = line_history[-10:]
        flagged = self.flag_corners(history)
        progress = self.build_progress(
            state, lap, lap_summary, target, deg, fuel, flagged, racing_line
        )

        analysis = copy.deepcopy(state.get("analysis", {}))
        analysis.update(
            {
                "last_lap_analyzed": int(lap["lap_num"]),
                "lap_summary": lap_summary,
                "corner_metrics": corners,
                "corner_history": history,
                "flagged_corners": flagged,
                "racing_line": racing_line,
                "line_history": line_history,
                "deg_model": deg,
                "fuel_model": fuel,
                "target": target,
                "progress": progress,
            }
        )
        await self.store.update(analysis=analysis)
        await self.strategy.recompute()
        return analysis

    @staticmethod
    def _clean_trace(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
        points = [
            point
            for point in trace
            if point.get("d") is not None and point.get("t") is not None
        ]
        points.sort(key=lambda item: (float(item["d"]), float(item["t"])))
        cleaned: list[dict[str, Any]] = []
        last_distance = -1.0
        for point in points:
            distance = float(point["d"])
            if distance + 5 < last_distance:
                continue
            cleaned.append(point)
            last_distance = max(last_distance, distance)
        return cleaned

    def segment_corners(
        self,
        trace: list[dict[str, Any]],
        personal_best: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        points = self._clean_trace(trace)
        if len(points) < 20:
            return []

        candidates: list[tuple[int, int]] = []
        in_zone = False
        start = 0
        quiet = 0
        for index, point in enumerate(points):
            braking = (
                float(point.get("brake", 0.0)) >= 0.12
                and float(point.get("speed", 0)) >= 70
            )
            high_g = (
                abs(float(point.get("lat_g", 0.0))) >= 1.65
                and float(point.get("speed", 0)) >= 120
            )
            active = braking or high_g
            if active and not in_zone:
                in_zone = True
                start = max(0, index - 3)
                quiet = 0
            elif in_zone:
                if active or float(point.get("throttle", 0.0)) < 0.92:
                    quiet = 0
                else:
                    quiet += 1
                if quiet >= 5 or index == len(points) - 1:
                    end = min(len(points) - 1, index + 3)
                    if float(points[end]["d"]) - float(points[start]["d"]) >= 25:
                        candidates.append((start, end))
                    in_zone = False

        merged: list[tuple[int, int]] = []
        for start, end in candidates:
            if (
                merged
                and float(points[start]["d"]) - float(points[merged[-1][1]]["d"]) < 80
            ):
                merged[-1] = (merged[-1][0], end)
            else:
                merged.append((start, end))

        pb_corners = personal_best.get("corner_metrics", []) if personal_best else []
        metrics: list[dict[str, Any]] = []
        for corner_no, (start_index, end_index) in enumerate(merged, 1):
            window = points[start_index : end_index + 1]
            if not window:
                continue
            apex_point = min(window, key=lambda point: float(point.get("speed", 9999)))
            apex_index = window.index(apex_point)
            braking_points = [
                point for point in window if float(point.get("brake", 0)) >= 0.1
            ]
            throttle_points = window[apex_index:]
            throttle_on = next(
                (
                    point
                    for point in throttle_points
                    if float(point.get("throttle", 0)) >= 0.25
                ),
                window[-1],
            )
            full_throttle = next(
                (
                    point
                    for point in throttle_points
                    if float(point.get("throttle", 0)) >= 0.95
                ),
                window[-1],
            )
            brake_point = braking_points[0] if braking_points else window[0]
            entry = max(0.0, float(brake_point["d"]) - 20.0)
            exit_distance = float(full_throttle["d"])
            apex_distance = float(apex_point["d"])
            time_in_corner = max(0.0, float(window[-1]["t"]) - float(window[0]["t"]))
            wheel_lock = any(
                float(point.get("brake", 0)) > 0.2
                and any(float(value) < -0.15 for value in point.get("slip", []))
                for point in window
            )
            wheelspin = any(
                float(point.get("throttle", 0)) > 0.6
                and any(float(value) > 0.15 for value in point.get("slip", []))
                for point in window
            )
            name = f"Corner {corner_no} @ {apex_distance:.0f} m"
            metric: dict[str, Any] = {
                "corner_no": corner_no,
                "name": name,
                "entry_m": round(entry, 1),
                "apex_m": round(apex_distance, 1),
                "exit_m": round(exit_distance, 1),
                "brake_point_m": round(float(brake_point["d"]), 1),
                "brake_peak": round(
                    max(float(point.get("brake", 0)) for point in window), 3
                ),
                "min_speed_kph": round(float(apex_point.get("speed", 0)), 1),
                "apex_speed_kph": round(float(apex_point.get("speed", 0)), 1),
                "throttle_on_m": round(float(throttle_on["d"]), 1),
                "full_throttle_m": round(float(full_throttle["d"]), 1),
                "gear_at_apex": int(apex_point.get("gear", 0)),
                "max_lat_g": round(
                    max(abs(float(point.get("lat_g", 0))) for point in window), 2
                ),
                "wheel_lock": wheel_lock,
                "wheelspin": wheelspin,
                "time_in_corner_s": round(time_in_corner, 3),
                "loss_vs_pb_s": None,
                "cause": "",
            }
            pb_metric = self._match_pb_corner(metric, pb_corners)
            if pb_metric:
                loss = time_in_corner - float(
                    pb_metric.get("time_in_corner_s") or time_in_corner
                )
                metric["loss_vs_pb_s"] = round(loss, 3)
                deltas = self._reference_deltas(metric, pb_metric)
                metric.update(deltas)
                metric["cause"] = self._cause(metric, pb_metric, deltas)
                if pb_metric.get("name"):
                    metric["reference_name"] = pb_metric["name"]
            elif wheel_lock:
                metric["cause"] = "lock-up"
            elif wheelspin:
                metric["cause"] = "wheelspin"
            metrics.append(metric)
        return metrics

    @staticmethod
    def _match_pb_corner(
        metric: dict[str, Any],
        pb_corners: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not pb_corners:
            return None
        closest = min(
            pb_corners,
            key=lambda item: abs(
                float(item.get("apex_m", 0)) - float(metric["apex_m"])
            ),
        )
        return (
            closest
            if abs(float(closest.get("apex_m", 0)) - float(metric["apex_m"])) <= 180
            else None
        )

    @staticmethod
    def _reference_deltas(
        metric: dict[str, Any],
        reference: dict[str, Any],
    ) -> dict[str, float]:
        """Signed differences from the personal-best corner.

        Positive brake delta means the brake point was later than the
        reference; negative apex-speed delta means slower through the apex.
        These are kept on the metric so coaching can quote the actual numbers
        instead of only naming a cause.
        """
        return {
            "brake_point_delta_m": round(
                float(metric.get("brake_point_m", 0))
                - float(reference.get("brake_point_m", 0) or 0),
                1,
            ),
            "apex_speed_delta_kph": round(
                float(metric.get("min_speed_kph", 0))
                - float(reference.get("min_speed_kph", 0) or 0),
                1,
            ),
            "throttle_on_delta_m": round(
                float(metric.get("throttle_on_m", 0))
                - float(reference.get("throttle_on_m", 0) or 0),
                1,
            ),
        }

    @staticmethod
    def _cause(
        metric: dict[str, Any],
        reference: dict[str, Any],
        deltas: dict[str, float] | None = None,
    ) -> str:
        if metric.get("wheel_lock"):
            return "lock-up"
        if metric.get("wheelspin"):
            return "wheelspin"
        resolved = deltas or AnalysisEngine._reference_deltas(metric, reference)
        brake_delta = resolved["brake_point_delta_m"]
        speed_delta = resolved["apex_speed_delta_kph"]
        throttle_delta = resolved["throttle_on_delta_m"]
        if brake_delta < -8:
            return "early brake"
        if brake_delta > 12 and speed_delta < -5:
            return "late brake / overslow"
        if speed_delta < -5:
            return "low apex speed"
        if throttle_delta > 10:
            return "late throttle"
        return "line or minimum-speed loss"

    @staticmethod
    def sector_fields(state: dict[str, Any], lap_num: int) -> dict[str, int]:
        """Sector times for a completed lap, when the game has reported them.

        Sectors arrive in the session-history packet rather than at the lap
        transition, so they are frequently not yet known when the lap is first
        analysed. Only a complete set is returned; partial data would persist a
        misleading zero for the missing sector.
        """
        for lap in reversed(state.get("completed_laps", [])):
            if int(lap.get("lap_num", -1)) != int(lap_num):
                continue
            sectors = {
                key: int(lap.get(key, 0) or 0) for key in ("s1_ms", "s2_ms", "s3_ms")
            }
            return sectors if all(sectors.values()) else {}
        return {}

    def build_lap_summary(
        self,
        lap: dict[str, Any],
        corners: list[dict[str, Any]],
        pb: dict[str, Any] | None,
        sectors: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        losses = [corner for corner in corners if (corner.get("loss_vs_pb_s") or 0) > 0]
        top = sorted(
            losses, key=lambda item: item.get("loss_vs_pb_s") or 0, reverse=True
        )[:3]
        pb_time = int(pb.get("lap_time_ms", 0)) if pb else 0
        lap_time = int(lap.get("lap_time_ms", 0))
        return {
            "lap_num": int(lap["lap_num"]),
            "lap_time_ms": lap_time,
            "lap_time": fmt_ms(lap_time),
            "valid": bool(lap.get("valid")),
            "personal_best_ms": pb_time,
            "personal_best": fmt_ms(pb_time),
            "delta_to_pb_s": round((lap_time - pb_time) / 1000, 3)
            if lap_time and pb_time
            else None,
            "top_corner_losses": top,
            "corner_count": len(corners),
            "timing_fields": dict(sectors or {}),
        }

    @staticmethod
    def compute_degradation(state: dict[str, Any]) -> dict[str, Any]:
        """Fuel-corrected personal degradation and wear estimates.

        Later laps are lighter on fuel, which can hide tyre degradation. The
        fit normalizes every lap back toward the heaviest fuel state in the
        current sample before applying the robust Theil-Sen slope.
        """
        grouped: dict[str, list[dict[str, Any]]] = {}
        for lap in state.get("completed_laps", []):
            if (
                not lap.get("valid")
                or not lap.get("lap_time_ms")
                or lap.get("pit_status")
            ):
                continue
            compound = str(lap.get("compound", "UNKNOWN")).upper()
            grouped.setdefault(compound, []).append(lap)

        result: dict[str, Any] = {"compounds": {}}
        for compound, laps in grouped.items():
            max_fuel = max(
                (
                    (
                        float(lap.get("fuel_start_kg", 0))
                        + float(lap.get("fuel_end_kg", 0))
                    )
                    / 2
                    for lap in laps
                ),
                default=0.0,
            )
            points: list[tuple[float, float]] = []
            wear_rates: list[float] = []
            max_wear_rates: list[float] = []
            for lap in laps:
                fuel_avg = (
                    float(lap.get("fuel_start_kg", 0))
                    + float(lap.get("fuel_end_kg", 0))
                ) / 2
                normalized_time = (
                    float(lap["lap_time_ms"]) / 1000
                    + max(0.0, max_fuel - fuel_avg) * 0.030
                )
                age = float(lap.get("tyre_age_end", 0))
                if age > 0:
                    points.append((age, normalized_time))
                start_wear = lap.get("wear_start", []) or []
                end_wear = lap.get("wear_end", []) or []
                if len(start_wear) == 4 and len(end_wear) == 4:
                    deltas = [
                        max(0.0, float(end) - float(start))
                        for start, end in zip(start_wear, end_wear)
                    ]
                    wear_rates.append(sum(deltas) / 4)
                    max_wear_rates.append(max(deltas))

            fit = theil_sen(points)
            model: dict[str, Any] = {
                "sample_size": len(points),
                "wear_sample_size": len(wear_rates),
                "wear_per_lap_pct": round(float(median(wear_rates)), 3)
                if wear_rates
                else None,
                "max_wear_per_lap_pct": round(float(median(max_wear_rates)), 3)
                if max_wear_rates
                else None,
                "source": "live_fuel_corrected_fit",
            }
            if fit:
                slope, intercept = fit
                model.update(
                    {
                        "slope_s_per_lap": round(max(-0.1, min(1.5, slope)), 3),
                        "intercept_s": round(intercept, 3),
                    }
                )
            result["compounds"][compound] = model

        current = str(state.get("tyre", {}).get("compound", "UNKNOWN")).upper()
        current_fit = result["compounds"].get(current, {})
        wear = max(state.get("tyre", {}).get("wear", [0]) or [0])
        slope = float(current_fit.get("slope_s_per_lap", 0.0) or 0.0)
        wear_per_lap = current_fit.get("max_wear_per_lap_pct")
        if wear_per_lap is None:
            age = max(1, int(state.get("tyre", {}).get("age_laps", 0)))
            wear_per_lap = wear / age if age >= 2 and wear > 0 else 3.0
        cliff_lap = None
        if wear > 0 and float(wear_per_lap) > 0:
            cliff_lap = int(
                state.get("current_lap", 0)
                + max(0, math.floor((72 - wear) / float(wear_per_lap)))
            )
        result.update(
            {
                "current_compound": current,
                "current_slope_s_per_lap": slope,
                "current_wear_per_lap_pct": round(float(wear_per_lap), 3),
                "projected_cliff_lap": cliff_lap,
            }
        )
        return result

    @staticmethod
    def compute_fuel_model(state: dict[str, Any]) -> dict[str, Any]:
        burns: list[float] = []
        for lap in state.get("completed_laps", [])[-8:]:
            start = float(lap.get("fuel_start_kg", 0))
            end = float(lap.get("fuel_end_kg", 0))
            if start > end > 0:
                burns.append(start - end)
        burn = float(median(burns)) if burns else 0.0
        delta = float(state.get("fuel_laps_delta", 0.0))
        lift_m = 0
        if delta < 0 and burn > 0:
            lift_m = int(min(250, max(20, abs(delta) * 35)))
        return {
            "burn_kg_per_lap": round(burn, 3),
            "fuel_laps_delta": round(delta, 2),
            "lift_and_coast_m_per_lap": lift_m,
            "recommendation": "save fuel"
            if delta < -0.1
            else "on target"
            if delta < 0.5
            else "fuel positive",
        }

    @staticmethod
    def compute_target(state: dict[str, Any]) -> dict[str, Any]:
        valid = [
            lap
            for lap in state.get("completed_laps", [])
            if lap.get("valid") and lap.get("lap_time_ms", 0) > 0
        ]
        player_best = min((lap["lap_time_ms"] for lap in valid), default=0)
        field_best = min(
            (
                int(driver.get("best_lap_ms", 0))
                for driver in state.get("drivers", [])
                if int(driver.get("best_lap_ms", 0)) > 0
            ),
            default=0,
        )
        session_best = field_best or player_best
        player_idx = int(state.get("player_car_index", 0))
        player = next(
            (
                driver
                for driver in state.get("drivers", [])
                if driver.get("car_idx") == player_idx
            ),
            None,
        )
        history = player.get("lap_history", []) if player else []
        best_s1 = min(
            (lap.get("s1_ms", 0) for lap in history if lap.get("s1_ms", 0) > 0),
            default=0,
        )
        best_s2 = min(
            (lap.get("s2_ms", 0) for lap in history if lap.get("s2_ms", 0) > 0),
            default=0,
        )
        best_s3 = min(
            (lap.get("s3_ms", 0) for lap in history if lap.get("s3_ms", 0) > 0),
            default=0,
        )
        theoretical = (
            best_s1 + best_s2 + best_s3 if best_s1 and best_s2 and best_s3 else 0
        )
        mode = state.get("mode_profile", "race")
        if mode in {"qualifying", "practice", "time_trial"}:
            target_ms = (
                min(value for value in (session_best, theoretical) if value > 0)
                if session_best or theoretical
                else 0
            )
            basis = "session best / theoretical best"
        else:
            recent = [lap["lap_time_ms"] for lap in valid[-4:]]
            target_ms = int(median(recent)) if recent else player_best
            basis = "sustainable race pace"
            player_position = int(state.get("player_position", 0) or 0)
            # There is no car ahead of the leader. Retired and garaged cars
            # report position 0, so without the guard a race leader would be
            # given a target taken from a car sitting in the garage.
            ahead = (
                next(
                    (
                        driver
                        for driver in state.get("drivers", [])
                        if int(driver.get("position", 0) or 0)
                        == player_position - 1
                    ),
                    None,
                )
                if player_position > 1
                else None
            )
            if ahead and ahead.get("last_lap_ms") and target_ms:
                target_ms = min(target_ms, int(ahead["last_lap_ms"]) - 100)
                basis = "pace required to pressure the car ahead"
        return {
            "target_ms": target_ms,
            "target": fmt_ms(target_ms),
            "session_best_ms": session_best,
            "session_best": fmt_ms(session_best),
            "field_best_ms": field_best,
            "field_best": fmt_ms(field_best),
            "player_best_ms": player_best,
            "player_best": fmt_ms(player_best),
            "theoretical_ms": theoretical,
            "theoretical": fmt_ms(theoretical),
            "basis": basis,
        }

    @staticmethod
    def flag_corners(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[int, list[dict[str, Any]]] = {}
        for lap in history[-3:]:
            for corner in lap.get("corners", []):
                grouped.setdefault(int(corner["corner_no"]), []).append(corner)
        flagged = []
        for corner_no, samples in grouped.items():
            losses = [float(sample.get("loss_vs_pb_s") or 0) for sample in samples]
            repeated = sum(loss > 0.08 for loss in losses) >= 2
            if repeated:
                latest = samples[-1]
                flagged.append(
                    {
                        "corner_no": corner_no,
                        "name": latest["name"],
                        "average_loss_s": round(sum(losses) / len(losses), 3),
                        "cause": latest.get("cause", ""),
                        "brake_point_delta_m": latest.get("brake_point_delta_m"),
                        "apex_speed_delta_kph": latest.get("apex_speed_delta_kph"),
                        "throttle_on_delta_m": latest.get("throttle_on_delta_m"),
                        "instruction": AnalysisEngine.corner_instruction(latest),
                    }
                )
        return sorted(flagged, key=lambda item: item["average_loss_s"], reverse=True)

    @staticmethod
    def _quantity(value: Any, unit: str, minimum: float = 1.0) -> str:
        """Render a delta for radio use, or an empty string when it is noise."""
        if value is None:
            return ""
        magnitude = abs(float(value))
        if magnitude < minimum:
            return ""
        return f"{magnitude:.0f} {unit}"

    @staticmethod
    def corner_instruction(corner: dict[str, Any]) -> str:
        cause = corner.get("cause", "")
        name = corner.get("name", f"Corner {corner.get('corner_no', '?')}")
        brake = AnalysisEngine._quantity(
            corner.get("brake_point_delta_m"), "metres", 2.0
        )
        apex = AnalysisEngine._quantity(
            corner.get("apex_speed_delta_kph"), "km/h", 2.0
        )
        throttle = AnalysisEngine._quantity(
            corner.get("throttle_on_delta_m"), "metres", 2.0
        )
        if cause == "early brake":
            if brake:
                return f"At {name}, brake {brake} later while protecting apex speed."
            return f"At {name}, release the brake point a little later while keeping the same apex speed."
        if cause == "late brake / overslow":
            if brake and apex:
                return (
                    f"At {name}, brake {brake} earlier — you are {apex} down at the apex."
                )
            if brake:
                return f"At {name}, brake {brake} earlier and carry the release more smoothly."
            return (
                f"At {name}, brake a touch earlier and carry the release more smoothly."
            )
        if cause == "low apex speed":
            if apex:
                return f"At {name}, open the entry and protect minimum speed; you are {apex} down at the apex."
            return f"At {name}, open the entry and protect minimum speed."
        if cause == "late throttle":
            if throttle:
                return f"At {name}, finish rotation earlier and pick up throttle {throttle} sooner."
            return f"At {name}, finish rotation earlier and pick up throttle sooner."
        if cause == "lock-up":
            return f"At {name}, reduce peak brake pressure and trail off more progressively."
        if cause == "wheelspin":
            return f"At {name}, straighten the exit and feed throttle in more progressively."
        return f"At {name}, focus on a cleaner line and stable minimum speed."

    @staticmethod
    def build_progress(
        state: dict[str, Any],
        lap: dict[str, Any],
        summary: dict[str, Any],
        target: dict[str, Any],
        deg: dict[str, Any],
        fuel: dict[str, Any],
        flagged: list[dict[str, Any]],
        racing_line: dict[str, Any],
    ) -> dict[str, Any]:
        target_ms = int(target.get("target_ms", 0) or 0)
        lap_ms = int(lap.get("lap_time_ms", 0) or 0)
        top = flagged[0] if flagged else None
        return {
            "lap_num": int(lap["lap_num"]),
            "position": int(state.get("player_position", 0)),
            "lap_time": fmt_ms(lap_ms),
            "delta_to_target_s": round((lap_ms - target_ms) / 1000, 3)
            if lap_ms and target_ms
            else None,
            "target": target.get("target"),
            "tyre_compound": state.get("tyre", {}).get("compound"),
            "tyre_age": state.get("tyre", {}).get("age_laps"),
            "max_wear_pct": round(
                max(state.get("tyre", {}).get("wear", [0]) or [0]), 1
            ),
            "deg_s_per_lap": deg.get("current_slope_s_per_lap", 0.0),
            "fuel_delta_laps": fuel.get("fuel_laps_delta", 0.0),
            "top_opportunity": top,
            "line_score": racing_line.get("line_score"),
            "line_deviation_m": racing_line.get("mean_abs_deviation_m"),
            "line_opportunity": racing_line.get("top_opportunity"),
            "summary": summary,
        }

    async def get_lap_analysis(
        self,
        lap: str = "last",
        compare_to: str = "personal_best",
    ) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        analysis = state.get("analysis", {})
        return {
            "lap": lap,
            "compare_to": compare_to,
            "summary": analysis.get("lap_summary", {}),
            "top_corners": analysis.get("lap_summary", {}).get("top_corner_losses", []),
            "flagged_corners": analysis.get("flagged_corners", []),
        }

    async def get_corner_analysis(
        self,
        corner: str,
        last_n_laps: int = 3,
    ) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        history = state.get("analysis", {}).get("corner_history", [])[-last_n_laps:]
        query = corner.strip().lower()
        number = None
        digits = "".join(character for character in query if character.isdigit())
        if digits:
            number = int(digits)
        samples = []
        for lap in history:
            for metric in lap.get("corners", []):
                if (
                    number == metric.get("corner_no")
                    or query in metric.get("name", "").lower()
                ):
                    samples.append({"lap_num": lap.get("lap_num"), **metric})
        if not samples:
            return {
                "available": False,
                "reason": "Corner not found in the learned lap map.",
            }
        latest = samples[-1]
        return {
            "available": True,
            "corner": latest.get("name"),
            "samples": samples,
            "instruction": self.corner_instruction(latest),
        }

    async def get_racing_line_analysis(
        self,
        lap: str = "last",
    ) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        line = state.get("analysis", {}).get("racing_line", {})
        if not line:
            return {
                "available": False,
                "reason": "No completed lap has been mapped yet.",
            }
        return {
            "lap": lap,
            "available": bool(line.get("available")),
            "reference": line.get("reference"),
            "mean_abs_deviation_m": line.get("mean_abs_deviation_m"),
            "p95_deviation_m": line.get("p95_deviation_m"),
            "max_deviation_m": line.get("max_deviation_m"),
            "line_score": line.get("line_score"),
            "top_opportunity": line.get("top_opportunity"),
            "zones": line.get("zones", [])[:6],
            "reason": line.get("reason"),
        }

    async def get_consistency(self) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        laps = [
            lap["lap_time_ms"] / 1000
            for lap in state.get("completed_laps", [])
            if lap.get("valid") and lap.get("lap_time_ms", 0) > 0
        ]
        flagged = state.get("analysis", {}).get("flagged_corners", [])
        return {
            "sample_size": len(laps),
            "lap_time_stddev_s": round(pstdev(laps), 3) if len(laps) >= 2 else None,
            "most_repeated_opportunities": flagged[:3],
        }
