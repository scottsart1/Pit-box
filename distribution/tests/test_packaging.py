"""Preflight must refuse to build anything that cannot legitimately be sold.

These are the checks standing between a careless `build` and shipping an app
signed with a key whose private half is in the repo, or one that phones an
endpoint that does not exist.
"""

from __future__ import annotations

import ast
import re
import importlib.util
import sys
from pathlib import Path

import pytest

DIST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIST.parent))

from distribution.packaging import build  # noqa: E402

PRODUCTION_KEY = "Zm9ydGhlc2FrZW9mYXRlc3Rvbmx5bm90YXJlYWxrZXk="


@pytest.fixture
def staged(tmp_path, monkeypatch):
    """A build tree with every check passing, so each test breaks exactly one."""
    licensing = tmp_path / "licensing"
    licensing.mkdir(parents=True)
    (licensing / "embedded_public_key.txt").write_text(PRODUCTION_KEY, encoding="ascii")
    (tmp_path.parent / "static").mkdir(exist_ok=True)
    (tmp_path.parent / "static" / "index.html").write_text("<p>ok</p>", encoding="utf-8")

    monkeypatch.setattr(build, "DIST_ROOT", tmp_path)
    monkeypatch.setattr(build, "REPO_ROOT", tmp_path.parent)
    monkeypatch.setattr(build.shutil, "which", lambda _name: "/usr/bin/pyinstaller")
    monkeypatch.setattr(build.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        "distribution.launcher.ACTIVATION_ENDPOINT",
        "https://activation.pitwall.app/activate",
    )
    return tmp_path


def test_a_fully_configured_tree_passes(staged):
    checks = build.preflight()
    assert checks.ok, checks.report()
    assert checks.problems == ()


def test_the_development_key_blocks_the_build(staged):
    (staged / "licensing" / "embedded_public_key.txt").write_text(
        build.DEV_PUBLIC_KEY, encoding="ascii"
    )
    checks = build.preflight()
    assert not checks.ok
    assert any("DEVELOPMENT key" in problem for problem in checks.problems)


def test_the_recorded_dev_key_matches_the_committed_one():
    # If keygen is run, this must be updated or the guard silently stops
    # matching and a dev-keyed build could ship.
    committed = (DIST / "licensing" / "embedded_public_key.txt").read_text().strip()
    assert committed == build.DEV_PUBLIC_KEY, (
        "embedded_public_key.txt changed; update build.DEV_PUBLIC_KEY to the "
        "old value or drop the check if this is now a production key."
    )


def test_the_placeholder_activation_endpoint_blocks_the_build(staged, monkeypatch):
    monkeypatch.setattr(
        "distribution.launcher.ACTIVATION_ENDPOINT",
        "https://activation.example.invalid/activate",
    )
    checks = build.preflight()
    assert not checks.ok
    assert any("ACTIVATION_ENDPOINT" in problem for problem in checks.problems)


def test_a_missing_public_key_blocks_the_build(staged):
    (staged / "licensing" / "embedded_public_key.txt").unlink()
    checks = build.preflight()
    assert not checks.ok
    assert any("missing" in problem for problem in checks.problems)


def test_missing_pyinstaller_blocks_the_build(staged, monkeypatch):
    monkeypatch.setattr(build.shutil, "which", lambda _name: None)
    checks = build.preflight()
    assert not checks.ok
    assert any("PyInstaller" in problem for problem in checks.problems)


def test_an_unsigned_mac_build_warns_but_does_not_block(staged, monkeypatch):
    monkeypatch.setattr(build.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        build.shutil, "which",
        lambda name: None if name == "codesign" else "/usr/bin/pyinstaller",
    )
    checks = build.preflight()
    # Still buildable — you can produce an unsigned .app to test locally — but
    # the reason it will not open elsewhere has to be stated.
    assert checks.ok
    assert any("Gatekeeper" in warning for warning in checks.warnings)


