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
