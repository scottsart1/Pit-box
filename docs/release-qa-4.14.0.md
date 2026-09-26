# Your Pit Box 4.14.0 — Dashboard editor and immersive Android

Release accepted and published on 25 September 2026 (26 September UTC).
Windows and Android revision 28 passed the checks recorded below, including
installed-device acceptance, public download readbacks and update-client
verification. Earlier failed attempts and the limits of this acceptance are
retained explicitly.

## Scope

- Driver Dashboard has a 24-widget library, six starting presets, add/remove,
  reorder and resize controls, undo, autosave and up to eight named layouts.
  Pointer handles support touch and mouse; arrow keys and size menus provide
  keyboard alternatives. Disconnected layouts remain editable.
- Sample data works without simulator telemetry and remains explicitly
  labelled, including in Race mode. Live, sample and replay provenance are
  distinguished. Packet-family freshness gates optional readings; missing
  values remain unavailable. Gesture cancellation handles lost capture,
  blur and pagehide, while flags and source labels continue updating during
  a drag. The standalone offline HTML and service worker are rebuilt.
- DRIVE restores tyre pace insight beside tyre wear and in its pace strip.
  Measured fuel-corrected trends, estimates for the fitted compound, learning,
  wet-condition uncertainty and unavailable telemetry have distinct wording.
  Negative trends and compound changes are handled without inventing values.
  Existing backend tyre-learning exclusions remain intact.
- Android uses native immersive status/navigation-bar hiding on launch,
  resume and focus return. Edge swipes reveal transient bars; cutout,
  caption and keyboard insets preserve usable controls. Keyboard dismissal
  restores immersive mode. The released Android revision is 28.
- Android dashboard preferences use one bounded, committed native record,
  exposed only to the backend's exact loopback origin (including its iframe).
  Restoration finishes before editing starts. New saves can survive backend
  port changes; first use migrates the current browser origin's preferences.
  Inaccessible older origins are not silently merged. Websites retain local
  browser storage, and failures cannot overwrite a saved native record.
- The website and packaged application use the same dashboard source.
  Versioned application asset references are updated to 4.14.0.

## Verified automated checks

| Check | Result |
| --- | --- |
| `node --test tests/driver_dashboard.test.mjs` | 43 passed, including widget/preset validation, migration, saved layouts, immutable reorder/resize, freshness, provenance, stale editing, escaping, accessible markup, offline-bundle parsing and nine native-preference restoration/error/order cases. |
| `node --test tests/drive_degradation.test.mjs` | 9 passed, including DRIVE renderer integration, wet uncertainty, stale/offline state, compound changes, signed slopes and fitted-stint estimate selection. |
| `pytest -q tests/test_tyre_learning.py tests/test_strategy_weather_evidence.py` | 45 passed; backend learning safeguards remain intact. |
| Shipped JavaScript | `node --check` passed for dashboard code; DRIVE inline script parsed successfully. |
| Offline build | `python tools/build_driver_dashboard.py` succeeded after the output-writing fix; generated HTML is current and the service-worker version is 4.14.0. |
| Android project and fullscreen policy checks | 34 passed after the final IME/consent fixes, including InsetsPolicy parser scenarios and executable Java edge-gesture, focus/IME, one-restore-per-gesture, deferred keyboard restoration and late-callback checks. |
| Native Java compile | `:app:compileDebugJavaWithJavac`: BUILD SUCCESSFUL, 18 tasks. Python packaging tasks were excluded; this is not a full APK build. |
| Final website checks | 81 passed after release-card and checksum-validation updates. |
| Download/update Worker checks | 87 passed with the Android revision 28 route. |

The initial application-wide run had 1,585 passes and one stale asset-version
assertion. Asset references were corrected; the targeted rerun passed all
11 tests. The initial distribution run had 314 passes, one skip and two stale
website assertions; those assertions were updated. The later Windows CI
suite completed with 1,902 passes and one skip, as detailed below.

