from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from pitwall.config import Settings
from pitwall.providers import (
    DeepSeekChatProvider,
    ProviderResult,
    ProviderRouter,
)


class _Completions:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        return self.responses.pop(0)


class _DeepSeekClient:
    def __init__(self, responses: list[Any]) -> None:
        self.completions = _Completions(responses)
        self.chat = SimpleNamespace(completions=self.completions)


def _choice(message: Any, finish_reason: str = "stop") -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4, total_tokens=14),
    )


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "DEEPSEEK_API_KEY": "deepseek-test",
        "OPENAI_API_KEY": "openai-test",
        "llm_provider": "deepseek",
        "llm_fallback_provider": "openai",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.asyncio
async def test_deepseek_non_thinking_uses_flash_and_chat_tools() -> None:
    message = SimpleNamespace(content="Copy. Gap is 1.2 seconds.", tool_calls=None)
    client = _DeepSeekClient([_choice(message)])
    provider = DeepSeekChatProvider(_settings(), client=client)  # type: ignore[arg-type]

    result = await provider.generate(
        prompt="DRIVER: gap ahead",
        instructions="Be brief.",
        route="normal",
        effort="low",
        tools=[
            {
                "type": "function",
                "name": "get_gap",
                "description": "Get a gap.",
                "parameters": {
                    "type": "object",
                    "properties": {"target": {"type": "string"}},
                    "required": ["target"],
                    "additionalProperties": False,
                },
                "strict": True,
            }
        ],
        execute_tool=lambda name, args: None,  # type: ignore[arg-type]
        max_rounds=3,
    )

    assert result.provider == "deepseek"
    assert result.model == "deepseek-v4-flash"
    assert result.text.startswith("Copy")
    request = client.completions.requests[0]
    assert request["extra_body"]["thinking"]["type"] == "disabled"
    assert request["tools"][0]["function"]["name"] == "get_gap"
    assert "strict" not in request["tools"][0]["function"]


@pytest.mark.asyncio
async def test_deepseek_thinking_tool_loop_preserves_reasoning_content() -> None:
    call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="get_gap", arguments='{"target":"ahead"}'),
    )
    first = SimpleNamespace(
        content="",
        reasoning_content="I need live telemetry.",
        tool_calls=[call],
    )
    second = SimpleNamespace(
        content="Box lap 12 for hards.",
        reasoning_content="The strategy result is decisive.",
        tool_calls=None,
    )
    client = _DeepSeekClient([_choice(first, "tool_calls"), _choice(second)])
    provider = DeepSeekChatProvider(_settings(), client=client)  # type: ignore[arg-type]
    executed: list[tuple[str, dict[str, Any]]] = []

    async def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        executed.append((name, arguments))
        return {"available": True, "gap_s": 1.2}

    result = await provider.generate(
        prompt="DRIVER: Should I pit?",
        instructions="Use tools.",
        route="deep",
        effort="high",
        tools=[
            {
                "type": "function",
                "name": "get_gap",
                "description": "Get a gap.",
                "parameters": {
                    "type": "object",
                    "properties": {"target": {"type": "string"}},
                    "required": ["target"],
                    "additionalProperties": False,
                },
                "strict": True,
            }
        ],
        execute_tool=execute,
        max_rounds=3,
    )

    assert result.model == "deepseek-v4-pro"
    assert executed == [("get_gap", {"target": "ahead"})]
    second_request = client.completions.requests[1]
    assistant = second_request["messages"][2]
    assert assistant["reasoning_content"] == "I need live telemetry."
    assert second_request["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_invalid_deepseek_tool_arguments_are_not_executed() -> None:
    call = SimpleNamespace(
        id="call_bad",
        function=SimpleNamespace(name="get_gap", arguments='{"wrong":1}'),
    )
    first = SimpleNamespace(content="", reasoning_content=None, tool_calls=[call])
    second = SimpleNamespace(content="Telemetry request was invalid.", tool_calls=None)
    client = _DeepSeekClient([_choice(first, "tool_calls"), _choice(second)])
    provider = DeepSeekChatProvider(_settings(), client=client)  # type: ignore[arg-type]
    executed = False

    async def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        nonlocal executed
        executed = True
        return {}

    await provider.generate(
        prompt="DRIVER: gap",
        instructions="Use tools.",
        route="normal",
        effort="low",
        tools=[
            {
                "type": "function",
                "name": "get_gap",
                "description": "Get a gap.",
                "parameters": {
                    "type": "object",
                    "properties": {"target": {"type": "string"}},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            }
        ],
        execute_tool=execute,
        max_rounds=2,
    )

    assert not executed
    tool_message = client.completions.requests[1]["messages"][-1]
    assert "failed validation" in tool_message["content"]


class _FakeProvider:
    def __init__(self, name: str, result: str | Exception) -> None:
        self.name = name
        self.result = result
        self.available = True
        self.calls = 0

    async def generate(self, **kwargs: Any) -> ProviderResult:
        del kwargs
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return ProviderResult(
            text=self.result,
            provider=self.name,
            model=f"{self.name}-model",
            latency_ms=1.0,
        )


@pytest.mark.asyncio
async def test_router_falls_back_to_openai_after_deepseek_failure() -> None:
    deepseek = _FakeProvider("deepseek", RuntimeError("temporary outage"))
    openai = _FakeProvider("openai", "Fallback answer")
    router = ProviderRouter(
        _settings(),
        providers={"deepseek": deepseek, "openai": openai},  # type: ignore[arg-type]
    )

    result = await router.generate(
        prompt="test",
        instructions="test",
        route="normal",
        effort="low",
        tools=[],
        execute_tool=lambda name, args: None,  # type: ignore[arg-type]
        max_rounds=2,
    )

    assert result.provider == "openai"
    assert result.text == "Fallback answer"
    assert deepseek.calls == 1
    assert openai.calls == 1

class _ToolUsingProvider:
    def __init__(self, name: str) -> None:
        self.name = name
        self.available = True

    async def generate(self, **kwargs: Any) -> ProviderResult:
        executor = kwargs["execute_tool"]
        first = await executor("get_gap", {"target": "ahead"})
        blocked = await executor("generate_setup", {"profile": "race", "track_id": 10})
        return ProviderResult(
            text=f"{first['gap_s']}|{blocked['error']}",
            provider=self.name,
            model=f"{self.name}-model",
            latency_ms=1.0,
        )


@pytest.mark.asyncio
async def test_compare_shares_tool_evidence_and_blocks_setup_mutation() -> None:
    calls = 0

    async def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"gap_s": 1.4, "name": name, "arguments": arguments}

    router = ProviderRouter(
        _settings(),
        providers={
            "deepseek": _ToolUsingProvider("deepseek"),
            "openai": _ToolUsingProvider("openai"),
        },  # type: ignore[arg-type]
    )
    result = await router.compare(
        prompt="test",
        instructions="test",
        route="deep",
        effort="high",
        tools=[],
        execute_tool=execute,
        max_rounds=2,
    )

    assert calls == 1
    assert result["shared_tool_results"] == 1
    assert "disabled during A/B comparison" in result["results"]["deepseek"]["reply"]

class _ToolThenFailProvider:
    name = "deepseek"
    available = True

    async def generate(self, **kwargs: Any) -> ProviderResult:
        await kwargs["execute_tool"]("get_gap", {"target": "ahead"})
        raise RuntimeError("failed after tool")


class _ToolThenSucceedProvider:
    name = "openai"
    available = True

    async def generate(self, **kwargs: Any) -> ProviderResult:
        result = await kwargs["execute_tool"]("get_gap", {"target": "ahead"})
        return ProviderResult(
            text=f"gap {result['gap_s']}",
            provider=self.name,
            model="openai-model",
            latency_ms=1.0,
        )


@pytest.mark.asyncio
async def test_fallback_reuses_primary_tool_result() -> None:
    calls = 0

    async def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"gap_s": 2.2, "name": name, "arguments": arguments}

    router = ProviderRouter(
        _settings(),
        providers={
            "deepseek": _ToolThenFailProvider(),
            "openai": _ToolThenSucceedProvider(),
        },  # type: ignore[arg-type]
    )
    result = await router.generate(
        prompt="test",
        instructions="test",
        route="normal",
        effort="low",
        tools=[],
        execute_tool=execute,
        max_rounds=2,
    )

    assert result.provider == "openai"
    assert result.text == "gap 2.2"
    assert calls == 1

