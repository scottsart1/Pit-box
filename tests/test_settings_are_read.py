"""Every setting does something.

A setting that nothing reads is worse than none: it is documented, a driver
changes it, and nothing happens. ``proactive_safe_throttle`` sat in
``.env.example`` at 0.65 while the speaking window used a literal 0.45, and
``strategy_risk_quantile`` was a switch between the median and the 75th
percentile whatever value it was given.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pitwall.config import Settings

SOURCE = Path(__file__).resolve().parents[1] / "src" / "pitwall"
ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def _sources() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in SOURCE.rglob("*.py")
        if path.name != "config.py"
    )


# Read through a property on Settings itself rather than by name elsewhere.
READ_IN_CONFIG = {"udp_host": "Settings.udp_bind_host"}


def test_every_setting_is_read_somewhere():
    text = _sources()
    unread = [
        name for name in Settings.model_fields
        if name not in READ_IN_CONFIG and not re.search(rf"\b{re.escape(name)}\b", text)
    ]
    assert unread == []


def test_every_documented_setting_exists():
    known = {f"PITWALL_{name.upper()}" for name in Settings.model_fields}
    # The bind-host name is an explicit alias of udp_host.
    known.add("PITWALL_UDP_BIND_HOST")
    documented = set(re.findall(r"^#?\s*(PITWALL_[A-Z0-9_]+)=", ENV_EXAMPLE.read_text(), re.M))
    assert sorted(documented - known) == []


@pytest.mark.parametrize("quantile,expected_key", [(0.5, "p50_s"), (0.75, "p75_s"), (0.9, "p90_s")])
def test_the_risk_quantile_is_the_quantile_plans_are_ranked_on(quantile, expected_key):
    from pitwall.strategy import StrategyEngine

    plan = {"projected_time_s": 1000.0, "stops_remaining": 1, "pit_stop_costs_s": [22.0],
            "stint_models": [{"laps": 20, "wear_sample_size": 0, "deg_sample_size": 0}],
            "projected_rejoin_position": 8}
    state = {"session_uid": 1, "current_lap": 5, "player_position": 5}
    profile = StrategyEngine._monte_carlo_profile(plan, state, 22.0, 400, quantile)
    assert profile["risk_s"] == profile[expected_key]
    assert profile["risk_quantile"] == quantile


def test_the_risk_quantile_is_clamped():
    from pitwall.strategy import StrategyEngine

    plan = {"projected_time_s": 1000.0, "stops_remaining": 0, "stint_models": []}
    low = StrategyEngine._monte_carlo_profile(plan, {}, 22.0, 80, 0.1)
    high = StrategyEngine._monte_carlo_profile(plan, {}, 22.0, 80, 0.999)
    assert (low["risk_quantile"], high["risk_quantile"]) == (0.5, 0.95)


def test_the_custom_endpoint_carries_the_name_it_was_given(monkeypatch):
    from pitwall import credentials
    from pitwall.config import settings

    custom = credentials.PROVIDERS["custom"]
    monkeypatch.setattr(settings, "custom_llm_label", "Local Ollama")
    assert credentials.provider_label(custom) == "Local Ollama"
    monkeypatch.setattr(settings, "custom_llm_label", "  ")
    assert credentials.provider_label(custom) == "Custom endpoint"
    assert credentials.provider_label(credentials.PROVIDERS["openai"]) == "OpenAI"