Independent code review found incorrect packet families for active
aero/overtake and penalty readings. These now use packet 16 and packet 2,
respectively. The review also prompted explicit cancellation of interrupted
pointer gestures and continued flag/source updates while editing.

## Private real-recording adapter check

The current dashboard adapter was run offline against an existing private
recorded state, using its latest packet timestamp as the freshness clock.
Direct and converted values were checked against the source for driving
inputs, G forces, tyre core/pressure/wear, fuel, energy, damage, component
wear, weather, progress and penalties. Missing packet-family gates,
disconnected/stale states and all 24 widget renderers passed. No sample
numbers were substituted.

RPM intentionally remains unavailable: the backend's live state does not
expose `engine_rpm`, although archive telemetry can contain it. This release
does not add that backend field, and the widget describes RPM as available
only when supplied.

Only aggregate results are recorded here. The recording and detailed
evidence remain outside the repository in the private `release-4.14.0`
directory; `dashboard-recording-adapter-check.json` contains the adapter
receipt without personal telemetry values. No private recording was copied
into the repository or website.

## Local Windows runtime

The original local Windows candidate built successfully. Its installer is 34,293,761
bytes with SHA-256
`2272d7ab77a7f038607627c6fcc94b67c7da1fd5820a693b15ef2fb1b0ebdff2`.
The frozen executable reports 4.14.0/schema 4904 and serves all 27 static
assets matching source `8b58c29bbe5387fcb430382fed93308509f7bb78`
(line-ending normalization only). Its verified executable and backend were
retained for the final frontend repack described below.

The first isolated four-lap run at 2x speed failed the helper's 30-second
post-emitter final-classification deadline. The receiver was still processing
the race: frame 2800 at emitter exit, then frame 3003 at failure, while the
race ended around simulation second 371.7. The received motion prefix had no
confirmed gaps at that checkpoint. This attempt is failed evidence, retained
privately as `frozen-qa-77sn3ojg`.

A second 2x run, with 180 seconds allowed for classification to catch up,
passed the unchanged correctness assertions: exact emitted final result,
eight comparisons against four rival references, comparison and single-lap
traces/analysis, Library/Field quality, and flag carry-over across the start
line. After archive drain, the database contains 90 race laps (four player,
86 rival). SQLite integrity, restart persistence and two clean exit-zero
shutdowns passed. All 18,464 received datagrams parsed; receiver and archive
queues dropped none and archive writes reported no errors.

The 2x run was **not lossless**: the final network diagnostics show 68
confirmed gaps in the regularly emitted motion stream before the receiver
queue. Receiver high-water reached 1,928 of 2,048. This is a functional pass
with an accelerated-load limit, not real-time acceptance. Evidence is
`frozen-qa-mmkdg535`.

The first 1x run also exceeded the 30-second classification deadline,
progressing from frame 3360 at emitter exit to frame 3539 at failure. It had
zero confirmed motion gaps, receiver drops, archive drops or write errors;
receiver high-water was 437. That run overlapped the separately installed
application's existing-history recovery, so it does not isolate replay
performance. Evidence is `frozen-qa-3kc0gk8h`.

The subsequent 1x run after startup recovery completed **passed**. Its first
post-emitter snapshot already contained the exact final classification.
Emitter exit to the classification assertion took 2.25 seconds, including
three diagnostic API calls; the 180-second allowance was not needed. All
18,936 received datagrams parsed without rejection. The regular motion
stream contained all 3,717 frames with zero confirmed gaps, duplicates or
out-of-order packets. Receiver high-water was 844 of 2,048, with zero drops;
archive queues and writes also had zero drops/errors. After drain, all 90
race laps persisted (four player, 86 rival). The same eight comparisons,
single-lap analysis, boundary flags, SQLite integrity, restart persistence
and two clean shutdowns passed. Evidence is `frozen-qa-zzedmwlj`, including
`final-reception-and-persistence.json`. No real user history was used in
these isolated frozen-app checks.

