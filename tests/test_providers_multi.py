"""The 4.7 multi-provider engine: Anthropic, Kimi, and custom endpoints.

Every provider narrates the same deterministic tool evidence; these tests pin
the wire contracts (tool schema conversion, thinking replay, truncation
retries) against mock transports that speak each API's real response shapes.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from pitwall.config import Settings
from pitwall.providers import (
    AnthropicMessagesProvider,
    CustomChatProvider,
    KimiChatProvider,
    ProviderConfigurationError,
    ProviderRouter,
    ProviderTruncationError,
    _is_health_failure,
)

GAP_TOOL = {
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


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "OPENAI_API_KEY": "openai-test",
        "ANTHROPIC_API_KEY": "anthropic-test-key",
        "KIMI_API_KEY": "kimi-test-key",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


# ---------------------------------------------------------------------------
# Anthropic Messages API
# ---------------------------------------------------------------------------


def _anthropic_message(
    content: list[dict[str, Any]],
    stop_reason: str = "end_turn",
) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": content,
        "stop_reason": stop_reason,
        "usage": {
            "input_tokens": 20,
            "output_tokens": 6,
            "cache_read_input_tokens": 12,
        },
    }


@pytest.mark.asyncio
async def test_anthropic_tool_loop_replays_thinking_and_converts_schemas() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "anthropic-test-key"
        assert request.headers["anthropic-version"]
        body = json.loads(request.content.decode("utf-8"))
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json=_anthropic_message(
                    [
                        {"type": "thinking", "thinking": "Need the gap.", "signature": "sig"},
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "get_gap",
                            "input": {"target": "ahead"},
                        },
                    ],
                    stop_reason="tool_use",
                ),
            )
        return httpx.Response(
            200,
            json=_anthropic_message([{"type": "text", "text": "Gap is 1.4 seconds."}]),
        )

    provider = AnthropicMessagesProvider(
        _settings(), transport=httpx.MockTransport(handler)
    )
    executed: list[tuple[str, dict[str, Any]]] = []

    async def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        executed.append((name, arguments))
        return {"gap_s": 1.4}

    result = await provider.generate(
        prompt="DRIVER: gap ahead",
        instructions="Be brief.",
        route="deep",
        effort="high",
        tools=[GAP_TOOL],
        execute_tool=execute,
        max_rounds=3,
    )

    assert result.provider == "anthropic"
    assert result.model == _settings().anthropic_model
    assert result.text == "Gap is 1.4 seconds."
    assert result.usage["cached_input_tokens"] == 24  # both rounds
    assert executed == [("get_gap", {"target": "ahead"})]

    first = requests[0]
    # Responses-format tools are converted to input_schema form.
    assert first["tools"][0]["input_schema"]["required"] == ["target"]
    assert "parameters" not in first["tools"][0]
    # The deep route enables extended thinking inside the max_tokens budget.
    assert first["thinking"]["type"] == "enabled"
    assert first["max_tokens"] > first["thinking"]["budget_tokens"]
    # The persona is marked as a cacheable prefix.
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}

    second = requests[1]
    # The assistant turn is replayed verbatim: thinking block included.
    assistant = second["messages"][1]
    assert assistant["role"] == "assistant"
    assert assistant["content"][0]["type"] == "thinking"
    tool_result = second["messages"][2]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == "toolu_1"
    assert json.loads(tool_result["content"]) == {"gap_s": 1.4}


@pytest.mark.asyncio
async def test_anthropic_fast_route_uses_fast_model_without_thinking() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json=_anthropic_message([{"type": "text", "text": "Copy. Box this lap."}]),
        )

    config = _settings()
    provider = AnthropicMessagesProvider(config, transport=httpx.MockTransport(handler))
    result = await provider.generate(
        prompt="DRIVER: box?",
        instructions="Be brief.",
        route="normal",
        effort="low",
        tools=[],
        execute_tool=lambda name, args: None,  # type: ignore[arg-type]
        max_rounds=2,
    )
    assert result.model == config.anthropic_fast_model
    assert "thinking" not in requests[0]
    assert "tools" not in requests[0]


@pytest.mark.asyncio
async def test_anthropic_truncation_retries_larger_then_raises() -> None:
    budgets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        budgets.append(body["max_tokens"])
        return httpx.Response(
            200,
            json=_anthropic_message(
                [{"type": "text", "text": "truncated"}], stop_reason="max_tokens"
            ),
        )

    provider = AnthropicMessagesProvider(
        _settings(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ProviderTruncationError):
        await provider.generate(
            prompt="deep question",
            instructions="Be brief.",
            route="deep",
            effort="high",
            tools=[],
            execute_tool=lambda name, args: None,  # type: ignore[arg-type]
            max_rounds=2,
        )
    assert len(budgets) == 2
    assert budgets[1] > budgets[0]


@pytest.mark.asyncio
async def test_anthropic_invalid_tool_arguments_are_not_executed() -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content.decode("utf-8")))
        if len(requests) == 1:
            return httpx.Response(
                200,
                json=_anthropic_message(
                    [
                        {
                            "type": "tool_use",
                            "id": "toolu_bad",
                            "name": "get_gap",
                            "input": {"wrong": 1},
                        }
                    ],
                    stop_reason="tool_use",
                ),
            )
        return httpx.Response(
            200,
            json=_anthropic_message([{"type": "text", "text": "Request was invalid."}]),
        )

    provider = AnthropicMessagesProvider(
        _settings(), transport=httpx.MockTransport(handler)
    )
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
        tools=[GAP_TOOL],
        execute_tool=execute,
        max_rounds=2,
    )
    assert not executed
    tool_result = requests[1]["messages"][2]["content"][0]
    assert "failed validation" in tool_result["content"]


@pytest.mark.asyncio
async def test_anthropic_api_errors_classify_for_the_circuit_breaker() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "type": "error",
                "error": {"type": "authentication_error", "message": "invalid x-api-key"},
            },
        )

    provider = AnthropicMessagesProvider(
        _settings(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(Exception) as excinfo:
        await provider.generate(
            prompt="check",
            instructions="Be brief.",
            route="normal",
            effort="low",
            tools=[],
            execute_tool=lambda name, args: None,  # type: ignore[arg-type]
            max_rounds=1,
        )
    # A rejected key is a configuration problem, not provider ill-health: it
    # must stay visible instead of tripping the cooldown circuit.
    assert _is_health_failure(excinfo.value) is False
    assert "invalid x-api-key" in str(excinfo.value)


@pytest.mark.asyncio
async def test_anthropic_without_key_is_unavailable() -> None:
    provider = AnthropicMessagesProvider(_settings(ANTHROPIC_API_KEY=None))
    assert provider.available is False
    with pytest.raises(ProviderConfigurationError):
        await provider.generate(
            prompt="check",
            instructions="Be brief.",
            route="normal",
            effort="low",
            tools=[],
            execute_tool=lambda name, args: None,  # type: ignore[arg-type]
            max_rounds=1,
        )


# ---------------------------------------------------------------------------
# Kimi (Moonshot) and custom OpenAI-compatible endpoints
# ---------------------------------------------------------------------------


class _Completions:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        return self.responses.pop(0)


class _ChatClient:
    def __init__(self, responses: list[Any]) -> None:
        self.completions = _Completions(responses)
        self.chat = SimpleNamespace(completions=self.completions)


def _choice(message: Any, finish_reason: str = "stop") -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=SimpleNamespace(prompt_tokens=9, completion_tokens=3, total_tokens=12),
    )


@pytest.mark.asyncio
async def test_kimi_tool_loop_and_reasoning_replay() -> None:
    call = SimpleNamespace(
        id="call_kimi",
        function=SimpleNamespace(name="get_gap", arguments='{"target":"ahead"}'),
    )
    first = SimpleNamespace(
        content="", reasoning_content="Check the live gap.", tool_calls=[call]
    )
    second = SimpleNamespace(content="Gap is 0.9 seconds.", tool_calls=None)
    client = _ChatClient([_choice(first, "tool_calls"), _choice(second)])
    config = _settings()
    provider = KimiChatProvider(config, client=client)  # type: ignore[arg-type]

    async def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert name == "get_gap"
        return {"gap_s": 0.9}

    result = await provider.generate(
        prompt="DRIVER: gap ahead",
        instructions="Be brief.",
        route="deep",
        effort="high",
        tools=[GAP_TOOL],
        execute_tool=execute,
        max_rounds=3,
    )

    assert result.provider == "kimi"
    assert result.model == config.kimi_deep_model
    assert result.text == "Gap is 0.9 seconds."
    second_request = client.completions.requests[1]
    # Thinking models require their reasoning replayed with the tool call.
    assert second_request["messages"][2]["reasoning_content"] == "Check the live gap."
    assert second_request["messages"][-1]["tool_call_id"] == "call_kimi"
    assert second_request["tools"][0]["function"]["name"] == "get_gap"
    assert "strict" not in second_request["tools"][0]["function"]


@pytest.mark.asyncio
async def test_kimi_length_retries_with_larger_budget() -> None:
    first = SimpleNamespace(content="", tool_calls=None)
    second = SimpleNamespace(content="Recovered.", tool_calls=None)
    client = _ChatClient([_choice(first, "length"), _choice(second)])
    provider = KimiChatProvider(_settings(), client=client)  # type: ignore[arg-type]

    result = await provider.generate(
        prompt="deep",
        instructions="brief",
        route="normal",
        effort="low",
        tools=[],
        execute_tool=lambda name, args: None,  # type: ignore[arg-type]
        max_rounds=2,
    )
    assert result.text == "Recovered."
    budgets = [request["max_tokens"] for request in client.completions.requests]
    assert budgets[1] > budgets[0]


def test_kimi_accepts_the_legacy_moonshot_env_name() -> None:
    config = Settings(_env_file=None, MOONSHOT_API_KEY="moonshot-key-value")
    assert config.kimi_key == "moonshot-key-value"


def test_custom_provider_requires_base_url_and_models() -> None:
    unconfigured = CustomChatProvider(_settings())
    assert unconfigured.available is False

    configured = CustomChatProvider(
        _settings(
            custom_llm_base_url="http://127.0.0.1:11434/v1",
            custom_llm_fast_model="llama3.3",
            custom_llm_deep_model="llama3.3",
        )
    )
    # No key needed: local OpenAI-compatible servers usually run without one.
    assert configured.available is True
    assert configured.model_for("deep") == "llama3.3"


@pytest.mark.asyncio
async def test_custom_provider_answers_over_chat_completions() -> None:
    message = SimpleNamespace(content="Copy. Push now.", tool_calls=None)
    client = _ChatClient([_choice(message)])
    config = _settings(
        custom_llm_base_url="http://127.0.0.1:8080/v1",
        custom_llm_fast_model="local-fast",
        custom_llm_deep_model="local-deep",
    )
    provider = CustomChatProvider(config, client=client)  # type: ignore[arg-type]
    result = await provider.generate(
        prompt="DRIVER: push?",
        instructions="Be brief.",
        route="normal",
        effort="low",
        tools=[],
        execute_tool=lambda name, args: None,  # type: ignore[arg-type]
        max_rounds=2,
    )
    assert result.provider == "custom"
    assert result.model == "local-fast"
    assert result.text == "Copy. Push now."


# ---------------------------------------------------------------------------
# Router integration across the new providers
# ---------------------------------------------------------------------------


def test_router_registers_all_five_engines() -> None:
    router = ProviderRouter(_settings())
    assert set(router.providers) == {"openai", "anthropic", "deepseek", "kimi", "custom"}
    status = router.status()
    assert status["providers"]["anthropic"]["configured"] is True
    assert status["providers"]["kimi"]["configured"] is True
    assert status["providers"]["custom"]["configured"] is False
    assert status["providers"]["anthropic"]["models"]["deep"] == _settings().anthropic_model


def test_router_resolves_anthropic_primary_with_openai_fallback() -> None:
    router = ProviderRouter(
        _settings(llm_provider="anthropic", llm_fallback_provider="openai")
    )
    status = router.status()
    assert status["resolved_provider"] == "anthropic"
    assert status["preferred_order"] == ["anthropic", "openai"]


def test_provider_settings_validation() -> None:
    config = Settings(_env_file=None, llm_provider="anthropic")
    assert config.llm_provider == "anthropic"
    config = Settings(_env_file=None, llm_provider="claude")
    assert config.llm_provider == "openai"  # unknown labels never invent a provider
    config = Settings(_env_file=None, llm_fallback_provider="kimi")
    assert config.llm_fallback_provider == "kimi"
    config = Settings(_env_file=None, llm_fallback_provider="auto")
    assert config.llm_fallback_provider == "none"


def test_configured_llm_providers_reflect_usable_keys() -> None:
    config = _settings()
    configured = config.configured_llm_providers
    # conftest exports test OpenAI/DeepSeek keys into the process environment,
    # so assert membership rather than the exact list.
    assert {"openai", "anthropic", "kimi"} <= set(configured)
    assert "custom" not in configured
    config = _settings(
        custom_llm_base_url="http://127.0.0.1:11434/v1",
        custom_llm_fast_model="m",
        custom_llm_deep_model="m",
    )
    assert "custom" in config.configured_llm_providers


def test_deepseek_key_saved_mid_session_takes_effect_via_rebind() -> None:
    from pydantic import SecretStr

    from pitwall.providers import DeepSeekChatProvider

    config = Settings(_env_file=None, DEEPSEEK_API_KEY=None)
    config.deepseek_api_key = None  # the test env exports a key; clear it
    provider = DeepSeekChatProvider(config)
    provider.rebind_client()
    assert provider.available is False

    config.deepseek_api_key = SecretStr("sk-deepseek-added-mid-session")
    provider.rebind_client()
    assert provider.available is True
