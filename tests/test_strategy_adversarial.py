"""Defects found by randomized adversarial scenario testing.

Each test pins a specific wrong recommendation the strategy engine produced
before the accompanying fix. They are written from the failing scenario, not
from the implementation, so they stay meaningful if the internals change.

The generator that found these lives in ``tools/strategy_fuzz.py``.
"""

from __future__ import annotations

import pytest


def _race(state, **overrides) -> None:
    state.session_type = "Race"
    state.mode_profile = "race"
    state.track_id = overrides.get("track_id", 13)
    state.current_lap = overrides["current_lap"]
    state.total_laps = overrides["total_laps"]
    state.player_position = overrides.get("position", 8)
    state.active_cars = overrides.get("active_cars", 20)
    state.weather = overrides.get("weather", "Clear")
    state.rain_next_15_pct = overrides.get("rain_pct", 0)
    state.safety_car = overrides.get("safety_car", "none")
    state.race_control_phase = overrides.get("phase", "green")
    state.tyre.compound = overrides.get("compound", "HARD")
    state.tyre.age_laps = overrides.get("age", 10)
    state.tyre.wear = overrides.get("wear", [30.0, 30.0, 30.0, 30.0])
    state.tyre_sets = overrides.get("sets", [])


# ---------------------------------------------------------------------------
# Wear is a percentage of a consumed tyre; there is no 102% worn.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_projected_wear_is_never_reported_above_fully_worn(stack):
    """A long stint on old tyres must not project past 100%.

    The projection accumulated unclamped, so the dashboard, the OBS overlay
    and the spoken radio call all rendered figures like "projects 102%".
    """
    store, _, strategy, _, _, _ = stack

    def setup(state):
        _race(
            state,
            current_lap=5,
            total_laps=70,
            compound="SOFT",
            age=30,
            wear=[86.0, 88.0, 90.0, 91.0],
            sets=[{"compound": "SOFT", "available": True}],
        )

    await store.mutate(setup)
    result = await strategy.recompute()

    for plan in result.get("plans", []):
        for key in ("projected_finish_wear_pct", "projected_max_wear_pct"):
            value = plan.get(key)
            assert value is None or 0.0 <= float(value) <= 100.0, (
                f"{key}={value} is not a physical wear state"
            )
        overshoot = plan.get("projected_wear_overshoot_pct")
        if overshoot is not None:
            assert float(overshoot) >= 0.0
            if float(overshoot) > 0.0:
                # The overshoot is the evidence the stint is over-extended, so
                # the plan must not simultaneously claim to be feasible.
                assert plan.get("feasible") is False


# ---------------------------------------------------------------------------
# A wet-tyre stop still has to earn back the pit lane.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_wet_stop_called_on_the_final_lap(stack):
    """Rain on the last lap must not trigger a stop that cannot pay back.

    The crossover fired purely on "it is raining and you are on slicks", with
    no regard for how many laps were left to recover the pit loss, so it
    called a ~20 s stop to gain grip for the run to the flag.
    """
    store, _, strategy, _, _, _ = stack

    def setup(state):
        _race(
            state,
            current_lap=51,
            total_laps=51,
            weather="Heavy rain",
            rain_pct=90,
            compound="MEDIUM",
            age=18,
            sets=[
                {"compound": "WET", "available": True},
                {"compound": "MEDIUM", "available": True},
            ],
        )

    await store.mutate(setup)
    result = await strategy.recompute()
    recommendation = result.get("recommended", {})

    assert not recommendation.get("stops_remaining"), (
        "called a stop with no laps left to recover the pit loss: "
        f"{recommendation.get('instruction')!r}"
    )
    crossover = result.get("weather_crossover") or {}
    if crossover:
        # The conditions must still be reported even though no stop is called.
        assert crossover.get("worth_stopping") is False
        assert crossover.get("box_lap") is None
        assert "cannot repay" in str(crossover.get("reason", ""))


@pytest.mark.asyncio
async def test_wet_stop_is_still_called_when_there_is_time_to_recover(stack):
    """The payback guard must not suppress a genuinely correct wet stop."""
    store, _, strategy, _, _, _ = stack

    def setup(state):
        _race(
            state,
            current_lap=10,
            total_laps=51,
            weather="Heavy rain",
            rain_pct=90,
            compound="MEDIUM",
            age=8,
            sets=[
                {"compound": "WET", "available": True},
                {"compound": "MEDIUM", "available": True},
            ],
        )

    await store.mutate(setup)
    result = await strategy.recompute()
    recommendation = result.get("recommended", {})

    assert str(recommendation.get("fit_compound")) in {"WET", "INTER"}
    assert recommendation.get("box_lap") == 10


# ---------------------------------------------------------------------------
# A disqualified car scores nothing, so legality outranks pace.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_illegal_stay_out_never_beats_a_legal_plan(stack):
    """Skipping a mandatory compound change must never be recommended.

    Skipping the stop always projects a better finish because it skips the
    pit loss. Ranking on projected position alone therefore selected the
    illegal plan: the engine recommended staying out for a projected P7 that
    would have been disqualified, over a legal P14 that actually scores.
    """
    store, _, strategy, _, _, _ = stack

    def setup(state):
        _race(
            state,
            current_lap=42,
            total_laps=53,
            compound="HARD",
            age=12,
            wear=[10.0, 10.0, 10.0, 10.0],
            sets=[
                {"compound": "HARD", "available": True, "wear_pct": 5.0,
                 "usable_life_laps": 40},
                # Legal but marginal: no plan is both legal and feasible, which
                # is exactly the fallback path that ranked illegal plans.
                {"compound": "MEDIUM", "available": True, "wear_pct": 95.0,
                 "usable_life_laps": 1},
            ],
        )

    await store.mutate(setup)
    result = await strategy.recompute()

    plans = result.get("plans", [])
    assert plans, "expected candidate plans"

    # The durable safety property: whenever any legal plan exists, the engine
    # must recommend a legal one. It must never trade a disqualification for a
    # better projected finish, however much faster skipping the stop looks.
    legal = [plan for plan in plans if plan.get("legal")]
    if legal:
        assert plans[0].get("legal") is True, (
            "an illegal plan outranked a legal one; a disqualified car scores "
            "nothing, so skipping a mandatory compound change must never win "
            "on projected position"
        )
        recommendation = result.get("recommended", {})
        fitted = [str(c).upper() for c in (recommendation.get("compounds") or [])[1:]]
        assert recommendation.get("stops_remaining") or not fitted, (
            "recommended a zero-stop plan while a legal stop was available and "
            "the dry-compound change was still outstanding"
        )


@pytest.mark.asyncio
async def test_unservable_compound_rule_is_spoken_not_hidden(stack):
    """Heading for disqualification must not sound like a routine call.

    With no eligible dry set left the engine still said only "Stay out to the
    finish.", so nothing in the spoken call revealed the race was about to end
    in a disqualification.
    """
    store, _, strategy, _, _, _ = stack

    def setup(state):
        _race(
            state,
            current_lap=42,
            total_laps=53,
            compound="HARD",
            age=12,
            wear=[40.0, 41.0, 42.0, 43.0],
            sets=[{"compound": "HARD", "available": True, "usable_life_laps": 30}],
        )

    await store.mutate(setup)
    result = await strategy.recompute()
    recommendation = result.get("recommended", {})
    instruction = str(recommendation.get("instruction", ""))

    if not result.get("plans", [{}])[0].get("legal", True):
        lowered = instruction.lower()
        assert "disqualif" in lowered or "compound" in lowered, (
            f"illegal plan reported as a routine call: {instruction!r}"
        )
