# Your Pit Box 5.1.0 — setup scope and race radio

Release candidate; production publication is pending validation.

Setup Lab offers Minimum, Moderate and Radical changes. Minimum keeps the
current setup and caps the final per-setting adjustments. Moderate starts
halfway between the current setup and the circuit foundation. Radical starts
from the circuit foundation. All three then consider personal handling,
corner evidence and stored runs. Fuel load is preserved as a separate race
decision. Recommendations for another selected circuit exclude live setup and
handling from the wrong track.

The pace review reports recent clean laps and, where available, a same-compound
field median using matching lap numbers from the rivals' current stint. It
states that fuel, traffic and tyre age can differ and does not attribute every
pace deficit to setup. The broader garage recommendations are separated from
permitted front-wing changes during a pit stop.

The engineer's direct-answer route now requires a complete supported request.
Questions about future field stops, qualifications and comparisons reach the
reasoning model, which can combine existing validated tools. Rival predictions
can include all 23 opponents, distinguish observed completed stops from uncertain
future stops, and report missing or unsuitable evidence. No arbitrary generated
code is executed.

Frequent proactive checks use bounded snapshots without race histories or
trace curves. The UDP consumer yields on a time budget as well as a packet
count. Corner and racing-line calculations run outside the event loop. Health
polls copy only the fields they return. Scheduling delay and evaluation duration
are available in proactive diagnostics.

The connection watchdog also avoids graph copies. The live graph carries only
the plotted channels, with a corrected sample bound; full recordings retain
the motion and slip data. Repeated identity-only observations avoid rebuilding
participant identities. The assembler shares unchanged, privately owned sample
metadata and publishes read-only finalized rows without copying each row again.

Android now retains the last 20 network availability/loss events and reports
thermal status. Code inspection found no Bluetooth toggles, Wi-Fi disable calls
or process-wide network rebinding. This does not establish the cause of the
reported physical radio disconnections. No physical tablet is currently connected
to ADB; a successful emulator or localhost test cannot prove those drops fixed.

The proposed dashboard-agent work is documented separately in
[engineer-dashboard-agent-proposal.md](engineer-dashboard-agent-proposal.md).
It is not implemented in this release.

## Validation in progress

- Initial local full run: 1,928 passed, two failed, two skipped. Both failures
  were supported radio requests affected by stricter routing. The corrected
  focused run passed all 30 tests.
- Setup UI exercised at 1280 × 800 and 800 × 1280 in an isolated source app.
- A synthetic 24-car, 60-lap snapshot benchmark measured 35.87 ms for the
  analysis snapshot versus 2.15 ms for the compact radio snapshot on this PC.
  This is a microbenchmark, not a tablet race or end-to-end voice measurement.
- Initial counted 60 Hz source tests failed: one final health timeout and one
  receive-count deficit (12,703 of 18,604). Profiling led to the identity,
  graph, watchdog and retained-metadata improvements above. Intermediate runs
  improved reception but still failed; their failures were not waived.
- Final local counted source run: **18,604 sent, received, parsed and written**
  in a 100-second, 20-car replay (about 186 datagrams/s). No queue drops, write
  errors or request timeouts. Health p95 0.421 s / max 2.984 s; state p95 0.469 s
  / max 1.328 s. All 19 rival laps persisted; shutdown and database integrity
  passed. This bounded test is not a full-race or physical-tablet endurance test.
- A separate parser/proactive integration test delivered a penalty call to a
  recording voice sink while 60 Hz frames continued. It exercised detection,
  queuing and delivery with retained rival history; it did not test a speaker.
- Final live stored-key model probe used both field-stop tools and correctly
  reported 22 likely / one unknown opponent, answered the cars-ahead question,
  and declined to invent a rival's radio message. Times were 9.86, 4.94 and
  4.52 seconds. Earlier probes hit route deadlines; this does not guarantee a
  response under all network/model conditions.
- Additional focused runs passed 135 tests (one platform skip), 44 networking
  and identity tests, and 28 assembler/setup/radio tests.

No personal library was used for synthetic telemetry. No credentials or private
recordings are included in this document.
