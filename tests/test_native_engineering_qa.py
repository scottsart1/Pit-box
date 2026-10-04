"""Safety and UI-tree behavior of the emulator-only native regression helper."""
import importlib.util
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("native_engineering_qa", ROOT / "android/native-engineering-qa.py")
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)
smoke_spec = importlib.util.spec_from_file_location("emulator_smoke_native_test", ROOT / "android/emulator-smoke.py")
smoke = importlib.util.module_from_spec(smoke_spec)
smoke_spec.loader.exec_module(smoke)


def target(serial="emulator-5554", qemu=b"1", version=None, database=None):
    calls = []
    revision = re.search(r"val androidRevision = (\d+)", (ROOT / "android/app/build.gradle.kts").read_text()).group(1)
    version = version or f"5.3.2-android.{revision}-debug"
    responses = {
        ("get-serialno",): serial.encode(),
        ("shell", "getprop", "ro.kernel.qemu"): qemu,
        ("shell", "dumpsys", "package", "com.yourpitbox.app.debug"): f"versionName={version}\n".encode(),
    }

    def adb(*args):
        calls.append(args)
        return SimpleNamespace(stdout=responses[args])

    return SimpleNamespace(ROOT=ROOT, adb=adb, get=lambda path: {
        "ok": True, "version": "5.3.2", "database": database or "/data/user/0/com.yourpitbox.app.debug/files/pitwall.sqlite3",
    }), calls


def test_native_guard_rejects_production_before_any_adb_call():
    runtime, calls = target()
    with pytest.raises(AssertionError, match="isolated debug"):
        qa.validate_target(runtime, "com.yourpitbox.app", "5.3.2")
    assert calls == []


@pytest.mark.parametrize("options,reason", [
    ({"serial": "R9X1234"}, "physical"),
    ({"qemu": b"0"}, "not an emulator"),
    ({"version": "5.3.1"}, "APK version"),
    ({"version": "5.3.20"}, "APK version"),
    ({"version": "5.3.2-android.999-debug"}, "APK version"),
    ({"database": "/data/user/0/com.yourpitbox.app/files/pitwall.sqlite3"}, "not the debug"),
    ({"database": "/data/user/0/com.yourpitbox.app.debug.other/files/pitwall.sqlite3"}, "not the debug"),
])
def test_native_guard_rejects_target_mismatches(options, reason):
    runtime, calls = target(**options)
    with pytest.raises(AssertionError, match=reason):
        qa.validate_target(runtime, "com.yourpitbox.app.debug", "5.3.2")
    assert all(call[0] == "get-serialno" or call[1] in {"getprop", "dumpsys"} for call in calls)


def test_native_guard_records_exact_verified_target():
    runtime, _ = target()
    verified = qa.validate_target(runtime, "com.yourpitbox.app.debug", "5.3.2")
    assert verified["serial"] == "emulator-5554"
    assert verified["version"] == "5.3.2"
    assert verified["package"] == "com.yourpitbox.app.debug"


def ui_runtime(trees):
    snapshots = iter(trees)
    calls, names = [], []

    def tree(label):
        names.append(label)
        return next(snapshots)

    runtime = SimpleNamespace(ui_tree=tree, node_bounds=smoke.node_bounds,
                              page_scroll_bounds=smoke.page_scroll_bounds,
                              UiProviderUnavailable=smoke.UiProviderUnavailable,
                              adb=lambda *args: calls.append(args))
    return runtime, calls, names


def tree_with_control(bounds):
    return ET.fromstring(f'''<hierarchy>
      <node bounds="[0,0][800,100]" text="Header"/>
      <node bounds="[0,100][800,700]" scrollable="true">
        <node resource-id="engineeringSaveGroups" text="Save groups" enabled="true" bounds="{bounds}"/>
      </node><node bounds="[0,700][800,744]" text="Connection"/>
    </hierarchy>''')


def test_native_locator_scrolls_clipped_control_and_uses_fresh_bounds(monkeypatch):
    runtime, calls, _ = ui_runtime([tree_with_control("[20,680][180,724]"),
                                  tree_with_control("[20,680][180,724]"),
                                  tree_with_control("[20,450][180,494]")])
    monkeypatch.setattr(qa.time, "sleep", lambda _: None)
    found = qa.NativeUI(runtime).find("save", resource_id="engineeringSaveGroups")
    assert smoke.node_bounds(found) == (20, 450, 180, 494)
    assert calls == [("shell", "input", "swipe", "400", "580", "400", "220", "250")]


