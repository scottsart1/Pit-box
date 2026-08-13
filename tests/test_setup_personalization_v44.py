"""Setup recommendations must reflect how THIS driver drives THIS track.

Born from the 2026-08-12 overnight review: the advisor read only the last
lap's booleans while the corner_metrics table held every analyzed lap the
driver ever turned at the circuit — so "three setups" never reflected the
driver at all. These tests pin the persistent-evidence nudges added that
night: stored lock-ups/wheelspin move the brakes/differential, a measured
hot wear style trims pressures, and — critically — a fresh database moves
nothing, so the nudges can never fire off defaults.
"""

import pytest


def _lap(session_uid: int, lap_num: int, *, trace: list | None = None) -> dict:
    lap = {
        "session_uid": session_uid,
        "track_id": 10,
        "track_name": "Spa",
        "session_type": "Race",
        "lap_num": lap_num,
        "lap_time_ms": 107_000 + lap_num,
        "valid": True,
        "compound": "MEDIUM",
    }
    if trace is not None:
        lap["trace"] = trace
    return lap


def _turn_row(
    turn: int,
    apex_m: float,
    time_s: float,
    *,
    lock: bool = False,
    spin: bool = False,
    brake_point_m: float | None = None,
    apex_speed: float = 90.0,
) -> dict:
    return {
        "corner_no": turn,
        "name": f"Turn {turn}",
        "entry_m": apex_m - 60.0,
        "apex_m": apex_m,
        "exit_m": apex_m + 60.0,
        "brake_point_m": brake_point_m if brake_point_m is not None else apex_m - 50.0,
        "min_speed_kph": apex_speed,
        "apex_speed_kph": apex_speed,
        "throttle_on_m": apex_m + 10.0,
        "time_in_corner_s": time_s,
        "wheel_lock": lock,
        "wheelspin": spin,
    }


def _front_lock_trace() -> list[dict]:
    # Braking hard through Turn 1's window with the FRONT wheels under-rotating.
    return [
        {
            "t": 10.0 + i * 0.1,
            "d": 240.0 + i * 5.0,
            "speed": 150,
            "throttle": 0.0,
            "brake": 0.6,
            "gear": 3,
            "lat_g": 1.2,
            "long_g": -3.0,
            "slip": [-0.3, -0.28, 0.0, 0.0],
        }
        for i in range(30)
    ]


@pytest.mark.asyncio
async def test_stored_corner_history_moves_the_setup(stack):
    """Repeatable per-turn evidence moves the car; technique does not.

    Six recorded laps at one circuit: Turn 1 locks the front axle on half of
    them (traces prove which axle), Turn 2 wheelspins out of a slow corner,
    Turn 3 is simply braked too early — a driving habit no setup change can
    buy back.
    """
    _, database, _, setup, _, _ = stack
    for lap_num in range(1, 7):
        best = lap_num == 1
        locked = lap_num in (2, 4, 6)
        rows = [
            _turn_row(
                1, 300.0, 3.0 if best else 3.4, lock=locked, apex_speed=110.0
            ),
            _turn_row(
                2, 800.0, 2.5 if best else 2.8, spin=not best, apex_speed=85.0
            ),
            _turn_row(
                3,
                1500.0,
                4.0 if best else 4.5,
                brake_point_m=1450.0 if best else 1428.0,
                apex_speed=150.0 if best else 144.0,
            ),
        ]
        await database.save_lap(
            _lap(501, lap_num, trace=_front_lock_trace() if locked else None),
            rows,
        )
    result = await setup.generate("race", track_id=10)
    assert result["available"] is True
    foundation = result["foundational"]
    recommended = result["recommended"]
    findings = {f["mechanism"]: f for f in result["corner_findings"]}

    lockup = findings["entry-lockup"]
    assert lockup["evidence"]["lock_axle"] == "front"
    assert recommended["brake_bias"] == foundation["brake_bias"] - 1
    assert recommended["brake_pressure"] == foundation["brake_pressure"] - 2

    assert "exit-wheelspin" in findings
    assert recommended["on_throttle"] == foundation["on_throttle"] - 4
    assert recommended["rear_anti_roll_bar"] == foundation["rear_anti_roll_bar"] - 1

    technique = findings["overslowed-entry"]
    assert technique["adjustments"] == {}
    assert any(
        "driving, not setup" in line for line in result["rationale"]
    )
    assert any("Turn 1" in line for line in result["rationale"])


@pytest.mark.asyncio
async def test_hot_wear_style_trims_pressures(stack):
    store, _, _, setup, _, _ = stack
    # A current stint burning tyre far faster than the circuit baseline is
    # driver evidence (source current_stint_level, not track_default).
    await store.update(track_id=10, track_name="Spa", session_uid=502)
    await store.mutate(
        lambda state: (
            setattr(state.tyre, "compound", "MEDIUM"),
            setattr(state.tyre, "age_laps", 2),
            setattr(state.tyre, "wear", [20.0, 20.0, 20.0, 20.0]),
        )
    )
    result = await setup.generate("race", track_id=10)
    foundation = result["foundational"]
    recommended = result["recommended"]
    for field in (
        "front_left_tyre_pressure", "front_right_tyre_pressure",
        "rear_left_tyre_pressure", "rear_right_tyre_pressure",
    ):
        assert recommended[field] == pytest.approx(foundation[field] - 0.2)
    assert any("measured tyre wear here" in line for line in result["rationale"])


@pytest.mark.asyncio
async def test_no_personal_evidence_means_no_personal_nudges(stack):
    _, _, _, setup, _, _ = stack
    # Fresh database, no live telemetry: the track default is not evidence
    # about the driver, so nothing personal may move or be claimed.
    result = await setup.generate("race", track_id=10)
    assert result["recommended"]["brake_bias"] == result["foundational"]["brake_bias"]
    assert not any("Your record here" in line for line in result["rationale"])
    assert not any("measured tyre wear here" in line for line in result["rationale"])
