# Android telemetry and local transfer review

Reviewed 2026-09-15 against Pit-box commit `7921134ce9920d1554c4ea78038b186b1e6bb0d2`, before this work's changes. This is a source review and implementation test strategy, not a claim that the user's phone, router, or console has been tested.

## Findings established from the existing source

- `android/app/build.gradle.kts` targets SDK 35. Its Python dependency and `inspect_2026_header()` accept F1 packet format **2026**. Receiving 2025 packets can therefore leave the app without usable telemetry even when the network delivers them.
- `network_service._create_endpoint()` already binds an IPv4 UDP socket to the configured local address; the default is `0.0.0.0:20777`. The dashboard's separate `127.0.0.1:8000` address is not the telemetry destination.
- `PitBoxService` already declares and runs a foreground service, holds Wi-Fi/multicast/partial-wake locks, and declares `INTERNET`. Adding those same mechanisms again would not explain or repair a proven defect.
- Android address discovery currently relies on Java interface enumeration plus a default-route probe. A phone can have Wi-Fi, mobile, hotspot, and VPN interfaces simultaneously. The correct destination must come from the LAN carrying the game, not simply whichever network supplies internet.
- The receiver already keeps raw arrival/header/parse information and a bounded parser queue. Present these distinct stages in the connection UI. A successful local bind is not proof that the console reaches the device.
- Saved network settings are device-specific. Historical transfers must not transplant the sender's bound IP, destination IP, microphone selection, or network profile into the receiving device.

No phone log or packet capture was supplied with the report that telemetry failed. The exact cause of that incident remains unconfirmed. Prioritize destination/bind/port discovery, distinguish incompatible format from zero packets, and obtain evidence before changing the receive technology.

## What public Android implementations actually do

