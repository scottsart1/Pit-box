import pytest

from pitwall.audio import RACING_VOCAB
from pitwall.proactive import ProactiveEngineer


@pytest.mark.parametrize("regulations_2026,expected", [(True, "Overtake Mode"), (False, "ERS deployment")])
def test_low_battery_warning_never_claims_to_disable_aero(regulations_2026, expected):
    event = {"type": "energy_low", "payload": {"ers_pct": 12, "regulations_2026": regulations_2026}}
    answer = ProactiveEngineer._fallback_text(event, {})
    assert expected in answer
    assert "12 percent" in answer
    assert "DRS" not in answer and "Active Aero" not in answer
    assert "Manual Override" not in answer


def test_low_battery_uses_current_formula_when_event_omits_it():
    answer = ProactiveEngineer._fallback_text(
        {"type": "energy_low", "payload": {"ers_pct": 8}}, {"regulations_2026": True}
    )
    assert "Overtake Mode" in answer


def test_transcription_prompt_uses_current_game_control_names():
    assert {"overtake mode", "active aero", "straight line mode", "cornering mode"} <= set(RACING_VOCAB)
