# Android connectivity and paired Wi-Fi history

This candidate shares the existing Python engine between desktop and Android.
It adds an opt-in, encrypted local history service and Android connection
diagnostics. It needs no cloud account, hosted database, or subscription.

## Using the candidate

Both devices need a build containing these changes. An older installation has
no paired-transfer listener. Updating desktop source keeps its existing data
directory; do not replace the directory with an empty installation.

1. Connect the devices to the same reachable private LAN. Wired desktop plus
   Wi-Fi tablet works on a bridged home network. Guest isolation can block it.
2. On each device open **Connection → Transfer history**, name the device, and
   enable Wi-Fi transfers. The selected local address is used for sharing.
3. Create an invitation on either device. Scan its QR code with the Android
   camera, or copy the invitation into the other device. Tap **Pair devices**.
   Invitations expire after five minutes and work once. A camera which does
   not open custom app links can use the copy/paste route.
4. On the receiving device stop its telemetry listener in Connection. Browse
   the paired device, select completed sessions, and copy them here. Keep both
   apps running and do not restart telemetry until the import finishes.
5. Open Library on the receiving device. Its copied history works while the
   source device is off. Copying in the opposite direction uses the same flow.

Copy up to 500 selected sessions and 8 GiB per transfer. Downloads stream to
disk; imports stage and validate records on disk. Temporary storage needs can
be several times the selected history's uncompressed size. Choose smaller
batches if either device runs low on space.

Pairing persists across app restarts; the listener starts off until enabled.
If copying is interrupted, enable sharing on both devices and retry. Verified
partial bytes can resume, including after a restart. Source exports expire
after one hour; an expired export starts a fresh download. Turning sharing off
pauses copying. An import already committing finishes its transaction.

If an IP address changes, turn sharing off and on, then pair again using a new
invitation. Allow the app's TCP port 20778 through the Windows firewall on the
private network when needed. Never expose or forward this port on the router.

## What travels

- Completed or recovered incomplete sessions, recorded laps, available legacy
  and typed traces, raw captures, feedback, setup history, radio history,
  strategy records, comparisons, track models and portable learning records.
- Required reference sessions are included when available. Unavailable or
  actively recording references are reported rather than fabricated.
- Existing local preferences win; differing incoming portable preferences are
  saved as variants. Different timestamps alone cannot block a history copy.
- API credentials, network profiles, device/audio settings and runnable jobs
  stay on their original device. Configure provider keys separately if needed.

The format cannot reconstruct detail already removed by retention. The
catalog indicates recorded detail; export checks actual file availability and
reports missing files on completion. Full history means available surviving
history, not a recovery of every original sample.

Imports are additive and transactional. Stable identities and provenance
receipts prevent repeat copies and round trips from counting a lap twice.
Integer database IDs are remapped with relationships intact. A changed version
of an existing recording stops that import and preserves the archive under
`PitWallData/transfers/conflicts` for review. It is shown as **History needs
review**, never as a successful copy. No automatic overwrite or conflict
resolution is attempted in this candidate.

## Android telemetry

The receiver remains an IPv4 UDP socket, normally `0.0.0.0:20777`, in the
existing foreground service. The game must target the tablet's local IP,
not its dashboard's `127.0.0.1` address or the laptop's old IP. Use UDP format
2026 with this engine. Begin with direct unicast and 20 Hz for diagnosis;
increase frequency after reception is established.

Connection now distinguishes received datagrams, parsed packets and rejected
packets, including an unsupported game format. Android adds actual Wi-Fi,
Ethernet, cellular and VPN network observations, service and lock status, and
clear startup failures. A missing microphone grant must not disable telemetry.
The saved dashboard port is loaded before the WebView begins probing it.
Stopping the service during cold startup is retained until the server exists.

For local transfers, the Android bridge binds each outgoing socket to the
matching Wi-Fi/Ethernet network and selected source address. It preserves the
process's separate internet route. Hotspot interfaces without an Android
upstream Network object use their normal directly connected route.

