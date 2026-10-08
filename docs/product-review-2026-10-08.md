# Product review — 8 October 2026

Base: `main`, `6d0961f00069e2ecbdd50aab203833a3b0d23edd`, matching
`origin/main` at the start of review. The release candidate is 5.3.4 / Android
revision 36. Publication status and final artifact evidence are recorded below
only after verification. Initial tests used isolated synthetic data; subsequent
authorized checks use a copied tablet capture and configured AI providers.

## Confirmed defects and repairs

| Area | Reproduced behavior | Repaired expectation |
| --- | --- | --- |
| Strategy cancellation | Clearing either manual override form retained a cancelled lap 16 MEDIUM call instead of the automatic lap 25 SOFT call. | Automatic ranking takes over immediately, including inside the stability hold window. |
| Strategy history | Changes to later stops, later compounds, driver control, or timeline could share a persistence key. | Material plan changes are retained; unchanged numerical ticks remain deduplicated. |
| Android speech | Normal stop flushed queued speech; playback cancelled before its thread started could still speak. | Normal completion drains speech, while cancellation discards it immediately. Draining runs outside the application event loop. |
| Android assets | Reinstalling a rebuilt APK with the same version kept the previous extracted dashboard. | The extraction stamp includes installation update time. Interrupted extraction retries, and saved data remains untouched. |
| Library | Delayed filters, failed old requests and old pagination could overwrite or mix newer results. | Only the newest request updates results. Cursors belong to their filter and duplicate pagination requests are suppressed. |
| Text exports | Global replacement of `None` changed authored words such as `Nonetheless` and driver notes beginning with `None`. | Missing measurements are formatted at their source; authored text and JSON values are preserved. |
| Phone pace chart | The 55px pace strip stretched to 129px at a 390px viewport; its fixed bitmap squeezed text. | The strip retains its intended height and draws at the displayed size and screen density. |
| Race verification | The prior smoke harness passed after a 25-lap emitter exited even though observed state stopped at lap 18. | A demo pass requires the independent emitted final classification, drained receiver/archive work, every player lap, and a completed catalog after restart. Arbitrary captures are labeled observation-only. |
| Red-flag restart | Pausing could suppress advice; movement or an ambiguous race-control event could clear suspension; a fresh set of the same compound could be missed. | Confirmed suspension triggers free-change planning with primary and alternative choices, physical-set wear/availability, and any later stops. Advice survives menu gaps and ends on restart evidence. |
| Engineer terminology | Electrical deployment, Overtake Mode, Active Aero and legacy DRS were conflated in several outputs. | Current protocol meanings and game terms are distinct; legacy hardware is hidden from 2026 views. |
| Field freshness | An unrelated fresh packet could make old fuel, temperatures or energy flags sound current. | Each measurement family carries its own availability; paused and stale readings are identified and missing aid flags remain unknown. |
| Telemetry backlog | Parsing time could be recorded as packet arrival time. | Arrival timestamps survive the parser queue, so delayed observations remain stale. |
| Proactive startup | The state default could override disabled saved/environment settings. | Effective settings are applied before scheduling; disabling cancels queued calls and skips unnecessary planning. |
| Strategy narration | A real deep-model request exceeded its deadline and returned an HTTP error. | Repeated simulation arrays are removed from model tool payloads; a typed deadline can return a grounded, explicitly limited plan summary while retaining failure diagnostics. |
| Drive readings | Missing or disconnected car measurements appeared as plausible zeros or retained values. | Missing/stale families show unavailable values; genuine measured zeros and reverse gear remain meaningful. |

## Verification evidence

- Initial application suite: **1,972 passed**, one dependency deprecation warning.
- Initial distribution suite: **321 passed, two skipped**. Subsequent Android
  checks with Java available exercise the native helper tests.
- JavaScript: **98 passed**, including nine Library ordering/pagination cases.
- UDP and browser acceptance: expected **-0.300 s** same-session and **-0.600 s**
  cross-session comparisons; Setup references, groups, exclusions and exports;
  **72 workspace cases** at 1280, 800 and 390px, with no page exceptions or
  server errors.
- Driver decisions and critical warnings: passed at all three viewport sizes.
- Pace chart: six browser cases at 390/800px and normal/high pixel density,
  including visual inspection of empty and recorded states.
- Focused strategy: 139 passing tests. Export fidelity: reproduced before repair
  and passed through the text/JSON HTTP endpoints afterward.
- Android: native Java sources compile; audio cancellation, drain, asset refresh,
  interrupted extraction and data preservation have executable regression tests.
- Race-completion harness: ten focused regressions cover incomplete results,
  queued/in-flight work, timeouts, archive loss and capture-only scope.

Additional observations:

- A corrected paced six-lap race passed final-classification matching, every
  saved player lap, archive settlement, restart and database integrity checks.
- The initial refreshed debug APK passed signature/package checks, embedded
  source and static-file identity checks, and physical tablet installation.
- Physical Setup, all seven workspaces, all 21 Settings labels, comparison
  groups and exact text export passed. Same-session and cross-session deltas
  were **-0.300 s** and **-0.600 s**. One export hierarchy capture was killed;
  the recovered continuation passed, so the final build still requires a clean
  repeat of that flow.
- A private snapshot of the tablet's 160-session catalog was retained. A copied
  Singapore capture contains `RDFL`, a long telemetry gap, a MEDIUM tyre age
  change from three laps to zero, then `LGOT`. Tests reproduce that sequence.
- The complete copied history bundle passed transfer checksum verification,
  but staged import correctly refused insufficient desktop space. Selected
  raw capture and history rows were separately verified against manifest
  hashes for review. No complete history-import pass is claimed.
- Actual OpenAI readiness checks passed basic text, tool use and reasoning
  with tool use. These establish provider connectivity, not correctness of
  every engineer answer; command responses are inspected separately.

Final combined regression, final APK, live response and release evidence are
recorded below when complete. Local raw evidence is under
`.codex-ui-test-data/review-2026-10-08-*`; private driver data remains outside
the repository.

## Limits and environment

The original accelerated 25-lap result is **not accepted**: it ended at lap 18,
had an archive backlog and no final classification. Disk protection also stopped
raw capture before any packets were written. Those observations establish a
verification gap and overload during that run, not a diagnosed parser defect.

Package-cache cleanup recovered approximately 2 GB after C: filled up. Automatic
approval review blocked deletion of an additional unused browser cache; that
cache was left intact.

The fresh APK uses exact pinned Android native dependencies recovered from the
previously verified 5.3.2 APK because local wheel inputs were absent. Recovery
checks both ABIs and retains file hashes in ignored local provenance. Product
source is built from the current workspace.

The tablet became available later and physical checks proceeded. Browser checks
and Java compilation alone do not establish physical microphone capture,
headset routing or full-race endurance. Accelerated historical replay during
parallel tests and low memory lost packets before observed ingestion; that run
is not accepted as a complete replay. Controlled paced checks are separate.