def test_native_page_that_fits_is_not_clipped_by_horizontal_workspace_tabs():
    # Exact geometry from the API-36 tablet CI failure. The dropdown is fully
    # visible; only the horizontal tab strip has scrollable=true.
    tree = ET.fromstring('''<hierarchy>
      <node class="android.widget.TabWidget" scrollable="true" bounds="[0,100][2560,188]"/>
      <node resource-id="setup" scrollable="false" bounds="[0,188][2560,1712]">
        <node resource-id="setupTrack" text="Melbourne" bounds="[86,518][618,606]"/>
      </node></hierarchy>''')
    runtime, calls, _ = ui_runtime([tree])
    assert qa.page_regions(runtime, tree) == ((0, 188, 2560, 1712), None)
    found = qa.NativeUI(runtime).find("setupTrack", resource_id="setupTrack", attempts=1)
    assert smoke.node_bounds(found) == (86, 518, 618, 606)
    assert calls == []


def test_native_popup_uses_its_list_region_not_dashboard_bounds():
    tree = ET.fromstring('''<hierarchy>
      <node class="android.widget.ListView" scrollable="true" bounds="[100,200][700,600]">
        <node text="Melbourne" bounds="[110,220][680,264]"/>
      </node></hierarchy>''')
    runtime, calls, _ = ui_runtime([tree])
    assert qa.page_regions(runtime, tree) == ((100, 200, 700, 600), (100, 200, 700, 600))
    assert qa.NativeUI(runtime).find("track-option", text="Melbourne", page=False, attempts=1).get("text") == "Melbourne"
    assert calls == []


def test_native_popup_scrolls_its_own_list_when_underlying_webview_is_also_exposed(monkeypatch):
    tree = ET.fromstring('''<hierarchy>
      <node resource-id="setup" scrollable="true" bounds="[0,188][2560,1712]"/>
      <node class="android.widget.ListView" scrollable="true" bounds="[500,400][2000,1100]">
        <node text="Monza" bounds="[510,420][1900,464]"/>
      </node></hierarchy>''')
    runtime, calls, _ = ui_runtime([tree] * 3)
    monkeypatch.setattr(qa.time, "sleep", lambda _: None)
    assert qa.page_regions(runtime, tree, popup=True) == ((500, 400, 2000, 1100), (500, 400, 2000, 1100))
    with pytest.raises(AssertionError, match="missing"):
        qa.NativeUI(runtime).find("missing-option", text="Melbourne", page=False, attempts=3, direction="up")
    assert calls == [("shell", "input", "swipe", "1250", "960", "1250", "540", "250")]


def test_native_option_ignores_identical_underlying_selected_text():
    tree = ET.fromstring('''<hierarchy>
      <node resource-id="setup" scrollable="true" bounds="[0,188][2560,1712]">
        <node resource-id="setupTrack" text="Melbourne" bounds="[86,518][618,606]"/>
      </node>
      <node class="android.widget.ListView" scrollable="true" bounds="[500,400][2000,1100]">
        <node text="Melbourne" bounds="[510,420][1900,464]"/>
      </node></hierarchy>''')
    runtime, calls, _ = ui_runtime([tree])
    found = qa.NativeUI(runtime).find("track-option", text="Melbourne", page=False, native_list=True, attempts=1)
    assert smoke.node_bounds(found) == (510, 420, 1900, 464)
    assert calls == []


def test_native_option_waits_for_popup_without_touching_underlying_page(monkeypatch):
    tree = ET.fromstring('''<hierarchy>
      <node resource-id="setup" scrollable="true" bounds="[0,188][2560,1712]">
        <node text="Melbourne" bounds="[86,518][618,606]"/>
      </node></hierarchy>''')
    runtime, calls, _ = ui_runtime([tree] * 3)
    monkeypatch.setattr(qa.time, "sleep", lambda _: None)
    with pytest.raises(AssertionError, match="missing"):
        qa.NativeUI(runtime).find("track-option", text="Melbourne", page=False, native_list=True, attempts=3)
    assert calls == []


