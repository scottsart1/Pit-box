# Your Pit Box 5.3.4

## Race engineer and strategy

- Red flags trigger restart tyre planning with a preferred choice and an
  alternative, including available sets, wear and any later stops.
- Suspension advice remains available through menu pauses and telemetry gaps.
- Updated Overtake Mode and Active Aero language; legacy DRS remains separate.
- Fuel, tyres, temperatures and energy reports check their own telemetry
  freshness and distinguish missing values from measured zeros.
- Clearing a driver strategy override immediately restores automatic planning.
- Strategy narration uses smaller tool responses and has a grounded fallback
  when model narration exceeds its deadline.
- Strategy answers retain their uncertainty and refresh when the primary or
  alternative plan changes while the engineer is responding.
- Complete plans state the recorded condition that would change the call;
  adding an equivalent action label no longer replaces a current AI answer.
- Rival gap trends explicitly distinguish pulling away from being caught.
- Session-best answers use the complete available session history, with
  coverage and recent-window limits stated explicitly.
- Confirmed final results remain available to the engineer after live
  telemetry stops, and the dashboard clearly marks the completed session.
- Drive, Strategy and current pit advice stop offering racing instructions
  after the game's final classification is received.

## Dashboard and reliability

- Clearer car-data availability and corrected 2026 dashboard hardware labels.
- Stable Library filtering and pagination, and faithful engineering text exports.
- A/B sector-delta bars and lap-time distributions show accepted samples,
  exclusions, consistency and the limits of cross-session comparisons.
- Sharper, correctly sized pace charts on phones and tablets.
- Android speech drains normally and stops promptly when interrupted.
- Rebuilt Android dashboards refresh reliably without replacing saved data.
- Android walkthrough completion survives dashboard port changes.
- Provisional restart choices remain visible during confirmed suspension
  telemetry gaps, with the last confirmed status clearly labelled.
- The compact overlay distinguishes live, unavailable, suspended and completed
  sessions, retaining provisional restart choices without stale car readings.
- Unrelated fresh packets cannot revive old position, lap, rival or race-control
  readings, or turn a provisional suspension plan into a live instruction.
- The strategy decision log shows estimated finishes and complete multi-stop
  schedules so changes behind repeated-looking calls are visible.
- Recorded lap and sector times are reconciled with the game's session
  history across the whole field, including laps missed during suspension.
  Missing or inconsistent telemetry remains excluded from setup learning.
- Repeated history packets are coalesced to keep the archive queue responsive;
  timing corrections and failed writes remain eligible for processing.
- Flashback recovery revalidates timing-only laps against each car's replacement
  history and prevents abandoned timeline writes from returning after restart.
- A deleted session can be recorded again without inheriting its former
  flashback state.
- Deleting one restart preserves history shared by another; the confirmation
  explains retained records, and the last owner's deletion cleans them up.
- Archive diagnostics distinguish persisted laps from discarded old-branch
  work instead of counting skipped writes as saved laps.
- Populated Lap Lab and History fit phone screens, and partial recordings
  clearly show their limits when reviewing traces and single-lap analysis.
- Proactive settings apply at startup; delayed telemetry retains its arrival
  time instead of appearing newly received.

Windows continues to use the explicitly unsigned direct-download installer.
Android retains the existing signing identity. Existing platform limitations
remain documented in the installation guide.
