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
    monkeypatch.setattr(build, "_pyinstaller_available", lambda: True)
    monkeypatch.setattr(build.shutil, "which", lambda _name: "/usr/bin/codesign")
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
    monkeypatch.setattr(build, "_pyinstaller_available", lambda: False)
    checks = build.preflight()
    assert not checks.ok
    assert any("PyInstaller" in problem for problem in checks.problems)


def test_pyinstaller_is_detected_by_import_not_by_path(staged, monkeypatch):
    """The build runs `sys.executable -m PyInstaller`, so preflight must agree.

    A venv invoked by path rather than activated has no pyinstaller.exe on
    PATH while the module imports fine. Checking PATH reported "PyInstaller is
    not installed" on a machine that had just built successfully with it —
    which would have blocked the real release build and left only `--dev`,
    the mode that stamps the artifact NOT SELLABLE.
    """
    # Nothing on PATH at all, but the real importability check in place.
    monkeypatch.setattr(build.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        build, "_pyinstaller_available",
        lambda: importlib.util.find_spec("PyInstaller") is not None,
    )
    checks = build.preflight()
    assert not any("PyInstaller" in problem for problem in checks.problems), (
        "preflight blocked on PyInstaller while it is importable here"
    )


def test_an_unsigned_mac_build_warns_but_does_not_block(staged, monkeypatch):
    monkeypatch.setattr(build.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(build.shutil, "which", lambda _name: None)
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


def test_inno_setup_is_found_where_winget_actually_puts_it(tmp_path, monkeypatch):
    """`winget install JRSoftware.InnoSetup` unelevated installs per-user.

    It lands in %LOCALAPPDATA%\\Programs and adds nothing to PATH. Searching
    only the two Program Files directories meant the documented release step
    ("install Inno Setup, then run --installer") ended in "Inno Setup not
    found" — and because build_installer returns None rather than failing, the
    build reported success having produced no installer at all.
    """
    monkeypatch.setattr(build.shutil, "which", lambda _name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert build._inno_compiler() is None, "nothing installed yet"

    per_user = tmp_path / "Programs" / "Inno Setup 6" / "ISCC.exe"
    per_user.parent.mkdir(parents=True)
    per_user.write_bytes(b"MZ")

    assert build._inno_compiler() == per_user


def test_a_compiler_on_the_path_still_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(build.shutil, "which", lambda name: str(tmp_path / "ISCC.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert build._inno_compiler() == tmp_path / "ISCC.exe"


def _installer_script() -> str:
    return (DIST / "packaging" / "pitwall.iss").read_text(encoding="utf-8")


def _iss_section(name: str) -> str:
    """The body of one [Section], up to the next section header."""
    script = _installer_script()
    header = f"[{name}]"
    rest = script[script.index(header) + len(header):]
    following = re.search(r"^\[", rest, flags=re.MULTILINE)
    return rest[: following.start()] if following else rest


def _setup_directives() -> dict[str, str]:
    return {
        line.split("=", 1)[0].strip().lower(): line.split("=", 1)[1].strip().lower()
        for line in _iss_section("Setup").splitlines()
        if "=" in line and not line.lstrip().startswith(";")
    }


def _code_lines(section: str) -> str:
    """A section with its comments removed.

    Needed because the comments explain the bugs these tests pin, so searching
    the raw text finds the explanation and not the defect.
    """
    return "\n".join(
        line for line in section.splitlines()
        if not line.lstrip().startswith((";", "//"))
    )


def test_the_installer_guards_against_a_running_copy():
    """Upgrading over a live process locks the executable mid-write.

    An earlier revision ran tasklist.exe in InitializeSetup and threw the
    result away, so the guard its own comment described did not exist. Restart
    Manager does the job properly; RestartApplications must stay off because
    [Run] already relaunches the app.
    """
    directives = _setup_directives()
    assert directives.get("closeapplications") == "yes"
    assert directives.get("restartapplications") == "no"
    assert "tasklist" not in _code_lines(_iss_section("Code")).lower(), (
        "the discarded-result guard is back"
    )


def test_the_install_needs_no_administrator():
    # A hobby app asking for admin rights is where a cautious buyer stops.
    assert _setup_directives().get("privilegesrequired") == "lowest"


def test_the_uninstaller_stays_quiet_when_run_silently():
    # A modal box in a silent uninstall has no one to dismiss it, so the
    # uninstaller hangs until the process is killed.
    assert "UninstallSilent" in _iss_section("Code")


def test_the_uninstaller_never_removes_recorded_sessions():
    # PitWallData holds race history and the licence. Removing it would destroy
    # the driver's data and burn their one activation on a reinstall.
    section = _iss_section("UninstallDelete")
    assert section.strip(), "section parser matched nothing"
    entries = [
        line for line in section.splitlines()
        if line.strip() and not line.lstrip().startswith(";")
    ]
    assert entries, "no uninstall-delete entries found to check"
    for entry in entries:
        assert "PitWallData" not in entry
        assert "{userprofile}" not in entry.lower()
