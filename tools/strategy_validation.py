"""Independent finite-resource strategy benchmark; no production scoring imports.

Run this *same file* against both revisions, for example::

    python tools/strategy_validation.py --source /path/to/baseline --output /tmp/base.json
    python tools/strategy_validation.py --source /path/to/candidate --output /tmp/new.json

Only EngineAdapter imports production code. The physical world, exact oracle,
plan scoring and summary use the standard library. No user database, credentials,
provider calls or real UDP endpoint is used. See the frozen manifest for limits.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
import random
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, replace
from functools import cache
from pathlib import Path
from typing import Any

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "docs/strategy-validation-manifest.json"
FAMILIES = ("linear", "finite_sets", "traffic", "quadratic", "wear_cliff", "weather")


@dataclass(frozen=True)
class PhysicalSet:
    index: int
    compound: str
    wear: float
    wear_rate: float
    age: int
    pace_delta_s: float
    degradation_s: float
    curvature_s: float = 0.0
    cliff_at_pct: float = 100.0
    cliff_s_per_pct: float = 0.0
    usable_laps: int = 100


@dataclass(frozen=True)
class World:
    name: str
    seed: int
    family: str
    split: str
    horizon: int
    sets: tuple[PhysicalSet, ...]
    pit_loss_s: float
    rain: tuple[bool, ...]
    traffic_delays_s: tuple[float, ...]
    current_lap: int = 11
    base_lap_s: float = 90.0
    wear_limit_pct: float = 90.0
    cold_cost_s: float = 0.8


def lap_cost(world: World, tyre: PhysicalSet, used_laps: int, offset: int) -> float:
    """Physical cost of the next lap, independent of any production functions."""
    end_wear = tyre.wear + tyre.wear_rate * (used_laps + 1)
    if end_wear > world.wear_limit_pct or used_laps + 1 > tyre.usable_laps:
        return math.inf
    age = tyre.age + used_laps
    wet = world.rain[offset]
    weather_s = (0.8 if wet else 7.5) if tyre.compound == "INTER" else (8.5 if wet else 0.0)
    warmup_s = world.cold_cost_s * (1.0 if age == 0 else 0.5 if age == 1 else 0.0)
    return (
        world.base_lap_s + tyre.pace_delta_s + tyre.degradation_s * age
        + tyre.curvature_s * age * age
        + max(0.0, end_wear - tyre.cliff_at_pct) * tyre.cliff_s_per_pct
        + weather_s + warmup_s
    )


def exact_oracle(world: World) -> dict[str, Any]:
    """Enumerate all next-lap choices by dynamic programming, with no set reuse."""
    @cache
    def solve(offset: int, fitted: int, used_laps: int, used_mask: int):
        if offset == world.horizon:
            return 0.0, ()
        continue_lap = lap_cost(world, world.sets[fitted], used_laps, offset)
        tail, actions = solve(offset + 1, fitted, used_laps + 1, used_mask)
        best = (continue_lap + tail, actions)
        for index, tyre in enumerate(world.sets):
            if used_mask & (1 << index):
                continue
            first_lap = lap_cost(world, tyre, 0, offset)
            rest, subsequent = solve(offset + 1, index, 1, used_mask | (1 << index))
            candidate = (
                world.pit_loss_s + world.traffic_delays_s[offset] + first_lap + rest,
                ((offset, tyre.index),) + subsequent,
            )
            # Exact ties keep the existing plan, favoring fewer interventions.
            if candidate[0] < best[0] - 1e-9:
                best = candidate
        return best

    score, actions = solve(0, 0, 0, 1)
    return {
        "time_s": round(score, 6) if math.isfinite(score) else None,
        "stops": [{"offset": lap, "set_index": index} for lap, index in actions],
        "states_evaluated": solve.cache_info().currsize,
    }


def score_assignment(world: World, boxes: list[int], indices: list[int]) -> tuple[float, str]:
    if len(boxes) != len(indices):
        return math.inf, "stop/set count mismatch"
    if boxes != sorted(set(boxes)) or any(offset < 0 or offset >= world.horizon for offset in boxes):
        return math.inf, "stop timing outside the remaining race or duplicate stop lap"
    available = {tyre.index: tyre for tyre in world.sets}
    if len(set(indices)) != len(indices) or world.sets[0].index in indices:
        return math.inf, "physical set reused"
    if any(index not in available for index in indices):
        return math.inf, "unavailable physical set"
    stops = dict(zip(boxes, indices))
    tyre = world.sets[0]
    used_laps = 0
    total = 0.0
    for offset in range(world.horizon):
        if offset in stops:
            tyre = available[stops[offset]]
            used_laps = 0
            total += world.pit_loss_s + world.traffic_delays_s[offset]
        cost = lap_cost(world, tyre, used_laps, offset)
        if not math.isfinite(cost):
            return math.inf, f"set {tyre.index} exceeds physical life at remaining lap {offset + 1}"
        total += cost
        used_laps += 1
    return total, ""


def score_plan(world: World, plan: dict[str, Any]) -> dict[str, Any]:
    """Score emitted allocations exactly; legacy compounds get optimistic matching."""
    if not plan:
        return {"time_s": None, "physical_feasible": False, "reason": "no recommendation"}
    boxes = [int(lap) - world.current_lap for lap in plan.get("box_laps", [])]
    compounds = list(plan.get("compounds", []))
    if len(compounds) != len(boxes) + 1 or compounds[0] != world.sets[0].compound:
        return {"time_s": None, "physical_feasible": False, "reason": "inconsistent compound/stint sequence"}
    explicit = plan.get("tyre_set_indices")
    if explicit is not None and all(index is not None for index in explicit):
        assignments = [list(explicit)]
        allocation_source = "explicit production set indices"
    else:
        candidates = [[tyre.index for tyre in world.sets[1:] if tyre.compound == compound]
                      for compound in compounds[1:]]
        assignments = itertools.product(*candidates)
        allocation_source = "optimistic unique-set matching for legacy compounds"
    by_index = {tyre.index: tyre for tyre in world.sets}
    best = math.inf
    selected = None
    reason = "no matching physical sets"
    for assignment in assignments:
        if len(assignment) != len(compounds) - 1 or any(
            index not in by_index or by_index[index].compound != compound
            for index, compound in zip(assignment, compounds[1:])
        ):
            reason = "allocated set does not match the stated compound"
            continue
        score, failure = score_assignment(world, boxes, list(assignment))
        if failure:
            reason = failure
        if score < best:
            best, selected, reason = score, list(assignment), ""
    return {
        "time_s": round(best, 6) if math.isfinite(best) else None,
        "physical_feasible": math.isfinite(best), "reason": reason,
        "set_indices": selected, "allocation_source": allocation_source,
    }


def build_world(seed: int, family: str, manifest: dict[str, Any]) -> World:
    # A family-specific stable seed, with no dependence on execution order/hash().
    family_seed = int.from_bytes(hashlib.sha256(f"{seed}/{family}".encode()).digest()[:8], "big")
    rng = random.Random(family_seed)
    horizon = rng.choice((12, 15, 18))
    wear_scale = rng.uniform(0.85, 1.15)
    current = PhysicalSet(0, "MEDIUM", rng.uniform(30, 45), 2.8 * wear_scale,
                          8, 0.0, rng.uniform(0.11, 0.20), usable_laps=18)
    soft = PhysicalSet(1, "SOFT", rng.uniform(0, 8), 4.1 * wear_scale,
                       0, -0.7, rng.uniform(0.15, 0.26), usable_laps=16)
    hard = PhysicalSet(2, "HARD", rng.uniform(0, 12), 1.8 * wear_scale,
                       0, 0.55, rng.uniform(0.04, 0.10), usable_laps=24)
    medium = PhysicalSet(3, "MEDIUM", rng.uniform(0, 15), current.wear_rate,
                         0, 0.0, current.degradation_s, usable_laps=19)
    tyres = [current, soft, hard, medium]
    rain = [False] * horizon
    traffic = [0.0] * horizon
    if family == "finite_sets":
        tyres = [replace(current, usable_laps=2, wear=78, wear_rate=4.0),
                 replace(soft, usable_laps=5), replace(hard, usable_laps=6),
                 replace(medium, usable_laps=6)]
    elif family == "traffic":
        # A compact train clears part-way through this particular generated race.
        clearance = rng.randint(3, 7)
        traffic = [7.0 if offset < clearance else 0.5 for offset in range(horizon)]
    elif family == "quadratic":
        tyres = [replace(tyre, curvature_s=rng.uniform(0.008, 0.022)) for tyre in tyres]
    elif family == "wear_cliff":
        tyres = [replace(tyre, cliff_at_pct=rng.uniform(57, 67), cliff_s_per_pct=0.23)
                 for tyre in tyres]
        tyres[0] = replace(tyres[0], wear=65.0)
    elif family == "weather":
        arrival = rng.randint(2, 6)
        rain = [offset >= arrival for offset in range(horizon)]
        tyres.append(PhysicalSet(4, "INTER", 0.0, 2.0, 0, 0.0, 0.10, usable_laps=24))
    split = "training" if seed in manifest["training_seeds"] and family in manifest["training_families"] else "heldout"
    return World(f"{seed}-{family}", seed, family, split, horizon, tuple(tyres),
                 22.5, tuple(rain), tuple(traffic))


def world_state(world: World) -> tuple[dict[str, Any], dict[str, Any]]:
    fitted = world.sets[0]
    current_wet = world.rain[0]
    drivers = [{"car_idx": 0, "is_player": True, "active": True, "position": 4,
                "name": "Synthetic player", "tyre_compound": fitted.compound,
                "tyre_age": fitted.age, "best_lap_ms": int(world.base_lap_s * 1000),
                "tyre_stints": [{"compound": "HARD", "start_lap": 1, "end_lap": 2},
                                 {"compound": fitted.compound, "start_lap": 3, "end_lap": 255}]}]
    if world.family == "traffic":
        drivers += [{"car_idx": index, "active": True, "position": index + 4,
                     "name": f"Synthetic rival {index}", "gap_to_player_s": 1.0 + index * 0.6,
                     "last_lap_ms": 90200, "best_lap_ms": 90000, "current_lap": world.current_lap,
                     "tyre_compound": "HARD", "tyre_age": 5, "tyre_wear": [15.0] * 4,
                     "lap_history": [{"lap_time_ms": 90200, "valid": True}] * 4}
                    for index in range(1, 7)]
    state = {
        "session_uid": world.seed, "session_type": "Race", "mode_profile": "race",
        "connected": True, "current_lap": world.current_lap,
        "total_laps": world.current_lap + world.horizon - 1,
        "session_time_s": (world.current_lap - 1) * world.base_lap_s,
        "track_id": -1, "track_name": "Independent synthetic circuit",
        "track_length_m": 5000, "lap_distance_m": 1000,
        "player_car_index": 0, "player_position": 4, "active_cars": 10,
        "speed_kph": 220, "weather": "Light rain" if current_wet else "Clear",
        "rain_now_pct": 100 if current_wet else 0, "rain_next_15_pct": 100 if any(world.rain) else 0,
        "rain_next_30_pct": 100 if any(world.rain) else 0, "weather_forecast": [],
        "race_control_phase": "green", "safety_car": "none", "strategy": {},
        "track_temp_c": 32, "air_temp_c": 24,
        "tyre": {"compound": fitted.compound, "age_laps": fitted.age,
                 "wear": [fitted.wear] * 4, "inner_temps_c": [92] * 4},
        "tyre_sets": [{"index": tyre.index, "compound": tyre.compound,
                       "available": True, "fitted": index == 0, "wear_pct": tyre.wear,
                       # EA lifeSpan is laps LEFT on this physical set;
                       # usableLife is the recommended TOTAL compound stint.
                       "usable_life_laps": tyre.usable_laps + tyre.age,
                       "life_span_laps": tyre.usable_laps,
                       "lap_delta_ms": 0} for index, tyre in enumerate(world.sets)],
        "drivers": drivers, "car_setup": {}, "analysis": {},
        "completed_laps": [{"lap_num": 1, "lap_time_ms": 90000, "compound": "HARD", "valid": True},
                           {"lap_num": 9, "lap_time_ms": 90000, "compound": fitted.compound, "valid": True}],
    }
    if any(world.rain) and not current_wet:
        arrival = next(index for index, wet in enumerate(world.rain) if wet)
        state["weather_forecast"] = [{"time_offset_min": arrival * world.base_lap_s / 60,
                                      "weather": "Light rain", "rain_pct": 100}]
    # Synthetic summaries expose linear terms, but do not leak nonlinear future truth.
    historical = {"compounds": {tyre.compound: {
        "slope_s_per_lap": tyre.degradation_s, "sample_size": 12,
        "max_wear_per_lap_pct": tyre.wear_rate, "wear_sample_size": 12,
        "wheel_wear_per_lap_pct": [tyre.wear_rate] * 4,
    } for tyre in reversed(world.sets)}}
    return state, historical


class EngineAdapter:
    def __init__(self, source: Path):
        sys.path.insert(0, str(source / "src"))
        from pitwall.strategy import StrategyEngine
        self.engine_type = StrategyEngine

    def new_engine(self):
        # compute() is a pure model boundary; no database method is called.
        return self.engine_type(None, None)

    def compute(self, engine, state, history):
        start = time.perf_counter()
        result = engine.compute(copy.deepcopy(state), copy.deepcopy(history))
        return result, (time.perf_counter() - start) * 1000


def output_contracts(result: dict[str, Any], state: dict[str, Any]) -> list[str]:
    errors = []
    try:
        json.dumps(result, allow_nan=False)
    except (ValueError, TypeError) as exc:
        errors.append(f"invalid JSON: {exc}")
    recommendation = result.get("recommended") or {}
    if recommendation.get("feasible") is False and recommendation.get("instruction") == "Stay out to the finish.":
        errors.append("infeasible recommendation carries an unconditional finish instruction")
    horizon = state["total_laps"] - state["current_lap"] + 1
    points = [0, 8, 7, 6, 5, 4, 3, 2, 1] if state["mode_profile"] == "sprint" else [0, 25, 18, 15, 12, 10, 8, 6, 4, 2, 1]
    plans = list(result.get("plans", []))
    if result.get("recommended"):
        plans += [result["recommended"]]
    for index, plan in enumerate(plans):
        stints = plan.get("stint_models", [])
        if stints and sum(stint.get("laps", len(stint.get("lap_times_s", []))) for stint in stints) != horizon:
            errors.append(f"plan {index}: stint lap totals differ from remaining distance")
        if len(plan.get("box_laps", [])) != plan.get("stops_remaining"):
            errors.append(f"plan {index}: stop count disagrees with stop laps")
        probs = plan.get("position_probabilities") or {}
        if probs:
            tolerance = max(0.002, len(probs) * 0.00051)
            if abs(sum(probs.values()) - 1.0) > tolerance or any(value < 0 or value > 1 for value in probs.values()):
                errors.append(f"plan {index}: position probabilities invalid")
            parsed = [(int(str(pos).removeprefix("P")), prob) for pos, prob in probs.items()]
            expected = sum(prob * (points[pos] if 0 < pos < len(points) else 0)
                           for pos, prob in parsed)
            if "points_expected" in plan and abs(expected - plan["points_expected"]) > 0.03:
                errors.append(f"plan {index}: expected points disagree with session scoring distribution")
    return sorted(set(errors))


def compact_plan(plan: dict[str, Any]) -> dict[str, Any]:
    keys = ("box_laps", "compounds", "stops_remaining", "tyre_set_indices", "inventory_status",
            "inventory_feasible", "feasible", "legal", "instruction", "projected_time_s",
            "projected_finish_position", "position_probabilities", "points_expected")
    return {key: plan[key] for key in keys if key in plan}


def evaluate_world(adapter: EngineAdapter, world: World) -> dict[str, Any]:
    state, history = world_state(world)
    result, runtime = adapter.compute(adapter.new_engine(), state, history)
    selected = result.get("recommended", {})
    score = score_plan(world, selected)
    oracle = exact_oracle(world)
    regret = score["time_s"] - oracle["time_s"] if score["time_s"] is not None and oracle["time_s"] is not None else None
    return {
        "name": world.name, "family": world.family, "split": world.split,
        "world": asdict(world), "state": state, "history": history,
        "recommended": compact_plan(selected), "score": score, "oracle": oracle,
        "regret_s": round(regret, 6) if regret is not None else None,
        "runtime_ms": round(runtime, 3), "contracts": output_contracts(result, state),
        "reported_feasible_but_physically_impossible": bool(selected.get("feasible") and not score["physical_feasible"]),
    }


def closed_loop(adapter: EngineAdapter, world: World) -> dict[str, Any]:
    """Replan from physical state; model decisions only, excluding wall-clock radio hold."""
    engine = adapter.new_engine()
    remaining_sets = list(world.sets)
    fitted = remaining_sets.pop(0)
    used_on_set = 0
    total = 0.0
    decisions = []
    changes = 0
    previous_future = None
    failure = ""
    for offset in range(world.horizon):
        live_fitted = replace(fitted, age=fitted.age + used_on_set,
                              wear=fitted.wear + fitted.wear_rate * used_on_set,
                              usable_laps=fitted.usable_laps - used_on_set)
        suffix = replace(world, horizon=world.horizon - offset,
                         current_lap=world.current_lap + offset,
                         sets=tuple([live_fitted] + remaining_sets),
                         rain=world.rain[offset:], traffic_delays_s=world.traffic_delays_s[offset:])
        state, history = world_state(suffix)
        result, runtime = adapter.compute(engine, state, history)
        recommendation = result.get("recommended", {})
        boxes = recommendation.get("box_laps", [])
        compounds = recommendation.get("compounds", [])
        signature = (boxes[0], compounds[1]) if boxes and len(compounds) > 1 else None
        if previous_future is not None and previous_future[0] > suffix.current_lap and signature != previous_future:
            changes += 1
        previous_future = signature
        chosen_index = None
        if signature and signature[0] <= suffix.current_lap:
            ids = recommendation.get("tyre_set_indices")
            chosen_index = ids[0] if ids and ids[0] is not None else None
            candidates = [tyre for tyre in remaining_sets if tyre.compound == signature[1]
                          and (chosen_index is None or tyre.index == chosen_index)]
            if not candidates:
                failure = "recommendation requested an unavailable set"
                break
            # Legacy output has no set identity; grant the lowest wear matching set.
            fitted = min(candidates, key=lambda tyre: (tyre.wear, tyre.index))
            remaining_sets.remove(fitted)
            chosen_index, used_on_set = fitted.index, 0
            total += world.pit_loss_s + world.traffic_delays_s[offset]
            previous_future = None
        cost = lap_cost(world, fitted, used_on_set, offset)
        decisions.append({"lap": suffix.current_lap, "plan": compact_plan(recommendation),
                          "executed_set_index": chosen_index, "runtime_ms": round(runtime, 3),
                          "lap_time_s": round(cost, 6) if math.isfinite(cost) else None})
        if not math.isfinite(cost):
            failure = "executed tyre exceeded physical wear or usable-life limit"
            break
        total += cost
        used_on_set += 1
    oracle = exact_oracle(world)
    return {"name": world.name, "failure": failure, "completed": not failure,
            "recommendation_changes_before_execution": changes, "checkpoints": decisions,
            "time_s": round(total, 6) if not failure else None,
            "regret_s": round(total - oracle["time_s"], 6) if not failure and oracle["time_s"] is not None else None}


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    low, high = math.floor(index), math.ceil(index)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (index - low), 3)


def summarize(rows: list[dict[str, Any]], loops: list[dict[str, Any]]) -> dict[str, Any]:
    grouped = {}
    for label in ("all", "training", "heldout", *FAMILIES):
        selected = [row for row in rows if label == "all" or row["family"] == label or row["split"] == label]
        regrets = [row["regret_s"] for row in selected if row["regret_s"] is not None]
        grouped[label] = {
            "worlds": len(selected), "physically_impossible": sum(not row["score"]["physical_feasible"] for row in selected),
            "reported_feasible_but_impossible": sum(row["reported_feasible_but_physically_impossible"] for row in selected),
            "finite_regret_count": len(regrets), "median_regret_s": percentile(regrets, 0.5),
            "p90_regret_s": percentile(regrets, 0.9),
            "contract_failures": sum(bool(row["contracts"]) for row in selected),
            "p95_runtime_ms": percentile([row["runtime_ms"] for row in selected], 0.95),
        }
    return {"groups": grouped,
            "closed_loop": {"runs": len(loops), "failures": sum(bool(run["failure"]) for run in loops),
                            "churn": sum(run["recommendation_changes_before_execution"] for run in loops),
                            "checkpoints": sum(len(run["checkpoints"]) for run in loops)}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Revision worktree whose src/pitwall is evaluated")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-closed-loop", action="store_true")
    parser.add_argument("--only", help="One family, useful for reproducing a failure")
    args = parser.parse_args()
    manifest_bytes = MANIFEST_PATH.read_bytes()
    manifest = json.loads(manifest_bytes)
    adapter = EngineAdapter(args.source.resolve())
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=args.source, capture_output=True, text=True, check=True).stdout.strip()
    source_diff = subprocess.run(["git", "diff", "HEAD", "--", "src"], cwd=args.source,
                                 capture_output=True, check=True).stdout
    rows = []
    for seed in manifest["training_seeds"] + manifest["heldout_seeds"]:
        for family in FAMILIES:
            if args.only and args.only != family:
                continue
            row = evaluate_world(adapter, build_world(seed, family, manifest))
            rows.append(row)
            print(f"{row['name']}: physical={row['score']['physical_feasible']} regret={row['regret_s']} runtime_ms={row['runtime_ms']}", flush=True)
    loops = [] if args.skip_closed_loop else [
        closed_loop(adapter, build_world(manifest["closed_loop_seed"], family, manifest))
        for family in manifest["closed_loop_families"] if not args.only or args.only == family]
    report = {"evaluated_commit": revision, "source_has_uncommitted_changes": bool(source_diff),
              "source_diff_sha256": hashlib.sha256(source_diff).hexdigest(),
              "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
              "manifest": manifest, "summary": summarize(rows, loops), "worlds": rows, "closed_loop": loops}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report["summary"], indent=2))
    # Findings are data, not harness execution failures. Release gates consume them.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
