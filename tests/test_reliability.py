import asyncio
import time

import pytest
from f1.packets import PacketCarTelemetry2Data, PacketHeader

from pitwall.config import Settings, settings
from pitwall.state import StateStore
from pitwall.udp import F1DatagramProtocol
from pitwall.voice import NativeVoiceController


def header(packet_id: int, session_uid: int = 123) -> PacketHeader:
    h = PacketHeader()
    h.packet_format = 2026
    h.game_year = 25
    h.packet_version = 1
    h.packet_id = packet_id
    h.session_uid = session_uid
    h.player_car_index = 0
    return h


def test_openai_key_loads_from_dotenv(tmp_path, monkeypatch):
    # Environment variables intentionally outrank dotenv values. Remove the
    # test-process sentinels so this test validates the dotenv path itself.
    for name in (
        "OPENAI_API_KEY",
        "PITWALL_OPENAI_API_KEY",
        "PITWALL_MODEL",
        "PITWALL_AUDIO_DEVICE",
    ):
        monkeypatch.delenv(name, raising=False)

    env = tmp_path / ".env"
    env.write_text(
        "OPENAI_API_KEY=test-key\nPITWALL_MODEL=gpt-5.4-mini\nPITWALL_AUDIO_DEVICE=3\n",
        encoding="utf-8",
    )
    loaded = Settings(_env_file=env)
    assert loaded.api_key == "test-key"
    assert loaded.model == "gpt-5.4-mini"
    assert loaded.audio_device == 3


@pytest.mark.asyncio
async def test_2026_telemetry2_updates_active_aero_and_overtake():
    store = StateStore()
    protocol = F1DatagramProtocol(store)
    packet = PacketCarTelemetry2Data()
    packet.header = header(16)
    car = packet.car_telemetry2_data[0]
    car.active_aero_mode = 2
    car.active_aero_available = 1
    car.active_aero_activation_distance = 140
    car.overtake_available = 1
    car.overtake_active = 1
    car.overtake_activation_distance = 90
    setattr(car, "2026_regulations", 1)
    car.driving_wrong_way = 0

    await protocol._handle(packet)
    snapshot = await store.snapshot()
    assert snapshot["regulations_2026"] is True
    assert snapshot["active_aero_mode"] == 2
    assert snapshot["overtake_active"] is True


@pytest.mark.asyncio
async def test_new_session_uid_resets_stale_session_state_but_keeps_ptt():
    store = StateStore()
    await store.update(session_uid=111, current_lap=20, ptt_mask=8, ptt_status="ready")
    await store.mutate(
        lambda s: s.proactive.update({"queued": 9, "last_call": "stale"})
    )
    await store.mutate(lambda s: s.traces.append({"d": 100.0}))

    await store.mark_packet(2026, 25, 222)
    snapshot = await store.snapshot()
    assert snapshot["session_uid"] == 222
    assert snapshot["current_lap"] == 0
    assert snapshot["traces"] == []
    assert snapshot["ptt_mask"] == 8
    assert snapshot["proactive"]["queued"] == 0
    assert snapshot["proactive"]["last_call"] == ""


@pytest.mark.asyncio
async def test_stale_telemetry_marks_connection_offline():
    store = StateStore()
    await store.mark_packet(2026, 25, 123)
    await store.update(last_packet_at=time.time() - 10)
    changed = await store.mark_disconnected_if_stale(3)
    snapshot = await store.snapshot()
    assert changed is True
    assert snapshot["connected"] is False
    assert snapshot["packet_rate_hz"] == 0.0


class _FakeBrain:
    async def ask(self, text: str) -> str:
        return "Copy."


class _FakeAudio:
    def stop_playback(self) -> None:
        pass


