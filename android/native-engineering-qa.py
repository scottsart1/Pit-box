"""Native WebView and DocumentsUI checks for the isolated CI emulator.

Loaded by emulator-smoke.py after its foreground checks. All input targets
come from current accessibility nodes; API reads verify, but never perform,
the edits being tested. This helper refuses production and physical devices.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from urllib.parse import quote
from urllib.request import urlopen


def labels(node):
    return tuple(value.strip() for key in ("text", "content-desc")
                 if (value := node.get(key, "")).strip())


def matches(node, *, resource_id=None, text=None):
    if resource_id is not None:
        actual = node.get("resource-id", "")
        return actual == resource_id or actual.rsplit(":id/", 1)[-1] == resource_id
    return any(value.casefold() == text.casefold() for value in labels(node))


def entered_value_matches(node, value, field_label=None):
    # Some WebView controls expose "value, label" as their accessible text.
    # Match that exact known label, never an arbitrary substring of the value.
    allowed = {value}
    for label in (node.get("hint", "").strip(), field_label):
        if label:
            allowed.add(f"{value}, {label}")
    return bool(allowed.intersection(labels(node)))


def page_regions(smoke, tree, *, popup=False):
    """Separate the visible page viewport from a surface which can scroll.

    Android marks the horizontally scrolling workspace tabs as scrollable
    even when the entire page fits. Those tabs must never clip page controls
    or receive vertical swipes intended for the page.
    """
    if popup:
        # A dump can expose both a native select dialog and its underlying
        # WebView. Scroll the real native list, not the obscured dashboard.
        for node in tree.iter("node"):
            if node.get("class") == "android.widget.ListView" and (bounds := smoke.node_bounds(node)):
                return bounds, bounds if node.get("scrollable") == "true" else None
    pages = {"live", "driver-dashboard", "strategy", "connection", "analysis", "setup", "settings"}
    for node in tree.iter("node"):
        if node.get("resource-id") in pages and (bounds := smoke.node_bounds(node)):
            return bounds, bounds if node.get("scrollable") == "true" else None
    # Native select dialogs and DocumentsUI supply their own list viewport,
    # without the dashboard's HTML page node. Exclude any tab strip here too.
    regions = [bounds for node in tree.iter("node") if node.get("scrollable") == "true"
               and node.get("class") != "android.widget.TabWidget"
               and (bounds := smoke.node_bounds(node))]
    region = max(regions, key=lambda bounds: (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]), default=None)
    return region, region


def validate_target(smoke, package, version):
    """No fixture, tap or text entry is permitted before all guards pass."""
    assert package == "com.yourpitbox.app.debug", "Native QA requires the isolated debug package"
    serial = smoke.adb("get-serialno").stdout.decode().strip()
    assert re.fullmatch(r"emulator-\d+", serial), "Native QA refuses physical ADB targets"
    assert smoke.adb("shell", "getprop", "ro.kernel.qemu").stdout.strip() == b"1", "Target is not an emulator"
    gradle = (smoke.ROOT / "android/app/build.gradle.kts").read_text(encoding="utf-8")
    revision = re.search(r"val androidRevision = (\d+)", gradle)
    assert revision, "Source does not declare an Android build revision"
    apk_version = f"{version}-android.{revision.group(1)}-debug"
    installed = smoke.adb("shell", "dumpsys", "package", package).stdout.decode()
    assert re.search(rf"\bversionName={re.escape(apk_version)}(?:\s|$)", installed), "Installed APK version differs from source"
    health = smoke.get("/api/health")
    assert health.get("ok") and health.get("version") == version, health
    assert re.search(rf"/{re.escape(package)}/", health.get("database", "")), "HTTP backend is not the debug APK"
    return {"serial": serial, "package": package, "version": version, "apk_version": apk_version, "database": health["database"]}


class NativeUI:
    def __init__(self, smoke):
        self.smoke = smoke
        self.tree = None
        self.sequence = 0

    def snapshot(self, label):
        self.sequence += 1
        safe_label = re.sub(r"[^A-Za-z0-9_.-]", "-", label)
        self.tree = self.smoke.ui_tree(f"native-{self.sequence:03d}-{safe_label}")
        return self.tree

    def find(self, label, *, resource_id=None, text=None, page=True, direction="down", attempts=18, enabled=False, native_list=False):
        """Search both ways only within a freshly reported scrollable region.

        A populated provider missing an expected control is a failure, not a
        skip. Clipped controls beneath the fixed app footer cannot be tapped.
        """
        populated = False
        for attempt in range(attempts):
            tree = self.snapshot(label)
            viewport, region = page_regions(self.smoke, tree, popup=not page)
            populated |= any(labels(node) for node in tree.iter("node"))
            candidates = tree.iter("node")
            if native_list:
                popup = next((node for node in tree.iter("node")
                              if node.get("class") == "android.widget.ListView" and self.smoke.node_bounds(node)), None)
                # A select can expose its obscured WebView in the same dump.
                # Never mistake the underlying selected text for a dialog row,
                # or scroll the dashboard while the native popup is opening.
                candidates = popup.iter("node") if popup is not None else ()
                viewport = self.smoke.node_bounds(popup) if popup is not None else None
                region = viewport if popup is not None and popup.get("scrollable") == "true" else None
            for node in candidates:
                bounds = self.smoke.node_bounds(node)
                if not matches(node, resource_id=resource_id, text=text) or not bounds:
                    continue
                if enabled and node.get("enabled", "true") != "true":
                    continue
                if (page or native_list) and viewport and not (viewport[0] <= bounds[0] < bounds[2] <= viewport[2]
                                              and viewport[1] <= bounds[1] < bounds[3] <= viewport[3]):
                    continue
                return node
            if attempt == attempts - 1:
                break
            if attempt and region:
                x1, y1, x2, y2 = region
                start, end = y1 + (y2 - y1) * 4 // 5, y1 + (y2 - y1) // 5
                down = direction == "down"
                if attempt >= attempts // 2:
                    down = not down
                if not down:
                    start, end = end, start
                self.smoke.adb("shell", "input", "swipe", str((x1 + x2) // 2), str(start),
                               str((x1 + x2) // 2), str(end), "250")
            time.sleep(0.3)
        if not populated:
            raise self.smoke.UiProviderUnavailable(f"Empty accessibility provider while finding {label}")
        raise AssertionError(f"Native control missing: {resource_id or text!r}; see native-*-{label}-ui.xml")

    def tap(self, label, **selector):
        self.smoke.tap_node(self.find(label, enabled=True, **selector))

    def select(self, field, option, *, direction="up"):
        self.tap(field, resource_id=field, direction="up")
        # HTML selects open real Android dialogs. Their list, not the WebView,
        # supplies the bounds used while locating an off-screen option.
        self.tap(f"option-{field}", text=option, page=False, direction=direction, native_list=True)

    def fill(self, field, value, *, page=True):
        assert re.fullmatch(r"[A-Za-z0-9 ._-]+", value), "Fixture text must be shell-safe ASCII"
        self.tap(f"fill-{field}", resource_id=field, page=page)
        self.smoke.adb("shell", "input", "keycombination", "KEYCODE_CTRL_LEFT", "KEYCODE_A")
        self.smoke.adb("shell", "input", "text", value.replace(" ", "%s"))
        # BACK is only a keyboard dismissal; never accidentally navigate away.
        ime = self.smoke.adb("shell", "dumpsys", "input_method").stdout.decode()
        if re.search(r"mInputShown=true", ime):
            self.smoke.adb("shell", "input", "keyevent", "KEYCODE_BACK")
        entered = self.find(f"filled-{field}", resource_id=field, page=page)
        field_label = {"engineeringGroupName": "Group name", "engineeringLapNoteText": "What happened?"}.get(field)
        assert entered_value_matches(entered, value, field_label), f"Native text entry failed for {field}: {labels(entered)}"

    def field_value(self, label, value):
        """Check a visible garage label and its next textual value in the tree."""
        node = self.find("garage-value", text=label)
        textual = [(item, labels(item)) for item in self.tree.iter("node") if labels(item)]
        position = next(index for index, (item, _) in enumerate(textual) if item is node)
        nearby = [part for _, parts in textual[position + 1:position + 2] for part in parts]
        assert str(value) in nearby, f"Garage value for {label} is not {value}: {nearby}"

    def capture(self, label):
        self.smoke.capture_view(f"native-{label}")


def await_report(smoke, endpoint, predicate, message, timeout=15):
    deadline = time.monotonic() + timeout
    while True:
        report = smoke.get(endpoint, timeout=20)
        if predicate(report):
            return report
        if time.monotonic() >= deadline:
            raise AssertionError(message)
        time.sleep(0.3)


def prove_setup(ui):
    ui.tap("setup-tab", text="SETUP LAB", page=False)
    ui.select("setupTrack", "Melbourne")
    ui.select("setupConditions", "Dry")
    ui.select("setupReferenceStyle", "Stable")
    ui.select("setupBasis", "Circuit baseline")
    ui.tap("stable-race", text="Race")
    ui.find("stable-subtitle", text="Race · Dry · Stable")
    ui.field_value("Front wing", 21)
    ui.field_value("Rear wing", 17)
    for heading in ("Aerodynamics", "Transmission", "Suspension geometry", "Suspension", "Brakes", "Tyre pressures · psi"):
        ui.find("garage-group", text=heading)
    ui.field_value("Rear left", 22)
    ui.capture("setup-stable")
    ui.tap("sources", text="Sources & setup details")
    ui.find("source-attribution", text="Melbourne — Matt212 stable race reference")
    ui.capture("setup-sources")
    ui.tap("close-sources", text="Sources & setup details", direction="up")
    ui.select("setupReferenceStyle", "More rotation")
    ui.tap("rotation-quali", text="Quali", direction="up")
    ui.find("rotation-subtitle", text="Quali · Dry · More rotation")
    ui.field_value("Front wing", 30)
    ui.field_value("Rear wing", 0)
    ui.capture("setup-rotation-quali")
    ui.tap("rotation-mixed", text="Mixed", direction="up")
    ui.find("mixed-subtitle", text="Mixed · Dry · More rotation")
    ui.field_value("Front wing", 42)
    ui.field_value("Rear wing", 15)
    ui.select("setupConditions", "Wet")
    ui.tap("wet-race", text="Race", direction="up")
    ui.find("wet-unavailable", text="Setup unavailable")
    reason = ui.find("wet-reason", resource_id="setupRationale")
    assert "wet" in " ".join(labels(reason)).lower(), labels(reason)
    assert not any("Front wing" in labels(node) and ui.smoke.node_bounds(node)
                   for node in ui.tree.iter("node")), "Wet result retained old visible garage values"
    ui.capture("setup-wet-unavailable")
    ui.select("setupConditions", "Dry")
    ui.tap("reload-race", text="Race", direction="up")
    ui.find("restored-subtitle", text="Race · Dry · More rotation")
    ui.tap("test-handoff", text="Test this setup")
    ui.find("test-engineer", text="Compare runs or lap groups")


def prove_engineering(ui, fixture):
    smoke = ui.smoke
    session_id = fixture["session_id"]
    endpoint = f"/api/v1/sessions/{quote(session_id)}/engineering"
    sessions = smoke.get("/api/v1/sessions?limit=200")["items"]
    session = next(item for item in sessions if item["id"] == session_id)
    label = session.get("display_name") or f"{session.get('track_name', 'Circuit')} · {session['session_type']} · {session.get('started_at', '')[:16]}"
    ui.select("engineeringSession", label)
    report = smoke.get(endpoint)
    assert len(report["runs"]) >= 2 and not report["groups"]
    ui.tap("group-editor", text="Define my lap groups")
    ui.tap("suggest-groups", resource_id="engineeringSuggestGroups")
    ui.find("suggestion-draft", resource_id="engineeringGroupName")
    assert smoke.get(endpoint)["groups"] == [], "Suggest groups saved without the Save action"
    group_name = "Native manual A"
    ui.fill("engineeringGroupName", group_name)
    # Explicitly shorten A to four exact chronological laps. This distinguishes
    # a user-authored range from merely accepting the automatic suggestion.
    chosen = report["laps"][:4]
    for field, lap in (("engineeringGroupFrom", chosen[0]), ("engineeringGroupTo", chosen[-1])):
        ui.select(field, lap_label(lap))
    ui.tap("apply-range", resource_id="engineeringApplyRange")
    ui.tap("save-groups", resource_id="engineeringSaveGroups")
    expected_ids = [lap["id"] for lap in chosen]
    report = await_report(smoke, endpoint, lambda r: len(r["groups"]) == 2 and any(
        group["name"] == group_name and group["lap_ids"] == expected_ids for group in r["groups"]),
        "Native group save did not persist the chosen four laps")
    ui.capture("saved-groups")
    ui.tap("close-groups", text="Define my lap groups", direction="up")
    ui.tap("refresh-saved-groups", resource_id="engineeringRefresh", direction="up")
    ui.find("saved-group-card", text=group_name)
    ui.tap("lap-context", text="Lap context and driver reports")
    noted = next(lap for lap in chosen if lap["id"] in report["runs"][0]["summary"]["clean_lap_ids"])
    ui.select("engineeringNoteFrom", lap_label(noted))
    ui.select("engineeringNoteTo", lap_label(noted))
    note = "Native fixture traffic through sector two"
    ui.fill("engineeringLapNoteText", note)
    ui.tap("exclude-lap", resource_id="engineeringNoteExclude")
    ui.tap("save-lap-note", resource_id="engineeringSaveLapNote")
    report = await_report(smoke, endpoint, lambda r: len(r["lap_notes"]) == 1,
                          "Native lap note was not saved")
    saved = report["lap_notes"][0]
    assert saved["text"] == note and saved["lap_ids"] == [noted["id"]]
    assert saved["source"] == "driver" and saved["category"] == "traffic" and saved["exclude_from_pace"]
    assert noted["id"] not in report["runs"][0]["summary"]["clean_lap_ids"]
    ui.find("reported-label", text="Driver report · traffic · excluded from pace", direction="up")
    ui.capture("saved-driver-note")
    ui.tap("close-context", text="Lap context and driver reports", direction="up")
    ui.select("engineeringCompareSource", "My saved lap groups")
    ui.select("engineeringCompareMode", "Stint comparison — any compound")
    ui.tap("compare-stints", text="Compare stints")
    ui.find("descriptive-clean-laps", text="Clean laps: A 3, B 4.")
    ui.capture("stint-comparison")
    ui.select("engineeringCompareSource", "Automatic runs")
    ui.select("engineeringCompareMode", "Matched setup test")
    ui.tap("compare-matched", text="Compare matched laps")
    # The fixture has one known 0.3-second sector gain; the note is reviewed in
    # both modes rather than being silently discarded during strict matching.
    ui.find("matched-sectors", text="Sector gains/losses: S1 0.000 s · S2 -0.300 s · S3 0.000 s")
    ui.find("comparison-driver-note", text=f"A · Driver report · traffic · excluded from pace: {note}")
    ui.capture("matched-comparison")
    # B is a second recorded practice at the same track. Its report remains
    # read-only, while A's custom groups, note and export stay on the first run.
    cross_id = fixture["cross_session_id"]
    cross_session = next(item for item in sessions if item["id"] == cross_id)
    cross_endpoint = f"/api/v1/sessions/{quote(cross_id)}/engineering"
    cross_before = smoke.get(cross_endpoint)
    cross_label = session_label(cross_session)
    if cross_session.get("display_name"):
        cross_label = f"{cross_session['display_name']} · {cross_label}"
    ui.select("engineeringSessionB", cross_label)
    ui.tap("compare-cross-session", text="Compare matched laps")
    ui.find("cross-session-verdict", text="B was quicker by 0.600 s on the median matched lap.")
    ui.find("cross-session-sectors", text="Sector gains/losses: S1 0.000 s · S2 -0.600 s · S3 0.000 s")
    ui.capture("cross-session-comparison")
    cross_after = smoke.get(cross_endpoint)
    assert cross_after["groups"] == cross_before["groups"] and cross_after["lap_notes"] == cross_before["lap_notes"], "Comparing changed B's saved groups or notes"
    report = smoke.get(endpoint)
    (smoke.OUTPUT / "native-engineering-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return endpoint, group_name, note


def lap_label(lap):
    epoch = int(lap.get("timeline_epoch", 0))
    return f"Lap {lap['lap_num']}" + (f" · timeline {epoch + 1}" if epoch else "")


def session_label(session):
    started = str(session.get("started_at", ""))[:19].replace("T", " ")
    return f"{session.get('track_name') or 'Circuit'} · {session.get('session_type') or 'Session'} · {started or session.get('session_id') or session.get('id')}"


def verify_export(data, expected, group_name, note):
    assert data == expected, "DocumentsUI output differs from the selected session export"
    decoded = data.decode("utf-8")
    assert group_name in decoded and note in decoded and "Custom lap groups:" in decoded
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "matches_api_bytes": True}


def read_saved_report(smoke, filename, expected, *, timeout=20):
    """Wait for the app's asynchronous write to finish, not just create a file."""
    deadline = time.monotonic() + timeout
    downloaded = b""
    last_error = "File was not created"
    while time.monotonic() < deadline:
        result = smoke.adb("shell", "cat", f"/sdcard/Download/{filename}", check=False)
        if result.returncode == 0:
            downloaded = result.stdout
            if downloaded == expected:
                return downloaded
        else:
            last_error = result.stderr.decode(errors="replace").strip() or f"cat exited {result.returncode}"
        time.sleep(0.5)
    if downloaded:
        (smoke.OUTPUT / (filename + ".partial")).write_bytes(downloaded)
        raise AssertionError(f"Android saved report differs from the selected export after {timeout}s: "
                             f"{len(downloaded)} of {len(expected)} expected bytes; see {filename}.partial")
    raise AssertionError(f"Android report save did not create a readable report in Downloads: {last_error}")


