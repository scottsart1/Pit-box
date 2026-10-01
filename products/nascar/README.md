# YourPitBox for NASCAR 26

A separate stock-car race workspace. Independent Python package, Windows
installer, Android companion, data directory, certificate and app ID. The F1
app does not import or run any of this code.

**Development build 0.1.0.** Race planning, local OCR, recording and the workspace
are implemented. A real NASCAR 26 session has not yet been available to validate
game HUD capture. There is no verified native game telemetry adapter. This build
must not be sold or described as a finished automatic NASCAR 26 race engineer.

## Run on Windows

From this directory, with Python 3.11–3.13:

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[windows]"
.venv/Scripts/yourpitbox-nascar
```

The browser workspace opens at `http://127.0.0.1:53260`. The encrypted Android
listener uses port 53261. Storage is `%USERPROFILE%\YourPitBoxNASCAR`, or set
`NASCAR_DATA_DIR`. Use `--no-lan` for a desktop-only session. The app never toggles
Wi-Fi, Bluetooth, low-latency Wi-Fi mode or audio routing.

## First weekend

1. Create a weekend with your actual series, race length and stage boundaries.
2. Enter a measured fuel-burn estimate, or log at least three clean fuel laps.
   Fuel uses **percentage points of a tank per lap** after your game's multiplier.
3. Log visible observations, import a CSV, or calibrate the Windows observer in
   Connection. Pick the game window and draw regions around the HUD values.
   Fuel and tire regions must show percentages; mph requires an mph display.
4. Enable Radio for spoken local alerts. The separate Android app uses native
   speech and text-to-speech. Desktop speech uses the browser's available service.
5. Optionally add your OpenAI API key in Connection for natural conversation.
   The engineer can inspect race state, field evidence, laps and scenarios through
   bounded read-only tools. It cannot invent unavailable opponent telemetry.

The sample race is explicitly simulated and stored separately. A stopped feed
expires after eight seconds. Manual numeric observations expire after five minutes;
race-control and pit-access readings always expire after eight seconds. Advancing
the lap without a fresh fuel reading invalidates the earlier tank observation.
Saved runs reopen as historical evidence, never as live telemetry.

## NASCAR decisions

- Independent green and caution fuel learning; refuel, pit and mixed-flag laps
  are excluded. Overtime is uncertain once advertised distance has passed.
- Four tires, right sides, left sides or fuel only; service and transit losses
  use your measurements. Pit opening and individual eligibility are separate.
- Up to 80 observed cars. Lapped cars, gap data and last-pit laps do not establish
  fuel load or future pit intent. No fabricated free-pass or wave-around call.
- Entry, center and exit balance; fresh-tire versus late-run behavior; minimum,
  moderate and radical setup reviews restricted to controls the user selects.
  Advice is a practice hypothesis, not a copied or game-validated setup table.
- Clean-lap trend, consistency, stint comparison, pit and handling notes, CSV
  import and JSON export. Track evolution and traffic remain confounders.

## Android companion

Build `android/` with JDK 17+ and Android SDK 36. It is a companion to the Windows
host, not an independent console telemetry receiver. App ID:
`com.yourpitbox.nascar`, with `.debug` for QA. Pair using the QR in Connection;
the invitation pins the PC's TLS certificate and supplies a workspace token.
NASCAR credentials and F1 credentials are never shared automatically.
If the PC has more than one network address, choose its home-network address
before scanning or copying the invitation. The companion stops speech and
microphone capture when its activity leaves the foreground.

```powershell
android/gradlew.bat -p android :app:assembleDebug :app:assembleRelease
```

Release output is unsigned until signed with the publisher's backed-up key.
Never publish an unsigned APK. Microphone access is requested only when the
driver presses the microphone. The app holds no Wi-Fi, Bluetooth or wake locks.
It keeps its screen awake only while its activity is visible.

## Adapter contract

Authenticated `POST /api/observations` accepts a `session_id`, strictly increasing
`sequence` per `source`, and a partial `sample`. See [the adapter specification](docs/ADAPTER.md).
This is an extension interface, **not** evidence that NASCAR 26 emits these data.

## Validate and package

```powershell
.venv/Scripts/python -m pip install -e ".[dev,windows]"
.venv/Scripts/python -m pytest tests -q
.venv/Scripts/python packaging/build.py --installer
```

The installer preserves the NASCAR data directory and has a different Inno Setup
AppId from F1. The F1 update and download endpoints are not used. Release gates
and actual validation status live in [BUILD_STATUS.md](docs/BUILD_STATUS.md).
Domain references and evidence boundaries live in [RESEARCH.md](docs/RESEARCH.md).