def test_native_option_rejects_a_clipped_list_row(monkeypatch):
    tree = ET.fromstring('''<hierarchy>
      <node class="android.widget.ListView" scrollable="false" bounds="[100,200][700,600]">
        <node text="Melbourne" bounds="[110,580][680,624]"/>
      </node></hierarchy>''')
    runtime, calls, _ = ui_runtime([tree])
    with pytest.raises(AssertionError, match="missing"):
        qa.NativeUI(runtime).find("track-option", text="Melbourne", page=False, native_list=True, attempts=1)
    assert calls == []


def test_native_options_ignore_hidden_windows_and_nodes():
    tree = ET.fromstring('''<hierarchy>
      <window layer="5"><node class="android.widget.ListView" visible-to-user="false" bounds="[0,0][700,600]">
        <node text="Melbourne" visible-to-user="false" bounds="[10,20][690,64]"/>
      </node></window>
      <window layer="4"><node class="android.widget.ListView" visible-to-user="true" bounds="[100,200][700,600]">
        <node text="Melbourne" visible-to-user="false" bounds="[110,220][680,264]"/>
        <node text="Melbourne" visible-to-user="true" bounds="[110,280][680,324]"/>
      </node></window></hierarchy>''')
    runtime, calls, _ = ui_runtime([tree])
    found = qa.NativeUI(runtime).find("visible-option", text="Melbourne", page=False, native_list=True, attempts=1)
    assert smoke.node_bounds(found) == (110, 280, 680, 324)
    assert calls == []


def test_all_window_dump_preserves_popup_and_underlying_windows(tmp_path, monkeypatch):
    data = b'''<?xml version="1.0"?><hierarchy display-id="0">
      <window layer="3"><node class="android.widget.ListView" bounds="[100,200][700,600]">
        <node text="Melbourne" bounds="[110,220][680,264]"/>
      </node></window>
      <window layer="2"><node resource-id="setup" bounds="[0,100][800,744]"/></window>
    </hierarchy>'''
    calls = []
    monkeypatch.setattr(smoke, "OUTPUT", tmp_path)
    monkeypatch.setattr(smoke, "UI_DUMP_JAR", "/data/local/tmp/test.jar")
    monkeypatch.setattr(smoke, "adb", lambda *args, **kwargs: calls.append(args) or SimpleNamespace(stdout=data, stderr=b"", returncode=0))
    tree = smoke.ui_tree("all-windows")
    assert len(tree.findall("window")) == 2
    assert tree.find("window/node").get("class") == "android.widget.ListView"
    assert calls == [("exec-out", "env", "CLASSPATH=/data/local/tmp/test.jar", "app_process", "/", "AndroidUiHierarchy")]


def test_all_window_helper_runtime_failure_is_not_a_provider_skip(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke, "OUTPUT", tmp_path)
    monkeypatch.setattr(smoke, "UI_DUMP_JAR", "/data/local/tmp/test.jar")
    monkeypatch.setattr(smoke, "adb", lambda *args, **kwargs: SimpleNamespace(stdout=b"", stderr=b"Constructor failed", returncode=1))
    with pytest.raises(AssertionError, match="helper failed"):
        smoke.ui_tree("failed-dump")
    assert (tmp_path / "failed-dump-dump-error.txt").read_bytes() == b"Constructor failed"


def test_native_missing_control_does_not_swipe_a_non_scrolling_page_or_tabs(monkeypatch):
    tree = ET.fromstring('''<hierarchy>
      <node class="android.widget.TabWidget" scrollable="true" text="Workspaces" bounds="[0,100][800,188]"/>
      <node resource-id="setup" text="Setup Lab" scrollable="false" bounds="[0,188][800,700]"/>
    </hierarchy>''')
    runtime, calls, _ = ui_runtime([tree] * 4)
    monkeypatch.setattr(qa.time, "sleep", lambda _: None)
    with pytest.raises(AssertionError, match="missing"):
        qa.NativeUI(runtime).find("missing", text="Unavailable control", attempts=4)
    assert calls == []


