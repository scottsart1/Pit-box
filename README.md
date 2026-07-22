# Pit Wall 3.3.0 — DeepSeek + OpenAI race engineer for PS5 and Windows

Pit Wall receives F1 25 / 2026 Season Pack telemetry from a PS5 over UDP, runs deterministic strategy/corner/setup analysis locally, keeps persistent SQLite history, answers spoken questions, makes proactive radio calls, and serves a live dashboard at `http://127.0.0.1:8000`.

Version 3.3 makes the engineer brain provider-neutral:

```text
Routine and normal reasoning  -> DeepSeek V4 Flash
Strategy / setup / what-if     -> DeepSeek V4 Pro, thinking enabled
Provider failure               -> OpenAI fallback
Speech-to-text and TTS         -> OpenAI audio models
```

The existing `L3 -> F1 UDP Action 1` binding, hands-free “Mark” trigger, dashboard, strategy engine, SQLite data, and audio pipeline are unchanged.

## What changed in 3.3

### DeepSeek provider

Pit Wall now uses DeepSeek's OpenAI-compatible Chat Completions endpoint for the engineer brain. It supports:

- `deepseek-v4-flash` for normal radio calls.
- `deepseek-v4-pro` for deep strategy, setup, SC/VSC/red-flag and what-if questions.
- Thinking disabled for routine calls to reduce latency.
- Thinking enabled with `high` or `max` effort for deep calls.
- Parallel execution of independent telemetry tools.
- Preservation of DeepSeek `reasoning_content` across thinking-mode tool rounds, as required by DeepSeek's API.
- Local JSON-schema validation before any model-generated tool arguments are executed.
- A hard tool-round limit.

The older `deepseek-chat` and `deepseek-reasoner` names are not used.

### Provider failover and circuit breaker

A transient DeepSeek failure can fall back to OpenAI without losing the radio call. Matching tool calls are memoized for the request, so a fallback provider sees the same tool result and does not repeat potentially expensive or stateful analysis.

After repeated provider failures, Pit Wall temporarily opens a short circuit breaker instead of adding the same failed API wait to every radio call. Provider health is visible at:

```text
http://127.0.0.1:8000/api/llm/providers
```

The dashboard footer shows the provider and model that answered the latest model-backed call.

### Safe A/B comparison

When both API keys are configured, an optional endpoint compares OpenAI and DeepSeek on one frozen prompt:

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/llm/compare" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"text":"Compare medium-hard and medium-soft for this race."}'
```

Both providers share memoized telemetry-tool results. Setup-generation tools are blocked during comparison because they persist recommendations. The comparison does not speak either answer or append it to the radio log. It does consume both providers' API tokens.

### Existing 3.2 latency and voice improvements remain

- Thin left rail: yellow listening, green processing, blue speaking, red error.
- Cached “Copy” and “Copy, stand by” acknowledgement clips.
- Streamed 24 kHz PCM TTS.
- Deterministic fast path for basic telemetry questions.
- Fast/normal/deep request routing.
- Last-call latency instrumentation.
- Wake state persistence and queued PTT clips.
- L3/UDP Action packets remain isolated from brake, throttle, gear and MFD inputs.

## Recommended `.env`

Copy `.env.example` to `.env`, then set both keys:

```env
# Primary reasoning provider
PITWALL_LLM_PROVIDER=deepseek
PITWALL_LLM_FALLBACK_PROVIDER=openai
DEEPSEEK_API_KEY=your_deepseek_api_key
PITWALL_DEEPSEEK_BASE_URL=https://api.deepseek.com
PITWALL_DEEPSEEK_FAST_MODEL=deepseek-v4-flash
PITWALL_DEEPSEEK_DEEP_MODEL=deepseek-v4-pro
PITWALL_DEEPSEEK_THINKING_EFFORT=high
PITWALL_DEEPSEEK_TIMEOUT_S=45
PITWALL_DEEPSEEK_MAX_TOOL_ROUNDS=4
PITWALL_DEEPSEEK_STRICT_TOOLS=false
PITWALL_LLM_FAILURE_COOLDOWN_S=20
PITWALL_LLM_COMPARE_ENABLED=true

