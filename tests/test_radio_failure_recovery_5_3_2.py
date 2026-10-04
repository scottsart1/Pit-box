from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AsyncOpenAI

from pitwall import providers
from pitwall.config import Settings, settings
from pitwall.providers import (
    OpenAIResponsesProvider,
    ProviderConfigurationError,
    ProviderDeadlineError,
    ProviderFailure,
    ProviderRequestError,
    ProviderResponseError,
    ProviderResult,
    ProviderRouter,
)
from pitwall.session_guard import SessionChangedError
from pitwall.state import StateStore
from pitwall.voice import (
    BRAIN_FALLBACK_LINE,
    NativeVoiceController,
    _brain_failure_feedback,
)


def function_schema(name):
    return {
        "type": "function", "name": name, "description": name,
        "parameters": {
            "type": "object", "properties": {}, "required": [], "additionalProperties": False,
        },
        "strict": True,
    }


def response_payload(output):
    return {
        "id": "resp_test", "object": "response", "created_at": 1,
        "model": "gpt-6-luna", "status": "completed", "output": output,
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }


def function_output(name, sequence):
    return {
        "type": "function_call", "id": f"fc_{sequence}",
        "call_id": f"call_{sequence}", "name": name, "arguments": "{}",
    }


@pytest.mark.asyncio
async def test_final_existing_openai_round_answers_from_tools_instead_of_discarding_them():
    requests = []
    returned = {
        "get_session_overview": {"track": "Madrid", "lap": 12},
        "get_cars_ahead_progress": {"driver": "Norris", "gap_s": 1.4},
        "get_weather": {"weather": "dry"},
    }

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if body.get("tool_choice") == "none":
            evidence = {
                item["call_id"]: json.loads(item["output"])
                for item in body["input"] if item.get("type") == "function_call_output"
            }
            assert evidence == {"call_1": returned["get_session_overview"], "call_2": returned["get_cars_ahead_progress"]}
            text = f"{evidence['call_2']['driver']} is {evidence['call_2']['gap_s']} seconds ahead."
            output = [{
                "type": "message", "id": "msg_answer", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }]
        else:
            # The old provider spends all three rounds collecting tools, then
            # throws away their successful outputs without asking for an answer.
            name = list(returned)[len(requests) - 1]
            output = [function_output(name, len(requests))]
        return httpx.Response(200, json=response_payload(output))

    executed = []

    async def execute(name, arguments):
        executed.append(name)
        return returned[name]

    async with AsyncOpenAI(api_key="test", http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))) as client:
        provider = OpenAIResponsesProvider(Settings(_env_file=None), client=client)
        result = await provider.generate(
            prompt="Who am I chasing?", instructions="Use measured evidence.",
            route="normal", effort="low", tools=[function_schema(name) for name in returned],
            execute_tool=execute, max_rounds=3,
        )
    assert result.text == "Norris is 1.4 seconds ahead."
    assert result.tool_rounds == 2
    assert len(requests) == 3
    assert executed == ["get_session_overview", "get_cars_ahead_progress"]
    assert "tool_choice" not in requests[0] and "tool_choice" not in requests[1]
    assert requests[2]["tool_choice"] == "none"


@pytest.mark.asyncio
async def test_provider_cannot_execute_a_late_mutating_tool_after_final_synthesis():
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        name = "get_session_overview" if len(requests) == 1 else "record_lap_observation"
        return httpx.Response(200, json=response_payload([function_output(name, len(requests))]))

    execute = AsyncMock(return_value={"lap": 3})
    async with AsyncOpenAI(api_key="test", http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))) as client:
        provider = OpenAIResponsesProvider(Settings(_env_file=None), client=client)
        with pytest.raises(ProviderResponseError, match="final answer"):
            await provider.generate(
                prompt="review lap", instructions="Use telemetry.", route="normal", effort="low",
                tools=[function_schema("get_session_overview"), function_schema("record_lap_observation")],
                execute_tool=execute, max_rounds=2,
            )
    execute.assert_awaited_once_with("get_session_overview", {})
    assert len(requests) == 2
    assert requests[-1]["tool_choice"] == "none"


@pytest.mark.asyncio
async def test_single_round_tool_budget_rejects_before_requests_or_side_effects():
    client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock()))
    execute = AsyncMock()
    provider = OpenAIResponsesProvider(Settings(_env_file=None), client=client)
    with pytest.raises(ProviderConfigurationError, match="at least two rounds"):
        await provider.generate(
            prompt="log context", instructions="test", route="normal", effort="low",
            tools=[function_schema("record_lap_observation")], execute_tool=execute, max_rounds=1,
        )
    client.responses.create.assert_not_awaited()
    execute.assert_not_awaited()


class ScriptedProvider:
    available = True

    def __init__(self, outcomes, name="openai"):
        self.name = name
        self.outcomes = list(outcomes)
        self.calls = 0

    async def generate(self, **_):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return ProviderResult(outcome, self.name, "test-model", 1.0)


def router_with(provider, **overrides):
    config = Settings(
        _env_file=None,
        llm_provider="openai",
        llm_fallback_provider=overrides.pop("llm_fallback_provider", "none"),
        **overrides,
    )
    return ProviderRouter(config, providers={"openai": provider})