def test_native_locator_searches_up_then_down_without_inventing_regions(monkeypatch):
    tree = tree_with_control("[20,680][180,724]")
    runtime, calls, _ = ui_runtime([tree] * 6)
    monkeypatch.setattr(qa.time, "sleep", lambda _: None)
    with pytest.raises(AssertionError, match="Native control missing"):
        qa.NativeUI(runtime).find("absent", text="Unknown button", attempts=6, direction="up")
    assert calls[0][4] == "220" and calls[-1][4] == "580"


def test_populated_provider_missing_control_is_failure_not_limitation(monkeypatch):
    runtime, _, _ = ui_runtime([ET.fromstring('<hierarchy><node text="Other control"/></hierarchy>')])
    monkeypatch.setattr(qa.time, "sleep", lambda _: None)
    with pytest.raises(AssertionError, match="missing"):
        qa.NativeUI(runtime).find("absent", text="Expected", attempts=1)


def test_empty_provider_is_explicit_limit():
    runtime, calls, _ = ui_runtime([ET.fromstring("<hierarchy/>")])
    with pytest.raises(smoke.UiProviderUnavailable, match="Empty accessibility"):
        qa.NativeUI(runtime).find("empty", text="Expected", attempts=1)
    assert calls == []


def test_android_resource_ids_match_and_evidence_filenames_stay_local():
    node = ET.fromstring('<node resource-id="com.google.android.documentsui:id/title"/>')
    assert qa.matches(node, resource_id="title")
    assert not qa.matches(node, resource_id="subtitle")
    runtime, _, names = ui_runtime([ET.fromstring("<hierarchy/>")])
    qa.NativeUI(runtime).snapshot("fill-com.google.android.documentsui:id/title")
    assert "/" not in names[0] and ":" not in names[0]


@pytest.mark.parametrize("attributes,expected", [
    ({"text": "Native manual A"}, True),
    ({"text": "Native manual A, Group name"}, True),
    ({"text": "Native manual A, Name", "hint": "Name"}, True),
    ({"text": "Native manual AB, Group name"}, False),
    ({"text": "Native manual A, Unexpected text"}, False),
])
def test_native_text_entry_accepts_only_exact_values_or_known_label_suffix(attributes, expected):
    assert qa.entered_value_matches(ET.Element("node", attributes), "Native manual A", "Group name") is expected


def test_native_tap_waits_for_enabled_control(monkeypatch):
    first = tree_with_control("[20,450][180,494]")
    first.find('.//*[@resource-id="engineeringSaveGroups"]').set("enabled", "false")
    runtime, calls, _ = ui_runtime([first, tree_with_control("[20,450][180,494]")])
    tapped = []
    runtime.tap_node = lambda node: tapped.append(node.get("enabled"))
    monkeypatch.setattr(qa.time, "sleep", lambda _: None)
    qa.NativeUI(runtime).tap("save", resource_id="engineeringSaveGroups")
    assert tapped == ["true"] and calls == []


def test_garage_numeric_assertion_checks_label_adjacent_value():
    tree = ET.fromstring('''<hierarchy><node bounds="[0,100][800,700]" scrollable="true">
      <node text="Front wing" bounds="[10,110][150,150]"/><node text="21" bounds="[700,110][740,150]"/>
      <node text="Rear wing" bounds="[10,155][150,195]"/><node text="17" bounds="[700,155][740,195]"/>
    </node></hierarchy>''')
    runtime, _, _ = ui_runtime([tree, tree, tree])
    ui = qa.NativeUI(runtime)
    ui.field_value("Front wing", 21)
    with pytest.raises(AssertionError, match="not 42"):
        ui.field_value("Front wing", 42)
    with pytest.raises(AssertionError, match="not 17"):
        ui.field_value("Front wing", 17)


def test_export_requires_exact_bytes_and_fixture_membership():
    data = b"Custom lap groups:\nNative manual A\nNative traffic note\n"
    proof = qa.verify_export(data, data, "Native manual A", "Native traffic note")
    assert proof["bytes"] == len(data) and len(proof["sha256"]) == 64
    with pytest.raises(AssertionError, match="differs"):
        qa.verify_export(data + b"corrupt", data, "Native manual A", "Native traffic note")
    with pytest.raises(AssertionError):
        qa.verify_export(data, data, "Other session", "Native traffic note")