1. [F1RaceHUD UDP listener](https://github.com/AndroidGameDevelopment/F1RaceHUD/blob/0bda7932fb041371fc03c3027a1796a0d8b52c4b/app/src/main/java/com/codaers/f1racehud/UDPListener.kt) uses an ordinary `DatagramSocket` bound to port 20777 and receives on an IO coroutine. Its [parser](https://github.com/AndroidGameDevelopment/F1RaceHUD/blob/0bda7932fb041371fc03c3027a1796a0d8b52c4b/app/src/main/java/com/codaers/f1racehud/F1Parser.kt) reads little-endian headers and dispatches explicitly by game format. This demonstrates the usual socket/parser boundary; it does not establish production reliability. The implementation has a 2048-byte receive buffer. Its [license](https://github.com/AndroidGameDevelopment/F1RaceHUD/blob/0bda7932fb041371fc03c3027a1796a0d8b52c4b/LICENSE) is source-available with non-commercial restrictions. No code was copied.
2. [AlessioCalini's F1 25 Android receiver](https://github.com/AlessioCalini/F1-25-Telemetry-UPD-Android-App/blob/6796d7d565441756ad371d1127470f2ce3434c0a/main/java/com/example/myapplication/UdpReceiver.kt) also uses a UDP socket and IO coroutine. It tracks receiving status independently from session status. Its small fixed buffer, outer-loop exception handling, and reused packet object are reasons to assess examples critically rather than adopt them wholesale. No code was copied.
3. [Sim Racing Telemetry's own F1 2026 setup guide](https://simracingtelemetry.com/games/F12026/) starts with UDP enabled, broadcast disabled, the receiving device's actual IP, port 20777, format 2026, and 20 Hz. Its user interface distinguishes waiting for a connection from waiting for recordable data. Pit-box can retain 60 Hz for analysis detail, with 20 Hz as a diagnostic comparison. Its advice to use the same connection type is a troubleshooting simplification: a bridged LAN can carry telemetry between wired and wireless devices. Do not reject wired consoles solely because the tablet uses Wi-Fi.

These examples do not supply evidence that changing Python sockets to Kotlin sockets would fix the reported failure. Retaining the shared engine avoids creating a second strategy and telemetry implementation.

## Android requirements and traps

### Local network permissions

[Official Android local-network guidance](https://developer.android.com/privacy-and-security/local-network-permission) says Android 16 enforcement is opt-in through the compatibility flag `RESTRICT_LOCAL_NETWORK`; that test mode temporarily uses `NEARBY_WIFI_DEVICES`. Android 17 enforces `ACCESS_LOCAL_NETWORK` for apps targeting SDK 37 or later. Apps targeting earlier SDKs receive local access through `INTERNET`; the guidance explicitly says not to declare/request the new permission before targeting 37.

Consequently, the absent new permission is **not an established defect in this SDK-35 build**. A future target-37 upgrade must request permission before LAN operations and handle denial/revocation. Restrictions cover native/Python sockets and WebView traffic, including incoming UDP and incoming TCP. Permission-denied socket errors should be distinguishable from an idle game or wrong address.

### Sockets, buffers and radio lifecycle

[Android's DatagramSocket reference](https://developer.android.com/reference/java/net/DatagramSocket) specifies wildcard binding for broadcast reception and documents datagram truncation when the receive capacity is too small. A native implementation must accept the largest supported datagram, use the received length, and reset capacity when reusing a packet. Do not add `SO_REUSEADDR` merely to hide an occupied port; report a conflicting listener. Python's current asyncio byte delivery avoids the Java packet-capacity reuse trap.

[MulticastLock documentation](https://developer.android.com/reference/android/net/wifi/WifiManager.MulticastLock) concerns reception of multicast traffic filtered by the Wi-Fi stack. Retain correct acquire/release lifecycle for discovery and supported broadcast scenarios, but do not promise it overrides router isolation or guarantees every broadcast reaches every Android device. Direct unicast is the baseline.

[Foreground-service requirements](https://developer.android.com/develop/background-work/services/fgs/service-types) allow network interaction under `connectedDevice`; the existing multicast-state manifest permission satisfies one listed prerequisite. Microphone service eligibility is separate from telemetry reception. Refusing the microphone must not prevent the connection screen or UDP receiver from operating.

### Multiple networks

[Android Network APIs](https://developer.android.com/reference/android/net/Network) permit individual sockets to use a selected network. [ConnectivityManager documentation](https://developer.android.com/reference/android/net/ConnectivityManager#bindProcessToNetwork(android.net.Network)) prefers individually bound sockets over binding the whole process. Process-wide Wi-Fi binding also moves future API-provider traffic and DNS onto that network and fails when it disappears. Do not use that as an unmeasured blanket fix, especially when Wi-Fi has no internet and cellular serves the engineer's provider.

Refresh candidate addresses when the network changes. Show the selected LAN IPv4, source packet IP, listener port, and unsupported-format reason. Never present a cellular or VPN address as a confirmed console destination. Hotspot-only and VPN-active cases need device evidence; a successful normal Wi-Fi test does not establish them.

## Required diagnostic ladder

| Observation | Meaning | Next check |
| --- | --- | --- |
| Socket cannot bind | Local port/address/permission problem | Display exact categorized error; inspect saved bind and competing receiver. |
| Socket bound, zero raw datagrams | No demonstrated game-to-device path | Verify tablet IP, matching port, UDP on, actual driving, LAN reachability and guest isolation. |
| Raw datagrams arrive, unsupported format | LAN works; game format and parser disagree | Show observed format and supported 2026 setting explicitly. |
| Header accepted, body parse fails | Datagram content/version/length problem | Count by packet type and preserve a bounded diagnostic sample. |
| Packets parse but state absent | Handler/session/indexing problem | Inspect player index, session ID and handler errors rather than network settings. |
| State updates but dashboard stale | UI/WebSocket problem | Inspect browser connection and rendering separately. |

Wi-Fi history transfer is a distinct TCP service and must have its own authenticated pairing and progress state. Pairing success proves that TCP path only, not that PS5 UDP is configured correctly. A local UDP self-check proves local socket operation only, not traversal from another device.

## Evidence to collect before declaring release readiness

| Test | Required evidence |
| --- | --- |
| Valid and malformed synthetic UDP through real socket | Raw count, parsed count, invalid count; malformed input does not stop later valid packets. |
| Mixed supported packet types and sizes | Largest packets remain intact; unknown/unsupported formats get actionable errors. |
| Fresh install and update with microphone refused | Dashboard and receiver remain usable; stored history survives update. |
| Foreground, screen off, resume, rotate, force-stop/relaunch | Appropriate service lifecycle and clear reconnect state; no duplicate listeners. |
| Tablet on Wi-Fi, console wired; then both Wi-Fi | Actual external unicast arrives on both reachable LAN topologies. |
| Address change, Wi-Fi loss/rejoin, hotspot, VPN, Wi-Fi without internet | Correct address selection and explicit unsupported/blocking conditions; no silent success. |
| Real recorded race replay with simultaneous history transfer | Packet-drop/queue metrics, bounded memory, UI responsiveness, complete transferred session. |
| Paired TCP test and independent console UDP test | Separate results; neither result is represented as proof of the other. |
| Representative physical Samsung race | Device/OS/AP/build IDs, duration, packet counts/rates, memory/thermal and audio observations. |

Unit tests and an emulator can establish deterministic behavior, but cannot certify the user's router, Samsung radio power management, Bluetooth headset, or console configuration. Report each executed test separately from remaining hardware validation.
