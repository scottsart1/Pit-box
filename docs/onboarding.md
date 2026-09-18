# Optional radio setup walkthrough

Published in 4.12.0 on Windows and Android. See
[the release verification](release-qa-4.12.0.md) for final packaged tests,
physical tablet checks and public download receipts. The preview notes below
describe development before publication.

## Open the development preview on this laptop

Run `tools/open-onboarding-preview.ps1` with PowerShell. It uses the sibling
`qa-venv` Python environment and an isolated sibling `onboarding-preview-data`
folder; it never upgrades or imports the installed app's data or credentials.
Once running, open <http://127.0.0.1:18004/#settings> and select **Open setup
walkthrough**. The browser opens automatically unless `-NoBrowser` is supplied.
Use **Quit Your Pit Box** in that preview, or Ctrl+C in its launcher, to stop it.

This is an isolated preview, not the installed app: microphone capture and raw
packet capture are disabled. Its separate UDP port (20784) listens on the LAN
for optional PS5 telemetry checks; the web dashboard remains loopback-only.
Proactive calls use the normal on-by-default setting, but no spoken radio runs
while native microphone/audio is disabled in this preview.
You do not need to enter a real key or change your game settings to view all
three steps or the app tour. Real keys explicitly saved in the preview belong only to its
isolated profile, not the installed app. The normal desktop shortcut and public
installers do not yet contain this feature.

The shared app frontend now contains a three-step walkthrough for Windows and
Android: an OpenAI key, L3 / UDP Action 1 calibration, and hands-free radio.
Each step has Skip this step; Skip setup and Escape close the entire guide.
Step 2 displays the real recommended LAN IP and configured UDP port, copy and
refresh controls, PS5 instructions, L3 mapping and live calibration status in
one place. Unavailable, loopback, wildcard and stale addresses are never offered
as usable PS5 destinations. Network polling only runs while that step is visible.
Settings provides Open setup walkthrough and Begin app tour. Finishing means the guide was viewed,
not that missing keys, game bindings or microphone permissions were configured.

After the three setup steps, Begin app tour / Skip tour offers an optional
11-stop, navigation-only tour. It covers Drive, radio, Driver Dashboard,
Strategy, Connection, beta transfers, Library, Lap Lab, Field, Setup Lab and
Settings. It highlights visible targets (or their workspace tab if a card is
hidden), supports Back/Next/Skip/Escape, reserves space so the panel does not
cover the app, and returns to the original workspace on exit. No action buttons
are invoked and no demonstration telemetry is injected. Reopening setup closes
an active app tour first. Only a finished/skipped marker is stored locally.

Privacy & usage reporting and App updates remain independently functional at
the bottom of Settings, after the dynamically rendered settings groups. New
install defaults are proactive calls on, race scope `all` and trace detail
`full_fidelity`. Existing saved choices and environment overrides still win.
Raw packet defaults and storage safeguards are unchanged.

## Behaviour and boundaries

- Opens once per browser/WebView origin after app startup and the existing
  usage-sharing decision. It does not change that decision or its defaults.
- A live unpaused session suppresses automatic opening for that page load.
  Settings can always open it explicitly. Opening the guide does not pause the game.
- Only `finished` or `skipped` is stored in `pitwall.onboarding.v1`. Clearing browser
  data or using a different browser/port can show the guide again. Storage denial
  does not block closing or using the app.
- Uses the existing local-only credentials route with `verify: true`. Never
  stores API keys in browser storage or logs; password entry is cleared on save,
  step change or exit. Existing plain-text `.env` key storage is disclosed and
  unchanged. The user's other provider selection is not changed.
- Key creation goes to <https://platform.openai.com/api-keys>, linked by the
  [official OpenAI quickstart](https://developers.openai.com/api/docs/quickstart).
- Calibration and wake configuration require an explicit action. Calibration
  requires live, unpaused telemetry; it is not reported as finished just because
  the start request succeeds. Game bindings must be changed in the game itself.
- The microphone meter uses backend input measurements, expires after five
  seconds without state updates, and is explicitly not proof of a complete voice
  round-trip. No synthetic voice, `/api/ask`, microphone permission prompt or paid
  voice request is initiated by opening/finishing the tour.
- Android microphone permission denied at startup requires enabling permission
  in Android Settings and restarting the app, matching the existing native flow.
- Skip remains available during requests. A key save or radio action already
  explicitly submitted may still complete after leaving the guide; skipping does
  not undo that action. Network timeouts warn that the setting may have been applied.
- Older browsers without native dialog support retain the Connection setup route.

## Checks

```sh
node --test distribution/activation-server/tests/onboarding-page.mjs
NODE_PATH=/path/to/jsdom/node_modules node tools/onboarding-ui-smoke.cjs
python -m pytest tests/test_credentials.py distribution/tests/test_android_project.py -q
```

The DOM smoke uses the shipped HTML and onboarding/usage scripts with isolated
fixtures and a dialog shim. It does not verify rendered layout, native focus
trapping, real device audio or a real OpenAI key. Those checks and newly packaged
Windows/Android installers are required before publishing this feature. This
source change does not modify the existing public 4.11.0 downloads.

2026-09-18 revision checks: 86 Node regression tests, 64 Python
credential/settings/Android-project tests, and the onboarding/tour and usage DOM
smokes passed. The running isolated preview was restarted and its APIs confirmed
`proactive_enabled=true`, `field_trace_scope=all`, `capture_mode=full_fidelity`,
and a LAN UDP listener on port 20784. The preview's old loopback-only network
profile was updated through its normal listener API. The regular installed app
and public downloads were not changed. Visual/device checks remain pending:
the browser automation connection could not initialize in this session.
