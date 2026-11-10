"""Possible races from here to the flag, shared by every plan.

The Monte Carlo perturbed one deterministic race with tyre, pit and traffic
noise: no safety car, one weather outcome, rivals on one fixed schedule. The
risk appetite therefore chose between plans on the spread of tyre-model noise,
not on exposure to a safety car or a shower. Each draw here is one possible
race: whether and when a safety car or a VSC comes out, which weather
scenario arrives, and when each rival actually stops. Every plan is judged
against the same draws.

This is the first stage. A stop that happens to fall under a neutralisation
is cheaper, for the player and for each rival; a weather scenario changes
each tyre's cost lap by lap. A plan's own reaction (moving a stop to take a
safety car) and field compression at a restart are not simulated yet.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import rain

# Per-lap chance a safety car comes out, when the circuit has no measured
# rate: a judgement, doubled at street circuits.
SAFETY_CAR_HAZARD_PRIOR = 0.02
STREET_CIRCUIT_HAZARD_PRIOR = 0.04
STREET_CIRCUITS = frozenset({0, 5, 6, 12, 20, 29, 30, 31})
# A VSC comes out at half the safety car's rate.
VSC_HAZARD_SHARE = 0.5
# Laps a neutralisation lasts: a safety car three, a VSC one or two.
SAFETY_CAR_LAPS = 3
VSC_LAPS = (1, 2)
# Share of the green-flag pit loss a stop costs under each, as the planner
# prices a stop made now (StrategyEngine._neutralisation).
SAFETY_CAR_LOSS_SHARE = 0.46
VSC_LOSS_SHARE = 0.64
# A rival's projected stop lap is uncertain by a lap either way.
RIVAL_STOP_JITTER_LAPS = 1

NONE, SAFETY_CAR, VSC = 0, 1, 2


def hazard_for(state: dict[str, Any]) -> tuple[float, str]:
    """Safety cars a lap at this circuit, and where the figure came from."""
    from .circuit_model import measured

    track = int(state.get("track_id", -1))
    prior = STREET_CIRCUIT_HAZARD_PRIOR if track in STREET_CIRCUITS else SAFETY_CAR_HAZARD_PRIOR
    return measured(state.get("circuit_model"), "safety_car_per_lap", prior)


def _seed(state: dict[str, Any]) -> int:
    material = repr((
        int(state.get("session_uid", 0)), int(state.get("restart_epoch", 0)),
        int(state.get("timeline_epoch", 0)), int(state.get("current_lap", 0)),
    )).encode("utf-8")
    return zlib.crc32(material) & 0xFFFFFFFF


def _compound_by_lap(start: str, changes: list[tuple[int, str]], laps: int) -> list[str]:
    """The tyre on the car for each lap offset, given (offset of the first lap on it, compound)."""
    sequence = [str(start).upper()] * laps
    for offset, compound in sorted(changes):
        for index in range(max(0, int(offset)), laps):
            sequence[index] = str(compound).upper()
    return sequence


@dataclass
class RaceSimulation:
    """The draws for one compute, and what they do to a plan and to the field."""

    samples: int
    current_lap: int
    remaining: int
    neutral_kind: np.ndarray            # per draw: NONE, SAFETY_CAR or VSC
    neutral_start: np.ndarray           # lap offset it comes out on (>= 1), or -1
    neutral_laps: np.ndarray            # how many laps it lasts
    scenario: np.ndarray                # per draw: weather scenario index
    scenario_trajectories: list[list[float]]
    scenario_probabilities: list[float]
    dry_base_lap_s: float
    hazard: float
    hazard_source: str
    rival_jitter: np.ndarray            # (draws, rivals) laps added to each projected stop
    rival_times: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))  # (rivals, draws)

    @classmethod
    def build(
        cls,
        state: dict[str, Any],
        rivals: list[dict[str, Any]],
        weather: dict[str, Any] | None,
        remaining: int,
        samples: int,
        *,
        hazard: float | None = None,
    ) -> RaceSimulation:
        samples = max(80, min(1200, int(samples)))
        rng = np.random.default_rng(_seed(state) ^ 0x51AC)
        measured_hazard, source = hazard_for(state)
        rate = float(measured_hazard if hazard is None else hazard)
        if hazard is not None:
            source = "given"
        remaining = max(0, int(remaining))
        # The first neutralisation from the next lap on: one per race here.
        total = rate * (1.0 + VSC_HAZARD_SHARE)
        start = np.full(samples, -1, dtype=int)
        kind = np.full(samples, NONE, dtype=int)
        if total > 0 and remaining > 1:
            first = rng.geometric(min(1.0, total), size=samples)  # 1, 2, ...
            happens = first < remaining
            start = np.where(happens, first, -1)
            is_vsc = rng.random(samples) < (VSC_HAZARD_SHARE / (1.0 + VSC_HAZARD_SHARE))
            kind = np.where(happens, np.where(is_vsc, VSC, SAFETY_CAR), NONE)
        vsc_laps = rng.integers(VSC_LAPS[0], VSC_LAPS[1] + 1, size=samples)
        laps = np.where(kind == SAFETY_CAR, SAFETY_CAR_LAPS, np.where(kind == VSC, vsc_laps, 0))
        scenarios = list((weather or {}).get("scenarios") or [])
        trajectories = [[float(v) for v in item.get("trajectory") or []] for item in scenarios]
        probabilities = [float(item.get("probability", 0.0) or 0.0) for item in scenarios]
        if scenarios and sum(probabilities) > 0:
            # Scenarios are ordered by rank: one uniform draw picks one.
            cumulative = np.cumsum(np.asarray(probabilities) / sum(probabilities))
            pick = np.searchsorted(cumulative, rng.random(samples), side="right")
            pick = np.minimum(pick, len(scenarios) - 1)
        else:
            pick = np.zeros(samples, dtype=int)
        jitter = rng.integers(-RIVAL_STOP_JITTER_LAPS, RIVAL_STOP_JITTER_LAPS + 1, size=(samples, len(rivals)))
        simulation = cls(
            samples=samples, current_lap=int(state.get("current_lap", 0) or 0), remaining=remaining,
            neutral_kind=kind, neutral_start=start, neutral_laps=laps,
            scenario=pick, scenario_trajectories=trajectories, scenario_probabilities=probabilities,
            dry_base_lap_s=float((weather or {}).get("dry_base_lap_s") or (weather or {}).get("lap_s") or 0.0),
            hazard=rate, hazard_source=source, rival_jitter=jitter,
        )
        simulation.rival_times = simulation._field(rivals)
        return simulation

    # --- what a draw does to one car -----------------------------------------------

    def _neutral_saving(self, stop_offsets: np.ndarray, costs: np.ndarray) -> np.ndarray:
        """Per draw, what the car's stops save by falling under the neutralisation.

        ``stop_offsets`` is (draws, stops): the lap offset each stop is made
        at the end of. A stop made on a neutralised lap costs its share.
        """
        if stop_offsets.size == 0:
            return np.zeros(self.samples)
        start = self.neutral_start[:, None]
        end = start + self.neutral_laps[:, None] - 1
        under = (self.neutral_kind[:, None] != NONE) & (stop_offsets >= start) & (stop_offsets <= end)
        share = np.where(self.neutral_kind == SAFETY_CAR, SAFETY_CAR_LOSS_SHARE, VSC_LOSS_SHARE)[:, None]
        return np.sum(np.where(under, costs[None, :] * (1.0 - share), 0.0), axis=1)

    def _weather_delta(self, sequence: list[str]) -> np.ndarray:
        """Per draw, the drawn scenario's cost for this tyre sequence against the expected one."""
        if not self.scenario_trajectories or not self.dry_base_lap_s:
            return np.zeros(self.samples)
        costs = []
        for trajectory in self.scenario_trajectories:
            cost = 0.0
            for index, compound in enumerate(sequence):
                surface = trajectory[min(index, len(trajectory) - 1)] if trajectory else 0.0
                cost += rain.lap_penalty_fraction(compound, surface)
            costs.append(cost * self.dry_base_lap_s)
        weights = np.asarray(self.scenario_probabilities, dtype=float)
        weights = weights / weights.sum() if weights.sum() > 0 else np.full(len(costs), 1.0 / len(costs))
        values = np.asarray(costs)
        return values[self.scenario] - float(np.dot(weights, values))

    def plan_adjustment(self, plan: dict[str, Any], start_compound: str) -> np.ndarray:
        """Per draw, seconds added to the plan's projected time by the race drawn."""
        boxes = [int(lap) for lap in plan.get("box_laps") or []]
        compounds = list(plan.get("compounds") or [start_compound])
        costs = [float(cost) for cost in plan.get("pit_stop_costs_s") or []]
        offsets = np.asarray([[lap - self.current_lap for lap in boxes]] * self.samples, dtype=int).reshape(
            self.samples, len(boxes))
        saving = self._neutral_saving(offsets, np.asarray(costs[:len(boxes)] + [0.0] * (len(boxes) - len(costs))))
        changes = [(lap - self.current_lap + 1, compounds[index + 1])
                   for index, lap in enumerate(boxes) if index + 1 < len(compounds)]
        sequence = _compound_by_lap(compounds[0] if compounds else start_compound, changes, self.remaining)
        return self._weather_delta(sequence) - saving

    def _field(self, rivals: list[dict[str, Any]]) -> np.ndarray:
        """(rivals, draws): each rival's finish time in each drawn race."""
        rows = []
        for column, rival in enumerate(rivals):
            base = float(rival.get("finish_time_s", 1e9))
            stops = [int(offset) for offset in rival.get("likely_stop_offsets_laps") or []]
            costs = np.asarray([float(cost) for cost in rival.get("pit_stop_costs_s") or []][:len(stops)])
            if len(costs) < len(stops):
                costs = np.concatenate([costs, np.zeros(len(stops) - len(costs))])
            # A rival's stop at offset j is made before lap j: at the end of
            # lap offset j - 1, the player's convention.
            jitter = self.rival_jitter[:, column][:, None] if self.rival_jitter.size else 0
            offsets = (np.asarray(stops, dtype=int)[None, :] - 1 + jitter) if stops else np.zeros((self.samples, 0), dtype=int)
            saving = self._neutral_saving(offsets, costs)
            changes = list(zip(stops, rival.get("likely_stop_compounds") or [], strict=False))
            sequence = _compound_by_lap(str(rival.get("compound") or "MEDIUM"), changes, self.remaining)
            rows.append(base + self._weather_delta(sequence) - saving)
        return np.asarray(rows) if rows else np.zeros((0, self.samples))

    # --- what the plan's positions say -------------------------------------------------

    def summary(self) -> dict[str, Any]:
        neutralised = self.neutral_kind != NONE
        wet_end = self._wet_end()
        return {
            "draws": self.samples,
            "safety_car_hazard_per_lap": round(self.hazard, 4),
            "safety_car_hazard_source": self.hazard_source,
            "share_with_safety_car": round(float(np.mean(self.neutral_kind == SAFETY_CAR)), 3),
            "share_with_vsc": round(float(np.mean(self.neutral_kind == VSC)), 3),
            "share_neutralised": round(float(np.mean(neutralised)), 3),
            "share_wet_finish": round(float(np.mean(wet_end)), 3) if wet_end is not None else None,
            "stage": "neutralisations, weather scenarios and rival stop timing; no reaction to them yet",
        }

    def _wet_end(self) -> np.ndarray | None:
        if not self.scenario_trajectories:
            return None
        ends = np.asarray([
            (trajectory[-1] if trajectory else 0.0) >= rain.WETNESS_SLICK_INTER
            for trajectory in self.scenario_trajectories
        ])
        return ends[self.scenario]

    def sensitivities(self, positions: np.ndarray) -> dict[str, Any]:
        """Mean places a plan gains with a safety car, and loses in the rain."""
        result: dict[str, Any] = {"safety_car_swing": None, "rain_swing": None}
        neutral = self.neutral_kind != NONE
        if neutral.any() and (~neutral).any():
            # Positive: the plan finishes higher when one comes out.
            result["safety_car_swing"] = round(float(np.mean(positions[~neutral]) - np.mean(positions[neutral])), 2)
        wet = self._wet_end()
        if wet is not None and wet.any() and (~wet).any():
            # Positive: the plan finishes lower when the race ends wet.
            result["rain_swing"] = round(float(np.mean(positions[wet]) - np.mean(positions[~wet])), 2)
        return result


def finishing_chances(positions: np.ndarray, mode: str) -> dict[str, float]:
    """Chance of a win, a podium and points from a position distribution."""
    points_places = 8 if mode == "sprint" else 10
    total = max(1, len(positions))
    return {
        "p_win": round(int(np.count_nonzero(positions <= 1)) / total, 4),
        "p_podium": round(int(np.count_nonzero(positions <= 3)) / total, 4),
        "p_points": round(int(np.count_nonzero(positions <= points_places)) / total, 4),
    }

