# Handover — Your Pit Box distribution work

State as of **6 August 2026** on `main`, pushed and in sync with `origin/main`.
Working tree clean.

Read `ARCHITECTURE.md` next to this file for the licensing design. This
document is the practical state: what works, what does not, and what to do
next.

---

## Test state

| Suite | Command | Result |
|---|---|---|
| Everything | `.\.venv\Scripts\python.exe -m pytest -q` | **717 passed** |
| Distribution only | `... -m pytest -q distribution/tests/` | **88 passed** |

Lint: `ruff check` reports pre-existing style across `src/` and `tests/` (there
is no `[tool.ruff]` config, so defaults apply). Nothing new was introduced
except the `# noqa: E402` convention used consistently by all four
`distribution/tests/` files.

---

## What works, verified by running it

### The packaged Windows app

Built and launched end to end on this machine. The dashboard, static assets,
`/api/health`, the credentials API, the network API and the overlay all
responded from the frozen executable, with clean logs.

```powershell
.\.venv\Scripts\python.exe -m distribution.packaging.build --dev
```

`--dev` builds despite preflight failures, for testing the packaging itself.
Without it, preflight refuses to build while the development key or the
placeholder activation endpoint are in place. Output goes to
`%LOCALAPPDATA%\PitWallBuild\` — **deliberately outside the repo**, see below.

### Four bugs found only by launching the real build

Each was invisible in development. If packaging is touched again, these are the
regressions to watch for; all four have tests in
`distribution/tests/test_packaging.py`.

1. **Relative import in the entry point.** PyInstaller runs `distribution/main.py`
   as `__main__`, which has no parent package. Windowed build, no console — the
   app just vanished.
2. **Integrity check hashed `.py` files.** A frozen build has none. It now
   hashes the executable, and the digest is stamped *after* PyInstaller runs,
   because a file cannot contain its own hash.
3. **uvicorn import string.** `"pitwall.app:app"` cannot be resolved when
   frozen. Passing the app object also surfaces real tracebacks.
4. **`static/` located by walking up from `__file__`.** Different depth in a
   bundle. Three call sites, now one frozen-aware helper.

### Do not build inside the repo

The repo is under OneDrive. OneDrive held handles on the build output while
syncing, so PyInstaller intermittently failed with `PermissionError` — and
because that left the *previous* build in place, the next test run silently
exercised a stale executable. Two "bugs" were chased that had already been
fixed. Override with `PITWALL_BUILD_DIR` if needed, but keep it off any synced
folder.

### The installer

Compiled with Inno Setup 6.7.3 and put through a full install → verify →
uninstall → verify cycle on this machine. `PitWall-Setup.exe`, 33 MB.

What the run proved, rather than assumed:

- Installs silently and exits 0; every one of the 1081 built files lands, and
  the executable is byte-for-byte identical to the build output.
- The integrity manifest ships inside the install and matches what the app
  computes for its own executable at launch. The digest covers
  `basename || 0x00 || bytes`, so it does not change with the install path —
  a buyer installing anywhere gets a matching hash.
- Start Menu and desktop shortcuts are created, and it registers per-user in
  Add/Remove Programs under HKCU with no administrator prompt.
- The uninstaller removes the install directory, both shortcuts and the
  registry entry, leaving nothing behind.
- **`PitWallData` is untouched.** Verified against a real 1591-file, 282 MB
  session store: identical file-for-file before and after, zero differences.

Three defects were found by doing this, all now covered by tests in
`test_packaging.py`:

1. **Inno Setup was never going to be found.** `winget install` run without
   elevation installs per-user into `%LOCALAPPDATA%\Programs` and puts nothing
   on PATH, but `_inno_compiler()` only searched the two Program Files
   directories. Following the documented release steps would have printed
   "Inno Setup not found" — and since `build_installer()` returns None rather
   than failing, the build would have reported success having produced no
   installer.
2. **Preflight blocked the real release build.** It tested
   `shutil.which("pyinstaller")` while the build actually runs
   `sys.executable -m PyInstaller`. A venv invoked by path rather than
   activated has no `pyinstaller.exe` on PATH, so preflight reported
   "PyInstaller is not installed" on the very machine that had just built with
   it. `--dev` masked this completely: the only mode that worked was the one
   that stamps the artifact NOT SELLABLE.
3. **The running-copy guard did not exist.** `InitializeSetup` ran
   `tasklist.exe` and discarded the result, so the check its own comment
   described never happened. Replaced with `CloseApplications=yes` /
   `RestartApplications=no`, which uses Restart Manager and is what the buyer
   actually sees. Also added a `UninstallSilent` guard around the post-uninstall
   message box, which would otherwise hang a silent uninstall forever.

### The activation-key tracker

```powershell
.\.venv\Scripts\python.exe -m distribution.tools.generate_codes --count 50 --issued 2026-08
```

Writes four files to `distribution/ledger/` (gitignored): the ledger JSON, a
plain code list, the D1 seed SQL, and **`activation_keys_*.xlsx`** — the sheet
to work from when someone pays. Three tabs: instructions, the key list with
status dropdowns, and a summary with live COUNTIF formulas and a revenue line.

Formulas are **evaluated and correct**, checked by opening the real batch in
Excel (via COM, on a copy, read-only). Excel loaded it with no repair prompt.
On the 12-code batch the Summary tab read 12 / 12 unused / 0 / 0 / 0 / 0 and
$0. Marking two rows Sold and one Activated moved the counts to 9 / 2 / 1 and
revenue to $60; marking a fourth Replaced left revenue at $60, which is the
intended behaviour — a replacement is not a second sale. The status dropdown
survived the openpyxl round-trip as a real list validation.

### Enforcement of the two rules you asked for

- **One-time use** — the Worker claims a code atomically in D1
  (`UPDATE ... WHERE claimed = 0`). A second person entering the same code is
  refused even if the spreadsheet was never updated.
- **One computer only** — the licence is bound to a salted hash of the machine
  id and re-checked at every launch. A copied install now shows a message
  saying activation is per-computer and points at a replacement, rather than
  silently asking for another code (which is how someone burns a second one).

---

## What is live

| Thing | Where |
|---|---|
| Activation Worker | `https://pitwall-activation.sarthakvij123450.workers.dev` |
| D1 database | `pitwall-licenses` — `4360c3cd-4f56-4643-a908-85cbed0d2944` |
| Marketing site | `https://pitwall-2k7.pages.dev` |
| Installer | private R2 bucket `pitwall-downloads`, streamed by the Worker |
| Codes seeded | 50, all unclaimed |

