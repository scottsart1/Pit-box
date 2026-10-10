# Your Pit Box 5.4.1

5.4.1 is the first public release with Session Analysis and Lap Lab's driver
and lap pickers; their features are described in the
[5.4.0 notes](release-notes-5.4.0.md) (5.4.0 itself was only installed on a
test tablet). This release makes the race order trustworthy on real
recordings, old and new, and records more of every race.

## Race results

- **The game's own result.** Races recorded from 5.4.1 save the game's final
  classification for every car, and Session Analysis uses it as the finishing
  order: positions, laps, DNF, disqualified and not classified, and gaps with
  penalties applied. A race the game classified counts as finished even if the
  recording was not closed properly.
- **Older recordings.** Versions before 5.3.4 lost a field car's lap row
  whenever its telemetry batch failed, often for most of the field at once.
  Session Analysis used to rank the few cars with complete records among
  themselves, which could show you P1 in a race you finished P20, or name a
  car that stopped on lap 2 as the winner. Now, when lap times cannot order the
  field, the order comes from the game's own race position at the end of each
  lap, recorded with the lap. A finish is placed only when the evidence is
  settled: a car's position on the final lap, or its position a lap earlier
  when no other car holds it at the finish and the car behind is still
  behind. Otherwise the result reads unknown, with the reason.
- Retirements come from the game's retirement and penalty messages, or from
  the game moving a car to the back on an unfinished lap. A record that simply
  ends is not called a retirement.
- A recording that stops before the race distance shows positions per lap and
  claims no finish.
- A lap nobody timed right after a red flag counts as part of the stoppage, so
  the restart is still timed.
- With lap times only, a car keeps its place only when no car with a gap in
  its record could be ahead of it.
- The player is the car with its own lap rows; older recordings could flag a
  second car as you.
- The headline names where the order comes from, and the results show the
  game's DNF, disqualified and not-classified statuses.

On the test tablet's 59 races this places 522 cars where 5.4.0 placed 214, and
no position contradicts an official result. Replays of a real Singapore race
reproduce the game's classification for every car, with gaps within 20 ms.

## Recording

- **A retirement no longer stops the recording of other cars.** The game
  lowers its active-car count when a car retires, but the other cars keep
  their numbers; the highest-numbered cars stopped being recorded, losing their
  traces and pit and flag context for the rest of the race.
- The game's lap chart (the grid and every car's position at each lap) is
  saved with the session.

Windows remains an explicitly unsigned direct-download installer, and Android
keeps the existing signing identity.
