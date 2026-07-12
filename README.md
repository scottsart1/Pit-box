# Pit Wall 3.2.0 — OpenAI F1 race engineer for PS5 and Windows

Pit Wall receives F1 25 / 2026 Season Pack telemetry from a PS5 over UDP, keeps a persistent race and setup history, answers spoken questions, produces proactive engineer calls, and renders a live/review/setup dashboard at `http://127.0.0.1:8000`.

## What changed in 3.2

### Radio state rail

A thin rail runs down the left edge of the dashboard:

- **Yellow** — Pit Wall is recording/listening.
- **Green** — the clip was finalized and is being transcribed or processed.
- **Blue** — the engineer is speaking.
- **Red** — the voice pipeline failed.

The state is driven by the same server-side voice state used by L3 and hands-free “Mark”; it is not a decorative browser timer.

### Less perceived dead air

The voice path now does the highest-impact low-risk latency work:

1. End-of-speech silence defaults to `0.60 s` for wake radio.
2. A short cached **“Copy”** or **“Copy, stand by”** acknowledgement plays while the answer is calculated.
3. Native TTS uses 24 kHz raw PCM and begins playback as network chunks arrive instead of waiting for a complete audio file.
4. Common factual radio calls use a deterministic fast path instead of an unnecessary reasoning/tool loop.
5. Independent telemetry tool calls are executed concurrently.
6. Deep reasoning is reserved for genuine planning questions rather than ordinary uses of words such as “why”, “corner”, “best lap”, or “target”.
7. Deep calls have sufficient reasoning-token headroom while the spoken persona remains concise.

The dashboard records elapsed time from finalized audio to final transcript, answer completion, first audio, and total completion. This makes future tuning evidence-based.

### Voice robustness

- The saved `L3 -> UDP Action 1` binding is unchanged.
- Wake on/off is persisted in `%USERPROFILE%\PitWallData\ptt.json`.
- A new PTT clip arriving while another clip is processing is queued rather than silently discarded.
- Prompt-echo transcripts and repeated wake-word garbage are rejected.
- Cached acknowledgement generation is non-fatal; a local tone is used if the cache is not ready.
- Streaming TTS falls back to the previous full-file path if the audio device or SDK cannot stream cleanly.

### Additional race-operation calls

Pit Wall now surfaces and can proactively react to:

- A qualifying lap becoming invalid.
- A probable clear-air release window during qualifying, labelled as an estimate.
- A blue flag.
- Outstanding drive-through or stop-go penalties and whether the game marks a penalty to be served at a stop.
- A nearby car behind pitting and creating an undercut threat.
- 2026 sessions use **Manual Override** terminology instead of presenting the overtaking aid as DRS.

The foundational Setup Lab now contains its own canonical track fallback, so Spa/Monza/etc. still resolve even if the third-party enum import is temporarily unavailable.

## Important latency scope

3.2 streams **TTS**, but speech recognition is still the proven bounded-clip path:

```text
local VAD -> finalize clip -> upload WAV -> final transcript
```

True incremental Realtime transcription is deliberately not enabled in this maintenance release. It needs a persistent WebSocket session, partial-transcript reconciliation, reconnect handling, and representative headset/game-audio testing. The dashboard timings will show whether STT remains the dominant stage on your hardware. That migration can then be made without guessing.

Likewise, model output is not yet spoken sentence-by-sentence. Tool-aware response streaming is possible, but the current patch prioritizes reliability: deterministic fast answers plus streamed TTS remove most of the avoidable wait without destabilizing the strategy tool loop.

## Upgrade from 3.1

1. Stop Pit Wall.
2. Extract the 3.2 ZIP into a new writable folder.
3. Copy your existing `.env` into the new folder.
4. Do **not** copy or replace `%USERPROFILE%\PitWallData`; it contains your SQLite history and saved PTT/wake configuration.
5. Open PowerShell in the new folder:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_windows.ps1
```

6. Start:

```powershell
.\start_pitwall.bat
```

Your L3/UDP Action 1 mask is reused automatically.

## Recommended `.env` additions

Keep your existing API key and model settings. Add or update:

```env
PITWALL_VOICE_ACK_ENABLED=true
PITWALL_VOICE_STREAM_TTS=true
PITWALL_VOICE_CLIP_QUEUE_SIZE=2
PITWALL_WAKE_SILENCE_S=0.60
```

A complete template is in `.env.example`.

If the wake phrase clips natural pauses, try `0.70`. If it still feels slow and your speech is continuous, test `0.50`. Avoid changing several latency settings at once; use the dashboard timing card to compare runs.

## First shakedown

Run one practice or qualifying session and test:

```text
Mark, what is my target lap?
Mark, what are my last two laps?
Mark, give me a race update.
Mark, should I pit under this safety car?
```

Expected dashboard flow:

```text
yellow -> green -> blue -> idle
```

For a simple factual call, the timing card should identify it as `fast`. Strategy/setup/what-if calls should identify as `deep` and play “Copy, stand by” while working.

During qualifying, Pit Wall continues to prioritize field best laps, your best, theoretical best, required target, run preparation, and clear-air estimates rather than volunteering race gaps.

## Windows installation from scratch

Use 64-bit Python 3.11 or 3.12.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_windows.ps1
```

Then edit `.env`:

```env
OPENAI_API_KEY=your_actual_key
```

Run the Windows firewall helper as administrator if UDP 20777 is blocked, then:

```powershell
.\start_pitwall.bat
```

PS5 telemetry:

```text
Telemetry: On
Destination IP: Windows laptop IPv4 address
Port: 20777
UDP format: 2026
Send rate: 60 Hz
```

For controller radio, retain:

```text
Physical L3 -> F1 UDP Action 1 -> Pit Wall saved mask
```

## Data and privacy

Runtime data is stored in:

```text
%USERPROFILE%\PitWallData
```

This includes:

- `pitwall.sqlite3`
- `ptt.json`
- latest bounded driver/wake clips
- latest engineer audio
- cached acknowledgement WAVs

The `.env` file is excluded by `.gitignore`. Never commit or share it.

## Verification

The release was checked with:

```text
57 automated tests passed
Ruff static checks passed
Python compilation passed
Dashboard JavaScript syntax passed
FastAPI startup and /api/health passed
```

Tests cover the original strategy, setup, persistence, racing-line and UDP behavior plus:

- Fast/normal/deep request routing.
- Prompt-echo rejection.
- Wake-setting persistence.
- Radio-state rail presence.
- Correct Setup Lab fallback tracks.
- FIA flag and unserved-penalty parsing.
- Qualifying invalid-lap calls.
- Nearby rival undercut-threat calls.

See `docs/VERIFICATION.md` and `docs/LATENCY_ARCHITECTURE.md`.
