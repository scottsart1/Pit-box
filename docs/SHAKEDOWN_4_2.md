# Pit Wall 4.2 hardware shakedown checklist

Everything in this file needs a real PS5, a real network, real audio devices,
or several hours of wall-clock running. None of it can be closed by the
automated suite, which is why it is a checklist rather than a test.

Automated coverage that already passed is listed at the end so you know what
you are *not* re-testing here.

Record the result of each numbered step. A step that fails should capture the
exact screen text or log line, not a paraphrase.

---

## Before you start

- [ ] Back up `%USERPROFILE%\PitWallData\pitwall.sqlite3` yourself. The upgrade
      makes its own verified backup under `PitWallData\backups`, but an
      independent copy costs nothing.
- [ ] Note the app version shown in the browser tab and the bottom status
      strip. Both should read 4.2.0.
- [ ] Start with the dashboard on loopback only and the UDP listener off.

## 1. Connection Center against the real console

1. [ ] Open **Connection**. Confirm the large address is your actual LAN IPv4
       and the adapter line names your real adapter with **high confidence**.
       It must not be `127.0.0.1`, `0.0.0.0`, or a `169.254.x.x` address.
2. [ ] Confirm the adapter list shows every adapter you expect, each with a
       kind (wifi/ethernet), gateway state and route metric. A single entry
       named "Detected IPv4 interface" means platform discovery failed - report
       it with the warning text shown on screen.
3. [ ] Enter that IP, port `20777`, and packet format `2026` on the PS5.
4. [ ] Before starting a session, confirm the badge reads **Listening**, in
       amber. It must not claim Receiving with no console traffic.
5. [ ] Start an on-track session. Confirm the badge flips to **Receiving**.
6. [ ] Check the packet matrix: every expected packet type present, observed Hz
       near expected, no sustained confirmed loss. Brief provisional gaps are
       normal; sustained confirmed loss is not.
7. [ ] Pause the game or exit to a menu. Confirm the state degrades to
       **Stale** rather than silently holding the last value.

## 2. Forwarding

8. [ ] Start any second UDP listener on `127.0.0.1:20778` (another telemetry
       app, or a throwaway socket).
9. [ ] Add it as a forwarding target and enable it. Confirm the counters climb
       and the second app receives telemetry.
10. [ ] Confirm Pit Wall's own live view is unaffected while forwarding.
11. [ ] Stop the second app with forwarding still enabled. Confirm only that
       target reports errors and local ingestion stays healthy.
12. [ ] Try to add a target pointing at Pit Wall's own listener. Confirm it is
       refused with a readable reason.

## 3. A real session, driven deliberately

13. [ ] Run a practice session with at least **three valid clean laps**.
14. [ ] Include one lap where you deliberately **brake much too early** into a
        specific corner. Write down which corner.
15. [ ] Include one clearly improved lap.
16. [ ] During the session confirm the existing engineer still behaves: live
        delta, fuel, ERS, tyres, flags, strategy and proactive radio.
17. [ ] Confirm wake word and push-to-talk both still work, and that voice
        latency feels no worse than 3.8.
18. [ ] Select several rival cars. Confirm identities, positions, laps, pit
        status and available telemetry update.
19. [ ] If the session type allows it, trigger a **flashback**. Confirm the lap
        is not silently joined across the rewind.
20. [ ] End the session cleanly.

## 4. Offline review of that session

21. [ ] Fully close Pit Wall. Turn the console off. Restart Pit Wall.
22. [ ] Open **Library**. Confirm the session you just drove is listed with
        correct track, type, duration and lap count.
23. [ ] Open **Session Review**. Run **Analyze / reprocess** and wait for it.
24. [ ] Open **Lap Lab**. Compare your deliberately-early-braking lap against
        your best clean lap.
25. [ ] Confirm the segments covering that corner now produce **real deltas**,
        not Unavailable. This is the key check that 4.2-native capture is dense
        enough for segment analysis - see the legacy-session note in
        `PIT_WALL_4_2.md`, which explains why pre-4.2 laps cannot do this.
26. [ ] Confirm a finding identifies the braking problem in roughly the corner
        you chose, with evidence and confidence.
27. [ ] Scrub and play. Confirm the map cars, cursor, gauges, traces and cards
        stay synchronized.
28. [ ] Switch the reference to a rival car. Confirm inputs the game does not
        supply for that rival show **Unavailable**, and that no coaching claims
        to know their braking.
29. [ ] Open **Field**. Check pace, corners, positions and stints, then drill a
        matrix cell through to Lap Lab.

## 5. The engineer explaining the analysis

30. [ ] Ask: *"Where did I lose the most time and why?"*
31. [ ] Confirm the spoken numbers **match the Lap Lab finding exactly**.
32. [ ] Confirm the answer **names what it compared against** ("against your
        session best"), rather than giving a bare delta.
33. [ ] Ask the same about a rival whose inputs are unavailable. Confirm it
        states the limitation instead of inventing technique advice.

## 6. Endurance and failure paths

34. [ ] Run a **two-hour** session (or a two-hour replay of a capture) with a
        forwarder and a browser open. Watch memory: it should not grow in
        proportion to elapsed samples after laps finalize.
35. [ ] Confirm no confirmed packet loss attributable to local processing.
36. [ ] Let disk get low, or point the data root at a nearly-full volume.
        Confirm the warning appears and the database stays intact.
37. [ ] Kill Pit Wall abruptly mid-session. Restart. Confirm the capture is
        recovered and labelled incomplete rather than lost or silently
        presented as clean.

## 7. Privacy before sharing anything

38. [ ] Export a diagnostic bundle. Confirm it contains no API key, no `.env`
        contents and no unexpected participant names.
39. [ ] If you plan to share a capture, run the anonymize command and read the
        `PIT_WALL_4_2.md` note: it redacts transport metadata only and does
        **not** scrub names inside packet payloads.

---

## Already covered by the automated suite

Do not spend shakedown time re-checking these; 582 tests cover them:

- packet gap, reorder, duplicate, wraparound and flashback accounting;
- byte-identical forwarding, loop/duplicate/public-address refusal;
- capture write, truncation recovery and replay determinism;
- additive migrations, backup creation and restore;
- distance alignment, segment reconciliation, track projection on
  self-crossing geometry, compatibility classification;
- coaching rule families, attributed-vs-measured bounds, opportunity scoring
  and finding diversity;
- API contracts, LAN auth/CORS/CSRF, and UI contract tests.

Verified by hand during implementation, on this machine, against the real
121 MB database:

- the 4.2 migration preserved all 13 pre-existing tables, all 17,558 rows and
  every column, added 19 tables, and produced a restorable backup;
- rollback via `restore_backup` returned the database to the pre-4.2 schema
  with all rows intact and kept a safety image of the 4.2 state;
- adapter discovery ranks the real Wi-Fi adapter (+216.5) far above the
  link-local Bluetooth adapter (-636.5);
- a real recorded lap comparison returned +0.182 s where the raw lap times
  differ by 0.183 s;
- a tool-call snapshot carrying 30,000 trace points went from 1,761 ms to
  63 ms once the trace payload was bounded, and no longer scales with trace
  density;
- no horizontal page overflow at 1920x1200, 1366x768 or 390x844, and all
  touch targets meet 44 px.
