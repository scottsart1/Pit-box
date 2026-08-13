"""Racing-line findings must be geometric facts, not plausible stories.

Born from the 2026-08-12 tier-2 work: the driver's quick laps through a turn
form the reference line; a finding exists only when the slow passes sit on
the SAME side of that line at the decisive station on most passes, the
offset clears the measured lap-to-lap noise floor (~2 m on real Vegas data),
and the time deficit is real. Anything less consistent is reported as "not a
line problem" — braking and throttle timing belong to the setup/coaching
analyses, not this one.
"""

import pytest

from pitwall.line_insights import (
    MIN_INSTANCES,
    line_findings,
    resample_window,
)

TURN = {"turn": 1, "entry_m": 0.0, "apex_m": 60.0, "exit_m": 120.0}


def _lap(
    lap_num: int,
    *,
    offset: float = 0.0,
    pace: float = 1.0,
    ramp: str = "flat",
    gap_at: float | None = None,
) -> dict:
    """A synthetic pass: path along +x, lateral position z, 4 m sampling.

    With the reference tangent pointing +x, positive z is LEFT of travel —
    the same sign convention the module promises. ``pace`` scales time (>1 is
    slower); ``ramp`` shapes where the offset lives (entry/exit/flat);
    ``gap_at`` deletes 60 m of samples to simulate a recording hole.
    """
    points = []
    d = -12.0
    while d <= 132.0:
        if gap_at is not None and gap_at <= d < gap_at + 60.0:
            d += 4.0
            continue
        if ramp == "entry":
            z = offset * max(0.0, 1.0 - d / 120.0)
        elif ramp == "exit":
            z = offset * min(1.0, max(0.0, d / 120.0))
        else:
            z = offset
        # 30 m/s nominal, slowest at the apex so the apex-speed comparison
        # has a real minimum to find.
        speed = 108.0 - 30.0 * max(0.0, 1.0 - abs(d - 60.0) / 60.0)
        points.append(
            {
                "t": 100.0 + (d + 12.0) * pace / 30.0,
                "d": d,
                "x": d,
                "z": z,
                "speed": speed,
            }
        )
        d += 4.0
    return {"session_uid": 900, "lap_num": lap_num, "trace": points}


def test_consistent_slow_side_is_a_finding_with_the_correct_correction():
    laps = [_lap(n, offset=0.0, pace=1.0) for n in range(1, 4)]
    laps += [
        _lap(n, offset=2.5, pace=1.12, ramp="entry") for n in range(4, 10)
    ]
    findings = line_findings([TURN], laps)
    assert len(findings) == 1
    f = findings[0]
    assert f["phase"] == "entry"
    # Slow passes sit +2.5 (left of travel); the quick line is to the right.
    assert f["offset_m"] == pytest.approx(2.5, abs=0.3)
    assert "further right" in f["advice"]
    assert f["sign_consistency"] == 1.0
    assert f["median_loss_s"] > 0.15


def test_exit_phase_offsets_read_as_exit_advice():
    laps = [_lap(n, offset=0.0, pace=1.0) for n in range(1, 4)]
    laps += [
        _lap(n, offset=-2.5, pace=1.12, ramp="exit") for n in range(4, 10)
    ]
    findings = line_findings([TURN], laps)
    assert len(findings) == 1
    assert findings[0]["phase"] == "exit"
    assert "further left" in findings[0]["advice"]


def test_slow_passes_on_both_sides_are_not_a_line_problem():
    laps = [_lap(n, offset=0.0, pace=1.0) for n in range(1, 4)]
    laps += [
        _lap(n, offset=2.5 if n % 2 else -2.5, pace=1.12)
        for n in range(4, 10)
    ]
    assert line_findings([TURN], laps) == []


def test_offsets_inside_the_noise_floor_are_not_claimed():
    laps = [_lap(n, offset=0.0, pace=1.0) for n in range(1, 4)]
    laps += [_lap(n, offset=0.6, pace=1.12) for n in range(4, 10)]
    assert line_findings([TURN], laps) == []


def test_a_trivial_time_spread_is_not_worth_coaching():
    laps = [_lap(n, offset=0.0, pace=1.0) for n in range(1, 4)]
    laps += [_lap(n, offset=2.5, pace=1.01) for n in range(4, 10)]
    assert line_findings([TURN], laps) == []


def test_a_recording_hole_disqualifies_the_pass_not_the_driver():
    holey = _lap(99, offset=2.5, pace=1.12, gap_at=30.0)
    window = resample_window(
        [
            {**p} for p in holey["trace"]
        ],
        TURN["entry_m"],
        TURN["exit_m"],
    )
    assert window is None
    # And with holes on enough passes the turn stays silent entirely.
    laps = [_lap(n, offset=0.0, pace=1.0) for n in range(1, 4)]
    laps += [
        _lap(n, offset=2.5, pace=1.12, gap_at=30.0) for n in range(4, 10)
    ]
    assert len(laps) >= MIN_INSTANCES
    assert line_findings([TURN], laps) == []


def test_findings_rank_by_measured_loss():
    turn_b = {"turn": 2, "entry_m": 300.0, "apex_m": 360.0, "exit_m": 420.0}

    def shifted(lap: dict, by: float) -> dict:
        return {
            **lap,
            "trace": lap["trace"]
            + [
                {**p, "d": p["d"] + by, "x": p["x"] + by, "t": p["t"] + 60.0}
                for p in lap["trace"]
            ],
        }

    laps = [shifted(_lap(n, offset=0.0, pace=1.0), 300.0) for n in range(1, 4)]
    slow = []
    for n in range(4, 10):
        first = _lap(n, offset=2.0, pace=1.08)
        second = _lap(n, offset=2.0, pace=1.30)
        slow.append(
            {
                **first,
                "trace": first["trace"]
                + [
                    {**p, "d": p["d"] + 300.0, "x": p["x"] + 300.0, "t": p["t"] + 60.0}
                    for p in second["trace"]
                ],
            }
        )
    findings = line_findings([TURN, turn_b], laps + slow)
    assert len(findings) == 2
    assert findings[0]["turn"] == 2
    assert findings[0]["median_loss_s"] > findings[1]["median_loss_s"]


@pytest.mark.asyncio
async def test_recent_lap_traces_round_trip(stack):
    _, database, _, _, _, _ = stack
    lap = _lap(1)
    await database.save_lap(
        {
            "session_uid": 900,
            "track_id": 31,
            "track_name": "Las Vegas",
            "session_type": "Race",
            "lap_num": 1,
            "lap_time_ms": 94_000,
            "valid": True,
            "compound": "MEDIUM",
            "trace": lap["trace"],
        },
        [],
    )
    laps = await database.recent_lap_traces(31, 10)
    assert len(laps) == 1
    assert laps[0]["lap_num"] == 1
    assert len(laps[0]["trace"]) == len(lap["trace"])
    findings = line_findings([TURN], laps)
    assert findings == []  # one lap can never support a claim
