from __future__ import annotations

import math
import statistics

from .models import Flag, Handling, Lap, RaceConfig


def long_run(laps: list[Lap]) -> dict:
    clean = [x for x in laps if x.clean and x.flag in (Flag.GREEN, Flag.WHITE) and x.time_s]
    if not clean:
        return {"count": 0, "message": "Log clean green-flag laps to build a long-run comparison.", "stints": []}
    groups: dict[int, list[Lap]] = {}
    for lap in clean:
        groups.setdefault(lap.stint, []).append(lap)
    stints = []
    for stint, rows in groups.items():
        times = [x.time_s for x in rows]
        median = statistics.median(times)
        mad = statistics.median(abs(x - median) for x in times)
        # Do not fit a tire degradation curve through spins or obvious traffic laps.
        fitted = [x for x in rows if abs(x.time_s - median) <= max(1.0, 4 * mad)]
        slope = None
        if len(fitted) >= 5:
            xs, ys = [x.number for x in fitted], [x.time_s for x in fitted]
            xmean, ymean = statistics.mean(xs), statistics.mean(ys)
            denom = sum((x - xmean) ** 2 for x in xs)
            if denom:
                slope = sum((x - xmean) * (y - ymean) for x, y in zip(xs, ys)) / denom
        stints.append({"stint": stint, "count": len(rows), "best_s": round(min(times), 3),
                       "median_s": round(median, 3), "spread_s": round(statistics.pstdev(times), 3),
                       "trend_s_per_lap": round(slope, 4) if slope is not None else None,
                       "first_lap": rows[0].number, "last_lap": rows[-1].number})
    return {"count": len(clean), "best_s": round(min(x.time_s for x in clean), 3),
            "stints": stints, "message": "Compare runs at similar fuel, traffic and track conditions. A lap-time trend alone does not prove tire degradation."}


def fuel_rate(laps: list[Lap], flag: Flag, configured: float | None) -> dict:
    rates = [x.fuel_used_pct for x in laps if x.clean and x.flag == flag and x.fuel_used_pct is not None and x.fuel_used_pct >= 0][-12:]
    if len(rates) >= 3:
        median = statistics.median(rates)
        spread = statistics.median(abs(x - median) for x in rates)
        # Whole-percent HUDs can alternate unchanged and one-percent-drop laps.
        # Omitting the unchanged laps would inflate a 0.5% caution rate to 1%.
        quantization = 1.0 if all(float(x).is_integer() for x in rates) else 0
        kept = [x for x in rates if abs(x - median) <= max(median * .3, spread * 3, quantization)]
        if len(kept) >= 3 and sum(kept) > 0:
            return {"value": round(statistics.mean(kept), 4), "source": "measured", "samples": len(kept),
                    "high": round(max(kept), 4), "low": round(min(kept), 4)}
    return {"value": configured, "source": "driver estimate" if configured else "unknown", "samples": len(rates),
            "high": configured, "low": configured}


