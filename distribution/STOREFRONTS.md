# Selling Your Pit Box through a storefront

> **Superseded (4.9.0):** Your Pit Box is now free to download, with a
> voluntary "buy me a coffee" link instead of a price. The $20 / $5 figures
> and margin arithmetic below describe the paid model this document was
> written for and are kept for the record only.

Today the only way to buy Your Pit Box is to find `pitwall-2k7.pages.dev`, pay
$20 by Venmo, wait for a code to be emailed by hand, and then talk Windows out
of the SmartScreen warning that an unsigned installer earns. Every one of those
steps loses buyers, and none of them puts the app in front of anyone who was
not already told about it.

This document is the assessment of what it would take to fix that by listing on
a storefront: which stores this app can actually go on, what each one demands,
what it costs, and what would have to change in this repository. It is a plan,
not a decision — the ordering at the end is a recommendation.

The short version: **the Microsoft Store is worth doing first, Steam is worth
more for marketing, and Google Play is a different product that does not exist
yet.**

---

## 1. What is being shipped, honestly

Everything below follows from what this app actually is, so it is worth stating
plainly:

| | |
| --- | --- |
| Platform | Windows x64 only. macOS code is written but never built (`build.py --check`). |
| Runtime | Python 3.11+, frozen by PyInstaller into a one-folder build (~300 MB). |
| Install | Inno Setup, per-user into `%LOCALAPPDATA%\Programs`, no UAC prompt. |
| Network | Binds UDP 20777 for F1 26 telemetry; serves the dashboard over HTTP on `127.0.0.1:8000`, which the buyer opens in their own browser. |
| Data | `~/PitWallData` — sessions, SQLite history, licence. Never inside the install directory. |
| Third-party dependency | The buyer supplies their own OpenAI API key and pays OpenAI directly. |
| Licence | $20 one-time, Ed25519-signed entitlement, one global activation, device-bound. Cloudflare Worker + D1. |
| Signing | None. No Authenticode certificate. |
| Hardware needed to see it work | A PS5 or a PC running F1 26, UDP format 2026, and a live session. |

Two of those lines decide most of what follows. **The app serves a local HTTP
server that a separate process (the browser) connects to** — that rules out any
sandboxed store container. And **nobody can see the app do anything without F1
26 running** — that is the single largest rejection risk on every store, and it
is fixable in this repository rather than by a store.

---

## 2. Microsoft Store — the one that fits, and it is close to free

### Why it is worth doing

Listing on the Microsoft Store fixes four separate problems that are already
written down as known gaps in `HANDOVER.md`, and it fixes them together:

- **The SmartScreen warning goes away.** Not by buying a certificate — by
  letting Microsoft sign the package. If the app is submitted as an **MSIX**,
  code signing is free and automatic: Microsoft re-signs the package after
  certification, and there is no certificate to buy, renew, or store on a USB
  token.
- **Tamper-evidence gets real.** `ARCHITECTURE.md` is candid that the integrity
  manifest is weak — the expected digest sits in a text file beside the
  executable, so an attacker edits both. An MSIX is signed as a whole and
  Windows verifies it at install; that is the "honest upgrade path" the
  architecture doc names, without the ~$100–400/yr certificate it assumed.
- **Updates stop being a PowerShell script.** `update_windows.ps1` becomes
  optional for Store buyers; the Store updates them.
- **Discovery.** Which is the entire point of the exercise.

Registration is now **free for individual developers** — Microsoft waived the
old $19 one-time fee, in nearly 200 markets. Company accounts still pay.

### The two submission paths, and why MSIX wins

| | MSIX package | Existing EXE installer |
| --- | --- | --- |
| Code signing | Free — Microsoft re-signs after certification | **You** must sign, with a cert chaining to a Microsoft Trusted Root CA. Self-signed is rejected. |
| Cost | $0 | Azure Artifact Signing ~$10/mo (individuals: US/Canada only) or an OV cert at $150–300/yr with an HSM token |
| Hosting | Microsoft hosts the package | You host an HTTPS, **versioned, immutable** URL per release, forever |
| Install UX | Store handles it | Must install **silently** — `PitWall-Setup.exe` currently shows the wizard |
| Repo work | New: MSIX manifest + packaging step | Small: `/VERYSILENT`, plus release URL discipline |

