"""Adversarial randomized scenario harness for the strategy engine.

Generates diverse race states, drives them through the real tool layer that
the live engineer uses, and checks each recommendation against ground truth
that can be verified without the game: arithmetic, internal consistency,
sporting-rule legality, and defensibility to a race engineer.

Run:
    python -m tools.strategy_fuzz --scenarios 400 --seed 7
    python -m tools.strategy_fuzz --scenarios 400 --seed 7 --verbose

Exit code is non-zero if any scenario produces a violation, so this can gate
a release. Seeds make every failure reproducible: rerun with --only <id>.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pitwall.analysis import AnalysisEngine  # noqa: E402
from pitwall.database import PitWallDatabase  # noqa: E402
from pitwall.setup_advisor import SetupAdvisor  # noqa: E402
from pitwall.state import DriverState, StateStore  # noqa: E402
from pitwall.strategy import StrategyEngine  # noqa: E402
from pitwall.tools import TelemetryTools  # noqa: E402

DRY_COMPOUNDS = ["SOFT", "MEDIUM", "HARD"]
WET_COMPOUNDS = ["INTER", "WET"]
ALL_COMPOUNDS = DRY_COMPOUNDS + WET_COMPOUNDS

# Track id -> (name, laps, base lap seconds). Spread of pit-loss profiles.
TRACKS = [
    (0, "Melbourne", 58, 80.0),
    (2, "Shanghai", 56, 94.0),
    (4, "Baku", 51, 103.0),
    (6, "Monaco", 78, 73.0),
    (7, "Montreal", 70, 74.0),
    (9, "Silverstone", 52, 88.0),
    (12, "Spa", 44, 106.0),
    (13, "Monza", 53, 82.0),
    (14, "Singapore", 62, 98.0),
    (19, "Mexico", 71, 78.0),
    (20, "Interlagos", 71, 71.0),
]

WEATHERS = [
    "Clear", "Light Cloud", "Overcast",
    "Light rain", "Heavy rain", "Storm",
]
WET_WEATHERS = {"light rain", "heavy rain", "storm"}

SAFETY_STATES = [
    ("none", "green"),
    ("full", "safety_car"),
    ("virtual", "vsc"),
    ("formation", "formation"),
    ("none", "red_flag"),
    ("full", "safety_car_ending"),
    ("virtual", "vsc_ending"),
]


@dataclass
class Violation:
    scenario_id: int
    check: str
    detail: str
    severity: str = "error"


@dataclass
class Scenario:
    id: int
    seed: int
    label: str
    setup: dict[str, Any]
    notes: list[str] = field(default_factory=list)


def build_scenario(index: int, rng: random.Random) -> Scenario:
    """One randomized but internally plausible race state."""
    track_id, track_name, default_laps, base_lap = rng.choice(TRACKS)
    mode = rng.choices(
        ["race", "race", "race", "race", "sprint", "qualifying", "practice"],
        weights=[45, 15, 10, 10, 8, 6, 6],
    )[0]
    total_laps = default_laps if mode == "race" else max(
        3, int(default_laps * rng.uniform(0.25, 0.45))
    )

    edge = rng.random()
    if edge < 0.10:
        current_lap = rng.choice([total_laps - 1, total_laps, total_laps + 0])
        note = "endgame: 1-2 laps remaining"
    elif edge < 0.16:
        current_lap = rng.choice([0, 1])
        note = "race start / formation"
    else:
        current_lap = rng.randint(1, max(1, total_laps))
        note = "midrace"

    wet = rng.random() < 0.3
    weather = rng.choice(
        [w for w in WEATHERS if (w.lower() in WET_WEATHERS) == wet]
    )
    rain_pct = rng.randint(60, 100) if wet else rng.randint(0, 45)
    if rng.random() < 0.12:  # mixed/transition
        rain_pct = rng.randint(40, 70)
        note += "; weather transition"

    safety_car, phase = rng.choices(
        SAFETY_STATES, weights=[55, 12, 12, 5, 4, 6, 6]
    )[0]

    compound = rng.choice(WET_COMPOUNDS if wet and rng.random() < 0.7 else ALL_COMPOUNDS)
    age = rng.randint(0, 45)
    wear_base = min(99.0, age * rng.uniform(1.2, 2.6))
    wear = [
        max(0.0, min(99.0, wear_base + rng.uniform(-8, 12))) for _ in range(4)
    ]
    if rng.random() < 0.10:
        wear = [rng.uniform(88, 99) for _ in range(4)]
        note += "; near-worn tyres"

    fuel_delta = rng.uniform(-3.5, 4.0)
    if rng.random() < 0.10:
        fuel_delta = rng.uniform(-2.5, -0.4)
        note += "; fuel short"

    sets: list[dict[str, Any]] = []
    for candidate in ALL_COMPOUNDS:
        if rng.random() < 0.7:
            sets.append(
                {
                    "compound": candidate,
                    "available": rng.random() < 0.85,
                    "wear_pct": rng.uniform(0, 60),
                    "usable_life_laps": rng.randint(5, 40),
                }
            )
    if not sets:
        sets = [{"compound": "HARD", "available": True, "wear_pct": 0.0}]

    used = rng.sample(DRY_COMPOUNDS, k=rng.randint(0, 2))

    position = rng.randint(1, 20)
    active_cars = rng.randint(max(position, 2), 22)

    drivers = _build_field(rng, active_cars, position, base_lap, compound, age, wear)

    setup: dict[str, Any] = {
        "session_type": {"race": "Race", "sprint": "Sprint", "qualifying": "Qualifying 1",
                         "practice": "Practice 1"}[mode],
        "mode_profile": mode,
        "track_id": track_id,
        "track_name": track_name,
        "current_lap": current_lap,
        "total_laps": total_laps,
        "player_position": position,
        "active_cars": active_cars,
        "weather": weather,
        "rain_next_15_pct": rain_pct,
        "safety_car": safety_car,
        "race_control_phase": phase,
        "fuel_laps_delta": round(fuel_delta, 2),
        "tyre_compound": compound,
        "tyre_age": age,
        "tyre_wear": [round(value, 1) for value in wear],
        "tyre_sets": sets,
        "used_compounds": used,
        "drivers": drivers,
        "player_car_index": 0,
        "base_lap_s": base_lap,
        "damage": _build_damage(rng),
        "game_strategy": {
            "game_ideal_lap": rng.randint(1, max(1, total_laps)),
            "game_latest_lap": rng.randint(1, max(1, total_laps)),
            "game_rejoin_position": rng.randint(1, active_cars),
        },
        "pit_lane_time_ms": rng.choice([0, 0, 18_000, 21_500, 24_000]),
    }
    return Scenario(index, 0, f"{track_name}/{mode}/{weather}/{phase}", setup, [note])


def _build_damage(rng: random.Random) -> dict[str, Any]:
    if rng.random() < 0.7:
        return {}
    return {
        "front_wing_left": rng.randint(0, 60),
        "front_wing_right": rng.randint(0, 60),
        "floor": rng.randint(0, 40),
        "diffuser": rng.randint(0, 40),
        "gearbox": rng.randint(0, 30),
        "engine": rng.randint(0, 30),
    }


def _build_field(
    rng: random.Random,
    active_cars: int,
    player_position: int,
    base_lap: float,
    player_compound: str,
    player_age: int,
    player_wear: list[float],
) -> list[DriverState]:
    drivers: list[DriverState] = []
    names = [
        "VER", "NOR", "LEC", "PIA", "SAI", "RUS", "HAM", "PER", "ALO", "STR",
        "GAS", "OCO", "ALB", "SAR", "TSU", "RIC", "BOT", "ZHO", "MAG", "HUL",
        "COL", "LAW",
    ]
    for index in range(active_cars):
        position = index + 1
        is_player = position == player_position
        lap_ms = int((base_lap + rng.uniform(-0.6, 2.5)) * 1000)
        age = player_age if is_player else rng.randint(0, 40)
        wear = (
            list(player_wear)
            if is_player
            else [min(99.0, age * rng.uniform(1.2, 2.4)) for _ in range(4)]
        )
        drivers.append(
            DriverState(
                car_idx=0 if is_player else index + 1,
                name="PLAYER" if is_player else names[index % len(names)],
                position=position,
                is_player=is_player,
                active=True,
                ai_controlled=not is_player,
                last_lap_ms=lap_ms,
                best_lap_ms=lap_ms - rng.randint(0, 900),
                gap_to_player_s=round(
                    (position - player_position) * rng.uniform(0.4, 3.5), 2
                ),
                delta_to_leader_s=round((position - 1) * rng.uniform(0.5, 3.0), 2),
                tyre_compound=player_compound if is_player else rng.choice(ALL_COMPOUNDS),
                tyre_age=age,
                tyre_wear=[round(value, 1) for value in wear],
                pit_stops=rng.randint(0, 3),
                current_lap=rng.randint(1, 40),
                lap_history=[
                    {"lap_ms": lap_ms + rng.randint(-400, 900), "valid_flags": 1}
                    for _ in range(rng.randint(0, 8))
                ],
            )
        )
    return drivers


def apply(state: Any, setup: dict[str, Any]) -> None:
    """Write one scenario onto the live SessionState."""
    state.session_type = setup["session_type"]
    state.mode_profile = setup["mode_profile"]
    state.track_id = setup["track_id"]
    state.track_name = setup["track_name"]
    state.current_lap = setup["current_lap"]
    state.total_laps = setup["total_laps"]
    state.player_position = setup["player_position"]
    state.active_cars = setup["active_cars"]
    state.weather = setup["weather"]
    state.rain_next_15_pct = setup["rain_next_15_pct"]
    state.safety_car = setup["safety_car"]
    state.race_control_phase = setup["race_control_phase"]
    state.fuel_laps_delta = setup["fuel_laps_delta"]
    state.tyre.compound = setup["tyre_compound"]
    state.tyre.age_laps = setup["tyre_age"]
    state.tyre.wear = setup["tyre_wear"]
    state.tyre_sets = setup["tyre_sets"]
    state.drivers = setup["drivers"]
    state.player_car_index = setup["player_car_index"]
    state.strategy = dict(setup["game_strategy"])
    state.pit_lane_time_ms = setup["pit_lane_time_ms"]
    if setup["damage"]:
        state.damage = dict(setup["damage"])


RE_NUMBER = __import__("re").compile(r"-?\d+(?:\.\d+)?")


def check_plan(sc: Scenario, plan: dict[str, Any]) -> list[Violation]:
    """Ground-truth checks that hold for any legal strategy recommendation."""
    out: list[Violation] = []
    sid = sc.id
    s = sc.setup
    current_lap = int(s["current_lap"])
    total_laps = int(s["total_laps"])
    mode = s["mode_profile"]

    def bad(check: str, detail: str, severity: str = "error") -> None:
        out.append(Violation(sid, check, detail, severity))

    if not plan.get("available"):
        # A refusal is legitimate; it must still be explained and must not
        # smuggle a recommendation through.
        if mode in {"race", "sprint"} and total_laps > 0 and current_lap <= total_laps:
            if not plan.get("reason"):
                bad("refusal_unexplained", "available=False with no reason")
        if plan.get("recommended"):
            bad(
                "refusal_with_recommendation",
                f"available=False but recommended={plan.get('recommended')}",
            )
        return out

    rec = plan.get("recommended") or {}
    plans = plan.get("plans") or []
    remaining = max(0, total_laps - current_lap + (1 if current_lap > 0 else 0))

    # --- pit lap bounds -------------------------------------------------
    box_lap = rec.get("box_lap")
    if box_lap is not None:
        if int(box_lap) < current_lap:
            bad(
                "box_lap_in_past",
                f"box_lap={box_lap} < current_lap={current_lap}",
            )
        if int(box_lap) > total_laps:
            bad(
                "box_lap_after_finish",
                f"box_lap={box_lap} > total_laps={total_laps}",
            )

    # --- structural consistency of each plan ----------------------------
    for pindex, candidate in enumerate(plans):
        laps = list(candidate.get("box_laps") or [])
        compounds = list(candidate.get("compounds") or [])
        stops = candidate.get("stops_remaining")

        if stops is not None and len(laps) != int(stops):
            bad(
                "stops_vs_box_laps",
                f"plan[{pindex}] stops_remaining={stops} but {len(laps)} box laps",
            )
        if laps and compounds and len(compounds) != len(laps) + 1:
            bad(
                "compound_count",
                f"plan[{pindex}] {len(laps)} stops needs {len(laps)+1} compounds, "
                f"got {len(compounds)}",
            )
        for lap in laps:
            if int(lap) < current_lap:
                bad(
                    "plan_box_lap_in_past",
                    f"plan[{pindex}] box lap {lap} < current_lap {current_lap}",
                )
            if int(lap) > total_laps:
                bad(
                    "plan_box_lap_after_finish",
                    f"plan[{pindex}] box lap {lap} > total_laps {total_laps}",
                )
        if laps != sorted(laps):
            bad("box_laps_unordered", f"plan[{pindex}] box laps not ascending: {laps}")
        if len(set(laps)) != len(laps):
            bad("box_laps_duplicate", f"plan[{pindex}] duplicate box laps: {laps}")

        projected = candidate.get("projected_time_s")
        risk = candidate.get("risk_adjusted_time_s")
        if projected is not None:
            if not (projected == projected) or projected <= 0:  # NaN-safe
                bad("projected_time_invalid", f"plan[{pindex}] projected={projected}")
        if projected is not None and risk is not None and risk < projected - 1e-6:
            bad(
                "risk_below_projected",
                f"plan[{pindex}] risk_adjusted={risk} < projected={projected}",
            )
        for key in ("projected_finish_wear_pct", "projected_max_wear_pct"):
            value = candidate.get(key)
            if value is not None and not (0.0 <= float(value) <= 100.0):
                bad("wear_out_of_range", f"plan[{pindex}] {key}={value}")

    # --- recommended must correspond to a real plan ---------------------
    # Two paths legitimately supersede the ranked plans: a red flag (tyres may
    # be changed in the pit lane at no time loss) and a live weather crossover
    # (the track is wet and the car is on slicks). Both are documented
    # overrides, not inconsistencies.
    override_path = (
        s["race_control_phase"] == "red_flag"
        or bool(plan.get("weather_crossover"))
        or str(rec.get("fit_compound", "")).upper() in set(WET_COMPOUNDS)
    )
    if plans and box_lap is not None and not override_path:
        first_laps = [
            (p.get("box_laps") or [None])[0] for p in plans if p.get("box_laps")
        ]
        if first_laps and int(box_lap) not in {int(v) for v in first_laps}:
            bad(
                "recommendation_not_in_plans",
                f"recommended box_lap={box_lap} matches no plan first stop {first_laps}",
            )

    # --- compound legality ----------------------------------------------
    rule = plan.get("compound_rule") or {}
    if rule.get("applies") and not rule.get("wet_waiver"):
        for pindex, candidate in enumerate(plans):
            crule = candidate.get("compound_rule") or {}
            if candidate.get("legal") and crule.get("compliant") is False:
                bad(
                    "illegal_plan_marked_legal",
                    f"plan[{pindex}] legal=True but compound rule non-compliant",
                )

    fit = rec.get("fit_compound")
    if fit:
        offered = {
            str(item.get("compound", "")).upper()
            for item in s["tyre_sets"]
            if item.get("available")
        }
        if offered and str(fit).upper() not in offered | set(WET_COMPOUNDS):
            bad(
                "fit_compound_unavailable",
                f"recommended {fit} but available sets are {sorted(offered)}",
                severity="warn",
            )

    # --- neutralisation math --------------------------------------------
    neutral = plan.get("neutralisation") or {}
    effective = neutral.get("effective_pit_loss_s")
    base = neutral.get("base_pit_loss_s")
    if effective is not None and base is not None:
        if s["race_control_phase"] in {"safety_car", "vsc"} and float(effective) > float(base) + 1e-6:
            bad(
                "neutralised_pit_loss_not_cheaper",
                f"phase={s['race_control_phase']} effective={effective} > base={base}",
            )
        if float(effective) < 0:
            bad("negative_pit_loss", f"effective_pit_loss_s={effective}")

    # --- endgame sanity ---------------------------------------------------
    if remaining <= 1 and rec.get("stops_remaining"):
        bad(
            "stop_with_no_laps_left",
            f"remaining={remaining} but stops_remaining={rec.get('stops_remaining')}",
        )

    # --- rejoin / finish projections stay inside the field ----------------
    field_size = int(s["active_cars"])
    for key in ("projected_rejoin_position", "projected_finish_position"):
        value = rec.get(key)
        if value is not None and not (1 <= int(value) <= max(1, field_size)):
            bad(
                "position_out_of_field",
                f"{key}={value} outside 1..{field_size}",
            )
    points = rec.get("projected_points")
    if points is not None and not (0 <= float(points) <= 26):
        bad("points_out_of_range", f"projected_points={points}")

    # --- pit loss must resemble the track ---------------------------------
    # Under a red flag the field is stationary in the pit lane and a tyre
    # change genuinely costs nothing, so zero is correct there and only there.
    pit_loss = plan.get("pit_loss_s")
    red_flag = s["race_control_phase"] == "red_flag"
    if pit_loss is not None:
        if red_flag:
            if float(pit_loss) != 0.0 and not (5.0 <= float(pit_loss) <= 80.0):
                bad("pit_loss_implausible", f"red flag pit_loss_s={pit_loss}")
            base = (plan.get("neutralisation") or {}).get("base_pit_loss_s")
            if base is not None and float(base) <= 0.0:
                bad(
                    "red_flag_lost_base_pit_loss",
                    "base_pit_loss_s must survive a red flag so the green-flag "
                    f"cost stays knowable, got {base}",
                )
        elif not (5.0 <= float(pit_loss) <= 80.0):
            bad(
                "pit_loss_implausible",
                f"pit_loss_s={pit_loss} outside any real F1 pit lane",
            )

    # --- compound rule must actually be honoured --------------------------
    if rule.get("applies") and rule.get("change_outstanding") and remaining > 1:
        fitted = [str(c).upper() for c in (plans[0].get("compounds") or [])[1:]] if plans else []
        if plans and not any(c in set(DRY_COMPOUNDS) for c in fitted):
            bad(
                "outstanding_rule_not_served",
                f"dry-compound change outstanding with {remaining} laps left but "
                f"top plan fits {fitted or 'nothing'}",
                severity="warn",
            )

    # --- the spoken call must name the compound it recommends -------------
    instruction_text = str(rec.get("instruction") or "")
    if fit and instruction_text and str(fit).upper() not in instruction_text.upper():
        if "box" in instruction_text.lower():
            bad(
                "instruction_compound_mismatch",
                f"fit_compound={fit} absent from instruction {instruction_text!r}",
            )

    # --- instruction must not invent numbers ------------------------------
    instruction = str(rec.get("instruction") or "")
    if instruction:
        structured = {
            str(int(v)) for v in (
                rec.get("box_lap"), rec.get("stops_remaining"),
                current_lap, total_laps,
            ) if v is not None
        }
        for candidate in plans:
            for lap in candidate.get("box_laps") or []:
                structured.add(str(int(lap)))
        for token in RE_NUMBER.findall(instruction):
            if "." in token:
                continue  # deltas/seconds are rendered from floats
            if token not in structured:
                bad(
                    "instruction_number_unbacked",
                    f"instruction cites {token!r} not present in structured fields "
                    f"({sorted(structured)}): {instruction!r}",
                    severity="warn",
                )
    return out


async def check_companion_tools(sc: Scenario, tools: Any) -> list[Violation]:
    """The strategy-adjacent tools the engineer calls in the same breath."""
    out: list[Violation] = []
    sid = sc.id
    s = sc.setup

    def bad(check: str, detail: str, severity: str = "error") -> None:
        out.append(Violation(sid, check, detail, severity))

    # Undercut / overcut must not claim a benefit they cannot support, and
    # must not crash on a driver that does not exist.
    for name, call in (
        ("undercut", tools.evaluate_undercut),
        ("overcut", tools.evaluate_overcut),
    ):
        for target in ("ahead", "behind", "NOBODY_XYZ"):
            try:
                result = await call(target)
            except Exception as exc:  # noqa: BLE001
                bad(f"{name}_exception", f"target={target}: {type(exc).__name__}: {exc}")
                continue
            if not isinstance(result, dict):
                bad(f"{name}_shape", f"target={target} returned {type(result).__name__}")
                continue
            if target == "NOBODY_XYZ" and result.get("available"):
                bad(
                    f"{name}_invented_driver",
                    f"claimed a result for a driver that is not in the field: {result}",
                )
            gain = result.get("gain_s") or result.get("net_gain_s")
            if gain is not None:
                try:
                    value = float(gain)
                except (TypeError, ValueError):
                    bad(f"{name}_gain_nonnumeric", f"gain={gain!r}")
                else:
                    if value != value or abs(value) > 600.0:
                        bad(f"{name}_gain_implausible", f"target={target} gain_s={value}")

    # Fuel/pace trade must agree in sign with the fuel margin it is given.
    try:
        pace = await tools.get_pace_mode_options()
    except Exception as exc:  # noqa: BLE001
        bad("pace_mode_exception", f"{type(exc).__name__}: {exc}")
    else:
        delta = float(s["fuel_laps_delta"])
        required = pace.get("required_saving_laps")
        if required is not None:
            value = float(required)
            if delta >= 0 and value > 0.05:
                bad(
                    "fuel_saving_demanded_with_surplus",
                    f"fuel_laps_delta={delta:+.2f} (surplus) but required saving {value}",
                )
            if delta < -0.2 and value <= 0:
                bad(
                    "fuel_shortfall_ignored",
                    f"fuel_laps_delta={delta:+.2f} (short) but required saving {value}",
                )

    # Energy plan must stay inside a physical battery.
    try:
        energy = await tools.get_energy_plan()
    except Exception as exc:  # noqa: BLE001
        bad("energy_exception", f"{type(exc).__name__}: {exc}")
    else:
        for key in ("battery_pct", "ers_pct"):
            value = energy.get(key)
            if value is not None and not (0.0 <= float(value) <= 100.0):
                bad("battery_out_of_range", f"{key}={value}")
    return out


async def run_batch(count: int, seed: int, verbose: bool = False,
                    only: int | None = None) -> tuple[int, list[Violation]]:
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="strategy_fuzz_"))
    store = StateStore()
    database = PitWallDatabase(tmp / "fuzz.sqlite3")
    await database.initialize()
    strategy = StrategyEngine(store, database)
    advisor = SetupAdvisor(store, database)
    analysis = AnalysisEngine(store, database, strategy)
    tools = TelemetryTools(store, database, analysis, strategy, advisor)

    violations: list[Violation] = []
    tested = 0
    for index in range(count):
        if only is not None and index != only:
            continue
        rng = random.Random(seed * 100_003 + index)
        sc = build_scenario(index, rng)
        await store.mutate(lambda s, setup=sc.setup: apply(s, setup))
        try:
            plan = await tools.get_pit_strategy()
        except Exception as exc:  # noqa: BLE001 - a crash is itself a defect
            violations.append(
                Violation(index, "engine_exception", f"{type(exc).__name__}: {exc}")
            )
            tested += 1
            continue
        found = check_plan(sc, plan)

        # Determinism: the same race state must not produce a different call.
        # A live engineer re-asked one second later must not get a new answer.
        try:
            again = await tools.get_pit_strategy()
            first_rec = (plan.get("recommended") or {})
            second_rec = (again.get("recommended") or {})
            for key in ("box_lap", "fit_compound", "stops_remaining"):
                if first_rec.get(key) != second_rec.get(key):
                    found.append(
                        Violation(
                            index,
                            "nondeterministic_recommendation",
                            f"{key}: {first_rec.get(key)!r} then {second_rec.get(key)!r} "
                            f"for identical state",
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            found.append(
                Violation(index, "engine_exception_repeat", f"{type(exc).__name__}: {exc}")
            )

        found.extend(await check_companion_tools(sc, tools))
        violations.extend(found)
        tested += 1
        if verbose and found:
            print(f"\n--- scenario {index} [{sc.label}] {sc.notes} ---")
            for item in found:
                print(f"    {item.severity.upper():5s} {item.check}: {item.detail}")
    return tested, violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=int, default=300)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--only", type=int, default=None)
    parser.add_argument("--json", type=str, default=None)
    args = parser.parse_args()

    tested, violations = asyncio.run(
        run_batch(args.scenarios, args.seed, args.verbose, args.only)
    )
    errors = [v for v in violations if v.severity == "error"]
    warns = [v for v in violations if v.severity == "warn"]

    by_check: dict[str, int] = {}
    for item in violations:
        by_check[f"{item.severity}:{item.check}"] = by_check.get(
            f"{item.severity}:{item.check}", 0
        ) + 1

    print(f"\nscenarios tested : {tested}")
    print(f"errors           : {len(errors)}")
    print(f"warnings         : {len(warns)}")
    if by_check:
        print("\nby check:")
        for name, hits in sorted(by_check.items(), key=lambda kv: -kv[1]):
            print(f"  {hits:5d}  {name}")
        affected = len({v.scenario_id for v in errors})
        print(f"\nscenarios with >=1 error: {affected} / {tested} "
              f"({100.0 * affected / max(1, tested):.1f}%)")
        print("\nfirst examples:")
        seen: set[str] = set()
        for item in violations:
            if item.check in seen:
                continue
            seen.add(item.check)
            print(f"  [{item.severity}] scenario {item.scenario_id} {item.check}")
            print(f"        {item.detail}")
    else:
        print("\nclean pass: no violations")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                [vars(v) for v in violations], indent=2
            ),
            encoding="utf-8",
        )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_scenario", "apply", "check_plan", "run_batch", "Scenario",
           "Violation", "TRACKS"]