The Worker was exercised against a local D1 first and then live. Verified by
running it, not by reading it: health, the CORS preflight, download gating,
first activation, re-activation on the same device (allowed), a second device
(refused 409), malformed input, lowercase/unhyphenated/O-for-0 code
normalisation, and **eight concurrent claims on one unused code, of which
exactly one won**. The entitlement the Worker returns verifies against the
embedded public key, and a tampered copy is rejected.

The live smoke test activated a real code and then reset it in D1, so all 50
remain sellable.

### The CORS bug that would have broken every download

`worker.js` had no `OPTIONS` route and set no `Access-Control-Allow-Origin`.
The site's download form POSTs cross-origin with
`Content-Type: application/json`, which is not a simple request, so the browser
sends a preflight first and refuses to expose the response without those
headers. Every buyer with a valid code would have landed in the form's "could
not reach the server" branch.

It survived review because the form had only ever been tested against an
endpoint that did not exist yet — a CORS failure and an unreachable host look
identical from the page. There were also no tests of any kind for the Worker.
It is now confirmed fixed from the deployed site: a cross-origin POST returns
503 and **the body is readable**, which is only possible with the header
present.

### How the installer is served

The file sits in a **private** R2 bucket (`pitwall-downloads`) and is streamed
by the Worker at `GET /file?code=…`. It is never linked to directly.

The obvious alternative was switching the bucket to public and handing out its
`r2.dev` URL. That URL is permanent and unauthenticated, so the first buyer to
post it anywhere would make the code gate meaningless. Streaming it here means
the code is re-checked against the database on the request for the file itself,
not only on the form.

The code travels in the query string, so it lands in the buyer's browser
history. That is the deliberate trade: the link only works for someone holding
a real code, and passing the link on means passing on your own activation code.

Verified live: the streamed bytes are SHA-256 identical to the built installer
(33,011,861 bytes), `Range` requests return a correct `206` with
`Content-Range` so a dropped connection resumes, an unknown code and a missing
code are both refused 404, and the whole funnel works from the published page —
typing `pitw h7dzk a21st 9efe8` reformats to `PITW-H7DZK-A21ST-9EFE8`, is
accepted, and starts the download.

One bug found here: passing `request.headers` to `env.DOWNLOADS.get()`
unconditionally makes R2 populate `object.range` even when no `Range` was
asked for, so an ordinary download answered `206 Partial Content`. Now the
range path is only taken when the request actually carried a `Range` header.

To replace the installer after a rebuild:

