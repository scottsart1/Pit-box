# Provider architecture — Pit Wall 3.3

## Goals

- Allow DeepSeek or OpenAI to act as the engineer brain without changing telemetry, strategy, setup, persistence, voice or dashboard code.
- Keep deterministic telemetry tools as the source of numeric truth.
- Fail over quickly during a live session.
- Prevent model-generated tool arguments from bypassing local validation.
- Preserve the special requirements of each provider's tool loop.

## Flow

```text
EngineerBrain
  -> ProviderRouter
       -> DeepSeekChatProvider
       -> OpenAIResponsesProvider
  -> TelemetryTools
  -> StateStore / deterministic analysis / SQLite
```

`EngineerBrain` owns F1-specific prompting and request classification. Providers only translate a compact prompt and the same tool schemas to their API format.

## Route selection

```text
fast   -> deterministic local answer when possible
normal -> DeepSeek V4 Flash, thinking disabled
 deep  -> DeepSeek V4 Pro, thinking enabled
```

When OpenAI is selected, the existing GPT model receives the configured reasoning effort.

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

For thinking-mode tool calls, DeepSeek requires the assistant's `reasoning_content` to be sent back with the assistant tool-call message. Pit Wall preserves it internally for continuity but never displays or logs it.

## Local tool validation

Every tool call is validated with JSON Schema before execution. Invalid calls become tool error results, allowing the model to correct the request in a later tool round.

The provider cannot:

- invoke a tool not in the allow-list;
- add undeclared arguments;
- omit required arguments;
- pass the wrong JSON type;
- exceed the configured tool-round limit.

## Failover consistency

The router memoizes tool results by canonical `(tool_name, arguments)` JSON for one radio request. If DeepSeek fails after calling a tool and OpenAI takes over, the fallback receives the same result rather than executing the tool again.

This matters for expensive strategy calculations and tools such as setup generation that can persist a recommendation.

## Circuit breaker

Each provider maintains:

- consecutive failure count;
- most recent error;
- temporary cooldown deadline.

One failure permits immediate fallback. Repeated failures open a short cooldown so the same unavailable provider does not add a full timeout to every subsequent radio call.

## A/B comparison

`POST /api/llm/compare` sends an identical prompt to both configured providers concurrently.

- Matching tool calls use a shared cache.
- `generate_setup` and `get_front_wing_adjustment` are blocked because they can persist or alter setup state.
- Results are returned as text only; they are not spoken or added to radio history.
- The endpoint is opt-in through `PITWALL_LLM_COMPARE_ENABLED`.

## Strict tool mode

DeepSeek strict tool mode is beta and requires the `/beta` base URL. Pit Wall switches automatically when `PITWALL_DEEPSEEK_STRICT_TOOLS=true`.

The default is `false`. Local schema validation remains active regardless, providing execution safety without depending on a beta endpoint.

## Privacy boundary

The provider receives:

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
