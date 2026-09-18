# Release notifications

This source adds an in-app update checker and a subscriber announcement pipeline.
The existing downloadable 4.11.0 artifacts predate it. They cannot be remotely
given a checker: users must manually install a future packaged release containing
this source once. Nothing changes usage-reporting consent or installs updates.

## App checks

The local background task starts after ten seconds, never blocking telemetry
startup. While enabled it requests public Windows/Android release metadata every
six hours. No installation ID or current version is sent. Manual checks are
rate-limited to one per minute. Settings can disable automatic checks or dismiss
one version. Only numerically newer stable versions get a header download link;
the destination is fixed to the official platform section. Offline/error responses
are nonfatal. The cached latest result expires after a day. No auto-installation.

## Publishing (both platforms)

1. Apply additive `0008_release_announcements.sql` before deploying the Worker.
   It does not modify existing subscribers or create campaigns.
2. Provision a separate random `RELEASE_PUBLISH_TOKEN` Worker secret; never reuse
   either read-only dashboard key. Keep its local copy in Windows DPAPI at
   `%LOCALAPPDATA%/YourPitBoxRelease/release-management/access-key.dpapi` or provide
   `PITBOX_RELEASE_PUBLISH_TOKEN` through a protected environment.
3. Complete the normal artifact QA/signing, R2 upload, Worker platform reference
   update and website deployment first. Do not publish a version merely because
   CI built a candidate. Android still uses the existing owner-local signing
   process; its build workflow intentionally does not announce unpublished APKs.
4. Run the shared final publication step from the repository:

   ```powershell
   .\publish_release.ps1 -Platform android -Version <version> -Artifact <signed-apk> -NotesFile <release-notes.txt>
   ```

   Windows `release_windows.ps1` runs this automatically after website publication.
   Android/manual releases must run the same final step after publication. It hashes
   the entire public download using a Range request (excluded from download-start
   counts), compares it to the local artifact, and verifies the public site contains
   the checksum before publishing. A mismatch prevents the notification.
   `-NoEmail` establishes a baseline without sending. Notes are immutable: retry
   with the same file/content. Without a notes file, stable generic release text is
   used. Do not replace a same-version artifact; use a higher version.

## Email activation — deliberately disabled until configured

The owner selected **vale.scott00@gmail.com** as the announcement sender on
18 September 2026. This is a saved sender choice, not an activated integration.
The current adapter uses Resend and a verified sending domain; do not configure
the owner's Gmail address as though that adapter could authenticate gmail.com.
Using this exact From address requires a Gmail-authorized sending integration
and separate server-side credentials. The Gmail connection available to the
assistant is not a reusable credential for the deployed release service.
Complete the owner's Google authorization before enabling unattended sending;
never request the owner's normal Gmail password or extract connector tokens.
Reference: [Gmail server-side authorization](https://developers.google.com/workspace/gmail/api/auth/web-server).

The service does not send without **all** of these Worker secrets:

- `RELEASE_EMAIL_ENABLED=true`
- `RESEND_API_KEY` with send permission
- `RELEASE_EMAIL_FROM`, a verified sender address (address only)
- `RELEASE_EMAIL_FOOTER`, the owner's approved sender/contact/postal information

Setting these is a separate owner setup step; no account, billing, sender identity
or postal address is assumed. Publish while disabled and the manifest still works,
but no email campaign is created. Enabling email later does not retroactively send
old releases. The owner dashboard can explicitly announce the current version using
the separate write-capable key plus confirmation. Loading/refreshing/copying data
never sends a message. The dashboard only reads with the original owner key.

After activation, future publication queues eligible release-news subscribers once.
Windows and Android signup sources receive their own platform announcement;
unspecified older sources are eligible for either platform. Review contacts and
anonymous downloaders are never added. Each recipient gets a separate email.
One campaign snapshots recipients once; retries do not add later subscribers.
The scheduled sender handles up to ten recipients every five minutes and rechecks
unsubscribe, active membership and the current release immediately before sending.

Emails have an unsubscribe link and one-click unsubscribe headers. GET displays a
confirmation without changing preferences; POST suppresses future mail and removes
the active subscription. Suppression persists even if the address is later entered
in the generic download form. Rejoining currently requires an explicit owner-assisted
consent check, not a silent resubscription.

Provider idempotency keys and immutable payloads protect retries. Ambiguous sends
older than 23 hours become `uncertain` and stop retrying before the provider's
24-hour idempotency window expires. Investigate these in the provider before any
manual recovery. `accepted` means the provider accepted the message, **not** inbox
delivery, open or click. Delivery/bounce webhooks and open/click tracking are not
implemented. The dashboard reports queue states without claiming those metrics.

For rollback, disable `RELEASE_EMAIL_ENABLED`, restore Worker/Pages code, and leave
the additive tables intact. Do not delete send history or reset uncertain states
to force a resend. The release schema must remain for unsubscribe suppression.

References: [Resend email API](https://resend.com/docs/api-reference/emails/send-email),
[idempotency](https://resend.com/docs/dashboard/emails/idempotency-keys).

## Implementation verification — 18 September 2026

- 73 Worker/website/owner/update-UI Node checks passed, using mocked mail delivery.
- 92 targeted website/publication-gate Python checks passed; the five native
  update-service/API tests also passed separately. Static site build passed.
- The full native suite was stopped at approximately 62% after low-disk errors.
  A focused transfer-test rerun confirmed its existing free-storage guard was
  refusing staging. C: had approximately 17–19 MB free. This is **not** a passing
  full-suite result. No storage guard was weakened to make tests pass.
- A distinct release-management secret was provisioned and saved privately with
  restricted Windows permissions. No email-provider credentials or sender were
  configured. No message was sent and no campaign was queued.
- Worker/Pages deployment, migration 0008 and initial release manifests remain
  pending. Existing public app artifacts and website functionality are unchanged.
  Do not treat this source commit as a released native build.

Resume after freeing disk space: rerun the full native suite; apply migration 0008;
deploy the Worker and website; run the final publication step with `-NoEmail` for
both existing 4.11.0 artifacts to establish a baseline; verify owner access and zero
campaigns. A new, version-bumped native release is required to distribute the checker.
Configure and verify the owner's chosen sender before enabling real subscriber mail.