The EXE path looks cheaper because the installer already exists, but it costs
money every year, keeps the SmartScreen problem on the direct-download copy
anyway, and adds a permanent obligation to keep every versioned URL alive. MSIX
costs a few days of packaging work once and then nothing.

One nice accident: Microsoft re-signing an MSIX rewrites the package signature,
**not** the frozen executable's bytes. So the existing integrity manifest keeps
working — unlike macOS, where `codesign` rewrites the binary and the manifest
had to be dropped entirely.

### The hard requirement nobody can skip: full trust

The app must be packaged as a **full-trust** desktop app —
`<rescap:Capability Name="runFullTrust" />` in the manifest. This is not a
preference. If it ever ran inside an MSIX AppContainer, network isolation
blocks loopback traffic between different apps, and the buyer's browser is a
different app: `http://127.0.0.1:8000` would simply not connect. Full trust is
also what keeps `~/PitWallData` writes, the UDP bind, and the Tk first-run
window behaving exactly as they do today.

Also non-negotiable: the install directory is **read-only** under MSIX. Nothing
may write next to the executable at runtime. Today nothing does — the data root
is `Path.home() / "PitWallData"` and the integrity manifest is written at build
time — but it is a constraint that any future change has to respect.

### What has to be built

1. **Reserve the app name** in Partner Center. Free, immediate, and it is the
   one thing worth doing before anything else because names are first-come.
   Reserve "Your Pit Box" — see §5 on why the name must not contain "F1".
2. **An MSIX packaging step** in `distribution/packaging/`, alongside
   `pitwall.iss` and driven by `build.py`, so the two artifacts cannot drift:
   an `AppxManifest.xml` template (version, identity, capabilities, the
   `windows.fullTrustApplication` entry point) plus a `makeappx` invocation and
   Store logo assets at the required scales. Tests belong in
   `distribution/tests/test_packaging.py` with the rest.
3. **Identity values from Partner Center.** `Identity/@Name` and
   `Identity/@Publisher` must match exactly what Microsoft assigns, or
   submission is rejected. They are per-account, so they cannot be guessed
   ahead of registration.
4. **A privacy policy at a public URL.** Required for submission, and this app
   genuinely needs one: telemetry-derived text goes to OpenAI under the buyer's
   own key, and the microphone is captured locally for the wake word. The site
   already exists on Cloudflare Pages; this is one more page beside `eula.html`.
5. **Age rating, support contact, screenshots.** The 14 screenshots in
   `docs/screenshots/` are already better than most listings; they need
   re-exporting at Store dimensions.
6. **A demo mode.** See §4. This is the real work.

### Licensing a Store copy

If the app is sold *through* the Store, asking a buyer to then type an
activation code is a bad experience and doubles the support surface. Two
options:

- **(A) Sell in the Store, treat the Store as the licence.** Microsoft's fee is
  15% for apps under $1M/yr (12% above). The activation code, the Worker, the
  D1 ledger and the Venmo fulfilment all disappear for Store buyers. The gate
  becomes "was this copy installed as a licensed Store package". That is
  weaker in theory than the Ed25519 entitlement — but read `ARCHITECTURE.md` §1
  again: the stated threat model is "make casual copying not worth the bother",
  and a Store package that a user must have acquired through Store commerce
  clears that bar as well as a code does.
- **(B) List free in the Store, keep selling codes yourself.** Microsoft
  explicitly permits third-party commerce for **non-game** apps at a 0% fee.
  This keeps every existing mechanism, but the listing converts far worse and
  reviewers still have to get past the activation screen.

Recommendation: **A for the Store SKU, and keep the existing code machinery for
direct sales from the site.** They can coexist — the gate already distinguishes
frozen builds from dev runs, so it can distinguish a Store build too.

---

## 3. Google Play — Play cannot run this app

Play is Android. Nothing in this repository runs on Android: it is CPython,
FastAPI, uvicorn, `sounddevice`, and a PyInstaller freeze of a desktop
interpreter. There is no packaging trick that changes that. Two real options
exist, and they are different amounts of work.

