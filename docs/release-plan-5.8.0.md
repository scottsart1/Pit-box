# 5.8.0 release plan

**5.8.0** for Android and Windows: Android revision **44**, versionName
`5.8.0-android.44`, from `main`. The database schema is unchanged (5700).

## One-time setup: Android signing in GitHub Actions

"Release Android" signs in GitHub Actions once the `release` environment
(Settings → Environments → `release` → Environment secrets) holds:

| Secret | Value |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | The release keystore, base64-encoded (below) |
| `ANDROID_KEYSTORE_PASSWORD` | Its store password (below) |
| `ANDROID_KEY_PASSWORD` | The password of the `pitbox-release` key; the same value unless it was set separately |
| `RELEASE_PUBLISH_TOKEN` | The release-management key (`release-access.txt`, or decrypted below) |
| `CLOUDFLARE_RELEASE_API_TOKEN` | Already set |

On the release PC, each command below copies one value to the clipboard to
paste into the secret. The `.dpapi` files are Windows-account-bound secure
strings, so only the account that wrote them can read them.

```powershell
$dir = "$env:LOCALAPPDATA\YourPitBoxRelease"
[Convert]::ToBase64String([IO.File]::ReadAllBytes("$dir\signing\yourpitbox-release.p12")) | Set-Clipboard
[Net.NetworkCredential]::new('', (Get-Content "$dir\signing\password.dpapi" | ConvertTo-SecureString)).Password | Set-Clipboard
[Net.NetworkCredential]::new('', (Get-Content "$dir\release-management\access-key.dpapi" | ConvertTo-SecureString)).Password | Set-Clipboard
Set-Clipboard -Value ' '
```

The last line clears the clipboard. Never create a new key: Android installs
an update only over an app signed by the same key, and the workflow refuses
any APK whose certificate SHA-256 is not
`20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`.

## Order

1. **Release commit** ("Release 5.8.0 (Android revision 44)") on `main`: the
   version, `androidRevision`, the asset cache keys in `static/`, the README
   and these notes. It does not change what anyone downloads.
2. **Android upload.** Run "Release Android" on `main` with stage `upload`.
   It builds and signs the APK, verifies the certificate, package and
   version code, and uploads `YourPitBox-5.8.0-android.44.apk` to
   `pitwall-downloads`. Its summary gives the size and SHA-256.
3. **Windows upload.** Run "Release Windows" on `main` with stage `upload`.
   It runs the full suite, builds and smoke-checks the installer, uploads it
   as `PitWall-Setup.exe` and confirms the public download serves it. Its
   summary gives the size and SHA-256. Windows has one download name, so
   visitors get 5.8.0 from this point.
4. **Download commit.** One commit on `main` sets `ANDROID_KEY` in
   `distribution/activation-server/src/worker.js` (and the copy in
   `distribution/activation-server/tests/downloads.mjs`) to the new APK and
   puts Windows and Android 5.8.0, with their sizes and SHA-256s, on the
   website. Merging it deploys the Worker and the website.
5. **Announce.**
   - Android: run "Release Android" with stage `notify`, the SHA-256 from
     step 2 and the notes below.
   - Windows: run "Release Windows" with stage `notify`, the SHA-256 from
     step 3 and the notes below.

   Published metadata is immutable: a retry must use identical notes.
6. **Verify.** The Worker's `/releases?platform=windows` and
   `?platform=android` report 5.8.0 with the right size and SHA-256, and a
   sign-in on each platform appears on the owner dashboard.

Suggested notes, for both platforms:

> 5.8.0 adds driver profiles. On first start, set up your profile with a name
> and an email address or 4-digit PIN; everything already saved is attached
> to it. Each further driver gets their own history, settings and keys, and
> Switch driver on CONNECTION changes driver. The app is ready sooner after
> the PIN. History transfers with 5.7.x work as before.
