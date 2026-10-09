"""Synthetic race shared by the Python and Node session-analysis tests.

`build_fixture()` runs a made-up 12-lap race through the real analysis code:
a safety car on lap 3 and a red flag on laps 6-7 (both from race-control
messages) with a standing restart on lap 8, a green-flag pit stop, a tyre
change nobody recorded, a local yellow, a lapped car, a retirement, teammates
and a car known only from the game's lap history after lap 2. tests/fixtures/session_analysis_race.json is its
output; test_session_analysis_fixture.py keeps the file in step with the
backend so the Node chart tests always read the current contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pitwall.session_analysis import AnalysisCar, AnalysisLap, build_session_analysis

FIXTURE = Path(__file__).parent / "fixtures" / "session_analysis_race.json"

SESSION = {
    "id": "ses_fixture_race",
    "session_type": "Race",
    "raw_session_type_id": 15,
    "track_id": 12,
    "track_name": "Singapore",
    "total_laps": 12,
    "status": "complete",
    "started_at": "2026-10-08T13:00:00+00:00",
}

CARS = [
    AnalysisCar(0, ("car_0",), "Charles Leclerc", 476, 16, True),
    AnalysisCar(1, ("car_1",), "Lando Norris", 484, 4, False),
    AnalysisCar(2, ("car_2",), "Oscar Piastri", 484, 81, False),
    AnalysisCar(3, ("car_3",), "Alexander Albon", 479, 23, False),
    AnalysisCar(4, ("car_4",), "Valtteri Bottas", 486, 77, False),
    AnalysisCar(5, ("car_5",), "Isack Hadjar", 478, 6, False),
]

# Race-control messages: lights out, a safety car on lap 3, a red flag on lap 6
# and the restart from the grid on lap 8.
EVENTS = [
    {"type": "LGOT", "lap": 1, "payload": {}},
    {"type": "SCAR", "lap": 3, "payload": {"safety_car_type": 1, "event_type": 0}},
    {"type": "SCAR", "lap": 3, "payload": {"safety_car_type": 1, "event_type": 2}},
    {"type": "RDFL", "lap": 6, "payload": {"active": True}},
    {"type": "LGOT", "lap": 8, "payload": {}},
]

PACE = {0: 92_000, 1: 92_150, 2: 92_500, 3: 93_100, 4: 100_400, 5: 92_900}
SUSPENDED = {6: 205_000, 7: 231_000}


def _lap(car: int, number: int, ms: int | None, **extra: object) -> AnalysisLap:
    compound = extra.pop("compound", "MEDIUM")
    age = extra.pop("age", number - 1)
    observed = bool(extra.pop("observed", True))
    sectors = (ms // 3 - 400, ms // 3 + 900, ms - 2 * (ms // 3) - 500) if ms and ms < 150_000 else (None, None, None)
    return AnalysisLap(
        car_index=car,
        lap_id=f"lap_{car}_{number}",
        lap_number=number,
        lap_time_ms=ms,
        s1_ms=sectors[0],
        s2_ms=sectors[1],
        s3_ms=sectors[2],
        valid=bool(extra.pop("valid", True)),
        pit_context=bool(extra.pop("pit", False)),
        flag_context=bool(extra.pop("flag", False)),
        compound=compound,  # type: ignore[arg-type]
        tyre_age_laps=age,  # type: ignore[arg-type]
        context_observed=observed,
        traced=observed and compound is not None,
    )


def laps() -> list[AnalysisLap]:
    out: list[AnalysisLap] = []
    for car, pace in PACE.items():
        last = 11 if car == 4 else 2 if car == 5 else 12  # car 4 a lap down, car 5 retires
        for number in range(1, last + 1):
            if number in SUSPENDED:
                ms = SUSPENDED[number] + car * 700
                if car == 4:
                    out.append(_lap(car, number, ms, compound=None, age=None, observed=False))
                else:
                    out.append(_lap(car, number, ms, flag=number == 6, age=number - 1))
                continue
            ms = pace + (2_500 if number in (1, 8) else 0) + (number % 3) * 60
            if car == 4 and number >= 8:
                # A damaged car after the restart: it takes the flag a lap down.
                ms = 118_000 + (number % 3) * 60
            flag = number == 3 or (car == 2 and number == 4)  # PIA sees a local yellow on lap 4
            if number == 3:
                ms = 131_000 + car * 40
            pit = car == 1 and number in (10, 11)
            if pit:
                ms += 11_000
            fresh = number >= 8
            compound = "HARD" if (car == 1 and number >= 11) else ("SOFT" if car == 2 and fresh else "MEDIUM")
            age = number - 11 if (car == 1 and number >= 11) else (number - 8 if fresh else number - 1)
            if car == 3 and number >= 11:
                compound, age = "HARD", number - 11  # changed without a recorded stop
            if car == 4 and number > 2:
                # Outside the trace scope: known from lap history only.
                out.append(_lap(car, number, ms, compound=None, age=None, observed=False))
                continue
            out.append(_lap(car, number, ms, flag=flag, pit=pit, compound=compound, age=age))
        if car == 5:
            out.append(_lap(car, 3, None))
    return out


def build_fixture() -> dict:
    return build_session_analysis(SESSION, CARS, laps(), events=EVENTS)


# A full field over a long race, for layout checks at scale: 22 cars and 60
# laps, a VSC and a safety car from race control, a stop under the safety
# car, two lapped cars, a retirement, a car known only from lap history
# after half distance and a car whose record has a lap without a time. It is
# not stored: tools/session-analysis-layout-smoke.cjs runs
# `python tests/session_analysis_fixture.py --large` and reads stdout.
LARGE_SESSION = {
    "id": "ses_fixture_large",
    "session_type": "Race",
    "raw_session_type_id": 15,
    "track_id": 7,
    "track_name": "Silverstone",
    "total_laps": 60,
    "status": "complete",
    "started_at": "2026-10-05T13:00:00+00:00",
}
LARGE_NAMES = [
    "Lando Norris", "Oscar Piastri", "George Russell", "Kimi Antonelli", "Max Verstappen",
    "Isack Hadjar", "Charles Leclerc", "Lewis Hamilton", "Alexander Albon", "Carlos Sainz",
    "Liam Lawson", "Arvid Lindblad", "Fernando Alonso", "Lance Stroll", "Oliver Bearman",
    "Esteban Ocon", "Nico Hulkenberg", "Gabriel Bortoleto", "Pierre Gasly", "Franco Colapinto",
    "Sergio Perez", "Valtteri Bottas",
]
# Teammates share a 2026 season-pack team id (476-486).
LARGE_TEAMS = [team for team in (484, 476, 478, 477, 479, 482, 480, 483, 485, 481, 486) for _ in range(2)]
LARGE_PLAYER = 9
LARGE_EVENTS = [
    {"type": "LGOT", "lap": 1, "payload": {}},
    {"type": "SCAR", "lap": 18, "payload": {"safety_car_type": 2, "event_type": 0}},
    {"type": "SCAR", "lap": 19, "payload": {"safety_car_type": 2, "event_type": 2}},
    {"type": "SCAR", "lap": 34, "payload": {"safety_car_type": 1, "event_type": 0}},
    {"type": "SCAR", "lap": 37, "payload": {"safety_car_type": 1, "event_type": 2}},
]


def large_cars() -> list[AnalysisCar]:
    return [
        AnalysisCar(index, (f"car_{index}",), name, LARGE_TEAMS[index], index + 1, index == LARGE_PLAYER)
        for index, name in enumerate(LARGE_NAMES)
    ]


def large_laps() -> list[AnalysisLap]:
    out: list[AnalysisLap] = []
    for car in range(22):
        last = {20: 59, 21: 58, 15: 41}.get(car, 60)  # two lapped cars and a retirement
        pit_lap = 35 if car in (4, 5) else 22 + (car * 3) % 9  # two cars stop under the safety car
        start = "SOFT" if car % 3 == 0 else "MEDIUM"
        for number in range(1, last + 1):
            ms = 90_000 + car * 110 + (number * 37 + car * 53) % 400
            if car >= 20:
                ms += 2_600  # off the pace: lapped before the flag
            if number == 1:
                ms += 2_500
            if number in (18, 19):
                ms = int(ms * 1.3)
            if number in range(34, 38):
                ms = int(ms * 1.45)
            pit = number in (pit_lap, pit_lap + 1)
            if pit and number not in range(34, 38):
                ms += 11_000
            fresh = number > pit_lap
            compound = "HARD" if fresh else start
            age = number - pit_lap - 1 if fresh else number - 1
            if car == 12 and number == 45:
                out.append(_lap(car, number, None, compound=compound, age=age))
                continue
            if car == 19 and number > 30:
                # Outside the trace scope after half distance: lap history only.
                out.append(_lap(car, number, ms, compound=None, age=None, observed=False))
                continue
            flag = number in (18, 19, 34, 35, 36, 37)
            out.append(_lap(car, number, ms, pit=pit, flag=flag, compound=compound, age=age))
    return out


def build_large_fixture() -> dict:
    return build_session_analysis(LARGE_SESSION, large_cars(), large_laps(), events=LARGE_EVENTS)


def write_fixture() -> None:
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(build_fixture(), indent=1, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if "--large" in sys.argv[1:]:
        sys.stdout.write(json.dumps(build_large_fixture(), separators=(",", ":"), sort_keys=True))
    else:
        write_fixture()
