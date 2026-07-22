from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from jsonschema import Draft202012Validator
from openai import AsyncOpenAI

from .config import Settings, settings

ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class ProviderError(RuntimeError):
    """Base error for an LLM provider failure."""


class ProviderConfigurationError(ProviderError):
    """Raised when a requested provider has no usable credentials."""


class ProviderResponseError(ProviderError):
    """Raised when a provider returns an unusable response."""


@dataclass(slots=True)
class ProviderResult:
    text: str
    provider: str
    model: str
    latency_ms: float
    tool_rounds: int = 0
    usage: dict[str, int] = field(default_factory=dict)


class EngineerProvider(Protocol):
    name: str

    @property
    def available(self) -> bool: ...

    async def generate(
        self,
        *,
        prompt: str,
        instructions: str,
        route: str,
        effort: str,
        tools: list[dict[str, Any]],
        execute_tool: ToolExecutor,
        max_rounds: int,
    ) -> ProviderResult: ...


def _usage_dict(usage: Any) -> dict[str, int]:
    if usage is None:
        return {}
    result: dict[str, int] = {}
    for source, target in (
        ("input_tokens", "input_tokens"),
        ("output_tokens", "output_tokens"),
        ("total_tokens", "total_tokens"),
        ("prompt_tokens", "input_tokens"),
        ("completion_tokens", "output_tokens"),
    ):
        value = getattr(usage, source, None)
        if value is not None:
            result[target] = int(value)
    return result




def _merge_usage(total: dict[str, int], current: dict[str, int]) -> None:
    for key, value in current.items():
        total[key] = total.get(key, 0) + int(value)


def _parse_and_validate_arguments(
    name: str,
    raw_arguments: str | None,
    schema_by_name: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    if name not in schema_by_name:
        return None, f"Unknown tool requested: {name}"
    try:
        arguments = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError as exc:
        return None, f"Tool arguments were not valid JSON: {exc.msg}"
    if not isinstance(arguments, dict):
        return None, "Tool arguments must be a JSON object."
    parameters = schema_by_name[name].get("parameters", {})
    errors = sorted(
        Draft202012Validator(parameters).iter_errors(arguments),
        key=lambda error: list(error.path),
    )
    if errors:
        message = "; ".join(error.message for error in errors[:4])
        return None, f"Tool arguments failed validation: {message}"
    return arguments, None


class OpenAIResponsesProvider:
    name = "openai"

    def __init__(
        self,
        config: Settings = settings,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.config = config
        self.client = client or (
            AsyncOpenAI(
                api_key=config.api_key,
                timeout=config.openai_timeout_s,
                max_retries=2,
            )
            if config.api_key
            else None
        )

    @property
    def available(self) -> bool:
        return self.client is not None

    @staticmethod
    def _safe_output_item(item: Any) -> dict[str, Any] | None:
        item_type = getattr(item, "type", None)
        if item_type == "function_call":
            return {
                "type": "function_call",
                "call_id": item.call_id,
                "name": item.name,
                "arguments": item.arguments,
            }
        if hasattr(item, "model_dump"):
            return item.model_dump(exclude_none=True, exclude={"id", "status"})
        return None

    async def generate(
        self,
        *,
        prompt: str,
        instructions: str,
        route: str,
        effort: str,
        tools: list[dict[str, Any]],
        execute_tool: ToolExecutor,
        max_rounds: int,
    ) -> ProviderResult:
        del route
        if self.client is None:
            raise ProviderConfigurationError("OpenAI API key is not configured.")

        started = time.perf_counter()
        input_items: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        schema_by_name = {tool["name"]: tool for tool in tools}
        total_usage: dict[str, int] = {}

        for round_index in range(max_rounds):
            if effort in {"high", "xhigh", "max"}:
                token_budget = 2000
            elif effort == "medium":
                token_budget = 1100
            else:
                token_budget = 520
            request: dict[str, Any] = {
                "model": self.config.model,
                "instructions": instructions,
                "input": input_items,
                "tools": tools,
                "max_output_tokens": token_budget,
                "parallel_tool_calls": True,
            }
            if self.config.model.startswith("gpt-5"):
                request["reasoning"] = {"effort": effort}

            response = await self.client.responses.create(**request)
            _merge_usage(total_usage, _usage_dict(getattr(response, "usage", None)))
            calls = [
                item
                for item in response.output
                if getattr(item, "type", None) == "function_call"
            ]
            if not calls:
                text = (response.output_text or "").strip()
                if not text:
                    raise ProviderResponseError("OpenAI returned no final text.")
                return ProviderResult(
                    text=text,
                    provider=self.name,
                    model=self.config.model,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    tool_rounds=round_index,
                    usage=total_usage,
                )

            for output_item in response.output:
                safe = self._safe_output_item(output_item)
                if safe is not None:
                    input_items.append(safe)

            async def run_call(call: Any) -> tuple[Any, dict[str, Any]]:
                arguments, validation_error = _parse_and_validate_arguments(
                    call.name,
                    call.arguments,
                    schema_by_name,
                )
                if validation_error:
                    return call, {"error": validation_error}
                assert arguments is not None
                try:
                    result = await execute_tool(call.name, arguments)
                except Exception as exc:  # tool errors must not crash the provider loop
                    result = {"error": f"Tool execution failed: {type(exc).__name__}: {exc}"}
                return call, result

            results = await asyncio.gather(*(run_call(call) for call in calls))
            for call, result in results:
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(result, ensure_ascii=False),
                    }
                )

        raise ProviderResponseError(
            "OpenAI did not produce a final answer within the tool-round limit."
        )


