"""The version is written once, in pitwall/__init__.py, and read everywhere.

A 4.9.3 build answered ``{"version": "4.9.2"}`` from /api/health because the
FastAPI app object carried its own copy of the version string, bumped by hand
on some releases and not others. Everything that reports a version has to read
the package's, and the packaging metadata has to agree with it.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pitwall

ROOT = Path(__file__).resolve().parents[1]


def _source(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def test_the_app_object_reads_the_package_version() -> None:
    app_py = _source("src", "pitwall", "app.py")
    assert 'version=__version__' in app_py
    assert not re.search(r'FastAPI\([^)]*version="\d', app_py), (
        "the FastAPI app must not carry its own version literal"
    )


def test_capture_metadata_reads_the_package_version() -> None:
    capture = _source("src", "pitwall", "capture_service.py")
    assert '"app_version": __version__' in capture
    assert not re.search(r'"app_version":\s*"\d', capture)


def test_pyproject_agrees_with_the_package() -> None:
    project = tomllib.loads(_source("pyproject.toml"))["project"]
    assert project["version"] == pitwall.__version__


def test_the_installer_default_agrees_with_the_package() -> None:
    build = _source("distribution", "packaging", "build.py")
    match = re.search(r'version: str = "([0-9.]+)"', build)
    assert match, "build_installer must declare a default version"
    assert match.group(1) == pitwall.__version__
