# Your Pit Box 5.0.0 — GPT-6 and optional Realtime radio

The normal OpenAI route now uses `gpt-6-luna` with low reasoning effort;
the deep strategy route uses `gpt-6-sol` with high effort. Both retain the
Responses API, validated deterministic telemetry tools, token limits and
route deadlines. Explicit older model pins and other providers are preserved.

File transcription defaults to `gpt-transcribe`, with JSON responses and the
plural `languages` hint required by its API. Explicit legacy transcription
models keep their previous request format. Wake-word and silence filters remain.

Settings → Engineer → **Realtime radio** saves a per-device choice and applies
without restarting. It is off by default. Enabling prepares the radio without
opening a connection; wake/PTT starts a conversation. Disabling closes it and
discards queued playback. Session opening and switching are serialized, and the
socket must receive `session.updated` before taking over from standard voice.
Rejection, timeout or cancellation releases the connection. The existing
25-second idle timeout, five-minute ceiling, bounded replies and telemetry tool
allowlist remain. Realtime may increase API usage; an idle connection by itself
is not billed.

## Model and voice validation

- Live stored-key Responses probes: Luna/low and Sol/high each called a telemetry
  tool and correctly reported its 1.4-second gap. These are synthetic fixtures,
  not measurements of driving performance. Observed total round-trip times were
  5.17 and 2.69 seconds, respectively; one sample does not establish a speed gain.
- Live `gpt-transcribe` recognized a bounded racing audio clip, including the
  wake name, Norris, hard tyres and lap 18.
- Live Realtime accepted the new input transcription settings. An audio replay
  produced a telemetry tool call and spoken answer using its gap, while declining
  to invent a pit recommendation without strategy evidence. The session closed
  after the check. This verifies the wire and audio path, not a human microphone
  conversation during a race.
- Focused tests cover reasoning continuation across tools, both transcription
  formats, saved mode restoration, missing-key rejection, switching during
  connection, rejected sessions and cancellation cleanup.

## Build and regression checks

Both release workflows passed against source commit
`ec4116fc15c0074699f3f3b6a43e26f10dfdafa7`:

- [Windows run 36210107556](https://github.com/scottsart1/Pit-box/actions/runs/36210107556):
  full suite, installed-artifact smoke, 25-lap stress and counted telemetry checks.
  The counted run received and parsed all 18,604 sent datagrams, with zero reported
  queue drops or write errors, followed by a clean shutdown and database check.
- [Android run 36210104559](https://github.com/scottsart1/Pit-box/actions/runs/36210104559):
  shared engine, bridge, UI and dashboard tests; Android 16 emulator startup,
  UDP, restart, background reception, fullscreen/keyboard and SQLite lifecycle
  checks. No unclosed SQLite-handle warnings were found.
- The locally built Windows executable passed an isolated four-lap replay,
  parser fixtures, 60 recorded laps, eight rival comparisons, flag-boundary
  checks, database integrity and restart persistence. All 28 served static files
  matched source. This local executable is the one in the published installer.
- Final focused model, version and website checks: **96 passed**. The earlier
  local full run passed 1,909 tests; its five failures were version snapshots
  taken while renaming the release. All seven relevant version checks passed
  after the rename, and the full cloud runs used the settled 5.0.0 commit.

## Installed devices

The Windows installer completed successfully and the signed Android APK was
installed as an upgrade on the Samsung SM-X930 (Android 16), without uninstalling
or clearing application data. Both report backend version **5.0.0**; Android
reports package version **5.0.0-android.29** with versionCode **29**.

On each installed device, its own stored key successfully completed the Luna
text/tool and Sol reasoning/tool diagnostics, accepted Realtime with all 38 radio
tools, and transcribed a racing audio clip followed by a generated spoken reply.
The API keys were not retrieved from the tablet or changed on either device.

The Settings control was exercised through the desktop browser and the tablet's
native WebView. Enabling it opened no session. The tablet retained the saved
choice through a cold restart, still without opening a session automatically.
With a session explicitly open, switching off through each UI closed it before
the 25-second idle timeout, saved the off choice and made further open requests
return 409. Both devices were left with Realtime **off**. Tablet fullscreen also
passed after the upgrade.

Windows retained **164 sessions / 1,084 laps**, its SQLite integrity check passed,
and the installation `.env` is byte-identical to its backup. The tablet retained
**103 sessions / 19,692 laps**, with unchanged session-identity and content-summary
hashes across install, restart and voice tests. No synthetic racing telemetry was
injected into either real library. A compressed, verified Windows database backup
was kept because disk space was low; cleanup removed duplicate generated artifacts.

## Artifacts

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Windows `PitWall-Setup.exe` | 34,303,522 | `9c2bd15cdce9449273487f20ab228eec59b940e7dbf8f17da609791f37c9c5ee` |
| Android `YourPitBox-5.0.0-android.29.apk` | 72,894,780 | `5ee586c3c5d665afaae59eb3ce1f2f36c5dd2ed2b1dc323670ed8be5ea051a01` |

Android ZIP integrity passed; all 28 static assets and 82 embedded Python modules
match the release source. The APK retains the production signing certificate:
`20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`.
Windows remains an unsigned installer, as disclosed on the download page.

## Production publication

Version **5.0.0** is published at [yourpitbox.com](https://yourpitbox.com), with
Windows and Android update notices live. Production deployment receipts:

- Pages deployment: `https://2d27fcf7.pitwall-2k7.pages.dev`, built from the website
  and artifact-checksum commit `427e246`.
- Activation/download Worker version: `df54b8b7-534d-4389-81c4-4b6c3e204d9e`.
- Both complete public installer downloads matched the sizes and SHA-256 hashes
  above. Eighteen public website routes matched the deployed build, allowing
  only Cloudflare's email-protection transformation where applicable.
- The actual update-service client passed six checks: Windows and Android at
  installed versions 4.13.2, 4.14.0 and 5.0.0. Older versions offer 5.0.0; current
  versions correctly report no newer release. Artifact metadata matched both
  verified downloads.
- Final checks through each installed device also reported version 5.0.0,
  successful update checks, retained stored credentials and Realtime off.
  The final Windows database check still retained 164 sessions and 1,084 laps.

No credentials, driver recordings or private logs are included here.
