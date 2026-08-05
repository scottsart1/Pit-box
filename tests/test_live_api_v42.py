from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pitwall.api.live import _project_topics, create_live_router
from pitwall.state import StateStore


def _test_app(store: StateStore) -> FastAPI:
    app = FastAPI()
    app.include_router(create_live_router(store, configured_max_hz=10))

    @app.post("/_test/burst")
    async def burst() -> dict[str, int]:
        for speed in range(1, 26):
            await store.update(speed_kph=speed)
        return {"speed": 25}

    return app


@pytest.mark.asyncio
async def test_topic_projection_is_bounded_and_omits_heavy_histories() -> None:
    store = StateStore()

    def seed(state) -> None:
        state.player_car_index = 0
        state.drivers[0].active = True
        state.drivers[0].is_player = True
        state.drivers[0].name = "Player"
        state.drivers[0].lap_history = [{"lap": item} for item in range(500)]
        state.traces = [{"d": item, "secret_raw": "x"} for item in range(2_000)]
        state.completed_laps = [{"lap": item} for item in range(100)]
        state.radio_log = [
            {"role": "engineer", "text": f"message {item}"}
            for item in range(80)
        ]
        state.last_error = "provider failed with OPENAI_API_KEY=do-not-stream"

    await store.mutate(seed)
    snapshot = await store.snapshot_live()
    topics = _project_topics(
        snapshot,
        ("session", "player", "classification", "engineer"),
    )

    assert "traces" not in topics["session"]
    assert "completed_laps" not in topics["session"]
    assert "lap_history" not in topics["classification"]["drivers"][0]
    assert len(topics["engineer"]["radio_log"]) == 20
    assert topics["engineer"]["has_error"] is True
    assert "OPENAI_API_KEY" not in str(topics)


def test_live_socket_subscribes_caps_rate_and_coalesces_latest_state() -> None:
    store = StateStore()
    with (
        TestClient(_test_app(store)) as client,
        client.websocket_connect("/api/v1/live/ws") as websocket,
    ):
        websocket.send_json(
            {
                "type": "subscribe",
                "topics": ["session", "player", "classification", "network"],
                "max_hz": 30,
            }
        )
        initial = websocket.receive_json()

        assert initial["type"] == "live.snapshot"
        assert initial["sequence"] == 1
        assert initial["payload"]["reason"] == "initial_subscription"
        assert initial["payload"]["subscription"]["effective_max_hz"] == 10
        assert set(initial["payload"]["topics"]) == {
            "session",
            "player",
            "classification",
            "network",
        }
        assert initial["payload"]["topics"]["network"]["available"] is False

        response = client.post("/_test/burst")
        assert response.status_code == 200
        delta = websocket.receive_json()

        assert delta["type"] == "live.delta"
        assert delta["sequence"] == 2
        assert "player" in delta["payload"]["changed_topics"]
        assert delta["payload"]["topics"]["player"]["current"]["speed_kph"] == 25
        assert delta["coalesced_revisions"] >= 24
        assert delta["state_revision"] - delta["base_revision"] >= 25


def test_snapshot_gap_request_and_resubscribe_return_complete_snapshots() -> None:
    store = StateStore()
    with (
        TestClient(_test_app(store)) as client,
        client.websocket_connect("/api/v1/live/ws") as websocket,
    ):
        websocket.send_json(
            {"type": "subscribe", "topics": ["session"], "max_hz": 5}
        )
        assert websocket.receive_json()["sequence"] == 1

        websocket.send_json({"type": "snapshot.request", "after_sequence": 0})
        gap_snapshot = websocket.receive_json()
        assert gap_snapshot["type"] == "live.snapshot"
        assert gap_snapshot["sequence"] == 2
        assert gap_snapshot["payload"]["reason"] == "client_sequence_gap"

        websocket.send_json(
            {"type": "subscribe", "topics": ["flags"], "max_hz": 2}
        )
        resubscribed = websocket.receive_json()
        assert resubscribed["type"] == "live.snapshot"
        assert resubscribed["sequence"] == 3
        assert resubscribed["payload"]["reason"] == "resubscribed"
        assert set(resubscribed["payload"]["topics"]) == {"flags"}


def test_invalid_first_message_is_rejected_without_streaming_state() -> None:
    store = StateStore()
    with (
        TestClient(_test_app(store)) as client,
        client.websocket_connect("/api/v1/live/ws") as websocket,
    ):
        websocket.send_json(
            {
                "type": "subscribe",
                "topics": ["session"],
                "max_hz": 10,
                "unexpected": "rejected by LiveSubscription",
            }
        )
        message = websocket.receive_json()

        assert message["type"] == "subscription.error"
        assert message["payload"]["code"] == "invalid_subscription"
        assert "topics" not in message["payload"]


def test_live_router_rejects_unsafe_rate_configuration() -> None:
    store = StateStore()
    for value in (0, 31):
        try:
            create_live_router(store, configured_max_hz=value)
        except ValueError as exc:
            assert "configured_max_hz" in str(exc)
        else:  # pragma: no cover - defensive failure message
            raise AssertionError(f"configured_max_hz={value} should fail")
