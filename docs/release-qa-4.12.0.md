# Your Pit Box 4.12.0 — published 18 September 2026

Both downloads, website and update metadata were verified at 22:35 UTC:
<https://yourpitbox.com/#download>.

## Changes

Skippable key/UDP/L3/Mark setup, a concise 11-stop tour, and new-install defaults
of proactive calls on, all-car race scope and full trace detail. Existing choices
remain authoritative. Privacy and App updates are at the bottom of Settings.
The packaged apps now include the update checker. Updates stay manual; there is
no email automation and usage consent is unchanged.

## Verification

- App artifact source: `8578a33ab46896d5d66f8b3e375404b55e4b674c`.
- Website/Worker source: `09756d429bfbe55126960daa8027e6d40b86e3b3`.
- [Windows CI](https://github.com/scottsart1/Pit-box/actions/runs/35400442266):
  1,844 tests passed, one skipped. Installer lifecycle, 25-lap synthetic race,
  SQLite integrity, persistence/restart and uninstall/data retention passed.
  The counted stress run received, parsed and recorded all 18,604 packets,
  with no rejects, queue drops, write errors or sampled timeouts. Health P95
  0.406 s; state P95 0.359 s.
- [Android CI](https://github.com/scottsart1/Pit-box/actions/runs/35400444429):
  1,577 engine/bridge tests passed. UDP/transfer integration, DOM controls,
  dashboard and Worker regressions passed. The sibling debug APK passed
  emulator startup, exact version, stationary trace, background UDP, transfer
  UI/TLS/QR and restart checks.
- Final distribution changes passed 85 website/publisher Python tests and
  87 Node tests. A test previously assumed Android 4.12.0 was unpublished;
  it now derives a mismatched version from the pinned APK. Production guards
  were not relaxed.
- A separate frozen Windows laptop run verified defaults, update API, real
  UDP parsing, shipped frontend assets and clean shutdown, using isolated data.
  The normal installed Windows app was not replaced.
- Android v2/v3 signatures use the existing certificate SHA-256
  `20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`.
  Public package `com.yourpitbox.app`, version code 19, non-debuggable. Public
  and signed QA APKs have 79 byte-identical engine/native/static payload entries.
  Source text comparisons normalize Windows/Linux line endings; the initial
  literal comparison failed on that difference and was not counted as a pass.
- The Samsung SM-X930 received the signed QA upgrade without clearing data.
  Physical UI checks covered the three setup steps, skips, invitation, all
  11 tour stops, completion, reopening and skipping both guides. Step 2 showed
  the real tablet IP and retained QA port 20787. Screenshots confirmed shorter
  copy and accessible controls. Privacy and App updates appeared at the bottom.
  The native checker successfully read published 4.12.0 metadata. No key was
  supplied or voice call made; the existing debug app/history was untouched.

Browser automation could not initialize; no automated desktop-browser visual
pass is claimed. These checks do not claim new provider-audio testing, physical
full-race endurance, or Android 16 KB runtime support.

## Public artifacts and deployment

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `PitWall-Setup.exe` | 34,725,253 | `e80b247d8ff15b0da11abf0a0a3ae75501f0863e1c7d51ec261bdd935432f4b0` |
| `YourPitBox-4.12.0-android.19.apk` | 72,788,107 | `16e8f1d5bc93ed73a20f35ea5eab0f61e167c927e9ae748def8eda03e243f211` |

Windows remains the owner-authorized unsigned direct installer. Android retains
the disclosed 4 KB page restriction; seven native libraries are not 16 KB
compatible. The signing identity and compatibility requirements are unchanged.

R2 stores the versioned APK, current Windows installer and immutable backup
`releases/windows/4.12.0/PitWall-Setup-e80b247d.exe`. Older releases remain.
Worker version: `918429e5-a60e-4726-a21c-db6a4f03be7e`.
Production Pages: <https://293af3b7.pitwall-2k7.pages.dev>.
Existing bindings and daily retention schedule remain; no migration was needed.

Anonymous full-range downloads hashed every public byte and matched both local
artifacts. MIME, sizes and short-range requests passed without adding test
download counts. The custom-domain versions, checksums, guide, Discord link and
static assets passed. Existing owner controls remain; email routes return 404.
Only after these checks were both 4.12.0 update records published. Source-service
checks show a flag for simulated 4.11.0 and none for 4.12.0 on both platforms.
Old 4.11.0 binaries need one manual upgrade to acquire the checker.

## Local evidence and storage

Sibling `release-4.12.0` retains logs, CI evidence, physical screenshots, signed
artifacts and verification JSON. Temporary-build cleanup was blocked by the
execution tool; nothing was deleted. Approximately 1.3 GB remained free, below
the unchanged 2 GiB capture reserve. Clear space before recording on this laptop.
This does not affect public hosting or installations on other devices.