# OpenAI is retained for voice and LLM fallback
OPENAI_API_KEY=your_openai_api_key
PITWALL_MODEL=gpt-5.6-terra
PITWALL_REASONING_EFFORT=low
PITWALL_DEEP_REASONING_EFFORT=high
PITWALL_OPENAI_TIMEOUT_S=45
PITWALL_STT_MODEL=gpt-4o-mini-transcribe
PITWALL_TTS_MODEL=gpt-4o-mini-tts
PITWALL_VOICE=coral
```

Keep the rest of your existing UDP, wake, proactive and audio settings.

### Other provider modes

DeepSeek only for the brain, no model fallback:

```env
PITWALL_LLM_PROVIDER=deepseek
PITWALL_LLM_FALLBACK_PROVIDER=none
```

OpenAI brain only:

```env
PITWALL_LLM_PROVIDER=openai
PITWALL_LLM_FALLBACK_PROVIDER=none
```

Automatically prefer DeepSeek when configured, otherwise OpenAI:

```env
PITWALL_LLM_PROVIDER=auto
PITWALL_LLM_FALLBACK_PROVIDER=auto
```

OpenAI is still required for the current cloud STT/TTS path when native voice is enabled.

## Install or upgrade on Windows

Use 64-bit Python 3.11 or 3.12.

1. Stop every existing Pit Wall process.
2. Extract this ZIP into a fresh writable folder.
3. Copy only your existing `.env` into the new folder, then add the DeepSeek settings above.
4. Do not copy or delete `%USERPROFILE%\PitWallData`; it contains your database and saved PTT/wake configuration.
5. Open PowerShell in the new folder:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_windows.ps1
```

6. Start:

```powershell
.\start_pitwall.bat
```

Your existing L3/UDP Action 1 binding is reused automatically.

## Health check

Open:

```text
http://127.0.0.1:8000/api/health
```

Expected provider fields:

```json
{
  "openai_key_configured": true,
  "deepseek_key_configured": true,
  "llm": {
    "selected": "deepseek",
    "fallback": "openai",
    "providers": {
      "deepseek": {"configured": true},
      "openai": {"configured": true}
    }
  }
}
```

After a model-backed question, `active_provider` and `active_model` identify which provider actually answered.

## First shakedown

Use a practice or qualifying session:

```text
Mark, what is my target lap?
Mark, give me the last two laps of the car ahead.
Mark, how should I approach the car ahead?
Mark, compare the top two strategies and tell me what changes the call.
```

Expected routing:

```text
Simple telemetry question       -> deterministic fast path, no LLM
Normal interpretation           -> DeepSeek V4 Flash
Strategy / setup / what-if      -> DeepSeek V4 Pro with thinking
DeepSeek API failure            -> OpenAI fallback
```

## Tool safety

The model never receives direct filesystem, shell, network, or database-write access. It can only request the allow-listed telemetry tools defined in `TelemetryTools.schemas()`.

Before execution, Pit Wall:

1. Confirms the tool name is allow-listed.
2. Parses arguments as JSON.
3. Validates arguments against the local JSON schema.
4. Converts tool exceptions into bounded error results rather than crashing the radio pipeline.
5. Limits the number of tool rounds.

DeepSeek strict tool mode exists behind a beta endpoint. It is disabled by default because local validation already protects execution and the non-beta endpoint is the safer race-day default.

## Data and privacy

Runtime data remains under:

```text
%USERPROFILE%\PitWallData
```

Pit Wall sends compact situation summaries, recent radio text and selected tool results to the chosen reasoning provider. It does not upload the SQLite database, raw 60 Hz trace files, Windows username, or full microphone recordings to the reasoning model. Audio clips are sent to OpenAI transcription when cloud voice is enabled.

Using DeepSeek's hosted API means the compact prompt is processed under DeepSeek's service and privacy terms. Choose `PITWALL_LLM_PROVIDER=openai` when you do not want race context sent to DeepSeek.

Never commit or share `.env`.

## Troubleshooting

### DeepSeek fails but radio still answers

Check `/api/llm/providers`. If `active_provider` is `openai`, failover worked. The DeepSeek error remains visible under its provider status.

Common causes:

- Missing or invalid `DEEPSEEK_API_KEY`.
- No DeepSeek API credit.
- Temporary service or network failure.
- An unavailable model name.

### Both providers fail

The dashboard turns red and `/api/health` reports the provider errors. Verify both keys and billing, then restart Pit Wall.

### Deep questions are slow

Keep Flash for normal calls and Pro only for deep calls. `PITWALL_DEEPSEEK_THINKING_EFFORT=max` can improve difficult planning but may increase latency; `high` is the recommended race-day setting.

### Strict tool mode fails

Set:

```env
PITWALL_DEEPSEEK_STRICT_TOOLS=false
```

Strict mode uses DeepSeek's beta endpoint and supports a narrower JSON-schema subset.

## Verification

The provider-neutral changes were checked with:

```text
49 locally executable automated tests passed
Ruff static checks passed
Python compilation passed
Dashboard JavaScript syntax passed
```

The 49 tests include all strategy, setup, persistence, racing-line, proactive, latency, wake, tools and provider tests executable in this build environment. Twelve UDP/parser tests require the `f1-packets` distribution, which the Windows installer installs from PyPI and runs as part of the complete 61-test suite.

New provider tests cover:

- DeepSeek Flash non-thinking routing.
- DeepSeek Pro thinking routing.
- Preservation of `reasoning_content` during tool loops.
- Local rejection of malformed tool arguments.
- DeepSeek-to-OpenAI fallback.
- Shared tool results during fallback.
- Shared evidence and blocked setup mutation during A/B comparison.

See `docs/PROVIDER_ARCHITECTURE.md` and `docs/VERIFICATION.md`.
