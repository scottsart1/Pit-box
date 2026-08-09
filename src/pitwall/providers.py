from __future__ import annotations

import asyncio
import json
import re
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


class ProviderTruncationError(ProviderResponseError):
    """Raised when a provider exhausts its output budget after one larger retry."""


class ProviderDeadlineError(ProviderError):
    """Raised when a provider exceeds the race-radio route deadline."""


class ProviderRateLimitError(ProviderError):
    """Raised when an opt-in diagnostic is invoked too frequently."""


_DELIBERATION_MARKERS = re.compile(
    r"(?:^|\b)(?:let me(?: check| think| give| reconcile| inspect| review| answer)|i (?:need|should|will) "
    r"(?:check|reconcile|give|answer|use|inspect)|wait\s*(?:[—:-]|$)|hold on|"
    r"the (?:driver|user) asked|the question is|key facts\s*:|critical concern\s*:|"
    r"the current call\s*:|i have the tyre|i have the tire|i should give a brief|"
    r"the strategy engine ranks|the current deterministic strategy|deterministic primary|"
    r"however,? the earlier recent calls|i note|confirm it)",
    re.IGNORECASE,
)


def _contains_deliberation(text: str) -> bool:
    return bool(_DELIBERATION_MARKERS.search(text.strip()))


def _final_answer_only(text: str) -> str:
    """Remove accidentally exposed scratchpad prose from a provider's final content.

    A provider can occasionally imitate deliberation inside its final text. This guard
    keeps only the trailing radio answer and never exposes scratchpad-like prose to
    history or TTS.
    """
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    cleaned = re.sub(r"^```(?:text|markdown)?\s*|\s*```$", "", cleaned, flags=re.I)
    if not cleaned:
        return ""

    paragraphs = [
        " ".join(line.strip() for line in block.splitlines() if line.strip())
        for block in re.split(r"\n\s*\n", cleaned)
        if block.strip()
    ]
    leaked = [
        index
        for index, block in enumerate(paragraphs)
        if _contains_deliberation(block)
    ]
    if leaked:
        trailing = [
            block
            for block in paragraphs[leaked[-1] + 1 :]
            if not _contains_deliberation(block)
        ]
        if trailing:
            cleaned = " ".join(trailing)
        else:
            sentences = re.split(
                r"(?<=[.!?])\s+(?=[A-Z0-9])", " ".join(paragraphs)
            )
            cleaned = " ".join(
                sentence
                for sentence in sentences
                if not _contains_deliberation(sentence)
            )
    else:
        cleaned = " ".join(paragraphs)

    cleaned = re.sub(r"^(?:final answer|engineer|radio)\s*:\s*", "", cleaned, flags=re.I)
    cleaned = " ".join(cleaned.split()).strip()
    if _contains_deliberation(cleaned):
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", cleaned)
        safe = [sentence for sentence in sentences if not _contains_deliberation(sentence)]
        cleaned = " ".join(safe).strip()
    return cleaned


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
    # Cached prefix tokens bill at a fraction of the normal input rate, so this
    # is the number that shows whether prefix caching is actually working.
    details = getattr(usage, "input_tokens_details", None)
    cached = getattr(details, "cached_tokens", None) if details is not None else None
    if cached is not None:
        result["cached_input_tokens"] = int(cached)
    return result


def _merge_usage(total: dict[str, int], current: dict[str, int]) -> None:
    for key, value in current.items():
        total[key] = total.get(key, 0) + int(value)


def _provider_error_code(exc: BaseException) -> str:
    """Best-effort extraction of stable provider error codes without SDK coupling."""
    direct = getattr(exc, "code", None)
    if isinstance(direct, str):
        return direct.strip().lower()

    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        candidate = body.get("code")
        error = body.get("error")
        if isinstance(error, dict):
            candidate = error.get("code", candidate)
        if isinstance(candidate, str):
            return candidate.strip().lower()
    return ""


def _is_health_failure(exc: BaseException) -> bool:
    """Return True only for failures that indicate provider availability trouble."""
    if isinstance(exc, (ProviderConfigurationError, ProviderTruncationError)):
        return False
    if isinstance(exc, (ProviderDeadlineError, asyncio.TimeoutError, TimeoutError)):
        return True

    name = type(exc).__name__
    status_code = getattr(exc, "status_code", None)
    code = _provider_error_code(exc)
    non_transient_codes = {
        "insufficient_quota",
        "billing_hard_limit_reached",
        "invalid_api_key",
        "account_deactivated",
        "credit_balance_insufficient",
    }
    if code in non_transient_codes or status_code == 402:
        return False
    if name in {
        "AuthenticationError",
        "PermissionDeniedError",
        "BadRequestError",
        "UnprocessableEntityError",
    }:
        return False
    if name in {
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
    }:
        return True
    if name == "RateLimitError":
        return True
    if isinstance(status_code, int):
        return status_code == 429 or status_code >= 500
    # Malformed/empty provider responses and unknown transport failures should
    # count, while known request/configuration failures above should not.
    return isinstance(exc, ProviderResponseError) or not isinstance(exc, ProviderError)


