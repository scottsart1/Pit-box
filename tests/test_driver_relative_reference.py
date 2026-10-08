import pytest

from pitwall.tools import TelemetryTools


@pytest.mark.parametrize("reference,position", [
    ("behind", 7), ("car behind", 7), ("the car behind", 7),
    ("the following car", 7), ("Driver behind", 7),
    ("ahead", 5), ("car ahead", 5), ("the car in front", 5),
])
def test_relative_tool_reference_resolves_classified_driver(reference, position):
    state = {"player_position": 6, "drivers": [
        {"position": 0, "name": "Retired"},
        {"position": 5, "name": "Hamilton"},
        {"position": 6, "name": "Player"},
        {"position": 7, "name": "Antonelli"},
    ]}
    assert TelemetryTools._resolve_driver(state, reference)["position"] == position


def test_relative_reference_does_not_match_unclassified_car():
    state = {"player_position": 1, "drivers": [{"position": 0, "name": "Retired"}]}
    assert TelemetryTools._resolve_driver(state, "the car ahead") is None
