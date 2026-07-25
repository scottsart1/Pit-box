from __future__ import annotations

import math
import re
from statistics import median
from typing import Any

import numpy as np

from .config import settings
from .database import PitWallDatabase
from .setup_model import setup_effects
from .state import StateStore


# Green-flag drive-through loss estimates. IDs follow f1-packets 2026 TRACKS.
PIT_LOSS_SECONDS = {
    0: 22.0,  # Melbourne
    3: 23.5,  # Bahrain / Sakhir
    4: 22.0,  # Catalunya
    5: 19.5,  # Monaco
    6: 21.0,  # Montreal
    7: 28.0,  # Silverstone
    9: 20.5,  # Hungaroring
    10: 23.0,  # Spa
    11: 24.0,  # Monza
    12: 28.0,  # Singapore
    13: 23.0,  # Suzuka
    14: 22.0,  # Abu Dhabi
    15: 22.0,  # Texas / COTA
    16: 21.0,  # Brazil
    17: 20.0,  # Austria
    19: 20.5,  # Mexico
    20: 20.5,  # Baku
    26: 19.5,  # Zandvoort
    27: 28.0,  # Imola
    29: 21.5,  # Jeddah
    30: 22.5,  # Miami
    31: 20.5,  # Las Vegas
    32: 23.0,  # Losail
    42: 24.0,  # Madrid
}

# Approximate point beyond which a current-lap stop is no longer realistic.
# This is deliberately conservative and can later be replaced by labelled maps.
PIT_ENTRY_FRACTION = {
    5: 0.965,  # Monaco
    7: 0.955,  # Silverstone
    10: 0.955,  # Spa
    11: 0.965,  # Monza
    12: 0.945,  # Singapore
    13: 0.955,  # Suzuka
    16: 0.965,  # Brazil
    20: 0.955,  # Baku
}

# Track multiplier for cold-start wear/deg defaults before personal evidence exists.
TRACK_TYRE_SEVERITY = {
    0: 1.00,
    3: 1.15,
    4: 1.20,
    5: 0.88,
    6: 0.95,
    7: 1.18,
    9: 1.08,
    10: 1.12,
    11: 0.92,
    12: 1.12,
    13: 1.28,
    14: 0.98,
    15: 1.15,
    16: 1.18,
    17: 1.08,
    19: 1.05,
    20: 0.92,
    26: 1.08,
    27: 1.05,
    29: 1.00,
    30: 1.02,
    31: 0.82,
    32: 1.30,
    42: 1.05,
}

COMPOUND_DELTA = {
    "SOFT": -0.55,
    "MEDIUM": 0.0,
    "HARD": 0.65,
    "INTER": 7.0,
    "WET": 12.0,
}

DEFAULT_DEG = {
    "SOFT": 0.16,
    "MEDIUM": 0.09,
    "HARD": 0.055,
    "INTER": 0.11,
    "WET": 0.10,
}

OPERATIONAL_WEAR_LIMIT = {
    "SOFT": 80.0,
    "MEDIUM": 85.0,
    "HARD": 88.0,
    "INTER": 88.0,
    "WET": 90.0,
}

DEFAULT_WEAR_PER_LAP = {
    "SOFT": 4.8,
    "MEDIUM": 3.2,
    "HARD": 2.1,
    "INTER": 3.1,
    "WET": 2.6,
}

DRY_COMPOUNDS = {"SOFT", "MEDIUM", "HARD"}
WET_COMPOUNDS = {"INTER", "WET"}

# Typical usable stint length per compound, derived from the operational wear
# limit and default wear rate. Used only to *estimate* when a rival is likely to
# stop; the game AI may deviate, so anything built on this is labelled an
# estimate.
TYPICAL_STINT_LAPS = {
    compound: max(6, round(OPERATIONAL_WEAR_LIMIT[compound] / DEFAULT_WEAR_PER_LAP[compound]))
    for compound in DEFAULT_WEAR_PER_LAP
}

# F1 points for finishing positions 1..10 (2026 system unchanged from 2010+).
F1_POINTS = {1: 25, 2: 18, 3: 15, 4: 12, 5: 10, 6: 8, 7: 6, 8: 4, 9: 2, 10: 1}


def points_for_position(position: int) -> int:
    return F1_POINTS.get(int(position), 0)