def _openai_response_truncated(response: Any) -> bool:
    status = getattr(response, "status", None)
    details = getattr(response, "incomplete_details", None)
    reason = getattr(details, "reason", None) if details is not None else None
    return status == "incomplete" and reason in {"max_output_tokens", "length"}


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
        self._client_injected = client is not None
        self.client = client
        if not self._client_injected:
            self.rebind_client()

    def rebind_client(self) -> None:
        """Rebuild from the current config after an API key change.

        An injected client (tests, offline diagnostics) is left alone; it was
        supplied deliberately and does not come from settings.
        """
        if self._client_injected:
            return
        self.client = (
            AsyncOpenAI(
                api_key=self.config.api_key,
                timeout=self.config.openai_timeout_s,
                max_retries=0,
            )
            if self.config.api_key
            else None
        )

    @property
    def available(self) -> bool:
        return self.client is not None

    def model_for(self, route: str) -> str:
        """Pick the model tier for a request route.

        Strategy, undercut evaluation and what-ifs justify the flagship tier.
        Reading back a gap, a tyre temperature or a lap time does not: it is
        narration of deterministic tool output, and the cheapest tier in the
        same family does it just as well for a fraction of the price.
        """
        if not self.config.tier_routing_enabled:
            return self.config.model
        return self.config.model if route == "deep" else self.config.fast_model

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
        if self.client is None:
            raise ProviderConfigurationError("OpenAI API key is not configured.")

        model = self.model_for(route)
        started = time.perf_counter()
        input_items: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        schema_by_name = {tool["name"]: tool for tool in tools}
        total_usage: dict[str, int] = {}

        for round_index in range(max_rounds):
            if effort in {"high", "xhigh", "max"}:
                base_budget = self.config.openai_deep_max_output_tokens
                retry_budget = self.config.openai_deep_retry_max_output_tokens
            elif effort == "medium":
                base_budget = 1100
                retry_budget = 2600
            else:
                base_budget = 520
                retry_budget = 1200

            response: Any | None = None
            for attempt, token_budget in enumerate((base_budget, retry_budget)):
                request: dict[str, Any] = {
                    "model": model,
                    "instructions": instructions,
                    "input": input_items,
                    "tools": tools,
                    "max_output_tokens": token_budget,
                    "parallel_tool_calls": True,
                    "text": {"verbosity": "low"},
                    # The persona and the tool schemas are identical on every
                    # call and sit at the front of the request, so they are a
                    # cacheable prefix. A stable key keeps successive radio
                    # calls on the same cache, which is the difference between
                    # paying full input price for that prefix every lap and
                    # paying it once.
                    "prompt_cache_key": f"pitwall-{route}-{self.config.engineer_name}",
                }
                if model.startswith("gpt-5"):
                    request["reasoning"] = {"effort": effort}

                response = await self.client.responses.create(**request)
                _merge_usage(total_usage, _usage_dict(getattr(response, "usage", None)))
                if _openai_response_truncated(response):
                    if attempt == 0:
                        continue
                    raise ProviderTruncationError(
                        "OpenAI exhausted the enlarged output-token budget."
                    )
                break

            assert response is not None
            calls = [
                item
                for item in response.output
                if getattr(item, "type", None) == "function_call"
            ]
            if not calls:
                text = _final_answer_only(response.output_text or "")
                if not text:
                    raise ProviderResponseError("OpenAI returned no final text.")
                return ProviderResult(
                    text=text,
                    provider=self.name,
                    model=model,
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
                    result = {
                        "error": f"Tool execution failed: {type(exc).__name__}: {exc}"
                    }
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
                max_retries=0,
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
            if thinking_enabled:
                base_budget = self.config.deepseek_deep_max_tokens
                retry_budget = self.config.deepseek_deep_retry_max_tokens
            else:
                base_budget = 650
                retry_budget = 1400

            response: Any | None = None
            choice: Any | None = None
            message: Any | None = None
            for attempt, token_budget in enumerate((base_budget, retry_budget)):
                request: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": token_budget,
                    "extra_body": {
                        "thinking": {
                            "type": "enabled" if thinking_enabled else "disabled"
                        }
                    },
                }
                if chat_tools:
                    request["tools"] = chat_tools
                    # DeepSeek V4 thinking mode rejects tool_choice on some official
                    # integrations. Omit it entirely there; non-thinking mode accepts auto.
                    if not thinking_enabled:
                        request["tool_choice"] = "auto"
                if thinking_enabled:
                    request["reasoning_effort"] = self.config.deepseek_thinking_effort

                response = await self.client.chat.completions.create(**request)
                _merge_usage(total_usage, _usage_dict(getattr(response, "usage", None)))
                if not response.choices:
                    raise ProviderResponseError("DeepSeek returned no choices.")
                choice = response.choices[0]
                if getattr(choice, "finish_reason", None) == "length":
                    if attempt == 0:
                        continue
                    raise ProviderTruncationError(
                        "DeepSeek exhausted the enlarged output-token budget."
                    )
                message = choice.message
                break

            assert response is not None and choice is not None and message is not None
            calls = list(getattr(message, "tool_calls", None) or [])
            if not calls:
                text = _final_answer_only(getattr(message, "content", None) or "")
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

            # DeepSeek V4 thinking-mode tool loops require reasoning_content to be
            # replayed with the assistant tool-call message.
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
                    result = {
                        "error": f"Tool execution failed: {type(exc).__name__}: {exc}"
                    }
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
    """Select providers, fail over quickly, and preserve deterministic tool evidence."""

    def __init__(
        self,
        config: Settings = settings,
        providers: dict[str, EngineerProvider] | None = None,
    ) -> None:
        self.config = config
        # Production race radio is intentionally OpenAI-only. The DeepSeek
        # implementation remains in this module solely for backward-compatible
        # tests and offline migration diagnostics; it is never registered here.
        self.providers: dict[str, EngineerProvider] = providers or {
            "openai": OpenAIResponsesProvider(config),
        }
        self.circuits = {name: _CircuitState() for name in self.providers}
        self.last_result: ProviderResult | None = None
        self.last_shakedown: dict[str, Any] | None = None
        self._last_compare_at = 0.0

    def rebind_clients(self) -> None:
        """Re-read credentials after an API key change, and forgive the circuit.

        A provider tripped by "no key configured" must not stay open once a
        working key is supplied, or the first radio call after activation would
        still fail.
        """
        for name, provider in self.providers.items():
            rebind = getattr(provider, "rebind_client", None)
            if callable(rebind):
                rebind()
            self.circuits[name] = _CircuitState()

    def _resolved_primary(self, explicit: str | None = None) -> str:
        del explicit
        return "openai"

    def _preferred_order(self, explicit: str | None = None) -> list[str]:
        del explicit
        return ["openai"] if "openai" in self.providers else []

    def _deadline_for(self, route: str) -> float:
        return (
            self.config.llm_deep_deadline_s
            if route == "deep"
            else self.config.llm_normal_deadline_s
        )

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

        deadline = self._deadline_for(route)
        for name in self._preferred_order(provider):
            implementation = self.providers[name]
            circuit = self.circuits[name]
            now = time.monotonic()
            if not implementation.available:
                errors.append(f"{name}: credentials not configured")
                continue
            if circuit.blocked_until > now:
                errors.append(f"{name}: temporarily cooling down after an API failure")
                continue
            try:
                try:
                    async with asyncio.timeout(deadline):
                        result = await implementation.generate(
                            prompt=prompt,
                            instructions=instructions,
                            route=route,
                            effort=effort,
                            tools=tools,
                            execute_tool=execute_once,
                            max_rounds=max_rounds,
                        )
                except TimeoutError as exc:
                    raise ProviderDeadlineError(
                        f"{name} exceeded the {deadline:.0f}-second {route} deadline."
                    ) from exc

                circuit.failures = 0
                circuit.blocked_until = 0.0
                circuit.last_error = ""
                self.last_result = result
                return result
            except Exception as exc:
                if _is_health_failure(exc):
                    circuit.failures += 1
                    if circuit.failures >= 2:
                        circuit.blocked_until = (
                            time.monotonic() + self.config.llm_failure_cooldown_s
                        )
                circuit.last_error = f"{type(exc).__name__}: {exc}"
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
        if not self.config.llm_compare_enabled:
            raise ProviderConfigurationError("LLM comparison is disabled in .env")
        now = time.monotonic()
        elapsed = now - self._last_compare_at
        if elapsed < self.config.llm_compare_cooldown_s:
            remaining = self.config.llm_compare_cooldown_s - elapsed
            raise ProviderRateLimitError(
                f"A/B comparison is cooling down for {remaining:.1f} more seconds."
            )
        self._last_compare_at = now

        # Both providers share one memoized tool executor. Matching tool calls see
        # byte-for-byte identical evidence, and stateful setup tools are blocked.
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
                async with asyncio.timeout(self._deadline_for(route)):
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

    async def shakedown(self, provider: str | None = None) -> dict[str, Any]:
        """Run a small live wire-contract check without touching race state."""
        names = self._preferred_order(provider)
        diagnostic_tool = {
            "type": "function",
            "name": "diagnostic_echo",
            "description": "Return the supplied diagnostic token unchanged.",
            "parameters": {
                "type": "object",
                "properties": {"token": {"type": "string"}},
                "required": ["token"],
                "additionalProperties": False,
            },
            "strict": True,
        }

        async def execute_diagnostic(
            tool_name: str,
            arguments: dict[str, Any],
        ) -> dict[str, Any]:
            if tool_name != "diagnostic_echo":
                return {"error": f"Unexpected diagnostic tool: {tool_name}"}
            return {"token": arguments.get("token"), "ok": True}

        async def stage(
            implementation: EngineerProvider,
            *,
            route: str,
            tools: list[dict[str, Any]],
            prompt: str,
            require_tool: bool,
        ) -> dict[str, Any]:
            started = time.perf_counter()
            try:
                async with asyncio.timeout(self.config.llm_shakedown_timeout_s):
                    result = await implementation.generate(
                        prompt=prompt,
                        instructions=(
                            "This is a Your Pit Box provider diagnostic. Follow the request "
                            "exactly and keep the final answer to one word: READY."
                        ),
                        route=route,
                        effort="high" if route == "deep" else "low",
                        tools=tools,
                        execute_tool=execute_diagnostic,
                        max_rounds=3,
                    )
                tool_ok = not require_tool or result.tool_rounds >= 1
                return {
                    "ok": tool_ok,
                    "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
                    "model": result.model,
                    "tool_rounds": result.tool_rounds,
                    "detail": "ready" if tool_ok else "model did not call the diagnostic tool",
                }
            except Exception as exc:
                return {
                    "ok": False,
                    "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
                    "error": f"{type(exc).__name__}: {exc}",
                }

        async def run(name: str) -> tuple[str, dict[str, Any]]:
            implementation = self.providers[name]
            if not implementation.available:
                return name, {"configured": False, "ready": False}
            basic = await stage(
                implementation,
                route="normal",
                tools=[],
                prompt="Reply exactly READY.",
                require_tool=False,
            )
            nonthinking = await stage(
                implementation,
                route="normal",
                tools=[diagnostic_tool],
                prompt=(
                    "Call diagnostic_echo with token pitwall-ready, then reply READY. "
                    "You must use the tool before answering."
                ),
                require_tool=True,
            )
            thinking = await stage(
                implementation,
                route="deep",
                tools=[diagnostic_tool],
                prompt=(
                    "Call diagnostic_echo with token pitwall-deep-ready, then reply READY. "
                    "You must use the tool before answering."
                ),
                require_tool=True,
            )
            stages = {
                "basic": basic,
                "nonthinking_tool": nonthinking,
                "thinking_tool": thinking,
            }
            return name, {
                "configured": True,
                "ready": all(item.get("ok") for item in stages.values()),
                "stages": stages,
            }

        results = dict(await asyncio.gather(*(run(name) for name in names)))
        payload = {
            "checked_at_monotonic": round(time.monotonic(), 3),
            "providers": results,
            "ready": bool(results) and all(item.get("ready") for item in results.values()),
        }
        self.last_shakedown = payload
        return payload

    def status(self) -> dict[str, Any]:
        resolved = self._resolved_primary()
        return {
            # selected remains for backward compatibility, but now reports the
            # concrete provider instead of the unresolved literal "auto".
            "selected": resolved,
            "configured_provider": self.config.llm_provider,
            "resolved_provider": resolved,
            "fallback": self.config.llm_fallback_provider,
            "active_provider": self.last_result.provider if self.last_result else None,
            "active_model": self.last_result.model if self.last_result else None,
            "normal_deadline_s": self.config.llm_normal_deadline_s,
            "deep_deadline_s": self.config.llm_deep_deadline_s,
            "compare_enabled": self.config.llm_compare_enabled,
            "last_shakedown": self.last_shakedown,
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
