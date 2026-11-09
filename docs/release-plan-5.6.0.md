# 5.6.0 release preparation

Status: preparation only. No 5.6.0 artifact, deployment or update publication is
claimed by this document.

## Version and distribution

Use **5.6.0**, Android revision **41**, from reviewed `main`. The release commit
("Release 5.6.0 (Android revision 41)") bumps `pyproject.toml`,
`src/pitwall/__init__.py`, `androidRevision` and its assertion, the asset cache
keys in `static/`, the APK the Worker serves (`YourPitBox-5.6.0-android.41.apk`
in `distribution/activation-server/src/worker.js`), the website and the README,
and adds `docs/release-notes-5.6.0.md`. Download sizes and checksums are not in
it; they are published after the build, as for 5.5.1.

Continue the existing distribution: an explicitly labelled **unsigned Windows
installer** and an Android APK signed with the existing release identity.

The database moves to schema **5600** on first start, after a backup. A
database upgraded by a pre-release 5.6.0 test build (schema 5510) is relabelled
5600 on start; nothing needs to be run by hand.

## Order

Android first. `release_windows.ps1` deploys the Worker, which from then on
serves `YourPitBox-5.6.0-android.41.apk` from `/android`, so that file must
already be in R2. Run everything in PowerShell from the repository folder.

## 0. Source

Merge the reviewed branch into `main`, then on the release PC:
`git checkout main`, `git pull --ff-only`, and confirm `git status` is clean.
`release_windows.ps1` releases only from a clean `main` and fast-forwards only.
`.venv` must exist (`install_windows.ps1`).

## 1. One-time checks

- `function T { npx --yes wrangler @args }; T r2 bucket list` must list buckets,
  not print help (see the 4.9.5 note in `distribution/HANDOVER.md`).
- If `CLOUDFLARE_API_TOKEN` is set but invalid, unset it for the session. If
  the OAuth login lacks R2 scope, upload through the S3 API with R2 keys
  (`docs/release-plan-5.3.4.md`).
- Confirm
  `%LOCALAPPDATA%\YourPitBoxRelease\release-management\access-key.dpapi`
  exists (`publish_release.ps1`, `docs/release-notifications.md`).

## 2. Android APK

versionCode 41, versionName `5.6.0-android.41`.

1. `.\.venv\Scripts\python.exe android\check-wheels.py` must pass for
   `arm64_v8a` and `x86_64`.
2. In the process environment only, set `PITBOX_KEYSTORE_PATH` (the keystore
   under `%LOCALAPPDATA%\YourPitBoxRelease\signing\`),
   `PITBOX_KEY_ALIAS=pitbox-release`, and `PITBOX_KEYSTORE_PASSWORD` and
   `PITBOX_KEY_PASSWORD` from the DPAPI copy. Never pass them on a command
   line, and never create a new key (`android/README.md`,
   `docs/production-readiness.md`).
3. `cd android; .\gradlew.bat assembleRelease "-Ppitbox.buildPython=<python3.13>"; cd ..`
   produces `android\app\build\outputs\apk\release\app-release.apk`.
4. `apksigner verify --verbose --print-certs` must show v2/v3 signatures and
   certificate SHA-256
   `20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`, package
   `com.yourpitbox.app`, versionCode 41. Never publish a debug or unsigned APK.
5. Copy it to `YourPitBox-5.6.0-android.41.apk`. Record
   `(Get-Item <apk>).Length` and
   `(Get-FileHash <apk> -Algorithm SHA256).Hash.ToLower()`; the website and
   `publish_release.py` need lowercase hex.
6. Recommended: upgrade a test tablet in place from 5.5.1 and compare sessions
   and laps before and after.
7. Upload:
   `npx --yes wrangler r2 object put pitwall-downloads/YourPitBox-5.6.0-android.41.apk --file "<apk>" --content-type application/vnd.android.package-archive --remote`

## 3. Windows release

`powershell -NoProfile -ExecutionPolicy Bypass -File .\release_windows.ps1 -AllowUnsignedRelease`

- Not `release_windows.bat`: it passes no switch, and the signing preflight
  stops an unsigned run.
- The script installs dependencies, runs the tests, builds
  `%LOCALAPPDATA%\PitWallBuild\artifacts\PitWall-Setup.exe` and prints
  `Installer: <bytes> bytes, SHA-256 <HASH>`. Record both; lowercase the hash.
- It then uploads to R2, applies the D1 migrations, deploys the Worker
  (`/android` now serves the 5.6.0 APK), verifies `/installer` byte for byte,
  and builds and deploys the site.
- Its last step, "Publish verified metadata for the in-app update flag", is
  expected to fail: `publish_release.py` requires the live site to show the new
  SHA-256, which it cannot until step 5. Nothing is published by that failure.
- Do not re-run `release_windows.ps1` to finish. It would rebuild and re-upload
  the installer, and the new build's hash need not match the one recorded.
- Until step 6 the site shows Windows 5.6.0 with the 5.5.1 checksum, so carry
  on promptly. The run is logged to `release_log.txt`.

## 4. Check the Android download

```powershell
Invoke-WebRequest -Uri https://pitwall-activation.sarthakvij123450.workers.dev/android -Headers @{Range='bytes=0-'} -OutFile $env:TEMP\a.apk -UseBasicParsing
(Get-FileHash $env:TEMP\a.apk -Algorithm SHA256).Hash.ToLower()
```

Size and hash must match the signed APK.

## 5. Checksum commit

In `distribution/website/index.html`, update the Windows installer size
(decimal MB, one decimal place) and lowercase SHA-256, and the Android APK size
and lowercase SHA-256. Then:

```powershell
.\.venv\Scripts\python.exe -m pytest distribution/tests/test_website.py -q
.\.venv\Scripts\python.exe -m distribution.website.build_site --check
git commit -am "Publish verified 5.6.0 download checksums"
git push
```

## 6. Redeploy the site

```powershell
.\.venv\Scripts\python.exe -m distribution.website.build_site
cd distribution\website; npx --yes wrangler pages deploy _site --project-name pitwall --branch main; cd ..\..
```

`--branch main` is required; without it the deploy is only a preview. Confirm
https://yourpitbox.com shows 5.6.0 and both new checksums.

## 7. Publish the in-app update flags

Notes files are plain text, at most 4,000 characters; without `-NotesFile` a
generic line is used.

```powershell
.\publish_release.ps1 -Platform windows -Version 5.6.0 -Artifact "$env:LOCALAPPDATA\PitWallBuild\artifacts\PitWall-Setup.exe" -NotesFile <windows-notes.txt>
.\publish_release.ps1 -Platform android -Version 5.6.0 -Artifact <YourPitBox-5.6.0-android.41.apk> -NotesFile <android-notes.txt>
```

Published metadata is immutable: a retry must use identical notes and the same
file. The helper re-hashes the public download and checks the live site before
publishing.

## 8. Verify

`https://pitwall-activation.sarthakvij123450.workers.dev/releases?platform=windows`
and `?platform=android` must report 5.6.0 with the right size and SHA-256. Keep
both checksums with the release record.