class DeepSeekChatProvider:
    name = "deepseek"

    def __init__(
        self,
        config: Settings = settings,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.config = config
        base_url = config.deepseek_base_url.rstrip("/")
        if config.deepseek_strict_tools and not base_url.endswith("/beta"):
            base_url = f"{base_url}/beta"
        self.client = client or (
            AsyncOpenAI(
                api_key=config.deepseek_key,
                base_url=base_url,
                timeout=config.deepseek_timeout_s,
                max_retries=2,
            )
            if config.deepseek_key
            else None
        )

    @property
    def available(self) -> bool:
        return self.client is not None

    def _model_for(self, route: str) -> str:
        return (
            self.config.deepseek_deep_model
            if route == "deep"
            else self.config.deepseek_fast_model
        )

    def _chat_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for tool in tools:
            function: dict[str, Any] = {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("parameters", {"type": "object"}),
            }
            if self.config.deepseek_strict_tools:
                function["strict"] = True
            converted.append({"type": "function", "function": function})
        return converted

    @staticmethod
    def _assistant_message(message: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "role": "assistant",
            "content": getattr(message, "content", None),
        }
        reasoning = getattr(message, "reasoning_content", None)
        if reasoning is not None:
            payload["reasoning_content"] = reasoning
        calls = getattr(message, "tool_calls", None)
        if calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in calls
            ]
        return payload

    async def generate(
        self,
        *,
        prompt: str,
        instructions: str,
        route: str,
        effort: str,
        tools: list[dict[str, Any]],
        execute_tool: ToolExecutor,
        max_rounds: int,
    ) -> ProviderResult:
        del effort
        if self.client is None:
            raise ProviderConfigurationError("DeepSeek API key is not configured.")

        started = time.perf_counter()
        model = self._model_for(route)
        thinking_enabled = route == "deep"
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": instructions},
            {"role": "user", "content": prompt},
        ]
        schema_by_name = {tool["name"]: tool for tool in tools}
        chat_tools = self._chat_tools(tools)
        total_usage: dict[str, int] = {}
        rounds = min(max_rounds, self.config.deepseek_max_tool_rounds)

        for round_index in range(rounds):
            request: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "tools": chat_tools,
                "tool_choice": "auto",
                "max_tokens": 2200 if thinking_enabled else 650,
                "extra_body": {
                    "thinking": {
                        "type": "enabled" if thinking_enabled else "disabled"
                    }
                },
            }
            if thinking_enabled:
                request["reasoning_effort"] = self.config.deepseek_thinking_effort

            response = await self.client.chat.completions.create(**request)
            _merge_usage(total_usage, _usage_dict(getattr(response, "usage", None)))
            if not response.choices:
                raise ProviderResponseError("DeepSeek returned no choices.")
            choice = response.choices[0]
            message = choice.message
            calls = list(getattr(message, "tool_calls", None) or [])
            if not calls:
                text = (getattr(message, "content", None) or "").strip()
                if not text:
                    reason = getattr(choice, "finish_reason", "unknown")
                    raise ProviderResponseError(
                        f"DeepSeek returned no final text (finish_reason={reason})."
                    )
                return ProviderResult(
                    text=text,
                    provider=self.name,
                    model=model,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    tool_rounds=round_index,
                    usage=total_usage,
                )

            # DeepSeek thinking-mode tool loops require reasoning_content to be
            # replayed with the assistant tool-call message. This helper preserves it.
            messages.append(self._assistant_message(message))

            async def run_call(call: Any) -> tuple[Any, dict[str, Any]]:
                arguments, validation_error = _parse_and_validate_arguments(
                    call.function.name,
                    call.function.arguments,
                    schema_by_name,
                )
                if validation_error:
                    return call, {"error": validation_error}
                assert arguments is not None
                try:
                    result = await execute_tool(call.function.name, arguments)
                except Exception as exc:
                    result = {"error": f"Tool execution failed: {type(exc).__name__}: {exc}"}
                return call, result

            results = await asyncio.gather(*(run_call(call) for call in calls))
            for call, result in results:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        raise ProviderResponseError(
            "DeepSeek did not produce a final answer within the tool-round limit."
        )


