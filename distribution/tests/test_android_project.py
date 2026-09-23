"""The Android build must install what the desktop build depends on.

The APK's requirements live in android/app/build.gradle.kts rather than in
pyproject.toml, because a few desktop dependencies cannot run on Android and
one (numpy) comes from a different source. This pins the mapping, so adding
a dependency to pyproject.toml without adding it to the APK fails a test
instead of failing on a phone.
"""

from __future__ import annotations

import re
import importlib.util
import tomllib
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
GRADLE = (ROOT / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
WHEELS = (ROOT / "android" / "build-wheels.sh").read_text(encoding="utf-8")
NATIVE_REQUIREMENTS = (ROOT / "android" / "native-requirements.txt").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "android-apk.yml").read_text(encoding="utf-8")
ANDROID_PYTHON = ROOT / "android" / "app" / "src" / "main" / "python"

# Desktop dependencies with no Android build, replaced or left out on purpose.
NOT_ON_ANDROID = {
    "sounddevice": "PortAudio has no Android build; voice is a later phase",
    "soundfile": "libsndfile binding used only by the voice layer",
}
# Desktop extras that are swapped for a pure-Python equivalent.
REPLACED = {"uvicorn[standard]": ("uvicorn", "wsproto")}


def _desktop_dependencies() -> list[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["dependencies"]


def _android_installs() -> list[str]:
    return re.findall(r'install\("([^"]+)"\)', GRADLE)


def _name(spec: str) -> str:
    return re.split(r"[<>=!\[ ]", spec, maxsplit=1)[0].lower()


def test_every_desktop_dependency_is_installed_or_accounted_for():
    installed = {_name(spec) for spec in _android_installs()}
    for spec in _desktop_dependencies():
        name = _name(spec)
        if name in NOT_ON_ANDROID:
            continue
        if spec in REPLACED:
            for replacement in REPLACED[spec]:
                assert replacement in installed, f"{spec} is replaced by {replacement}, which is missing"
            continue
        assert name in installed, f"{spec} from pyproject.toml is not installed by the APK"


def test_numpy_is_pinned_to_a_version_chaquopy_provides():
    # Chaquopy's repository, not PyPI, supplies numpy for Android; the pin
    # must be one it has for Python 3.13, and the suite is run against it.
    assert 'install("numpy==1.26.2")' in GRADLE


def test_socks_proxy_extra_is_installed_on_android_too():
    # Merely comparing base package names would accept plain httpx and miss
    # the optional transport which prevents the saved-key startup crash.
    assert "httpx[socks]>=0.28" in _desktop_dependencies()
    assert "httpx[socks]>=0.28" in _android_installs()


def test_the_rust_wheels_are_built_for_the_versions_the_apk_installs():
    # pydantic pins pydantic-core exactly, so the cross-compiled wheel has to
    # be the version pip resolves; the build script carries those pins.
    for package in ("pydantic-core", "jiter", "rpds-py"):
        assert re.search(rf"^{package}==\d[\w.]*$", NATIVE_REQUIREMENTS, re.MULTILINE), f"{package} has no native pin"
    assert "native-requirements.txt" in WHEELS
    assert "native-requirements.txt" in GRADLE and "--constraint" in GRADLE
    assert "--find-links" in GRADLE and 'dir("wheels")' in GRADLE


def test_the_backend_is_a_source_root_not_a_copy():
    assert 'srcDir("../../src")' in GRADLE


def test_candidate_targets_android_16_and_ci_tests_that_platform():
    assert "compileSdk = 36" in GRADLE and "targetSdk = 36" in GRADLE
    assert "platforms;android-36" in WORKFLOW and "api-level: 36" in WORKFLOW
    assert "val androidRevision = 24" in GRADLE


def test_isolated_release_qa_package_cannot_replace_existing_apps():
    assert 'providers.gradleProperty("pitbox.qaPackage")' in GRADLE
    assert 'check(!qaPackage || prepareUnsignedRelease)' in GRADLE
    assert 'applicationId = if (qaPackage) "com.yourpitbox.app.qa" else "com.yourpitbox.app"' in GRADLE
    assert 'if (qaPackage) "Your Pit Box QA" else "Your Pit Box"' in GRADLE
    assert 'android-qa-unsigned-${{ github.sha }}' in WORKFLOW
    assert WORKFLOW.index('name: android-release-unsigned-') < WORKFLOW.index('-Ppitbox.qaPackage=true')
    assert 'android:label="${pitboxAppLabel}"' in (ROOT / 'android/app/src/main/AndroidManifest.xml').read_text()


def test_release_signing_cannot_silently_produce_unsigned_apk():
    assert "val signingConfigured = missingSigning.isEmpty()" in GRADLE
    assert "check(signingConfigured)" in GRADLE
    assert 'it.name == "preReleaseBuild"' in GRADLE
    assert "dependsOn(verifyReleaseSigning)" in GRADLE
    assert 'providers.gradleProperty("pitbox.prepareUnsignedRelease")' in GRADLE
    assert '.map { it == "true" }.getOrElse(false)' in GRADLE
    assert 'check(!signingConfigured)' in GRADLE
    assert 'Do not publish this artifact' in GRADLE
    assert 'android-release-unsigned-${{ github.sha }}' in WORKFLOW
    assert "isDebuggable = false" in GRADLE


def test_the_workflow_builds_wheels_before_the_apk():
    assert "build-wheels.sh" in WORKFLOW
    assert WORKFLOW.index("build-wheels.sh") < WORKFLOW.index("assembleDebug")
    assert "python-version: \"3.13\"" in WORKFLOW


def test_native_cache_requires_both_abis_and_rejects_corrupt_or_newer_api_wheels(tmp_path):
    spec = importlib.util.spec_from_file_location("check_wheels", ROOT / "android/check-wheels.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    package = "jiter==0.16.0"
    arm = tmp_path / "jiter-0.16.0-cp313-cp313-android_24_arm64_v8a.whl"
    x86 = tmp_path / "jiter-0.16.0-cp313-cp313-android_24_x86_64.whl"

    def write_wheel(path):
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("jiter-0.16.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")

    write_wheel(arm)
    missing = module.missing_wheels(tmp_path, [package], ["arm64_v8a", "x86_64"])
    assert len(missing) == 1 and "x86_64" in missing[0]
    x86.write_bytes(b"interrupted download")
    assert module.missing_wheels(tmp_path, [package], ["x86_64"])
    write_wheel(x86)
    assert not module.missing_wheels(tmp_path, [package], ["arm64_v8a", "x86_64"])
    x86.rename(tmp_path / x86.name.replace("android_24", "android_28"))
    assert module.missing_wheels(tmp_path, [package], ["x86_64"])


def _names_provided(module_file: Path) -> set[str]:
    source = module_file.read_text(encoding="utf-8")
    return set(re.findall(r"^(?:def|class) (\w+)", source, re.MULTILINE))


def test_the_android_audio_layer_covers_every_sounddevice_and_soundfile_use():
    # The desktop opens the microphone and speaker through sounddevice and
    # reads WAVs through soundfile. Android has neither package; the stand-ins
    # in android/app/src/main/python must provide every name the backend
    # uses, or voice breaks on the phone with an AttributeError.
    used: dict[str, set[str]] = {"sd": set(), "sf": set()}
    for path in (ROOT / "src" / "pitwall").rglob("*.py"):
        for alias, name in re.findall(r"\b(sd|sf)\.([A-Za-z_]+)", path.read_text(encoding="utf-8")):
            used[alias].add(name)
    assert used["sd"] and used["sf"], "the backend's audio calls moved; update this test"
    provided_sd = _names_provided(ANDROID_PYTHON / "sounddevice.py")
    provided_sf = _names_provided(ANDROID_PYTHON / "soundfile.py")
    assert used["sd"] <= provided_sd, f"missing from Android sounddevice: {used['sd'] - provided_sd}"
    assert used["sf"] <= provided_sf, f"missing from Android soundfile: {used['sf'] - provided_sf}"


def test_the_microphone_is_declared_and_only_typed_when_granted():
    manifest = (ROOT / "android" / "app" / "src" / "main" / "AndroidManifest.xml").read_text(encoding="utf-8")
    service = (ROOT / "android" / "app" / "src" / "main" / "java" / "com" / "yourpitbox" / "app" / "PitBoxService.java").read_text(encoding="utf-8")
    assert 'android.permission.RECORD_AUDIO' in manifest
    assert 'android.permission.FOREGROUND_SERVICE_MICROPHONE' in manifest
    assert 'foregroundServiceType="connectedDevice|microphone"' in manifest
    # Android 14 throws if a service claims the microphone type without the
    # permission, so the type must be conditional on the grant.
    assert "if (microphoneGranted()) type |= ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE" in service


@pytest.mark.parametrize("invalid", [None, "session_uid", "track_name", "current_lap", "player_position", "connected", "speed_kph", "throttle", "brake", "traces"])
def test_emulator_state_probe_records_latency_without_weakening_decoding_checks(tmp_path, monkeypatch, invalid):
    spec = importlib.util.spec_from_file_location("emulator_smoke", ROOT / "android/emulator-smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.OUTPUT = tmp_path
    state = {"session_uid": 918273645, "track_name": "Suzuka", "current_lap": 8,
             "player_position": 4, "connected": True, "speed_kph": 0, "throttle": 0,
             "brake": 0, "traces": [{"t": 1}, {"t": 2}]}
    if invalid:
        state[invalid] = [] if invalid == "traces" else None
    calls = []

    def get(path, *, timeout):
        calls.append((path, timeout))
        return state

    monkeypatch.setattr(module, "get", get)
    monkeypatch.setattr(module.socket, "socket", lambda *args: nullcontext(SimpleNamespace(sendto=lambda *args: None)))
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    times = iter([10, 11.25])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(times))
    if invalid:
        with pytest.raises(AssertionError):
            module.prove_udp("test", 100)
    else:
        module.prove_udp("test", 100)
    assert calls == [("/api/state", 20)]
    assert '"request_elapsed_seconds": 1.25' in (tmp_path / "test-state-timing.json").read_text()


@pytest.mark.parametrize("clipped", ["[930,1664][1254,1736]", "[930,1616][1254,1688]"])
def test_emulator_page_action_scrolls_past_clipped_accessibility_bounds(monkeypatch, clipped):
    spec = importlib.util.spec_from_file_location("emulator_smoke", ROOT / "android/emulator-smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def tree(bounds):
        return ET.fromstring(f'''<hierarchy>
            <node scrollable="true" bounds="[0,148][2560,236]" />
            <node scrollable="true" bounds="[0,236][2560,1668]">
                <node text="Enable Wi-Fi transfers" enabled="true" bounds="{bounds}" />
            </node>
            <node bounds="[0,1668][2560,1736]" />
        </hierarchy>''')

    # The first dump settles, the second triggers a scroll, and only the fresh
    # third dump exposes a control fully inside the content viewport.
    snapshots = iter([tree(clipped), tree(clipped), tree("[930,1000][1254,1072]")])
    calls = []
    monkeypatch.setattr(module, "ui_tree", lambda label: next(snapshots))
    monkeypatch.setattr(module, "adb", lambda *args: calls.append(args))
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    node = module.find_ui("enable", "Enable Wi-Fi transfers", within_scroll_region=True)
    assert module.node_bounds(node) == (930, 1000, 1254, 1072)
    assert calls == [("shell", "input", "swipe", "1280", "1381", "1280", "808", "350")]
    module.tap_node(node)
    assert calls[-1] == ("shell", "input", "tap", "1092", "1036")


def test_emulator_fixed_tab_does_not_require_page_containment(monkeypatch):
    spec = importlib.util.spec_from_file_location("emulator_smoke", ROOT / "android/emulator-smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tree = ET.fromstring('''<hierarchy>
        <node text="CONNECTION" bounds="[500,148][750,236]" />
        <node scrollable="true" bounds="[0,236][2560,1668]" />
    </hierarchy>''')
    monkeypatch.setattr(module, "ui_tree", lambda label: tree)
    assert module.node_bounds(module.find_ui("tab", "CONNECTION")) == (500, 148, 750, 236)


@pytest.mark.parametrize("leaked", [False, True])
def test_emulator_checks_sqlite_warnings_for_current_process(tmp_path, monkeypatch, leaked):
    spec = importlib.util.spec_from_file_location("emulator_smoke", ROOT / "android/emulator-smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.OUTPUT = tmp_path
    calls = []

    def adb(*args):
        calls.append(args)
        if args[0] == "shell":
            return SimpleNamespace(stdout=b"1234\n")
        return SimpleNamespace(stdout=(b"ResourceWarning: unclosed database in <sqlite3.Connection>"
                                      if leaked else b"Application startup complete"))

    monkeypatch.setattr(module, "adb", adb)
    if leaked:
        with pytest.raises(AssertionError, match="abandoned SQLite"):
            module.prove_sqlite_lifecycle("com.yourpitbox.app.debug", "test")
    else:
        module.prove_sqlite_lifecycle("com.yourpitbox.app.debug", "test")
    assert calls[-1] == ("logcat", "-d", "--pid=1234")
    assert (tmp_path / "test-sqlite-lifecycle.json").is_file()