def test_no_manifest_is_left_in_the_source_tree():
    # A live manifest in a working checkout makes the next edit to any guarded
    # module read as tampered, which breaks every gate test at once.
    assert not (DIST / "licensing" / "integrity_manifest.txt").exists()

    from distribution.licensing import gate

    assert gate.integrity_ok() is True


def test_a_frozen_build_hashes_its_executable_not_its_source(tmp_path, monkeypatch):
    """The shipped app has no .py files — PyInstaller compiles them away.

    Hashing source paths crashed the packaged app on every launch with
    FileNotFoundError while working perfectly in development, because the
    files only exist here.
    """
    from distribution.licensing import gate

    fake_exe = tmp_path / "Pit Wall.exe"
    fake_exe.write_bytes(b"MZ-pretend-executable")
    monkeypatch.setattr(gate.sys, "frozen", True, raising=False)
    monkeypatch.setattr(gate.sys, "executable", str(fake_exe))

    digest = gate._module_digest()
    assert digest == gate.digest_for_packaged_executable(fake_exe)

    # Changing a byte of the executable changes the digest.
    fake_exe.write_bytes(b"MZ-pretend-executabl3")
    assert gate._module_digest() != digest


def test_the_build_stamp_matches_what_the_app_will_compute(tmp_path):
    """Build-time and runtime must agree, or the app never starts.

    Both go through gate.digest_for_packaged_executable for exactly this
    reason: two separate hash implementations would drift into permanently
    disagreeing, and the symptom would be "refuses to start" for every buyer.
    """
    from distribution.licensing import gate

    app_dir = tmp_path / "Pit Wall"
    app_dir.mkdir()
    executable = app_dir / "Pit Wall.exe"
    executable.write_bytes(b"MZ-pretend-executable")

    stamped = build.stamp_manifest(app_dir, executable)
    written = app_dir / "_internal" / "distribution" / "licensing" / gate.MANIFEST_NAME
    assert written.read_text(encoding="ascii").strip() == stamped
    assert stamped == gate.digest_for_packaged_executable(executable)


def test_the_spec_does_not_try_to_collect_the_manifest():
    # It cannot: a frozen build hashes its own executable, so the value does
    # not exist until after PyInstaller has run. Any datas entry for it would
    # be collecting a stale file from an earlier build.
    spec = (DIST / "packaging" / "pitwall.spec").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in spec.splitlines() if not line.lstrip().startswith("#")
    )
    assert "integrity_manifest" not in code


def test_the_entry_point_uses_absolute_imports_only():
    """The frozen app runs main.py as __main__, which has no parent package.

    A relative import there raises ImportError before anything else happens,
    and because the build is windowed there is no console to show it: the
    packaged app simply flashes and vanishes. It is invisible in development,
    where `python -m distribution.main` supplies the package context.
    """
    source = (DIST / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    relative = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level > 0
    ]
    assert not relative, (
        "distribution/main.py must use absolute imports; found relative import "
        f"of {[n.module for n in relative]} at line {relative[0].lineno}"
    )


def test_the_entry_point_imports_resolve_standalone():
    # Belt and braces: import the module the way the frozen app does, with only
    # the repo root on the path and no package context.
    spec = importlib.util.spec_from_file_location("__pitwall_entry__", DIST / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.main)


def test_the_launcher_calls_a_function_the_app_actually_has():
    """The packaged entry point hands over to pitwall.main at the very end.

    A wrong name here survives every unit test and only shows up after a
    successful activation, in a windowed build with no console — the app
    simply disappears. It is the last line of the happy path and the easiest
    to get wrong, because renaming it in the app breaks nothing else.
    """
    import pitwall.main

    source = (DIST / "main.py").read_text(encoding="utf-8")
    called = re.findall(r"from pitwall\.main import (\w+)", source)
    assert called, "the entry point no longer imports from pitwall.main"
    for name in called:
        assert hasattr(pitwall.main, name), f"pitwall.main has no {name!r}"
        assert callable(getattr(pitwall.main, name))


def test_the_release_steps_name_every_mac_only_command():
    steps = build.macos_release_steps()
    for command in ("codesign", "notarytool", "stapler", "spctl"):
        assert command in steps
