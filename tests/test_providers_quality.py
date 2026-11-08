"""What the driver receives from the provider layer.

These pin properties of every provider's final answer: ordinary radio
phrasing is never mistaken for leaked reasoning, leaked reasoning (including
inline <think> blocks) never reaches TTS, and the evidence a model gathered is
answered from instead of discarded when its tool rounds run out.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

import httpx
import pytest
from openai import AsyncOpenAI

from pitwall import providers
from pitwall.config import Settings
from pitwall.providers import (
    AnthropicMessagesProvider,
    CustomChatProvider,
    DeepSeekChatProvider,
    KimiChatProvider,
    OpenAIResponsesProvider,
    ProviderRequestError,
    ProviderResponseError,
    ProviderResult,
    ProviderRouter,
    _final_answer_only,
    _is_health_failure,
)


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "OPENAI_API_KEY": "openai-test",
        "ANTHROPIC_API_KEY": "anthropic-test",
        "DEEPSEEK_API_KEY": "deepseek-test",
        "KIMI_API_KEY": "kimi-test",
        "custom_llm_base_url": "http://127.0.0.1:8080/v1",
        "custom_llm_fast_model": "local-reasoner",
        "custom_llm_deep_model": "local-reasoner",
        "llm_provider": "openai",
        "llm_fallback_provider": "none",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _schema(name: str) -> dict[str, Any]:
    return {
        "type": "function", "name": name, "description": name,
        "parameters": {
            "type": "object", "properties": {}, "required": [], "additionalProperties": False,
        },
        "strict": True,
    }


async def _unused_executor(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    raise AssertionError(f"no tool should run: {name}")


@contextlib.asynccontextmanager
async def _openai_client(handler: Any):
    async with AsyncOpenAI(
        api_key="test",
        base_url="http://provider.test/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as client:
        yield client


def _chat_payload(message: dict[str, Any], finish_reason: str = "stop") -> dict[str, Any]:
    return {
        "id": "chat", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"index": 0, "finish_reason": finish_reason, "message": message}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _responses_payload(text: str) -> dict[str, Any]:
    return {
        "id": "resp_test", "object": "response", "created_at": 1,
        "model": "gpt-6-luna", "status": "completed",
        "output": [{
            "type": "message", "id": "msg", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }


async def _router_request(router: ProviderRouter) -> ProviderResult:
    return await router.generate(
        prompt="DRIVER: question", instructions="Be brief.", route="normal",
        effort="low", tools=[], execute_tool=_unused_executor, max_rounds=2,
    )


# ---------------------------------------------------------------------------
# Ordinary radio phrasing is kept whole
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("answer", [
    "Hold on to P4, gap behind is 1.2 and stable.",
    "Hold on to P4. Hamilton is 1.2 seconds behind and not closing.",
    "Hold on to P4: Norris is 0.8 behind and closing a tenth a lap.",
    "Hold onto that gap, Norris is 1.4 behind.",
    "Hold on to the mediums until lap 20, then box for hards.",
    "You can hold on to this tyre until lap 22.",
    "Box box. Hold on to the brakes into turn one.",
    "Stay out. Hold on, Safety Car is being deployed, box box box.",
    "Copy. Wait - not this lap, box next lap for mediums.",
    "It's a wait-and-see on the rain. Stay on mediums.",
    "Let me give you the gaps: Norris 1.4 ahead, Leclerc 2.1 behind.",
    "The question is whether the hards last 22 laps; wear model says yes, just.",
    "Copy, the question is answered: box lap 18.",
    "I noted your brake feedback. Front-left is 6 degrees hotter than the right.",
    "Copy, I noted that for lap 12.",
    "Box lap 18 for hards. I note the rears are at 70 percent.",
    "Understood, staying out. I will give you the gap each lap.",
    "Box this lap, confirm it with a click.",
    "Front wing at 40 percent; box this lap for a new nose and mediums, confirm it.",
    "Box this lap for hards. Confirm it with a radio check when you cross the line.",
    "I need to give you a heads-up: Safety Car likely in the next two laps.",
    "Copy. I have to give you the bad news, rears are at 80 percent.",
    "Using the soft tyre, you can go 18 laps from here.",
    "Tyre plan is medium_hard; box lap 18.",
])
def test_radio_phrasing_that_resembles_deliberation_is_spoken_unchanged(answer: str) -> None:
    assert _final_answer_only(answer) == answer


# ---------------------------------------------------------------------------
# Leaked reasoning is removed and the answer after it is kept
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("leaked", "spoken"), [
    (
        (
            "<think>The driver asks if fuel is OK. Fuel delta is +0.4 laps, so yes.</think> "
            "Fuel is fine, plus 0.4 laps at the flag."
        ),
        "Fuel is fine, plus 0.4 laps at the flag.",
    ),
    (
        (
            "<think>\nThe driver asks if fuel is OK.\n\nFuel delta is +0.4 laps.\n</think>\n\n"
            "Fuel is fine, plus 0.4 laps at the flag."
        ),
        "Fuel is fine, plus 0.4 laps at the flag.",
    ),
    (
        "<thinking>Gap tool says 1.4.</thinking>Norris is 1.4 ahead.",
        "Norris is 1.4 ahead.",
    ),
    # A chat template that opens the block in the prompt returns only its end.
    (
        "The driver asks about fuel. Delta is +0.4 laps.\n</think>\n\nFuel is fine.",
        "Fuel is fine.",
    ),
    (
        "The user wants the tyre temps. I'll call the tyre tool. Fronts are 92, rears 88.",
        "Fronts are 92, rears 88.",
    ),
    (
        (
            "Okay, so the driver wants to know the gap ahead. Looking at get_gaps, Norris is "
            "1.4 ahead. Gap to Norris is 1.4 seconds."
        ),
        "Norris is 1.4 ahead. Gap to Norris is 1.4 seconds.",
    ),
    (
        "First, I need to look at the strategy result. Plan A is one stop. Box lap 18 for hards.",
        "Plan A is one stop. Box lap 18 for hards.",
    ),
    (
        "Based on the tool output, you are losing two tenths a lap to Norris.",
        "You are losing two tenths a lap to Norris.",
    ),
    ("Norris is 1.4 ahead per get_gaps.", "Norris is 1.4 ahead."),
    ("Norris is 1.4 ahead (from get_gaps). Box this lap.", "Norris is 1.4 ahead. Box this lap."),
    (
        (
            "The driver's question is about the undercut. I should answer briefly. "
            "Undercut works if you box this lap."
        ),
        "Undercut works if you box this lap.",
    ),
])
def test_leaked_reasoning_is_removed_and_the_answer_after_it_kept(
    leaked: str, spoken: str
) -> None:
    assert _final_answer_only(leaked) == spoken


def test_unterminated_think_block_is_never_spoken() -> None:
    assert _final_answer_only("<think>The driver asks about fuel. Fuel is fine.") == ""
    assert _final_answer_only("Box this lap. <think>Should I mention the rears") == (
        "Box this lap."
    )


@pytest.mark.asyncio
async def test_custom_reasoning_model_think_block_is_not_spoken() -> None:
    content = (
        "<think>The driver asks if fuel is OK. Fuel delta is +0.4 laps, so yes.</think>\n\n"
        "Fuel is fine, plus 0.4 laps at the flag."
    )

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_payload({"role": "assistant", "content": content}))

    async with _openai_client(respond) as client:
        provider = CustomChatProvider(_settings(), client=client)
        result = await provider.generate(
            prompt="DRIVER: fuel ok?", instructions="Be brief.", route="normal",
            effort="low", tools=[], execute_tool=_unused_executor, max_rounds=2,
        )
    assert result.text == "Fuel is fine, plus 0.4 laps at the flag."


# ---------------------------------------------------------------------------
# A filter decision is never an outage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_defensive_call_reaches_the_driver_through_the_router() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_responses_payload(
            "Hold on to P4, gap behind is 1.2 and stable."
        ))

    async with _openai_client(respond) as client:
        config = _settings()
        router = ProviderRouter(
            config, providers={"openai": OpenAIResponsesProvider(config, client=client)},
        )
        result = await _router_request(router)
    assert result.text == "Hold on to P4, gap behind is 1.2 and stable."
    assert router.circuits["openai"].failures == 0


@pytest.mark.asyncio
async def test_scratchpad_only_answers_do_not_put_a_healthy_provider_in_cooldown() -> None:
    answers = iter([
        "The driver asked about the gap. Let me check the context.",
        "Key facts: lap 13. I should give a brief answer.",
        "Box this lap for mediums.",
    ])
    sent: list[int] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(1)
        return httpx.Response(200, json=_responses_payload(next(answers)))

    async with _openai_client(respond) as client:
        config = _settings()
        router = ProviderRouter(
            config, providers={"openai": OpenAIResponsesProvider(config, client=client)},
        )
        for _ in range(2):
            with pytest.raises(ProviderRequestError) as caught:
                await _router_request(router)
            # Nothing speakable came back, which the driver hears as such.
            assert caught.value.kind == "response"
            assert router.status()["recent_failures"][-1]["error_type"] == "ProviderContentError"
        result = await _router_request(router)
    assert result.text == "Box this lap for mediums."
    assert len(sent) == 3
    assert router.circuits["openai"].failures == 0


@pytest.mark.asyncio
async def test_a_provider_that_sends_nothing_still_counts_as_unhealthy() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_responses_payload(""))

    async with _openai_client(respond) as client:
        config = _settings()
        router = ProviderRouter(
            config, providers={"openai": OpenAIResponsesProvider(config, client=client)},
        )
        with pytest.raises(ProviderRequestError):
            await _router_request(router)
    assert router.circuits["openai"].failures == 1


# ---------------------------------------------------------------------------
# The last tool round answers from gathered evidence on every provider
# ---------------------------------------------------------------------------

EVIDENCE = {
    "get_session_overview": {"track": "Madrid", "lap": 12},
    "get_cars_ahead_progress": {"driver": "Norris", "gap_s": 1.4},
    "get_weather": {"weather": "dry"},
}
DIRECTIVE = "Give the final radio answer now using only the evidence already supplied."


def _chat_tool_call(name: str, sequence: int) -> dict[str, Any]:
    return _chat_payload({
        "role": "assistant", "content": None,
        "tool_calls": [{
            "id": f"call_{sequence}", "type": "function",
            "function": {"name": name, "arguments": "{}"},
        }],
    }, "tool_calls")


def _anthropic_payload(content: list[dict[str, Any]], stop: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg", "type": "message", "role": "assistant", "content": content,
        "stop_reason": stop, "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def _forced(kind: str, body: dict[str, Any]) -> bool:
    if kind == "anthropic":
        return body.get("tool_choice") == {"type": "none"}
    return body.get("tool_choice") == "none" or "tools" not in body


def _evidence_in(kind: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    if kind == "anthropic":
        return [
            json.loads(block["content"])
            for message in body["messages"] if isinstance(message["content"], list)
            for block in message["content"] if block.get("type") == "tool_result"
        ]
    return [json.loads(m["content"]) for m in body["messages"] if m["role"] == "tool"]


def _respond_with(kind: str, sequence: int, name: str | None, text: str) -> httpx.Response:
    if kind == "anthropic":
        if name is None:
            return httpx.Response(200, json=_anthropic_payload([{"type": "text", "text": text}]))
        return httpx.Response(200, json=_anthropic_payload(
            [{"type": "tool_use", "id": f"toolu_{sequence}", "name": name, "input": {}}],
            "tool_use",
        ))
    if name is None:
        return httpx.Response(200, json=_chat_payload({"role": "assistant", "content": text}))
    return httpx.Response(200, json=_chat_tool_call(name, sequence))


@contextlib.asynccontextmanager
async def _provider(kind: str, handler: Any):
    config = _settings()
    if kind == "anthropic":
        yield AnthropicMessagesProvider(config, transport=httpx.MockTransport(handler))
        return
    async with _openai_client(handler) as client:
        provider_type = {
            "deepseek": DeepSeekChatProvider,
            "kimi": KimiChatProvider,
            "custom": CustomChatProvider,
        }[kind]
        yield provider_type(config, client=client)


PROVIDER_ROUTES = [
    ("anthropic", "normal"), ("anthropic", "deep"),
    ("deepseek", "normal"), ("deepseek", "deep"),
    ("kimi", "normal"), ("kimi", "deep"),
    ("custom", "normal"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "route"), PROVIDER_ROUTES)
async def test_final_round_answers_from_gathered_tools_on_every_provider(kind, route):
    requests: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if _forced(kind, body):
            evidence = _evidence_in(kind, body)
            assert evidence == [
                EVIDENCE["get_session_overview"], EVIDENCE["get_cars_ahead_progress"],
            ]
            ahead = evidence[1]
            return _respond_with(
                kind, len(requests), None, f"{ahead['driver']} is {ahead['gap_s']} seconds ahead."
            )
        # A model that would keep gathering for as long as it is allowed to.
        return _respond_with(kind, len(requests), list(EVIDENCE)[len(requests) - 1], "")

    executed: list[str] = []

    async def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        executed.append(name)
        return EVIDENCE[name]

    async with _provider(kind, respond) as provider:
        result = await provider.generate(
            prompt="Who am I chasing?", instructions="Use measured evidence.",
            route=route, effort="high" if route == "deep" else "low",
            tools=[_schema(name) for name in EVIDENCE], execute_tool=execute, max_rounds=3,
        )

    assert result.text == "Norris is 1.4 seconds ahead."
    assert result.tool_rounds == 2
    assert executed == ["get_session_overview", "get_cars_ahead_progress"]
    assert len(requests) == 3
    final = requests[-1]
    if kind == "anthropic":
        assert all("tool_choice" not in body for body in requests[:-1])
        # The directive follows the tool results; the cached system prefix is untouched.
        last_turn = final["messages"][-1]["content"]
        assert [block["type"] for block in last_turn] == ["tool_result", "text"]
        assert DIRECTIVE in last_turn[-1]["text"]
        assert final["system"] == requests[0]["system"]
        assert final["tools"] == requests[0]["tools"]
    else:
        assert DIRECTIVE in final["messages"][0]["content"]
        assert all(DIRECTIVE not in body["messages"][0]["content"] for body in requests[:-1])
        if kind == "deepseek" and route == "deep":
            # Thinking mode rejects tool_choice, so the answer is forced by
            # sending no tools at all.
            assert "tools" not in final and "tool_choice" not in final
            assert all("tool_choice" not in body for body in requests)
        else:
            assert final["tool_choice"] == "none"
            assert final["tools"] == requests[0]["tools"]
            assert [body["tool_choice"] for body in requests[:-1]] == ["auto", "auto"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "route"), PROVIDER_ROUTES)
async def test_late_tool_call_after_final_round_is_never_executed(kind, route):
    requests: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        # A server that ignores tool_choice and asks for a mutating tool.
        name = "get_session_overview" if len(requests) == 1 else "record_lap_observation"
        return _respond_with(kind, len(requests), name, "")

    executed: list[str] = []

    async def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        executed.append(name)
        return {"lap": 3}

    async with _provider(kind, respond) as provider:
        with pytest.raises(ProviderResponseError, match="final answer") as caught:
            await provider.generate(
                prompt="review lap", instructions="Use telemetry.", route=route,
                effort="high" if route == "deep" else "low",
                tools=[_schema("get_session_overview"), _schema("record_lap_observation")],
                execute_tool=execute, max_rounds=2,
            )
    assert executed == ["get_session_overview"]
    assert len(requests) == 2
    # Model behaviour inside a healthy service: never an availability failure.
    assert _is_health_failure(caught.value) is False


@pytest.mark.asyncio
async def test_round_exhaustion_does_not_put_the_provider_in_cooldown() -> None:
    sent: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if not body.get("tools"):
            return httpx.Response(200, json=_anthropic_payload(
                [{"type": "text", "text": "Box this lap for mediums."}]
            ))
        return httpx.Response(200, json=_anthropic_payload(
            [{"type": "tool_use", "id": f"toolu_{len(sent)}", "name": "get_weather", "input": {}}],
            "tool_use",
        ))

    config = _settings(llm_provider="anthropic")
    router = ProviderRouter(config, providers={
        "anthropic": AnthropicMessagesProvider(config, transport=httpx.MockTransport(respond)),
    })

    async def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"weather": "dry"}

    for _ in range(2):
        with pytest.raises(ProviderRequestError):
            await router.generate(
                prompt="Weather?", instructions="Use telemetry.", route="normal", effort="low",
                tools=[_schema("get_weather")], execute_tool=execute, max_rounds=2,
            )
    assert router.circuits["anthropic"].failures == 0
    result = await router.generate(
        prompt="Box?", instructions="Brief.", route="normal", effort="low",
        tools=[], execute_tool=execute, max_rounds=1,
    )
    assert result.text == "Box this lap for mediums."


def test_new_error_types_keep_the_response_kind_for_spoken_feedback() -> None:
    for error_type in (providers.ProviderContentError, providers.ProviderToolRoundsError):
        assert issubclass(error_type, ProviderResponseError)
        assert providers._provider_failure_kind(error_type("x")) == "response"
