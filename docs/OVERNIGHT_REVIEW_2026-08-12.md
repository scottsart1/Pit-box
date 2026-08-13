# Overnight review — 2026-08-12

Running log of evidence → fix → re-verify cycles, per Scott's brief. Sessions
used as evidence: Brazil GP (2026-08-09), Las Vegas #1 (2026-08-10), Las
Vegas #2 (tonight, capture-20260812T023621, 25 laps, P22 → P5 boxing lap 19).

## Cycle 1 — the "early softs" call (item 6)

**Evidence (tonight's DB + transcript):**
- Lap 4 snapshot: recommended "Box lap 6 for SOFT", `stop_required_reason:
  "mandatory compound change"`, `positions_lost_by_stopping: 15`,
  `positions_gained_vs_stay_out: -1..-8`, rejoin P22.
- 23:08 "Box lap 7 for softs" → 23:09 challenged: "Stay out — boxing costs you
  14 places and gains nothing. Your current wear model projects 100% by the
  finish…" → 23:10 proactive: "Box lap 6 for softs." Three contradictory
  positions in 65 seconds.
- Lap-3 plan board: ONLY early-stop variants (box 3–5). The lap-18 one-stop
  the driver actually drove (finishing P5) was generated but outranked.

**Root causes found:**
1. `ranking_key` ties on projected finish (the recovery model gave every plan
   P20 early on, ~1 position recoverable) and then falls through to raw
   risk-adjusted time — which always prefers the earliest soft stop. No
   preference for keeping track position / option value when the model itself
   claims positional parity.
2. The stay-out traffic guard in `brain._spoken_strategy_instruction` fires on
   `gained <= 0 < lost` even when the stop is MANDATORY (two-compound rule),
   telling the driver to stay out of a stop the engine knows must happen.
3. The engineer never says WHY the stop exists. "Box lap 6 for SOFT" with no
   mention that a second compound is legally required reads as a (bad) pace
   call.

**Fixes:** (see code)
- ranking_key: among plans equal on legality/feasibility/classification/points,
  prefer the LATER first stop (staying out counts as latest). Raw time only
  breaks remaining ties.
- Mandatory-stop instructions now lead with the reason: "A second compound is
  mandatory — box lap N for X."
- The stay-out guard only speaks when the recommendation is purely
  pace-driven, never against a mandatory or wear-forced stop.

**Verification:** replay of tonight's capture at laps 3–6 (below).

## Cycle 2 — the port-24 profile poisoning (found while verifying Cycle 1)

**Evidence:** replaying tonight's capture into an isolated app with a copy of
the real database, the app bound UDP to `10.234.197.105:24`. The real
`pitwall.log` shows every launch since 2026-08-09 21:36 bound port 24 — three
nights of "telemetry isn't connected" at session start. The stored profile
row: `bind_host=10.234.197.105, udp_port=24, last_working_at=<tonight>`.

**Root cause chain:** the Connection tab rendered adapters as
`10.234.197.105/24` (CIDR) exactly where the user is told to copy the IP; the
/24 was read as a port and typed into the listener form; the bind SUCCEEDED
(port 24 binds fine); `start_listener` persisted the bind immediately, before
any packet proved it; every shutdown re-persisted unconditionally and stamped
`last_working_at`. The user then matched the game to port 24, so the setup
now works — which is why NO auto-rebind is performed.

**Fixes:**
- Adapter cards show the plain IP; the prefix moved to a separate line
  labelled "subnet mask /24 — not a port".
- A bind is persisted only once PROVEN by a valid F1 packet (first-ever
  profile excepted, so fresh installs still remember their setup).
- Shutdown never overwrites a proven profile with an unproven bind.
- A visible warning when the listener port is < 1024, with the fix spelled
  out — but the working bind is respected (the game may be matched to it).

**Judgment call:** his current game+app both use port 24 and function; a
silent "repair" to 20777 would kill telemetry again. Warn, don't rebind.
Tests: tests/test_network_profiles_v42.py (2 new), all 68 networking tests
green.

**Cycle 1 verification (done):** tonight's capture replayed at lap 3 with a
copy of the real DB. Old output: "Box lap 6 for SOFT" (rejoin P22, recover
~1). New output: "A second compound is mandatory — box lap 20 for SOFT",
plan board all late one-stops (19/20) — matching the stop the driver actually
made (lap 19) on his way to P5.

## Cycle 3 — the "100% coverage / zero seconds / room for improvement" contradiction (item 3)

**Evidence (workflow audit, file:line):** a segment whose aligned time delta
is unmeasurable was coerced to 0.0 (`comparison_service.py:1191 delta_s or
0.0`), fed to coaching (whose control-signal findings are independent of the
time channel), persisted as 0 (never NULL), and rendered as
"Measured: 0.000 s through this segment. Try: …". Meanwhile the "100%"
figure is RECORDING coverage (field presence, session_assembler), a different
quantity from timing-alignment coverage — both can honestly coexist with an
unmeasurable delta, but the UI presented them as one story. Bonus hole: a lap
could be compared against itself (direct reference kinds never checked
reference != candidate), which yields 0.000 s everywhere with 100% coverage.

**Fixes:**
- `loss_measured` now travels from segment alignment through SegmentEvidence
  and CoachingFinding into the finding dict: unmeasurable → `measured_loss_s:
  null` end to end (API, persistence as NULL, deterministic explain()).
  Both UI sites already rendered null honestly ("time attribution
  unavailable"); they had just never been given a null.
- Self-comparison rejected with a clear message for lap/field_driver/
  saved_benchmark reference kinds (derived kinds already excluded it in SQL).

Tests: tests/test_comparison_service_v42.py (2 new); comparison, coaching,
analysis-API, engineer-review suites all green (37 tests).

## Cycle 4 — UI: consolidation, strategy interaction, customizable dashboard

**Item 1 (page redundancy).** Evidence: session-review, lap-lab and field are
one JS module sharing one state object and one load path; HISTORY is a legacy
inline-script page re-showing laps/corners/strategy from the old API; the
same lap tables/quality/segment losses render on 3-4 pages. Decision: one
ANALYSIS top-level tab with Library / Session Review / Lap Lab / Field /
History as inner views. Every element id, aria pairing and JS hook kept, so
the existing module works unchanged; old #library-style links still land on
the right view. Top nav: DRIVE · STRATEGY · ANALYSIS · SETUP LAB ·
CONNECTION (was 9 tabs).

**Item 2 (strategy tab).** The conversation panel and decision log now live
in a sticky right rail beside the plans (no more related-things-at-both-ends).
The stint timeline is editable: clicking the lap axis moves (or creates) the
nearest stop of YOUR plan to that lap; clicking a stint bar cycles its dry
compound, skipping illegal back-to-back duplicates. Edits anchor on the
committed driver plan, not the engine's live ranking (verified: a compound
click no longer drifts the stop lap). All edits commit through the existing
/api/strategy/plan contract — same as saying it over the radio.

**Item 5 (collapsible trace + dashboard).** The telemetry trace is a
collapsible card (state persists). A ⚙ Dashboard control in the header
toggles 15 cards on/off, persisted in localStorage; hidden cards skip their
canvas redraws and per-lap fetches. Full drag-and-drop rearranging is future
work (documented).

Verified in the running app against tonight's replayed race: dashboard
toggle round-trip, trace collapse persistence, all five analysis views,
history content, timeline click-to-move-stop (lap 20 committed), compound
cycle (stop lap unchanged), zero console errors.

## Cycle 5 — setup recommendations reflect the driver (item 4)

The advisor read only last-lap booleans and a live wear snapshot. It now also
reads: (a) the persistent corner_metrics record for the circuit — repeated
lock-ups move brake bias/pressure with the worst corner named in the
rationale; repeated wheelspin softens the power differential; (b) the
driver's measured wear rate vs the circuit baseline (same evidence extraction
the strategy engine uses), trimming pressures when the driver runs hot.
Every nudge names its evidence, so "three setups" now visibly reflect how the
driver actually drives. FUTURE (flagged, not tonight): per-corner causal
setup optimization from the typed telemetry pipeline; drag-and-drop dashboard.

