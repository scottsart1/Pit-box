# Pit Wall 3.1.0 — OpenAI F1 Race Engineer for PS5 and Windows

Pit Wall receives F1 25 / 2026 telemetry from a PS5, records the full driving session, answers L3 radio calls, makes unsolicited race-engineer updates, simulates tyre-and-stop strategy, compares the driver's world-coordinate line with a personal-best reference, and learns setup behavior across weekends.

Dashboard: `http://127.0.0.1:8000`

## 3.1.0 — hands-free “Mark” radio and qualifying timing mode

### Hands-free wake radio

Pit Wall now keeps one Windows microphone stream open and uses a local RMS voice-activity gate. Only bounded speech candidates are uploaded for transcription. A command is accepted only when the transcript starts with a configured wake phrase.

Both patterns work:

```text
Mark, what is the target lap?

Mark
[local beep]
Give me the top three best laps.
```

The default accepted prefixes are `Mark`, `Hey Mark`, `Mark radio`, and `Hey Marc`. Prefix-only matching reduces false activation from commentary that mentions the name later in a sentence. The engineer's own TTS temporarily pauses wake detection and applies a cooldown so it does not trigger itself. A 15-second cap prevents a stuck capture.

The existing controller path is preserved without recalibration:

```text
L3 -> F1 UDP Action 1 -> Pit Wall radio
```

L3 remains a fallback. Brake, throttle, steering, gears, DRS, MFD and every unrelated controller bit remain excluded from the PTT state machine.

Relevant `.env` controls:

```env
PITWALL_WAKE_ENABLED=true
PITWALL_WAKE_PHRASE=mark
PITWALL_WAKE_ALIASES=hey mark,mark radio,hey marc
PITWALL_WAKE_SPEECH_RMS=260
PITWALL_WAKE_SILENCE_S=0.90
PITWALL_WAKE_ARM_TIMEOUT_S=6
PITWALL_WAKE_TTS_COOLDOWN_S=1.50
```

Use a headset microphone. If game audio creates too many rejected candidates, increase `PITWALL_WAKE_SPEECH_RMS` in steps of 40–80. The dashboard shows accepted/rejected counts and the last rejection reason.

### Qualifying is now time-target driven

In qualifying, Pit Wall no longer volunteers race-style gaps ahead or behind. The timing tower is sorted by best lap and shows delta to the session best. Radio and proactive updates prioritize:

- session/pole best lap;
- the player's best lap;
- theoretical best from personal sectors;
- target lap and required improvement;
- one corner or racing-line opportunity.

Traffic gaps remain available only when explicitly requested. The new `get_qualifying_targets` tool returns the leading best laps and target context without race-gap fields.

### Upgrade