```
wrangler r2 object put pitwall-downloads/PitWall-Setup.exe \
  --file "%LOCALAPPDATA%\PitWallBuild\artifacts\PitWall-Setup.exe" \
  --content-type application/vnd.microsoft.portable-executable --remote
```

### The ledger sync (added 2026-08-10)

The activation-key workbook is enforceable, not just bookkeeping: a daily
10:00 scheduled task runs `distribution.tools.sync_ledger_status`, which reads
the newest `activation_keys_*.xlsx` and sets the `disabled` flag in D1. The
Worker refuses a disabled code for activation, re-activation and download
(`code_retired`, HTTP 410).

Policy — the owner's rule: **only Unused stays live** (decided 2026-08-10):

- **Activated / Replaced / Void** → retired at the next run. For Activated,
  the buyer's install keeps running (the licence validates offline), but a
  reinstall after a wiped disk needs a replacement code — accepted support
  cost. For Replaced this closes a real hole: before this, a Replaced code's
  original device could re-activate forever alongside its replacement.
- **Sold** → the buyer is told they have 48 hours to install. Retired at the
  first run ≥ `--sold-window-days` (default 2) days after the workbook's
  Sold Date, and even then only `WHERE claimed = 0` — a buyer who activated
  while the sheet lagged is never punished. No Sold Date ⇒ no countdown
  (warned in the log).

The sheet is authoritative both ways: reverting a code to Unused re-enables it
on the next run. Retiring a code never reaches into an already-activated
install — the app validates its cached licence offline by design.

Pieces: `activation-server/migrations/0001_disabled_codes.sql` (one-time D1
migration), the `disabled` checks in `worker.js`, the sync tool, and
`tools/register_ledger_sync_task.ps1` (one-time Task Scheduler registration;
logs to `ledger/sync_log.txt`). Tests: `tests/test_ledger_sync.py`.

---

## Free edition (4.9)

Your Pit Box went free in 4.9. What that changed, and what it did not:

- `distribution/edition.py` holds one switch, `FREE_EDITION = True`. With it
  on, `launcher.launch()` runs `_launch_free`: integrity check, a one-time
  welcome form (`first_run.prompt_first_run(free=True)`) that takes an
  optional OpenAI key, then `start_app`. A `welcome_shown` marker in the
  config directory stops the form reappearing. Nothing is claimed, bound or
  phoned home. `distribution/tests/test_free_edition.py` pins all of that,
  including that the free flow never calls `gate.activate`.
- The paid flow is untouched and still tested; `launch(..., free=False)`
  runs it. Existing paid installs validate their cached licence offline as
  before, and the Worker's `/activate`, `/download` and `/file` routes stay
  live for them.
- The Worker gained `GET /installer` (public, streams the R2 object, Range
  supported) and `POST /subscribe` (optional email for release news, stored
  in the D1 `subscribers` table). The table comes from
  `migrations/0002_subscribers.sql`; until it is applied `/subscribe`
  answers 503 and the site still starts the download.
- The site has no price and no code form. The Download button opens an
  optional email prompt, then navigates to `/installer`. The PayPal and
  Venmo details moved to a buy-me-a-coffee section.
- `release_windows.ps1` now deploys the Worker and applies the migration
  between the R2 upload and the site deploy, and confirms `/installer`
  answers before the page that links to it goes out.

The 4.9 installer must be in R2 before the 4.9 site is deployed: the site
promises a free download, and the previous installer still asks for a code.
The release script enforces that order.

### The bridge: shipping the free site before the free installer

The 4.9 site and Worker went live on 2 September 2026 from a cloud session,
while the installer in R2 was still the 4.8.1 build, which asks for an
activation code on first start. To keep downloads usable in between:

- D1 has a `settings` table (`migrations/0003_settings.sql`) with
  `installer_needs_code = 1` and `universal_code = PITW-0HGQG-3XGGW-DJ021`.
  That code is one of the seeded batch, marked claimed with
  `claimed_device = 'free-edition-shared-code'` so the ordinary path can never
  hand it out.
- `POST /activate` with that code returns its pre-signed entitlement to any
  device without claiming anything (checked before the claimed and disabled
  branches). The app verifies the signature and binds locally, so one
  entitlement serves every install. Nothing about the other 49 codes changed.
- `GET /installer-info` answers `{ needs_code, code }`. `download.js` calls
  it after the email prompt and, while `needs_code` is true, reveals the
  `#codePanel` under the Download button with that code and a copy button.
  The FAQ and guide step 4 explain the extra window.
- `release_windows.ps1` ends the bridge automatically: after uploading a
  free-edition installer it sets `installer_needs_code = 0`, the panel stops
  appearing, and the shared code becomes irrelevant (installs made with it
  keep working; they validate offline).

