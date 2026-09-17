"""The one-click release script keeps the order that keeps the site honest."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = (ROOT / "release_windows.ps1").read_text(encoding="utf-8")
WRAPPER = (ROOT / "release_windows.bat").read_text(encoding="utf-8")


def test_the_installer_is_published_before_the_site():
    # The site describes the current build. Deploying the page before the
    # installer would advertise features the download does not have yet.
    upload = SCRIPT.index("r2 object put pitwall-downloads/PitWall-Setup.exe")
    deploy = SCRIPT.index("pages deploy _site")
    assert upload < deploy


def test_the_site_deploys_to_the_production_branch():
    # Without --branch main, Pages files the deployment under the local git
    # branch and anything else becomes a preview that never reaches
    # yourpitbox.com. This happened in the 4.7.0 release.
    assert re.search(r"pages deploy _site --project-name pitwall --branch main", SCRIPT)


def test_the_upload_is_verified_by_round_trip_hash():
    assert "r2 object get pitwall-downloads/PitWall-Setup.exe" in SCRIPT
    assert SCRIPT.count("Get-FileHash") >= 2  # local build and R2 round-trip


def test_the_tests_gate_the_build_and_the_build_gates_the_deploys():
    tests = SCRIPT.index("-m pytest -q")
    build = SCRIPT.index("-m distribution.packaging.build --installer")
    upload = SCRIPT.index("r2 object put")
    assert tests < build < upload


def test_the_wrapper_scopes_the_policy_bypass_to_one_process():
    assert "-ExecutionPolicy Bypass" in WRAPPER
    assert "release_windows.ps1" in WRAPPER


def test_public_upload_requires_signing_and_clean_fast_forward_checkout():
    assert SCRIPT.index('configuration(required=True)') < SCRIPT.index('-m distribution.packaging.build --installer')
    assert SCRIPT.index('Assert-ProductionSignature -Path $installer') < SCRIPT.index('r2 object put pitwall-downloads')
    assert 'Assert-ProductionSignature -Path $frozenApp' in SCRIPT
    assert 'git status --porcelain' in SCRIPT and 'git pull --ff-only' in SCRIPT


def test_ci_does_not_publish_unsigned_candidates_by_default():
    workflow = (ROOT / '.github/workflows/windows-installer.yml').read_text(encoding='utf-8')
    attach_option = workflow.split('attach_release:', 1)[1].split('permissions:', 1)[0]
    assert 'default: false' in attach_option
    assert workflow.index('Assert-ProductionSignature') < workflow.index('gh release create')


def test_unsigned_direct_release_is_explicit_and_does_not_accept_bad_signatures():
    assert 'param([switch]$AllowUnsignedRelease)' in SCRIPT
    assert "if ($signature.Status -ne 'NotSigned')" in SCRIPT
    assert 'Assert-ProductionSignature -Path $artifact' in SCRIPT
    assert 'never tell users to disable security protections' in SCRIPT
