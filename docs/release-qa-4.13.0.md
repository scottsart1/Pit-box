# Your Pit Box 4.13.0 — Lap Lab rival comparisons

Windows published and verified 23 September 2026, 13:53 UTC.
Public download: <https://yourpitbox.com/#download>. Android remains 4.12.4
revision 23; see "Not in this release".

## Scope

Source: `d0a23ae0eeb5e1c66d6f10e991be08cff9274328`.
Website/pins: `6852405282d30427cc4c16f9d1dfb75da39a4802`.

Comparing a lap against rivals in Lap Lab worked once and then failed. The
faults were reproduced on a copy of a real race history (164 sessions, 14,577
trace manifests) at desktop size and in Galaxy Tab emulation before they were
fixed:

- Findings collided with an earlier comparison's findings in the same corner
  (UNIQUE constraint, a 500), and six of eleven session reprocess jobs died
  the same way. Finding ids are now scoped to their comparison.
- The Android WebView had no chrome client, so `confirm()` returned false
  unseen and every comparison against a caveated rival did nothing on the
  tablet. The dashboard no longer asks, and the app shell shows dialogs.
- References whose telemetry could not be read were offered and failed when
  chosen; two laps with no common distance range raised a 500.
- Startup ran VACUUM on every launch and could abort with "database is
  locked"; the Library took 5.8 s per visit without an index (migration 4903).

A raw capture of a real race received over Wi-Fi showed four in five
telemetry ticks lost before reaching the app, whole ticks at a time, while
the receiver kept up (74 percent of datagrams were read back to back). Laps
recorded that way overlap a rival's on 0-40 percent of the track. Comparisons
now headline the official lap-time delta, state how much of the lap lined up,
and draw coaching only from segments both laps measured over half their
length. History's scope picker, Field positions and a missing favicon were
fixed in the same pass.

## Regression checks

- 1,570 application tests and 306 distribution tests (6 skipped) passed in a
  fresh Python 3.12 environment built from the release source; 87 Worker and
  browser Node tests and the five jsdom UI smoke tests, including Analysis,
  passed.
- The frozen executable, started in isolation with a seeded data directory,
  reported 4.13.0, served the new dashboard assets and API fields, and created
  migration 4903's indexes. A replayed four-lap race produced 19 laps; eight
  comparisons against four references, each run twice, returned 201, and all
  four reopened with 200. Every headline delta came from the lap times.
- The installer upgraded an existing 4.12.1 installation silently (exit 0,
  registered as 4.13.0) without launching the app or touching its data. The
  installed executable, started in isolation, reported 4.13.0 with UDP
  listening and served the 4.13.0 dashboard.

The GitHub Windows and Android workflows were not run for this release; the
suites above ran locally instead.

## Publication

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `PitWall-Setup.exe` | 34,210,017 | `84b1fcc4d76c2d3ce9a84df5532a4d61195de85804b2398f0ce0362739b4573a` |

The Worker's `/installer` served exactly these bytes on a ranged read, which
does not count as a download. The versioned copy is
`releases/windows/4.13.0/PitWall-Setup-84b1fcc4.exe`; earlier releases remain.
The Worker source did not change and was not redeployed. Production Pages:
<https://17ea54c9.pitwall-2k7.pages.dev>; `yourpitbox.com` shows Windows
4.13.0 with the checksum above, and the Android card and `/android` still
serve 4.12.4 revision 23 (72,788,107 bytes).

The installer is unsigned, as for every 4.12 release, and the site says so.

## Not in this release

- **Android.** Revision 24 is set in the source, but no APK was built: the
  release keystore is not on the machine this release was cut on, and an APK
  signed with any other key cannot upgrade existing installs. The tablet's
  dialog fix and all Lap Lab fixes reach Android with that APK.
- **Update flags.** The release-management credential was not available, so
  `publish_release.ps1` was not run. Existing installs will not show an update
  flag for 4.13.0 until it is; downloads from the site already get 4.13.0.
- **Physical tablet test.** No Android device was connected over debugging.
