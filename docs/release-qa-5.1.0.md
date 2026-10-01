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
  receive-count deficit (12,703 of 18,604). These failures remain recorded and
  are release blockers until investigated and retested.

No personal library was used for synthetic telemetry. No credentials or private
recordings are included in this document.