async def request(router):
    return await router.generate(
        prompt="private driver request",
        instructions="test",
        route="normal",
        effort="low",
        tools=[],
        execute_tool=AsyncMock(return_value={}),
        max_rounds=1,
    )


def voice_with(brain, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "voice_realtime_enabled", False)
    audio = SimpleNamespace(play_ack=AsyncMock(), stop_playback=lambda: None)
    voice = NativeVoiceController(StateStore(), brain, audio)
    monkeypatch.setattr(voice, "speak_text", AsyncMock(return_value=True))
    return voice


@pytest.mark.asyncio
async def test_inner_timeout_is_not_misreported_as_the_route_deadline():
    router = router_with(ScriptedProvider([TimeoutError("socket read timed out")]))
    with pytest.raises(ProviderRequestError) as caught:
        await request(router)
    assert caught.value.kind == "timeout"
    assert "exceeded the" not in str(caught.value)
    failure = router.status()["recent_failures"][0]
    assert failure["error_type"] == "TimeoutError"
    assert failure["kind"] == "timeout"


@pytest.mark.asyncio
async def test_actual_route_deadline_cancels_provider_and_records_precise_cause():
    cancelled = asyncio.Event()

    class SlowProvider:
        available = True

        async def generate(self, **_):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    router = router_with(SlowProvider(), llm_normal_deadline_s=0.02)
    with pytest.raises(ProviderRequestError) as caught:
        await request(router)
    assert caught.value.kind == "deadline"
    assert cancelled.is_set()
    assert router.status()["recent_failures"][0]["error_type"] == "ProviderDeadlineError"


@pytest.mark.parametrize(
    ("remaining", "spoken_seconds"),
    [
        ((20.001 + 20) - 20.001, 20),
        (20.0, 20),
        (19.2, 20),
        (20.2, 21),
        (20.000001, 21),
        (0.01, 1),
        (1e-10, 1),
    ],
)
def test_cooldown_speech_normalizes_clock_precision_but_rounds_up_real_fractions(
    remaining, spoken_seconds
):
    failure = ProviderRequestError([
        ProviderFailure(
            "openai", "cooldown", "Service cooldown", attempted=False,
            retry_after_s=remaining,
        ),
    ])
    assert _brain_failure_feedback(failure) == (
        "The engineer service is temporarily unavailable. "
        f"Try again in {spoken_seconds} seconds."
    )


@pytest.mark.asyncio
async def test_cooldown_does_not_contact_provider_or_request_immediate_repeat(
    monkeypatch, tmp_path
):
    # A same-tick clock can leave a tiny positive remainder above 20 seconds.
    # Patch this module's clock only, keeping asyncio's deadline clock real.
    fixed_now = 20.001
    monkeypatch.setattr(providers, "time", SimpleNamespace(
        monotonic=lambda: fixed_now,
        time=providers.time.time,
        perf_counter=providers.time.perf_counter,
    ))
    provider = ScriptedProvider([TimeoutError(), TimeoutError(), "Recovered."])
    router = router_with(provider, llm_failure_cooldown_s=20)
    for _ in range(2):
        with pytest.raises(ProviderRequestError):
            await request(router)
    assert router.circuits["openai"].blocked_until - fixed_now > 20

    async def ask(_):
        return (await request(router)).text

    brain = SimpleNamespace(classify_request=lambda _: "normal", ask=ask)
    voice = voice_with(brain, monkeypatch, tmp_path)
    await voice._run_command("private follow-up", "wake")
    assert provider.calls == 2
    spoken = voice.speak_text.await_args.args[0]
    assert "Try again in 20 seconds" in spoken
    assert "go again" not in spoken
    state = await voice.store.snapshot_live()
    assert state["radio_latency"]["error_kind"] == "cooldown"
    assert 19 <= state["radio_latency"]["retry_after_s"] <= 20
    assert len(router.status()["recent_failures"]) == 2

    # Once the known cooldown expires, a new request can recover normally.
    router.circuits["openai"].blocked_until = 0
    await voice._run_command("retry", "wake")
    assert provider.calls == 3
    assert voice.speak_text.await_args.args == ("Recovered.",)
    assert router.status()["providers"]["openai"]["failures"] == 0
    assert router.status()["providers"]["openai"]["last_error"] == ""
    assert len(router.status()["recent_failures"]) == 2
    assert voice.failure_status()[0]["kind"] == "cooldown"
    assert (await voice.store.snapshot_live())["radio_latency"]["error_kind"] == ""
    await voice.shutdown()


@pytest.mark.asyncio
async def test_successful_fallback_keeps_sanitized_failure_evidence():
    class ProviderAPIError(RuntimeError):
        status_code = 503
        code = "service_unavailable"

    primary = ScriptedProvider([ProviderAPIError("private request and secret-value")])
    fallback = ScriptedProvider(["Fallback answered."], "deepseek")
    router = router_with(primary, llm_fallback_provider="deepseek")
    router.providers["deepseek"] = fallback
    router.circuits["deepseek"] = type(router.circuits["openai"])()
    result = await request(router)
    assert result.provider == "deepseek"
    evidence = router.status()["recent_failures"]
    assert evidence[0]["status_code"] == 503
    assert evidence[0]["error_code"] == "service_unavailable"
    assert evidence[0]["kind"] == "service"
    assert "private" not in json.dumps(evidence)
    assert "secret-value" not in json.dumps(evidence)
    evidence[0]["kind"] = "changed externally"
    assert router.status()["recent_failures"][0]["kind"] == "service"