def prove_export(ui, endpoint, group_name, note, session_id):
    smoke = ui.smoke
    resolved = smoke.adb("shell", "cmd", "package", "resolve-activity", "--brief", "-a",
                         "android.intent.action.CREATE_DOCUMENT", "-c", "android.intent.category.OPENABLE",
                         "-t", "text/plain").stdout.decode()
    if "documentsui" not in resolved.lower():
        return {"status": "not_tested", "reason_code": "documents_provider_unavailable", "resolver": resolved.strip()}
    with urlopen(smoke.BASE + endpoint + "/export?format=text", timeout=20) as response:
        expected = response.read()
    ui.tap("export-report", resource_id="engineeringExportText")
    tree = ui.snapshot("documents-opened")
    assert any("documentsui" in node.get("package", "").lower() for node in tree.iter("node")), "Native export did not open DocumentsUI"
    # Use the drawer's actual accessible label, not a presumed hamburger spot.
    drawer = next((node for node in tree.iter("node") if any(value.casefold() in {
        "show roots", "show navigation drawer"} for value in labels(node)) and smoke.node_bounds(node)), None)
    assert drawer is not None, "DocumentsUI does not expose its destination drawer"
    smoke.tap_node(drawer)
    ui.tap("downloads", text="Downloads", page=False)
    tree = ui.snapshot("documents-destination")
    fields = [node for node in tree.iter("node") if node.get("class") == "android.widget.EditText" and smoke.node_bounds(node)]
    assert len(fields) == 1 and fields[0].get("resource-id"), "DocumentsUI filename field is ambiguous"
    filename = "ypb-native-" + re.sub(r"[^A-Za-z0-9_-]", "", session_id) + ".txt"
    ui.fill(fields[0].get("resource-id"), filename, page=False)
    ui.capture("documents-save")
    ui.tap("documents-save", text="Save", page=False)
    downloaded = read_saved_report(smoke, filename, expected)
    (smoke.OUTPUT / filename).write_bytes(downloaded)
    result = verify_export(downloaded, expected, group_name, note)
    ui.capture("report-saved")
    return {"status": "passed", "filename": filename, "destination": "Android Downloads via ACTION_CREATE_DOCUMENT", **result}


