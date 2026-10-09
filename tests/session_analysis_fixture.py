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


def write_fixture() -> None:
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(build_fixture(), indent=1, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write_fixture()
