# Your Pit Box 5.5.1

5.5.1 fixes the Engineering page's practice review, which could list a lap
twice and cut a stint into one-lap runs.

## Practice review

- **Each lap once.** When the game re-sends its lap history under a new
  identity for your car, the recording keeps a timing-only copy of every
  earlier lap beside the lap it observed. The Engineering page showed both:
  one with air and track temperatures, fuel and setup, one with `?` for all
  of them, both with the lap time. It now shows the observed lap only. A lap
  that telemetry genuinely missed still appears once, with `?`, and stays
  excluded from pace as missing telemetry.
- **Stints stay whole.** Those copies had no compound or setup, so run
  detection took each one as a change of car and a six-lap stint became
  twelve one-lap runs, every one "too few clean laps to judge". Runs and the
  suggested stints now follow the observed laps, and a lap known only from
  the game's history stays inside the stint it was driven in. A real tyre
  change on the next lap still starts a new run.
- Sessions recorded by earlier versions show correctly after updating; no
  re-recording is needed.

Windows remains an explicitly unsigned direct-download installer, and Android
keeps the existing signing identity.
