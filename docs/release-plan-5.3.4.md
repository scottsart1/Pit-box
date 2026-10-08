# 5.3.4 release preparation

Status: preparation only. No 5.3.4 artifact, deployment or update publication is
claimed by this document.

## Version and distribution

Use **5.3.4**, Android revision **36**, from reviewed `main`. Public metadata
and download headers checked on 8 October 2026 still report 5.3.2 / revision 34.
A separate signed 5.3.3 / revision 35 candidate exists from
`398808f89de1d9ed684e7b4d9a12f02f135a5831`; reusing that version would make two
different candidates ambiguous. Do not incorporate that branch implicitly.

Continue the existing distribution: an explicitly labelled **unsigned Windows
installer** and an Android APK signed with the existing release identity. The
machine has SignTool but no configured trusted Windows publisher certificate.
The public website already describes the unsigned Windows download accurately.

Version changes include `src/pitwall/__init__.py`, `pyproject.toml`, the dashboard
title/brand and asset query versions in `static/index.html`, and
`androidRevision` in `android/app/build.gradle.kts`. Update its assertion in
`distribution/tests/test_android_project.py`. Historical test fixture versions
and the existing screenshot's 5.3.2 provenance remain historical facts.

## Source and artifact gates

1. Commit the reviewed source on `main`, record its SHA, and run the app and
   distribution suites, JavaScript and Worker tests, affected browser checks,
   and the Android bridge tests. Preserve failed observations and their repair
   evidence. Run version-sensitive tests again after the bump.
2. Build the Windows installer from that commit and test that **exact** artifact.
   The preferred complete gate is manual dispatch of
   `.github/workflows/windows-installer.yml` with `attach_release=false`.
   It runs the full suite, installer startup/UDP/persistence/restart/uninstall
   checks and counted telemetry acceptance. Inspect retained diagnostics and
   download the artifact from the successful run at the recorded SHA.
3. The installer smoke is restricted to a disposable GitHub-hosted Windows
   runner. Its Inno AppId affects uninstall registration even with a different
   install directory. Never spoof its runner checks or run it over the driver's
   existing installation. A local frozen-app check is useful additional
   evidence but does not establish installer/uninstaller behavior.
4. Local Windows compilation is available if needed:
   set `PITWALL_BUILD_DIR` to a fresh directory outside OneDrive, then run
   `.venv/Scripts/python.exe -m distribution.packaging.build --installer`.
   PyInstaller and Inno Setup are present; `--check` passed during preparation.
   Record the installer, ZIP and executable hashes and verify the integrity
   manifest. Keep the public unsigned disclosure.
5. Build the non-debug Android release using the existing key, or prepare it
   with `assembleRelease -Ppitbox.prepareUnsignedRelease=true` and sign locally.
   Use `com.yourpitbox.app`, version `5.3.4-android.36`, code 36, both configured
   ABIs, and the same production certificate. Verify `apksigner`, ZIP alignment,
   package/version metadata, embedded source/static identity and actual runtime.
   Do not publish the debug or unsigned intermediate APK.
6. Physical Android checks must cover the changed audio and dashboard behaviors,
   telemetry, background/restart and saved-data preservation. The debug package
   is isolated; its successful run does not itself establish a production
   upgrade. Retain the original production data and compare history/settings
   before and after any authorized in-place upgrade. Keep the existing 4 KB
   page-size and Bluetooth limitations unless new evidence supports changing
   them. ZIP alignment alone does not establish 16 KB ELF compatibility.
   Complete the signed production tablet upgrade and preservation comparison
   before uploading public artifacts or announcing the update.

The local Android release identity is under
`%LOCALAPPDATA%/YourPitBoxRelease/signing/`, alias `pitbox-release`, certificate
SHA-256 `20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`.
Use the existing DPAPI-protected password through private process environment
variables or an equivalent supported secure input. Do not print it, put it on
a command line, copy it into the checkout or generate a replacement key.

The local debug build recovered the exact pinned native dependency files for
both ABIs from the verified 5.3.2 APK. Its ignored
`android/wheels/RECOVERY-PROVENANCE.json` records the original APK and every
recovered file hash. `android/check-wheels.py` passes. The normal Android CI
workflow can also restore/cross-compile its native wheel cache.

## Existing access and tooling

