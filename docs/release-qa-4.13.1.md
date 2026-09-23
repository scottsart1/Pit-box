# Your Pit Box 4.13.1 — One flag rule for every car

Windows published and verified 23 September 2026, 19:30 UTC.
Public download: <https://yourpitbox.com/#download>. Android remains 4.12.4
revision 23 until the 4.13.1 APK is signed with the release key; see "Not in
this release".

## Scope

Fixes: `e69817c`. Release: `954c196`. Website/pins: `c98904f`.

Checked against a copy of a real history (164 sessions) and a recorded race,
4.13.0 still judged the field and the player by different rules:

- A rival's lap was flag context whenever any marshal zone showed any flag,
  anywhere on track, including the green shown for a few seconds after a
  yellow clears: 20 percent of the field's laps. The player's live laps were
  never marked, so a lap slowed for a yellow was compared as strict. Every
  car is now judged the same way: a safety car, VSC, formation lap or red
  flag, or a yellow or red shown to that car. The recorded race showed the
  game reports each car's own flag, and most cars showed none while a yellow
  was out elsewhere.
- Laps imported from the legacy table were flag context for being invalid.
  Migration 4904 repairs the player's catalogued laps from the legacy
  record; on the real history 35 neutralised laps had read as clean and 13
  invalid laps as flagged, and the player's flagged laps went from 78 to 100.
- The Library and Field summary showed "Unavailable" quality for every
  session and named sessions by track number. Both now show the mean lap
  quality and the circuit. The Library still lists 50 real sessions in
  0.08 s.
- A changed reference left the previous comparison's status on screen.
- Comparison, trace and single-lap analysis work ran on the event loop that
  reads UDP: 40-60 ms per comparison of two densely recorded real laps on a
  desktop. It runs on worker threads.

## Regression checks

- 1,582 application tests, 306 distribution tests (6 skipped), 110 Worker,
  page and driver-dashboard Node tests and the five jsdom UI smoke tests,
  including Analysis, passed from the release source. Each new regression
  test fails on 4.13.0.
- The frozen executable, started in isolation, reported 4.13.1 on schema
  4904 and served the 4.13.1 dashboard assets. A replayed four-lap race
  produced 90 laps from 20 cars; none of the 86 rival laps was flag context
  (every one would have been under 4.13.0, since the replay shows green in
  every zone). Of 89 references for the player's lap, 41 were strict. Eight
  comparisons against four references, each run twice, returned 201 with
  their traces; single-lap analysis and the Field summary answered; the app
  shut down with exit code 0.
- The installer upgraded the existing 4.13.0 installation silently (exit 0,
  registered as 4.13.1) without launching the app; `PitWallData` was
  unchanged. The installed executable, started in isolation, reported 4.13.1
  with UDP listening and served the 4.13.1 dashboard.

## Android build

GitHub Actions run
[#42](https://github.com/scottsart1/Pit-box/actions/runs/35909632654) built
`c98904f`: its checks passed, and its isolated debug APK passed startup, UDP,
restart and background reception on the API 36 tablet emulator. The unsigned
release APK is `com.yourpitbox.app` versionCode 25, versionName
`4.13.1-android.25`, for arm64-v8a and x86_64.

## Publication

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `PitWall-Setup.exe` | 34,212,114 | `23fcb3459484676518c643a555d17cd12abaf8d789da998d701a4fc82b726f8b` |

The Worker's `/installer` served exactly these bytes on a ranged read (206),
which does not count as a download. The versioned copy is
`releases/windows/4.13.1/PitWall-Setup-23fcb345.exe`; earlier releases remain.
The Worker source did not change and was not redeployed. Production Pages:
<https://60898e5f.pitwall-2k7.pages.dev>; `yourpitbox.com` shows Windows 4.13.1
with the checksum above, and the Android card still serves 4.12.4 revision 23.

The installer is unsigned, as for every 4.12 and 4.13 release, and the site
says so.

## Not in this release

- **Android.** The APK is built but not yet signed: the release keystore was
  not found at the location recorded by the earlier signing script, and an
  APK signed with any other key cannot upgrade existing installs. It must
  carry certificate
  `20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`.
- **Update flags.** The release-management credential was not available, so
  `publish_release.ps1` was not run; downloads from the site already get
  4.13.1.