@dataclass(slots=True)
class _CircuitState:
    failures: int = 0
    blocked_until: float = 0.0
    last_error: str = ""


class ProviderRouter:
    """Selects the requested provider and fails over without changing race logic."""

    def __init__(
        self,
        config: Settings = settings,
        providers: dict[str, EngineerProvider] | None = None,
    ) -> None:
        self.config = config
        self.providers: dict[str, EngineerProvider] = providers or {
            "openai": OpenAIResponsesProvider(config),
            "deepseek": DeepSeekChatProvider(config),
        }
        self.circuits = {name: _CircuitState() for name in self.providers}
        self.last_result: ProviderResult | None = None

    def _preferred_order(self, explicit: str | None = None) -> list[str]:
        primary = (explicit or self.config.llm_provider).lower()
        if primary == "auto":
            primary = "deepseek" if self.providers.get("deepseek", None) and self.providers["deepseek"].available else "openai"
        order = [primary]
        fallback = self.config.llm_fallback_provider.lower()
        if fallback not in {"none", "auto", primary}:
            order.append(fallback)
        if fallback == "auto":
            for name in ("openai", "deepseek"):
                if name not in order:
                    order.append(name)
        return [name for name in order if name in self.providers]

    async def generate(
        self,
        *,
        prompt: str,
        instructions: str,
        route: str,
        effort: str,
        tools: list[dict[str, Any]],
        execute_tool: ToolExecutor,
        max_rounds: int,
        provider: str | None = None,
    ) -> ProviderResult:
        errors: list[str] = []
        now = time.monotonic()
        tool_cache: dict[str, dict[str, Any]] = {}
        tool_locks: dict[str, asyncio.Lock] = {}

        async def execute_once(
            tool_name: str,
            arguments: dict[str, Any],
        ) -> dict[str, Any]:
            key = json.dumps(
                [tool_name, arguments],
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            lock = tool_locks.setdefault(key, asyncio.Lock())
            async with lock:
                if key not in tool_cache:
                    tool_cache[key] = await execute_tool(tool_name, arguments)
                return tool_cache[key]

        for name in self._preferred_order(provider):
            implementation = self.providers[name]
            circuit = self.circuits[name]
            if not implementation.available:
                errors.append(f"{name}: credentials not configured")
                continue
            if circuit.blocked_until > now:
                errors.append(f"{name}: temporarily cooling down after an API failure")
                continue
            try:
                result = await implementation.generate(
                    prompt=prompt,
                    instructions=instructions,
                    route=route,
                    effort=effort,
                    tools=tools,
                    execute_tool=execute_once,
                    max_rounds=max_rounds,
                )
                circuit.failures = 0
                circuit.blocked_until = 0.0
                circuit.last_error = ""
                self.last_result = result
                return result
            except Exception as exc:
                circuit.failures += 1
                circuit.last_error = f"{type(exc).__name__}: {exc}"
                # One transient failure immediately allows fallback, while repeated
                # failures open a short circuit to avoid adding race-radio latency.
                if circuit.failures >= 2:
                    circuit.blocked_until = (
                        time.monotonic() + self.config.llm_failure_cooldown_s
                    )
                errors.append(f"{name}: {circuit.last_error}")

        raise ProviderError(
            "No LLM provider completed the request. " + " | ".join(errors)
        )

    async def compare(
        self,
        *,
        prompt: str,
        instructions: str,
        route: str,
        effort: str,
        tools: list[dict[str, Any]],
        execute_tool: ToolExecutor,
        max_rounds: int,
    ) -> dict[str, Any]:
        # Both providers share one memoized tool executor. Matching tool calls see
        # byte-for-byte identical evidence, and potentially stateful setup tools
        # are blocked so an A/B diagnostic cannot alter the live race setup state.
        cache: dict[str, dict[str, Any]] = {}
        locks: dict[str, asyncio.Lock] = {}
        blocked = {"generate_setup", "get_front_wing_adjustment"}

        async def execute_frozen(
            tool_name: str,
            arguments: dict[str, Any],
        ) -> dict[str, Any]:
            if tool_name in blocked:
                return {
                    "error": (
                        f"{tool_name} is disabled during A/B comparison because "
                        "it can persist or alter setup recommendations."
                    )
                }
            key = json.dumps(
                [tool_name, arguments],
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            lock = locks.setdefault(key, asyncio.Lock())
            async with lock:
                if key not in cache:
                    cache[key] = await execute_tool(tool_name, arguments)
                return cache[key]

        async def run(name: str) -> tuple[str, dict[str, Any]]:
            provider = self.providers[name]
            if not provider.available:
                return name, {"available": False, "error": "credentials not configured"}
            try:
                result = await provider.generate(
                    prompt=prompt,
                    instructions=instructions,
                    route=route,
                    effort=effort,
                    tools=tools,
                    execute_tool=execute_frozen,
                    max_rounds=max_rounds,
                )
                return name, {
                    "available": True,
                    "reply": result.text,
                    "model": result.model,
                    "latency_ms": round(result.latency_ms, 1),
                    "tool_rounds": result.tool_rounds,
                    "usage": result.usage,
                }
            except Exception as exc:
                return name, {
                    "available": True,
                    "error": f"{type(exc).__name__}: {exc}",
                }

        names = [name for name in ("openai", "deepseek") if name in self.providers]
        results = await asyncio.gather(*(run(name) for name in names))
        return {"results": dict(results), "shared_tool_results": len(cache)}

    def status(self) -> dict[str, Any]:
        return {
            "selected": self.config.llm_provider,
            "fallback": self.config.llm_fallback_provider,
            "active_provider": self.last_result.provider if self.last_result else None,
            "active_model": self.last_result.model if self.last_result else None,
            "providers": {
                name: {
                    "configured": provider.available,
                    "failures": self.circuits[name].failures,
                    "cooldown_remaining_s": round(
                        max(0.0, self.circuits[name].blocked_until - time.monotonic()), 1
                    ),
                    "last_error": self.circuits[name].last_error,
                }
                for name, provider in self.providers.items()
            },
        }
