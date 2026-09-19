# Your Pit Box 4.12.1 — Strategy scrolling hotfix

Published and verified 19 September 2026, 03:23 UTC (18 September locally).
Both downloads: <https://yourpitbox.com/#download>.

## Scope

The Strategy grid switched to one column at 1180px, but its own columns waited
until 960px. Its radio/Decision log rail also retained desktop row-spanning and
sticky positioning on small screens. The rail could cover the scrolling plans.

Both breakpoints now agree. Phones/tablets use normal document order and page
scrolling. Only wide, sufficiently tall desktop windows keep the sticky rail;
its height accounts for the header, tabs and footer. Short desktop windows scroll
normally. No coaching, telemetry, privacy, voice or saved-setting behavior changed.

## Checks completed before packaging

- Source: `e3727ffe36150dff066b38cbcf90c7b0904a0205`.
- Real isolated Edge layout tests: 20 cases at ten phone/tablet/desktop sizes,
  each with empty and populated radio/log. Covers 960/961 and 1180/1181 boundaries,
  short landscape, normal card order, no overlap, no sideways page overflow,
  moving hero/rail bounds and an unobstructed Decision log control.
- The same test reproduces the bug with the previous release's stylesheet.
- 46 focused Python tests and 17 onboarding Node tests passed locally.
- Android pre-build gate: 1,580 engine/bridge tests, real UDP/transfer integration,
  DOM smokes, dashboard tests, 20 Chromium layout cases and Worker tests passed.
- Distribution preparation: 85 website/publisher Python and 87 Node tests passed.

CI: [Windows](https://github.com/scottsart1/Pit-box/actions/runs/35417584931),
[Android](https://github.com/scottsart1/Pit-box/actions/runs/35417586454).

The in-app browser connection failed before initialization; layout testing used
an isolated headless browser with real shipped HTML/CSS and no user profile.
Backend scripts are suppressed in the layout fixture; separate engine/native
checks cover startup and telemetry. No provider-audio or new physical race
endurance result is claimed.

## Packaging and publication

- Windows CI: 1,848 tests passed, one skipped. Installed startup, real UDP,
  25-lap synthetic race, restart, database integrity and data-preserving uninstall
  passed. The counted 100-second run received, parsed and wrote all 18,604 packets:
  no rejects, queue drops, write errors or sampled timeouts. Health/state P95 both
  0.297 s; max 1.641/1.156 s. This is not a new physical full-race endurance test.
- Android CI: native build and emulator startup, stationary/background UDP,
  transfer controls and restart passed. No unclosed SQLite warnings in final logs.
- Both signed Android APKs use existing certificate SHA-256
  `20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`.
  Public package `com.yourpitbox.app`, version code 20, non-debuggable. Public/QA
  APKs contain 79 identical engine/native/static payload entries; frontend source
  matches with Windows/Linux line endings normalized.
- Physical Samsung SM-X930: QA package upgraded without uninstall/data clearing.
  After dismissing setup, a UI-tree-derived swipe moved the radio heading from
  y=2541 to y=1927, scrolled the recommendation offscreen, and exposed the Decision
  log. Its Refresh control was tapped successfully. Screenshots were inspected.
  The backend served the exact fixed CSS. QA then shut down cleanly and its test
  ADB forward was removed. The normal app/history was not changed.
- Isolated frozen Windows laptop run: startup, defaults, update endpoint, exact
  frontend assets, real UDP parser fixture and clean shutdown passed. All 20
  real-browser layout cases also passed against the packaged Windows assets.
  The regular installed Windows app was not replaced.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `PitWall-Setup.exe` | 34,732,662 | `e09c20d697ab0974e02c8d1ffd6b2e7903376cbf796d61aee907ff649bed1d5a` |
| `YourPitBox-4.12.1-android.20.apk` | 72,788,107 | `cc44e0c0cfa10023bf78b3ee129ec4d8ce391a24c0ab8ac2a7c3845e520360d2` |

Android's disclosed 4 KB page restriction is unchanged. Windows remains the
approved unsigned installer. No security protection was disabled.

Website/Worker source: `2f970548a5e5f0e211f7fb014c190efeaf6ac1fa`.
Worker version: `a46ed2d1-ae81-40d7-b2ce-26447a942667`.
Production Pages: <https://4e0089f7.pitwall-2k7.pages.dev>.
The deployment's only untracked checkout file was this QA receipt; website and
Worker source were committed unchanged. Existing bindings, schedule and owner
controls remain. No database migration or email automation was introduced.

R2 retains both the versioned APK and Windows backup
`releases/windows/4.12.1/PitWall-Setup-e09c20d6.exe`. Previous releases remain.
Full-range anonymous reads verified every public byte, MIME, size and range
support without counting QA as downloads. Custom-domain checks verified versions,
checksums, guide, Discord and static assets. Both update records were published
last: the actual update service reports an available update for simulated 4.12.0
and none for 4.12.1, on both platforms. Updates remain manual.

## Local evidence and storage

Sibling `release-4.12.1` holds CI logs/evidence, signed artifacts, layout/device
screenshots and verification JSON. Archives were downloaded/extracted in memory
to avoid duplicate disk copies. About 0.49 GB remains free, below the unchanged
2 GiB capture reserve: free space before recording on this laptop. Nothing was
deleted. This does not affect public hosting or other users' installations.

Mark's cross-session practice goals/progress remain a separate, approved next
feature; no claim of persistent coaching was added in this emergency patch.
