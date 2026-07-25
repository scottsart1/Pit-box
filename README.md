# Pit Wall 3.5.0 — DeepSeek + OpenAI race engineer for PS5 and Windows

Pit Wall receives F1 25 / 2026 Season Pack telemetry from a PS5 over UDP, runs deterministic strategy/corner/setup analysis locally, keeps persistent SQLite history, answers spoken questions, makes proactive radio calls, and serves a live dashboard at `http://127.0.0.1:8000`.

## What changed in 3.5.0

3.5 is a capability release. It adds nine engineer tools, six proactive radio
calls, a real-time delta, an overlay/second-screen surface, data export and
personalisation. The design invariant is unchanged: deterministic code computes
the numbers and the language model narrates them. No database migration is
required.

### Race intelligence and real-time

- **Live delta** to your personal best, interpolated each telemetry tick from a
  normalised reference lap, shown on the overlay and in `get_my_car_state`.
- **Race-start call**: grid slot, clean/dirty side and a lap-one plan, from the
  now-read `grid_position`.
- **Safety-car restart prep** on the transition into an ending neutralisation.
- **Rival pace radar**: flags a car behind closing over a rolling window.
- **Energy deployment**: a low-battery warning while an attack aid is available,
  plus a `get_energy_plan` deploy/harvest tool (2026 Manual Override aware).
- **Component wear**: escalating power-unit wear warnings toward grid penalties,
  from the previously-unused engine-wear fields.

### Strategy depth

- **Rival strategy prediction** (`predict_rival_strategy`): estimated pit
  windows and pre-emptive undercut threats from tyre age and compound life.
- **Cold-tyre out-lap penalty** in the stint model, so undercut maths accounts
  for warm-up.
- **Fuel-save vs push** trade (`get_pace_mode_options`).
- **Championship view** (`get_championship_scenario`): F1 points per plan's
  projected finish, to weigh a safe result against a gamble.

### Analytics and debrief

- **Sector bests / theoretical best**, **cross-session progress trend**, and
  **setup-to-performance correlation** tools, unblocked by persisted sectors and
  results.
- **Post-session debrief**: `GET /api/debrief` and `get_session_debrief`
  summarise pace, consistency, tyres, result and the biggest corner losses.

### Surfaces and personalisation

- **OBS / second-screen overlay** at `/overlay` (transparent for a browser
  source; `?bg=1` for an opaque phone panel), using `wss://` under HTTPS.
- **Data export**: `GET /api/export/laps.csv` and `/api/export/session.json`,
  linked from the Review tab.
- **Custom engineer** name, persona and radio verbosity (terse/standard/chatty)
  via `.env`, folded into the persona without dropping safety-critical rules.

### Multiplayer foundation

- The **collision** event now decodes severity and player involvement, and a
  **lobby-info** handler tracks player/ready counts. Both were previously
  ignored.

Deferred: a local offline speech pipeline (item 24) — it requires model
downloads and real audio-device evaluation outside this build's scope.

## What changed in 3.4.1

3.4.1 is the reviewed and corrected form of the 3.4 extraction release. It
retains the recovered telemetry and analysis, fixes the defects found during an
independent code review, and requires no database migration.

### Coaching now quotes the numbers

Corner coaching already measured how far off the personal best each corner was,
but only reported a cause. The measured deltas are now kept and spoken:

```text
before  At Corner 3, release the brake point a little later.
after   At Corner 3, brake 8 metres later while protecting apex speed.
after   At Corner 3, brake 8 metres earlier — you are 6 km/h down at the apex.
```

Brake-point, apex-speed and throttle-application deltas are attached to each
corner metric and passed to the engineer, so the radio and the dashboard can
both reference real figures. Differences below a couple of metres or km/h are
treated as noise and left unspoken.

### Sector times are actually stored

Lap sector times were persisted as zero because the lap summary carried an empty
timing payload. Sectors are now written, and the session-history packet directly
triggers database backfill when it arrives after lap analysis. This also covers
the final lap, when no later lap exists to trigger another analysis cycle.

### Sessions record their result

`ended_at` and `result_position` existed in the schema but were never written, so
there was no record of how any session finished. The result is now saved in
the same packet-handling cycle as final
classification instead of relying on the periodic watchdog. Repeated watchdog
writes preserve the first `ended_at` timestamp rather than moving it forward.

### Sprint weekends are classified from the weekend sequence

The 2026 packet enum names IDs 15, 16 and 17 as Race, Race 2 and Race 3; those
are sequence slots, not permanent "Sprint" labels. On a sprint weekend the
Sprint can be Race (15) and the Grand Prix Race 2 (16). Pit Wall now reads
`weekend_structure`: when Race (15) is followed by Race 2 or Race 3, the first
slot is treated as the Sprint and the later slot remains the Grand Prix. Without
that evidence, every race slot defaults to Grand Prix rules so the mandatory
two-dry-compound requirement is never silently waived.

"One-Shot Sprint Shootout" is also recognised as qualifying despite the
truncated display label.

### Power-unit damage and safety-car delta calls

- Engine, gearbox and DRS/ERS faults were captured but excluded from the damage
  change signature, so a failing power unit never produced a radio call. They are
  now included, and a DRS or ERS fault is called regardless of damage percentage.
- `safety_car_delta` was captured and never read. Running under the delta during
  a safety car or VSC is a penalty risk and is now called, repeating on a short
  cooldown while the breach lasts. Tune with `PITWALL_PROACTIVE_SC_DELTA_MIN_S`.

### Diagnostics

- Logs are written to a rotating `%USERPROFILE%\PitWallData\pitwall.log` as well
  as the console, so a session can be investigated after the console window
  closes.