@pytest.mark.asyncio
async def test_provider_failure_history_is_bounded_and_does_not_store_error_prose():
    provider = ScriptedProvider([ValueError("secret request") for _ in range(10)])
    router = router_with(provider)
    for _ in range(10):
        router.circuits["openai"].blocked_until = 0
        with pytest.raises(ProviderRequestError):
            await request(router)
    evidence = router.status()["recent_failures"]
    assert len(evidence) == 8
    assert "secret request" not in json.dumps(evidence)


@pytest.mark.asyncio
async def test_external_cancellation_never_opens_provider_circuit_or_logs_failure():
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    class PendingProvider:
        available = True

        async def generate(self, **_):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    router = router_with(PendingProvider())
    task = asyncio.create_task(request(router))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    assert router.status()["recent_failures"] == []
    assert router.circuits["openai"].failures == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["session", "cancel"])
async def test_cancelled_voice_request_does_not_speak_or_record_failure(
    monkeypatch, tmp_path, boundary
):
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def ask(_):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    voice = voice_with(SimpleNamespace(classify_request=lambda _: "normal", ask=ask), monkeypatch, tmp_path)
    await voice.store.mark_packet(2026, 26, 100)
    task = asyncio.create_task(voice._run_command("private call", "wake"))
    await asyncio.wait_for(entered.wait(), 1)
    if boundary == "session":
        await voice.store.mark_packet(2026, 26, 123)
        await asyncio.wait_for(task, 1)
    else:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert cancelled.is_set()
    voice.speak_text.assert_not_awaited()
    assert voice.failure_status() == []
    await voice.shutdown()


@pytest.mark.asyncio
async def test_session_changed_exception_is_not_spoken_as_an_error(monkeypatch, tmp_path):
    brain = SimpleNamespace(
        classify_request=lambda _: "normal",
        ask=AsyncMock(side_effect=SessionChangedError("replaced session")),
    )
    voice = voice_with(brain, monkeypatch, tmp_path)
    await voice._run_command("question", "wake")
    voice.speak_text.assert_not_awaited()
    assert voice.failure_status() == []
    await voice.shutdown()


class ServiceError(RuntimeError):
    def __init__(self, status, code=""):
        super().__init__("private details")
        self.status_code = status
        self.code = code


@pytest.mark.asyncio
@pytest.mark.parametrize("error, kind, expected", [
    (ProviderDeadlineError("private details"), "deadline", "too long"),
    (httpx.ReadTimeout("private details"), "timeout", "timed out"),
    (ServiceError(401), "authentication", "denied access"),
    (ServiceError(429, "insufficient_quota"), "quota", "usage or billing limit"),
    (ProviderConfigurationError("private details"), "configuration", "needs a key"),
    (ServiceError(429), "rate_limit", "busy"),
    (ServiceError(404, "model_not_found"), "request", "rejected that request"),
])
async def test_known_provider_errors_have_actionable_spoken_feedback(
    monkeypatch, tmp_path, error, kind, expected
):
    router = router_with(ScriptedProvider([error]))

    async def ask(_):
        return (await request(router)).text

    brain = SimpleNamespace(classify_request=lambda _: "normal", ask=ask)
    voice = voice_with(brain, monkeypatch, tmp_path)
    await voice._run_command("question", "ptt")
    assert expected in voice.speak_text.await_args.args[0]
    assert "private details" not in voice.speak_text.await_args.args[0]
    assert voice.failure_status()[0]["kind"] == kind
    assert router.status()["recent_failures"][0]["kind"] == kind
    await voice.shutdown()


@pytest.mark.asyncio
async def test_unexpected_radio_failures_remain_visible_after_success_without_private_text(
    monkeypatch, tmp_path
):
    brain = SimpleNamespace(
        classify_request=lambda _: "normal",
        ask=AsyncMock(side_effect=[ValueError("private driver transcript")] * 9 + ["Copy."]),
    )
    voice = voice_with(brain, monkeypatch, tmp_path)
    for _ in range(9):
        await voice._run_command("private driver transcript", "wake")
    assert voice.speak_text.await_args.args == (BRAIN_FALLBACK_LINE,)
    await voice._run_command("next question", "wake")
    assert voice.speak_text.await_args.args == ("Copy.",)
    evidence = voice.failure_status()
    assert len(evidence) == 8
    assert evidence[-1]["error_type"] == "ValueError"
    assert evidence[-1]["where"]
    assert "private driver transcript" not in json.dumps(evidence)
    evidence[-1]["where"][0]["line"] = -1
    assert voice.failure_status()[-1]["where"][0]["line"] != -1
    await voice.shutdown()