After the final dashboard preferences changes at
`d5ad9f9cf02eb1fd3fabc15682fe37587ca4665a`, the bundle's static assets were
replaced and the installer/ZIP repacked. All 1,121 non-static bundle files,
including the executable and integrity manifest, stayed byte-identical to
the 1x-tested candidate. All 28 served assets match final source, allowing
only HTTP text line-ending normalization; the new `.mjs` module also has the
correct JavaScript MIME type. Isolated startup, the real UDP fixture and a
clean exit-zero shutdown passed after this swap.

The final local installer is 34,295,718 bytes, SHA-256
`8d6acc7255c98cfc21f08dbc4e4e670bd2a9541a825b00106907ea0ebb6e7c2f`.
The ZIP is 42,743,287 bytes, SHA-256
`f47ca06929acfa57ec18c7514283eb86bc44a56beb355ee4897ff1ee61882592`.
Original artifacts/static files and the complete before/after receipt remain
in private `windows-static-repack-e8zkxxjw`. The initial repack check rejected
CRLF normalization of `index.html`; it restored the original static directory.
Correcting that private check to the established normalized comparison
allowed the final verification above. This did not require an app change.

## Browser and installed Windows checks

The embedded dashboard and self-contained offline HTML were exercised in the
browser. Desktop and phone layouts, sample labels (including Race mode),
widget search/add, presets, named save/clear/load, autosave across reload,
pointer drag reordering, pointer corner resizing, keyboard arrow resizing
and width/height menus passed. A bounded replay sent 1,800 datagrams from an
existing private recording at 2x; live values appeared, then cleared when UDP
stopped without substituting samples. Layout editing remained available.

The Windows installer completed successfully on the owner's computer. The
installed executable and all 28 final static assets match the verified build;
health reports 4.14.0. The existing `.env` is byte-identical to its backup.
SQLite integrity passed and all 164 sessions / 1,084 laps remain. The installed
application's dashboard opens without UDP and shows labelled sample data.
The final static files were refreshed in the running installation without
restarting its unchanged backend. Named save/reload passed again; the
temporary test layout was removed. The final configuration/history check
passed with the same counts and byte-identical `.env`.
The installed DRIVE page also displays its restored tyre insight in both
locations, with an explicit telemetry-unavailable explanation while UDP is
absent.

The first launch took approximately eight minutes while the existing trace
archive was recovered/scanned before HTTP startup. Metadata inspection found
over 14,000 manifests and 65,000 chunks; no backend startup code was changed
in this release. The app subsequently became healthy. The initial acceptance
script also used an incorrect `app.js` URL; changing that private check to
the actual `dashboard.js` path resolved its 404. Evidence is in
`browser-qa.json` and `installed-windows-verification.json` outside the repo.

## Windows CI