def run(smoke, package, version):
    summary = {"scope": "Installed debug APK native Setup, Test Engineer and Android report save",
               "status": "running", "stages_passed": [], "physical_device_tested": False}
    try:
        summary["target"] = validate_target(smoke, package, version)
        fixture_path = smoke.OUTPUT / "native-engineering-fixture.json"
        process = subprocess.run([sys.executable, str(smoke.ROOT / "tools/engineering-telemetry-smoke.py"),
                                  "--base", smoke.BASE, "--host", "127.0.0.1", "--port", "20777",
                                  "--output", str(fixture_path)], capture_output=True, timeout=150)
        (smoke.OUTPUT / "native-fixture-process.txt").write_bytes(process.stdout + process.stderr)
        assert process.returncode == 0, "Isolated engineering UDP fixture failed; see native-fixture-process.txt"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        summary["session_id"] = fixture["session_id"]
        summary["stages_passed"].append("two-run UDP fixture and retired-session protection")
        ui = NativeUI(smoke)
        prove_setup(ui)
        summary["stages_passed"].append("native Setup choices, all six groups reachable, wing/last tyre values, attribution, wet clearing and Test Engineer handoff")
        endpoint, group, note = prove_engineering(ui, fixture)
        summary["stages_passed"].append("native suggested draft, exact custom range, save/refresh, reported exclusion, both comparison modes and read-only B from another same-track session")
        summary["system_export"] = prove_export(ui, endpoint, group, note, fixture["session_id"])
        summary["status"] = "passed" if summary["system_export"]["status"] == "passed" else "partially_tested"
        if summary["status"] != "passed":
            print("LIMITATION: Android report picker unavailable; native export not tested.")
        else:
            summary["stages_passed"].append("DocumentsUI save with exact report byte verification")
            print("PASS: Native Setup, Test Engineer and DocumentsUI session-report export.")
    except smoke.UiProviderUnavailable as error:
        summary.update(status="partially_tested" if summary["stages_passed"] else "not_tested",
                       reason_code="accessibility_provider_unavailable", reason=str(error))
        print(f"LIMITATION: Native engineering UI not fully verified: {error}")
    except Exception as error:
        summary.update(status="failed", reason=f"{type(error).__name__}: {error}")
        raise
    finally:
        (smoke.OUTPUT / "native-engineering-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
