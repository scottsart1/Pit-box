# Provider architecture — Pit Wall 3.3.1

## Goals

- Allow DeepSeek or OpenAI to act as the engineer brain without changing telemetry, strategy, setup, persistence, voice, or dashboard code.
- Keep deterministic telemetry tools as the source of numeric truth.
- Fail over within a bounded race-radio deadline rather than after hidden SDK retries.
- Prevent model-generated tool arguments from bypassing local validation.
- Preserve the provider-specific requirements of each tool loop.
- Make live API readiness observable before entering a session.

## Flow

```text
EngineerBrain
  -> ProviderRouter
       -> DeepSeekChatProvider
       -> OpenAIResponsesProvider
  -> TelemetryTools
  -> StateStore / deterministic analysis / SQLite
```

`EngineerBrain` owns F1-specific prompting and request classification. Providers translate one explicit prompt and the same allow-listed tool schemas to their API format.

## Route selection and deadlines

```text
fast    -> deterministic local answer when possible
normal  -> DeepSeek V4 Flash, thinking disabled; 12 s provider deadline
deep    -> DeepSeek V4 Pro, thinking enabled; 25 s provider deadline
```

The route deadline covers the provider's complete text/tool loop. Both provider SDK clients use `max_retries=0`; after the route deadline the router can immediately try the fallback. The SDK-level HTTP timeout remains a separate upper safety bound.

When OpenAI is selected, the configured GPT model receives the matching reasoning effort. Deep calls receive a larger output-token allowance because reasoning tokens share the model budget.

## DeepSeek tool loop

DeepSeek uses Chat Completions. Pit Wall converts each Responses-style tool schema:

```json
{
  "type": "function",
  "name": "get_gap",
  "parameters": {}
}
```

into Chat Completions format:

```json
{
  "type": "function",
  "function": {
    "name": "get_gap",
    "parameters": {}
  }
}
```

Thinking mode uses the documented `thinking` toggle and `reasoning_effort`. If a thinking turn makes a tool call, the assistant's `reasoning_content` is sent back with that tool-call message on subsequent requests, as required by the DeepSeek tool-call contract. It is never shown in the dashboard or written to Pit Wall history.

`tool_choice` is omitted in thinking mode. Empty tool lists are omitted entirely to avoid sending unnecessary tool parameters during basic text diagnostics.

## Local tool validation

Every tool call is validated with JSON Schema before execution. Invalid calls become bounded tool-error results, allowing the model to correct the request in a later round.

The provider cannot:

- invoke a tool not in the allow-list;
- add undeclared arguments;
- omit required arguments;
- pass the wrong JSON type;
- exceed the configured tool-round limit.

## Failover consistency

The router memoizes tool results by canonical `(tool_name, arguments)` JSON for one radio request. If DeepSeek fails after calling a tool and OpenAI takes over, the fallback receives the same result rather than executing the calculation again.

This matters for expensive strategy calculations and for setup tools that can persist a recommendation.

## Failure classification and circuit breaker

Each provider maintains:

- consecutive provider-health failure count;
- most recent error;
- temporary cooldown deadline.

Failures that indicate availability trouble count toward the circuit:

- route or network timeout;
- connection failure;
- ordinary rate limiting;
- server-side 5xx failure;
- malformed or empty provider response.

Failures that require user/configuration action do not mark the provider temporarily unhealthy:

- invalid or missing credentials;
- permission or bad-request errors;
- insufficient quota/credit or billing hard limit;
- invalid tool arguments;
- token-budget truncation.

Two consecutive health failures open the configured short cooldown. One failure still permits immediate fallback.

## Truncation recovery

A response ending because its output budget was exhausted is not treated as an outage. Pit Wall retries the same provider once with a larger budget:

```text
DeepSeek deep: 2,200 -> 6,000 max tokens
OpenAI deep:   2,200 -> 6,000 max output tokens
```

If the enlarged retry also truncates, the fallback may answer, but the primary provider's circuit failure count remains unchanged.

## Live provider shakedown

`POST /api/llm/shakedown` performs three explicit, low-volume checks for each configured provider:

1. Basic text completion.
2. Non-thinking function call and tool-result continuation.
3. Thinking-mode function call and tool-result continuation.

The dashboard's **Test providers** button shows per-stage readiness, model, tool rounds, and latency. It is a real API call and therefore checks credentials, account access, the current wire contract, and the provider service—not merely local mocks.

The shakedown never reads or changes race state; it uses a synthetic `diagnostic_echo` tool.

## A/B comparison

`POST /api/llm/compare` sends an identical prompt to both configured providers concurrently.

- It is disabled by default.
- Enabling it is explicit because each comparison consumes both providers' tokens.
- A 30-second cooldown limits accidental repeated double billing.
- Matching tool calls use a shared cache.
- `generate_setup` and `get_front_wing_adjustment` are blocked because they can persist or alter setup state.
- Results are returned as text only; they are not spoken or added to radio history.

## Provider status

`/api/health` and `/api/llm/providers` distinguish:

```text
configured_provider  literal .env choice, e.g. auto
resolved_provider    concrete preferred provider, e.g. deepseek
active_provider      provider that answered the latest model-backed call
active_model         model that answered it
fallback             configured fallback provider
```

This avoids the previous ambiguity where the UI could display `auto` while DeepSeek was actually selected.

## Strict tool mode

DeepSeek strict tool mode is beta and requires the `/beta` base URL. Pit Wall switches automatically when `PITWALL_DEEPSEEK_STRICT_TOOLS=true`.

The default is `false`. Local schema validation remains active regardless, providing execution safety without depending on a beta endpoint.

## Privacy boundary

The reasoning provider receives:

- compact situation header;
- recent radio text;
- user utterance;
- compact telemetry tool results.

It does not receive:

- raw UDP packets;
- full 60 Hz traces;
- the SQLite database;
- local file paths or usernames;
- microphone audio.

OpenAI STT receives only the bounded audio clip being transcribed.