Stop Pit Wall, extract this build into a fresh folder, copy your existing `.env`, then run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_windows.ps1
.\start_pitwall.bat
```

Your database and `%USERPROFILE%\PitWallData\ptt.json` are reused. No L3 recalibration is required.


## 3.0.1 database hotfix

F1 telemetry identifies a session with an **unsigned 64-bit** `sessionUID`. SQLite's `INTEGER` type is a **signed 64-bit** value. Some real sessions therefore produced `OverflowError: Python int too large to convert to SQLite INTEGER` when saving radio, strategy, lap, setup, line, proactive or event history.

Version 3.0.1 stores the UID using a reversible signed two's-complement representation and restores the original unsigned value when history is read. No database reset or migration is required. Existing data remains compatible.

## What 3.0 fixes

### L3 / UDP Action 1 no longer breaks when another control is used

Your game mapping stays unchanged:

```text
L3 -> F1 UDP Action 1 -> Pit Wall radio
```

F1's `BUTN` events are not a reliable stream of the controller's complete held state. Earlier builds interpreted a later brake, throttle, gear or MFD event that omitted the UDP Action bit as a release. Version 3.0 instead **latches** the radio when the configured `0x...` bit is observed. Unrelated controller packets cannot close it.

The normal production flow is:

```text
L3/UDP Action pulse -> recording starts
other driving inputs -> ignored by PTT state machine
speech ends -> post-speech silence finalises the clip
15 seconds -> hard safety cap if silence is never found
```

The saved binding in `%USERPROFILE%\PitWallData\ptt.json` is reused and is not recalibrated or overwritten.

A practical limitation remains: if the game does not emit the **initial** UDP Action event at all for a particular simultaneous control combination, software on the laptop cannot observe a missing packet. The new latch fixes the much more common failure where the press arrived and a later unrelated input falsely ended it.

### Proactive engineer every two analysed laps

The cadence is threshold-based, not a fragile `lap % 2 == 0` check. If an analysis cycle misses the exact boundary, the overdue update is emitted on the next analysed lap rather than being lost.

A progress call can include:

- pace versus session/PB target;
- position and nearby gap trend;
- tyre wear, degradation and fuel state;
- current tyre-and-lap stop plan;
- one repeated corner or racing-line opportunity.

Calls queue while braking or in high lateral-G and are delivered on a safe section. A delivery deadline relaxes the requirement on tracks with no long straight, while still refusing heavy braking. Driver radio always pre-empts proactive speech. Critical SC/VSC/red-flag, weather, damage and penalty calls bypass the ordinary cadence.

The dashboard shows `last queued lap`, `next due lap`, queue age and delivery state so silence is diagnosable rather than mysterious. A **Test call** button queues an immediate progress update for live hardware acceptance testing; it still waits for a safe section before speaking.

### Strategy is now a risk model, not a fresh-tyre shortcut

Candidate plans cover zero, one, two and—on long/high-degradation races—three remaining stops. Each stint is evaluated with:

- four independent FL/FR/RL/RR wear rates;
- fuel-corrected personal degradation;
- current-stint evidence;
- prior personal evidence for that track and compound;
- fitted and available-set wear, life and transmitted lap delta;
- setup-induced front/rear wear and pace effects;
- tyre-age thermal growth, wear penalty and cliff behavior;
- green/VSC/SC/red-flag stop loss;
- pit-entry availability;
- projected rejoin and traffic cost;
- dry-race two-compound legality and wet waiver;
- Monte Carlo p25/p50/p75/p90 outcome ranges.

Plans are ranked by the configured risk quantile (`p75` by default), not only nominal time. Repeated stint simulations are cached, keeping a synthetic 70-lap/three-stop search well below one second on the development environment rather than progressively blocking the event loop.

Late Safety Cars are treated specially. When only a few laps remain, positions surrendered in the pits receive a large classification-risk penalty because the race may not restart. This prevents advice that looks fast in seconds but gives away an unrecoverable finishing position.

The strategy dashboard exposes the top plans, confidence, uncertainty, stop requirement, compound legality, effective pit loss, traffic/rejoin, per-wheel finish wear and the evidence source behind the call.

See `docs/2026_STRATEGY_BENCHMARKS.md` and `data/strategy_benchmarks_2026.json` for race-inspired regression cases.

### Personal racing-line map

Motion/world-position telemetry is recorded with speed, brake, throttle and steering. A valid personal-best trace becomes the reference. Each later lap is aligned by lap distance and assessed for:

- signed left/right deviation;
- mean, p95 and maximum deviation;
- persistent deviation zones;
- speed/brake/throttle difference in each zone;
- a bounded line-consistency score;
- the most actionable path opportunity.

The Live tab draws current and PB paths. Review stores the metrics by lap. This is a **personal reference line**, not an official ideal line supplied by the game; it improves as the driver's own PB improves.

### Complete pre-weekend Setup Lab

Setup Lab works with no live session. Select a track, then generate:

- **Race:** long-run stability, traction and tyre protection;
- **Quali:** response, rotation and peak one-lap performance;
- **Hybrid:** compromise between both.

The output contains the complete setup surface: wings, differentials, geometry, suspension, anti-roll bars, ride heights, brakes, engine braking, all four pressures and ballast. It begins from a circuit-archetype foundation, then conservatively blends stored personal runs, per-wheel wear, temperatures, lock-ups, wheelspin, line score and spoken handling feedback.

During a race, the separate pit-stop card only recommends a permitted front-wing adjustment. It does not pretend the complete setup can be changed in the pits.

### Durable, queryable history

SQLite is stored at:

```text
%USERPROFILE%\PitWallData\pitwall.sqlite3
```

Version 3.0 persists:

- sessions and final classification;
- lap summaries, sectors and full traces;
- per-corner metrics;
- racing-line metrics;
- radio transcript;
- proactive calls and whether they were delivered;
- strategy snapshots and alternatives;
- SC/VSC/red-flag/penalty/overtake/session events;
- setup recommendations, setup runs and handling feedback.

The Review tab reads the database, and the engineer tool `get_stored_history` can answer questions such as:

```text
Give me my last five valid laps.
What strategy did you recommend before the Safety Car?
What are my recurring line deviations at Spa?
How did this setup affect rear wear in earlier races?
```

### Long-race architecture

- UDP datagrams flow through one ordered bounded queue.
- No task is spawned for every packet.
- Hot dashboard snapshots exclude full traces and driver history.
- Full traces are persisted once and removed from hot memory.
- Repeated strategy stint calculations are cached.
- Session events use a separate bounded persistence queue.
- Queue depth and dropped-packet counts are visible in the dashboard/health state.

## Upgrade from a working installation

1. Stop Pit Wall.
2. Keep the old folder as a backup.
3. Extract this package to a new writable folder.
4. Copy only your existing `.env` into the new folder.
5. Open PowerShell in the new folder:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_windows.ps1
```

