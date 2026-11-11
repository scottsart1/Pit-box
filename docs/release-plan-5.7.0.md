# 5.7.0 release preparation

Status: preparation only. No 5.7.0 artifact, deployment or update publication is
claimed by this document.

## Version and distribution

Use **5.7.0**, Android revision **42**, from reviewed `main`. The release commit
("Release 5.7.0 (Android revision 42)") bumps `pyproject.toml`,
`src/pitwall/__init__.py`, `androidRevision` and its assertion, the asset cache
keys in `static/`, the APK the Worker serves (`YourPitBox-5.7.0-android.42.apk`
in `distribution/activation-server/src/worker.js`), the website and the README,
and adds `docs/release-notes-5.7.0.md`. Download sizes and checksums are not in
it; they are published after the build, as for 5.6.0.

Continue the existing distribution: an explicitly labelled **unsigned Windows
installer** and an Android APK signed with the existing release identity.

The database moves to schema **5700** on first start, after a backup. Nothing
needs to be run by hand. 5.6.0 cannot open the upgraded database.

## Before building

5.7.0 adds modules the frozen builds must carry (`race_simulator`,
`weather_picture`, `circuit_model`, `tyre_limits`, `strategy_review`). The
PyInstaller build and the installed-artifact smoke run only on Windows, so
before step 2 run the "Build Windows installer" workflow manually on `main`
(leave `attach_release` off): it runs the full suite, builds the installer
and smoke-tests the installed app. On Linux the app suite and the
distribution tests pass, except the one that needs PyInstaller installed.

## Order

Android first. `release_windows.ps1` deploys the Worker, which from then on
serves `YourPitBox-5.7.0-android.42.apk` from `/android`, so that file must
already be in R2. Run everything in PowerShell from the repository folder.

Steps 0, 1, 4 and 6 are as in `docs/release-plan-5.6.0.md` (the Worker URL,
the bucket names and the signing certificate are unchanged); the version
specific commands follow.

## 2. Android APK

versionCode 42, versionName `5.7.0-android.42`.

1. `.\.venv\Scripts\python.exe android\check-wheels.py` must pass for
   `arm64_v8a` and `x86_64`.
2. Set the signing variables in the process environment only, as for 5.6.0.
   Never pass them on a command line, and never create a new key.
3. `cd android; .\gradlew.bat assembleRelease "-Ppitbox.buildPython=<python3.13>"; cd ..`
4. `apksigner verify --verbose --print-certs` must show v2/v3 signatures, the
   release certificate SHA-256 recorded in `docs/release-plan-5.6.0.md`,
   package `com.yourpitbox.app`, versionCode 42.
5. Copy it to `YourPitBox-5.7.0-android.42.apk`. Record the size and the
   lowercase SHA-256.
6. Recommended: upgrade a test tablet in place from 5.6.0 and compare
   sessions and laps before and after; then drive a race lap and open
   Session Analysis for it.
7. Upload:
   `npx --yes wrangler r2 object put pitwall-downloads/YourPitBox-5.7.0-android.42.apk --file "<apk>" --content-type application/vnd.android.package-archive --remote`

## 3. Windows release

`powershell -NoProfile -ExecutionPolicy Bypass -File .\release_windows.ps1 -AllowUnsignedRelease`

As for 5.6.0: record the installer's size and SHA-256 it prints, expect its
last step ("Publish verified metadata for the in-app update flag") to fail
until step 5, and do not re-run the script to finish.

## 5. Checksum commit

In `distribution/website/index.html`, update the Windows installer size and
lowercase SHA-256, and the Android APK size and lowercase SHA-256. Then:

```powershell
.\.venv\Scripts\python.exe -m pytest distribution/tests/test_website.py -q
.\.venv\Scripts\python.exe -m distribution.website.build_site --check
git commit -am "Publish verified 5.7.0 download checksums"
git push
```

## 7. Publish the in-app update flags

```powershell
.\publish_release.ps1 -Platform windows -Version 5.7.0 -Artifact "$env:LOCALAPPDATA\PitWallBuild\artifacts\PitWall-Setup.exe" -NotesFile <windows-notes.txt>
.\publish_release.ps1 -Platform android -Version 5.7.0 -Artifact <YourPitBox-5.7.0-android.42.apk> -NotesFile <android-notes.txt>
```

Notes files are plain text, at most 4,000 characters. Published metadata is
immutable: a retry must use identical notes and the same file.

Suggested notes, for both platforms:

> 5.7.0: the stop for inters is called on the rain that is falling and said
> once; rivals' stops are called at pit entry with the arithmetic to cover
> them; plans are judged against safety cars and rain, and the call for a
> safety car is ready before it comes out; Session Analysis shows every
> strategy call beside your stops. The database is upgraded on first start,
> after a backup; 5.6.0 cannot open it, and history transfers need both
> devices on 5.7.0.

## 8. Verify

The Worker's `/releases?platform=windows` and `?platform=android` must report
5.7.0 with the right size and SHA-256. Keep both checksums with the release
record.
