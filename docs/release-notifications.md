# In-app update flag

Scope: **Update available flag only. No email automation.** The discarded email
sender, queue, campaign tables, dashboard send controls and five-minute email
schedule are absent. Existing newsletter signups, copy-all emails and analytics
are unchanged. No Gmail authorization or email-service credentials are required.

## App behavior

The background check starts ten seconds after launch without blocking telemetry.
It requests public Windows/Android version metadata every six hours while enabled.
Settings provide Check now, automatic-check toggle and dismissal of one version.
The header offers an update link only for a numerically newer stable version.
The official platform download section is the only accepted destination.
There is no automatic download, installation or interruption of driving.

Checks send only the platform, not the current version, installation ID, telemetry
or API keys. Network failures are nonfatal; manual requests are coalesced/cooldown
limited, responses are size/time bounded, and cached metadata expires after a day.
This is independent of optional usage reporting, whose consent is unchanged.

## Publication

Apply additive `distribution/activation-server/migrations/0008_release_manifest.sql`
before deploying the Worker. It creates only release metadata/channel tables; it
does not alter subscribers or their history. Public `GET /releases?platform=windows`
(or `android`) exposes only version, notes, publication time, checksum, size and
official download link.

A separate `RELEASE_PUBLISH_TOKEN` protects `POST /release-admin/publish`.
Both existing read-only reporting keys are rejected for publication. The owner's
DPAPI copy is at
`%LOCALAPPDATA%/YourPitBoxRelease/release-management/access-key.dpapi`;
never embed it in the apps or website.

Finish artifact QA/signing, R2 upload, the Worker's Android version reference and
website deployment first. The final step from the repository is:

```powershell
.\publish_release.ps1 -Platform android -Version <version> -Artifact <signed-apk> -NotesFile <notes.txt>
```

Windows `release_windows.ps1` performs that final step automatically. Android
release publication uses the same helper; candidate CI builds do not announce
themselves. The helper hashes all public download bytes using a Range request
(excluded from download-start counters), compares them to the local artifact,
and confirms the production website advertises that checksum before publishing.
No subscriber data is read or changed, and no mail is sent.

The channel cannot be downgraded; a published version's metadata is immutable.
Use identical notes on retries. Without a notes file, stable generic release text
is used. Replacing a same-version artifact is unsupported: increment the version.

## Delivery boundary

**4.12.0 update:** both new public binaries now contain the checker. Their
download bytes, website checksums and published metadata have been verified;
see [release QA](release-qa-4.12.0.md). The older baseline history below remains
for reference.

The existing downloadable 4.11.0 binaries predate this checker. They cannot acquire
it remotely. A new packaged release must include this source; users need that one
manual upgrade before subsequent releases can show the flag. Publishing backend
metadata alone does not update installed applications.

## Verification status

The earlier broad native run was interrupted because the laptop had only about
17–19 MB free; an isolated transfer test confirmed the existing low-storage guard
was blocking staged transfers. The guard was not changed. Do not represent that
run as passing. On the latest recheck, free space was still about 24 MB, so only
bounded update/service/website checks are appropriate until space is available.

The release write credential was already provisioned. The abandoned email schema
was never applied and no email configuration, customer message or campaign was
created. See the publication receipt below when backend deployment is completed.

Flag-only checks: 69 Node tests passed across the Worker, owner dashboard,
download/analytics pages and update UI. Another 19 Python tests passed for the
native checker/API, verified publication tool and Windows release ordering.
Website static checks and build passed. This does not replace packaged-device QA.

## Backend publication receipt — 18 September 2026

- Source: `8e725a1e7dbeded5614a92f5c290e5290af48cd7`.
- Additive migration `0008_release_manifest.sql` applied successfully. Only the
  release/channel tables were added; subscriber data was not changed.
- Worker version `e5720395-431d-4c04-86e4-4522c32eea70` deployed. Its only scheduled
  job remains the original daily usage-retention cleanup; there is no email job.
- Final Pages deployment: `https://f859ba14.pitwall-2k7.pages.dev`, production
  branch main, serving `https://yourpitbox.com`. An initial identical-assets
  deployment had an incorrect commit-reference label; this final deployment
  corrects that label to the full source revision above.
- Published baseline 4.11.0 metadata for both platforms after hashing every byte
  of their public Range downloads against the existing local release artifacts:
  Windows 34,709,580 bytes / SHA-256
  `c98eeaad90056b0eb8b538e6846c43ec05ce0c94e5eba9efaea3589e22302159`;
  Android 72,775,518 bytes / SHA-256
  `ab56cd50dac845d3ff99bdea329934f149bf45c16cc08091a353e8fe3c55d1fe`.
- Read-only live checks using the new app service and simulated installed versions
  confirmed: 4.10.4 gets an available flag; 4.11.0 gets no flag, on both platforms.
  These are source-service checks, not claims about already-installed binaries.
- Public terms include the update-check explanation; the owner page retains its
  copy-all feature and has no send controls. Served owner JavaScript matches the
  local file byte-for-byte. Removed email/unsubscribe routes return 404.
- The existing Windows installer and Android APK were **not** replaced, rebuilt
  or installed. Their one-time upgrade to a build containing the checker remains
  pending sufficient laptop storage and packaged-app verification.
