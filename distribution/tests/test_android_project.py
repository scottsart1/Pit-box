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
