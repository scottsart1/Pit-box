# Android opening animation and account unlock

Android revision 44 adds a native 2.6-second pit-light introduction, followed by
an email/four-digit PIN gate when account rollout is enabled. The interpreter now
starts on the service thread after native authorization so first-run Python
unpacking cannot block the opening animation. Slow-start diagnostics remain
available underneath the mask. Reduced-motion devices get a static title.

## Rollout status

**The account gate is staged, not enabled in public builds. Do not enable or
publish account-required APKs until email verification/recovery and physical
Android vault/lifecycle acceptance pass.** The default Gradle property
`pitbox.accountGate=false` preserves access for existing users. An already
enrolled installation always requires its account even in a gate-disabled build.
The animation, encrypted local key storage and authenticated loopback dashboard
are active independently of the rollout flag.

The connected Cloudflare account currently rejects Email Sending access. The
account routes fail closed with 503 unless all three bindings below are present.
No live verification email has been sent or delivery validated. Cloud account
rows are not imported from newsletter signups or old anonymous usage.

## Cloudflare preparation

Use the existing `distribution/activation-server` Wrangler project:

1. Apply `migrations/0009_app_accounts.sql` to `pitwall-licenses` before deployment.
   This is additive and can safely run more than once.
2. Provision `ACCOUNT_PEPPER` as a cryptographically random secret (at least 32
   random bytes); keep it stable. Rotating it without a migration invalidates
   account lookup, registered devices and PIN hashes. Never commit its value.
3. Authorize a transactional sending domain and a restricted `ACCOUNT_EMAIL`
   Email Sending binding. Set `ACCOUNT_FROM` to its verified sender. Example
   configuration after that sender is validated:

   ```toml
   [vars]
   ACCOUNT_FROM = "verify@accounts.yourpitbox.com"

   [[send_email]]
   name = "ACCOUNT_EMAIL"
   allowed_sender_addresses = ["verify@accounts.yourpitbox.com"]
   ```

   This example is not proof that this sender exists. Keep the existing Worker
   bindings, secrets, download routes and cron schedule intact.
4. Deploy the tested Worker and website. `/owner/accounts` is authenticated by
   the existing **owner** key, never the download-report key.
5. Test real delivery to an owner-controlled mailbox, wrong-code handling, PIN
   recovery, second-device enrollment and rate limiting. Do not use customer
   addresses for fixtures.
6. Build an isolated candidate with `-Ppitbox.accountGate=true`. Verify the
   native flows below, then enable the flag in the signed release workflow.
   Do not simply flip the default while existing build/release jobs are untested.

## Account contract

Only native HTTPS POSTs without a browser Origin are accepted. Bodies are
bounded to 2 KiB and fields are allowlisted.

- `/account/challenge`: email + 256-bit install secret; sends a six-digit,
  ten-minute code. At most three sends per 15 minutes and ten per day/address.
- `/account/verify`: verifies that email code for this install, sets a four-digit
  PIN on a new account, or registers another device with the same existing PIN.
  Choosing a different PIN is a verified-email reset that revokes older devices.
  Codes allow five guesses and are consumed atomically once.
- `/account/unlock`: email + PIN + registered install secret. Global atomic D1
  counters allow five failed attempts per 15 minutes, twenty per day/address,
  plus an IP budget. Ordinary successful starts release their PIN reservation.
- Successful verification/unlock returns only the random account ID. There is
  no persistent cloud bearer token or cached PIN. Network failures keep the
  gate closed. Internet is required to open an account-enabled app.

D1 stores HMAC email lookups, peppered salted PIN verifiers and hashed random
install secrets. The email itself is used transiently for delivery. The pepper
must remain separate from D1. A four-digit PIN is not a strong password by
itself; the registered-device secret, email verification and server limits are
part of this design.

## Key storage and local access

All five provider key types, including Moonshot/custom aliases, migrate from the
Android private `.env` into AES-256-GCM entries under a non-exportable Android
Keystore key. Each entry authenticates its setting name as additional data.
Migration verifies encrypted writes before atomically removing assignments;
no plaintext backup is made and vault errors stop startup. Other `.env`
settings/comments and all race history stay intact. Credential changes use the
vault thereafter; desktop `.env` behavior is unchanged.

The service refuses cold starts without in-process authorization. A fresh
process requires unlock again; a running race may continue in the background
while returning to the UI requires a new PIN. Gate inputs are not instance-state
saved or autofilled, and the account screen prevents screenshots/task previews.

Android's localhost HTTP and WebSocket routes require a random process token,
including health and static files. The native WebView gets an HttpOnly cookie;
native health/export requests get a header. Same-origin checks remain enforced.
The debug package writes a test token in its private directory for `adb run-as`
QA. Production builds compile out that write and have no public bypass.

This protects stored key files and ordinary app/API access. It does not promise
protection against a rooted device, malicious code in the app process, an
unlocked race session, or an OS-level compromise. Keys must exist in memory
while the engineer calls providers. The PIN is not used as an encryption key,
and API keys never go to the account service.

## Account analytics

A separate unchecked opt-in shares successful starts using a random account ID,
a launch UUID, Android version and server time. Requests retrying the same launch
UUID cannot inflate counts. No API key, PIN, raw email, telemetry, voice or
newsletter membership enters the starts table. Turning sharing off stops future
start reporting; existing starts expire after 180 days. Account authentication
metadata persists independently of that analytics retention.

The private owner page shows 30-day totals and up to 200 recent accounts with
starts, active days, first and last start in the retained interval. This is not
an all-customer or lifetime metric. Existing anonymous-installation reports and
newsletter lists remain separate.

## Acceptance and rollback

Local automated coverage includes real SQLite execution of the Worker account
SQL, concurrent guessing, code expiry/replay/device binding, recovery and device
revocation, opt-out, duplicate launches, report authorization and retention;
Python coverage checks migration preservation/failure and HTTP/WebSocket gates.

Before release, verify on Android: first install; upgrading with existing keys;
all provider save/delete flows; wrong PIN; offline startup; interrupted enrollment;
background/resume with an active race; permission/file-picker round trips;
rotation and process death; reduced motion; phone/tablet/folded layouts; and
Keystore loss. Existing emulator backend tests exercise the rollout-disabled
path, not real email delivery or the full account-enabled path.

Do not roll enrolled users back to an APK that predates encrypted storage: it
cannot read their saved keys. A safe rollback retains the vault/native gate and
only changes the animation or rollout to new installations. If access must be
recovered, preserve race history and require verified-email recovery; never
silently bypass enrolled-account authentication.
