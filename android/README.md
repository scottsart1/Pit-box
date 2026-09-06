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

## Why the Rust wheels

Everything the backend imports is pure Python except numpy, pydantic-core,
jiter and rpds-py. Chaquopy's repository has numpy; the other three are Rust
extensions with no Android build anywhere, so `build-wheels.sh` compiles them
with cibuildwheel's Android support before the APK is built. It takes a
Rust toolchain and the Android SDK and runs in minutes.

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

The APK is at `app/build/outputs/apk/debug/app-debug.apk`. Install it with
`adb install` or by opening it on the device (allow installs from this
source when asked).

## Using it

1. Open the app. The notification "Your Pit Box is running" means the
   backend is up; the dashboard appears when it answers.
2. Open the CONNECTION tab: it shows the phone's IP address. In the game's
   telemetry settings enter that address, UDP port 20777, format 2026.
3. Keep the phone on the same Wi-Fi as the console or PC running the game.
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