def fuel_plan(config: RaceConfig, values: dict, laps: list[Lap], yellow_laps: float = 0) -> dict:
    green = fuel_rate(laps, Flag.GREEN, config.fuel_burn_green)
    yellow = fuel_rate(laps, Flag.YELLOW, config.fuel_burn_yellow)
    fuel = values.get("fuel_pct")
    completed = values.get("completed_laps")
    frac = values.get("lap_fraction") or 0
    player_completed, player_frac = completed, frac
    lapped_without_leader = bool(values.get("laps_down")) and values.get("leader_completed_laps") is None
    if values.get("laps_down") and values.get("leader_completed_laps") is not None:
        completed = values["leader_completed_laps"]
        frac = 0  # Player's fractional lap cannot describe the leader's progress.
    remaining = max(0, config.total_laps - completed - frac) if completed is not None else None
    distance_reached = completed is not None and completed >= config.total_laps and values.get("flag") not in (Flag.CHECKERED, Flag.WHITE)
    overtime = distance_reached and config.overtime_enabled and config.session_type == "race"
    if values.get("flag") == Flag.WHITE:
        remaining = 1 - frac
    if values.get("flag") == Flag.CHECKERED:
        remaining = 0
    reserve = config.reserve_laps if config.overtime_enabled and values.get("flag") not in (Flag.CHECKERED, Flag.WHITE) else 0
    # Once advertised distance has passed, race control must establish the finish.
    if distance_reached:
        remaining = None
    stage = next((x for x in config.stage_ends if completed is not None and x > completed), None)
    result = {"green": green, "yellow": yellow, "fuel_pct": fuel, "remaining_laps": remaining,
              "reserve_laps": reserve, "overtime": overtime, "stage_end": stage,
              "stage_remaining": max(0, stage - completed - frac) if stage is not None else None,
              "fuel_laps": None, "margin_pct": None, "needed_pct": None, "minimum_stops": None,
              "latest_pit_completed_lap": None, "save_pct": None, "status": "unknown",
              "message": "Enter fuel, completed laps and a green-flag burn estimate, or collect three clean fuel laps.",
              "assumed_yellow_laps": yellow_laps}
    if fuel is not None and green["value"]:
        result["fuel_laps"] = round(fuel / green["value"], 1)
        if player_completed is not None:
            result["latest_pit_completed_lap"] = player_completed + math.floor(player_frac + max(0, fuel / green["high"] - 1))
    if distance_reached:
        result["message"] = ("Past the scheduled distance. Confirm the current overtime attempt; another restart can add more laps." if overtime else
                             "The configured distance has been reached. Confirm race control or update the planned distance before estimating the finish.")
        return result
    if lapped_without_leader and values.get("flag") != Flag.CHECKERED:
        result.update(remaining_laps=None, stage_remaining=None, stage_end=None,
                      message="You are a lap or more down. Confirm the leader's completed lap to estimate the distance until the race ends.")
        return result
    if fuel is None or remaining is None or not green["value"]:
        return result
    if remaining == 0:
        result.update(status="finished", message="Scheduled distance complete.", margin_pct=fuel, needed_pct=0, minimum_stops=0, save_pct=0)
        return result
    ylaps = min(max(yellow_laps, 0), remaining)
    if ylaps and not yellow["value"]:
        result["message"] = "A caution scenario needs a measured or entered yellow-flag fuel rate."
        return result
    needed = (remaining - ylaps + reserve) * green["value"] + ylaps * (yellow["value"] or 0)
    margin = fuel - needed
    result.update(needed_pct=round(needed, 2), margin_pct=round(margin, 2),
                  minimum_stops=max(0, math.ceil((needed - fuel) / 100)),
                  save_pct=round(max(0, 1 - fuel / needed) * 100, 1) if needed else 0)
    if result["fuel_laps"] is not None and result["fuel_laps"] <= 2:
        result.update(status="critical", message="Fuel is at two green laps or less. Check pit access now.")
    elif margin < 0:
        result.update(status="short", message=f"{abs(margin):.1f}% of a tank short including the selected reserve. Plan service or measured fuel saving.")
    else:
        result.update(status="good", message=f"{margin:.1f}% of a tank above the target, including {reserve:g} reserve laps.")
    return result


def pit_options(config: RaceConfig, values: dict, fuel: dict, add_fuel: float | None = None) -> dict:
    flag = values.get("flag")
    access = values.get("pit_open")
    if access is False:
        access_text = "Pit road is reported closed. Wait for the game's open/eligible call."
    elif access is None:
        access_text = "Pit-road access is unknown. Confirm the game's pit-open and eligibility message."
    else:
        access_text = "Pit road is reported open. Check your car's eligibility and penalties."
    current = values.get("fuel_pct")
    target = fuel.get("needed_pct")
    added = add_fuel if add_fuel is not None else (min(100 - current, max(0, target - current)) if current is not None and target is not None else None)
    loss = config.yellow_pit_loss_s if flag == Flag.YELLOW else config.green_pit_loss_s if flag in (Flag.GREEN, Flag.WHITE) else None
    options = []
    for name, tire_time in [("Four tires", config.four_tire_s), ("Right-side tires", config.two_tire_s), ("Left-side tires", config.two_tire_s), ("Fuel only", 0)]:
        fueling = added / config.fuel_fill_pct_s if added is not None and config.fuel_fill_pct_s else 0 if added == 0 else None
        service = None
        if tire_time is not None and fueling is not None:
            service = max(tire_time, fueling) if config.service_parallel else tire_time + fueling
        options.append({"name": name, "fuel_added_pct": round(added, 2) if added is not None else None,
                        "service_s": round(service, 2) if service is not None else None,
                        "total_loss_s": round(loss + service, 2) if loss is not None and service is not None else None,
                        "tradeoff": "Refresh every corner; strongest tire reset." if name == "Four tires" else
                        "Preserves left-side age; balance changes need a practice test." if name == "Right-side tires" else
                        "Preserves right-side age; verify this option in your series." if name == "Left-side tires" else
                        "Keeps track position where tire performance allows; no grip reset."})
    return {"access": access, "access_message": access_text, "options": options,
            "note": "Times use your measured pit transit and service inputs. Traffic, queueing, damage and restart order are additional uncertainties."}


