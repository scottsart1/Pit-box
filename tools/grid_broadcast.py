"""Broadcast a race from the grid, and drive it forward on command.

The shipped scenarios in ``tools.scenarios`` start mid-race - lap 28 and lap 49
- because they exist to photograph a strategy decision. Anything that happens
*before* the lights (the pre-race plan, the starting tyre, the first stint) has
no fixture at all, and reusing a mid-race one silently supplies 19-lap-old tyres
at 78% wear, which makes every plan project 100% wear at the flag and every
shape infeasible. That looks like a strategy bug and is not one.

This starts everyone on fresh rubber at lap 1 and advances only when told to, so
a test can hold the race at a checkpoint, assert against it, and move on.

    python -m tools.grid_broadcast --hold 30            # sit on the grid
    python -m tools.grid_broadcast --to-lap 30 --stops 12,34

Advancing is deliberately coarse. This is a state generator for verifying that
strategy output stays coherent as a race progresses, not a race simulator.
"""

from __future__ import annotations

import argparse
import dataclasses
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tools.demo_broadcast import Race  # noqa: E402
from tools.scenarios import SILVERSTONE  # noqa: E402

# Rough per-lap wear used while advancing. The real degradation model lives in
# the app; this only has to be plausible enough that a plan is not immediately
# infeasible, and monotonic enough that a stop visibly resets it.
WEAR_PER_LAP = 1.6


def fresh_race(seed: int = 7, total_laps: int | None = None) -> Race:
    scenario = dataclasses.replace(
        SILVERSTONE,
        start_lap=1,
        display_lap=1,
        player_tyre_age=0,
        player_wear=(0.0, 0.0, 0.0, 0.0),
        stint_overrides={},
        **({"total_laps": total_laps} if total_laps else {}),
    )
    race = Race(seed, scenario)
    race.lap = 1
    race.age = [0 for _ in race.age]
    race.wear = [[0.0, 0.0, 0.0, 0.0] for _ in race.wear]
    # Every car starts the shipped scenarios on one completed stop, which on the
    # grid is a lie the strategy engine believes: it re-bases a driver's plan
    # past its own first stop and reports the race has moved on.
    race.stops = [0 for _ in race.stops]
    return race


def send(race: Race, sock: socket.socket, target: tuple[str, int]) -> None:
    sock.sendto(race.session_packet(), target)
    sock.sendto(race.participants_packet(), target)
    sock.sendto(race.lap_packet(), target)
    sock.sendto(race.motion_packet(), target)
    sock.sendto(race.telemetry_packet(), target)
    sock.sendto(race.status_packet(), target)
    sock.sendto(race.damage_packet(), target)


def step_to(
    race: Race,
    lap: int,
    stops: set[int] | dict[int, str],
) -> None:
    """Move the player to ``lap``, pitting on any lap in ``stops``.

    Pass a dict to fit a specific compound at each stop. Without one the tyre
    comes back the same compound, which is not a pit stop any real strategy
    makes: the app correctly reports the mandatory dry-compound change as
    outstanding, and every call for the rest of the race is a disqualification
    warning about a stop the fixture never actually made.
    """
    fit = stops if isinstance(stops, dict) else {}
    while race.lap < lap:
        race.lap += 1
        for index in range(len(race.wear)):
            race.age[index] += 1
            for wheel in range(4):
                race.wear[index][wheel] = min(
                    99.0, race.wear[index][wheel] + WEAR_PER_LAP
                )
        if race.lap in stops:
            # A stop resets the player's set. Compound is left to the caller's
            # expectation; what matters downstream is age and wear going to zero
            # and pit_stops incrementing.
            race.age[race.player] = 0
            race.wear[race.player] = [0.0, 0.0, 0.0, 0.0]
            race.stops[race.player] += 1
            if race.lap in fit:
                race.compound[race.player] = fit[race.lap]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=20790)
    parser.add_argument("--hold", type=float, default=6.0,
                        help="seconds to broadcast at each checkpoint")
    parser.add_argument("--to-lap", type=int, default=1)
    parser.add_argument("--stops", default="",
                        help="comma-separated laps the player pits on")
    parser.add_argument("--total-laps", type=int, default=0)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    stops = {int(v) for v in args.stops.split(",") if v.strip()}
    race = fresh_race(args.seed, args.total_laps or None)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = (args.host, args.port)

    step_to(race, args.to_lap, stops)
    print(
        f"lap {race.lap}/{race.scenario.total_laps} "
        f"age={race.age[race.player]} wear={race.wear[race.player][0]:.0f}% "
        f"stops={race.stops[race.player]} -> {args.host}:{args.port}"
    )

    deadline = time.monotonic() + args.hold
    while time.monotonic() < deadline:
        send(race, sock, target)
        time.sleep(0.05)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
