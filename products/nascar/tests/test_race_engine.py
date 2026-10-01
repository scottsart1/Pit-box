import math
import time

import pytest
from pydantic import ValidationError

from yourpitbox_nascar.analysis import field_strategy, fuel_plan, fuel_rate, long_run, pit_options, setup_review
from yourpitbox_nascar.engine import RaceEngine
from yourpitbox_nascar.models import Flag, Handling, Lap, Observation, RaceConfig, Sample
from yourpitbox_nascar.store import Store


@pytest.fixture
def engine(tmp_path):
    store = Store(tmp_path)
    now = [1000.0]
    engine = RaceEngine(store, clock=lambda: now[0])
    engine.start(RaceConfig(fuel_burn_green=2, fuel_burn_yellow=.5))
    engine.test_now = now
    yield engine
    store.close()


def observe(engine, seq, source="adapter", confidence=1, **sample):
    return engine.ingest(Observation(session_id=engine.sid, sequence=seq, source=source, confidence=confidence, sample=sample))


def test_unknown_fuel_never_means_zero_or_safe():
    plan = fuel_plan(RaceConfig(), {}, [])
    assert plan["status"] == "unknown"
    assert plan["fuel_laps"] is None
    assert plan["minimum_stops"] is None


def test_fuel_reserve_and_multiple_stops():
    cfg = RaceConfig(total_laps=100, fuel_burn_green=2, reserve_laps=3)
    plan = fuel_plan(cfg, {"completed_laps": 20, "fuel_pct": 40}, [])
    assert plan["needed_pct"] == 166
    assert plan["minimum_stops"] == 2
    assert plan["margin_pct"] == -126


def test_measured_burn_not_multiplied_twice():
    cfg = RaceConfig(total_laps=100, fuel_multiplier=4, fuel_burn_green=2, overtime_enabled=False)
    assert fuel_plan(cfg, {"completed_laps": 90, "fuel_pct": 25}, [])["needed_pct"] == 20


def test_caution_is_an_assumption_not_free_fuel():
    cfg = RaceConfig(fuel_burn_green=2, fuel_burn_yellow=.5, overtime_enabled=False)
    plan = fuel_plan(cfg, {"completed_laps": 90, "fuel_pct": 15}, [], yellow_laps=4)
    assert plan["needed_pct"] == 14
    cfg.fuel_burn_yellow = None
    assert fuel_plan(cfg, {"completed_laps": 90, "fuel_pct": 15}, [], yellow_laps=4)["status"] == "unknown"


def test_overtime_does_not_invent_finish():
    plan = fuel_plan(RaceConfig(fuel_burn_green=2), {"completed_laps": 103, "flag": "green", "fuel_pct": 4}, [])
    assert plan["overtime"]
    assert plan["remaining_laps"] is None
    assert plan["minimum_stops"] is None


def test_white_flag_one_partial_lap_without_overtime_reserve():
    plan = fuel_plan(RaceConfig(fuel_burn_green=2), {"completed_laps": 103, "lap_fraction": .4, "flag": "white", "fuel_pct": 2}, [])
    assert plan["remaining_laps"] == pytest.approx(.6)
    assert plan["needed_pct"] == pytest.approx(1.2)
    assert plan["reserve_laps"] == 0


def test_stage_boundaries_do_not_change_flag(engine):
    engine.config = RaceConfig(stage_ends=[25, 50])
    observe(engine, 1, completed_laps=25, flag="green")
    snap = engine.snapshot()
    assert snap["fuel"]["stage_end"] == 50
    assert snap["values"]["flag"] == "green"


@pytest.mark.parametrize("stages", [[50,25],[25,25],[0],[100],[101]])
def test_bad_stages_rejected(stages):
    with pytest.raises(ValidationError):
        RaceConfig(total_laps=100, stage_ends=stages)


def test_pit_closed_survives_critical_fuel(engine):
    observe(engine, 1, flag="yellow", fuel_pct=1, completed_laps=90, pit_open=False)
    engine.proactive()
    snap = engine.snapshot()
    assert snap["pits"]["access"] is False
    assert "closed" in snap["radio"][-1]["text"]
    assert "box" not in snap["radio"][-1]["text"].lower()


