"""The Android build must install what the desktop build depends on.

The APK's requirements live in android/app/build.gradle.kts rather than in
pyproject.toml, because a few desktop dependencies cannot run on Android and
one (numpy) comes from a different source. This pins the mapping, so adding
a dependency to pyproject.toml without adding it to the APK fails a test
instead of failing on a phone.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GRADLE = (ROOT / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
WHEELS = (ROOT / "android" / "build-wheels.sh").read_text(encoding="utf-8")
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


def test_the_rust_wheels_are_built_for_the_versions_the_apk_installs():
    # pydantic pins pydantic-core exactly, so the cross-compiled wheel has to
    # be the version pip resolves; the build script carries those pins.
    for package in ("pydantic-core", "jiter", "rpds-py"):
        assert re.search(rf'"{package} \d[\w.]*"', WHEELS), f"{package} is not pinned in build-wheels.sh"
    assert "--find-links" in GRADLE and 'dir("wheels")' in GRADLE


def test_the_backend_is_a_source_root_not_a_copy():
    assert 'srcDir("../../src")' in GRADLE


def test_the_workflow_builds_wheels_before_the_apk():
    assert "build-wheels.sh" in WORKFLOW
    assert WORKFLOW.index("build-wheels.sh") < WORKFLOW.index("assembleDebug")
    assert "python-version: \"3.13\"" in WORKFLOW


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
