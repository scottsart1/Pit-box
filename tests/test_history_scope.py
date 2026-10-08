import time

import pytest

from pitwall.state import DriverState


def history():
    return [{"lap_num": number, "lap_ms": (91000 if number==2 else 80000 if number==3 else 95000+number),
             "s1_ms": 30000,"s2_ms": 30000,"s3_ms": 31000,"valid_flags": 14 if number==3 else 15}
            for number in range(1,30)]


async def seed(store, rows):
    await store.mark_packet(2026,26,1234,packet_id=11)
    await store.update(session_time_s=500,current_lap=30,player_car_index=6)
    def apply(state):
        state.drivers[6]=DriverState(6,"Player",active=True,is_player=True,current_lap=30,
                                    lap_history=rows,history_updated_at=498)
    await store.mutate(apply)


@pytest.mark.asyncio
async def test_session_best_is_computed_before_recent_window_and_uses_validity_bit_zero(stack):
    store,*_,tools=stack
    await seed(store,history())
    result=await tools.get_driver_lap_history("me",5)
    assert [lap["lap_num"] for lap in result["laps"]]==[25,26,27,28,29]
    assert result["session_best_valid_lap"]["lap_num"]==2
    assert result["session_best_valid_lap"]["lap_ms"]==91000
    assert result["history_complete_to_current_lap"] is True
    assert result["session_uid"]=="1234" and result["history_source"]=="game_session_history"
    assert 2 <= result["staleness_s"] < 3  # Both timestamps use game time, plus wall silence.
    pace=await tools.get_rival_pace_analysis("me")
    assert pace["best_lap"] != pace["session_best_valid_lap"]["lap"]
    assert "recent returned valid laps only" in pace["best_lap_scope"]


@pytest.mark.asyncio
@pytest.mark.parametrize("rows",[history()[-5:],history()[:-1],[]])
async def test_missing_earlier_or_latest_history_does_not_claim_a_full_session_best(stack,rows):
    store,*_,tools=stack
    await seed(store,rows)
    result=await tools.get_driver_lap_history("me",5)
    assert result["session_best_valid_lap"] is None
    assert result["history_complete_to_current_lap"] is False
    assert (result["best_valid_observed_lap"] is not None)==bool(rows)


@pytest.mark.asyncio
async def test_best_history_does_not_leak_across_session_change(stack):
    store,*_,tools=stack
    await seed(store,history())
    await store.mark_packet(2026,26,5678,packet_id=1)
    result=await tools.get_driver_lap_history("me",5)
    assert not result.get("session_best_valid_lap")


@pytest.mark.asyncio
async def test_history_age_includes_feed_silence_without_mixing_wall_and_game_clocks(stack):
    store,*_,tools=stack
    await seed(store,history())
    await store.update(last_packet_at=time.time()-10)
    result=await tools.get_driver_lap_history("me",5)
    assert 12 <= result["staleness_s"] < 13
