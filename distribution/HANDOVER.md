# Handover — Pit Wall distribution work

State as of **6 August 2026**, commit `d3161ff` on `main`, pushed and in sync
with `origin/main`. Working tree clean.

Read `ARCHITECTURE.md` next to this file for the licensing design. This
document is the practical state: what works, what does not, and what to do
next.

---

## Test state

| Suite | Command | Result |
|---|---|---|
| Everything | `.\.venv\Scripts\python.exe -m pytest -q` | **710 passed** |
| Distribution only | `... -m pytest -q distribution/tests/` | **81 passed** |

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

### The activation-key tracker

```powershell
.\.venv\Scripts\python.exe -m distribution.tools.generate_codes --count 50 --issued 2026-08
```

Writes four files to `distribution/ledger/` (gitignored): the ledger JSON, a
plain code list, the D1 seed SQL, and **`activation_keys_*.xlsx`** — the sheet
to work from when someone pays. Three tabs: instructions, the key list with
status dropdowns, and a summary with live COUNTIF formulas and a revenue line.

Formulas were verified structurally (correct functions, ranges matching the
data exactly) but **not evaluated**: LibreOffice is not installed here and the
xlsx skill's recalc helper needs POSIX sockets. Opening it in Excel once would
confirm. On a fresh batch it should read: total = batch size, Unused = batch
size, everything else 0, revenue $0.

### Enforcement of the two rules you asked for

- **One-time use** — the Worker claims a code atomically in D1
  (`UPDATE ... WHERE claimed = 0`). A second person entering the same code is
  refused even if the spreadsheet was never updated.
- **One computer only** — the licence is bound to a salted hash of the machine
  id and re-checked at every launch. A copied install now shows a message
  saying activation is per-computer and points at a replacement, rather than
  silently asking for another code (which is how someone burns a second one).

---

## What is built but not yet live

- **The installer** (`distribution/packaging/pitwall.iss`) is written but has
  never been compiled — Inno Setup is not installed. It is per-user (no UAC),
  makes Start Menu and desktop shortcuts, launches at the end of setup, and
  never touches `PitWallData` on uninstall.
- **Download gating** (`/download` on the Worker, `website/download.js`) is
  written and the form was tested in a browser: live code formatting, an
  incomplete-code error, and a clean failure with a fallback contact when the
  endpoint is unreachable. It has never talked to a deployed Worker.
- **The marketing site** is complete and reviewed at desktop and mobile widths.
  It is blocked from publishing until the activation endpoint is set.

---

## To go live — in order

1. **Mint a production key.**
   `python -m distribution.tools.keygen --force`. Move the private key off this
   machine (it is under OneDrive). Commit the new public key. Then update
   `distribution/packaging/build.py::DEV_PUBLIC_KEY` to the old value, or drop
   that check — `test_the_recorded_dev_key_matches_the_committed_one` will fail
   until you do, on purpose.
2. **Deploy the Worker + D1**, seed it with the generated SQL, and set
   `DOWNLOAD_URL` on the Worker.
3. **Point the app and site at it**: `launcher.ACTIVATION_ENDPOINT` and
   `ACTIVATION_API` in `website/download.js`. Both are placeholders that block
   their respective builds until changed.
4. **Install Inno Setup** (`winget install JRSoftware.InnoSetup`) and run
   `build.py --installer`.
5. **Host the installer** somewhere the Worker's `DOWNLOAD_URL` can reach.
6. **Publish the site**: `python -m distribution.website.build_site` then upload
   `_site/`.

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
