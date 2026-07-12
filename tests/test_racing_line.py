from pitwall.racing_line import compare_lines


def _trace(offset_start: float = 10_000, offset_end: float = -1, lateral_m: float = 0.0):
    points = []
    for index in range(0, 401):
        distance = float(index * 5)
        offset = lateral_m if offset_start <= distance <= offset_end else 0.0
        points.append(
            {
                "d": distance,
                "t": index / 20,
                "x": distance,
                "z": offset,
                "speed": 220.0 if offset == 0 else 210.0,
                "brake": 0.0 if offset == 0 else 0.25,
                "throttle": 1.0 if offset == 0 else 0.72,
            }
        )
    return points


def test_racing_line_detects_persistent_world_coordinate_deviation():
    reference = _trace()
    current = _trace(500, 800, 2.4)
    result = compare_lines(current, reference, threshold_m=1.0)

    assert result["available"] is True
    assert result["mean_abs_deviation_m"] > 0.2
    assert result["p95_deviation_m"] >= 2.0
    assert result["zones"]
    zone = result["zones"][0]
    assert 500 <= zone["center_m"] <= 800
    assert zone["average_deviation_m"] >= 2.0
    assert "overslowing" in zone["cause"]
    assert result["line_score"] < 100


def test_racing_line_requires_a_reference_lap():
    result = compare_lines(_trace(), None)
    assert result["available"] is False
    assert result["reference_building"] is True
