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
- Rival gap trends explicitly distinguish pulling away from being caught.

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
- Recorded lap and sector times are reconciled with the game's session
  history across the whole field, including laps missed during suspension.
  Missing or inconsistent telemetry remains excluded from setup learning.
- Populated Lap Lab and History fit phone screens, and partial recordings
  clearly show their limits when reviewing traces and single-lap analysis.
- Proactive settings apply at startup; delayed telemetry retains its arrival
  time instead of appearing newly received.

Windows continues to use the explicitly unsigned direct-download installer.
Android retains the existing signing identity. Existing platform limitations
remain documented in the installation guide.
