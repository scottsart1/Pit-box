#!/usr/bin/env python3
"""Exercise the installed APK's real Python engine and UDP socket via adb.

Requires a running x86_64 emulator and the host f1-packets dependency. This
does not claim to validate a physical router, Samsung power management, or
Bluetooth audio. Logs and a screenshot are retained even when checks fail.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import socket
import subprocess
import time
from urllib.error import URLError
from urllib.request import urlopen

from f1.packets import PacketHeader, PacketLapData, PacketSessionData

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "android" / "smoke-output"
BASE = "http://127.0.0.1:18000"


def adb(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["adb", *args], check=check, capture_output=True, timeout=45)


def get(path: str):
    with urlopen(BASE + path, timeout=5) as response:
        return json.load(response)


def await_health(timeout: float = 150):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            health = get("/api/health")
            if health.get("ok"):
                return health
        except (OSError, URLError, ValueError):
            pass
        time.sleep(1)
    raise AssertionError("APK did not expose a healthy Python backend within 150 seconds")


def fixture_packets(frame: int):
    def header(packet_id: int) -> PacketHeader:
        value = PacketHeader()
        value.packet_format = 2026
        value.game_year = 26
        value.game_major_version = 1
        value.packet_version = 1
        value.packet_id = packet_id
        value.session_uid = 918273645
        value.session_time = frame / 20
        value.frame_identifier = frame
        value.overall_frame_identifier = frame
        value.player_car_index = 0
        value.secondary_player_car_index = 255
        return value

    session = PacketSessionData()
    session.header = header(1)
    session.session_type = 15
    session.track_id = 13
    session.total_laps = 53
    session.track_length = 5807
    lap = PacketLapData()
    lap.header = header(2)
    lap.lap_data[0].current_lap_num = 8
    lap.lap_data[0].car_position = 4
    lap.lap_data[0].lap_distance = 1200
    return bytes(session), bytes(lap)


def prove_udp(label: str, first_frame: int):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        for frame in range(first_frame, first_frame + 60):
            for packet in fixture_packets(frame):
                sender.sendto(packet, ("127.0.0.1", 20777))
            time.sleep(0.05)
    state = get("/api/state")
    (OUTPUT / f"{label}-state.json").write_text(json.dumps(state, indent=2))
    assert state["session_uid"] == 918273645, "UDP packets did not reach the APK session parser"
    assert state["track_name"] == "Suzuka", "Session packet decoded the wrong circuit"
    assert state["current_lap"] == 8 and state["player_position"] == 4, "Lap packet did not update race state"
    assert state["connected"], "App did not report telemetry connected"


def capture(package: str):
    for name, args in {
        "logcat.txt": ("logcat", "-d"),
        "crashes.txt": ("logcat", "-b", "crash", "-d"),
        "memory.txt": ("shell", "dumpsys", "meminfo", package),
        "ui.xml": ("exec-out", "uiautomator", "dump", "/dev/tty"),
        "screen.png": ("exec-out", "screencap", "-p"),
    }.items():
        try:
            (OUTPUT / name).write_bytes(adb(*args, check=False).stdout)
        except (OSError, subprocess.TimeoutExpired) as error:
            (OUTPUT / (name + ".error.txt")).write_text(str(error))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", default="android/app/build/outputs/apk/debug/app-debug.apk")
    parser.add_argument("--package", default="com.yourpitbox.app.debug")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    expected_version = re.search(r'__version__ = "([^"]+)"', (ROOT / "src/pitwall/__init__.py").read_text()).group(1)
    adb("install", "-r", "-g", str(ROOT / args.apk))
    adb("logcat", "-c")
    adb("forward", "tcp:18000", "tcp:8000")
    redir = adb("emu", "redir", "add", "udp:20777:20777")
    assert b"KO" not in redir.stdout, redir.stdout.decode(errors="replace")
    try:
        for attempt in (1, 2):
            if attempt == 2:
                adb("shell", "am", "force-stop", args.package)
            adb("shell", "am", "start", "-W", "-n", f"{args.package}/com.yourpitbox.app.MainActivity")
            health = await_health()
            assert health["version"] == expected_version, health
            assert health["udp_listener"], health
            (OUTPUT / f"launch-{attempt}-health.json").write_text(json.dumps(health, indent=2))
            prove_udp(f"launch-{attempt}", attempt * 100)
        # The foreground service must retain receiving when the activity is
        # no longer visible. This is not a substitute for physical-device Doze QA.
        adb("shell", "input", "keyevent", "KEYCODE_HOME")
        prove_udp("background", 300)
        adb("shell", "am", "start", "-W", "-n", f"{args.package}/com.yourpitbox.app.MainActivity")
        print("PASS: APK startup, exact engine version, UDP session/lap parsing, process restart, and foreground-service reception.")
    finally:
        capture(args.package)
        adb("emu", "redir", "del", "udp:20777", check=False)
        adb("forward", "--remove", "tcp:18000", check=False)


if __name__ == "__main__":
    main()
