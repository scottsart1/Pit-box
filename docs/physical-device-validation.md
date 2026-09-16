# Physical Android and Windows transfer validation

Validation on 2026-09-15 used a physical Samsung SM-X930 running Android API 36,
Android candidate revision 9, and the shared Windows engine from PR #43.
The devices were on the same private Wi-Fi network. USB forwarding accessed
the tablet's loopback management API only; peer pairing and history bytes
travelled over the Wi-Fi interfaces and pinned HTTPS listener.

## Findings and corrections

- Another tablet application already occupied TCP 8000. Revision 9 could
  not start its dashboard. A temporary alternate-port configuration allowed
  validation; revision 10 selects an available loopback port when the saved
  port is occupied and passes that address to both the backend and WebView.
  Its Unix probe uses the same address-reuse policy as Uvicorn, so connections
  left in TIME_WAIT after a restart are not mistaken for an active listener.
- Windows adapter discovery through `Get-NetIPConfiguration` intermittently
  exceeded 20 seconds, making invitation creation report an address change.
  Using .NET's adapter enumeration retained confirmed adapter names, states
  and subnet masks and completed in approximately two seconds on this host.
- An upgraded history database and a fresh database both reported schema
  4902, but SQLite column positions differed in `laps` and `proactive_calls`.
  Imports now compare named columns, types, constraints and primary keys,
  allowing different physical positions while rejecting incompatible schemas.

## Completed physical checks

A read-only snapshot of existing Windows history supplied one qualifying
session with 64 recorded car-laps, 419 supporting files and 698 portable rows.
The source installation and original database were not modified.

| Check | Result |
| --- | --- |
| Samsung native interface diagnostics | Wi-Fi, default route, subnet, service and Wi-Fi/multicast/wake locks present |
| Samsung initiates pairing to Windows | Passed over Wi-Fi, exercising Android's outgoing socket-binding path |
| Windows to Samsung history copy | 28,950,914 bytes; 698 rows, one session imported; no missing assets |
| Repeat Windows to Samsung copy | Zero rows imported; all 698 rows and the session recognized as existing |
| Samsung to Windows return copy | 28,950,916 bytes; zero rows imported; all 698 rows recognized as existing |
| History integrity and process restart | All 64 recorded car-laps and trace checksums match Windows; every trace is ready after restarting the tablet process |
| Synthetic F1 2026 UDP over physical Wi-Fi | 120 packets received and parsed in foreground plus 120 in background; zero rejected; expected circuit, lap and position decoded |
| Live console to Samsung | Melbourne Time Trial received after correcting the console's UDP destination; 5,656 packets received and parsed in a 32.24-second sample, zero rejected |
| Targeted transfer, network and Android regression tests | 89 passed |

The UDP fixture originated on Windows and used the actual Wi-Fi path to the
tablet. It establishes that physical-device receiving works in foreground
and background, but is not evidence of packets from a console. Transfers
remained opt-in after a full process restart and retained the paired identity.

The subsequent live-console check used a different source device from the
Windows fixture. Raw recording reached 27,407 packets and 27,102,334 bytes
with zero recording-queue drops or write errors. The car remained stationary
in the sampled interval; this does not establish completed-lap persistence,
pedal/steering response, or full-race reliability. The active recording was
left running rather than interrupted for an APK replacement.

The local full suite initially reported 1,644 passed and four failures because
the existing development interpreter lacked the declared `socksio` dependency.
All five proxy tests passed in an isolated validation interpreter with that
dependency supplied; the existing development environment was not changed.

Emulator diagnosis also recorded a 6.040-second initial live-state response
during cold boot. The integration check now records that latency with a
bounded 20-second request while retaining the decoded-state and connection
assertions. This is not a responsiveness/performance pass. Runtime evidence
and a clearly named diagnostic APK are retained if the runtime check fails.

These checks used the source Windows engine, not a newly installed Windows
installer. They do not yet establish completed live-console lap persistence,
controller or Bluetooth audio behavior, screen-off endurance, or full-race thermal/memory
performance. Revision 10's port recovery requires packaged-device validation.
No stable release was published by this validation.
