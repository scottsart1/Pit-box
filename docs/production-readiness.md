# Production release gates

Windows 4.10.1 is published as an owner-authorized unsigned direct download,
with its SHA-256 and security-warning notice on the website. Android revision
14 is published as a directly downloadable release-signed APK, not a Play
Store submission. Android support is limited to 4 KB memory-page devices;
16 KB devices are unsupported because some embedded native libraries do not
satisfy ELF alignment requirements. Do not claim broader compatibility.

## Candidate changes

### Owner acceptance — 17 September 2026

The owner has accepted Windows and Android operation for release after their
manual checks and requests beta labelling for telemetry transmission. Keep
manual acceptance separate from reproducible automated results: prior test
failures remain in the evidence, not silently changed to passes. Public website
copy now labels UDP forwarding and device-to-device history transfer as beta.

The owner explicitly authorized unsigned Windows direct download and creating
a permanent Android release key. Both downloads are published. Android signing
material is protected outside the repository. An unsigned-preparation opt-in
lets CI build a non-debug APK without seeing that key; local signing, signature
verification and exact public-download checksum checks are complete. Keep the existing debug
app and its history: the public release uses the separate production package.
Website availability must describe the artifacts actually served.

The release CI run passed 1,526 source/bridge tests plus integration and
sibling-debug emulator checks for startup, UDP, restart and background/transfer
UI. These checks are not physical-race endurance or runtime testing of the
exact locally signed release APK. Public Windows/APK downloads passed checksum,
size, MIME and partial-content checks; all 65 final website tests passed.

### Implemented source changes

- History transfers expose preparation, send/receive, verification and import
  progress. Sender completion does not imply receiver import completion.
- A legacy portable export can recover a finalized session's close timestamp
  from existing canonical metadata without rewriting the source database.
- Optional post-race narration runs outside the UDP consumer, with a bounded
  task count, timeout, captured finish-state and session-aware publication.
- Live assembly retains finalized lap metadata for invalidation, not a second
  copy of archived sample groups. Sink deliveries retain the complete batches.
- Automatic capture names include a unique suffix, preventing collisions when
  Windows returns identical clock values across session boundaries.
- The UDP consumer yields after each 32-packet batch, including malformed
  packets, so a continuously nonempty queue cannot monopolize the event loop.
- Website copy distinguishes released Windows/Android downloads, Android
  compatibility limits and beta transfer features. There is no automatic cloud sync.

Large-history export preparation and transfer timeouts remain documented beta
limitations. Progress reporting is not a throughput fix. Document small batches
and retry of the existing job rather than representing arbitrary-size transfers
as reliable; retain prior evidence alongside the owner's acceptance.

## Windows

1. Build reviewed source in a clean checkout. Run the app and distribution test
   suites. Record the commit, dependency environment, actual binary version and
   SHA-256 of the EXE, ZIP and installer.
2. Provision an existing trusted publisher certificate in the build account's
   Windows certificate store. Set `PITBOX_WINDOWS_CERT_SHA1`,
   `PITBOX_SIGNTOOL_PATH` and a credential-free HTTPS RFC 3161 endpoint in
   `PITBOX_TIMESTAMP_URL`. Keep private keys in the protected certificate store
   or approved signing service, not in this repository or command arguments.
3. Run `python -m distribution.packaging.build --installer`. Unsigned output is
   allowed for candidate checks and an explicitly authorized unsigned direct
   release. For publisher-signed releases the EXE must be signed before
   its integrity manifest is generated, and the finished installer signed too.
4. Verify both artifacts with `verify-production-signature.ps1`: a valid
   trusted signature, the expected publisher and a timestamp are required.
5. Run the installed-artifact smoke on a disposable Windows runner, including
   install, UDP, full synthetic race, persistence, restart and uninstall/data
   retention. Do not run the installer smoke on a driver's existing install.
6. Separately verify real first-run, microphone/output selection and a full
   real-time race on the signed candidate. Synthetic accelerated races do not
   establish voice or endurance quality.

The release script rejects unsigned artifacts by default. An owner-approved
`-AllowUnsignedRelease` exception accepts unsigned files, not invalid or
tampered signatures, and requires transparent website disclosure. Never disable
firewall/antivirus/Smart App Control for distribution. CI release attachment
still requires publisher signing; a thumbprint alone supplies no certificate.
See [Microsoft SignTool](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool).

## Android

1. Build with JDK 17 or 21 and the configured Android toolchain. Produce and
   verify the pinned CPython 3.13 wheels for both arm64-v8a and x86_64 before
   assembling. See [Android build instructions](../android/README.md).
2. Source targets API 36. Recheck the current
   [Google Play target requirement](https://developer.android.com/google/play/requirements/target-sdk)
   and [Android 16 behavior changes](https://developer.android.com/about/versions/16/behavior-changes-16)
   before submission; changing the target does not prove runtime compatibility.
3. Validate 16 KB page-size compatibility of every native library, including
   Python extensions packaged inside Chaquopy assets, plus APK ZIP alignment.
   Run on a 16 KB device/emulator as described in the
   [Android page-size guide](https://developer.android.com/guide/practices/page-sizes).
   A ZIP-alignment success alone does not establish ELF or runtime compatibility.
4. Configure the existing backed-up release key via `PITBOX_KEYSTORE_PATH`,
   `PITBOX_KEYSTORE_PASSWORD`, `PITBOX_KEY_ALIAS`, `PITBOX_KEY_PASSWORD` in the
   private build environment. Release builds reject incomplete signing unless
   explicitly preparing an unsigned artifact with
   `-Ppitbox.prepareUnsignedRelease=true` for owner-local signing. Such an
   unsigned artifact is never a public download.
   Never generate a replacement key to bypass an upgrade-signature mismatch.
5. Run the API 36 emulator workflow and physical-device tests for Wi-Fi,
   pause/resume, flashbacks, race finish, app background/screen-off, permission
   refusal/grant, microphone capture, speaker output and supported routing.
   Bluetooth headset microphone support is not yet a release claim.
6. Record PSS, capture/archival write errors, UDP queue drops and API latency
   over a complete real-time race and cooldown, then repeat with retained
   history. Check that growth plateaus and the process stays responsive.
7. Verify upgrade and history preservation. The debug package is separate
   from the release package, and debug certificates can differ across CI runs.
   Do not uninstall an existing app to overcome signing errors. Export and
   verify recovery first, keeping the original installation/data available.
8. For Play, additionally prepare the signed AAB, listing, privacy/data-safety
   disclosures and track rollout. For direct download, use a verified signed
   APK with a checksum and accurate compatibility/installation instructions.

## Website and deployment

- Preserve the existing website architecture and download flow. Candidate
  copy must remain explicitly labelled until the matching binaries pass.
- Revoke any credential disclosed in chat or logs. Put replacement credentials
  in an approved secret store/CI secret, scoped to only the required bucket
  and action. Never put Cloudflare tokens or S3 secret keys in frontend code,
  downloadable artifacts, source control, reports or release transcripts.
- Review account, bucket, object keys, permissions and retained rollback
  artifacts before an authorized deployment. An R2 object-write token does not
  by itself authorize or enable Worker, website or DNS changes.
- Publish the verified installer first, then verify the public download's
  actual bytes and SHA-256 before updating the website. Add an Android
  download link only when the signed APK at that URL is verified.
- Keep a known-good artifact and website revision for rollback. Preserve all
  recording databases, captures, history and signing identities across rollback;
  test schema compatibility rather than blindly downgrading a populated database.
- Final release approval requires recorded artifact/runtime evidence and an
  explicit publishing decision, not a green build alone.
