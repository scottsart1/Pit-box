# Driver profiles

Every launch of Your Pit Box, on Android and on a computer, opens on a
driver-profile screen. Each driver on a device has their own race history,
settings, dashboard layout and API keys, opened with either an email address or
a 4-digit PIN. A profile stays open until the app closes.

## The same on both platforms

- **No profiles yet** (fresh install, or the first launch after updating from a
  build without profiles): *Set up your driver profile*. When the installation
  already has history, settings or keys, the screen says that everything
  already on the device will be attached to this profile. The driver enters a
  name and chooses **Email OR PIN**; a PIN is typed twice.
- **One profile**: *Welcome back, Name* opens straight to that driver's email
  or PIN field.
- **Two or more**: *Choose your driver* lists each driver with how it opens,
  plus **+ New driver**.

Email has no verification and no outbound mail. It is an identifier, not proof
of ownership: anyone who knows the address can open that profile, and the setup
screen says so. A PIN is an app lock: five wrong PINs lock the profile for 15
minutes, twenty in a day lock it until the next day, and the counters survive
restarts because each attempt is reserved before it is checked. PINs are
verified with salted PBKDF2-HMAC-SHA256 (120,000 iterations) and are never
encryption keys. A forgotten PIN cannot be reset. Names are unique per device
(case-insensitive), an email can open only one profile, and a device holds up
to 20 profiles.

The first profile created inherits everything stored before profiles existed;
every other profile gets its own folder, `.env`, database, captures, transfer
identity and pairings. A process opens exactly one profile: the backend's
settings, database and caches are created once per process from that
profile's folder and never switched in place. CONNECTION shows the open driver
with **Switch driver**, which stops the backend the way Quit does, so the
session is saved and the capture finalized.

Wi-Fi history transfers copy only the open profile's history. A reinstall
loses local credentials and creates new reporting identities; using the same
email on another device does not link or recover local data.

## Android

### Opening

A 2.6-second native opening (five pit lights, the wordmark and a progress
line) covers the profile screen while its encrypted index loads. Reduced-motion
users get a static version. The intro and the profile screen use no WebView and
no Python; the interpreter starts on the backend thread once a profile is open.

### Profile screen

The screen is native. With one profile the keyboard comes up on its field;
Next moves name → PIN → confirmation (or name → email) and Done submits.
Strings are localized (English, German, Spanish, French). Controls carry
resource ids (`gate_name`, `gate_credential`,
`gate_confirm`, `gate_submit`, `gate_method_pin`, `gate_method_email`,
`gate_other_driver`, `gate_new_driver`, `gate_notice`, `gate_retry`,
`gate_reset`) for accessibility tools and the emulator acceptance check.

The profile index, with the PIN verifiers, is encrypted under the Android
Keystore. Android 7 has no PBKDF2-HMAC-SHA256, so it uses PBKDF2-HMAC-SHA1 with
600,000 iterations and records the algorithm so OS upgrades preserve access.

### Separate local data

The first profile owns the existing private `files/` workspace, so an update
keeps the existing `.env`, settings, race history, transfer identity and
pairings. Later profiles use `files/profiles/<random-id>/`, each with its own
`.env`, `PitWallData`, SQLite history, setups, logs, captures, pairing
credentials and dashboard assets. Dashboard and onboarding preferences use
per-profile native preference files. Provider keys use a separate vault
namespace per additional profile; the first keeps the existing entries. Names
and email addresses are stored only in the encrypted index. Android backup
remains disabled.

The dashboard's WebView is created only after a profile opens. On Android 9 and
later each additional driver gets its own WebView data directory (local
storage, cookies, cache); the first profile keeps the existing one, so its DRIVE
card layout, app-tour state and other browser-side preferences survive the
update. On Android 7 and 8 the platform offers no per-process WebView data
directory, so those browser-side preferences are shared between drivers; they
hold layout and UI state only, never history or keys.

