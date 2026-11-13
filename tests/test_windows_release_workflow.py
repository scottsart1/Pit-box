"""Windows releases from GitHub Actions keep release_windows.ps1's safeguards."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RELEASE = (WORKFLOWS / "release-windows.yml").read_text(encoding="utf-8")
BUILD = (WORKFLOWS / "windows-installer.yml").read_text(encoding="utf-8")


def _triggers(workflow: str) -> str:
    return workflow.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]


def _job(name: str) -> str:
    return RELEASE.split(f"\n  {name}:\n", 1)[1].split("\n  notify:\n", 1)[0]


def test_releases_run_only_by_hand_from_main_in_the_release_environment() -> None:
    triggers = _triggers(RELEASE)
    assert "workflow_dispatch:" in triggers
    assert "push:" not in triggers and "pull_request" not in triggers
    assert "if: inputs.stage == 'upload' && github.ref == 'refs/heads/main'" in _job("preflight")
    assert "if: inputs.stage == 'notify' && github.ref == 'refs/heads/main'" in _job("notify")
    for job in ("preflight", "upload", "notify"):
        assert "environment: release" in _job(job)
    assert "permissions:\n  contents: read\n" in RELEASE


def test_the_published_installer_is_the_one_ci_tested_and_smoke_checked() -> None:
    # The release builds through the candidate workflow, so it gets the same
    # full test suite, installed-artifact smoke and counted UDP checks.
    assert "uses: ./.github/workflows/windows-installer.yml" in _job("build")
    assert "workflow_call:" in _triggers(BUILD)
    for check in ("python -m pytest -q", "--stress-telemetry", "counted-telemetry-smoke.py"):
        assert check in BUILD
    upload = _job("upload")
    assert "needs: build" in upload
    assert '"$sha" != "${SHA256,,}"' in upload
    # The called build never attaches a GitHub Release.
    assert "attach_release: true" not in RELEASE


def test_upload_checks_before_it_builds_and_verifies_what_visitors_download() -> None:
    assert "needs: preflight" in _job("build")
    preflight = _job("preflight")
    assert "secrets.CLOUDFLARE_RELEASE_API_TOKEN" in preflight
    assert '"$live" == "$version"' in preflight
    upload = _job("upload")
    put = upload.index('r2 object put "$BUCKET/$KEY"')
    served = upload.index('"$API/installer"')
    report = upload.index("GITHUB_STEP_SUMMARY")
    assert put < served < report
    assert "-H 'Range: bytes=0-'" in upload
    assert '"$served" == "$SHA256"' in upload


def test_the_update_is_announced_only_for_the_installer_the_download_serves() -> None:
    notify = _job("notify")
    assert notify.index('test "$served" = "$expected"') < notify.index("distribution.tools.publish_release")
    assert "--platform windows" in notify
    assert "-H 'Range: bytes=0-'" in notify
    assert "secrets.RELEASE_PUBLISH_TOKEN" in notify


def test_the_release_needs_no_signing_key_and_uses_the_release_token() -> None:
    assert "ANDROID_KEYSTORE" not in RELEASE
    assert "secrets.CLOUDFLARE_RELEASE_API_TOKEN" in RELEASE
    assert "secrets.CLOUDFLARE_API_TOKEN" not in RELEASE
