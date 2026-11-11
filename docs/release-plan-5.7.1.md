# 5.7.1 release plan

Android only: **5.7.1**, Android revision **43**, versionName
`5.7.1-android.43`, from `main`. Windows stays on 5.7.0. No release PC is
needed: every step runs in GitHub Actions.

## One-time setup

The `release` environment (Settings → Environments) holds the signing key and
the update-notice token. The Cloudflare secrets `CLOUDFLARE_API_TOKEN` and
`CLOUDFLARE_ACCOUNT_ID` are used as they are; the token needs Pages, Workers
scripts and R2 edit permission.

| Secret | Value |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | The release keystore under `%LOCALAPPDATA%\YourPitBoxRelease\signing\`, base64-encoded |
| `ANDROID_KEYSTORE_PASSWORD` | Its store password |
| `ANDROID_KEY_PASSWORD` | The password of the `pitbox-release` key |
| `RELEASE_PUBLISH_TOKEN` | The release-management token (`%LOCALAPPDATA%\YourPitBoxRelease\release-management\access-key.dpapi`) |

Never create a new key. Android installs an update only over an app signed by
the same key, and the workflow refuses any APK whose certificate SHA-256 is
not `20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`.

## Order

1. **Release commit** ("Release 5.7.1 (Android revision 43)") on `main`: the
   version, `androidRevision`, the asset cache keys in `static/`, the README
   and these notes. It does not change what anyone downloads.
2. **Upload.** Run "Release Android" on `main` with stage `upload`. It builds
   and signs the APK, verifies the certificate, package and version code, and
   uploads `YourPitBox-5.7.1-android.43.apk` to `pitwall-downloads`. Its
   summary gives the size and SHA-256.
3. **Switch the download.** One commit on `main` sets `ANDROID_KEY` in
   `distribution/activation-server/src/worker.js` (and the copy in
   `distribution/activation-server/tests/downloads.mjs`) to the new APK and
   puts Android 5.7.1, its file name, size and SHA-256 on the website. Merging
   it deploys the Worker ("Deploy download service", which first checks the
   APK is in the bucket) and the website ("Deploy website").
4. **Announce.** Run "Release Android" with stage `notify`, the SHA-256 from
   step 2 and the notes below. It checks the download and the website serve
   that APK, then publishes the in-app update notice. Published metadata is
   immutable: a retry must use identical notes.

Suggested notes:

> 5.7.1 lays the app out for phones: one header row, readable tabs, and DRIVE
> in race order (lap and position, the call, the tyres, the radio, then the
> field). Settings, the Library and the strategy log fit the screen. Install
> it over 5.7.0; your history and settings are kept.
