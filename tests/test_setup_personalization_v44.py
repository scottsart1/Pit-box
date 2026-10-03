"""Legacy observations remain reviewable without becoming qualified learning."""

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
async def test_stored_corner_history_remains_reviewable_without_automatic_setup_changes(
    stack,
):
    """Legacy turn observations remain inspectable without qualified context.

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
            _turn_row(1, 300.0, 3.0 if best else 3.4, lock=locked, apex_speed=110.0),
            _turn_row(2, 800.0, 2.5 if best else 2.8, spin=not best, apex_speed=85.0),
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
    observations, _, _ = await setup._corner_causal_findings(10)
    findings = {f["mechanism"]: f for f in observations}

    lockup = findings["entry-lockup"]
    assert lockup["evidence"]["lock_axle"] == "front"
    assert lockup["adjustments"] == {"brake_bias": -1, "brake_pressure": -2}
    assert recommended == foundation
    assert result["corner_findings"] == []
    assert not result["learning_status"]["qualified"]

    assert "exit-wheelspin" in findings
    assert findings["exit-wheelspin"]["adjustments"] == {
        "on_throttle": -4,
        "rear_anti_roll_bar": -1,
    }

    technique = findings["overslowed-entry"]
    assert technique["adjustments"] == {}
    assert "lack the matched" in result["learning_status"]["reason"]


@pytest.mark.asyncio
async def test_hot_wear_style_does_not_assume_a_pressure_direction(stack):
    store, _, _, setup, _, _ = stack
    # Measured wear alone does not establish which pressure change would help.
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
        "front_left_tyre_pressure",
        "front_right_tyre_pressure",
        "rear_left_tyre_pressure",
        "rear_right_tyre_pressure",
    ):
        assert recommended[field] == pytest.approx(foundation[field])
    assert any(
        "Pressure changes are not inferred" in line for line in result["rationale"]
    )


@pytest.mark.asyncio
async def test_no_personal_evidence_means_no_personal_nudges(stack):
    _, _, _, setup, _, _ = stack
    # Fresh database, no live telemetry: the track default is not evidence
    # about the driver, so nothing personal may move or be claimed.
    result = await setup.generate("race", track_id=10)
    assert result["recommended"]["brake_bias"] == result["foundational"]["brake_bias"]
    assert not any("Your record here" in line for line in result["rationale"])
    assert not any("measured tyre wear here" in line for line in result["rationale"])
