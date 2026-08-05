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
async def test_a_stop_that_serves_nothing_still_warns(stack):
    """An illegal plan that stops must warn too, not just an illegal stay-out.

    The first fix only covered the stay-out branch, so a plan that stopped and
    refitted the same dry compound produced a confident "Box lap 18 for HARD."
    while still finishing the race disqualified.
    """
    store, _, strategy, _, _, _ = stack

    def setup(state):
        _race(
            state,
            current_lap=14,
            total_laps=20,
            compound="HARD",
            age=16,
            wear=[80.0, 82.0, 84.0, 86.0],
            sets=[{"compound": "HARD", "available": True, "usable_life_laps": 25}],
        )

    await store.mutate(setup)
    result = await strategy.recompute()
    recommendation = result.get("recommended", {})
    plans = result.get("plans", [])

    if plans and not plans[0].get("legal", True):
        instruction = str(recommendation.get("instruction", "")).lower()
        assert "disqualif" in instruction or "compound" in instruction, (
            "a stop that cannot serve the mandatory change was announced as a "
            f"routine call: {recommendation.get('instruction')!r}"
        )


@pytest.mark.asyncio
async def test_red_flag_tyre_change_is_free_and_still_offered(stack):
    """The endgame payback guard must not suppress a free red-flag change.

    Under a red flag the field is stationary in the pit lane, so fitting wets
    for a wet restart costs nothing and is correct however few laps remain.
    """
    store, _, strategy, _, _, _ = stack

    def setup(state):
        _race(
            state,
            current_lap=53,
            total_laps=53,
            weather="Storm",
            rain_pct=100,
            phase="red_flag",
            compound="SOFT",
            age=20,
            sets=[{"compound": "WET", "available": True}],
        )

    await store.mutate(setup)
    result = await strategy.recompute()
    recommendation = result.get("recommended", {})

    assert float(result.get("pit_loss_s", 99)) == 0.0, (
        "a red-flag tyre change must not be priced as a green-flag stop"
    )
    base = (result.get("neutralisation") or {}).get("base_pit_loss_s")
    assert base is None or float(base) > 0.0, (
        "the green-flag pit cost must remain knowable under a red flag"
    )
    assert str(recommendation.get("fit_compound")) in {"WET", "INTER"}


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


# ---------------------------------------------------------------------------
# The generator itself, run small, so the invariants stay enforced in CI.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_randomized_scenarios_hold_every_invariant():
    """A bounded slice of the fuzz batch, so the checks run on every commit.

    The full sweep lives in ``tools/strategy_fuzz.py`` and is run by hand at
    thousands of scenarios; this keeps a fixed, seeded subset in the suite so
    a regression cannot land silently between sweeps.
    """
    from tools.strategy_fuzz import run_batch

    for seed in (7, 23):
        tested, violations = await run_batch(40, seed)
        errors = [item for item in violations if item.severity == "error"]
        assert tested == 40
        assert not errors, "\n".join(
            f"scenario {item.scenario_id} {item.check}: {item.detail}"
            for item in errors[:10]
        )


@pytest.mark.asyncio
async def test_randomized_adversarial_profiles_hold_every_invariant():
    """The forced high-risk profiles, which is where the real defects were."""
    from tools.strategy_fuzz import run_batch

    tested, violations = await run_batch(40, 7, adversarial=True)
    errors = [item for item in violations if item.severity == "error"]
    assert tested == 40
    assert not errors, "\n".join(
        f"scenario {item.scenario_id} {item.check}: {item.detail}"
        for item in errors[:10]
    )


# ---------------------------------------------------------------------------
# A tyre that never wears does not exist.
# ---------------------------------------------------------------------------


def _engine():
    from pitwall.strategy import StrategyEngine

    return StrategyEngine.__new__(StrategyEngine)


def test_single_wear_sample_does_not_override_the_prior():
    """One observation is noise, not a wear rate.

    The per-wheel path accepted personal history at any sample size while the
    scalar path required two. A single lap whose wear did not tick over
    therefore produced a 0%/lap tyre, a stint on it projected 0% wear at the
    finish, and that both looked ideal to the ranking and reached the radio as
    "the soft stint projects 0%".
    """
    engine = _engine()
    state = {"track_id": 7, "car_setup": {}, "tyre": {"compound": "MEDIUM"}}
    historical = {
        "compounds": {
            "SOFT": {
                "wheel_wear_per_lap_pct": [0.0, 0.0, 0.0, 0.0],
                "wear_sample_size": 1,
                "max_wear_per_lap_pct": 0.0,
            }
        }
    }

    rates, source, _, _ = engine._wheel_wear_rates(state, "SOFT", historical, 1.0)

    assert all(rate > 0.0 for rate in rates), (
        f"projected a tyre that never wears: {rates}"
    )
    assert source != "personal_per_wheel_history", (
        "a single sample must not be presented as personal history"
    )


def test_established_wear_history_is_still_used():
    """The guard must not discard genuinely earned personal history."""
    engine = _engine()
    state = {"track_id": 7, "car_setup": {}, "tyre": {"compound": "MEDIUM"}}
    historical = {
        "compounds": {
            "SOFT": {
                "wheel_wear_per_lap_pct": [4.1, 4.3, 4.8, 5.0],
                "wear_sample_size": 8,
                "max_wear_per_lap_pct": 5.0,
            }
        }
    }

    rates, source, samples, _ = engine._wheel_wear_rates(state, "SOFT", historical, 1.0)

    assert source == "personal_per_wheel_history"
    assert samples == 8
    assert rates == [4.1, 4.3, 4.8, 5.0]


def test_one_bad_wheel_falls_back_without_discarding_the_others():
    """A single implausible wheel must not throw away three good ones."""
    engine = _engine()
    state = {"track_id": 7, "car_setup": {}, "tyre": {"compound": "MEDIUM"}}
    historical = {
        "compounds": {
            "SOFT": {
                "wheel_wear_per_lap_pct": [4.1, 0.0, 4.8, 5.0],
                "wear_sample_size": 3,
                "max_wear_per_lap_pct": 5.0,
            }
        }
    }

    rates, source, _, _ = engine._wheel_wear_rates(state, "SOFT", historical, 1.0)

    assert source == "personal_per_wheel_history"
    assert rates[0] == 4.1 and rates[2] == 4.8 and rates[3] == 5.0
    assert rates[1] > 0.0, "the implausible wheel kept a zero rate"