**(a) A free companion "second screen" app.** The phone or tablet shows the
live dashboard over the home LAN while the PC keeps doing the work. Most of
this already exists: `PITWALL_WEB_LAN_ACCESS`, `PITWALL_WEB_ACCESS_TOKEN`, and
CSS breakpoints down to 440px. The known obstacles:

- Play rejects thin WebView wrappers under its minimum-functionality and spam
  policies. A wrapper around `http://192.168.1.x:8000` with no native substance
  is exactly the shape they reject. It needs real native work — discovering the
  PC on the LAN, a pairing flow for the access token, a foreground service to
  keep the screen alive, notifications for radio calls.
- Android blocks cleartext HTTP by default since Android 9; talking to a LAN
  address over plain HTTP needs an explicit network security configuration, and
  newer Android releases are tightening local-network access further.
- A **new personal Play account must run a closed test with at least 12 testers
  opted in continuously for 14 days** before it can publish to production.
  Organisation accounts registered to a legal entity are exempt. That is a
  calendar constraint, not an engineering one — start it early or not at all.
- $25 one-time registration.

**(b) A native Android telemetry receiver.** Technically proven — Sim Racing
Telemetry ships on both Steam and Play and takes console UDP directly. But the
PS5 sends telemetry to exactly **one** destination address, so an Android
receiver *competes* with the PC app instead of complementing it. The version
that makes sense uses a feature this repo already has: the PC receives, and
**forwards a byte-identical copy** to the phone. Fan-out is already built,
tested, and guarded against self-loops.

Either way, Play is a **funnel, not a revenue line**: a free companion that
makes people search for the PC app. It should not be attempted before the
Windows listing exists, because it has nothing to point at until then.

---

## 4. The rejection risk that applies to every store

**A reviewer cannot test this app.** They do not have a PS5, they do not have
F1 26, they will not create an OpenAI billing account, and — today — they
cannot get past the activation screen without a code. Microsoft's guidance is
explicit that if a third-party API key is needed, the publisher supplies one in
the certification notes, or supplies instructions that work without it. An app
that shows a login wall and then nothing is a routine rejection on every store
there is.

This is fixable here, and the parts already exist: `tools/replay_demo.py`,
`tools/demo_server.py`, and recoverable `.pwcap` replay archives. What is
missing is that they are developer tools outside the frozen build. The work is:

- ship a small bundled `.pwcap` session inside the package;
- add a demo entry point reachable from first-run — one button, no code, no API
  key, no console — that replays it through the real dashboard;
- make the deterministic paths (strategy, corner analysis, gaps, wear) work in
  demo mode without an OpenAI key, and say plainly in the UI which parts are
  model-backed and therefore silent;
- write certification notes that tell the reviewer exactly which button to
  press.

This has value well beyond store review. It is also the demo a prospective
buyer can try before paying, and the reproduction case for a support ticket.
If only one item from this whole document gets built, it should be this one.

---

## 5. Cross-cutting things to settle before any listing

**The name cannot contain "F1" or "Formula 1".** Those are trademarks of
Formula One Licensing BV, and store listings that put someone else's mark in
the product name are a standard takedown. "Your Pit Box" is already clear of
this — keep it that way in the title, the icon, and the package identity.
Factual compatibility statements in the description are the normal approach
("works with the 2026 UDP telemetry format"), together with an explicit
unofficial-and-unaffiliated disclaimer in the listing and in the site footer.
Some telemetry apps do carry game names in their store titles; assume they have
an agreement, and do not copy them on the strength of the observation alone.

**Privacy disclosure, three times over.** Microsoft needs a privacy policy URL,
Play needs a Data Safety declaration, Steam needs a privacy statement. All
three must describe the same true thing: telemetry-derived text leaves the
machine to OpenAI under the buyer's own key; audio is captured locally for the
wake word; session history stays on disk in `~/PitWallData`.

**The EULA still has not been read by a lawyer.** `HANDOVER.md` flags this.
Store distribution raises the stakes — refunds and consumer rights get handled
by the storefront under its own terms, and statutory rights override the EULA's
wording regardless.

**The OpenAI key requirement is the biggest conversion risk, not the biggest
compliance risk.** No store forbids it. But "buy this, then go set up billing
with a second company" will show up in reviews, and it belongs on the store
listing above the fold rather than as a surprise after purchase.