**Switch driver** saves the session and closes the app; reopening shows the
profile screen. Leaving the app, turning the screen off or rotating it does not
ask for the profile again; if Android recreates the activity while the process
lives, the open profile continues without a second opening animation or
sign-in.

### Key storage and the dashboard boundary

Provider keys migrate from the profile's private `.env` into AES-256-GCM
entries under a non-exportable Android Keystore key. Migration verifies each
encrypted write before removing plaintext assignments; errors stop startup and
leave the file untouched.

All Android localhost HTTP and WebSocket routes, including health and static
assets, require a random per-process token: the WebView gets it as an HttpOnly,
SameSite=Strict cookie and native requests send `X-Pitbox-Native`. Debug builds
also write it to app-private storage for the emulator checks (readable only
through `adb run-as` on the debug package). The peer-transfer listener is a
separate TLS server and is unaffected.

The profile screen sets `FLAG_SECURE` (no screenshots or task-switcher
preview), never autofills or restores typed values, and clears a half-typed
PIN when it leaves the screen.

#### When secure storage fails

If the encrypted index cannot be read (for example the Keystore key was lost or
invalidated), the profile screen says so and offers **Try again** and **Reset
sign-in data**. Reset, after a confirmation, deletes the vault entries (profile
index, queued sign-ins, provider keys) and the Keystore key. Files are not
touched: the first profile's history and settings attach to the next profile
created; additional drivers' folders remain on the device but can no longer be
opened. Provider keys must be entered again.

## Desktop

The Windows installer (`distribution/main.py`) and the source install
(`start_pitwall.bat`, `python -m pitwall.desktop`) both open the profile screen
in the default browser, on the dashboard's address, from a small server in
`pitwall.desktop`. That server binds `127.0.0.1` only, answers only requests
whose `Host` is `127.0.0.1` or `localhost` on its port, and accepts profile
POSTs only as same-origin JSON of at most 4 KiB, so another site cannot use it
through the browser. It runs before anything reads settings; once a driver
opens a profile it hands its address to the dashboard, the backend starts in
that profile's folder, and the same tab shows the dashboard. A second launch
while the screen or the dashboard is open just opens it in the browser.
`python -m pitwall.main` remains the development entry point, with no profile
screen.

The installer's one-time welcome window still comes first; the API key typed
there is saved once a profile is open, into that profile's settings.

### Folders

The first profile keeps `%USERPROFILE%\PitWallData` and the `.env` in the app's
start folder. Each other driver uses
`%USERPROFILE%\PitWallData\driver-profiles\<random-id>`, which holds their
`.env` and their whole data folder; the app starts in that folder so settings
and keys are read and written there. The profile list and queued sign-ins are
`driver-profiles\profiles.json` and `driver-profiles\sign-ins.json`.
Nothing in the backend walks the data folder recursively (captures and traces
sweep only their own subfolders), so another driver's folder is never touched
by the first driver's session.

These files belong to the driver's Windows account. A PIN keeps the app from
opening another driver's profile; it does not encrypt anything a person signed
in to that account could open directly. An unreadable profile list offers
**Try again** and **Reset sign-in data**, which renames the list aside
(nothing is deleted) so profiles can be created again; the first driver's
folder attaches to the next profile created.

### Switching

**Switch driver** asks the backend (only from the computer itself) to save the
session and stop; the launcher then starts the app again with its original
environment and folder, and the profile screen returns on the same address.
The dashboard tab waits for it and shows it. If another app took the address
in the meantime, the screen opens on a free port in a new tab.

### Browser storage

Browser-side preferences (DRIVE card layout, app tour, onboarding) live in the
browser's storage for the dashboard's address, which all drivers on the
computer share. They hold layout and UI state only, never history or keys.

## Sign-in records

