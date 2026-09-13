# Strategy weather and learning validation

Synthetic causal cases were written and executed against commit `c3229ed`
before changing the implementation. No existing user database was read or
modified. These checks validate data handling and decision constraints; they
do not calibrate the underlying weather/tyre physics against real races.

## Observed baseline defects and corrected behavior

| Observation | Before | After |
| --- | --- | --- |
| A recorded VSC/pit/red-flag lap takes 110 s against a 90 s dry baseline | Ratio 1.2222 counted as weather evidence despite the recorded interruption | Interrupted lap excluded; remaining clean evidence retains ratio 1.0 |
| Eight rivals slow during VSC, or the latest player lap records the interruption | Eight field weather observations | Field pace withheld with an explicit exclusion reason |
| Player fits INTER after completing a MEDIUM lap | Completed-lap evidence retrospectively becomes INTER | Completed lap retains its recorded MEDIUM compound |
| Heavy rain arrives at 3:54 on a 120 s circuit | Second-lap wetness 0.324, equivalent to a whole lap of rain | 0.0212, reflecting only six seconds of soaking |
| Rain arrives exactly at the finish | Final-lap wetness 0.324 | No rain affects the completed race |
| A five-minute forecast was captured three session minutes ago | Four-minute projection remains dry because countdown restarts | Rain begins after the remaining two minutes |
| Race forecast includes qualifying storm at the same offset | Both samples become current-session weather | Explicitly different session types are excluded |
| Weather adds 0.50 s/lap while true tyre degradation is 0.15 s/lap | Intrinsic degradation reported as 0.65 s/lap with ten samples | Wet pace fit unavailable with ten explicit exclusions; measured wear remains 2.5 percentage points/lap |
| Explicit inventory offers MEDIUM and WET only | Unavailable INTER inserted into weather options | Choices respect the supplied inventory |

The initial 14-case run produced 13 failures and one pass. Separate packet and
inventory probes then reproduced two further failures before those fixes.
After implementation, the combined weather, tyre-learning, analysis, UDP and
archive suite passes **133 tests**, including 17 new scenarios. The synthetic
wet-wear history was saved and reopened to verify that pace exclusions remain
visible while valid wear learning survives persistence.

Two existing probability tests required corrected interval indexes: a forecast
at three minutes cannot wet the 90-second lap ending at exactly three minutes.
The nonlinear scenario-cost equality assertion remains, with the comparison
stint placed one lap later to retain its original settled-weather coverage.

## Compatibility and evidence boundaries

- Forecast age uses the game session clock, paired with its captured origin.
  Missing legacy clock metadata retains the relative-offset fallback. Forecast
  probability still weights complete rain/no-rain scenarios; it never scales
  the category's physical intensity.
- EA's rival history has no historical SC/VSC flag per lap. The conservative
  gate covers current neutralisation and the first player lap after a recorded
  interruption. It cannot reconstruct unobserved older interruptions or infer
  whether apparently independent rivals were actually in one traffic train.
- Wet lap time alone cannot separate intrinsic degradation from wetting or
  drying. Wet pace fits are therefore unavailable until independent surface
  evidence exists. Measured wear remains usable. Missing legacy sky metadata
  retains its existing learning behavior.
- Existing wetness rates, tyre penalty curves, forecast temporal correlation
  assumption and lap-level cost approximation remain heuristics. Passing these
  tests does not establish real-world probability calibration or race-optimal
  behavior under every unknown condition.
- Historical output now includes `pace_excluded_laps` and
  `pace_learning_policy`; live pace fits retain the same provenance.

The official packet source confirms that forecast samples carry a session type
and relative minute offset and that packet headers carry session time:
[EA F1 2026 telemetry structures](https://forums.ea.com/t5/s/tghpe58374/attachments/tghpe58374/f1-games-game-info-hub-en/61/8/2026%20Season%20Pack%20Telemetry%20Output%20Structures%20%281%29.txt).
