#!/usr/bin/env python3
"""Exercise the installed APK's real Python engine and UDP socket via adb.

Requires a running x86_64 emulator and the host f1-packets dependency. This
does not claim to validate a physical router, Samsung power management, or
Bluetooth audio. Logs and a screenshot are retained even when checks fail.
"""

from __future__ import annotations

import argparse
import base64
import ipaddress
import json
from pathlib import Path
import re
import socket
import subprocess
import time
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from f1.packets import PacketCarTelemetryData, PacketHeader, PacketLapData, PacketSessionData

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "android" / "smoke-output"
BASE = "http://127.0.0.1:18000"


def adb(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["adb", *args], check=check, capture_output=True, timeout=45)


def get(path: str, *, timeout: float = 5):
    with urlopen(BASE + path, timeout=timeout) as response:
        return json.load(response)


def post(path: str, body: dict | None = None):
    request = Request(
        BASE + path,
        data=json.dumps(body or {}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
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
    telemetry = PacketCarTelemetryData()
    telemetry.header = header(6)
    telemetry.car_telemetry_data[0].gear = 1
    return bytes(session), bytes(lap), bytes(telemetry)


def prove_udp(label: str, first_frame: int):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        for frame in range(first_frame, first_frame + 60):
            for packet in fixture_packets(frame):
                sender.sendto(packet, ("127.0.0.1", 20777))
            time.sleep(0.05)
    # Fresh API-35 images can still be indexing system apps during the first
    # session. Keep a bounded request, record its latency, and retain every
    # decoded-state/liveness assertion; this is not a responsiveness benchmark.
    started = time.monotonic()
    try:
        state = get("/api/state", timeout=20)
    finally:
        elapsed = time.monotonic() - started
        (OUTPUT / f"{label}-state-timing.json").write_text(json.dumps({
            "request_elapsed_seconds": round(elapsed, 3),
            "request_timeout_seconds": 20,
            "over_five_seconds": elapsed > 5,
        }, indent=2))
        print(f"{label}: telemetry-state request took {elapsed:.3f}s", flush=True)
    (OUTPUT / f"{label}-state.json").write_text(json.dumps(state, indent=2))
    assert state["session_uid"] == 918273645, "UDP packets did not reach the APK session parser"
    assert state["track_name"] == "Suzuka", "Session packet decoded the wrong circuit"
    assert state["current_lap"] == 8 and state["player_position"] == 4, "Lap packet did not update race state"
    assert state["connected"], "App did not report telemetry connected"
    assert state["speed_kph"] == state["throttle"] == state["brake"] == 0, "Stationary fixture changed the live pedals"
    assert len(state["traces"]) == 2, "Stationary duplicates grew the APK's lap trace instead of retaining stop endpoints"


def prove_transfer_service(label: str, previous_pin: str | None = None) -> str:
    """Load real Android TLS/QR libraries and restart the private-LAN listener.

    This is a management/lifecycle check, not a paired-device history copy.
    Invitations and their tokens stay in memory and are never written to the
    evidence directory or printed in assertion messages.
    """
    prefix = "/api/v1/transfers"
    initial = get(prefix + "/status")
    assert not initial["running"], "Transfers must remain opt-in after app startup"
    summary = {"scope": "APK transfer management and listener lifecycle"}
    try:
        # Use the app's ordinary interface discovery. The emulator's actual
        # 10.0.2.x interface must qualify; no loopback permission or mock route.
        started = post(prefix + "/start", {"device_name": "Pit Box emulator"})
        assert started["running"], "Transfer listener did not start"
        endpoint = urlsplit(started["endpoint"])
        assert endpoint.scheme == "https", "Transfer listener did not use HTTPS"
        address = ipaddress.IPv4Address(endpoint.hostname)
        assert address in ipaddress.IPv4Network("10.0.2.0/24"), "Listener did not bind the emulator's private LAN interface"
        assert endpoint.port == started["port"], "Advertised transfer port differs from listener port"
        pin = started["certificate_sha256"]
        assert isinstance(pin, str) and re.fullmatch(r"[0-9a-f]{64}", pin), "Transfer certificate fingerprint is invalid"
        if previous_pin is not None:
            assert pin == previous_pin, "Transfer identity changed after app process restart"

        invitation = post(prefix + "/invite")
        assert invitation["endpoint"] == started["endpoint"], "Invitation advertises the wrong listener"
        encoded = invitation["invitation"]
        assert encoded.startswith("pitwall-pair://"), "Invitation format is invalid"
        encoded = encoded[len("pitwall-pair://"):]
        payload = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True))
        assert payload["certificate_sha256"] == pin, "Invitation does not pin the active certificate"
        assert payload["endpoint"] == started["endpoint"], "Encoded invitation advertises the wrong listener"
        assert payload["expires_at"] == invitation["expires_at"], "Invitation expiry is inconsistent"
        assert isinstance(payload["token"], str) and len(payload["token"]) >= 32, "Invitation token is invalid"
        pairing_uri = urlsplit(invitation["pairing_uri"])
        assert pairing_uri.scheme == "pitwall" and pairing_uri.netloc == "pair", "Pairing deep link is invalid"
        assert parse_qs(pairing_uri.query).get("invite") == [invitation["invitation"]], "Pairing deep link changed the invitation"
        qr = ET.fromstring(invitation["qr_svg"])
        assert qr.tag == "{http://www.w3.org/2000/svg}svg", "QR renderer did not produce SVG"
        assert qr.find(".//{http://www.w3.org/2000/svg}path") is not None, "QR SVG contains no code geometry"

        stopped = post(prefix + "/stop")
        assert not stopped["running"] and stopped["endpoint"] is None, "Transfer listener did not stop"
        restarted = post(prefix + "/start", {"device_name": "Pit Box emulator"})
        assert restarted["running"], "Transfer listener did not restart"
        assert restarted["certificate_sha256"] == pin, "Transfer identity changed after listener restart"
        assert restarted["device_id"] == started["device_id"], "Transfer device identity changed after listener restart"
        summary.update({
            "listener_address": str(address),
            "listener_port": endpoint.port,
            "tls_certificate_created": True,
            "invitation_and_qr_valid": True,
            "identity_survived_listener_restart": True,
            "identity_survived_process_restart": previous_pin is not None,
            "paired_history_copy_tested": False,
        })
    finally:
        stopped = post(prefix + "/stop")
        assert not stopped["running"], "Transfer listener cleanup failed"
    (OUTPUT / f"{label}-transfer-summary.json").write_text(json.dumps(summary, indent=2))
    return pin


