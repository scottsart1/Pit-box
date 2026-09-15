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
