# Pit Wall distribution & licensing — architecture

This folder builds a sellable, licensed copy of Pit Wall. It is **fully
isolated** from the core app: nothing under `src/pitwall` imports anything here,
and the license gate runs **only** in the packaged distribution build. Running
the dev app (`python -m pitwall.main`) is never gated and never changed.

The security-sensitive part is the licensing/activation design, below. Read
this before it is wired into the packaged launcher and website.

---

## 1. What we are protecting, and the honest threat model

This is a $20, one-time, single-activation hobby product. The goal is to make
casual copying not worth the bother and to enforce "one code, one activation",
**not** to defeat a determined reverse-engineer. Client-side licensing on the
user's own machine is always ultimately defeatable; the design is honest about
where its guarantees end.

What the design **does** guarantee:

- **Only genuine codes work.** A code's entitlement is signed with a private
  Ed25519 key that never leaves the developer's machine. The app verifies with
  the embedded public key. No server, database, or local file can forge a valid
  entitlement without the private key.
- **A code activates exactly once, globally.** Enforced by one atomic online
  claim at first activation. Two people with the same code cannot both activate.
- **After activation, the app runs fully offline.** No ongoing phone-home.
- **A license is bound to the machine it activated on.** Copying the license
  file to another machine fails the device check.

What it **does not** claim:

- It cannot stop a skilled attacker who patches the compiled binary. The
  tamper/kill-switch raises the cost of that, but a native app on hardware the
  attacker controls can always be cracked eventually.
- Device binding deters casual license-file copying; it is not unbreakable (see
  §5).

---

## 2. Two-layer model: signed entitlement + device-bound license

Codes are minted in **batches, offline, before any buyer or machine is known**.
So the signature cannot include a device. We split the two concerns:

```
Entitlement            (signed offline, at code-gen time)
  { version, code_id, sku, issued }
  + Ed25519 signature over the canonical bytes

License                (built locally, at activation time)
  { entitlement, signature, device_hash, activated_at }
```

- The **entitlement** proves the code is genuine (signature ← public key).
- The **license** binds that genuine entitlement to one machine. The device
  hash is *not* signed (it cannot be, see above); it is enforced two ways: the
  server records the first claiming device, and the app re-checks the machine
  hash on every launch.

`entitlement.py::canonical_bytes` is the single source of the exact bytes that
are signed and verified. The signer (code-gen) and verifier (app) both call it,
so they cannot drift.

---

## 3. Activation flow (the one online moment)

```
  App (first run)                    Activation Worker + D1
  --------------                     ----------------------
  user types code, enters own
  LLM API key
  device_hash = sha256(salt|os|machine_id)

  POST /activate {code, device_hash} ─────────►
                                     lookup code_id in D1
                                     ├─ not found        → 404 code_not_found
                                     ├─ claimed, same dev → return entitlement+sig
                                     ├─ claimed, other dev→ 409 code_already_claimed
                                     └─ unclaimed:
                                        UPDATE ... SET claimed=1, device=?
                                          WHERE code_id=? AND claimed=0   ← ATOMIC
                                        (D1 is strongly consistent; exactly one
                                         concurrent request flips 0→1)
                                        return { entitlement, signature }
                 ◄─────────────────────────────
  verify signature with EMBEDDED PUBLIC KEY   ← trust nothing until this passes
  write license.json (entitlement+sig+device_hash)
  run.

  Every later launch: read license.json, verify signature, check device_hash.
  No network.
```

### Why Cloudflare **D1**, not KV

Single-global-use needs an atomic compare-and-set. Workers **KV is eventually
consistent and has no CAS** — two simultaneous activations could both read
"unclaimed" and both succeed. **D1 (SQLite)** gives `UPDATE … WHERE claimed=0`
with a reliable `changes` count: exactly one writer wins. D1's free tier
(millions of reads/day, 100k writes/day) dwarfs any hobby volume. The Worker
holds **no private key** and signs nothing, so a fully compromised endpoint
still cannot forge entitlements — the worst case is leaking the (single-use,
burnable) code list.

---

## 4. Key management

- `keygen` writes the **private** key to `distribution/.secrets/` (gitignored)
  and the **public** key to `licensing/embedded_public_key.txt` (shipped).