---

## 6. Cost and effort

| Item | Cost | Effort |
| --- | --- | --- |
| Microsoft Store individual account | **$0** (fee waived) | ~1 hour |
| MSIX packaging + manifest + tests | $0 | 2–4 days |
| Demo / reviewer mode (§4) | $0 | 3–5 days |
| Privacy page + listing copy + screenshots | $0 | 1 day |
| Microsoft Store fee, if selling in-Store | 15% under $1M/yr | — |
| Steam Direct | $100, recouped at $1k revenue, 30-day wait | 2–3 days |
| Steam fee | 30% | — |
| Google Play registration | $25 | — |
| Android companion app | $0 | 3–6 weeks + 14-day closed test |
| Authenticode cert (only if avoiding MSIX) | $120–300/yr | — |
| Apple Developer (macOS, direct download) | $99/yr | needs a Mac |

---

## 7. Steam, which is probably worth more than Play

It is not one of the two stores in the original question, so it sits here rather
than at the top — but for this specific product it likely returns more than
Google Play ever will.

- The audience *is* sim racers. Microsoft Store discovery for a niche utility is
  thin; Steam has wishlists, a discovery queue, and reviews that racing players
  actually read.
- The precedent is exact: Sim Racing Telemetry sells in Steam's Utilities
  category, with per-title support for the F1 games.
- The app already supports the PC case — the guide covers `127.0.0.1` when F1
  runs on the same machine — and F1 on PC is bought on Steam. That is the
  overlap.
- $100 Steam Direct, recoupable at $1,000 of revenue, 30-day identity wait, 30%
  cut. Steam also issues keys that can be sold from the existing site.

The cost is the 30% cut and a Steamworks build pipeline. The upside is being
findable by the exact people this was built for.

---

## 8. Recommended order

1. **Now, free, one hour**: register the Microsoft Store individual account and
   reserve the name. Nothing else is blocked on it and names go first-come.
2. **First build task**: the demo / reviewer mode (§4). Every store needs it,
   and it doubles as the site's try-before-you-buy.
3. **Then**: MSIX packaging and the Microsoft Store submission, selling through
   Store commerce, with the existing code activation kept for direct sales.
4. **Then**: Steam, once the Store listing has proven the copy, the screenshots
   and the demo.
5. **Later, if the PC app sells**: the free Android companion on Play, fed by
   the forwarding path that already exists. Start its 12-tester closed test the
   day the account is registered, because that clock runs in calendar days.
6. **Not now**: the Mac App Store. macOS should stay a notarized direct
   download, which `build.py --check` already prints the commands for.

---

## Sources

- [Code signing options for Windows app developers](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options) — Store MSIX signing is free and automatic; MSI/EXE submissions must be signed by the publisher
- [App package requirements for MSI/EXE app](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msi/app-package-requirements) — trusted-root CA signing, versioned immutable URL, silent install
- [Free developer registration for individual developers](https://learn.microsoft.com/en-us/windows/apps/publish/whats-new-individual-developer) — the $19 fee is waived
- [Microsoft Store Policies](https://learn.microsoft.com/en-us/windows/apps/publish/store-policies) — third-party commerce permitted for non-game apps
- [Benefits of distributing your apps via Microsoft Store](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/why-distribute-through-store) — 15%/12% fee, 0% with own commerce
- [MSIX AppContainer apps](https://learn.microsoft.com/en-us/windows/msix/msix-container) and [MSIX containerization overview](https://learn.microsoft.com/en-us/windows/msix/msix-containerization-overview) — loopback restrictions, full-trust behaviour
- [App testing requirements for new personal developer accounts](https://support.google.com/googleplay/android-developer/answer/14151465) — 12 testers, 14 continuous days
- [Sim Racing Telemetry on Steam](https://store.steampowered.com/app/845210/Sim_Racing_Telemetry/) and [on Google Play](https://play.google.com/store/apps/details?id=com.unamedia.srt) — the closest precedent for this product
- [Formula 1 legal notices](https://www.formula1.com/en/information/legal-notices.7egvZU48hzrypubGBNcQKt) — F1 and FORMULA 1 are trademarks of Formula One Licensing BV