[The first Windows run](https://github.com/scottsart1/Pit-box/actions/runs/36202362274)
had 1,901 passes, one skip and one failure: the free-installer download test's
Node subprocess exceeded its 10-second process deadline. It did not report
a download-behavior assertion failure. Build and installed-runtime steps
were skipped after that failed test step.

The Node harness deadline was changed to 30 seconds while retaining its
browser-request deadline assertion of at most 5,000 ms; the targeted ten
tests passed. [The second Windows run](https://github.com/scottsart1/Pit-box/actions/runs/36203458923)
on `da74210` had 1,901 passes, one skip and one failure: a test still required
the previous Android release's exact checksum. That assertion was corrected
to validate the release's scoped 64-character checksum.
[The third Windows run](https://github.com/scottsart1/Pit-box/actions/runs/36204128508)
on `3ba80c5` completed successfully: 1,902 tests passed, one skipped and one
warning in 329.93 seconds, followed by a successful installer build and
installed-artifact smoke. That smoke passed silent install/uninstall,
25-lap accelerated telemetry, final classification persistence/readback,
restart, database integrity, capture finalization and preservation of data
after uninstall. Transfer management passed, but paired history-copy and
TLS/QR checks were not exercised because the CI runner had no usable private
LAN. The accelerated smoke does not establish lossless real-time reception.

The separate counted test sent and received/parsed all 18,604 datagrams over
100 simulated seconds at 1x with 20 cars; zero datagrams were rejected, and
capture/archive queues reported zero drops or write errors. State API latency
was 0.312 seconds p95 / 1.141 seconds maximum; health API latency was 0.359
seconds p95 / 1.187 seconds maximum, with no sample timeouts. Database integrity
and finalized capture checks passed. This is bounded reception evidence,
not full-race endurance. The final frontend at `d5ad9f9` was checked separately
in the local repack above; the frozen backend is unchanged from this CI run.

All three runs' logs and final CI diagnostic artifacts remain in the private
release evidence directory; `windows-ci-final-summary.json` records the
aggregate result. Neither earlier failed run reached installer/runtime checks.

## Android CI keyboard-check failure and harness correction

[Android run 36205370234](https://github.com/scottsart1/Pit-box/actions/runs/36205370234)
built the APKs and passed its host checks. Emulator launch/relaunch fullscreen,
UDP/background reception and transfer TLS/identity checks passed before the
keyboard test failed with `Pairing input did not open the soft keyboard`.
The retained screenshot and accessibility tree show the first-run optional
usage panel covering the pairing input's tap center. The test tapped the
obscured input; logcat contains no corresponding IME-show request. This run
does not establish keyboard or later foreground swipe acceptance.

The harness now declines that optional prompt through its actual `No thanks`
accessibility node and waits for it to disappear before interacting. It also
retains input-method diagnostics on a keyboard timeout. All 33 Android project
tests passed, including absent/present/stuck-prompt cases. An offline replay
against the actual failed CI XML confirmed one decline tap and waiting for
the overlay to close. The corrected harness then passed the final emulator
run below; physical keyboard acceptance is recorded separately.
Private evidence includes `android-ci-emulator-failure.log` and
`android-ci-consent-harness-replay.json`.

## Final Android CI and packaged artifact

[Android run 36206665655](https://github.com/scottsart1/Pit-box/actions/runs/36206665655)
on `edbf1ae77efd69466d862b292b9f6f75dea685f4` completed successfully. Host
Python checks had 1,636 passes and one warning in 148.08 seconds. Node groups
passed all 16 transfer/DOM, 52 dashboard/tyre and 87 usage/owner tests.
Chromium strategy scrolling passed across its tested viewports, and all
54 analysis touch/playback checks passed. Debug, unsigned release and isolated
release-mode QA APK builds completed.

The API 36 Pixel C emulator passed startup and process restart, exact backend
version, real UDP parsing while foregrounded and backgrounded, stationary
trace stability, transfer TLS/QR management and certificate identity across
listener/process restarts. Connection/transfer controls worked through the
WebView UI. Both system bars were hidden after launch, restart and foreground
return; an edge swipe revealed transient bars which hid again. The pairing
keyboard opened, reduced the viewport, kept its focused field above the IME
and restored fullscreen on dismissal. SQLite lifecycle checks found zero
unclosed-handle warnings, and the crash log was empty.

The final signed production APK is `YourPitBox-4.14.0-android.28.apk`, built
from the same `edbf1ae` source. ZIP integrity passed; all 28 static assets and
82 compiled Python modules match that commit, with embedded Python 3.13.
Its exact public size/hash are recorded below. Detailed evidence remains in
`android-ci-final-summary.json`, `android-runtime-evidence-36206665655.zip`
and `android-signed-final28-verification.json` outside the repository.

## Physical Android acceptance

The final signed revision 28 was accepted on the Samsung SM-X930 running
Android API 36. It was installed as an upgrade, without uninstalling or
clearing data. Earlier candidates passed isolated edge swipes but the combined
swipe-then-keyboard case exposed a stuck Samsung status bar. The final fix
retains a pending edge restoration during keyboard use and applies it once
after dismissal; both the final emulator and physical-device checks passed.

On the final device build, top and bottom swipes revealed the system bars;
they hid again after 6.547 and 6.157 seconds, respectively, and remained hidden
for another 12 seconds in each check. The combined test tapped the input
2.125 seconds after the edge swipe. The keyboard stayed open beyond the bar
restoration delay, the viewport resized and the focused input stayed above
the keyboard. Back dismissed the keyboard and restored both hidden bars.
Foreground return also restored fullscreen.

A temporary named layout survived a cold process restart and a subsequent
APK upgrade, including both changes of the backend port. Loading worked and
the temporary layout was deleted through the UI. All 103 sessions and 19,692
laps remained, with unchanged session identity/content summaries. The device
was left on the Cockpit dashboard with labelled sample data and hidden bars.
No real telemetry was injected during this device acceptance. The aggregate
receipt is `android-device-final28.json`; screenshots, timing and persistence
evidence remain private.

## Production publication and update checks

The production Worker version is `a9934438-76f9-4f8a-a495-c51275c0de23`.
The Pages deployment is
[`0caf7484.pitwall-2k7.pages.dev`](https://0caf7484.pitwall-2k7.pages.dev),
from site commit `9345575`, serving [yourpitbox.com](https://yourpitbox.com).

| Public artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Windows `PitWall-Setup.exe` | 34,295,718 | `8d6acc7255c98cfc21f08dbc4e4e670bd2a9541a825b00106907ea0ebb6e7c2f` |
| Android `YourPitBox-4.14.0-android.28.apk` | 72,894,780 | `64d468a7e4d9eb49b0f58cf72587de6d39ecc745e0ec1eb912832528886c9c21` |

Complete ranged public downloads returned HTTP 206 with exact lengths and
SHA-256 matches, correct filenames and MIME types. The Windows installer is
unsigned, as disclosed on its release card; the Android APK is signed.
Each source and built release card matches its corresponding final artifact
and version. All 18 checked production routes match built source bytes,
allowing only the documented Cloudflare email-protection decoding for HTML.
This includes every checked dashboard runtime module and the self-contained
offline HTML. The public dashboard opened with labelled sample data without
UDP; resizing and named save/reload passed, and the test layout was removed.

An earlier staging audit caught a stale built Android release card. The
private verifier now checks each source/built platform card's own heading
and checksum against its local artifact before remote comparison. Its 39
local checks passed and it rejected the stale build; the final site was
rebuilt and passed. Public download and site receipts are retained separately
in `public-verification.json`, with the prior phase's timestamp preserved.

Both 4.14.0 update-publication requests succeeded after artifact/site
verification. The shipped update client then passed all six public checks:
Windows and Android clients on 4.12.4 and 4.13.2 report 4.14.0 available, while
clients already on 4.14.0 report no newer update. Every returned hash, size
and platform download-page anchor matches the final release. The receipt is
`public-update-service-checks.json`, checked at 2026-09-26 01:14:42 UTC.
The actual installed Windows application's update-check endpoint also
returned outcome `ok`, current/latest version 4.14.0, `available: false` and
the correct Windows checksum. Its separate receipt is
`installed-windows-public-update-check.json`.

## Acceptance limits

The emulator runtime uses the debug x86_64 APK; the separate physical check
covers the final signed ARM64 build. These checks do not establish paired
device history copying, full-race physical console/Wi-Fi endurance or
Bluetooth/audio endurance. Android still requires supported 4 KB memory-page
devices; 16 KB devices remain unsupported. The accelerated Windows run's
68 motion gaps and the existing-history startup delay remain documented
above; the quiet 1x run is the representative local reception acceptance.
