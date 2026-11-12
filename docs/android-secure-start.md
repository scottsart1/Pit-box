# Android driver profiles and secure startup

The native opening masks the old startup screen with five pit lights, the Your
Pit Box wordmark and a progress line for 2.6 seconds. Reduced-motion users get a
static version. Python initialization stays off the UI thread.

## Driver setup and access

On first opening a profile-enabled build, the driver chooses a display name and
**either email or a four-digit PIN**. Email has no verification or outbound mail.
Returning drivers select their profile and enter its email or PIN. Additional
drivers use **New driver**. Email addresses and PINs never leave the device.

Email is only a local identifier: anyone who knows the address can open that
profile. The app says this explicitly. It does not verify identity or ownership
of the address. PIN-only profiles provide an additional app lock with five
attempts per 15 minutes and twenty failures per day. The verifier uses salted
PBKDF2-HMAC-SHA256 (120,000 iterations; Android 7 uses PBKDF2-HMAC-SHA1 with 600,000 iterations
and records the algorithm so OS upgrades preserve access), inside the Keystore-encrypted profile
index. PINs are never encryption keys. There is no forgotten-PIN recovery or
cross-device email account matching. A new profile does not unlock another
profile's data. Twenty local profiles are supported.

## Separate local data

The first profile owns the existing private `files/` workspace, preserving the
existing `.env`, settings, race history and key migration. Later profiles use
`files/profiles/<random-local-id>/`, each with its own `.env`, `PitWallData`, SQLite
history, setups, logs, captures, pairing credentials and dashboard assets.
Dashboard layouts and onboarding flags use profile-specific native preferences.
Provider secrets have a separate vault namespace for each additional profile;
the first retains the existing encrypted entries. Names and email addresses are
stored only in the encrypted local index. Android backup remains disabled.

A process can authorize only one profile. Returning from the background requires
that profile's credentials while telemetry may keep running. The CONNECTION
page's **Switch driver profile** action stops the backend normally so its session
can save, then closes the process. Reopen to choose another driver. Selecting
another driver from the lock screen also requires this shutdown. The process
never hot-swaps Python's database/configuration singletons. Browser caches,
cookies and local storage are cleared before the first dashboard load in a new
process, preventing the prior driver's browser state from carrying across.

This provides separate local backend stores, not cloud history storage. Explicit
Wi-Fi transfers still copy only the active profile's history. A reinstall loses
local credentials and creates a new reporting identity; matching the same email
on another device does not link or recover local data.

## Key storage and dashboard boundary

Provider keys migrate from private `.env` into AES-256-GCM entries using a
non-exportable Android Keystore key. Migration verifies encrypted writes before
removing plaintext assignments; errors stop startup. Desktop behavior remains
unchanged. All Android localhost HTTP and WebSocket routes, including health and
static assets, require a random process token. The native WebView gets an HttpOnly
cookie. The debug-only private QA token is never written in release builds.

Keys stay on the device except calls to the chosen AI provider. This is not a
promise against rooted devices, malicious app-process code, or someone who knows
an email-only profile's address. An active engineer needs decrypted keys in
memory. PIN fields are not autofilled or saved in activity state; the lock screen
blocks screenshots and task previews.

## Sign-in records

Sign-in reporting is standard, with no opt-in checkbox. Every successful local
credential acceptance queues one record before opening.
The app briefly discloses that sign-in records use a random profile ID, time and
app version, and explicitly states that email and PIN are not sent. This includes
successful authentication whose screen is subsequently interrupted. Wrong
credentials are not sign-ins and do not create a success record.

The encrypted durable queue retries every minute while its process is alive and
after the next successful opening. Network outages do not prevent local access.
Records are removed only after the server acknowledges them. No email, PIN,
driver name, key, voice, telemetry or gameplay data enters the payload. The
registration credential is a random 256-bit value, not derived from credentials.

The server hashes that random credential with a separately stored HMAC secret.
An event UUID prevents retries from increasing counts. Reports use device event
time, keep server receipt time separately, reject timestamps over five minutes
in the future, and retain 180 days. Older queued events are acknowledged as
expired so they cannot block later events. Clearing app storage before upload
loses that queue. These are client-reported sign-ins, not verified unique people
or evidence of gameplay. The service cannot attest local authentication on a
modified or compromised app.

`/owner/accounts` requires the existing owner key. It shows 30-day aggregate
profiles/sign-ins and the latest 200 profiles with first/last sign-in, active days
and counts over the retained period. Newsletter data and optional anonymous
product usage stay separate. Reinstalls and profiles on another device have
separate reporting IDs.

## Cloudflare contract and rollout

Native HTTPS POSTs only; no browser Origin, no query string, bounded 2-KiB JSON
bodies and strict field allowlists:

- `/account/profile`: `account_id`, `device_secret`. Idempotent registration;
  cannot replace an existing credential. Twenty new profiles per IP per day.
- `/account/sign-in`: the same credential plus `event_id`, `occurred_at`, `version`.
  A missing/incorrect credential cannot write another profile's sign-ins.
- Both routes explicitly reject email, PIN, names and API keys. Combined requests
  have a 300-per-IP/15-minute budget. Rate-limited events remain queued.

Apply additive migrations `0009_app_accounts.sql` and `0010_driver_profiles.sql`
to `pitwall-licenses`. Both migrations have been applied during staging; the
0009 account/challenge tables are unused by the current local-profile flow. Keep
them for additive rollout. The stable `ACCOUNT_PEPPER` Worker secret has been
provisioned with 32 random bytes. Deploy the Worker plus owner page after review. No email
sending configuration is required. Preserve all existing download, subscriber,
usage, owner and release credentials and cron configuration.

**Release hold: profiles are staged behind `pitbox.accountGate=false`. Do not
merge or publish a profile-required APK until the enabled native flow, data
isolation, upgrade preservation and sign-in delivery pass acceptance.** Existing
profile indexes always require the profile gate even in a flag-disabled build.
The introduction, encrypted provider storage and localhost boundary operate
independently. Enable an isolated candidate with `-Ppitbox.accountGate=true`.

## Acceptance and rollback

Before release, check first setup with email and PIN; wrong/leading-zero PINs;
lockout surviving restart; duplicate names/emails; two drivers with distinct
keys/settings/history; end/save/switch; explicit history transfer isolation;
background/resume, rotation, permissions, process death; offline sign-in, durable
retry and deduplication; first-profile upgrade ownership; provider save/delete;
phone/tablet/folded layouts, reduced motion and Keystore loss. Check all profile
IDs in the owner report without email, PIN or name exposure.

The HTML preview at `docs/previews/android-profile-opening.html` illustrates the flow;
it is not proof of native lifecycle behavior. Existing emulator backend checks
exercise the rollout-disabled path. Do not roll enrolled profiles back to an APK
that predates profile isolation or encrypted storage.

Local validation: 3,651 app tests passed (4 skipped), 101 Worker/website tests
passed, and 62 Android packaging/network checks passed (2 JDK-dependent checks
skipped). The new native classes compile against Android 36 APIs; the JVM profile
harness covers credentials, persistent lockouts, namespace separation, queue
acknowledgements and the process boundary. APK/emulator and enabled-device
acceptance remain separate release gates.
