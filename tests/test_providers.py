from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from pitwall.config import Settings
from pitwall.providers import (
    DeepSeekChatProvider,
    ProviderResult,
    ProviderRouter,
    ProviderTruncationError,
    _final_answer_only,
    _is_health_failure,
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
        "llm_provider": "openai",
        "llm_fallback_provider": "none",
        "llm_compare_enabled": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.asyncio
async def test_deepseek_non_thinking_uses_pro_and_chat_tools() -> None:
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
    assert result.model == "deepseek-v4-pro"
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
async def test_provider_outside_the_configured_chain_is_never_consulted() -> None:
    deepseek = _FakeProvider("deepseek", RuntimeError("temporary outage"))
    openai = _FakeProvider("openai", "Fallback answer")
    router = ProviderRouter(
        _settings(),  # primary openai, fallback none
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
    assert deepseek.calls == 0
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
        _settings(llm_provider="deepseek", llm_fallback_provider="openai"),
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


def test_primary_none_is_rejected_but_fallback_none_is_allowed() -> None:
    config = Settings(
        _env_file=None,
        llm_provider="none",
        llm_fallback_provider="none",
    )
    assert config.llm_provider == "openai"
    assert config.llm_fallback_provider == "none"


def test_compare_is_opt_in_by_default() -> None:
    config = Settings(_env_file=None)
    assert config.llm_compare_enabled is False


@pytest.mark.asyncio
async def test_deepseek_thinking_omits_tool_choice() -> None:
    message = SimpleNamespace(content="READY", reasoning_content="checked", tool_calls=None)
    client = _DeepSeekClient([_choice(message)])
    provider = DeepSeekChatProvider(_settings(), client=client)  # type: ignore[arg-type]

    await provider.generate(
        prompt="deep check",
        instructions="brief",
        route="deep",
        effort="high",
        tools=[],
        execute_tool=lambda name, args: None,  # type: ignore[arg-type]
        max_rounds=2,
    )

    request = client.completions.requests[0]
    assert "tool_choice" not in request
    assert "tools" not in request
    assert request["extra_body"]["thinking"]["type"] == "enabled"


@pytest.mark.asyncio
async def test_deepseek_length_retries_with_larger_budget() -> None:
    first = SimpleNamespace(content="", reasoning_content="long", tool_calls=None)
    second = SimpleNamespace(content="Recovered answer", reasoning_content="done", tool_calls=None)
    client = _DeepSeekClient([_choice(first, "length"), _choice(second, "stop")])
    config = _settings(
        deepseek_deep_max_tokens=1234,
        deepseek_deep_retry_max_tokens=5678,
    )
    provider = DeepSeekChatProvider(config, client=client)  # type: ignore[arg-type]

    result = await provider.generate(
        prompt="deep check",
        instructions="brief",
        route="deep",
        effort="high",
        tools=[],
        execute_tool=lambda name, args: None,  # type: ignore[arg-type]
        max_rounds=2,
    )

    assert result.text == "Recovered answer"
    assert [request["max_tokens"] for request in client.completions.requests] == [1234, 5678]


class _AlwaysTruncatedProvider:
    name = "deepseek"
    available = True

    async def generate(self, **kwargs: Any) -> ProviderResult:
        del kwargs
        raise ProviderTruncationError("budget exhausted")


@pytest.mark.asyncio
async def test_truncation_falls_back_without_opening_circuit() -> None:
    openai = _FakeProvider("openai", "Fallback answer")
    router = ProviderRouter(
        _settings(llm_provider="deepseek", llm_fallback_provider="openai"),
        providers={
            "deepseek": _AlwaysTruncatedProvider(),
            "openai": openai,
        },  # type: ignore[arg-type]
    )

    result = await router.generate(
        prompt="test",
        instructions="test",
        route="deep",
        effort="high",
        tools=[],
        execute_tool=lambda name, args: None,  # type: ignore[arg-type]
        max_rounds=2,
    )

    assert result.provider == "openai"
    assert router.circuits["deepseek"].failures == 0


class _SlowProvider:
    name = "deepseek"
    available = True

    async def generate(self, **kwargs: Any) -> ProviderResult:
        del kwargs
        await asyncio.sleep(0.2)
        return ProviderResult("late", "deepseek", "slow", 200.0)


@pytest.mark.asyncio
async def test_unconfigured_fallback_never_slows_the_primary() -> None:
    openai = _FakeProvider("openai", "fast fallback")
    router = ProviderRouter(
        _settings(llm_normal_deadline_s=0.02),
        providers={"deepseek": _SlowProvider(), "openai": openai},  # type: ignore[arg-type]
    )
    started = asyncio.get_running_loop().time()
    result = await router.generate(
        prompt="test",
        instructions="test",
        route="normal",
        effort="low",
        tools=[],
        execute_tool=lambda name, args: None,  # type: ignore[arg-type]
        max_rounds=2,
    )
    elapsed = asyncio.get_running_loop().time() - started

    assert result.provider == "openai"
    assert elapsed < 0.15
    assert router.circuits["deepseek"].failures == 0


def test_status_resolves_auto_to_first_configured_provider() -> None:
    deepseek = _FakeProvider("deepseek", "ok")
    openai = _FakeProvider("openai", "ok")
    router = ProviderRouter(
        _settings(llm_provider="auto"),
        providers={"deepseek": deepseek, "openai": openai},  # type: ignore[arg-type]
    )
    status = router.status()
    assert status["configured_provider"] == "auto"
    assert status["resolved_provider"] == "openai"
    assert status["selected"] == "openai"


def test_auto_skips_providers_without_credentials() -> None:
    openai = _FakeProvider("openai", "ok")
    openai.available = False
    deepseek = _FakeProvider("deepseek", "ok")
    router = ProviderRouter(
        _settings(llm_provider="auto"),
        providers={"openai": openai, "deepseek": deepseek},  # type: ignore[arg-type]
    )
    assert router.status()["resolved_provider"] == "deepseek"


@pytest.mark.asyncio
async def test_configured_non_openai_primary_takes_the_call() -> None:
    deepseek = _FakeProvider("deepseek", "DeepSeek answer")
    openai = _FakeProvider("openai", "OpenAI answer")
    router = ProviderRouter(
        _settings(llm_provider="deepseek", llm_fallback_provider="openai"),
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
    assert result.provider == "deepseek"
    assert openai.calls == 0


@pytest.mark.asyncio
async def test_unknown_explicit_provider_falls_back_to_openai() -> None:
    openai = _FakeProvider("openai", "ok")
    router = ProviderRouter(
        _settings(),
        providers={"openai": openai},  # type: ignore[arg-type]
    )
    result = await router.generate(
        prompt="test",
        instructions="test",
        route="normal",
        effort="low",
        tools=[],
        execute_tool=lambda name, args: None,  # type: ignore[arg-type]
        max_rounds=2,
        provider="not-a-provider",
    )
    assert result.provider == "openai"


@pytest.mark.asyncio
async def test_compare_cooldown_is_enforced() -> None:
    router = ProviderRouter(
        _settings(llm_compare_enabled=True, llm_compare_cooldown_s=60),
        providers={
            "deepseek": _FakeProvider("deepseek", "one"),
            "openai": _FakeProvider("openai", "two"),
        },  # type: ignore[arg-type]
    )
    kwargs = dict(
        prompt="test",
        instructions="test",
        route="deep",
        effort="high",
        tools=[],
        execute_tool=lambda name, args: None,  # type: ignore[arg-type]
        max_rounds=2,
    )
    await router.compare(**kwargs)
    with pytest.raises(Exception, match="cooling down"):
        await router.compare(**kwargs)


class _DiagnosticProvider:
    def __init__(self, name: str) -> None:
        self.name = name
        self.available = True

    async def generate(self, **kwargs: Any) -> ProviderResult:
        tools = kwargs["tools"]
        rounds = 0
        if tools:
            result = await kwargs["execute_tool"](
                "diagnostic_echo", {"token": "pitwall-ready"}
            )
            assert result["ok"] is True
            rounds = 1
        return ProviderResult("READY", self.name, f"{self.name}-model", 1.0, rounds)


@pytest.mark.asyncio
async def test_live_shakedown_checks_basic_and_tool_continuations() -> None:
    router = ProviderRouter(
        _settings(),
        providers={
            "deepseek": _DiagnosticProvider("deepseek"),
            "openai": _DiagnosticProvider("openai"),
        },  # type: ignore[arg-type]
    )
    result = await router.shakedown()
    assert result["ready"] is True
    assert set(result["providers"]) == {"openai"}
    assert result["providers"]["openai"]["stages"]["thinking_tool"]["ok"] is True
    assert router.status()["last_shakedown"] == result


class _InsufficientQuotaError(Exception):
    status_code = 429
    code = "insufficient_quota"


class _TransientRateLimitError(Exception):
    status_code = 429


def test_billing_exhaustion_does_not_open_provider_circuit() -> None:
    assert _is_health_failure(_InsufficientQuotaError()) is False
    assert _is_health_failure(_TransientRateLimitError()) is True


def test_final_answer_only_removes_exposed_deliberation() -> None:
    leaked = """The driver asked for tyre temperature. Let me check the context.

I should give a brief answer. Hold on — the fronts are cooler.

Target 208 to 221 F at the fronts. Build the left-front under braking and protect the rears."""
    assert _final_answer_only(leaked) == (
        "Target 208 to 221 F at the fronts. "
        "Build the left-front under braking and protect the rears."
    )



def test_final_answer_only_removes_strategy_reconciliation_scratchpad() -> None:
    leaked = """Key facts: lap 13, RR at 41 percent. The strategy engine ranks lap 21 soft best.

Wait — I need to reconcile the earlier call.

Hold on — the current deterministic strategy is different. Confirm it.

Box lap 21 for softs."""
    assert _final_answer_only(leaked) == "Box lap 21 for softs."

def test_final_answer_only_keeps_normal_radio_copy() -> None:
    text = "Box lap 14 for mediums. Protect the rear on exit until then."
    assert _final_answer_only(text) == text