def test_pit_service_uses_measured_parallel_times():
    cfg = RaceConfig(four_tire_s=12, two_tire_s=7, fuel_fill_pct_s=5, green_pit_loss_s=25)
    result = pit_options(cfg, {"flag":"green", "pit_open": True, "fuel_pct":50}, {"needed_pct":75}, 25)
    assert result["options"][0]["service_s"] == 12
    assert result["options"][0]["total_loss_s"] == 37
    cfg.service_parallel = False
    assert pit_options(cfg, {"flag":"green"}, {}, 25)["options"][0]["service_s"] == 17


def test_opponent_last_pit_is_not_a_fuel_prediction():
    result = field_strategy({"opponents":[{"car":"22","position":1,"last_pit_lap":5,"gap_s":1}]}, {"remaining_laps":10})
    assert result["assessments"] == []
    assert result["unknown_cars"] == ["22"]


def test_lapped_opponent_still_drives_until_leader_finish():
    result = field_strategy({"opponents":[{"car":"22","position":30,"laps_down":4,"fuel_laps":8}]}, {"remaining_laps":10})
    assert result["assessments"][0]["assessment"] == "short on reported fuel"


def test_lapped_player_needs_leader_progress():
    cfg=RaceConfig(fuel_burn_green=2,overtime_enabled=False)
    unknown=fuel_plan(cfg,{'completed_laps':90,'fuel_pct':30,'laps_down':2},[])
    assert unknown['remaining_laps'] is None
    known=fuel_plan(cfg,{'completed_laps':90,'leader_completed_laps':92,'lap_fraction':.9,'fuel_pct':30,'laps_down':2},[])
    assert known['remaining_laps']==8
    assert known['needed_pct']==16
    assert known['latest_pit_completed_lap']==104


def test_full_40_car_field_and_unique_positions():
    assert len(Sample(opponents=[{"car":str(i),"position":i} for i in range(1,41)]).opponents) == 40
    with pytest.raises(ValidationError):
        Sample(opponents=[{"car":"4","position":1},{"car":"5","position":1}])


def test_stale_fuel_cannot_trigger_calls(engine):
    observe(engine, 1, completed_laps=97, fuel_pct=1, flag="green")
    engine.test_now[0] += 9
    engine.proactive()
    snap = engine.snapshot()
    assert snap["values"]["fuel_pct"] is None
    assert snap["fuel"]["status"] == "unknown"
    assert not snap["radio"]


def test_fresh_low_fuel_still_warns_when_race_control_is_unknown(engine):
    observe(engine,1,completed_laps=80,fuel_pct=2)
    engine.proactive()
    assert engine.radio[-1]['kind']=='urgent'
    assert 'unconfirmed' in engine.radio[-1]['text']
    assert 'Confirm pit access' in engine.radio[-1]['text']
    assert 'box' not in engine.radio[-1]['text'].lower()


def test_manual_pit_access_expires_quickly_and_lap_advance_expires_old_fuel(engine):
    observe(engine,1,source='manual',completed_laps=10,fuel_pct=50,flag='green',pit_open=True)
    engine.test_now[0]+=9
    snap=engine.snapshot()
    assert snap['values']['fuel_pct']==50
    assert snap['values']['pit_open'] is None
    observe(engine,2,source='manual',completed_laps=11)
    assert engine.snapshot()['fuel']['fuel_laps'] is None


def test_partial_tire_frame_does_not_refresh_other_corners(engine):
    observe(engine, 1, tires={"lf":90, "rf":60})
    engine.test_now[0] += 9
    observe(engine, 2, tires={"lf":89})
    snap = engine.snapshot()
    assert snap["values"]["tires"] == {"lf":89,"rf":None}
    observe(engine, 3, tires=None)
    observe(engine, 4, tires={"rr":50})
    assert engine.snapshot()["values"]["tires"]["rr"] == 50


def test_order_session_confidence_and_lap_regression(engine):
    assert observe(engine,1,completed_laps=4)["accepted"]
    assert not observe(engine,1,completed_laps=5)["accepted"]
    assert not observe(engine,2,confidence=.5, fuel_pct=30)["accepted"]
    assert not observe(engine,3,completed_laps=3)["accepted"]
    with pytest.raises(ValueError):
        engine.ingest(Observation(session_id="old-session",sequence=7,sample={"fuel_pct":30}))


