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
  integrity self-check raises the cost of that, but a native app on hardware
  the attacker controls can always be cracked eventually.
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
second *activation* on a different device, and `device.py` is inside the
integrity manifest (§6), so patching `device_hash()` to return a constant is
itself detected. For a $20 product this is a deliberate, proportionate
trade-off, not an oversight.

---

## 6. Tamper response: refuse to run (`gate.py`)

At build time, `write_integrity_manifest()` bakes a SHA-256 over the guarded
licensing modules. On every launch `integrity_ok()` recomputes it; a mismatch
returns `GateStatus.TAMPERED` and the launcher shows `TAMPER_MESSAGE` and
exits. **Nothing is deleted or modified — ever.**

Guarded modules: `entitlement.py`, `verify.py`, `keys.py`, `license_store.py`,
`device.py`, `gate.py`. `device.py` is in that list deliberately: it computes
the machine hash `license_store` binds against, so patching `device_hash()` to
return a constant would otherwise defeat device binding while every other file
still hashed correctly.

Absence of a manifest (a dev tree) reads as OK, so integrity is enforced only
in a build that shipped one.

### Why not a self-deleting kill-switch

An earlier draft deleted the app's own install directory on a tamper
detection. It was dropped, deliberately. The detection cannot distinguish
malicious patching from an interrupted update, a bad disk sector, or antivirus
quarantining a file — and the deletion would land on a paying customer, while
the attacker it targeted can simply patch the kill-switch out. The deterrent
against casual patching is identical either way (the app does not run), so the
destructive half bought nothing and carried all the risk.

`TAMPER_MESSAGE` is therefore written for the innocent case: it says the
install looks damaged, points at reinstalling, and reassures the user that
`PitWallData` is untouched and their code is still valid. No EULA disclosure of
a destructive response is needed, because there is none.

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

**Built and tested (50 passing tests in `distribution/tests/`):**

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
- Tamper response: `device.py` is guarded, a modified guarded module fails the
  check, and a TAMPERED result leaves the install byte-for-byte intact.
- **Launcher** (`launcher.py`): licensed installs start silently; a bad code
  returns to the form with the reason rather than exiting; closing the window
  activates nothing; a failed API-key save cannot hold a paid activation
  hostage. UI arrives as callables, so the tree is tested headlessly.
- **First-run screen** (`first_run.py`): Tk, so no dependency and no server is
  needed before the app starts. Collects the code and the OpenAI key.
- **Build tooling** (`packaging/`): one PyInstaller spec for both platforms,
  plus preflight that refuses to build with the development key, a placeholder
  activation endpoint, or a missing `static/`.
- **macOS code paths**, proven against captured `ioreg` output: UUID parsing,
  hash stability, a clear error when the field is absent, and the OS family
  folded into the hash so the two platforms cannot collide.
- **Marketing site** (`website/`): light palette, real screenshots, honest
  comparison including the two rows Pit Wall loses, empty reviews, and a EULA
  matching the refuse-to-run behaviour. `build_site.py` refuses to publish
  while the Venmo handle, contact email, or jurisdiction are unfilled, and
  tests pin the claims that would be dishonest if they went stale.

**Pending — the parts that need something I do not have:**

- **Produce the macOS artifact.** Everything except the build itself is done.
  PyInstaller freezes the interpreter running it, so a `.app` must be built on
  a Mac; signing and notarization additionally need an Apple Developer account
  (~$99/yr). Without notarization Gatekeeper blocks the app as "damaged" on
  every Mac but the one that built it. `build.py --check` prints the exact
  `codesign` / `notarytool` / `stapler` commands.
- **Mint the production key**: `python -m distribution.tools.keygen --force`,
  keep the private half offline, commit the new public key. Until then
  preflight blocks every build. Update `packaging/build.DEV_PUBLIC_KEY` or drop
  that check once the committed key is a real one.
- **Deploy the Worker + D1** and set `launcher.ACTIVATION_ENDPOINT` to it.
- **Fill the site placeholders**: Venmo handle, contact email, jurisdiction.
- **Have the EULA read by a solicitor** before selling at volume. It is written
  to be honest and readable, not maximally protective, and consumer statutory
  rights override it regardless of wording.

Payment is **Venmo at $20 with manual fulfilment**: the buyer pays, you mark
the code `sold` in the ledger and email it. There is no payment webhook, which
is why the ledger lifecycle (`unused → sold → redeemed_email`) is the system of
record.

The activation endpoint's real backend footprint, for the hosting-cost note:
**one Cloudflare Worker + one D1 database, both free tier** for this volume; a
domain (~$10–15/yr); static site on Cloudflare Pages / Netlify / GitHub Pages
free tier.
