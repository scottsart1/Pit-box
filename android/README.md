# Your Pit Box for Android

The desktop app, on a phone or tablet: the same Python backend and the same
dashboard, packaged as an APK. No PC is involved. The game sends its telemetry
to the phone's IP address, the backend runs inside the app as a foreground
service, and the dashboard opens in a WebView.

## How it is put together

```
android/
  app/src/main/java/com/yourpitbox/app/
    PitBoxApplication.java   starts the embedded interpreter
    PitBoxService.java       foreground service: runs pitwall.main.run(),
                             holds Wi-Fi and wake locks, Stop action
    MainActivity.java        WebView on http://127.0.0.1:<port>/
  app/src/main/python/pitbox_android.py
                             sets PITWALL_* environment, then hands over to
                             the unchanged backend
  app/build.gradle.kts       Chaquopy: Python 3.13, pip requirements,
                             ../../src as the Python source root
  build-wheels.sh            cross-compiles the Rust dependencies
  wheels/                    their .whl files (built, not committed)
```

`src/pitwall` is used as-is; nothing is copied or forked. The dashboard
(`static/`) is bundled as assets and extracted to the app's storage on first
start, and `PITWALL_STATIC_DIR` tells the backend where it landed.

## Native dependencies

Chaquopy's package repository supplies the Python 3.13 Android builds of numpy
1.26.2 and cryptography 42.0.8. The latter supplies certificates for paired
Wi-Fi transfers. Pairing QR codes use qrcode's SVG renderer and do not need
Pillow. `build-wheels.sh` compiles pydantic-core, jiter, rpds-py and websockets
with cibuildwheel for both arm64-v8a phones/tablets and x86_64 emulators.

`native-requirements.txt` is shared by the wheel builder and the APK's pip
constraints. Updating a Python dependency cannot silently select a different
native version from the wheels which were built. `check-wheels.py` rejects a
cache missing either ABI, corrupt ZIP files, a different Python ABI, or a
wheel requiring an Android API newer than our minimum API 24. A partial
cache is rebuilt even when GitHub Actions reports a cache hit.

The Android Python and Java APIs are documented by
[Chaquopy](https://chaquo.com/chaquopy/doc/current/android.html), and its
[cryptography package index](https://chaquo.com/pypi-13.1/cryptography/)
lists the builds used here. Native dependency compatibility must be checked
when those pins change.

Left out on purpose: uvicorn's `[standard]` extras (uvloop, httptools),
which have no Android builds and are not needed. `sounddevice` and
`soundfile` (PortAudio and libsndfile) have no Android builds either; the
`sounddevice.py` and `soundfile.py` in `app/src/main/python` provide the
subset the backend uses on top of Android's AudioRecord and AudioTrack, and
`distribution/tests/test_android_project.py` fails if the backend starts
using a name they do not provide.

## Building

Prerequisites: JDK 17+, Android SDK with `platforms;android-35` and
`build-tools;35.0.0`, Python 3.13 on PATH (or `-Ppitbox.buildPython=`),
Python 3.12 with `cibuildwheel` and `uv` for the wheels, and Rust.

```
export ANDROID_HOME=/path/to/sdk
PITBOX_HOST_PYTHON=python3.12 ./build-wheels.sh
./gradlew assembleDebug
```

The debug APK is at `app/build/outputs/apk/debug/app-debug.apk`. Install it with
`adb install` or by opening it on the device (allow installs from this
source when asked).

The engine version is read directly from `src/pitwall/__init__.py` at build
time. Increment `androidRevision` in `app/build.gradle.kts` for each
distributed Android update; it is Android's monotonic `versionCode`.

### Debug builds and preserving installed data

Debug builds now use `com.yourpitbox.app.debug`, separately from the
distribution app `com.yourpitbox.app`. They install alongside an existing app
and have a separate data directory. The CI workflow produces debug test
artifacts and does not publish GitHub Releases.

Previous Android prereleases used the distribution package ID with a
runner-generated debug certificate. A different runner's certificate cannot
update that installation. The `.debug` variant avoids requiring the user to
uninstall it, but does not inherit its settings or stored history. Keep the
old installation until any wanted data has been recovered. Do not describe
a new certificate as an in-place upgrade path.

For distribution, configure an existing, securely backed-up signing key via
`PITBOX_KEYSTORE_PATH`, `PITBOX_KEYSTORE_PASSWORD`, `PITBOX_KEY_ALIAS`, and
`PITBOX_KEY_PASSWORD`, then run `./gradlew assembleRelease`. Without those
variables Gradle can produce an unsigned release build for compilation
checks, which is not installable. Never check a key or its passwords into
the repository. See [Android app signing](https://developer.android.com/studio/publish/app-signing).

### Executable emulator verification

`.github/workflows/android-apk.yml` runs on the implementation branch and
relevant pull requests. After building the APK for both ABIs, it launches an
API 35 x86_64 Pixel C tablet emulator and runs `emulator-smoke.py`. To run it
with an already booted emulator, from the repository root:

```bash
python3.13 -m pip install 'f1-packets>=2026.1.1,<2027'
python3.13 android/emulator-smoke.py
```

The script installs the APK, checks the actual embedded engine version,
forwards real UDP packets into the emulator, and verifies the decoded
session, circuit, lap and position through the app's HTTP API. It repeats
after force-stopping/restarting the app and checks reception with the
activity in the background. Logs, decoded state, memory snapshot, UI tree
and screenshot are captured in `android/smoke-output/`, including on failure.

A passing emulator run establishes the packaged interpreter, dependencies,
service and parser can work together. It does not establish physical router
broadcast delivery, Samsung battery-management behavior, voice quality,
headset routing, or a full-race performance budget. A single debug-emulator
memory snapshot cannot establish a memory leak or predict a tablet's RAM
use. Those need focused physical-device checks with both apps' versions and
the router/device configuration recorded.

## Using it

1. Open the app. The notification "Your Pit Box is running" means the
   backend is up; the dashboard appears when it answers.
2. Open the CONNECTION tab: it shows the phone's IP address. In the game's
   telemetry settings enter that address, UDP port 20777, format 2026. The
   address changes when the phone moves to another network, so check it
   again after switching Wi-Fi; the game's saved address does not follow.
3. Keep the phone on the same Wi-Fi as the console or PC running the game.
   Some routers and most guest networks keep devices from talking to each
   other ("AP isolation" or "client isolation"); if nothing arrives with the
   address and port set correctly, turn that off, or make the phone the
   network: enable its hotspot, join the console to it, and use the hotspot
   address CONNECTION shows (192.168.43.1 on most phones). Setting the
   game to broadcast mode is a useful test too: it reaches every device on
   the network without an address.
4. Leave with the back button and the session keeps running; Stop is in the
   notification, or Quit in the dashboard.

## Voice on a phone

The engineer listens on the phone's microphone and answers through its
speaker, or through connected headphones. The first start asks for the
microphone; if it is refused, the app runs without voice (text radio still
works) until the permission is granted in Android's settings and the app is
restarted. Push-to-talk from the controller works as on the desktop: the
game forwards the button inside its telemetry, so the driver never touches
the phone. The speech-to-speech radio (Settings, "voice realtime") is
available too.

Not yet: a Bluetooth headset's microphone. Playback reaches Bluetooth
headphones, but capture uses the phone's own microphone until the headset
audio route is added.

## What does not work yet

- The dashboard's layout is the desktop one. Tablets in landscape are fine;
  phones get the responsive rules in `static/css/v42.css`, which cover the
  workspaces but not yet a purpose-built DRIVE screen.
