# Pit Wall 3.8.0 — OpenAI race engineer for PS5 and Windows

Pit Wall receives **F1 26** telemetry (UDP format **2026**) from a PS5, runs deterministic
strategy/corner/setup analysis locally, keeps persistent SQLite history, answers spoken questions,
makes proactive radio calls, and serves a live dashboard at `http://127.0.0.1:8000`.

> Set **UDP Format: 2026** on the PS5. The parser resolves packet format 2026 only; anything else
> is reported as `Unsupported F1 packet format/version`.

## What changed in 3.8.0

3.8.0 makes the pit wall outcome-aware and turns the dashboard into a standing race brief.

- Strategy now ranks legal plans by projected finishing position and championship points before
  elapsed time. Rival finish projections, circuit overtaking difficulty, positions recoverable,
  Monte Carlo position bands and conservative/balanced/aggressive risk preferences are explicit.
- Refusing a stop creates a five-lap driver hold. Automatic strategy calls remain silent until the
  hold expires or safety car, red flag, weather, damage or hard wear limits materially change.
- Tyre-state answers feed a bounded, decaying wear/degradation input. Conflicts with measured wear
  are visible, and feedback cannot by itself invert the position-primary plan.
- Manual pre-session briefs work without live telemetry; qualifying in-lap debriefs and final race
  reports are generated, persisted and shown on the dashboard, with deterministic fallback text.
- New live panels cover rival pace and sectors, damage and finish wear, pace degradation, the
  points-position objective, race flow and the full strategy rationale. The overlay carries the
  projected finish, wear and degradation or the active driver hold.
- Audio capture now uses one bounded consumer instead of one task per 30 ms block. Database VACUUM
  no longer holds the application database lock, and proactive delivery records why calls were
  blocked, expired or superseded.
- `tools/replay_demo.py` now emits 2026 active-aero/Manual Override state, lap-position history and
  a final classification packet so the race-report path can be exercised end to end.

## What changed in 3.7.0

3.7.0 is a field-awareness and conversation release. The engineer can now see the whole
grid rather than only your car, holds a real conversation instead of matching keywords, and
costs a fraction of what it did per race.

### The engineer can see the whole field

Every F1 26 telemetry packet carries all 24 cars. Earlier releases read only `player_car_index`
from the damage, status, telemetry and motion packets and discarded the rest, so a question about
a rival was answered with your own car's data. All 24 are now captured:

- **Rival condition** — per-wheel tyre wear, blisters, brake and body damage, power-unit wear,
  blown/seized engine.
- **Rival energy and fuel** — battery store, deploy mode, harvest, fuel load and margin.
  `get_attack_plan` used to state that opponent ERS was not exposed; it is.
- **Live pit stops** — `pit_lane_timer_active` and `pit_stop_timer_in_ms`, so a rival's stop is
  called while it happens rather than inferred a lap later.
- **Retirements** — `result_status` distinguishes retired, disqualified and not classified. A
  retired car no longer keeps quoting the gap it held when it stopped.
- **Marshal zones** — up to 21 live flag zones, converted to metres, so a yellow can be located
  on the lap. Plus DRS and 2026 active-aero zones.
- **Driver identity** — `driver_id` and `race_number` resolve first names, nicknames and car
  numbers. "Has Max boxed yet" previously returned "No data available for Max", because the game
  only sends the surname `VERSTAPPEN`. Team names now resolve too.
- Previously empty event payloads are decoded: retirements record *who* retired, speed traps
  record the speed, penalties record the infringement.
- `PacketTimeTrialData` has a handler for the first time.

New tools: `get_rival_car_state`, `get_field_state`, `get_race_flow`, `get_flag_status`.

### Conversation instead of keyword matching

The deterministic fast path was a keyword cascade over your own car that answered on the first
match. Recorded sessions show it returning your damage to "does Max have any damage on his car"
three times in a row, and returning the standing pit call to a question about a hypothetical
undercut. It now stands aside whenever the request names a rival, corrects or refuses something,
or asks for reasoning — and hands the model the field-wide tools instead.

- **Negation is understood.** "I'm not going to take another pit stop" no longer locks a pit stop.
- **History is no longer deleted.** Earlier turns were dropped whenever they looked like strategy
  requests, so "please answer my question" arrived with no question attached and the engineer
  replied that it had not come through.
- **Standing instructions persist.** "Stop telling me about engine damage" is remembered across
  laps and sessions, and cannot override a safety-critical call.

### Speech-to-speech radio (optional)