- **The private key must be moved off this machine.** The repo is under
  OneDrive; `.secrets/` is gitignored but OneDrive may still sync it to the
  cloud. `keygen` warns about this. Code-gen reads the key from
  `$PITWALL_SIGNING_KEY` so it can live on a USB stick or password manager,
  outside any synced folder.
- The committed `embedded_public_key.txt` currently holds a **development** key
  so the build and tests work. **Before selling anything, run
  `python -m distribution.tools.keygen --force`** to mint a production key pair
  you alone control, and commit the new public key.
- Losing the private key means you can never sign new codes for the installed
  base's public key. Back it up offline.

The **ledger** (`distribution/ledger/`, gitignored) is your source of truth:
every minted code, its signed entitlement, and its lifecycle
(`unused → sold → redeemed_email`). Never commit or push it.

---

## 5. Device binding and its honest limits

`device.py` hashes a stable per-machine id — Windows `MachineGuid`, macOS
`IOPlatformUUID` — with a public salt and the OS family. The raw id never
leaves the machine or is stored; only the hash is.

Limit, stated plainly: because the device hash is **not** part of the signed
entitlement (it can't be — codes are pre-signed), a determined attacker who
copies `license.json` to a second machine and patches out the device check in
the binary can run there. Defenses that raise this cost: the server refuses a
second *activation* on a different device, and the tamper kill-switch (§6)
responds to a patched binary. For a $20 product this is a deliberate,
proportionate trade-off, not an oversight.

---

## 6. Tamper response / kill-switch (`killswitch.py`)

On detecting that the license-verification code was modified (a build-time hash
of the guarded modules no longer matches the baked manifest), the app removes
**its own install directory and nothing else**, then leaves a `README.txt`
saying: *"You tried to kill the app. Sorry. The app killed itself."* This is
disclosed in the EULA shown at install.

It is scoped and defended in depth:

- **Never runs in development.** Requires a frozen/packaged build (`sys.frozen`)
  **and** an explicit `armed` flag the launcher sets only in the real
  distribution. In dev it is a dry run that logs intent.
- Deletes only inside the resolved install root. It **refuses** a filesystem
  root, the home directory, a too-shallow path, or any path that contains the
  user's `PitWallData`. User telemetry and everything outside the install dir
  are never touched.

This deters casual patching; it is not claimed to stop a determined cracker who
also neutralizes the kill-switch.

---

## 7. Repo hygiene (enforced)

`distribution/.gitignore` ignores `.secrets/`, `ledger/`, `*.ed25519`, key
files, `seed_codes_*.sql`, and build artifacts. Verified before every commit
that neither the private key nor the ledger is staged:

```
git check-ignore distribution/.secrets/signing_key.ed25519   # must be ignored
git add -n distribution/                                      # review the list
```

The folder is `distribution/`, not `dist/`, because the repo root already
gitignores `dist/` as a Python build directory.

---

## 8. What is built vs pending

**Built and tested (13 passing tests, `distribution/tests/test_licensing.py`):**

- Ed25519 sign/verify roundtrip; forged entitlement and garbage signature
  rejected.
- Code format + typo-tolerant normalizer; Python and the Worker's JS normalizer
  cross-checked to agree on 126 cases.
- Device-bound local license: loads offline on the bound device, rejected on a
  different device, forged local license rejected.
- Gate flow: needs-activation without a license; activation verifies-then-
  persists; a server entitlement that doesn't verify is refused.
- Activation Worker (D1) with the atomic claim; schema and wrangler config.
- Private keygen and code-gen tools (ledger + D1 seed, all gitignored).

**Pending (after this design is reviewed):**

- Windows packaging (PyInstaller/Nuitka + installer) with the first-run screen
  that collects the LLM API key and activation code, and wires the gate +
  `armed` kill-switch into the packaged launcher.
- macOS packaging of the same.
- Build-time integrity manifest generation (`gate.write_integrity_manifest`).
- Marketing website (light palette, real screenshots, Venmo $20, generic
  comparison, EULA with the kill-switch disclosure, empty reviews).
- Deploying the Worker + D1 and pointing the app's activation endpoint at it.

The activation endpoint's real backend footprint, for the hosting-cost note:
**one Cloudflare Worker + one D1 database, both free tier** for this volume; a
domain (~$10–15/yr); static site on Cloudflare Pages / Netlify / GitHub Pages
free tier.