Every successful opening queues one record before the profile opens (Android:
in the encrypted vault; desktop: in `driver-profiles\sign-ins.json`): the profile's random account ID, a random event UUID, the device
time and the app version. No email, PIN, driver name, key, voice, telemetry or
gameplay data is included. The device credential is a random 256-bit value,
not derived from the email or PIN. Records are sent in the background as soon
as the profile opens and then every minute while the app runs; network
failures never delay opening. The version names the platform
(`5.7.1-android.43`, `5.7.1-windows`). Android debug builds (development and
the CI emulator) queue records but never send them, and the Windows installer
smoke points `PITWALL_PROFILE_REPORT_ENDPOINT` at a closed local port, so the
report counts only releases and QA builds.

Delivery rules:

- `200` acknowledges the event (including duplicates and events older than the
  retention window, which the server acknowledges as expired).
- `429`, `408`, `5xx` and network errors keep the event queued and end that
  attempt, so an outage costs one request per minute.
- Other `4xx` answers would repeat on every retry, so the event is dropped
  rather than blocking later ones. Events whose profile no longer exists, and
  malformed entries, are dropped too.
- At most 500 events wait on a device; the oldest are dropped first.

The server hashes the device credential with a separately stored HMAC secret,
deduplicates by event UUID, keeps device event time and receipt time
separately, rejects timestamps more than five minutes in the future and keeps
records for 180 days. These are client-reported sign-ins, not verified unique
people or evidence of gameplay.

`/owner/accounts` requires the owner key. It shows 30-day aggregate profiles
and sign-ins, a daily breakdown, and the latest 200 profiles with first and
last sign-in, active days and counts. Newsletter data and the optional
anonymous usage report stay separate and are not joined.

## Cloudflare contract

Native HTTPS POSTs only: no browser Origin, no query string, bounded 2-KiB JSON
bodies and strict field allowlists.

- `/account/profile`: `account_id`, `device_secret`. Idempotent registration
  that cannot replace an existing credential; twenty new profiles per IP per day.
- `/account/sign-in`: the same credential plus `event_id`, `occurred_at`,
  `version`. A missing or wrong credential cannot write another profile's
  sign-ins.
- Both routes reject email, PIN, name and API-key fields. Combined requests
  have a 300-per-IP per 15-minute budget.

Migrations `0009_app_accounts.sql` and `0010_driver_profiles.sql` are applied to
`pitwall-licenses`; the 0009 tables other than `account_limits` are unused and
kept so the migration history stays additive. The `ACCOUNT_PEPPER` Worker secret
holds 32 random bytes. Without it the endpoints answer 503 and events stay
queued on devices. Deploy the Worker and owner page with the app release.

## Acceptance

Desktop: `tests/test_driver_profiles.py` and `tests/test_desktop_profile_screen.py`
cover the store, lockouts, the sign-in queue, the screen's HTTP boundary, the
hand-over and the switch endpoint. The Windows installer smoke creates a PIN
profile on the installed app's first launch (and checks the existing-data
notice), opens it by PIN on the second, and checks the queued sign-ins hold
only their four fields.

Android: the emulator smoke (`android/emulator-smoke.py`, run by the Android workflow)
drives the real profile screen through the accessibility tree:

1. Fresh install: create a PIN profile; the backend opens the installation's
   own storage (`files/`, not `files/profiles/`).
2. Restart: the single profile opens straight to its PIN; same database and
   transfer identity.
3. Background and foreground return without the profile screen.
4. Add an email driver: a separate database under `files/profiles/` and a new
   transfer identity.
5. Restart with two drivers: choose the first by name and enter its PIN; its
   database and transfer identity return unchanged.

`distribution/tests/java/ProfileStoreCheck.java` runs on the JVM with the APK
build (`testDebugUnitTest`): credentials, persistent lockouts, duplicate
names and emails, the profile limit, namespaces, WebView data directories,
upgrade detection, the bounded queue, delivery outcomes and the one-profile-
per-process boundary.

Still checked on a physical device before release: updating an installed
release with history and keys (callout shown, everything attached), rotation
and folded layouts during setup, Android 7/8, TalkBack on the profile screen,
and offline sign-in delivery after reconnecting.

Do not roll a device with profiles back to an APK without profile isolation or
encrypted key storage: the older build cannot read the encrypted keys.
