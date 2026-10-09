# Your Pit Box 5.4.0

## Session Analysis

- **Analyze session** in Library and Session Review opens a new Session
  Analysis view for any saved session.
- Race pace: a lap-time box plot per driver, fastest median first, built from
  pace laps only. Lap 1, restart laps, pit in/out laps, safety-car, VSC, flag
  and red-flag laps, laps excluded in lap notes, laps recorded without
  telemetry context and laps without a time are left out, and laps over 107%
  of the driver's median are hidden unless you turn that off. Each box shows
  how many laps it holds; drivers with too few pace laps are listed rather
  than boxed.
- Race trace: the gap per lap to the race leader or to any classified driver,
  read as seconds behind or ahead. Safety-car and VSC laps come from the
  game's race-control messages and red-flag laps from the field's suspended
  laps; if race control was not recorded, the view says so.
- Positions at the end of every lap, with each driver's finishing place: a
  lapped finisher keeps its place with its laps down, and only a retirement
  is shown as out.
- Tyre strategy: every stint per driver, in finishing order in a race and most
  laps first otherwise, lettered S, M, H, I or W (or the game's C1-C6 label).
  Laps without recorded tyre data are shown as unknown instead of borrowing a
  compound, and a tyre change made while the race is suspended starts a stint.
- Lap times for the drivers in focus, and a lap-time heatmap of every driver
  and lap against that driver's own median. Select a lap to read it, then
  open it in Lap Lab; laps known only from timing say so instead, and a lap
  missing from the recording is marked as not recorded.
- Race timelapse: play, pause or scrub through the running order and gaps lap
  by lap. While a race is suspended the order is shown frozen; the finish
  shows every classified car, lapped cars with their laps down. A recording
  that stopped early ends on its last recorded lap, not a finish, with every
  placed car still in the order. With reduced motion it steps a lap at a time.
- Fastest and ideal laps (best sectors combined), and pit stops with an
  estimated time lost for green-flag stops. A stop count that cannot be known
  is shown as unknown, never 0, and a tyre change without a recorded stop is
  listed as a stop not recorded.
- Race order and gaps come from lap times summed between suspensions: a red
  flag stops the game's lap timer and the restart starts from the grid, so
  each racing segment is timed from its own start. The finishing order is
  derived from laps completed and race time; penalties are not applied. Your
  official result is shown when the game reported one, with a note when the
  derived order differs; then the car first on lap times is not called the
  winner. A session that did not finish recording, or with only some cars
  recorded, is labelled provisional or among the recorded cars instead of
  naming a winner, and its fastest lap is the fastest recorded lap. With only
  one car recorded there is no race order, so the race trace, positions and
  timelapse give way to a note.
- Every chart has a data table, including one with an Open in Lap Lab button
  for every lap recorded with telemetry. Each chart is one Tab stop: arrow
  keys move between its marks and Enter opens a lap. Readings appear on hover,
  tap or keyboard focus. Charts are drawn at the width of the screen, so their
  text stays readable on a phone, and a wide heatmap scrolls sideways on its
  own.

## Lap Lab

- Choose a driver, then a lap, for the lap you are studying. A driver opens on
  their fastest lap with telemetry.
- The best comparable reference is chosen and compared as soon as the lap has
  loaded. Choose any other driver and lap - including any lap of the same
  session, or a compatible lap from another session at the track - and it is
  compared once your choice settles; Use suggested goes back to the
  recommendation. A comparison against another session names that session.
- Laps from the same session that were not pre-classified are checked by the
  comparison itself, and any caveat or refusal is shown with the result while
  the lap keeps playing.
- Laps a flashback abandoned are left out; laps known only from lap timing
  are labelled and cannot be compared.

Windows continues to use the explicitly unsigned direct-download installer.
Android retains the existing signing identity.
