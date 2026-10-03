"""Whole-request radio routing, setup scope and high-rate radio scheduling."""
import asyncio
import time
from contextlib import suppress
from types import SimpleNamespace

import pytest

from pitwall.brain import EngineerBrain
from pitwall.providers import ProviderResult
from pitwall.proactive import ProactiveEngineer
from pitwall.config import settings
from pitwall.setup_advisor import SetupAdvisor
from pitwall.state import StateStore
from pitwall.udp import F1DatagramProtocol
from tools import replay_demo as replay


@pytest.mark.asyncio
async def test_setup_scope_rebuilds_instead_of_multiplying_a_nudge(stack):
    store, _, _, setup, _, _ = stack
    await store.update(track_id=11, session_uid=91, connected=True, formula=13,
                       packet_group_freshness={"1": 1.0}, weather="Clear",
                       car_setup={"front_wing": 48, "rear_wing": 46, "on_throttle": 90, "fuel_load": 42})
    results = {level: await setup.generate("race", 11, level) for level in ("minimum", "moderate", "radical")}
    assert results["minimum"]["recommended"]["front_wing"] == 48
    assert 20 < results["moderate"]["recommended"]["front_wing"] < 48
    assert results["radical"]["recommended"]["front_wing"] == results["radical"]["foundational"]["front_wing"]
    for level, result in results.items():
        assert result["change_level"] == level
        assert result["recommended"]["fuel_load"] == 42
        assert len(result["recommended"]) >= 20
    assert not results["radical"]["pit_adjustment"]["available"]
    assert not (await setup.generate("race", 11, "invalid"))["available"]


@pytest.mark.asyncio
async def test_other_circuit_does_not_inherit_live_setup_or_handling(stack):
    store, _, _, setup, _, _ = stack
    baseline = await setup.generate("race", 11, "radical")
    await store.update(track_id=5, car_setup={"front_wing": 50}, feedback=[{"category": "understeer"}])
    await store.mutate(lambda s: setattr(s.tyre, "inner_temps_c", [110] * 4))
    result = await setup.generate("race", 11, "minimum")
    assert result["recommended"] == baseline["recommended"]
    assert not result["current"]
    assert not result["pace_review"]["available"]


def test_pace_review_reports_comparison_and_does_not_claim_setup_caused_it():
    state = {"player_car_index": 0, "completed_laps": [
        {"lap_num": 7, "lap_time_ms": 92000, "valid": True, "compound": "MEDIUM"},
        {"lap_num": 8, "lap_time_ms": 91000, "valid": True, "compound": "MEDIUM"},
        {"lap_num": 9, "lap_time_ms": 180000, "valid": True, "compound": "MEDIUM", "learning_exclusions": ["neutralised_lap"]},
    ], "drivers": [{"car_idx": 1, "current_lap": 10, "tyre_age": 6, "tyre_compound": "MEDIUM", "lap_history": [
        {"lap_num": 7, "lap_ms": 90000, "valid_flags": 1},
        {"lap_num": 8, "lap_ms": 90000, "valid_flags": 1},
    ]}]}
    result = SetupAdvisor._pace_review(state)
    assert result["delta_s"] == 1.5
    assert result["clean_laps"] == 2
    assert "does not establish a setup-caused loss" in result["summary"]
    state["drivers"][0]["restricted"] = True
    assert "delta_s" not in SetupAdvisor._pace_review(state)


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "Will the cars ahead need another pit stop?",
    "Are other cars in the entire field expected to stop again?",
    "How many pit stops will the field make?",
    "How many stops are left for me?",
    "The cars in front are pitting soon, right?",
    "What is my position going to be after everyone stops?",
    "What is the gap behind after our next stop?",
    "What is the weather doing to everyone's pit strategy?",
    "Is the car ahead saving battery for an attack?",
])
async def test_qualified_questions_reach_reasoning_without_mutating_commands(stack, question):
    store, db, _, _, _, tools = stack
    await store.update(connected=True, track_id=11, current_lap=18, total_laps=53, mode_profile="race")
    brain = EngineerBrain(store, tools, db)
    before = await store.snapshot_analysis()
    assert await brain._fast_answer(question) is None
    after = await store.snapshot_analysis()
    assert after["standing_instructions"] == before["standing_instructions"]
    assert after["strategy_override"] == before["strategy_override"]


@pytest.mark.asyncio
async def test_full_field_question_calls_the_forecast_tool_through_the_model(stack, monkeypatch):
    store, db, _, _, _, tools = stack
    await store.update(connected=True, mode_profile="race", current_lap=18, total_laps=53)
    def field(s):
        for i, driver in enumerate(s.drivers):
            driver.active = True
            driver.name = f"Driver {i}"
            driver.position = i + 1
            driver.tyre_compound = "MEDIUM"
            driver.tyre_age = 15
            driver.pit_stops = 1
    await store.mutate(field)
    brain = EngineerBrain(store, tools, db)
    called = []
    async def model(**kwargs):
        assert "entire field" in kwargs["prompt"]
        report = await kwargs["execute_tool"]("predict_rival_strategy", {"top_n": 24})
        assert len(report["rivals"]) == 23
        assert not report["truncated"]
        assert all(r["completed_stops"] == 1 and r["another_stop"] == "likely" for r in report["rivals"])
        called.append(report)
        return ProviderResult("Another stop looks likely across the field on tyre age, with low confidence.", "test", "test", 1, 1)
    monkeypatch.setattr(brain.router, "generate", model)
    result = await brain.ask("Will the entire field need another pit stop?")
    assert called and "Another stop" in result


