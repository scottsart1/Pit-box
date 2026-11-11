"""The website deploy workflow publishes what build_site builds, from main only."""

from __future__ import annotations

import re
from pathlib import Path

from distribution.website import build_site

ROOT = Path(__file__).parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "deploy-website.yml").read_text(encoding="utf-8")


def _trigger_paths() -> list[str]:
    block = WORKFLOW.split("    paths:\n", 1)[1].split("  workflow_dispatch:", 1)[0]
    return re.findall(r"^\s+- (\S+)$", block, flags=re.MULTILINE)


def test_every_build_input_redeploys_the_site() -> None:
    # A directory build_site reads from but the trigger does not watch would
    # change the site in main without ever reaching yourpitbox.com.
    paths = _trigger_paths()
    for source in (build_site.SITE_DIR, build_site.SCREENSHOT_DIR, build_site.DRIVER_DASHBOARD_DIR):
        relative = source.relative_to(build_site.REPO_ROOT).as_posix()
        assert f"{relative}/**" in paths, relative
    assert build_site.ASSET_DIR.is_relative_to(build_site.SITE_DIR)
    assert ".github/workflows/deploy-website.yml" in paths


def test_only_main_deploys_and_always_to_the_production_branch() -> None:
    triggers = WORKFLOW.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    assert "branches: [main]" in triggers
    assert "pull_request" not in triggers
    # Without --branch main, Pages files the upload as a preview.
    assert re.search(
        r"pages deploy _site --project-name pitwall --branch main$", WORKFLOW, flags=re.MULTILINE
    )
    assert "permissions:\n  contents: read\n" in WORKFLOW


def test_the_tests_gate_the_build_and_the_build_gates_the_deploy() -> None:
    tests = WORKFLOW.index("python -m pytest distribution/tests/test_website.py")
    build = WORKFLOW.index("python -m distribution.website.build_site")
    deploy = WORKFLOW.index("pages deploy _site")
    assert tests < build < deploy