- Unhandled UDP packet types are reported once instead of being dropped silently.
- All 18 proactive event types now have specific fallback text. Nine previously
  degraded to "Engineer update available on the dashboard" whenever the model
  call failed.
- Participants populate `team_id` and teammate detection. Human-readable team
  names are intentionally not guessed; `team` stays empty until a verified
  team-id table exists for the current title.
- Spectator packets (`player_car_index=255`) are ignored safely by player-only
  telemetry handlers instead of indexing beyond the 24-car arrays.
- Safety-car delta calls require a real Lap Data sample, preventing a configured
  positive threshold from treating the default zero value as a breach.

## Provider-neutral engineer retained from 3.3.1

Version 3.3.1 hardened the provider-neutral engineer for race-day failover:

```text
Routine and normal reasoning  -> DeepSeek V4 Flash
Strategy / setup / what-if     -> DeepSeek V4 Pro, thinking enabled
Provider failure               -> OpenAI fallback
Speech-to-text and TTS         -> OpenAI audio models
```

The existing `L3 -> F1 UDP Action 1` binding, hands-free “Mark” trigger, dashboard, strategy engine, SQLite data, and audio pipeline are unchanged.

### Race-radio provider deadlines and zero SDK retries

Each provider now gets one bounded wall-clock attempt before the router moves to the fallback:

```text
normal call  -> 12 seconds
strategy call -> 25 seconds
SDK retries  -> 0
```

This prevents a first provider outage from creating multiple hidden SDK retries and a long radio vacuum. The route deadline covers the complete provider/tool loop, not only one HTTP request.

### Truncation recovery without poisoning provider health

If a model exhausts its output budget, Pit Wall retries that same provider once with a larger budget. Only after the enlarged retry fails may the fallback provider answer. Token truncation does not increment the circuit breaker because it is not a provider outage.

### Safer provider configuration and diagnostics

- `none` is valid only for the fallback provider; an invalid primary resolves to `auto`.
- A/B comparison is disabled by default and has a 30-second cooldown when enabled.
- `/api/health` and `/api/llm/providers` report configured, resolved, active, and fallback providers separately.
- `POST /api/llm/shakedown` performs an explicit live text, non-thinking tool, and thinking-tool continuation check for every configured provider.
- The dashboard includes a **Test providers** button and displays the latest shakedown result.
- DeepSeek thinking requests omit `tool_choice`, while still preserving required `reasoning_content` across tool turns.
- Circuit breakers count only provider-health failures such as timeout, connection, rate-limit, server, or malformed-response failures. Authentication, bad-request, and token-budget errors remain visible without marking the provider temporarily unhealthy.

## Provider-neutral foundation retained

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
PITWALL_DEEPSEEK_TIMEOUT_S=30
PITWALL_DEEPSEEK_MAX_TOOL_ROUNDS=4
PITWALL_DEEPSEEK_STRICT_TOOLS=false
PITWALL_LLM_FAILURE_COOLDOWN_S=20
PITWALL_LLM_NORMAL_DEADLINE_S=12
PITWALL_LLM_DEEP_DEADLINE_S=25
PITWALL_LLM_SHAKEDOWN_TIMEOUT_S=20
PITWALL_LLM_COMPARE_ENABLED=false
PITWALL_LLM_COMPARE_COOLDOWN_S=30
PITWALL_DEEPSEEK_DEEP_MAX_TOKENS=2200
PITWALL_DEEPSEEK_DEEP_RETRY_MAX_TOKENS=6000

# OpenAI is retained for voice and LLM fallback
OPENAI_API_KEY=your_openai_api_key
PITWALL_MODEL=gpt-5.6-terra
PITWALL_REASONING_EFFORT=low
PITWALL_DEEP_REASONING_EFFORT=high
PITWALL_OPENAI_TIMEOUT_S=30
PITWALL_OPENAI_DEEP_MAX_OUTPUT_TOKENS=2200
PITWALL_OPENAI_DEEP_RETRY_MAX_OUTPUT_TOKENS=6000
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
    "configured_provider": "deepseek",
    "resolved_provider": "deepseek",
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

Before entering a session, click **Test providers** on the dashboard or run:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/llm/shakedown" -Method Post
```

This is a manual, low-volume live API diagnostic and consumes a small amount of credit. It checks basic text, a non-thinking tool continuation, and a thinking-tool continuation. Then use a practice or qualifying session:

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

Check `/api/llm/providers`. If `active_provider` is `openai`, failover worked. Normal calls fail over after 12 seconds and deep calls after 25 seconds by default; the SDK itself does not retry. The DeepSeek error remains visible under its provider status.

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

Completed for 3.5.0 with the full dependency set installed:

```text
144 automated tests passed
Ruff static checks passed
Python compileall passed
FastAPI startup and /api/health passed (version 3.5.0)
/overlay, /api/export/laps.csv, /api/export/session.json and /api/debrief served
Dashboard and overlay JavaScript syntax passed with Node.js
```

The 3.5 features add `tests/test_features_3_5.py` (43 tests) on top of the
existing suite. Each feature is driven through its deterministic layer so the
numbers are pinned independently of any language model; two representative
end-to-end paths (quantified coaching and sector persistence) were exercised in
earlier releases and remain covered. Live PS5 telemetry, microphone, speakers
and provider credentials remain hardware/account boundaries checked with the
dashboard shakedown and a live session.

The one deselected test exercises the real OpenAI SDK object's serialization.
`install_windows.ps1` installs that SDK and runs the complete suite on Windows.
Live PS5 telemetry, microphone, speakers and provider credentials remain hardware
or account boundaries and must be checked with the dashboard shakedown.

See `docs/VERIFICATION.md` and `docs/CLAUDE_REVIEW_3.4.1.md`.