@pytest.mark.asyncio
async def test_radio_snapshot_never_copies_trace_or_field_history():
    class History(list):
        def __deepcopy__(self, memo):
            raise AssertionError("radio copied a race history")
    store = StateStore()
    store.state.drivers[1].active = True
    store.state.drivers[1].lap_history = History([{"lap_num": 1}])
    store.state.traces = History([{"d": 1}])
    store.state.completed_laps = History([{"lap_num": 1}])
    store.state.analysis["line_history"] = History([{"lap_num": 1}])
    store.state.analysis["racing_line"] = {"current_line": History([{"x": 1}]), "summary": "Wide at exit"}
    store.state.strategy = {"plans": [{"value": i} for i in range(20)], "weather_crossover": {"trajectory": list(range(60))}}
    snapshot = await store.snapshot_radio()
    assert not snapshot["traces"] and not snapshot["completed_laps"]
    assert snapshot["analysis"]["racing_line"] == {"summary": "Wide at exit"}
    snapshot["strategy"]["weather_crossover"]["trajectory"].clear()
    assert len(store.state.strategy["weather_crossover"]["trajectory"]) == 60
    assert len(snapshot["strategy"]["plans"]) == 3


@pytest.mark.asyncio
async def test_live_graph_is_bounded_at_fractional_sample_multiples():
    store = StateStore()
    store.state.traces = [{"d": i, "speed": 270, "throttle": .9, "brake": 0, "slip": [1, 2, 3, 4]} for i in range(2399)]
    snapshot = await store.snapshot_live()
    assert len(snapshot["traces"]) <= 1201
    assert snapshot["traces"][-1]["d"] == 2398
    assert "slip" not in snapshot["traces"][0]
    assert len(store.state.traces) == 2399
    assert store.state.traces[0]["slip"] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_slow_packet_batch_yields_before_32_packets(monkeypatch):
    protocol = F1DatagramProtocol(StateStore(), queue_capacity=256)
    ticks = [0.0]
    monkeypatch.setattr("pitwall.udp.time", SimpleNamespace(monotonic=lambda: ticks[0]))
    monkeypatch.setattr("pitwall.udp.resolve", lambda data: data)
    handled = []
    async def handle(packet, received):
        handled.append(packet)
        ticks[0] += 0.003
    monkeypatch.setattr(protocol, "_handle", handle)
    for i in range(64):
        protocol.packet_queue.put_nowait(SimpleNamespace(data=i, health_key=None))
    task = asyncio.create_task(protocol._consume_packets())
    try:
        await asyncio.sleep(0)
        assert 0 < len(handled) < 32
        await asyncio.wait_for(protocol.packet_queue.join(), 1)
        assert handled == list(range(64))
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_proactive_penalty_reaches_voice_during_60hz_packet_stream(stack, monkeypatch):
    """Real parser, detection, queue and delivery; playback is a recording sink."""
    store, db, strategy, setup, _, tools = stack
    monkeypatch.setattr(settings, "proactive_narration_enabled", False)
    frame_now = [0]
    spoken = []

    class Voice:
        is_busy = False
        async def speak_text(self, text):
            spoken.append((frame_now[0], text))
            await asyncio.sleep(0.15)
            return True

    engineer = ProactiveEngineer(store, EngineerBrain(store, tools, db), Voice(), setup, strategy)
    protocol = F1DatagramProtocol(store)
    consumer = asyncio.create_task(protocol._consume_packets())
    cars = [replay.Car(i, spec) for i, spec in enumerate(replay.GRID)]
    for car in cars:
        car.lap, car.speed = 51, 260
    def send(packet):
        protocol.datagram_received(packet, ("127.0.0.1", 9999))
    try:
        send(replay.build_participants(cars, 4600, 1))
        send(replay.build_session(4600, 1, 57, 51))
        send(replay.build_car_status(cars, 4600, 1))
        send(replay.build_lap_data(cars, 4600, 1))
        await protocol.packet_queue.join()
        def seed_history(s):
            for d in s.drivers:
                d.lap_history = [{"lap_num": n, "lap_ms": 90000, "valid_flags": 1} for n in range(1, 51)]
                d.gap_history = [{"lap": n, "gap_s": 1.5} for n in range(180)]
            s.proactive["enabled"] = True
        await store.mutate(seed_history)
        await engineer._reset_for_session(replay.SESSION_UID)
        await engineer._detect(await store.snapshot_radio())
        await engineer.start()
        started = time.monotonic()
        for frame in range(1, 241):
            frame_now[0] = frame
            stamp = 4600 + frame / 60
            packet = replay.PacketLapData.from_buffer_copy(replay.build_lap_data(cars, stamp, frame + 1))
            packet.lap_data[replay.PLAYER_INDEX].penalties = 5 if frame >= 60 else 0
            send(bytes(packet))
            send(replay.build_motion(cars, stamp, frame + 1))
            send(replay.build_telemetry(cars, stamp, frame + 1))
            await asyncio.sleep(max(0, started + frame / 60 - time.monotonic()))
        await protocol.packet_queue.join()
        assert any(60 <= frame < 240 and "penalt" in text.lower() for frame, text in spoken), spoken
        assert store.state.packets_received == 724
        assert not protocol.receiver_queue_drops
        assert not store.state.last_error
    finally:
        await engineer.stop()
        await strategy.stop()
        consumer.cancel()
        with suppress(asyncio.CancelledError):
            await consumer