class UiProviderUnavailable(RuntimeError):
    """The emulator did not expose WebView content to accessibility automation."""


def ui_tree(label: str) -> ET.Element:
    """Keep each fresh accessibility snapshot; never infer taps from pixels."""
    result = adb("exec-out", "uiautomator", "dump", "/dev/tty", check=False)
    output = result.stdout.decode(errors="replace")
    (OUTPUT / f"{label}-dump.txt").write_text(output)
    # uiautomator adds a status line after the XML on several Android images.
    start, end = output.find("<hierarchy"), output.rfind("</hierarchy>")
    if start < 0 or end < start:
        raise UiProviderUnavailable("Android accessibility dump did not contain a UI hierarchy")
    xml = output[start:end + len("</hierarchy>")]
    (OUTPUT / f"{label}-ui.xml").write_text(xml)
    return ET.fromstring(xml)


def node_bounds(node: ET.Element) -> tuple[int, int, int, int] | None:
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds", ""))
    if match is None:
        return None
    x1, y1, x2, y2 = map(int, match.groups())
    return (x1, y1, x2, y2) if x2 > x1 and y2 > y1 else None


def system_bar_state(display_dump: str) -> tuple[str, str, str]:
    """Read Android's actual bar policy, not the activity's requested flags."""
    policy = re.search(r"InsetsPolicy\s+status: (\w+)\s+nav: (\w+)([^\n]*\n[^\n]*)?", display_dump)
    if policy is None:
        raise AssertionError("Android did not report the status/navigation bar policy")
    transient = re.search(r"mShowingTransientTypes=([^\n]+)", policy.group(3) or "")
    return policy.group(1), policy.group(2), transient.group(1).strip() if transient else ""