- Git's noninteractive credential helper authenticates repository access with
  push/admin permission. Keep returned credentials only in process memory and
  send them only to the GitHub API. The Windows workflow is active, ID
  `349671327`; after the commit is pushed, manual dispatch uses
  `POST /repos/scottsart1/Pit-box/actions/workflows/349671327/dispatches` with
  `{"ref":"main","inputs":{"attach_release":"false"}}`. The current PR
  job condition names an older branch, so do not assume a new PR runs this gate.
- Cached Wrangler 4.147.0 is available at
  `%LOCALAPPDATA%/npm-cache/_npx/c7c463f4b2350a3b/node_modules/wrangler/bin/wrangler.js`.
  Invoke it with Node. The inherited `CLOUDFLARE_API_TOKEN` is invalid (401);
  temporarily unset it for the child invocation so Wrangler uses its working
  cached OAuth session, restoring the previous environment afterward.
- OAuth `whoami` passed and reports Worker, Pages and D1 write scopes. It does
  not report R2 scope. Existing `R2_S3_ENDPOINT`, `R2_ACCESS_KEY_ID` and
  `R2_SECRET_ACCESS_KEY` successfully authenticated a signed read of the current
  installer. Use the S3 API with AWS Signature V4, region `auto`, for artifact
  upload; do not assume the OAuth Wrangler R2 command can publish them.
- The update-publication credential exists at
  `%LOCALAPPDATA%/YourPitBoxRelease/release-management/access-key.dpapi`.
  `publish_release.ps1` handles it without printing the value. Presence is
  confirmed; no publication request was made during preparation.

## Publication order and verification

1. Upload the validated Windows installer to
   `pitwall-downloads/PitWall-Setup.exe` and the signed Android file to
   `pitwall-downloads/YourPitBox-5.3.4-android.36.apk`.
2. Update `ANDROID_KEY` in `distribution/activation-server/src/worker.js` and
   its download test. Verify the existing release schema
   (`migrations/0008_release_manifest.sql`) is present before deploying a
   Worker that depends on it. Deploy the Worker from its directory using the
   working Wrangler OAuth session.
3. Fetch the complete public `/installer` and `/android` downloads with
   `Range: bytes=0-` and `Accept-Encoding: identity`. Compare status, exact byte
   count, content range and SHA-256 to the tested local artifacts. A successful
   upload, HEAD request or plausible file size is insufficient.
4. Update website release copy, both checksums, displayed sizes and Android
   filename in `distribution/website/index.html`, plus current release wording
   in the guide. Preserve the Windows unsigned notice, existing Android limits,
   and accurate screenshot captions. Run website/Worker tests and
   `python -m distribution.website.build_site --check`, then build the site.
5. Deploy `distribution/website/_site` to Cloudflare Pages project `pitwall`
   with production branch `main`. Verify actual `https://yourpitbox.com` routes
   against the generated files and record the deployment identifier.
6. Only after artifact and site checks pass, call `publish_release.ps1` once
   per platform with version 5.3.4, its exact artifact and final notes file.
   This helper independently rehashes the public download and checks the live
   site's checksum/version before publishing the in-app update flag. Published
   version metadata is immutable; retries must use identical notes and hashes.
7. Re-read both public `/releases?platform=...` responses, confirm the official
   download links and hashes, and exercise the update service with older and
   current installed versions. Record successful public receipts separately
   from local build and device evidence.

`release_windows.ps1 -AllowUnsignedRelease` contains the existing ordered
Windows publication path, but its inherited invalid Cloudflare token and the
OAuth R2 scope limitation must be handled first. Staged execution provides
clearer artifact gates; the script also catches failures and waits for console
input, so its process exit alone is not a publication receipt.

## Live engineer checks without moving credentials

Forward the already authorized installed app's loopback HTTP port with ADB.
Read `/api/health`, `/api/v1/credentials` and `/api/llm/providers` for version,
masked readiness and bounded diagnostics. Use `POST /api/ask` with
`{"text":"..."}` for a real engineer response; the installed app uses its own
stored key. Compare the answer with that app's current `/api/state` and the
specific request, retaining response and timing without copying credentials.
`POST /api/llm/shakedown` and `/api/realtime/shakedown` are available for explicit
provider/wire-contract checks. A text reply does not establish microphone
capture, speech playback, interruption or headset routing. Test the relevant
native audio flow separately and lock the tablet only after all work finishes.