async def _wait_for_ptt_state(
    store: StateStore, expected: bool, timeout_s: float = 0.75
) -> dict:
    """Wait for an asynchronously scheduled controller transition to settle."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    snapshot = await store.snapshot()
    while bool(snapshot["ptt_pressed"]) is not expected:
        if asyncio.get_running_loop().time() >= deadline:
            return snapshot
        await asyncio.sleep(0.01)
        snapshot = await store.snapshot()
    return snapshot


@pytest.mark.asyncio
async def test_fast_ptt_release_does_not_leave_microphone_stuck(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "native_voice", False)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    store = StateStore()
    voice = NativeVoiceController(store, _FakeBrain(), _FakeAudio())  # type: ignore[arg-type]
    voice.mask = 4
    await voice.initialize()

    monkeypatch.setattr(settings, "ptt_release_mode", "explicit")
    monkeypatch.setattr(settings, "ptt_release_ignore_ms", 0)
    voice.on_button_status(4)
    assert (await _wait_for_ptt_state(store, True))["ptt_pressed"] is True
    await asyncio.sleep(0.09)
    voice.on_button_status(4)
    voice.on_button_status(0)

    snapshot = await _wait_for_ptt_state(store, False)
    assert snapshot["ptt_pressed"] is False
    await voice.shutdown()


@pytest.mark.asyncio
async def test_unrelated_button_events_do_not_release_udp_action_ptt(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "native_voice", False)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "ptt_release_mode", "heartbeat")
    monkeypatch.setattr(settings, "ptt_release_watchdog_s", 0.08)
    store = StateStore()
    voice = NativeVoiceController(store, _FakeBrain(), _FakeAudio())  # type: ignore[arg-type]
    voice.mask = 0x2000
    await voice.initialize()

    voice.on_button_status(0x2000)
    await asyncio.sleep(0.01)
    # Simulate throttle/brake/MFD/gear-related BUTN events that do not carry
    # the configured UDP Action bit. They must not close the radio.
    voice.on_button_status(0x0001)
    voice.on_button_status(0x0040)
    voice.on_button_status(0x0800)
    # Continued PTT heartbeat proves L3 is still held.
    voice.on_button_status(0x2000 | 0x0040)
    await asyncio.sleep(0.03)

    snapshot = await store.snapshot_live()
    assert snapshot["ptt_pressed"] is True

    # Once the PTT heartbeat disappears, the watchdog synthesizes release.
    await asyncio.sleep(0.10)
    snapshot = await store.snapshot_live()
    assert snapshot["ptt_pressed"] is False
    assert voice.mask == 0x2000


@pytest.mark.asyncio
async def test_live_snapshot_excludes_heavy_history_and_completed_laps():
    store = StateStore()

    def setup(state):
        state.drivers[0].active = True
        state.drivers[0].position = 1
        state.drivers[0].lap_history = [
            {"lap_num": n, "lap_ms": 90_000} for n in range(50)
        ]
        state.completed_laps = [
            {"lap_num": n, "lap_time_ms": 90_000} for n in range(80)
        ]
        state.traces = [{"d": float(n), "t": n / 60, "speed": 300} for n in range(6000)]

    await store.mutate(setup)
    live = await store.snapshot_live()
    analysis = await store.snapshot_analysis()

    assert live["completed_laps"] == []
    assert "lap_history" not in live["drivers"][0]
    assert len(live["traces"]) <= 1201
    assert len(analysis["completed_laps"]) == 80
    assert analysis["traces"] == []


@pytest.mark.asyncio
async def test_ptt_hard_cap_recovers_without_changing_saved_udp_action(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "native_voice", False)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "ptt_max_recording_s", 0.01)
    store = StateStore()
    voice = NativeVoiceController(store, _FakeBrain(), _FakeAudio())  # type: ignore[arg-type]
    voice.mask = 0x2000
    await voice.initialize()
    voice._signal_pressed = True
    voice.pressed_at = time.monotonic() - 1.0
    await store.update(ptt_pressed=True)

    await voice._recording_guard()

    snapshot = await store.snapshot_live()
    assert snapshot["ptt_pressed"] is False
    assert snapshot["ptt_mask"] == 0x2000
    assert voice.mask == 0x2000


@pytest.mark.asyncio
async def test_production_silence_mode_latches_across_brake_and_throttle_packets(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(settings, "native_voice", False)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "ptt_release_mode", "silence")
    monkeypatch.setattr(settings, "ptt_silence_release_s", 0.03)
    monkeypatch.setattr(settings, "ptt_max_recording_s", 1.0)
    store = StateStore()
    voice = NativeVoiceController(store, _FakeBrain(), _FakeAudio())  # type: ignore[arg-type]
    voice.mask = 0x2000
    await voice.initialize()

    voice.on_button_status(0x2000)
    await asyncio.sleep(0.02)
    # These model other controller actions arriving while the driver is still
    # talking. None may be interpreted as radio release.
    for status in (0x0001, 0x0040, 0x0800, 0):
        voice.on_button_status(status)
    await asyncio.sleep(0.02)
    assert (await store.snapshot_live())["ptt_pressed"] is True

    # Microphone VAD/silence, not another controller packet, ends the clip.
    voice._speech_detected = True
    voice.pressed_at = time.monotonic() - 0.8
    voice._last_voice_at = time.monotonic() - 0.2
    await asyncio.sleep(0.08)
    snapshot = await store.snapshot_live()
    assert snapshot["ptt_pressed"] is False
    assert snapshot["ptt_release_reason"] == "speech_silence"
    assert voice.mask == 0x2000
