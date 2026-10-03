# Your Pit Box 5.3.1 — sourced circuit setup references

**Published 3 October 2026.** Windows and Android downloads, the public website
and both update channels serve **5.3.1**. The physical production tablet was
upgraded in place to Android revision **33**, preserving all **144 sessions and
25,301 lap records**. Final CI, browser, artifact and preservation results are
recorded below, alongside the limits of native touch verification. Verification
began on 2 October US Eastern and continued on 3 October.

## Source identity and scope

Application source:
[`fb5d56a3944d7ae4e27a1325da26bb64e82481ad`](https://github.com/scottsart1/Pit-box/commit/fb5d56a3944d7ae4e27a1325da26bb64e82481ad).
Application version: **5.3.1**, Android revision **33**.
This adds a source-format-tolerant regression-test extractor to the previously
browser-verified application commit
[`a5f20e2eeac84f4730dbf6f43a4ef0dd53a08fc2`](https://github.com/scottsart1/Pit-box/commit/a5f20e2eeac84f4730dbf6f43a4ef0dd53a08fc2);
application behavior is unchanged.

Setup Lab now defaults to a sourced circuit reference. Users choose a circuit,
dry or wet conditions, Stable race (Matt212) or More rotation (Derp3339), and a
race, qualifying or hybrid goal. Source links, review dates and limitations are
available with each result. Personalise for me separately enables bounded
changes using applicable live context and explicit driver preferences.

The reference catalogue targets **F1 25: 2026 Season Pack**. It contains 28
stable circuit rows and 25 rotation rows, including all 24 calendar circuits.
Rotation references retain the creator's separate race and qualifying values;
hybrid uses race values. Stable qualifying uses the unchanged published race
reference. Unsupported wet/circuit/style selections return an unavailable
result, clearing any previously displayed setup.

Research provenance, interpretation and reproducible source CSV digests are in
[setup-reference-research.md](setup-reference-research.md). Independent parsing
compared all **560 stable numeric fields** and **1,000 rotation numeric fields**
across both profiles against the fetched source CSVs. All matched. The latter
comparison includes the documented interpretation of the creator's `LLLL`
geometry notation; it does not claim independent in-game slider inspection.
Original bias ranges, notation, theory flags and missing dates remain disclosed.

Published starting points have not been driven or proven faster by Your Pit Box.
No universal pressure changes are inferred from temperature or wear alone.
Uncalibrated setup pace and wear corrections are neutral. Legacy mixed-session
setup learning is retained for review but no longer alters recommendations.

Live personalization requires an observed Session packet identifying formula
13, the matching circuit and conditions, and a live session. Packet year alone
does not establish compatibility. Deliberate reference browsing remains
available when the connected car differs, with an applicability warning and no
live pit instruction. Session, timeline and track changes during preparation
prevent publication into the replaced context. Raw received setup values are
not mutated.

## Local verification

| Check | Result |
| --- | --- |
| Focused setup/advisor/strategy regressions | 107 passed |
| Reference catalogue tests | 34 passed |
| Setup API and voice-tool integration | 9 passed |
| Node tests | 139 passed |
| Website and Android project checks | 116 passed |
| Initial full application test suite | 1,811 passed, one failed, one warning in 837.29 seconds; stale source-format assertion corrected below |
| Corrected UI regression tests | Six passed after the multiline extractor fix; final Android full-suite revalidation passed below |
| Isolated real-UDP engineering acceptance | Passed |
| Existing engineering and lap-group browser suites | Passed at all three viewports |
| Setup-reference browser acceptance | Passed at all three viewports |

Focused counts describe the reported test invocations and are not summed into
an independent total; suites can overlap. The full local run is recorded in
`.codex-ui-test-data/setup-reference-full-tests-5.3.1.log`.

The initial full run failed
`test_ui_analysis_v461.py::test_render_setup_result_owns_every_name_it_uses`.
Its extractor stopped at the first newline, so it inspected only the now
multiline function declaration and missed the local `profile` binding on the
following line. Browser acceptance exercised race and qualifying rendering
successfully. The failed assertion is retained here as release evidence. Commit
`fb5d56a` corrected the test extractor, and all six focused UI regression tests
passed. The application did not change. Full CI on that commit supplies the
required full-suite revalidation; another duplicate local full run was not
performed.

An additional in-memory offline matrix exercised **504 combinations** across
28 circuits, two styles and three profiles. Dry references and neutral
personalization preserved the selected source values exactly. Wet requests and
missing rotation reverse-layout references remained unavailable; no current
telemetry or live pit instruction was invented, and raw setup state remained
unchanged. This matrix used a mocked persistence boundary and did not modify
production data. Its full case list, source commit and reviewed-module SHA-256
digests are saved outside the repository at
`%LOCALAPPDATA%/YourPitBoxRelease/5.3.1/setup-reference-review/offline-matrix.json`.

The isolated UDP fixture produced **four matched pairs**, with setup B quicker
by **0.300 seconds**, entirely in sector two. Pit-entry debrief, track change
and rejection of a late retired-session packet passed. No provider credentials
or production database were used in this acceptance run.

Browser acceptance ran at **1280×800, 800×1280 and 390×844**. It checked:

- Stable and rotation selection, published race/qualifying differences, source
  links, and exact agreement between every displayed numeric field and the API.
- Unsupported wet results clearing prior values, changes, source information
  and pit instructions, followed by a fresh uncontaminated dry reference.
- Personalization enabling the change-level selector and reference mode
  disabling it; invalid API selector values returning HTTP 422.
- Circuit and style preservation on navigation. A deliberately held circuit-list
  response did not overwrite a circuit selection made while it was pending.
- Existing matched comparisons, notes, exports and navigation; lap grouping,
  manual lap selection, draft protection, context-note correction and desktop
  delayed-save races.
- No horizontal overflow at any viewport and no setup-page JavaScript errors.

All three browser result files passed under
`.codex-ui-test-data/setup-reference-acceptance-final-5.3.1/browser/`.
The nine setup screenshots were reviewed. Desktop shows all 20 numeric fields
clearly; portrait and phone layouts wrap their headings and controls without
horizontal clipping. Source details are collapsed, with recommended values
before rationale.

A separate isolated source-server check closed the narrow-screen scroll gap.
All setup selectors and profile buttons measured at least **44 pixels** high at
each viewport. The final rear-left tyre-pressure row was scrolled into view,
remained inside the setup pane and passed a hit test showing it was unobscured.
Opening the source disclosure exposed readable links without overflow or
JavaScript errors. Nine additional controls, settings-bottom and open-source
screenshots were inspected under `browser/setup-references/scroll-review/`.
The owned server was stopped and its temporary data root removed after testing.

## Physical Android QA backend acceptance

The separately signed **5.3.1 QA package** passed a read-only identity preflight
at forwarded port 18767: its database was under
`/data/user/0/com.yourpitbox.app.qa/`, and health reported `voice_ready=false`. The prepared
helper restricted synthetic telemetry to **QA UDP port 20787**. The separate
production package and its active recording were not modified.

All three browser suites then passed against that physical tablet's QA backend
at **1280×800, 800×1280 and 390×844**, including the new setup references and
delayed circuit-list response regression. These were desktop Chrome interactions
with the tablet server; native touch, Android system export UI and production
upgrade checks remain separate.

Nine direct physical-backend checks passed. The synthetic ten-lap Spa fixture
produced four initial matched pairs. Three persisted custom groups held **3, 5
and 2 laps**, including noncontiguous selections. The selected-group comparison
retained **three matched pairs**, with B **0.300 seconds quicker** entirely in
sector two. A driver-reported traffic exclusion reduced the comparison to two
pairs and correctly removed the verdict; correcting and deleting the note
restored the three-pair result. Text and JSON exports included the saved groups
and retained context. A fingerprint over the ten measured lap records remained
identical through group and note operations.

Evidence and the native-test handoff are saved outside the repository at
`%LOCALAPPDATA%/YourPitBoxRelease/5.3.1/tablet-groups/20261003T042833Z/`.
The fixture session is `ses_c53c05a2e528d113275541f0`; `handoff.json` identifies
the groups and retained non-excluding note for subsequent native checks.

The installed QA package was pulled back from the tablet, and its bytes matched
the signed QA APK: **73,136,521 bytes**, SHA-256
`8480d8cc4d57adac76f24d42c594989e6a4b9756fa5d1246192d12daaef8253c`.
Native QA screen interaction was not completed: attempts to foreground the QA
activity returned to the production application. Further native controls were
stopped, and the QA backend was shut down gracefully after its API/browser
checks. This is not evidence of a native QA visual or Android system-export
pass. The production installation and its active recording remained separate;
the subsequent in-place production upgrade is recorded below.

### Scheduled native follow-up — 3 October, 03:46 EDT

The requested one-time follow-up started at **07:46 UTC**. The tablet was not
connected to ADB over USB or wireless debugging. Initial discovery retained
the previous Samsung advertisement at `192.168.12.189:41045`, but connecting
was refused (Windows error 10061). A subsequent bounded TCP attempt timed out,
and two ICMP probes received no replies. Restarting the host ADB server and
refreshing discovery produced no connected devices or advertised ADB services.
These observations establish an unavailable debugging connection; they do not
establish an application, Wi-Fi or Bluetooth defect.

No tablet inputs, synthetic telemetry, application restart, installation or
data mutation occurred during this attempt. Native Setup Lab selection and
scrolling, Test Engineer editing and Android's system report-export flow remain
**unverified**, rather than passed. The existing QA fixture and signed APKs
remain available for resumption. Reconnect USB, or wake/unlock the tablet and
enable wireless debugging on the same Wi-Fi, before resuming these checks.
Connection evidence is retained at
`%LOCALAPPDATA%/YourPitBoxRelease/5.3.1/tablet-native-20261003T074617Z/connection-preflight.json`.

## Android CI and signed artifacts

[Android run 37096074694](https://github.com/scottsart1/Pit-box/actions/runs/37096074694)
completed successfully at commit `fb5d56a3944d7ae4e27a1325da26bb64e82481ad`.
The shared-engine and Android-bridge gate passed **1,862 tests** with one
Starlette test-client deprecation warning in **118.61 seconds**. Dashboard and
degradation tests passed **52**, and usage-ingestion/owner-report tests passed
**87**. Real UDP/transfer integration, DOM controls, strategy scrolling,
analysis touch scrolling, all three engineering/Setup Lab browser suites,
native dependency checks, and debug/release/QA APK builds passed.

The fresh Setup Lab browser evidence passed at **1280×800, 800×1280 and
390×844**. Screenshot review confirmed readable source cards and correct
published values, including Australian rotation qualifying brake pressure 98.
The phone layout wraps and scrolls vertically; automated checks found no
horizontal overflow. Early Stable screenshots captured a faint startup fade;
later desktop/tablet rotation screenshots were clear. The wet phone screenshot
was framed around selectors, so clearing of the old recommendation is supported
by DOM assertions, not that screenshot. These capture limitations are retained
in the evidence summary.

The **API 36, x86_64 Pixel C emulator** passed startup with exact engine version
**5.3.1** and schema **5200**, UDP background reception, stationary trace
stability, transfer TLS/invitation/QR checks, listener/process identity
preservation, Connection/Transfer UI controls, fullscreen restoration and
keyboard behavior. It recorded **360 received, 360 parsed, zero rejected**
datagrams, zero receiver/forwarding queue drops, 360 captured packets, zero
capture write errors and zero unclosed-SQLite warnings. Telemetry-state request
times were **0.050 seconds** after launch, **0.071 seconds** after restart and
**2.121 seconds** in the background. These are individual request observations,
not startup-duration benchmarks.

The global crash buffer is **6,179 bytes**, not empty. Its fatal exception is
`UiAutomation: RuntimeException: Bad file descriptor` in PID **4289**.
`logcat.txt` identifies that process as
`com.android.commands.uiautomator.Launcher`; the app processes were **2282** and
**3319**. No fatal exception matched either app process in the captured log,
and the final app health and required UI gates passed. The automation exception
is retained as a tooling limitation rather than reported as an app crash or
omitted from the record. The final stale-telemetry warning is expected after the
synthetic feed stops. Physical Wi-Fi/Bluetooth, live provider/voice calls and
paired-device history copying are outside this emulator check.

Downloaded evidence archives matched their GitHub artifact digests:

| Artifact | ID | SHA-256 |
| --- | --- | --- |
| Engineering/browser acceptance | 11264577045 | `c2c283b096f173ea5f794fa6c3d0c9d1b08d5b94cadd616aff66391095c28473` |
| Android runtime evidence | 11264961142 | `76172e4580a06e83e1b0e2edd2abb2fad1110ed4739481d3792cb0305f578ea0` |

The archives, CI logs, source identity, metrics and visual limits are saved at
`%LOCALAPPDATA%/YourPitBoxRelease/5.3.1/android-ci-37096074694/verification-summary.json`.
The failed initial run remains in its own `android-ci-37095494055` directory.

Owner-local release signing produced the following verified artifacts. Both
use certificate SHA-256
`20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`.

| APK | Bytes | SHA-256 |
| --- | --- | --- |
| Production 5.3.1 revision 33 | 73,136,521 | `aa98ad546c82afc9a5fb1de65f5884374c1019adb5b2ddc1b8b0e6c127085b71` |
| Isolated QA 5.3.1 revision 33 | 73,136,521 | `8480d8cc4d57adac76f24d42c594989e6a4b9756fa5d1246192d12daaef8253c` |

The separate production in-place upgrade and public-download checks are
recorded below.

## Windows CI and frozen artifact acceptance

[Windows run 37096074700](https://github.com/scottsart1/Pit-box/actions/runs/37096074700)
completed successfully at the exact application commit
`fb5d56a3944d7ae4e27a1325da26bb64e82481ad`. Its full suite passed **2,134 tests**,
with one skipped test and one Starlette test-client deprecation warning, in
**627.77 seconds**. Installer construction and all installed-artifact and
counted-telemetry gates passed. The failed initial Windows run remains preserved
separately; it did not supply either final artifact.

The disposable CI runner installed the actual installer, started the frozen
application with isolated data, received real F1 2026 UDP, completed a **25-lap
Monza race**, shut down, relaunched and read the recorded session. Final
classification and **25 strategy snapshots** persisted, with zero reported
telemetry drops. The stress fixture ran in **107.937 seconds** at accelerated
simulation speed; this is not a real-time race-duration endurance claim.
Uninstallation removed the app while preserving existing and newly recorded
data. Transfer-management checks passed before and after restart; the runner
had no usable private LAN, so paired transfer, TLS and QR checks were not
performed by this Windows installed-artifact test.

A separate counted check sent **18,604 datagrams** for **20 cars** over
**100.094 seconds**, with the primary telemetry streams at 60 Hz. All 18,604
were received, parsed and captured. Rejections, capture queue drops/write errors
and full-field archive queue drops/write errors were zero; no API sample timed
out. Health request p95/max were **0.203/1.641 seconds**, and state request
p95/max were **0.265/0.609 seconds**. SQLite integrity and finalized captures
passed after shutdown. This 100-second check is not a full-race endurance test.

All three downloaded CI artifact archives matched the GitHub SHA-256 digests
and identified the expected run and source commit:

| Artifact | ID | SHA-256 |
| --- | --- | --- |
| Installer and portable archive | 11264453518 | `8b6e4882c463f69fd631d4eaf4d944d5bbeb399080861253cd92789220d3816b` |
| Installed-app diagnostics | 11263959257 | `0c9c5836f7d4ad9fe16aff1bdf6cf30f725a01d46be8f5adeae41fe1576cda04` |
| Counted telemetry evidence | 11263929339 | `6d75534b4bda433218b167063bf5bb61a2478f70604ed1db2a5d5f49ad478d0d` |

The downloaded portable runtime was then exercised locally using a new
temporary database and no provider credentials. The real-UDP engineering
fixture and all **three browser suites at 1280×800, 800×1280 and 390×844**
passed against that frozen executable, including exact setup values, separate
rotation race/qualifying profiles, source links, unavailable wet clearing,
delayed circuit selection, group/note edits and exports. Nine new setup
screenshots were inspected: desktop values were readable and portrait headings
wrapped without horizontal clipping. Some portrait screenshots frame only
part of the scrollable pane; the phone wet-state screenshot shows selectors,
so removal of the prior recommendation is verified by browser assertions.

The frozen app became ready in **11.938 seconds** and stopped through its
dashboard API in **11.296 seconds**, exiting with code **0** without a forced
kill. Cold SQLite integrity passed. The final groups and note edits/deletions
matched their API payload, and the saved rotation race reference retained all
**20 settings**, including Melbourne front/rear wings **42/15** and off-throttle
differential **60**. All **three captures**, containing **4,910 packets**, had
clean footers with no temporary capture left. The isolated data root was
removed and no process from the tested executable remained.

The local log was not error-free: two Windows asyncio connection-reset
callbacks (`WinError 10054`) occurred around browser connection closures.
Uvicorn subsequently logged `Cancel 0 running task(s), timeout graceful
shutdown exceeded` at its ten-second network-shutdown bound. Application
shutdown then completed and the exit/data/capture checks above passed. These
messages remain in the evidence; they are not represented as an error-free
shutdown or a forced process termination.

Only after these checks passed were the following files staged under
`%LOCALAPPDATA%/YourPitBoxRelease/5.3.1/`. Both installer and frozen executable
have Authenticode status **NotSigned**; no trusted publisher signature is
claimed.

| File | Bytes | SHA-256 |
| --- | --- | --- |
| `PitWall-Setup.exe` | 34,992,637 | `48848bc8885b69c61118d318b44f4c6aae6906a12abade7e681e25c2a851aafd` |
| `YourPitBox-windows.zip` | 43,758,842 | `aa6b6b3026a6489cfe19b86afb7d296f04a2b2f183fa3c6f68d41206ebfed52e` |

The final record is `windows-final-verification.json` in that release directory.
CI logs and diagnostics are under `windows-ci-37096074700/`; frozen browser
screenshots, runtime logs and cold-data results are under
`windows-frozen-engineering-acceptance-final/`. This verification staged the
files locally and did not install them over the user's Windows application or
publish them. Public-download verification remains separate below.

## Release artifact and production checks

The production app had already stopped after a several-minute telemetry pause
when the installation preflight ran. No active recorder was terminated for the
upgrade. `adb install -r` preserved application data and installed the same
signed production APK listed above. Pulling the installed base APK back from
the tablet confirmed the exact release SHA-256. Runtime health returned
**5.3.1**, schema **5200**, with the OpenAI credential configured and voice ready.
Credentials were not replaced. Native touch checks were left incomplete when
the user moved to another application; no native visual or system-export pass
is inferred from the API/browser checks.

Before/after inventories contain the same **144 session IDs** and all **25,301
lap records** compare identically (excluding only the derived engineering JSON
field). Their normalized SHA-256 is
`7345f5f4de74e6009d67b8ebf1c8cbd1f1ff0047e65f637d5e552b0636578b93`.
The initial strict metadata check flagged one `size_bytes` change, which was
investigated rather than ignored: it increased from **21,275,527** to
**84,626,827** bytes. The exact **63,351,300-byte** difference is the finalized
raw capture, containing **238,425 packets**, with zero incomplete or recovered
captures and no quality warnings. All other session metadata matches apart
from `updated_at`. Evidence is retained in `tablet-history-verification.json`,
`tablet-last-session-quality.json` and `tablet-upgrade.json` in the external
release directory.

Publication used the existing Cloudflare deployment:

- Worker version: `6288879f-670b-4fd1-857b-11af9b2edc91`.
- Pages deployment: <https://b222c832.pitwall-2k7.pages.dev>.
- Public site: <https://yourpitbox.com>.

Both public downloads were fetched in full and matched the local signed APK
and verified Windows installer hashes, sizes, range responses and filenames.
All **18 website routes/assets** matched the local build, allowing only
Cloudflare email obfuscation and newline normalization. Release notices on
both platforms report plain version **5.3.1** and the exact artifact hashes.
Six client checks passed: versions **5.2.0** and **5.3.0** see the update on
Windows and Android, while **5.3.1** reports no newer release. The final website
and release-publishing regression run passed **97 tests**. External evidence is
saved as `production-downloads.json`, `production-site.json` and
`production-updates.json`.

Release artifacts were built from `fb5d56a`; subsequent repository changes only
record this verification, link the current QA document, refine website copy
and publish artifact sizes/hashes. Application code is unchanged from the
verified build.

| Check | Status |
| --- | --- |
| [Initial Windows CI](https://github.com/scottsart1/Pit-box/actions/runs/37095501912) | 2,133 passed, one failed, one skipped; same stale source-format assertion as local full suite; no artifact staged |
| [Initial Android CI](https://github.com/scottsart1/Pit-box/actions/runs/37095494055) | 1,861 passed, one failed, one warning in 137.38 seconds; same stale source-format assertion; browser/APK/emulator gates skipped |
| [Final Windows CI](https://github.com/scottsart1/Pit-box/actions/runs/37096074700) | Passed: 2,134 tests, one skipped, installed 25-lap race and counted 20-car UDP gates |
| [Final Android CI](https://github.com/scottsart1/Pit-box/actions/runs/37096074694) | Passed: 1,862 Python tests, 139 JavaScript tests, three browser suites at three viewports, APK builds and emulator gates |
| Windows installer/archive identity and frozen acceptance | Passed; exact source and GitHub digests, three browser suites at three viewports, cold data and captures verified; unsigned artifacts staged locally |
| Android APK identity and signing | Passed; production and QA hashes/certificate recorded above; both installed packages matched their signed bytes |
| Physical Android native visual/system-export acceptance | Blocked; initial activity contention, then no reachable USB/wireless ADB connection at the scheduled 03:46 EDT follow-up; no native pass claimed |
| Physical Android QA backend/browser acceptance | Passed; nine direct checks and three suites at three viewports; native touch checks separate |
| Production in-place upgrade and data preservation | Passed; revision 33, same 144 sessions and 25,301 laps, finalized-capture storage accounting reconciled |
| Website/public downloads/update metadata | Passed; both full-download hashes, 18 routes/assets and six update-client cases |