def test_refuel_and_caution_transitions_do_not_train_green_rate(engine):
    observe(engine,1,completed_laps=0,fuel_pct=100,flag="green")
    observe(engine,2,completed_laps=1,fuel_pct=98,flag="green",last_lap_s=30)
    observe(engine,3,completed_laps=1,flag="yellow")
    observe(engine,4,completed_laps=2,fuel_pct=97,flag="yellow",last_lap_s=50)
    observe(engine,5,completed_laps=3,fuel_pct=96.5,flag="yellow",last_lap_s=60)
    observe(engine,6,completed_laps=3,fuel_pct=100,in_pit=True)
    observe(engine,7,completed_laps=4,fuel_pct=99,flag="yellow",last_lap_s=70,in_pit=False)
    assert [x.clean for x in engine.laps] == [True,False,True,False]
    assert engine.laps[2].flag == Flag.YELLOW


def test_three_clean_samples_learn_separate_burn(engine):
    for n in range(5):
        observe(engine,n,completed_laps=n,fuel_pct=100-n*2.5,flag="green",last_lap_s=30)
    snap = engine.snapshot()
    assert snap["fuel"]["green"]["value"] == 2.5
    assert snap["fuel"]["green"]["source"] == "measured"
    assert snap["fuel"]["yellow"]["value"] == .5


def test_lost_feed_resets_lap_anchor(engine):
    observe(engine,0,completed_laps=0,fuel_pct=100,flag="green")
    engine.test_now[0] += 50
    observe(engine,1,completed_laps=1,fuel_pct=98,flag="green")
    assert not engine.laps


def test_missing_flag_does_not_train_green_even_when_fuel_feed_continues(engine):
    observe(engine,0,completed_laps=0,fuel_pct=100,flag='green')
    for n in range(1,6):
        engine.test_now[0]+=3
        observe(engine,n,completed_laps=n,fuel_pct=100-n*2)
    assert [x.clean for x in engine.laps] == [True,True,False,False,False]
    assert engine.snapshot()['fuel']['green']['source']=='driver estimate'


def test_pit_entry_remains_excluded_until_exit_is_observed(engine):
    observe(engine,0,completed_laps=0,fuel_pct=100,flag='green',in_pit=True)
    observe(engine,1,completed_laps=1,fuel_pct=99,flag='green')
    observe(engine,2,completed_laps=2,fuel_pct=98,flag='green',in_pit=False)
    observe(engine,3,completed_laps=3,fuel_pct=96,flag='green')
    assert [x.clean for x in engine.laps]==[False,False,True]


def test_reopen_is_history_not_live(engine):
    observe(engine,1,completed_laps=10,fuel_pct=50,flag="green")
    engine.flush()
    engine.resume(engine.sid)
    assert engine.snapshot()["values"]["fuel_pct"] is None
    assert engine.snapshot()["last_observed"]["fuel_pct"] == 50


def test_demo_cannot_pollute_driver(engine):
    sid = engine.sid
    with pytest.raises(ValueError):
        observe(engine,1,source="demo",fuel_pct=50)
    engine.start(RaceConfig(mode="demo"))
    with pytest.raises(ValueError):
        observe(engine,1,source="manual",fuel_pct=20)
    assert engine.store.read(sid)["laps"] == []


def test_clean_pace_excludes_incidents_and_caution():
    laps = [Lap(number=n,time_s=30+n*.05,flag="green") for n in range(1,11)]
    laps += [Lap(number=11,time_s=2,flag="yellow"),Lap(number=12,time_s=1,flag="green",clean=False)]
    run = long_run(laps)
    assert run["count"] == 10
    assert run["best_s"] == 30.05
    assert run["stints"][0]["trend_s_per_lap"] == .05


def test_setup_scope_and_unavailable_controls():
    minimum = setup_review(Handling(scope="minimum"),RaceConfig(),[])
    radical = setup_review(Handling(scope="radical",available_controls=["wedge","pressures","bars","springs","camber"]),RaceConfig(),[])
    assert len(minimum["actions"]) == 1
    assert len(radical["actions"]) > len(minimum["actions"])
    assert len(radical["test_plan"]) > len(minimum["test_plan"])
    road = setup_review(Handling(),RaceConfig(track_type="road"),[])
    assert all(x["control"] != "wedge" for x in road["actions"])
    assert setup_review(Handling(available_controls=[]),RaceConfig(),[])["actions"] == []


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_inputs_rejected(value):
    with pytest.raises(ValidationError):
        Sample(fuel_pct=value)