## Ship — 4.4.0 published 2026-08-12 ~03:07 EDT

- Full suite before ship: **968 passed, 0 failed** (tests + distribution/tests),
  including 3 new setup-personalization regression tests and the earlier
  network/comparison/strategy additions.
- Version 4.4.0 stamped in all nine locations; installer built via the
  standard driver (`python -m distribution.packaging.build --installer`),
  integrity manifest stamped.
- Smoke test: the frozen one-folder build (the installer's exact payload) was
  run against an isolated data dir with a copy of the real licence —
  /api/health returned ok + version 4.4.0, licence gate passed, UDP listener
  up, and all new UI markers (ANALYSIS subnav, dashboard config, collapsible
  trace, strategy rail, timeline click handler, subnet-mask label) served by
  the frozen bundle. Clean shutdown.
  - A silent install of the packaged installer was attempted first and
    deliberately abandoned: Inno treats it as an upgrade of the existing
    4.3.1 install (registry install path wins over /DIR), so a silent smoke
    test would have modified the real install. It exited without writing
    anything (verified by file timestamps).
- Rollback: the production 4.3.1 installer was copied inside R2 to
  `PitWall-Setup-4.3.1.exe` and hash-verified (SHA-256 2A3D9D02…A37025)
  BEFORE anything touched the download key.
- Published: `pitwall-downloads/PitWall-Setup.exe` now holds the 4.4.0
  installer — 33,091,593 bytes, SHA-256 71AA5A86…35AB3E, verified by
  downloading the object through the Cloudflare dashboard (an independent
  read path) and hashing it. The site's download flow serves this fixed key
  through the Worker, so no site or Worker change was needed or made.
- **Incident, disclosed**: `wrangler r2 object get` served stale bytes for
  any key it had read before (its first read of a key sticks), which made
  three successful uploads look like failures. Chasing that ghost, a 24-byte
  probe file was PUT onto the production key at 03:02:40 EDT and replaced
  with the real 4.4.0 installer at 03:06:47 EDT — roughly a four-minute
  window at 3 AM in which a download would have failed loudly (a 24-byte
  file that is not an executable). The dashboard object listing was used as
  ground truth from then on. Writes (`put`/`delete`) were reliable the whole
  time; only the CLI's reads lie.

---

# Follow-up session — 2026-08-12 (daytime): the three deferred items

## Cycle 6 — per-corner causal setup optimization (and the bug underneath it)

**The foundation was broken, and fixing it came first.** The 2026 Motion
packet packs g-forces as int16 milli-g; the app forwarded the raw value as
if it were g (verified against real captures: threshold braking reads
-4002..-4040 = -4.0 g, peak Vegas lateral 3849 = 3.85 g). Every sample over
120 kph therefore looked like a 1 600 g corner, which merged corner
segmentation into ONE lap-length zone per lap — the entire per-corner
record ("Corner 1 @ 5206 m", losses of 1 400 s, apex speed 0) was garbage.
Collateral: the proactive engineer's "safe moment to speak" gate compares
lat_g against 1.35 g, so with milli-g values its quiet-corner logic had
degenerated to "speed < 75". Fixed at ingestion (udp.py g_force_to_g), with
scale auto-detection for historical traces (no car corners at 30 g).

**Zone extents lie; canonical turn windows don't.** Even segmented
correctly, detected zones vary lap to lap (a lift extends one, close
corners merge on one lap and split on the next) — a real Vegas cluster
showed "median 14.3 s vs best 6.6 s", pure extent variance. Each track now
gets a canonical turn model (median entry/apex/exit of zones across laps,
support-filtered, overlap-clipped, stored in the preferences KV), and every
lap — historical at rebuild, live at completion — is measured over those
fixed windows with boundary interpolation. Turns a lap doesn't fully cover
are skipped, not guessed.

**The causal layer.** For each turn: cluster all passes, measure the median
loss against the driver's own best pass, classify the mechanism, and only
touch the car when the mechanism is one setup can address. Lock-ups get
their traces re-read to learn WHICH axle locks (front wants bias rearward,
rear the opposite). One event in four passes is a moment, not a pattern
(two events minimum). A slower apex only blames the car when the entries
demonstrably matched; braking earlier than your best pass is driving and is
said so. Conflicting corners don't average silently — the bigger measured
loss wins and the conflict is named.

**Verified against the real database** (copy): Vegas → 7 supported turns,
losses 0.17-0.45 s, two honest technique findings (early braking at the two
big stops); Mexico → exit wheelspin at the slow T4 (the traction circuit
gets the traction fix); Brazil → 0.41 s × 25 passes of late-back-to-power
at T3 → gentler power diff, and mid-corner grip at T1 → softer front ARB.
SETUP LAB gained a "Where the car costs you time — corner by corner" panel.
13 new tests (test_setup_insights_v45.py) + the v44 advisor test rewritten
for the causal engine.

## Cycle 7 — drag-and-drop dashboard layout (item 2 of the follow-up)

The DRIVE cards live in two columns, interleaved with fixed furniture (the
radio log, pre-race card, ask-engineer controls, error strip). Reordering is
therefore SLOT-BASED: cards permute among the positions cards already
occupy, fixed elements never move, and cards never cross columns — the
columns have different widths and roles, and the "Car" caption and error
strip anchor the right one. Dragging is armed only while the ⚙ panel is
open, so a mid-race canvas click can never grab a card; the ⚙ list also
gained ↑/↓ buttons (keyboard/precision path), shows cards in their current
order, and has a Reset layout button. Order persists in
localStorage (pitwall.dash.order) beside the existing show/hide state.

Verified in the running app against the real database: nudge moved a card
past two others while every fixed sibling held position; a synthetic
drag-and-drop landed fuelCard after wingCard with the fixed strategyCard
untouched; order survived a full page reload; the top card of a column
refuses to leave it; Reset restored the natural order and cleared storage;
arrange mode disarms when the panel closes. Zero console errors.

## Cycle 8 — "telemetry storage scoping" (item 3), corrected and closed

**Correction first**: the literal Vegas item 9 — limit race trace storage to
the player, teammate, podium and ±2 grid neighbours, everyone in
practice/quali — was already implemented, tested and SHIPPED in 4.3.0
(cars_in_trace_scope, commit 4649e5d). The overnight report's "future work"
flag for it was a mislabel. What genuinely remained was the symptom that
motivated it and two loose ends:

1. **"Telemetry is stored sporadically" — root-caused with raw captures.**
   The PWCAP files record every datagram as it reached the socket. Two real
   race nights measured car telemetry ARRIVING at p50 = 4/s (08-10) and
   12/s (08-11) — and stored trace density matches arrivals almost exactly
   (4.6/s and 10.6/s). The app stores everything it receives; the feed
   itself is sparse (game send-rate setting + network loss; rates vary
   night to night on the same track). The app now measures the CarTelemetry
   arrival rate at the socket and, when a live session runs below 15 Hz,
   says the actual number and what to change (game Settings → Telemetry UDP
   send rate; prefer wired). Regression test feeds 5 Hz and expects the
   warning, then 30 Hz and expects silence.

2. **Fidelity control**: PITWALL_FIELD_TRACE_SCOPE=all keeps every car's
   race traces for installs where disk is cheaper than lost detail (this
   dedicated laptop's stated preference); default stays "focused".

3. **The storage API had no consumer**: the Library now shows managed bytes
   against the configured budget, free disk, and the policy warnings —
   with the explicit note that nothing is ever deleted automatically.

**Found while verifying**: module scripts cache per-URL, so an UPGRADED
install could run the previous release's JS against the new backend (seen
live in the browser pane — the server served new bytes while the page
executed stale code). The three module script URLs now carry a version
query that changes with each release.

## Ship — 4.5.0 published 2026-08-12 ~08:51 EDT

- Full suite before ship: **982 passed, 0 failed** (two UI tests updated for
  the versioned script URLs — their literal-tag assertions, same intent).
- Version 4.5.0 stamped in all nine locations; installer built with the
  standard driver (one retry: a locked VCRUNTIME140.dll from the previous
  dist was renamed aside), integrity manifest stamped.
- Frozen-build smoke test against an isolated data dir: health ok, version
  4.5.0, licence gate passed, all new markers served (corner findings panel,
  dash reorder controls, versioned module URLs, storage strip code).
- Rollback: the production 4.4.0 installer (byte-verified dashboard copy,
  SHA-256 71AA5A86…35AB3E) uploaded as `PitWall-Setup-4.4.0.exe` and
  round-trip verified on first read. 4.3.1 also remains as a key.
- Published: `pitwall-downloads/PitWall-Setup.exe` = 33,106,850 bytes,
  SHA-256 86282B06…5D5F92, **verified via the Cloudflare dashboard
  download** — per the standing lesson, wrangler's read of a previously-read
  key is never trusted. Site and Worker untouched, as before.
- On first launch after upgrade, each installed copy rebuilds its own
  per-track corner history from stored traces (once per track, marker is the
  stored turn model; derived data only, traces untouched).

## Cycle 9 — whole-corner racing-line optimization (tier 2)

**What recon found.** The typed pipeline had a racing-line feature half-built
and abandoned: a Frenet track-model projector with zero callers, a `line_n`
channel that is always NaN, a dead "Line offset" tab in Lap Lab, a declared
but unimplemented `line_offset` metric, a dormant coaching rule, and a DRIVE
bug — drawLine read `line.persistent_zones`, a field no backend produces
(the real field is `zones`), so line-deviation markers have never rendered.
Also: `~/PitWallData/track-models` is empty — no published centerline model
exists for any circuit, so any design that requires one would ship dead.

**Design (self-relative, honest).** New `line_insights.py`, a sibling of
`setup_insights.py`: for each canonical turn (4.5.0's turn model), every
recorded pass is resampled at 4 m stations (skipped entirely on >40 m
recording holes); the median line of the driver's fastest tercile is the
reference; a finding exists only when the slow passes sit on the same side
of that line at the decisive station on >=60% of passes, the offset clears
the measured ~2 m lap-to-lap noise floor, and the time deficit is >=0.15 s.
Advice is phrased as what the quick laps already do ("your quick laps enter
1.8 m further right — use that road"), in metres, signed left/right of the
direction of travel. Anything inconsistent is reported as "not a line
problem" so braking/throttle losses stay with the setup and coaching
analyses. Rival-line comparison from field traces and true centerline
`line_n` (needs track-model publishing) are flagged future work, not faked.

**Verified against real data** (rebuilt Vegas history, 61 valid laps):
three findings — Turn 1 exit (quick laps release 4.1 m further left,
0.382 s), Turn 3 exit (2.6 m right, 0.268 s, 100% consistency), Turn 6 exit
(2.0 m right, 0.234 s, 100% consistency, quick laps 107 vs 98 kph at apex —
matching the raw x/z probe done before the module existed). Turns 4/5
correctly gated (time spread 0.130 s; geometry inconsistent). 8 new
regression tests pin the gates.

**Shipped surface**: GET /api/analysis/line (on-demand, track-resolved like
/api/review), a "Racing line" card in ANALYSIS > History, and the
persistent_zones/zones DRIVE fix.

**Cycle 9 verification (live app on the rebuilt real database):**
/api/analysis/line with no live session resolves the most recently driven
circuit (added after the first verification pass exposed the -1 track hole);
ANALYSIS > History renders all three Vegas findings with evidence lines;
zero console errors. 8 regression tests green first run.

## Cycle 10 — Settings page (tier 2)

**Design.** New `settings_service.py`: a whitelisted subset of Settings
fields (17, in four groups — Engineer, Proactive engineer, Telemetry &
storage, Application) persisted as ONE preference row (`app_settings`) in
the database, honoring config.py's contract that .env is installation
defaults and mutable profiles never rewrite it. Overrides load
synchronously from SQLite at `pitwall.app` import — before capture/storage/
web services construct from settings — so restart-flagged fields genuinely
apply at next launch. Hot fields (engineer name, verbosity, voice, wake
phrase/aliases/cue, proactive trio, race telemetry scope) apply live;
restart fields (trace detail, raw capture, disk budget, retention window,
dashboard port, open-browser) are persisted, refused live application, and
badged "saved — applies after restart" instead of pretending. Every row
shows provenance (default / .env / saved in app). Deliberately excluded:
wake_enabled (ptt.json owns it; two writers would fight), network binds
(the proven-bind store owns them), LAN access + token (security validator),
credentials (their own loopback-only flow).

**Real bugs fixed along the way:**
- The DRIVE proactive card saved its toggle/cadence in memory only —
  every restart silently reverted them to .env defaults. Both the card and
  the Settings page now persist through the same store and mirror onto the
  live settings object, so the two surfaces always agree.
- `run()` probed the single-instance port and spawned the browser-opener
  BEFORE the app import that applies saved overrides — a saved dashboard
  port would have had the server bind the new port while both helpers aimed
  at the old one. The import is hoisted above the first port read.

**Verified live**: page renders 4 groups with provenance chips and ⟳
badges; select change → "Applied." + provenance flips to "saved in app";
web_port save → "restart_required", live value untouched, pending badge; bad
value (cadence 99) → named 400; restart → saved values survive and apply.
7 regression tests pin coercion, the wake_enabled exclusion, garbage-
tolerant startup, restart honesty, provenance, and the store round-trip.

## Ship — 4.6.0 published 2026-08-12 ~13:49 EDT

- Full suite: **997 passed, 0 failed** (3m51s); distribution + new-feature
  tests re-run after the version bump to close the mid-run edit race
  (134 passed).
- Installer built (integrity manifest 4e099539…), ProductVersion 4.6.0,
  33,131,661 bytes, SHA-256 AAA9A1E8…58E9E8.
- Frozen-build smoke test against a copy of the real rebuilt database:
  health ok/4.6.0, SETTINGS tab + settings loader served, ANALYSIS>History
  line card served, /api/analysis/line returned the three real Vegas
  findings from inside the frozen bundle, 17 settings entries, clean exit.
- Published to `pitwall-downloads/PitWall-Setup.exe`; verified by
  downloading the object through the Cloudflare dashboard (independent read
  path per the wrangler stale-read lesson) and hashing: exact match.
- Rollback chain now on the bucket: PitWall-Setup-4.5.0.exe (86282B06…,
  round-trip-matched before the overwrite), -4.4.0.exe, -4.3.1.exe.
- No Worker, D1, licensing, or secrets touched. Working tree left
  uncommitted for review.

## Cycle 11 — the blank ANALYSIS sub-pages, the stale-frontend trap, and 4.6.1

**Scott's report (real 4.3.1→4.6.0 upgrade):** ANALYSIS sub-tabs led to a
blank page. Reproduced exactly with a real mouse click — and the reproduction
exposed that my own pre-ship checks had been reading DOM text of elements the
user could not see. Screenshots are the honest instrument; internal flags are
not.

**Root cause (two routers fighting over one DOM):** connection.js's
syncTabs collected every '[role="tab"][data-page]' button — which matches
the five ANALYSIS sub-tabs, not just the six top tabs. A sub-tab click passed
its own name through syncTabs, no <main> matched it, and every page went
hidden at once, leaving only the status rail. Fixed by scoping syncTabs and
its keyboard roving to '.tab' top-level buttons and resolving analysis
sub-view names to the analysis page. Five regression tests pin the selector,
the sub-view mapping, and that sub-tabs never carry the top-level class.

**The deeper trap fixed with it:** asset cache-busting was frozen at
?v=4.5.0 (the bump script didn't know about it) and nothing sent
Cache-Control, so an upgraded install could run last week's JavaScript
against this week's HTML — the exact recipe for symptoms only upgrades see.
Now: the ?v= params are part of the version bump, a test fails the suite if
they drift from __version__, the CSS is versioned too, and the dashboard
serves everything with Cache-Control: no-cache (ETags make that one 304 per
file on a localhost app).

**Launch experience (Scott's ask):** a dashboard boot overlay (app badge,
spinner, rotating notes) shows from first paint and dismisses on the first
live state frame (12 s failsafe), and the frozen build shows a small
always-on-top splash between double-click and server-up, closed by the same
socket-ready signal that opens the browser — the silent 5–15 s that read as
"broken" now looks alive. Setup Lab also re-renders its last recommendation
from live state after a reload instead of coming back blank.

**Website refreshed and deployed:** six gallery screenshots captured from
the running 4.6.1 build replaying the real Las Vegas race (drive, strategy,
setup corner insights, settings, connection, library), a "New in the latest
updates" section, and the 7-day money-back guarantee in the buy box and FAQ.
NOTE FOR SCOTT: the FAQ previously promised 14 days; per the instruction it
now reads 7 everywhere — flip it back if you prefer the longer window.

**Ship — 4.6.1 published ~19:02 EDT.** Full suite 1002 passed, 0 failed
(one pre-existing test updated: it asserted the literal unversioned CSS href;
its no-CDN intent is kept and strengthened by the version-pin test). Frozen
smoke on a real-data copy: health 4.6.1, no-cache header, boot overlay,
settings page, sub-view fix in the bundle, 3 line findings, clean exit.
Published 33,131,335 bytes, SHA-256 5460A703…68CE0F, byte-verified via
dashboard download (wrangler get confirmed stale-serving again in 4.122.0 —
the dashboard remains the only trusted read). Rollback chain: 4.6.0, 4.5.0,
4.4.0, 4.3.1 all retrievable. Site live on yourpitbox.com with the new
content (verified over HTTP). No Worker/D1/licensing/secrets touched; no
commits or pushes.

## Cycle 12 — 4.6.1 crashed on launch; 4.6.2 replaces the splash with the bootloader's

**Scott's report:** ERR_CONNECTION_REFUSED at 127.0.0.1:8000 after
installing 4.6.1. His log showed two launches (19:54, 19:56) both reaching
"startup complete" and then dying silently within seconds; the Windows
Event Log pinned both: faulting module **tcl86t.dll**, exception
0x80000003 — the new launch splash, a Tk window in a daemon thread,
aborts the frozen process. My smoke test had missed it because it ran with
the browser-opener disabled, which also gated the splash: the one code
path I never exercised was the one every customer runs.

**Fix:** the in-process Tk splash is deleted. 4.6.2 uses PyInstaller's
bootloader splash — drawn by the bootloader on its own main thread before
Python even starts, closed from the app via pyi_splash's pipe message
(safe from any thread). It closes when the server socket accepts, when a
first-run dialog needs the screen, on the single-instance handover, or on
a 90 s timeout so it can never mask a failed launch. 5 regression tests
pin the design (no tkinter in pitwall/main.py, Splash in the spec, closer
behavior); the native crash itself is untestable from Python, so the smoke
test now runs the REAL path: browser-opener on, splash active.

**Ship — 4.6.2 published ~20:17 EDT.** Full suite 1007 passed, 0 failed.
Frozen smoke on the real path: startup ok, then alive at every 30 s check
for 3 minutes past the moment 4.6.1 died, zero new Event Log crashes,
clean shutdown. Installer 33,164,803 bytes, SHA-256 9D10FF5F…463257.
Publish verified via the dashboard listing: the key is 33.16 MB (a size
unique to this build among all six objects) modified 20:17:14, right
after the 4.6.1 archive (20:17:00, uploaded from the dashboard-verified
copy after wrangler's stale-read cache served 4.6.0 bytes and the hash
guard correctly refused to archive them). Rollback chain now: 4.6.1,
4.6.0, 4.5.0, 4.4.0, 4.3.1.