def test_lap_labels_preserve_flashback_timeline_identity():
    assert qa.lap_label({"lap_num": 3, "timeline_epoch": 0}) == "Lap 3"
    assert qa.lap_label({"lap_num": 3, "timeline_epoch": 1}) == "Lap 3 · timeline 2"
    assert qa.session_label({"track_name": "Spa", "session_type": "Practice 2", "started_at": "2026-10-04T03:10:20Z"}) == "Spa · Practice 2 · 2026-10-04 03:10:20"


@pytest.mark.parametrize("first", [b"par", None])
def test_report_read_waits_through_partial_or_missing_file(tmp_path, monkeypatch, first):
    expected = b"partial report now complete"
    results = iter([SimpleNamespace(returncode=int(first is None), stdout=first or b"", stderr=b"not created yet"),
                    SimpleNamespace(returncode=0, stdout=expected, stderr=b"")])
    clock = [0.0]
    monkeypatch.setattr(qa.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(qa.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    runtime = SimpleNamespace(OUTPUT=tmp_path, adb=lambda *args, **kwargs: next(results))
    assert qa.read_saved_report(runtime, "fixture.txt", expected, timeout=2) == expected
    assert clock[0] == 0.5


@pytest.mark.parametrize("data,reason", [(b"truncated", "differs"), (b"", "Permission denied")])
def test_report_read_retains_final_mismatch_or_file_error(tmp_path, monkeypatch, data, reason):
    clock = [0.0]
    monkeypatch.setattr(qa.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(qa.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    result = SimpleNamespace(returncode=0 if data else 1, stdout=data, stderr=b"Permission denied")
    runtime = SimpleNamespace(OUTPUT=tmp_path, adb=lambda *args, **kwargs: result)
    with pytest.raises(AssertionError, match=reason):
        qa.read_saved_report(runtime, "fixture.txt", b"complete report", timeout=1)
    if data:
        assert (tmp_path / "fixture.txt.partial").read_bytes() == data


@pytest.mark.parametrize("outcome,expected", [
    ("passed", "passed"), ("picker_unavailable", "partially_tested"),
    ("empty_provider", "partially_tested"), ("missing_control", "failed"),
])
def test_native_runner_retains_honest_stage_outcomes(tmp_path, monkeypatch, outcome, expected):
    runtime = SimpleNamespace(ROOT=ROOT, OUTPUT=tmp_path, BASE="http://127.0.0.1:18000",
                              UiProviderUnavailable=smoke.UiProviderUnavailable)
    monkeypatch.setattr(qa, "validate_target", lambda *args: {"package": "com.yourpitbox.app.debug"})
    monkeypatch.setattr(qa, "prepare_window_dump", lambda *args: None)

    def fixture(command, **options):
        Path(command[-1]).write_text(json.dumps({"session_id": "fixture-native"}))
        return SimpleNamespace(returncode=0, stdout=b"fixture passed", stderr=b"")

    def setup(ui):
        if outcome == "empty_provider":
            raise smoke.UiProviderUnavailable("empty provider")
        if outcome == "missing_control":
            raise AssertionError("Expected populated control is absent")

    monkeypatch.setattr(qa.subprocess, "run", fixture)
    monkeypatch.setattr(qa, "prove_setup", setup)
    monkeypatch.setattr(qa, "prove_engineering", lambda *args: ("/engineering", "group", "note"))
    monkeypatch.setattr(qa, "prove_export", lambda *args: {
        "status": "not_tested" if outcome == "picker_unavailable" else "passed",
    })
    if expected == "failed":
        with pytest.raises(AssertionError, match="control is absent"):
            qa.run(runtime, "com.yourpitbox.app.debug", "5.3.2")
    else:
        qa.run(runtime, "com.yourpitbox.app.debug", "5.3.2")
    evidence = json.loads((tmp_path / "native-engineering-summary.json").read_text())
    assert evidence["status"] == expected
    assert evidence["session_id"] == "fixture-native"
    assert evidence["physical_device_tested"] is False
    assert "UDP fixture" in evidence["stages_passed"][0]
    if outcome == "empty_provider":
        assert evidence["reason_code"] == "accessibility_provider_unavailable"
        assert len(evidence["stages_passed"]) == 1
