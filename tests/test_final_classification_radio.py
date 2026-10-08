import pytest

from pitwall.brain import EngineerBrain


RESULT={"position":1,"laps":31,"points":25,"pit_stops":2,"best_lap_ms":91000,"penalties_s":0}
QUESTION="What is my final classification, and is the result confirmed?"


@pytest.mark.asyncio
@pytest.mark.parametrize("paused",[False,True])
@pytest.mark.parametrize("question",[QUESTION,"Final position."])
async def test_confirmed_result_survives_disconnected_and_paused_live_feed(stack,monkeypatch,paused,question):
    store,db,*_,tools=stack
    await store.mark_packet(2026,26,1234,packet_id=8)
    await store.update(final_classification=RESULT,connected=False,game_paused=paused,last_packet_at=1)
    brain=EngineerBrain(store,tools,db)
    async def no_provider(**kwargs):
        raise AssertionError("Exact confirmed result does not require a provider")
    monkeypatch.setattr(brain.router,"generate",no_provider)
    answer=await brain.ask(question)
    assert "Confirmed final classification: P1" in answer
    assert "31 laps completed" in answer and "25 points" in answer and "2 pit stops" in answer
    assert "best lap 1:31.000" in answer and "0 seconds of penalties" in answer
    assert "stale" not in answer and "cannot" not in answer
    for tool in (tools.get_session_overview,tools.get_standings,tools.get_race_picture):
        result=await tool()
        assert result["result_confirmed"] is True and result["final_classification"]==RESULT
        assert result["result_source"]=="game_final_classification"


@pytest.mark.asyncio
async def test_missing_result_is_not_inferred_from_live_first_place(stack):
    store,db,*_,tools=stack
    await store.update(player_position=1,current_lap=31,total_laps=31)
    answer=await EngineerBrain(store,tools,db).ask(QUESTION)
    assert "No final classification has been received" in answer
    assert (await tools.get_session_overview())["result_confirmed"] is False


@pytest.mark.asyncio
async def test_new_session_cannot_inherit_confirmed_result(stack):
    store,db,*_,tools=stack
    await store.mark_packet(2026,26,1234,packet_id=8)
    await store.update(final_classification=RESULT)
    await store.mark_packet(2026,26,5678,packet_id=1)
    answer=await EngineerBrain(store,tools,db).ask(QUESTION)
    assert "No final classification has been received" in answer


@pytest.mark.parametrize("question",[
    "What would my final result be if I boxed now?",
    "What was my final classification in the previous race?",
    "Compare my final position with Norris.",
    "What is the final classification for the whole field?",
])
def test_hypothetical_rival_and_history_queries_are_not_replaced(question):
    assert not EngineerBrain._requests_current_final_result({},question)


def test_named_driver_result_does_not_use_player_classification():
    state={"drivers":[{"name":"Norris","driver_id":54,"car_index":0,"position":2}]}
    assert not EngineerBrain._requests_current_final_result(state,"What is Norris's final classification?")


@pytest.mark.asyncio
@pytest.mark.parametrize("fresh", [True, False])
@pytest.mark.parametrize("question", ["Should I box now?", "Give me the full pit strategy and its best alternative."])
async def test_confirmed_finish_suppresses_live_pit_instructions_without_mutating_plan(stack, monkeypatch, fresh, question):
    store, db, _, _, _, tools = stack
    for group in (1, 2, 6, 7, 8, 10):
        await store.mark_packet(2026, 26, 1234, packet_id=group)
    old_plan = {"recommended": {"box_lap": 31, "fit_compound": "SOFT", "feasible": True, "legal": True}}
    await store.update(final_classification=RESULT, current_lap=31, total_laps=31, strategy=old_plan)
    if not fresh:
        await store.update(connected=False, last_packet_at=1)
    brain = EngineerBrain(store, tools, db)

    async def forbidden(*args, **kwargs):
        raise AssertionError("A confirmed finish needs neither a provider nor another live plan calculation")

    monkeypatch.setattr(brain.router, "generate", forbidden)
    monkeypatch.setattr(tools.strategy, "get_plan", forbidden)
    answer = await brain.ask(question)
    assert answer.startswith("Race complete; final classification P1 is confirmed")
    assert "no further racing pit stop or live strategy alternative is needed" in answer
    assert "stay out" not in answer.lower() and "finish the lap" not in answer.lower()
    result = await tools.get_pit_strategy()
    assert result["race_completed"] is True and result["planning_available"] is False
    assert result["recommended"] == {} and result["plans"] == []
    assert result["final_classification"] == RESULT
    assert (await store.snapshot_analysis())["strategy"] == old_plan


@pytest.mark.asyncio
async def test_new_session_and_unconfirmed_final_lap_still_allow_planning(stack, monkeypatch):
    store, db, _, _, _, tools = stack
    await store.mark_packet(2026, 26, 1234, packet_id=8)
    await store.update(final_classification=RESULT)
    await store.mark_packet(2026, 26, 5678, packet_id=1)
    await store.update(current_lap=31, total_laps=31)
    expected = {"recommended": {"box_lap": 31}}

    async def plan():
        return expected

    monkeypatch.setattr(tools.strategy, "get_plan", plan)
    assert await tools.get_pit_strategy() == expected
    assert EngineerBrain._finished_strategy_answer(await store.snapshot_analysis()) is None


@pytest.mark.parametrize("question", [
    "What if I box now?", "What was my pit strategy during this race?",
    "Compare my pit strategy with the previous race.", "What is the car ahead's pit strategy?",
    "Should Norris box now?", "Give me the best strategy for the next race.",
])
def test_terminal_pit_guard_preserves_hypothetical_history_and_other_driver_scope(question):
    state = {"drivers": [{"name": "Norris", "driver_id": 54}]}
    assert not EngineerBrain._requests_current_pit_instruction(state, question)
