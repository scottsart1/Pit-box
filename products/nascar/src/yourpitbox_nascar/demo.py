from __future__ import annotations

import math

from .engine import RaceEngine
from .models import Lap, Observation, RaceConfig


def start_demo(engine: RaceEngine):
    config = RaceConfig(name="Charlotte · sample race", track="Charlotte", total_laps=100,
                        stage_ends=[25, 50], fuel_burn_green=2.15, fuel_burn_yellow=.8,
                        green_pit_loss_s=27, yellow_pit_loss_s=8, fuel_fill_pct_s=5,
                        four_tire_s=11.5, two_tire_s=7, mode="demo")
    sid = engine.start(config)
    engine.add_laps([Lap(number=n, time_s=29.4 + n * .047 + math.sin(n) * .11,
                        fuel_used_pct=2.15 + math.sin(n) * .06, flag="green", stint=1,
                        source="demo") for n in range(51, 67)])
    demo_tick(engine, 0)
    return sid


def demo_tick(engine: RaceEngine, tick: int):
    completed = min(99, 66 + tick // 8)
    frac = (tick % 8) / 8
    caution = 20 <= tick < 32
    fuel = max(0, 71 - (completed - 66 + frac) * 2.15)
    opponents = [{"car": str(n), "name": f"Sample driver {n}", "position": n,
                  "laps_down": 0 if n < 28 else 1, "gap_s": round(n * .63, 2),
                  "last_pit_lap": 49 if n < 20 else 54, "fuel_laps": None} for n in range(1, 41) if n != 8]
    engine.ingest(Observation(session_id=engine.sid, sequence=tick, source="demo", sample={
        "completed_laps": completed, "lap_fraction": frac, "position": 8,
        "speed_mph": 75 if caution else 178 + math.sin(tick) * 12,
        "fuel_pct": round(fuel, 1), "flag": "yellow" if caution else "green",
        "pit_open": tick >= 24 if caution else True, "in_pit": False, "laps_down": 0,
        "last_lap_s": 32.52 + math.sin(tick) * .1,
        "tires": {"lf": 83, "rf": max(8, 63 - tick / 6), "lr": 81, "rr": max(9, 70 - tick / 7)},
        "opponents": opponents}))