6. Start:

```powershell
.\start_pitwall.bat
```

The database and PTT binding live outside the project folder under `%USERPROFILE%\PitWallData`, so they are reused automatically.

For an in-place update, preserve `.env`, replace the project files, then run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\update_windows.ps1
```

## Recommended `.env`

Copy `.env.example`, put your own key after `OPENAI_API_KEY=`, and keep it out of Git.

For the stronger strategic-judgement profile:

```env
PITWALL_MODEL=gpt-5.6-terra
PITWALL_REASONING_EFFORT=low
PITWALL_DEEP_REASONING_EFFORT=high
PITWALL_OPENAI_TIMEOUT_S=45
```

Routine telemetry calls remain low-effort. Strategy, setup, degradation, comparisons and what-if questions are automatically sent with the deep reasoning effort.

The PTT lines should remain:

```env
PITWALL_PTT_RELEASE_MODE=silence
PITWALL_PTT_SILENCE_RELEASE_S=1.15
PITWALL_PTT_MAX_RECORDING_S=15
PITWALL_PTT_RELEASE_WATCHDOG_S=0
```

Do not switch back to explicit release for the current PS5 UDP Action behavior.

## PS5 and Windows setup

Use 64-bit Python 3.11 or 3.12. Then:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_windows.ps1
```

Run `check_windows_firewall.ps1` as Administrator. In F1:

- UDP telemetry: On
- Destination IP: laptop IPv4
- Port: `20777`
- UDP format: `2026`
- Send rate: `60 Hz` (or `30 Hz` on unreliable Wi-Fi)
- Your Telemetry: Public
- L3 mapped to UDP Action 1

Start with `start_pitwall.bat`.

## Acceptance checks

Open:

```text
http://127.0.0.1:8000/api/health
```

Confirm the model, key, PTT state and UDP listener. Then complete three clean practice laps and verify:

1. L3 begins recording; braking, throttle, gear and MFD inputs do not end it.
2. Silence after speech finalises the clip.
3. A proactive update appears at analysed lap 2, then lap 4 (or the next analysed lap if processing was late).
4. The racing-line map builds a PB reference and shows later deviation zones.
5. Review contains radio, laps, strategy snapshots and line metrics after restart.
6. Setup Lab can generate a complete setup after selecting a track with no PS5 session running.

## Verification for this package

The recorded verification run is in `docs/VERIFICATION.md`.

```text
40 automated tests passed
Ruff static checks passed
Python compilation passed
Dashboard JavaScript syntax passed
FastAPI startup/health smoke test passed
70-lap strategy performance smoke test passed
```

The automated suite covers controller-event latching, silence release, proactive cadence recovery, current 2026 enums, two-compound legality, per-wheel degradation, setup interaction, VSC/SC/red-flag behavior, late-SC track-position protection, multi-stop selection, racing-line deviation, persistent history and long-race snapshots.

## Honest limits

- A laptop cannot react to a controller event that the PS5 game never transmits. The latch prevents false releases after a received press; it cannot manufacture a missing initial UDP Action packet.
- The line reference is personal-best based, not an official optimal racing line.
- Public 2026 race reports provide useful regression mechanisms, but not team-private degradation curves or strategy simulations.
- Recommendations become materially stronger after several clean, comparable personal laps and repeated sessions with the same setup/track/compound.