`PITWALL_VOICE_REALTIME_ENABLED=true` routes the radio through the Realtime API
(`gpt-realtime-2.1-mini`). The wake phrase or L3 opens a live session: the engineer can be
interrupted mid-sentence, answers without the "stand by" acknowledgement that exists only to mask
chain latency, and calls the same allow-listed telemetry tools. The session closes after a pause
and has a hard length cap, because it bills for audio while connected. The file-based
transcribe → reason → speak pipeline remains as the fallback and is still the default.

### Unsolicited calls are spoken, and actually reach you

`EngineerBrain.proactive()` existed but nothing called it: every unsolicited call was a fixed
template. It is now wired up, with the deterministic event payload as the only source of numbers
and the template as the fallback on any failure. Delivery was also failing — stored calls show
only 33% were ever spoken (rival stops 30 of 276, closing rivals 2 of 76, battery warnings 0 of 10),
because a strict queue made every non-critical call wait behind a routine progress update. Calls
are now prioritised, and only routine chatter waits the full interval.

### Cost

- `PITWALL_MODEL=gpt-5.6` resolved to the **flagship Sol tier** for every call. Deep strategy work
  stays on Sol; ordinary radio questions now use Luna, which is 25× cheaper per token and is
  narrating deterministic tool output either way.
- The persona and tool schemas are a stable prefix and are now sent with a prompt cache key.
- Fast and normal routes no longer ship the planning and setup-generation schemas.

### Storage

The database had reached **677 MB for 327 laps**. `StrategyEngine.recompute()` wrote a ~25 KB row
every time it ran, and the proactive loop calls it on a 0.35 s tick — 2,800 rows in one session,
515 MB in total. Snapshots are now written only on material change. Controller-button packets were
also persisted at packet frequency (40,434 rows), which additionally flooded the 200-entry events
log that "any race updates" reads, so real incidents were pushed out by button noise.

Maintenance runs at startup and at `POST /api/maintenance`. On the recorded database it reclaimed
**613 MB (677 MB → 63 MB)** with no lap, corner or radio rows lost; each track's reference lap keeps
its trace, because that is the live-delta and racing-line baseline.

## What changed in 3.6.1

3.6.1 repairs the hands-free wake path after the 3.6.0 architecture release.
Wake transcription is explicitly steered to the engineer name, both Mark and
Marc are accepted even with an older preserved `.env`, legacy saved disable
state is migrated safely, and the microphone trigger adapts to measured room
noise. The dashboard and `/api/health` now expose live input, noise and trigger
RMS values plus the last accepted/rejected transcript.

The wider 3.6 architecture continues to parse driver commands before the
language model, provide explicit session/strategy overrides, and answer
rival/sector/gap questions from matched telemetry rather than free-form model
inference.

- Boundary-aware intent routing prevents names such as Verstappen from matching
  ERS keywords.
- Recognised Practice, Qualifying and Time Trial packets are no longer relabelled
  as a Race by duration heuristics; a manual session override is available before
  live telemetry is complete.
- Driver tyre/stop choices can be locked, challenged with evidence, or returned
  to automatic ranking.
- Personal tyre regression uses track/air temperature, fuel, session profile and
  setup, while older laps inherit their stored session profile.
- Named-rival sector comparison and rolling gap trends answer where time is lost
  and whether the player is actually closing.
- Setup preferences are persistent and alter the generated baseline.
- PTT uses normal button-up, a 200 ms capture tail and a 2.2 second silence
  fallback so natural pauses do not cut off the statement.

## What changed in 3.5.1

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

## OpenAI-only engineer runtime

The live race-radio path registers only the OpenAI Responses provider:

```text
Exact telemetry / commands      -> deterministic local handlers
Normal interpretation           -> OpenAI, low reasoning
Strategy / setup / what-if      -> OpenAI, high reasoning
Speech-to-text and TTS          -> OpenAI audio models
```

Legacy DeepSeek fields are accepted only so older `.env` files and offline
diagnostic tests do not break; they cannot reactivate DeepSeek in production.
Every model call has a bounded deadline and tool-round limit, while common live
questions bypass the model entirely.

### Provider diagnostics

- `/api/health` reports `engineer_runtime: openai-only` and the configured model.
- `/api/llm/providers` shows the OpenAI circuit state and the model that answered
  the latest model-backed call.
- `POST /api/llm/shakedown` verifies text and tool-continuation contracts.
- Circuit breakers count provider-health failures; authentication and bad
  requests remain visible instead of silently switching providers.

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

Copy `.env.example` to `.env`, then set the OpenAI key:

```env
PITWALL_LLM_PROVIDER=openai
PITWALL_LLM_FALLBACK_PROVIDER=none
OPENAI_API_KEY=your_openai_api_key
PITWALL_MODEL=gpt-5.6-sol
PITWALL_FAST_MODEL=gpt-5.6-luna
PITWALL_REASONING_EFFORT=low
PITWALL_DEEP_REASONING_EFFORT=high
PITWALL_OPENAI_TIMEOUT_S=30
PITWALL_STT_MODEL=gpt-4o-mini-transcribe
PITWALL_TTS_MODEL=gpt-4o-mini-tts
PITWALL_VOICE=coral
```

The engineer runtime is OpenAI-only. Legacy DeepSeek variables may remain in an
older `.env`, but they are ignored. Common live questions such as gaps, rival
laps, tyre temperatures, damage and strategy status use deterministic telemetry
answers before any model call.

## Install or upgrade on Windows

Use 64-bit Python 3.11 or 3.12.

1. Stop every existing Pit Wall process.
2. Extract this ZIP into a fresh writable folder.
3. Copy only your existing `.env` into the new folder. `update_windows.ps1` migrates its engineer settings to OpenAI while preserving keys and unrelated preferences.
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

## Driver controls

The Live dashboard now exposes **Session mode** and **Strategy override** controls.
The Setup Lab stores a persistent five-axis driver profile for rear stability,
traction, rotation, tyre life and straight-line speed. The same controls are
available by radio:

```text
This is the race.
Start on mediums, box lap 12 for hards, one stop.
I am taking hards next lap.
Clear strategy override.
Prioritise rear stability and traction.
Clear session override.
```

A locked strategy remains a hard planning constraint unless it becomes illegal,
unsafe, impossible with the available tyre sets, or weather forces a crossover.
When challenged, the engineer reports the projected wear, evidence source and
confidence instead of simply insisting that the current call is correct.

## Health check

Open:

```text
http://127.0.0.1:8000/api/health
```

Expected provider fields:

```json
{
  "openai_key_configured": true,
  "engineer_runtime": "openai-only",
  "model": "gpt-5.6-sol",
  "llm": {
    "selected": "openai",
    "configured_provider": "openai",
    "resolved_provider": "openai",
    "fallback": "none",
    "providers": {
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
Simple telemetry question       -> deterministic local handler
Normal interpretation           -> OpenAI, low reasoning
Strategy / setup / what-if      -> OpenAI, high reasoning
OpenAI unavailable              -> explicit radio error; no silent model switch
```

## Tool safety

The model never receives direct filesystem, shell, network, or database-write access. It can only request the allow-listed telemetry tools defined in `TelemetryTools.schemas()`.

Before execution, Pit Wall:

1. Confirms the tool name is allow-listed.
2. Parses arguments as JSON.
3. Validates arguments against the local JSON schema.
4. Converts tool exceptions into bounded error results rather than crashing the radio pipeline.
5. Limits the number of tool rounds.


## Data and privacy

Runtime data remains under:

```text
%USERPROFILE%\PitWallData
```

Pit Wall sends compact situation summaries and selected tool results to OpenAI
only for questions that require model judgement. Exact live telemetry questions
and unsolicited proactive calls are handled locally and deterministically. It
does not upload the SQLite database, raw 60 Hz trace files, Windows username, or
full microphone recordings to the reasoning model. Audio clips are sent to
OpenAI transcription when cloud voice is enabled.

Never commit or share `.env`.

## Troubleshooting

### Engineer model fails

Check `/api/llm/providers` and `/api/health`. Verify `OPENAI_API_KEY`, API billing,
network access and `PITWALL_MODEL=gpt-5.6-sol`, then restart Pit Wall.

### Strategy call changes unexpectedly

Version 3.6.1 holds the current radio strategy through small simulation changes.
A new call requires a material projected gain, urgent tyre wear, a completed pit
stop/compound change, a weather crossover, race-control change, or the old call
becoming infeasible. The dashboard exposes the `strategy.stability` reason.

### Answers are too long

Say “keep your answers short.” The terse preference persists across laps and
sessions. Set `PITWALL_RADIO_VERBOSITY=terse` in `.env` to make it the default.

## Verification

Run the full dependency-backed suite after installation:

```powershell
.\.venv\Scripts\python.exe -m compileall -q .\src
.\.venv\Scripts\python.exe -m pytest -q
```

The 3.5.1 regression coverage includes reasoning-leak filtering, persistent
short-answer and temperature preferences, evidence-based cars-ahead trends,
driver lap-history responses without strategy contamination, directionally
correct differential/brake-bias advice, and strategy-call stability.

Live PS5 telemetry, microphone, speakers and provider credentials remain
hardware/account boundaries checked with the dashboard shakedown and a live
session.
