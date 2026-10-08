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
| Historical race | Every packet from a complete, checksum-verified tablet capture is emitted with recorded timing. Count received versus sent, retain failures/timeouts, require an independently decoded final result, drain archive work, inspect all player laps and restart persistence. |
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
