# Release validation for 5.3.4

This is the acceptance plan, not a list of claimed passes. Results must identify
the exact commit and artifact. A failed observation remains part of the record
when a repair or a corrected harness is rerun.

## Acceptance gates

| Area | Required evidence |
| --- | --- |
| Application regression | Entire Python application and distribution suite on the candidate commit; no unexplained failures or omissions. |
| Browser behavior | Entire JavaScript suite; real browser workspace, layout, scrolling, setup, engineering, chart and stale-state checks at phone, tablet and desktop sizes. |
| Windows artifact | The exact installer passes installation, frozen startup, UDP ingestion, completed-race persistence, restart, integrity and uninstall/data-retention tests on a disposable runner. Counted reception and responsiveness pass separately. |
| Android artifact | Existing signing identity, correct application ID/version/ABIs, embedded source and asset identity, native screen/input/export checks and data preservation. |
| Historical race | Every packet from a complete, checksum-verified tablet capture is emitted with recorded timing. Count received versus sent, retain failures/timeouts, require an independently decoded final result, drain archive work, match every completed lap and sector time across the whole field against original session-history packets, then verify restart persistence. |
| Race control | Actual recorded red flag, suspension telemetry gap, same-compound fresh set and lights-out restart are inspected. Ordinary SC/VSC must not imply free suspension tyre changes. |
| Engineer correctness | Execute the command matrix through the real API. Retain requests, before/after state, provider results and mutations. Verify each numerical or causal claim; a deterministic fallback is identified separately from successful AI generation. |
| Missing/stale data | Withhold individual packet families while other groups arrive, pause, disconnect and switch session identity. Old values must not become new facts or instructions. |
| Driver commands | Commit, refuse, negate cancellation, clear and agree to plans; inspect resulting state and saved records. An acknowledgement alone is insufficient. |
| A/B comparison | Accepted cohorts, excluded laps, missing sectors, identical times, cross-session limits and signed deltas agree between charts, numbers and exported reports. |
| Production | Public downloads match tested hashes and byte lengths; published website and update metadata identify the same version and artifacts. Verify the installed tablet upgrade preserves existing history and configuration. |

## Independent historical expectation

The selected full tablet recording contains 477,184 datagrams over 3,980.032
seconds. Its three final-classification packets agree: the player completes
31 laps, finishes P1 from grid P3, records a 91,649 ms best lap and a 3,466.302 s
race time, with two pit stops, 25 points and no time penalty. These are decoded
directly from the original capture before the replay; they are not inferred
from the application's output. Private raw data and driver identifiers are
retained outside the repository.

The final session-history packets also contain 624 completed laps across 22
cars: two cars complete two laps each, and the other 20 complete 31. Every lap
has all three sector times. The final gate compares canonical stored lap times,
sector times and validity for this complete set, including the player's legacy
history export. Missing telemetry must remain excluded from telemetry learning
even when the game supplies an authoritative completed lap time.

This expectation is for a complete playback gate. The earlier 49-second
historical segment is useful targeted evidence and does not satisfy it.

## Known failed observations to rerun

- Accelerated desktop replay during parallel tests and memory pressure lost
  datagrams before observed ingestion; it is not a full-race pass.
- One full-strategy provider call exceeded the reasoning deadline; verify both
  successful narration and the explicitly identified grounded fallback.
- Defence narration attributed a default score to the driver's performance
  without supporting driving samples; verify missing evidence remains unknown.
- An export accessibility capture was killed, and a later harness assumed a
  collapsed control which was already open. Preserve those failures and require
  an uninterrupted native export with byte-for-byte comparison on the candidate.
- Local signed Android packaging exhausted desktop space before an artifact
  was created. A later artifact must have an independently successful build,
  signing and runtime record.
- A mixed weather question committed a pit stop; the repaired command parser
  must preserve an existing override when answering a question.
- Empty forecasts became a numeric zero in a session summary, and a new 2026
  session without packet 16 used legacy DRS terminology. Test missing forecast
  horizons and never-received aid flags independently of socket connectivity.
- The complete historical replay exposed a missing player lap after the
  red-flag gap and the following lap's time assigned to the preceding trace.
  Sector-only history updates could leave a contradictory saved row. Require
  authoritative timing reconciliation and a complete replay on the repair.
- Actual narration inverted a positive gap change to the car behind and
  omitted strategy uncertainty. A separate response retained an old stop lap
  after the recommendation changed during model generation. Verify direction,
  confidence, primary and alternative plans against the returned evidence.
- Populated Lap Lab compatibility text overflowed a phone viewport. Test real
  recorded options and long reasons in addition to empty-page navigation.
- Restart choices disappeared from the dashboard during a confirmed red-flag
  telemetry gap. Retain explicitly provisional same-session choices, without
  displaying old car readings as live or carrying choices into a new session.
- A late-race answer labelled the fastest lap in the recent returned window
  as the session best. Compare against the complete independently decoded
  history, including a best lap outside the requested window.
- A confirmed final result was refused as stale and the dashboard returned to
  its waiting screen. Verify the authoritative result survives the end of live
  telemetry and is cleared on a new session.
- Drive, Strategy and actual answers to current pit requests retained live
  stay-out instructions after the confirmed finish. Verify completed-session
  wording, cleared action controls and terminal tool responses together.
- The intermediate full tablet replay lost wireless transport packets and its
  ADB forwarding connection. Preserve the failed reception and monitor record;
  a repaired-source run must satisfy the complete counted replay gate.
- Capture validation originally consumed the replay clock before the first
  packet, causing a catch-up burst. Both the release replay harness and the
  standalone capture command start their clocks after initialization.

## Intermediate regression evidence

The Windows workflow for `ce562b9daa3a705dcc5d8bf19471b2e2ac56e435`
passed 2,392 Python tests with one skip. Its exact installer passed installation,
frozen startup, a completed 25-lap synthetic race, persistence, restart, SQLite
integrity and uninstall/data retention. The separate 100.11-second reception
gate sent, received and parsed 18,604 datagrams, with zero rejections, archive or
capture drops, errors or timeouts. Queues drained and 19 field laps persisted.
Health latency p95 was 0.203 s and state latency p95 was 0.250 s.

These results belong to that intermediate commit. They do not establish the
later fixes or replace the full historical tablet race. Both final artifacts
must be rebuilt and validated after the remaining corrections.