These changes repair identified source defects and improve diagnosis. The
original phone failure has no supplied packet capture or log, so its exact
cause remains unconfirmed. See [the source research](android-connectivity-research.md).

## Security and resource boundaries

The dedicated listener binds the selected private IPv4 interface and exposes
only pairing, session listings and history downloads. It never serves the
dashboard or settings. TLS certificates are pinned before sending an HTTP
request or secret. Each pairing has separate directional credentials and can
be revoked. Pairing and request limits, four server workers, an export cache,
one incoming job and chunked file I/O bound resource use.

Management endpoints accept only the device's own loopback dashboard, validate
Host and Origin, reject cross-site browser access, and return no-store headers.
The peer protocol rejects browser-origin requests. Archives are checked for
path traversal, escaped symlinks, unexpected entries and tables, incompatible
schemas, invalid relationships, excessive sizes and checksum failures before
being committed. Existing credentials and history are not overwritten.

## Validation and build handoff

Executed locally on Linux:

| Check | Result |
| --- | --- |
| Existing clean baseline | 1,371 tests passed |
| Combined app suite and Android bridge/build-contract regressions | 1,421 tests passed |
| Transfer/archive/Android bridge and build-contract checks, including two additional preference regressions | 48 tests passed |
| Final peer protocol checks, including bounded request-limit bookkeeping | 11 tests passed |
| Two real HTTPS service instances | Pairing, one-use invitation, certificate rejection, real archive copy, duplicate prevention and revocation passed |
| Restart both services during partial download | Resumed from byte 200 with HTTP Range and imported once |
| Real app with real UDP socket | 62 received, 60 parsed, 2 rejected; Suzuka/lap 8 state verified; listener stop verified |
| Real app's management API in isolated host | Correctly reported no private LAN instead of claiming sharing was available |
| DOM flows with jsdom | Enable, QR, handoff, pairing, safe text rendering, selection, progress, stop and conflict UI passed |
| Python undefined/unused-name checks and git whitespace checks | Passed |

Two dependency deprecation warnings came from FastAPI/Starlette test tooling.
DOM checks do not render a WebView or establish tablet layout correctness.

Not executed: Android compilation, installation, emulator runtime, physical
Samsung/PS5/router tests, screen-off power behavior, Bluetooth/audio checks,
long-race memory/thermal profiling, and Windows installer upgrade testing.
No installable APK has been produced or published from this candidate.

The Android workflow runs engine/bridge regressions and host/DOM checks before
building both arm64-v8a and x86_64. Its API-35 Pixel C emulator stage checks
startup, exact packaged engine version, UDP parsing, force-stop/relaunch and
background reception, retaining screenshot/logcat/memory evidence.

The local environment lacks usable Gradle/SDK/adb/emulator downloads. Repository
access was restored and the tested tree was uploaded to
[`codex/android-wifi-history`, PR #43](https://github.com/scottsart1/Pit-box/pull/43).
The Android workflow is running on GitHub. Review its build and emulator
results before offering an APK. It runs for pull-request changes or a manual
dispatch, avoiding duplicate builds for a branch push and its pull request.

The CI APK uses `com.yourpitbox.app.debug` to keep its temporary signing key
and data separate from an existing installation. Do not uninstall an older
Android app to make a candidate install. An upgrade of the stable application
requires its existing compatible signing identity and separate validation;
see [Android build and signing notes](../android/README.md).

Reproduce the available checks:

```sh
python -m pip install -e '.[dev]'
python -m pytest tests distribution/tests/test_android_network.py distribution/tests/test_android_project.py -q
python tools/backend_transfer_smoke.py
# With jsdom available in the Node module search path:
node tools/transfer-ui-smoke.cjs
```

Before release, independently prove console-to-tablet UDP and paired history
copy on the actual Wi-Fi network. TCP pairing success cannot prove UDP
configuration. Compare a representative old session's laps, trace availability,
feedback and analysis on both devices, then repeat and round-trip the transfer.