@pytest.mark.asyncio
async def test_deepseek_sdk_round_trip_serializes_thinking_tool_state() -> None:
    import httpx
    from openai import AsyncOpenAI

    requests: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-1",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "deepseek-v4-pro",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "reasoning_content": "Need the current gap.",
                                "tool_calls": [
                                    {
                                        "id": "call_sdk",
                                        "type": "function",
                                        "function": {
                                            "name": "get_gap",
                                            "arguments": '{"target":"ahead"}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-2",
                "object": "chat.completion",
                "created": 2,
                "model": "deepseek-v4-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Gap is 1.7 seconds.",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 4,
                    "total_tokens": 16,
                },
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AsyncOpenAI(
        api_key="deepseek-test",
        base_url="https://api.deepseek.com",
        http_client=http_client,
    )
    provider = DeepSeekChatProvider(_settings(), client=client)

    async def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert name == "get_gap"
        assert arguments == {"target": "ahead"}
        return {"gap_s": 1.7}

    result = await provider.generate(
        prompt="DRIVER: gap ahead",
        instructions="Be brief.",
        route="deep",
        effort="high",
        tools=[
            {
                "type": "function",
                "name": "get_gap",
                "description": "Get a gap.",
                "parameters": {
                    "type": "object",
                    "properties": {"target": {"type": "string"}},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            }
        ],
        execute_tool=execute,
        max_rounds=3,
    )
    await http_client.aclose()

    assert result.text == "Gap is 1.7 seconds."
    assert result.usage["total_tokens"] == 31
    assert requests[0]["thinking"] == {"type": "enabled"}
    assert requests[1]["messages"][2]["reasoning_content"] == "Need the current gap."
    assert requests[1]["messages"][-1]["tool_call_id"] == "call_sdk"

def test_placeholder_api_keys_are_not_treated_as_configured() -> None:
    config = Settings(
        _env_file=None,
        DEEPSEEK_API_KEY="replace_me",
        OPENAI_API_KEY="your_key_here",
    )
    assert config.deepseek_key is None
    assert config.api_key is None