class StrategyEngine:
    """Deterministic, explainable tyre, stop and neutralisation strategy model."""

    def __init__(self, store: StateStore, database: PitWallDatabase) -> None:
        self.store = store
        self.database = database

    @staticmethod
    def _valid_laps(state: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            lap
            for lap in state.get("completed_laps", [])
            if lap.get("valid")
            and lap.get("lap_time_ms", 0) > 0
            and not lap.get("pit_status")
        ]

    @staticmethod
    def _estimate_base_lap_s(state: dict[str, Any]) -> float:
        valid = StrategyEngine._valid_laps(state)
        recent = [lap["lap_time_ms"] / 1000 for lap in valid[-4:]]
        if recent:
            return float(median(recent))
        player_idx = int(state.get("player_car_index", 0))
        player = next(
            (
                driver
                for driver in state.get("drivers", [])
                if int(driver.get("car_idx", -1)) == player_idx
            ),
            None,
        )
        if player and player.get("best_lap_ms"):
            return float(player["best_lap_ms"]) / 1000
        return 95.0

    @staticmethod
    def _used_compounds(state: dict[str, Any]) -> list[str]:
        used: list[str] = []
        player_idx = int(state.get("player_car_index", 0))
        player = next(
            (
                driver
                for driver in state.get("drivers", [])
                if int(driver.get("car_idx", -1)) == player_idx
            ),
            None,
        )
        if player:
            for stint in player.get("tyre_stints", []):
                compound = str(stint.get("compound", "UNKNOWN")).upper()
                if compound not in {"UNKNOWN", "FITTED"} and compound not in used:
                    used.append(compound)
        for lap in state.get("completed_laps", []):
            compound = str(lap.get("compound", "UNKNOWN")).upper()
            if compound not in {"UNKNOWN", "FITTED"} and compound not in used:
                used.append(compound)
        current = str(state.get("tyre", {}).get("compound", "UNKNOWN")).upper()
        if current not in {"UNKNOWN", "FITTED"} and current not in used:
            used.append(current)
        return used

    @staticmethod
    def _compound_rule(
        state: dict[str, Any], future: list[str] | None = None
    ) -> dict[str, Any]:
        used = StrategyEngine._used_compounds(state)
        future = [str(value).upper() for value in (future or [])]
        all_compounds = used + [item for item in future if item not in used]
        wet_used = any(item in WET_COMPOUNDS for item in all_compounds)
        dry_used = sorted({item for item in all_compounds if item in DRY_COMPOUNDS})
        applies = str(state.get("mode_profile", "idle")) == "race" and not wet_used
        compliant = (not applies) or len(dry_used) >= 2
        return {
            "applies": applies,
            "wet_waiver": wet_used,
            "used_compounds": used,
            "dry_compounds_used": dry_used,
            "dry_count": len(dry_used),
            "required_dry_count": 2 if applies else 0,
            "compliant": compliant,
            "change_outstanding": applies and not compliant,
            "eligible_next_compounds": sorted(DRY_COMPOUNDS - set(dry_used))
            if applies
            else [],
            "note": (
                "Two different dry visual compounds are enforced for a dry Race. "
                "The game does not expose the event-specific FIA mandatory race specification."
                if applies
                else "Wet/intermediate use waives the two-dry-compound check."
                if wet_used
                else "The two-dry-compound Race requirement does not apply to this session type."
            ),
        }

    @staticmethod
    def _available_sets(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
        best: dict[str, dict[str, Any]] = {}
        for tyre in state.get("tyre_sets", []):
            if not tyre.get("available"):
                continue
            compound = str(tyre.get("compound", "UNKNOWN")).upper()
            if compound not in DRY_COMPOUNDS | WET_COMPOUNDS:
                continue
            candidate = dict(tyre)
            current = best.get(compound)
            candidate_key = (
                float(candidate.get("wear_pct", 100.0)),
                -int(candidate.get("usable_life_laps", 0)),
                int(candidate.get("lap_delta_ms", 0)),
            )
            if current is None:
                best[compound] = candidate
            else:
                current_key = (
                    float(current.get("wear_pct", 100.0)),
                    -int(current.get("usable_life_laps", 0)),
                    int(current.get("lap_delta_ms", 0)),
                )
                if candidate_key < current_key:
                    best[compound] = candidate
        if not best:
            best = {
                compound: {
                    "compound": compound,
                    "wear_pct": 0.0,
                    "usable_life_laps": 0,
                    "life_span_laps": 0,
                    "lap_delta_ms": 0,
                    "source": "fallback",
                }
                for compound in ("SOFT", "MEDIUM", "HARD")
            }
        return best

    @staticmethod
    def _live_wear_samples(state: dict[str, Any], compound: str) -> list[float]:
        samples: list[float] = []
        for lap in StrategyEngine._valid_laps(state)[-10:]:
            if str(lap.get("compound", "")).upper() != compound:
                continue
            start = lap.get("wear_start") or []
            end = lap.get("wear_end") or []
            if len(start) == 4 and len(end) == 4:
                # Strategy is constrained by the most worn corner, not the
                # four-tyre average. This catches driving styles that load one
                # axle or one side heavily (for example Spa rear wear).
                samples.append(
                    max(
                        max(0.0, float(after) - float(before))
                        for before, after in zip(start, end)
                    )
                )
        return samples

    @staticmethod
    def _driver_wear_factor(
        state: dict[str, Any],
        historical: dict[str, Any],
    ) -> tuple[float, dict[str, Any]]:
        current = str(state.get("tyre", {}).get("compound", "MEDIUM")).upper()
        severity = TRACK_TYRE_SEVERITY.get(int(state.get("track_id", -1)), 1.0)
        default = DEFAULT_WEAR_PER_LAP.get(current, 3.2) * severity
        live_samples = StrategyEngine._live_wear_samples(state, current)
        observed: float | None = float(median(live_samples)) if live_samples else None
        source = "live_lap_wear"
        sample_size = len(live_samples)

        if observed is None:
            history_model = historical.get("compounds", {}).get(current, {})
            value = history_model.get("max_wear_per_lap_pct")
            if value is not None:
                observed = float(value)
                source = "personal_track_history"
                sample_size = int(history_model.get("wear_sample_size", 0))

        if observed is None:
            age = int(state.get("tyre", {}).get("age_laps", 0))
            wear = state.get("tyre", {}).get("wear", []) or []
            if age >= 2 and wear:
                observed = max(float(v) for v in wear) / age
                source = "current_stint_level"
                sample_size = age

        if observed is None or observed <= 0:
            return 1.0, {
                "factor": 1.0,
                "observed_limiting_wear_per_lap_pct": None,
                "baseline_wear_per_lap_pct": round(default, 3),
                "source": "track_default",
                "sample_size": 0,
            }

        factor = max(0.60, min(2.60, observed / max(default, 0.1)))
        return factor, {
            "factor": round(factor, 3),
            "observed_limiting_wear_per_lap_pct": round(observed, 3),
            "baseline_wear_per_lap_pct": round(default, 3),
            "source": source,
            "sample_size": sample_size,
        }

    @staticmethod
    def _wear_rate(
        state: dict[str, Any],
        compound: str,
        historical: dict[str, Any],
        style_factor: float,
    ) -> tuple[float, str, int]:
        live = StrategyEngine._live_wear_samples(state, compound)
        if live:
            return float(median(live)), "live_lap_wear", len(live)
        model = historical.get("compounds", {}).get(compound, {})
        value = model.get("max_wear_per_lap_pct")
        if value is not None and int(model.get("wear_sample_size", 0)) >= 2:
            return (
                float(value),
                "personal_track_history",
                int(model.get("wear_sample_size", 0)),
            )
        severity = TRACK_TYRE_SEVERITY.get(int(state.get("track_id", -1)), 1.0)
        return (
            DEFAULT_WEAR_PER_LAP.get(compound, 3.0) * severity * style_factor,
            "style_adjusted_track_default",
            0,
        )

    @staticmethod
    def _deg_for(
        state: dict[str, Any],
        compound: str,
        historical: dict[str, Any],
    ) -> tuple[float, str, int]:
        model = state.get("analysis", {}).get("deg_model", {})
        live = (
            model.get("compounds", {}).get(compound, {})
            if isinstance(model, dict)
            else {}
        )
        value = live.get("slope_s_per_lap")
        sample = int(live.get("sample_size", 0))
        if value is not None and sample >= 3 and -0.1 <= float(value) <= 1.5:
            return max(0.0, float(value)), "live_fuel_corrected_fit", sample
        prior = historical.get("compounds", {}).get(compound, {})
        value = prior.get("slope_s_per_lap")
        sample = int(prior.get("sample_size", 0))
        if value is not None and sample >= 3 and -0.1 <= float(value) <= 1.5:
            return max(0.0, float(value)), "personal_track_history", sample
        severity = TRACK_TYRE_SEVERITY.get(int(state.get("track_id", -1)), 1.0)
        return DEFAULT_DEG.get(compound, 0.08) * severity, "track_default", 0

    @staticmethod
    def _pit_entry_status(state: dict[str, Any]) -> str:
        track_length = float(state.get("track_length_m", 0) or 0)
        distance = float(state.get("lap_distance_m", 0) or 0)
        if track_length <= 100 or distance <= 0:
            return "unknown"
        threshold = PIT_ENTRY_FRACTION.get(int(state.get("track_id", -1)), 0.94)
        return "passed" if distance / track_length >= threshold else "available"

    @staticmethod
    def _base_pit_loss(state: dict[str, Any]) -> float:
        measured = [
            float(lap.get("pit_lane_time_ms", 0)) / 1000
            for lap in state.get("completed_laps", [])
            if float(lap.get("pit_lane_time_ms", 0)) > 5000
        ]
        return (
            float(median(measured))
            if measured
            else PIT_LOSS_SECONDS.get(int(state.get("track_id", -1)), 22.5)
        )

    @staticmethod
    def _neutralisation(state: dict[str, Any]) -> dict[str, Any]:
        base = StrategyEngine._base_pit_loss(state)
        phase = str(state.get("race_control_phase", "green"))
        safety = str(state.get("safety_car", "none"))
        red = bool(state.get("red_flag_active")) or phase == "red_flag"
        if red:
            factor = 0.0
            kind = "red_flag"
        elif phase in {"safety_car", "formation"} or safety == "full":
            factor = 0.46
            kind = "safety_car"
        elif phase == "vsc" or safety == "virtual":
            factor = 0.64
            kind = "vsc"
        elif phase in {"safety_car_ending", "vsc_ending", "formation_ending"}:
            factor = 0.82
            kind = phase
        else:
            factor = 1.0
            kind = "green"
        effective = base * factor
        pit_entry_status = StrategyEngine._pit_entry_status(state)
        opportunity = (
            "free_tyre_change_during_suspension"
            if red
            else "major_discount_and_field_compression"
            if kind == "safety_car"
            else "discount_with_gaps_partly_preserved"
            if kind == "vsc"
            else "reduced_discount_as_restart_approaches"
            if kind.endswith("ending")
            else "normal_green_flag_stop"
        )
        current_lap = int(state.get("current_lap", 0))
        total_laps = int(state.get("total_laps", 0))
        laps_remaining = max(0, total_laps - current_lap + (1 if current_lap > 0 else 0))
        late_neutralisation = kind in {"safety_car", "safety_car_ending"} and laps_remaining <= 4
        finish_under_neutralisation_risk = (
            "high" if late_neutralisation and laps_remaining <= 2
            else "medium" if late_neutralisation
            else "low"
        )
        return {
            "phase": kind,
            "base_pit_loss_s": round(base, 2),
            "effective_pit_loss_s": round(effective, 2),
            "saving_vs_green_s": round(base - effective, 2),
            "pit_entry_status": pit_entry_status,
            "pit_this_lap_available": red or pit_entry_status != "passed",
            "field_compression_expected": kind == "safety_car",
            "gaps_partly_preserved": kind == "vsc",
            "opportunity": opportunity,
            "laps_remaining": laps_remaining,
            "late_neutralisation": late_neutralisation,
            "finish_under_neutralisation_risk": finish_under_neutralisation_risk,
            "track_position_warning": (
                "A late safety-car stop can permanently surrender track position if the race does not restart."
                if late_neutralisation
                else ""
            ),
            "estimate_confidence": (
                "high"
                if red
                else "medium"
                if kind in {"safety_car", "vsc", "green"}
                else "low"
            ),
            "red_flag_tyre_change": red,
        }

    @staticmethod
    def _weather_crossover(
        state: dict[str, Any], base_lap_s: float
    ) -> dict[str, Any] | None:
        current_lap = int(state.get("current_lap", 0))
        total_laps = int(state.get("total_laps", 0))
        weather = str(state.get("weather", "Unknown"))
        current_rain = weather in {"Light rain", "Heavy rain", "Storm"}
        current_compound = str(state.get("tyre", {}).get("compound", "UNKNOWN"))
        if current_rain and current_compound not in WET_COMPOUNDS:
            compound = "WET" if weather in {"Heavy rain", "Storm"} else "INTER"
            return {
                "box_lap": current_lap,
                "compound": compound,
                "rain_pct": 100
                if weather == "Storm"
                else 80
                if weather == "Heavy rain"
                else 60,
                "time_offset_min": 0,
                "reason": f"{weather} is already on track.",
            }
        for sample in sorted(
            state.get("weather_forecast", []),
            key=lambda item: int(item.get("time_offset_min", 999)),
        ):
            rain_pct = int(sample.get("rain_pct", 0))
            if rain_pct < 60:
                continue
            minutes = max(0, int(sample.get("time_offset_min", 0)))
            laps_until = max(1, math.ceil(minutes * 60 / max(base_lap_s, 30.0)))
            box_lap = min(total_laps, current_lap + laps_until)
            sample_weather = str(sample.get("weather", "Light rain"))
            compound = (
                "WET"
                if rain_pct >= 80 or sample_weather in {"Heavy rain", "Storm"}
                else "INTER"
            )
            return {
                "box_lap": box_lap,
                "compound": compound,
                "rain_pct": rain_pct,
                "time_offset_min": minutes,
                "reason": f"Rain reaches {rain_pct}% in about {minutes} minutes ({sample_weather.lower()}).",
            }
        return None

    @staticmethod
    def _rejoin_position(state: dict[str, Any], effective_pit_loss_s: float) -> int:
        current_position = int(state.get("player_position", 0)) or 1
        if effective_pit_loss_s <= 0:
            return current_position
        lost_positions = 0
        for driver in state.get("drivers", []):
            gap = driver.get("gap_to_player_s")
            if gap is not None and 0 < float(gap) < effective_pit_loss_s:
                lost_positions += 1
        active = int(state.get("active_cars", 0)) or 24
        return min(active, current_position + lost_positions)

    @staticmethod
    def _live_wheel_wear_samples(
        state: dict[str, Any], compound: str
    ) -> list[list[float]]:
        wheel_samples: list[list[float]] = [[], [], [], []]
        for lap in StrategyEngine._valid_laps(state)[-12:]:
            if str(lap.get("compound", "")).upper() != compound:
                continue
            start = lap.get("wear_start") or []
            end = lap.get("wear_end") or []
            if len(start) != 4 or len(end) != 4:
                continue
            for index, (before, after) in enumerate(zip(start, end)):
                wheel_samples[index].append(max(0.0, float(after) - float(before)))
        return wheel_samples

    def _wheel_wear_rates(
        self,
        state: dict[str, Any],
        compound: str,
        historical: dict[str, Any],
        style_factor: float,
    ) -> tuple[list[float], str, int, dict[str, Any]]:
        live = self._live_wheel_wear_samples(state, compound)
        if any(live):
            fallback, _, _ = self._wear_rate(state, compound, historical, style_factor)
            rates = [float(median(values)) if values else fallback for values in live]
            return rates, "live_per_wheel_wear", min(len(v) for v in live if v), setup_effects(state.get("car_setup", {}), int(state.get("track_id", -1)))

        history = historical.get("compounds", {}).get(compound, {})
        history_rates = history.get("wheel_wear_per_lap_pct") or []
        if len(history_rates) == 4 and any(value is not None for value in history_rates):
            fallback = float(history.get("max_wear_per_lap_pct") or DEFAULT_WEAR_PER_LAP.get(compound, 3.0))
            rates = [float(value) if value is not None else fallback for value in history_rates]
            return rates, "personal_per_wheel_history", int(history.get("wear_sample_size", 0)), setup_effects(state.get("car_setup", {}), int(state.get("track_id", -1)))

        scalar, source, sample_size = self._wear_rate(state, compound, historical, style_factor)
        effects = setup_effects(state.get("car_setup", {}), int(state.get("track_id", -1)))
        multipliers = effects.get("wheel_wear_multipliers", [1.0, 1.0, 1.0, 1.0])
        rates = [scalar * float(multiplier) for multiplier in multipliers]
        return rates, f"{source}+setup_prior", sample_size, effects

    def _simulate_stint(
        self,
        state: dict[str, Any],
        compound: str,
        laps: int,
        starting_age: int,
        starting_wear: float | list[float],
        base_lap_s: float,
        historical: dict[str, Any],
        style_factor: float,
        set_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        deg, deg_source, deg_samples = self._deg_for(state, compound, historical)
        wheel_rates, wear_source, wear_samples, effects = self._wheel_wear_rates(
            state, compound, historical, style_factor
        )
        set_info = set_info or {}
        set_wear = max(0.0, float(set_info.get("wear_pct", 0.0)))
        if isinstance(starting_wear, list) and len(starting_wear) == 4:
            wear = [max(float(value), set_wear) for value in starting_wear]
        else:
            wear = [max(float(starting_wear), set_wear)] * 4
        usable_life = int(set_info.get("usable_life_laps", 0) or 0)
        life_span = int(set_info.get("life_span_laps", 0) or 0)
        set_delta_s = float(set_info.get("lap_delta_ms", 0) or 0) / 1000.0
        reference = str(state.get("tyre", {}).get("compound", "MEDIUM"))
        compound_delta = COMPOUND_DELTA.get(compound, 0.0) - COMPOUND_DELTA.get(reference, 0.0)
        setup_delta_s = float(effects.get("lap_time_delta_s", 0.0))
        expected = conservative = 0.0
        feasible = True
        peak_wear = max(wear)
        lap_times: list[float] = []
        wheel_projection: list[list[float]] = []
        # A fresh set is cold for its first laps. Only a stint that begins on a
        # new tyre (starting_age == 0) pays this; continuing the current stint
        # does not. Full penalty on the out-lap, half on the following lap.
        cold_penalty_s = (
            settings.strategy_cold_tyre_penalty_s if starting_age == 0 else 0.0
        )
        for offset in range(max(0, laps)):
            age = starting_age + offset
            thermal_growth = 1.0 + max(0, age - 8) * 0.012
            for index in range(4):
                wear[index] += wheel_rates[index] * thermal_growth
            peak_wear = max(peak_wear, max(wear))
            average_wear = sum(wear) / 4
            wear_penalty = max(0.0, average_wear - 52.0) * 0.009 + max(0.0, peak_wear - 58.0) * 0.008
            cliff = max(0.0, peak_wear - 70.0) * 0.060
            warm_up = cold_penalty_s if offset == 0 else (cold_penalty_s * 0.5 if offset == 1 else 0.0)
            expected_lap = base_lap_s + compound_delta + set_delta_s + setup_delta_s + deg * age + wear_penalty + cliff + warm_up
            uncertainty = 0.045 + (0.24 if compound == "SOFT" else 0.12 if compound == "MEDIUM" else 0.075) * (1.0 if min(deg_samples, wear_samples) < 3 else 0.35)
            conservative_lap = expected_lap + uncertainty + max(0.0, peak_wear - 65.0) * 0.020
            expected += expected_lap
            conservative += conservative_lap
            lap_times.append(expected_lap)
            wheel_projection.append([round(value, 2) for value in wear])
            if peak_wear >= 92.0:
                feasible = False
                expected += 18.0 + (peak_wear - 92.0) * 2.0
                conservative += 30.0 + (peak_wear - 92.0) * 3.0
        if usable_life > 0 and laps > usable_life + 1:
            feasible = False
            conservative += (laps - usable_life) * 8.0
        if life_span > 0 and starting_age + laps > life_span + 2:
            feasible = False
            conservative += (starting_age + laps - life_span) * 5.0
        operational_limit = OPERATIONAL_WEAR_LIMIT.get(compound, 85.0)
        if peak_wear > operational_limit:
            feasible = False
            conservative += 12.0 + (peak_wear - operational_limit) * 1.75
        return {
            "expected_time_s": expected,
            "conservative_time_s": conservative,
            "projected_finish_wear_pct": max(wear),
            "projected_finish_wear_fl_fr_rl_rr": [round(value, 1) for value in wear],
            "projected_max_wear_pct": peak_wear,
            "feasible": feasible,
            "wear_per_lap_pct": max(wheel_rates),
            "wheel_wear_per_lap_pct": [round(value, 3) for value in wheel_rates],
            "wear_source": wear_source,
            "wear_sample_size": wear_samples,
            "deg_s_per_lap": deg,
            "deg_source": deg_source,
            "deg_sample_size": deg_samples,
            "usable_life_laps": usable_life,
            "operational_wear_limit_pct": operational_limit,
            "setup_effects": effects,
            "lap_times_s": [round(value, 3) for value in lap_times],
            "wheel_projection": wheel_projection,
        }

    @staticmethod
    def _traffic_cost(
        state: dict[str, Any],
        effective_pit_loss_s: float,
        stops: int,
    ) -> tuple[float, int]:
        rejoin = StrategyEngine._rejoin_position(state, effective_pit_loss_s)
        current = int(state.get("player_position", 1)) or 1
        positions_lost = max(0, rejoin - current)
        # The first positions in a train cost more than an isolated position because
        # the fresh-tyre benefit can be trapped behind traffic.
        cost = positions_lost * (0.32 + 0.06 * max(0, stops - 1))

        phase = str(state.get("race_control_phase", "green"))
        safety = str(state.get("safety_car", "none"))
        current_lap = int(state.get("current_lap", 0))
        total_laps = int(state.get("total_laps", 0))
        laps_remaining = max(0, total_laps - current_lap + (1 if current_lap > 0 else 0))
        late_sc = (phase in {"safety_car", "safety_car_ending"} or safety == "full") and laps_remaining <= 4
        if late_sc and positions_lost:
            # Time alone is the wrong objective near the finish: a position lost
            # during a late Safety Car may be impossible to recover. Convert that
            # classification risk into a large ranking penalty.
            per_position = 15.0 if laps_remaining <= 2 else 9.0 if laps_remaining == 3 else 5.5
            cost += positions_lost * per_position
        return round(cost, 2), rejoin

    @staticmethod
    def _monte_carlo_profile(
        plan: dict[str, Any],
        state: dict[str, Any],
        effective_pit_loss_s: float,
        samples: int,
    ) -> dict[str, Any]:
        samples = max(80, min(1200, int(samples)))
        seed = hash((int(state.get("session_uid", 0)), int(state.get("current_lap", 0)), tuple(plan.get("box_laps", [])), tuple(plan.get("compounds", [])))) & 0xFFFFFFFF
        rng = np.random.default_rng(seed)
        expected = float(plan.get("projected_time_s", 0.0))
        stints = plan.get("stint_models", [])
        evidence = max((int(s.get("wear_sample_size", 0)) + int(s.get("deg_sample_size", 0)) for s in stints), default=0)
        uncertainty_scale = 0.45 if evidence >= 8 else 0.85 if evidence >= 3 else 1.35
        laps = sum(len(s.get("lap_times_s", [])) for s in stints)
        tyre_sigma = uncertainty_scale * math.sqrt(max(1, laps)) * 0.18
        pit_sigma = max(0.15, effective_pit_loss_s * (0.015 if state.get("race_control_phase") == "green" else 0.04)) * max(1, int(plan.get("stops_remaining", 0)))
        traffic_sigma = 0.35 * max(0, int(plan.get("projected_rejoin_position", state.get("player_position", 1))) - int(state.get("player_position", 1)))
        outcomes = expected + rng.normal(0.0, tyre_sigma, samples) + rng.normal(0.0, pit_sigma, samples) + np.abs(rng.normal(0.0, traffic_sigma, samples))
        if not plan.get("feasible", True):
            outcomes += rng.uniform(12.0, 35.0, samples)
        return {
            "samples": samples,
            "p25_s": round(float(np.quantile(outcomes, 0.25)), 2),
            "p50_s": round(float(np.quantile(outcomes, 0.50)), 2),
            "p75_s": round(float(np.quantile(outcomes, 0.75)), 2),
            "p90_s": round(float(np.quantile(outcomes, 0.90)), 2),
            "mean_s": round(float(np.mean(outcomes)), 2),
            "uncertainty_s": round(float(np.std(outcomes)), 2),
            "evidence_samples": evidence,
        }

    async def recompute(self) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        historical = await self.database.tyre_history_model(
            int(state.get("track_id", -1))
        )
        plan = self.compute(state, historical)
        await self.store.update(strategy=plan)
        await self.database.save_strategy_snapshot(state, plan)
        return plan

    def compute(
        self,
        state: dict[str, Any],
        historical: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        historical = historical or {"compounds": {}}
        current_lap = int(state.get("current_lap", 0))
        total_laps = int(state.get("total_laps", 0))
        # Include the current lap because telemetry reports the lap in progress.
        # At lap 3/22 there are 20 lap completions left, not 19.
        remaining = max(
            0,
            total_laps - current_lap + (1 if current_lap > 0 else 0),
        )
        current_compound = str(state.get("tyre", {}).get("compound", "UNKNOWN")).upper()
        current_age = int(state.get("tyre", {}).get("age_laps", 0))
        current_wear = list(state.get("tyre", {}).get("wear", [0.0, 0.0, 0.0, 0.0]) or [0.0, 0.0, 0.0, 0.0])
        game = state.get("strategy", {})
        game_ideal = int(game.get("game_ideal_lap", game.get("ideal_lap", 0)) or 0)
        game_latest = int(game.get("game_latest_lap", game.get("latest_lap", 0)) or 0)
        game_status = "unavailable"
        if game_ideal:
            if current_lap > game_latest > 0:
                game_status = "expired"
            elif current_lap >= game_ideal:
                game_status = "open"
            else:
                game_status = "upcoming"

        mode = str(state.get("mode_profile", "idle"))
        base_rule = self._compound_rule(state)
        neutralisation = self._neutralisation(state)
        if remaining <= 0 or total_laps <= 0 or mode not in {"race", "sprint"}:
            return {
                "available": False,
                "reason": (
                    "No race-distance tyre strategy applies in this session."
                    if mode not in {"race", "sprint"}
                    else "Race distance is not available yet."
                ),
                "compound_rule": base_rule,
                "neutralisation": neutralisation,
                "game_window": {
                    "ideal_lap": game_ideal,
                    "latest_lap": game_latest,
                    "status": game_status,
                },
                "recommended": {},
                "plans": [],
            }

        base_lap_s = self._estimate_base_lap_s(state)
        effective_pit_loss = float(neutralisation["effective_pit_loss_s"])
        available_sets = self._available_sets(state)
        compounds = list(available_sets)
        weather_crossover = self._weather_crossover(state, base_lap_s)
        style_factor, style_evidence = self._driver_wear_factor(state, historical)
        plans: list[dict[str, Any]] = []
        simulation_cache: dict[tuple[Any, ...], dict[str, Any]] = {}

        def simulate(
            state_arg: dict[str, Any],
            compound: str,
            laps: int,
            starting_age: int,
            starting_wear: float | list[float],
            base_lap: float,
            history: dict[str, Any],
            personal_factor: float,
            set_info: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            wear_key = (
                tuple(round(float(value), 3) for value in starting_wear)
                if isinstance(starting_wear, list)
                else round(float(starting_wear), 3)
            )
            set_key = tuple(
                sorted(
                    (str(key), str(value))
                    for key, value in (set_info or {}).items()
                    if key in {
                        "compound", "wear_pct", "usable_life_laps",
                        "life_span_laps", "lap_delta_ms", "source",
                    }
                )
            )
            key = (
                str(compound), int(laps), int(starting_age), wear_key,
                round(float(base_lap), 3), round(float(personal_factor), 4), set_key,
            )
            if key not in simulation_cache:
                simulation_cache[key] = self._simulate_stint(
                    state_arg, compound, laps, starting_age, starting_wear,
                    base_lap, history, personal_factor, set_info,
                )
            return simulation_cache[key]

        def make_plan(
            *,
            stops: int,
            box_laps: list[int],
            compounds_in_plan: list[str],
            stints: list[dict[str, Any]],
            reason: str,
            weather: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            traffic_cost, projected_rejoin = self._traffic_cost(
                state, effective_pit_loss, stops
            )
            expected = (
                sum(float(stint["expected_time_s"]) for stint in stints)
                + stops * effective_pit_loss
                + traffic_cost
            )
            conservative = (
                sum(float(stint["conservative_time_s"]) for stint in stints)
                + stops * effective_pit_loss
                + traffic_cost * 1.35
            )
            legality = self._compound_rule(state, compounds_in_plan[1:])
            feasible = all(bool(stint["feasible"]) for stint in stints) and legality["compliant"]
            return {
                "stops_remaining": stops,
                "box_laps": box_laps,
                "compounds": compounds_in_plan,
                "projected_time_s": round(expected, 2),
                "risk_adjusted_time_s": round(conservative, 2),
                "projected_finish_wear_pct": round(
                    float(stints[-1]["projected_finish_wear_pct"]), 1
                ),
                "projected_max_wear_pct": round(
                    max(float(stint["projected_max_wear_pct"]) for stint in stints), 1
                ),
                "feasible": feasible,
                "legal": legality["compliant"],
                "compound_rule": legality,
                "reason": reason,
                "traffic_cost_s": traffic_cost,
                "projected_rejoin_position": projected_rejoin,
                "stint_models": stints,
                **({"weather_crossover": weather} if weather else {}),
            }

        no_stop = simulate(
            state,
            current_compound,
            remaining,
            current_age,
            current_wear,
            base_lap_s,
            historical,
            style_factor,
            None,
        )
        plans.append(
            make_plan(
                stops=0,
                box_laps=[],
                compounds_in_plan=[current_compound],
                stints=[no_stop],
                reason="Stay out to the finish",
            )
        )

        red_flag_change = neutralisation["phase"] == "red_flag"
        earliest_box_lap = current_lap
        if neutralisation["pit_entry_status"] == "passed" and not red_flag_change:
            earliest_box_lap += 1
        for box_lap in range(earliest_box_lap, total_laps):
            # A normal stop at the end of the current lap still consumes that
            # lap on the fitted tyre. A red-flag change happens during the
            # suspension and therefore consumes zero additional racing laps.
            pre_laps = (
                0
                if red_flag_change and box_lap == current_lap
                else box_lap - current_lap + 1
            )
            post_laps = remaining - pre_laps
            if post_laps <= 0:
                continue
            pre = simulate(
                state,
                current_compound,
                pre_laps,
                current_age,
                current_wear,
                base_lap_s,
                historical,
                style_factor,
                None,
            )
            for compound in compounds:
                if compound == current_compound and len(compounds) > 1:
                    continue
                post = simulate(
                    state,
                    compound,
                    post_laps,
                    0,
                    0.0,
                    base_lap_s,
                    historical,
                    style_factor,
                    available_sets.get(compound),
                )
                reason = (
                    f"Fit {compound} during the red flag"
                    if neutralisation["phase"] == "red_flag"
                    else f"Box lap {box_lap} for {compound}"
                )
                plans.append(
                    make_plan(
                        stops=1,
                        box_laps=[box_lap],
                        compounds_in_plan=[current_compound, compound],
                        stints=[pre, post],
                        reason=reason,
                    )
                )

        if remaining >= 18 and not red_flag_change:
            for first_box_lap in range(earliest_box_lap, total_laps - 6, 2):
                first_laps = first_box_lap - current_lap + 1
                for second_box_lap in range(first_box_lap + 5, total_laps, 3):
                    middle_laps = second_box_lap - first_box_lap
                    final_laps = total_laps - second_box_lap
                    if final_laps <= 0:
                        continue
                    for middle in compounds:
                        for final in compounds:
                            if middle == current_compound or final == middle:
                                continue
                            first = simulate(
                                state,
                                current_compound,
                                first_laps,
                                current_age,
                                current_wear,
                                base_lap_s,
                                historical,
                                style_factor,
                                None,
                            )
                            middle_stint = simulate(
                                state,
                                middle,
                                middle_laps,
                                0,
                                0.0,
                                base_lap_s,
                                historical,
                                style_factor,
                                available_sets.get(middle),
                            )
                            final_stint = simulate(
                                state,
                                final,
                                final_laps,
                                0,
                                0.0,
                                base_lap_s,
                                historical,
                                style_factor,
                                available_sets.get(final),
                            )
                            plans.append(
                                make_plan(
                                    stops=2,
                                    box_laps=[first_box_lap, second_box_lap],
                                    compounds_in_plan=[current_compound, middle, final],
                                    stints=[first, middle_stint, final_stint],
                                    reason=f"Box laps {first_box_lap} and {second_box_lap}",
                                )
                            )

        if (
            settings.strategy_max_stops >= 3
            and remaining >= 28
            and not red_flag_change
        ):
            # Coarse but complete three-stop search. It is intentionally sampled
            # rather than lap-by-lap to keep live recomputes fast.
            first_candidates = range(earliest_box_lap + 4, total_laps - 15, 4)
            for first_box_lap in first_candidates:
                first_laps = first_box_lap - current_lap + 1
                for second_box_lap in range(first_box_lap + 6, total_laps - 8, 5):
                    middle1_laps = second_box_lap - first_box_lap
                    for third_box_lap in range(second_box_lap + 6, total_laps, 5):
                        middle2_laps = third_box_lap - second_box_lap
                        final_laps = total_laps - third_box_lap
                        if min(first_laps, middle1_laps, middle2_laps, final_laps) <= 0:
                            continue
                        for compound1 in compounds:
                            for compound2 in compounds:
                                for compound3 in compounds:
                                    if compound1 == current_compound or compound2 == compound1 or compound3 == compound2:
                                        continue
                                    stints = [
                                        simulate(state, current_compound, first_laps, current_age, current_wear, base_lap_s, historical, style_factor, None),
                                        simulate(state, compound1, middle1_laps, 0, 0.0, base_lap_s, historical, style_factor, available_sets.get(compound1)),
                                        simulate(state, compound2, middle2_laps, 0, 0.0, base_lap_s, historical, style_factor, available_sets.get(compound2)),
                                        simulate(state, compound3, final_laps, 0, 0.0, base_lap_s, historical, style_factor, available_sets.get(compound3)),
                                    ]
                                    plans.append(
                                        make_plan(
                                            stops=3,
                                            box_laps=[first_box_lap, second_box_lap, third_box_lap],
                                            compounds_in_plan=[current_compound, compound1, compound2, compound3],
                                            stints=stints,
                                            reason=f"Box laps {first_box_lap}, {second_box_lap} and {third_box_lap}",
                                        )
                                    )

        weather_plan: dict[str, Any] | None = None
        if weather_crossover is not None:
            box_lap = int(weather_crossover["box_lap"])
            fit = str(weather_crossover["compound"])
            first_laps = max(1, box_lap - current_lap + 1)
            pre = simulate(
                state,
                current_compound,
                first_laps,
                current_age,
                current_wear,
                base_lap_s,
                historical,
                style_factor,
                None,
            )
            post = simulate(
                state,
                fit,
                max(0, remaining - first_laps),
                0,
                0.0,
                base_lap_s,
                historical,
                style_factor,
                available_sets.get(fit),
            )
            weather_plan = make_plan(
                stops=1,
                box_laps=[box_lap],
                compounds_in_plan=[current_compound, fit],
                stints=[pre, post],
                reason=str(weather_crossover["reason"]),
                weather=weather_crossover,
            )
            # Wet use waives the dry-compound requirement.
            weather_plan["feasible"] = bool(pre["feasible"] and post["feasible"])
            weather_plan["legal"] = True
            plans.append(weather_plan)

        feasible = [plan for plan in plans if plan["feasible"] and plan["legal"]]
        deterministic = sorted(
            feasible or plans,
            key=lambda plan: (
                not plan.get("feasible", False),
                float(plan["risk_adjusted_time_s"]),
            ),
        )
        # Run uncertainty analysis only on credible candidates; this keeps live
        # strategy recomputes bounded even when the full enumeration is large.
        shortlisted = deterministic[: min(48, len(deterministic))]
        for plan in shortlisted:
            plan["monte_carlo"] = self._monte_carlo_profile(
                plan,
                state,
                effective_pit_loss,
                settings.strategy_monte_carlo_samples,
            )
            risk_key = "p75_s" if settings.strategy_risk_quantile >= 0.70 else "p50_s"
            plan["risk_adjusted_time_s"] = plan["monte_carlo"][risk_key]
        ranked = sorted(
            shortlisted,
            key=lambda plan: (
                not plan.get("feasible", False),
                float(plan.get("risk_adjusted_time_s", 1e9)),
                int(plan.get("stops_remaining", 9)),
            ),
        )[:8]
        force_weather = bool(
            weather_plan is not None
            and int(weather_plan.get("weather_crossover", {}).get("time_offset_min", 99)) <= 1
        )
        best = weather_plan if force_weather else ranked[0]
        second = ranked[1] if len(ranked) > 1 else best
        best = dict(best)
        best["delta_to_next_s"] = round(
            float(second["risk_adjusted_time_s"]) - float(best["risk_adjusted_time_s"]),
            2,
        )

        if best["stops_remaining"]:
            box_lap = best["box_laps"][0]
            fit = best["compounds"][1]
            if neutralisation["phase"] == "red_flag":
                instruction = f"During the red flag, fit {fit} for the restart."
            else:
                instruction = f"Box lap {box_lap} for {fit}."
            current_projection = plans[0]["projected_finish_wear_pct"]
            tyre_reason = (
                str(best["weather_crossover"]["reason"])
                if best.get("weather_crossover")
                else (
                    f"Your current wear model projects {current_projection:.0f}% by the finish; "
                    f"the {fit.lower()} stint projects {best['projected_finish_wear_pct']:.0f}%."
                )
            )
        else:
            box_lap = None
            fit = None
            instruction = "Stay out to the finish."
            tyre_reason = f"Current {current_compound.lower()}s project to {best['projected_finish_wear_pct']:.0f}% at the finish."

        evidence_samples = max(
            int(style_evidence.get("sample_size", 0)),
            max(
                (
                    int(stint.get("deg_sample_size", 0))
                    for stint in best.get("stint_models", [])
                ),
                default=0,
            ),
        )
        confidence = (
            "high"
            if evidence_samples >= 8
            else "medium"
            if evidence_samples >= 3
            else "low"
        )
        stay_out_plan = plans[0]
        stay_out_comparable = bool(
            stay_out_plan.get("feasible") and stay_out_plan.get("legal")
        )
        stay_out_risk = float(stay_out_plan["risk_adjusted_time_s"])
        net_gain_vs_stay_out = (
            stay_out_risk - float(best["risk_adjusted_time_s"])
            if stay_out_comparable
            else None
        )
        stop_required_reason = (
            "mandatory compound change"
            if not stay_out_plan.get("legal")
            else "current tyre cannot reach the finish inside the operational wear margin"
            if not stay_out_plan.get("feasible")
            else "faster on risk-adjusted race time"
        )
        selected_stint = (
            best.get("stint_models", [])[-1] if best.get("stint_models") else {}
        )
        model_summary = {
            "confidence": confidence,
            "evidence_samples": evidence_samples,
            "personal_style_factor": style_evidence.get("factor", 1.0),
            "personal_style_source": style_evidence.get("source", "track_default"),
            "limiting_wear_per_lap_pct": style_evidence.get(
                "observed_limiting_wear_per_lap_pct"
            ),
            "selected_stint_wear_per_lap_pct": round(
                float(selected_stint.get("wear_per_lap_pct", 0.0)), 3
            ),
            "selected_stint_wear_source": selected_stint.get("wear_source"),
            "selected_stint_deg_s_per_lap": round(
                float(selected_stint.get("deg_s_per_lap", 0.0)), 3
            ),
            "selected_stint_deg_source": selected_stint.get("deg_source"),
        }
        return {
            "available": True,
            "laps_remaining": remaining,
            "pit_loss_s": round(effective_pit_loss, 1),
            "neutralisation": neutralisation,
            "compound_rule": self._compound_rule(state, best.get("compounds", [])[1:]),
            "game_window": {
                "ideal_lap": game_ideal,
                "latest_lap": game_latest,
                "rejoin_position": int(
                    game.get("game_rejoin_position", game.get("rejoin_position", 0))
                    or 0
                ),
                "status": game_status,
            },
            "recommended": {
                **best,
                "box_lap": box_lap,
                "fit_compound": fit,
                "instruction": instruction,
                "tyre_reason": tyre_reason,
                "projected_rejoin_position": self._rejoin_position(
                    state, effective_pit_loss
                ),
                "net_gain_vs_stay_out_s": (
                    round(net_gain_vs_stay_out, 2)
                    if net_gain_vs_stay_out is not None
                    else None
                ),
                "stop_required_reason": stop_required_reason,
            },
            "plans": ranked[:5],
            "confidence": confidence,
            "model_summary": model_summary,
            "personal_wear_model": style_evidence,
            "assumptions": [
                "Plans are ranked by conservative risk-adjusted time, not raw fresh-tyre pace alone.",
                "Live and historical personal wear/deg data override track defaults as samples accumulate.",
                "A dry Race plan must finish with at least two different dry visual compounds unless inters or wets are used.",
                "SC/VSC loss is an estimate; pit-entry position, traffic and field compression are recalculated from live state.",
            ],
        }

    async def get_plan(self) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        current = state.get("strategy", {})
        if current.get("recommended") or current.get("available") is False:
            return current
        return await self.recompute()

    async def predict_rival_strategy(self, top_n: int = 6) -> dict[str, Any]:
        """Estimate each nearby rival's likely next stop and undercut threat.

        The estimate is deterministic: a rival on a compound with typical stint
        life L who is A laps into that set is projected to stop in L - A laps.
        Anyone projected to stop within a couple of laps who sits just behind is
        flagged as an undercut threat before they actually box.
        """
        state = await self.store.snapshot_analysis()
        current_lap = int(state.get("current_lap", 0))
        total_laps = int(state.get("total_laps", 0))
        player_pos = int(state.get("player_position", 0))
        rivals: list[dict[str, Any]] = []
        for driver in state.get("drivers", []):
            if int(driver.get("car_idx", -1)) == int(state.get("player_car_index", -1)):
                continue
            gap = driver.get("gap_to_player_s")
            compound = str(driver.get("tyre_compound", "UNKNOWN")).upper()
            age = int(driver.get("tyre_age", 0))
            typical = TYPICAL_STINT_LAPS.get(compound)
            laps_to_stop = max(0, typical - age) if typical else None
            projected_stop_lap = (
                current_lap + laps_to_stop
                if laps_to_stop is not None and current_lap > 0
                else None
            )
            # gap_to_player_s is positive for a car behind, negative for a car
            # ahead. A car just behind on dying tyres is the classic undercut.
            behind = gap is not None and float(gap) > 0
            # A car just behind on tyres near the end of their life is the
            # classic pre-emptive undercut threat.
            undercut_threat = bool(
                behind
                and laps_to_stop is not None
                and laps_to_stop <= 2
                and abs(float(gap)) <= 3.0
            )
            rivals.append(
                {
                    "driver": driver.get("name"),
                    "position": driver.get("position"),
                    "gap_to_player_s": round(float(gap), 2) if gap is not None else None,
                    "compound": compound,
                    "tyre_age": age,
                    "typical_stint_laps": typical,
                    "laps_until_estimated_stop": laps_to_stop,
                    "projected_stop_lap": (
                        projected_stop_lap
                        if projected_stop_lap is None or projected_stop_lap <= total_laps or total_laps == 0
                        else None
                    ),
                    "undercut_threat": undercut_threat,
                }
            )
        rivals.sort(
            key=lambda item: abs(item["gap_to_player_s"])
            if item["gap_to_player_s"] is not None
            else 1e9
        )
        return {
            "available": bool(rivals),
            "player_position": player_pos,
            "current_lap": current_lap,
            "rivals": rivals[: max(1, top_n)],
            "note": (
                "Stop laps are estimated from typical compound life; the game AI "
                "may pit earlier or later."
            ),
        }

    async def championship_scenario(self) -> dict[str, Any]:
        """Attach F1 points to each ranked plan's projected finish position so
        the trade-off between a safe finish and an aggressive one is explicit.
        """
        current = await self.store.snapshot_analysis()
        strategy = current.get("strategy", {})
        plans = strategy.get("plans", []) or []
        player_pos = int(current.get("player_position", 0))
        scored: list[dict[str, Any]] = []
        for plan in plans:
            projected = int(
                plan.get("projected_rejoin_position", player_pos) or player_pos
            )
            scored.append(
                {
                    "instruction": plan.get("instruction"),
                    "stops_remaining": plan.get("stops_remaining"),
                    "projected_position": projected,
                    "projected_points": points_for_position(projected),
                    "risk_time_s": plan.get("projected_time_s"),
                    "confidence": plan.get("confidence"),
                }
            )
        current_points = points_for_position(player_pos)
        best_points = max((item["projected_points"] for item in scored), default=current_points)
        return {
            "available": bool(scored),
            "current_position": player_pos,
            "current_points_if_held": current_points,
            "best_projected_points": best_points,
            "plans": scored,
            "note": (
                "Points use the projected finishing position of each plan; a "
                "safe finish can outweigh a higher-variance gamble."
            ),
        }

    async def evaluate_undercut(self, driver: str = "ahead") -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        target = self._resolve_driver(state, driver)
        if target is None or target.get("gap_to_player_s") is None:
            return {
                "available": False,
                "reason": "Target driver or gap is unavailable.",
            }
        gap = abs(float(target["gap_to_player_s"]))
        historical = await self.database.tyre_history_model(
            int(state.get("track_id", -1))
        )
        own_deg, _, _ = self._deg_for(state, str(state["tyre"]["compound"]), historical)
        rival_age = int(target.get("tyre_age", 0))
        rival_compound = str(target.get("tyre_compound", "MEDIUM"))
        rival_deg = DEFAULT_DEG.get(rival_compound, 0.09)
        fresh_gain = max(0.35, own_deg * max(1, int(state["tyre"]["age_laps"])) + 0.45)
        two_lap_gain = 2 * (fresh_gain + rival_deg * rival_age)
        neutral = self._neutralisation(state)
        traffic_penalty = (
            0.5
            if self._rejoin_position(state, float(neutral["effective_pit_loss_s"]))
            > int(state.get("player_position", 0)) + 3
            else 0.0
        )
        margin = two_lap_gain - gap - traffic_penalty
        return {
            "available": True,
            "driver": target["name"],
            "gap_s": round(gap, 2),
            "estimated_two_lap_gain_s": round(two_lap_gain, 2),
            "traffic_penalty_s": round(traffic_penalty, 2),
            "margin_s": round(margin, 2),
            "verdict": "undercut is on"
            if margin > 0.4
            else "undercut is marginal"
            if margin > -0.4
            else "undercut is unlikely",
            "required": "Clean in-lap, legal compound, no traffic on rejoin, and a strong out-lap.",
            "confidence": "medium" if target.get("lap_history") else "low",
        }

    async def evaluate_overcut(self, driver: str = "ahead") -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        target = self._resolve_driver(state, driver)
        if target is None or target.get("gap_to_player_s") is None:
            return {
                "available": False,
                "reason": "Target driver or gap is unavailable.",
            }
        historical = await self.database.tyre_history_model(
            int(state.get("track_id", -1))
        )
        current_wear = max(state["tyre"]["wear"] or [0])
        current_deg, _, _ = self._deg_for(
            state, str(state["tyre"]["compound"]), historical
        )
        clean_air_gain = 0.5 if abs(float(target["gap_to_player_s"])) < 2.0 else 0.2
        extra_lap_cost = (
            current_deg * max(1, state["tyre"]["age_laps"])
            + max(0.0, current_wear - 65) * 0.03
        )
        margin = clean_air_gain - extra_lap_cost
        return {
            "available": True,
            "driver": target["name"],
            "margin_s_per_extra_lap": round(margin, 2),
            "verdict": "overcut is viable"
            if margin > 0.15
            else "overcut is marginal"
            if margin > -0.15
            else "overcut is not advised",
            "required": "The rival must rejoin in traffic and your current tyre must remain stable.",
            "confidence": "low" if not target.get("lap_history") else "medium",
        }

    @staticmethod
    def _resolve_driver(state: dict[str, Any], driver: str) -> dict[str, Any] | None:
        query = driver.strip().lower()
        player_position = int(state.get("player_position", 0))
        if query in {"me", "myself", "player", "my car"}:
            index = int(state.get("player_car_index", 0))
            return next(
                (
                    item
                    for item in state.get("drivers", [])
                    if item.get("car_idx") == index
                ),
                None,
            )
        for item in state.get("drivers", []):
            if query == "ahead" and item.get("position") == player_position - 1:
                return item
            if query == "behind" and item.get("position") == player_position + 1:
                return item
            if query == "leader" and item.get("position") == 1:
                return item
            if (
                query == f"p{item.get('position')}"
                or query in item.get("name", "").lower()
            ):
                return item
        return None

    async def what_if(self, scenario: str) -> dict[str, Any]:
        state = await self.store.snapshot_analysis()
        historical = await self.database.tyre_history_model(
            int(state.get("track_id", -1))
        )
        base = self.compute(state, historical)
        text = scenario.lower()
        compound = next(
            (
                name
                for name in ("SOFT", "MEDIUM", "HARD", "INTER", "WET")
                if name.lower() in text
            ),
            None,
        )
        match = re.search(r"(?:lap\s*)?(\d{1,2})", text)
        requested_lap = (
            int(match.group(1)) if match else int(state.get("current_lap", 0)) + 1
        )
        requested_lap = max(int(state.get("current_lap", 0)), requested_lap)
        if compound is None:
            compound = base.get("recommended", {}).get("fit_compound") or "HARD"
        current_lap = int(state.get("current_lap", 0))
        total_laps = int(state.get("total_laps", 0))
        remaining = max(
            0,
            total_laps - current_lap + (1 if current_lap > 0 else 0),
        )
        first_laps = max(1, requested_lap - current_lap + 1)
        if first_laps >= remaining:
            return {
                "available": False,
                "reason": "That stop is after the race distance.",
            }
        style_factor, evidence = self._driver_wear_factor(state, historical)
        base_lap = self._estimate_base_lap_s(state)
        pre = self._simulate_stint(
            state,
            str(state["tyre"]["compound"]),
            first_laps,
            int(state["tyre"]["age_laps"]),
            max(state["tyre"]["wear"] or [0]),
            base_lap,
            historical,
            style_factor,
            None,
        )
        post = self._simulate_stint(
            state,
            compound,
            remaining - first_laps,
            0,
            0.0,
            base_lap,
            historical,
            style_factor,
            self._available_sets(state).get(compound),
        )
        neutral = self._neutralisation(state)
        total = (
            float(pre["expected_time_s"])
            + float(neutral["effective_pit_loss_s"])
            + float(post["expected_time_s"])
        )
        risk = (
            float(pre["conservative_time_s"])
            + float(neutral["effective_pit_loss_s"])
            + float(post["conservative_time_s"])
        )
        best_time = float(base.get("recommended", {}).get("risk_adjusted_time_s", risk))
        legality = self._compound_rule(state, [compound])
        return {
            "available": True,
            "scenario": f"Box lap {requested_lap} for {compound}",
            "projected_time_s": round(total, 2),
            "risk_adjusted_time_s": round(risk, 2),
            "delta_to_best_s": round(risk - best_time, 2),
            "projected_finish_wear_pct": round(
                float(post["projected_finish_wear_pct"]), 1
            ),
            "feasible": bool(
                pre["feasible"] and post["feasible"] and legality["compliant"]
            ),
            "compound_rule": legality,
            "personal_wear_model": evidence,
        }
