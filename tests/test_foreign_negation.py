"""French, German and Spanish refusals must never lock a pit stop.

Drivers mix English radio jargon into their own language. Before the
negations below were recognised, "Non, je ne veux pas box lap 18 pour les
mediums" contained the English commitment words "box lap 18" and nothing
English to negate them, so it was locked as a stop on lap 18 for mediums.
"""

from __future__ import annotations

import pytest

from pitwall.brain import EngineerBrain
from pitwall.intent import has_negation

FOREIGN_REFUSALS = (
    "Non, je ne veux pas box lap 18 pour les mediums",
    "Je ne vais jamais box lap 18 pour les hards",
    "No quiero box lap 18 con los mediums",
    "Nunca box lap 18, me quedo con los hards",
    "No vamos a box lap 18 para los softs",
    "Nein, ich will nicht box lap 18 auf Medium",
    "Kein box lap 18 für die Softs",
    "Wir gehen niemals box lap 18 auf Hard",
)


@pytest.mark.parametrize("utterance", FOREIGN_REFUSALS)
def test_foreign_refusals_are_negations(utterance):
    assert has_negation(utterance), utterance


@pytest.mark.parametrize("utterance", FOREIGN_REFUSALS)
def test_foreign_refusals_never_produce_a_strategy_override(utterance):
    assert EngineerBrain._strategy_override_action(utterance, 12) is None, utterance


@pytest.mark.parametrize(
    "utterance",
    (
        "no problem, box lap 18 for mediums",
        "box lap 18 for mediums",
        "I am going to take hards on lap 18",
        "we're going non-stop to the end on the hards",
        "let's do the one-stop on mediums",
        "what is the gap to Verstappen",
        "the pace is good, box this lap for softs",
    ),
)
def test_english_commitments_are_not_mistaken_for_negations(utterance):
    assert not has_negation(utterance), utterance


def test_an_english_commitment_still_locks_after_the_change():
    committed = EngineerBrain._strategy_override_action(
        "no problem, box lap 18 for mediums", 12
    )
    assert committed is not None
    assert committed["next_box_lap"] == 18
    assert committed["next_compound"] == "MEDIUM"


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", FOREIGN_REFUSALS[:3] + FOREIGN_REFUSALS[5:6])
async def test_a_foreign_refusal_leaves_the_plan_unlocked(stack, utterance):
    store, database, _strategy, _setup, _analysis, tools = stack
    brain = EngineerBrain(store, tools, database)

    def apply(state):
        state.connected = True
        state.current_lap = 12
        state.total_laps = 28
        state.mode_profile = "race"

    await store.mutate(apply)
    answer = await brain._fast_answer(utterance)
    assert "locked" not in str(answer or "").lower(), (utterance, answer)
    state = await store.snapshot_analysis()
    override = dict(state.get("strategy_override", {}) or {})
    assert not override.get("locked"), (utterance, override)
    assert override.get("next_box_lap") is None, (utterance, override)