def prove_fullscreen(label: str, *, swipe: bool = False):
    """The emulator must hide both bars and permit a temporary edge reveal."""
    def snapshot(stage: str):
        dump = adb("shell", "dumpsys", "window", "displays").stdout.decode(errors="replace")
        (OUTPUT / f"{label}-{stage}-window.txt").write_text(dump)
        return system_bar_state(dump)

    def await_hidden(stage: str):
        deadline = time.monotonic() + 20
        while True:
            state = snapshot(stage)
            if state == ("WINDOW_STATE_HIDDEN", "WINDOW_STATE_HIDDEN", ""):
                return
            if time.monotonic() >= deadline:
                raise AssertionError(f"System bars did not hide at {label}/{stage}: {state}")
            time.sleep(0.5)

    await_hidden("hidden")
    if swipe:
        # Use the freshly reported display size for the system edge gesture.
        # This does not tap a page control or assume a dashboard coordinate.
        sizes = re.findall(r"(?:Physical|Override) size: (\d+)x(\d+)",
                           adb("shell", "wm", "size").stdout.decode())
        assert sizes, "Android did not report display dimensions"
        width, height = map(int, sizes[-1])
        adb("shell", "input", "swipe", str(width // 2), "1",
            str(width // 2), str(max(80, height // 6)), "300")
        revealed = snapshot("swipe-reveal")
        assert "statusBars" in revealed[2], f"Edge swipe did not reveal transient bars: {revealed}"
        (OUTPUT / f"{label}-swipe-screen.png").write_bytes(adb("exec-out", "screencap", "-p").stdout)
        await_hidden("auto-hidden")
    (OUTPUT / f"{label}-fullscreen.json").write_text(json.dumps({
        "status_bar_hidden": True, "navigation_bar_hidden": True,
        "swipe_reveal_and_auto_hide": swipe,
    }, indent=2))


def page_scroll_bounds(tree: ET.Element) -> tuple[int, int, int, int] | None:
    regions = [bounds for node in tree.iter("node")
               if node.get("scrollable") == "true" and (bounds := node_bounds(node))]
    # The largest scrollable region is the page, not a tab strip or small field.
    return max(regions, key=lambda bounds: (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]), default=None)


def find_ui(label: str, text: str, *, attempts: int = 6,
            within_scroll_region: bool = False) -> ET.Element:
    """Retry WebView accessibility initialization and search by visible label.

    If the label is missing, scroll only inside an actual scrollable node from
    the latest tree, then dump and search again. This also covers a smaller
    viewport without assuming where Connection's transfer card is laid out.
    Page actions must fit inside that region: WebView can expose positive
    bounds for a clipped control whose tap center is underneath a fixed footer.
    """
    populated = False
    for attempt in range(attempts):
        tree = ui_tree(f"{label}-{attempt + 1}")
        region = page_scroll_bounds(tree)
        populated |= any(node.get("text") or node.get("content-desc") for node in tree.iter("node"))
        for node in tree.iter("node"):
            labels = (node.get("text", ""), node.get("content-desc", ""))
            bounds = node_bounds(node)
            if not any(value.strip().casefold() == text.casefold() for value in labels) or bounds is None:
                continue
            if within_scroll_region and (region is None or not (
                region[0] <= bounds[0] < bounds[2] <= region[2]
                and region[1] <= bounds[1] < bounds[3] <= region[3]
            )):
                continue
            return node
        if attempt == attempts - 1:
            break
        # Let the first WebView accessibility request settle before scrolling.
        if attempt > 0 and region is not None:
            x1, y1, x2, y2 = region
            x = (x1 + x2) // 2
            height = y2 - y1
            adb("shell", "input", "swipe", str(x), str(y1 + height * 4 // 5),
                str(x), str(y1 + height * 2 // 5), "350")
        time.sleep(1)
    if not populated:
        raise UiProviderUnavailable(f"WebView accessibility provider remained empty; see {label}-*-ui.xml")
    raise AssertionError(f"Android UI did not expose visible control: {text!r}; see {label}-*-ui.xml")


def tap_node(node: ET.Element):
    bounds = node_bounds(node)
    assert bounds is not None, "Cannot tap a node without visible bounds"
    x1, y1, x2, y2 = bounds
    assert node.get("enabled", "true") == "true", "Target UI control is disabled"
    adb("shell", "input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2))
    time.sleep(1)


def capture_view(label: str):
    # Screenshot coordinates are evidence only; UI-tree bounds drive all input.
    ui_tree(label)
    (OUTPUT / f"{label}-screen.png").write_bytes(adb("exec-out", "screencap", "-p").stdout)


def dismiss_usage_prompt(label: str) -> ET.Element:
    """Decline optional usage sharing before testing controls it can obscure."""
    deadline = time.monotonic() + 10
    tapped = False
    attempt = 0
    while True:
        attempt += 1
        tree = ui_tree(f"{label}-{attempt}")
        prompt = next((node for node in tree.iter("node")
                       if node.get("resource-id") == "usagePrompt" and node_bounds(node)), None)
        if prompt is None:
            return tree
        if not tapped:
            decline = next((node for node in prompt.iter("node")
                            if node.get("resource-id") == "usageNo" and node_bounds(node)), None)
            assert decline is not None, "Usage prompt has no visible No thanks control"
            tap_node(decline)
            tapped = True
        assert time.monotonic() < deadline, "Usage prompt did not close after No thanks"
        time.sleep(0.5)


def prove_fullscreen_keyboard():
    """A real pairing field must remain above the IME and restore on Back."""
    before = dismiss_usage_prompt("keyboard-before")

    def web_bounds(tree):
        regions = [bounds for node in tree.iter("node")
                   if node.get("class") == "android.webkit.WebView" and (bounds := node_bounds(node))]
        assert regions, "No WebView bounds available for the keyboard viewport check"
        return max(regions, key=lambda bounds: bounds[3] - bounds[1])

    full_view = web_bounds(before)
    field = next((node for node in before.iter("node")
                  if node.get("resource-id") == "transferPairCode" and node_bounds(node)), None)
    assert field is not None, "Pairing input is not visible for keyboard QA"
    tap_node(field)
    try:
        deadline = time.monotonic() + 10
        while True:
            dump = adb("shell", "dumpsys", "input_method").stdout.decode(errors="replace")
            (OUTPUT / "keyboard-input-method.txt").write_text(dump)
            if "mInputShown=true" in dump:
                break
            assert time.monotonic() < deadline, "Pairing input did not open the soft keyboard"
            time.sleep(0.5)
        after = ui_tree("keyboard-open")
        resized_view = web_bounds(after)
        assert resized_view[3] < full_view[3], "Keyboard covers the page instead of resizing its viewport"
        focused = next((node for node in after.iter("node")
                        if node.get("resource-id") == "transferPairCode" and node.get("focused") == "true"), None)
        assert focused is not None and node_bounds(focused), "Pairing field lost focus or became invisible"
        bounds = node_bounds(focused)
        assert resized_view[1] <= bounds[1] < bounds[3] <= resized_view[3], "Focused input overlaps the keyboard"
        (OUTPUT / "keyboard-open-screen.png").write_bytes(adb("exec-out", "screencap", "-p").stdout)
    finally:
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
    prove_fullscreen("keyboard-dismissed")
    restored = web_bounds(ui_tree("keyboard-dismissed"))
    assert restored == full_view, "Dismissing the keyboard did not restore the full viewport"
    (OUTPUT / "fullscreen-keyboard.json").write_text(json.dumps({
        "keyboard_opened": True, "focused_input_above_keyboard": True,
        "viewport_resized": True, "fullscreen_restored": True,
    }, indent=2))


def capture_transfer_ui():
    """Navigate the installed WebView and open its actual pairing controls."""
    dismiss_usage_prompt("usage-onboarding")
    capture_view("drive")
    tap_node(find_ui("connection-tab", "CONNECTION"))
    find_ui("connection-heading", "Connection Center")
    capture_view("connection")
    find_ui("transfer-heading", "Transfer history")
    find_ui("transfer-enable", "Enable Wi-Fi transfers", within_scroll_region=True)
    capture_view("transfer-history-off")
    try:
        tap_node(find_ui("transfer-enable-tap", "Enable Wi-Fi transfers", within_scroll_region=True))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not get("/api/v1/transfers/status")["running"]:
            time.sleep(0.5)
        assert get("/api/v1/transfers/status")["running"], "Enable Wi-Fi transfers did not start the service from the UI"
        find_ui("transfer-invite-control", "Create pairing code")
        capture_view("transfer-invitation-controls")
        find_ui("transfer-pair-control", "Pair devices")
        capture_view("transfer-history")
        prove_fullscreen_keyboard()
        (OUTPUT / "transfer-ui-summary.json").write_text(json.dumps({
            "scope": "Installed APK Connection and Transfer history UI",
            "status": "passed",
            "navigation": "UI-tree-derived taps and scrolls",
            "connection_heading_visible": True,
            "transfer_heading_visible": True,
            "enable_control_started_service": True,
            "create_pairing_code_control_visible": True,
            "pair_devices_control_visible": True,
            "paired_history_copy_tested": False,
        }, indent=2))
    finally:
        # Do not create a UI invitation: screenshots must not retain its token.
        stopped = post("/api/v1/transfers/stop")
        assert not stopped["running"], "UI transfer listener cleanup failed"


def prove_transfer_ui() -> bool:
    try:
        capture_transfer_ui()
        return True
    except UiProviderUnavailable as error:
        # A blank accessibility provider is a QA-infrastructure limitation, not
        # evidence that the app's controls passed or failed. A populated tree
        # missing the expected controls still raises an AssertionError above.
        (OUTPUT / "transfer-ui-summary.json").write_text(json.dumps({
            "scope": "Installed APK Connection and Transfer history UI",
            "status": "not_tested",
            "reason_code": "accessibility_provider_unavailable",
            "reason": str(error),
            "paired_history_copy_tested": False,
        }, indent=2))
        print(f"LIMITATION: Connection/Transfer history UI not verified: {error}")
        return False


def prove_sqlite_lifecycle(package: str, label: str):
    """The embedded Python 3.13 runtime reports connections abandoned to GC."""
    pid = adb("shell", "pidof", "-s", package).stdout.decode().strip()
    assert pid.isdecimal(), "APK process is unavailable for SQLite lifecycle check"
    logs = adb("logcat", "-d", f"--pid={pid}").stdout.decode(errors="replace")
    leaked = [line for line in logs.splitlines()
              if "ResourceWarning: unclosed database" in line]
    (OUTPUT / f"{label}-sqlite-lifecycle.json").write_text(json.dumps({
        "scope": "Current installed APK process logs",
        "unclosed_sqlite_warnings": len(leaked),
    }, indent=2))
    assert not leaked, "APK abandoned SQLite handles to garbage collection; see logcat"


def capture(package: str):
    # Preserve management diagnostics even if the live-state request failed.
    # These endpoints contain neither pairing invitations nor credentials.
    for name, path in {"final-health": "/api/health", "final-network": "/api/v1/network/status"}.items():
        try:
            (OUTPUT / f"{name}.json").write_text(json.dumps(get(path), indent=2))
        except (OSError, URLError, ValueError) as error:
            (OUTPUT / f"{name}.error.txt").write_text(str(error))
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
    # A one-time OS tutorial can cover a fresh emulator's first immersive app.
    # Mark only that emulator tutorial seen; never change app permissions/data.
    adb("shell", "settings", "put", "secure", "immersive_mode_confirmations", "confirmed")
    adb("shell", "settings", "put", "secure", "show_ime_with_hard_keyboard", "1")
    adb("forward", "tcp:18000", "tcp:8000")
    redir = adb("emu", "redir", "add", "udp:20777:20777")
    assert b"KO" not in redir.stdout, redir.stdout.decode(errors="replace")
    try:
        transfer_pin = None
        for attempt in (1, 2):
            if attempt == 2:
                adb("shell", "am", "force-stop", args.package)
            adb("shell", "am", "start", "-W", "-n", f"{args.package}/com.yourpitbox.app.MainActivity")
            health = await_health()
            assert health["version"] == expected_version, health
            assert health["udp_listener"], health
            (OUTPUT / f"launch-{attempt}-health.json").write_text(json.dumps(health, indent=2))
            transfer_pin = prove_transfer_service(f"launch-{attempt}", transfer_pin)
            prove_udp(f"launch-{attempt}", attempt * 100)
            prove_sqlite_lifecycle(args.package, f"launch-{attempt}")
            prove_fullscreen(f"launch-{attempt}")
        # The foreground service must retain receiving when the activity is
        # no longer visible. This is not a substitute for physical-device Doze QA.
        adb("shell", "input", "keyevent", "KEYCODE_HOME")
        prove_udp("background", 300)
        adb("shell", "am", "start", "-W", "-n", f"{args.package}/com.yourpitbox.app.MainActivity")
        ui_passed = prove_transfer_ui()
        prove_fullscreen("foreground-return", swipe=True)
        prove_sqlite_lifecycle(args.package, "final")
        print("PASS: APK startup, exact engine version, UDP parsing/background reception, stationary trace stability, transfer TLS/QR management, and identity across listener/process restart.")
        print("PASS: Status/navigation bars hidden after launch, process restart and foreground return; edge swipe reveals transient bars which hide again.")
        if ui_passed:
            print("PASS: Installed APK Connection/Transfer history UI navigation, enable action, and pairing controls.")
        print("LIMITATION: Paired-device history copying and physical Wi-Fi are not covered by this smoke check.")
    finally:
        capture(args.package)
        adb("emu", "redir", "del", "udp:20777", check=False)
        adb("forward", "--remove", "tcp:18000", check=False)


if __name__ == "__main__":
    main()
