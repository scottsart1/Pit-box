"""Preflight must refuse to build anything that cannot legitimately be sold.

These are the checks standing between a careless `build` and shipping an app
signed with a key whose private half is in the repo, or one that phones an
endpoint that does not exist.
"""

from __future__ import annotations

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


def test_the_release_steps_name_every_mac_only_command():
    steps = build.macos_release_steps()
    for command in ("codesign", "notarytool", "stapler", "spctl"):
        assert command in steps