def field_strategy(values: dict, fuel: dict) -> dict:
    opponents = values.get("opponents") or []
    remaining = fuel.get("remaining_laps")
    known, unknown = [], []
    for car in opponents:
        # Stop count/last stop alone cannot reveal fuel load, savings or intent.
        if car.get("fuel_laps") is None or remaining is None:
            unknown.append(car["car"])
        else:
            # Lapped cars still drive until the leader finishes. Their deficit
            # must not be subtracted from the remaining time/distance estimate.
            needed = remaining
            known.append({"car": car["car"], "position": car["position"],
                          "assessment": "short on reported fuel" if car["fuel_laps"] < needed else "reported fuel covers scheduled laps",
                          "reported_fuel_laps": car["fuel_laps"]})
    return {"observed_cars": len(opponents), "assessments": known, "unknown_cars": unknown,
            "message": "Opponents' future stops are unknown without their fuel range or an observed commitment. Gaps and last-stop laps are not proof of pit intent."}


def setup_review(report: Handling, config: RaceConfig, laps: list[Lap]) -> dict:
    run = long_run(laps)
    phases = {"entry": "braking and initial turn-in", "middle": "steady corner balance", "exit": "throttle pickup and drive off"}
    balance = "front tires sliding / car pushing wide" if report.balance == "tight" else "rear stepping out" if report.balance == "loose" else "balanced handling"
    actions = []
    def add(control, action, why):
        if control in report.available_controls:
            actions.append({"control": control, "action": action, "why": why})
    if report.balance == "neutral":
        diagnosis = "The car is balanced. Establish repeatable long-run pace before disturbing the platform."
    else:
        diagnosis = f"{report.balance.title()} during {phases[report.phase]} means {balance}."
        if report.phase == "entry":
            add("brake_bias", "Test one small step of brake balance only after checking wheel lockup.", "Front lockup can feel tight; rear lockup can feel loose. Bias direction needs the lockup evidence.")
        add("wedge", "Test a small decrease in crossweight / wedge." if report.balance == "tight" else "Test a small increase in crossweight / wedge.", "A general left-turn stock-car balance hypothesis. Confirm the game's control direction and keep every value inside its limits.")
        if report.scope != "minimum":
            add("pressures", "Compare all four tire readings and reset to a saved pressure baseline before a separate pressure test.", "Avoid changing wedge and pressure together; hot pressures, wear and the corner that overheats decide the next step.")
        if report.scope == "radical":
            add("bars", "Compare the baseline roll-stiffness balance with an alternative preset in a matched run.", "A platform review may reveal a persistent balance compromise that a pit adjustment cannot fix.")
            add("springs", "Review spring balance and suspension travel against a saved stable baseline.", "Entry, center and exit need separate checks; no unsupported spring rates are invented.")
            add("camber", "Review alignment against inside/middle/outside temperatures when available.", "Wear or shoulder heat can make a setup deteriorate even when its first lap is fast.")
    if config.track_type == "road":
        actions = [a for a in actions if a["control"] != "wedge"]
        diagnosis += " This is a road course: evaluate left and right turns separately before applying an oval crossweight change."
    if report.timing == "late":
        diagnosis += " The late-run onset makes tire evolution and fuel-load change part of the investigation."
    if report.scope == "radical":
        diagnosis = "Rebuild the baseline decision first, then tune individual controls. " + diagnosis
        actions.insert(0, {"control": "baseline", "action": "Compare the saved setup with a different stable game preset, if your mode offers one, over matched full runs.",
                           "why": "Judge entry, center, exit, tire retention and repeatability together. The fastest first lap is not necessarily the best race platform."})
    limit = 1 if report.scope == "minimum" else 3 if report.scope == "moderate" else 10
    actions = actions[:limit]
    plan = ["Save the current setup and record fuel level, assists, wear multipliers and track conditions.",
            "Run a repeatable baseline; log clean laps and entry, middle and exit balance.",
            "Change one supported control, then repeat at a comparable fuel level.",
            "Keep the change only if it improves the chosen goal without creating a new handling problem."]
    if report.scope == "radical":
        plan.insert(1, "Reassess the whole baseline, gearing, braking approach and driving line; compare an alternative game preset as a separate run.")
    return {"scope": report.scope, "goal": report.goal, "diagnosis": diagnosis,
            "actions": actions, "test_plan": plan, "run": run,
            "caveat": "These are test hypotheses, not measured NASCAR 26 setup values. Select only controls actually exposed by your car's garage.",
            "driving_check": "Unwind steering as you add throttle; compare a later apex." if report.phase == "exit" else
            "Compare an earlier lift and smoother brake release before adding steering." if report.phase == "entry" else
            "Compare entry speed and line before asking the front tires for more steering."}