Do not mark `PITW-0HGQG-3XGGW-DJ021` Void or Replaced in the ledger workbook
while the bridge is on: the sync would retire it and the shared code would
stop activating. Once the free installer is up, retiring it is harmless.

## Releasing without the Windows PC

`release_windows.ps1` needs Windows for one step, the PyInstaller and Inno
Setup build. `.github/workflows/windows-installer.yml` runs that step on a
GitHub-hosted runner on demand (Actions tab, "Build Windows installer",
Run workflow). It runs the tests that need no signing key, builds the
installer, checks it, and attaches it to a GitHub Release tagged
`v<version>` with the SHA-256 in the notes. The 4.9.1 installer was produced
this way on 3 September 2026 and published from a cloud session:

1. Download `PitWall-Setup.exe` from the release and check its SHA-256.
2. `wrangler r2 object put pitwall-downloads/PitWall-Setup.exe --file ... --remote`
3. `UPDATE settings SET value = '0' WHERE key = 'installer_needs_code'` in D1
   (only needed while the bridge is on).
4. Confirm `GET /installer` on the Worker serves the new byte count and hash,
   and `GET /installer-info` answers `needs_code: false`.
5. Redeploy the Worker and the site only if their sources changed; compare
   `build_site` output with the live pages first. Cloudflare rewrites
   `mailto:` links at the edge, so those lines differ on every fetch.

## To go live

Steps 1–4 and 6 are done. Preflight now reports "all checks passed" for the
first time, so `build.py` no longer needs `--dev`.

- [x] **Production key minted.** Public half `YJG3YB6HvNqah63bnMYd4yiehpL5kTsA2WGQY6ooFuE=`
      is committed; the private half is in `distribution/.secrets/`, gitignored
      and never committed. The old development key stays recorded in
      `build.py::DEV_PUBLIC_KEY` as a blocklist entry, so a build that reverts
      to it is still refused.
- [x] **Worker + D1 deployed** and seeded with 50 codes.
- [x] **App and site point at it.**
- [x] **Installer builds** — `build.py --installer`.
- [x] **Installer hosted** in private R2, streamed by the Worker behind the
      code check.
- [x] **Site published** to `pitwall-2k7.pages.dev`.

The purchase path works end to end. What has *not* been exercised is the
packaged app's first-run screen against the live endpoint, because it needs an
OpenAI API key. Everything beneath it is verified: the Worker, the entitlement
signature, device binding and the licence store.

Redeploy either side with:

```
cd distribution/activation-server && wrangler deploy
python -m distribution.website.build_site
cd distribution/website && wrangler pages deploy _site --project-name pitwall
```

The site is ready to share. A buyer can pay, receive a code, download the
installer with it, and activate.

### Still outstanding

- **macOS**: all code is written and the platform-specific parts are tested
  against captured `ioreg` output, but the artifact needs a Mac to build, plus
  an Apple Developer account (~$99/yr) — without notarization Gatekeeper blocks
  the app as "damaged".
- **The EULA has not been read by a lawyer.** It is a plain-language draft and
  says so in an HTML comment. There is deliberately no governing-law clause
  (removed on request).
- **The mic fix is unconfirmed on real hardware.** Root cause was a
  self-fulfilling STT prompt telling the transcriber the opening word was
  probably the wake word; fixed in `a695f22`. No microphone here to verify. If
  unrequested calls persist, read the Drive radio panel: "armed follow-up"
  means the 6-second window (`PITWALL_WAKE_ARM_TIMEOUT_S`); high accepts with
  rare rejects means the transcriber is still false-matching.
- **Windows anti-tamper is weak by design.** The expected digest sits in a text
  file beside the executable, so a determined attacker edits both. It reliably
  catches a corrupted install and deters casual patching. Real anti-tamper is
  Authenticode signing (~$100–400/yr), which is the honest upgrade path.

---

## Things worth not re-litigating

- **The tamper response never deletes anything.** An earlier draft self-deleted
  the install. It was dropped because the check cannot tell malicious patching
  from a bad disk sector or antivirus quarantine, so the destructive half would
  eventually land on a paying customer while the attacker it targeted can patch
  it out anyway.
- **Download does not consume the code.** Claiming happens once, at activation.
  If downloading burned the code, a buyer whose disk died mid-install would be
  locked out of the file they paid for.
- **Screenshots are copied in at build time**, not committed twice — they are
  ~860 KB and a second copy would drift.
- **`src/pitwall` never imports `distribution`.** The dev app is never gated.
