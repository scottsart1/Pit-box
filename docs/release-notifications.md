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
