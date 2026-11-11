"""Android releases from GitHub Actions keep the release PC's safeguards."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RELEASE = (WORKFLOWS / "release-android.yml").read_text(encoding="utf-8")
WORKER = (WORKFLOWS / "deploy-worker.yml").read_text(encoding="utf-8")
PLAN = (ROOT / "docs" / "release-plan-5.6.0.md").read_text(encoding="utf-8")


def _triggers(workflow: str) -> str:
    return workflow.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]


def test_releases_run_only_by_hand_from_main_in_the_release_environment() -> None:
    triggers = _triggers(RELEASE)
    assert "workflow_dispatch:" in triggers
    assert "push:" not in triggers and "pull_request" not in triggers
    for job in ("upload", "notify"):
        body = RELEASE.split(f"\n  {job}:\n", 1)[1].split("\n  notify:\n", 1)[0]
        assert f"if: inputs.stage == '{job}' && github.ref == 'refs/heads/main'" in body
        assert "environment: release" in body
    assert "permissions:\n  contents: read\n" in RELEASE


def test_only_the_existing_release_key_is_accepted() -> None:
    # The digest recorded when the key was created; Android refuses an update
    # signed by any other key, so a different one must never be published.
    recorded = re.search(r"certificate SHA-256\s+`([0-9a-f]{64})`", PLAN).group(1)
    assert f"RELEASE_CERT_SHA256: {recorded}" in RELEASE
    assert "PITBOX_KEY_ALIAS: pitbox-release" in RELEASE
    assert "trap 'rm -f \"$PITBOX_KEYSTORE_PATH\"' EXIT" in RELEASE
    assert 'test "$certs" = 1' in RELEASE


def test_upload_checks_before_it_builds_and_verifies_before_it_uploads() -> None:
    existing = RELEASE.index('r2 object get "$BUCKET/$NAME"')
    build = RELEASE.index("./gradlew --no-daemon assembleRelease")
    verify = RELEASE.index("apksigner\" verify --verbose --print-certs")
    upload = RELEASE.index('r2 object put "$BUCKET/$NAME"')
    assert existing < build < verify < upload
    assert 'echo "name=YourPitBox-$version-android.$revision.apk"' in RELEASE
    assert "versionCode='$REVISION' versionName='$VERSION-android.$REVISION'" in RELEASE


def test_the_update_is_announced_only_for_the_apk_the_download_serves() -> None:
    notify = RELEASE.split("\n  notify:\n", 1)[1]
    assert notify.index('test "$served" = "$EXPECTED"') < notify.index("distribution.tools.publish_release")
    assert "-H 'Range: bytes=0-'" in notify


def test_the_download_service_deploys_from_main_after_its_tests_and_upload_check() -> None:
    triggers = _triggers(WORKER)
    assert "branches: [main]" in triggers and "pull_request" not in triggers
    for path in ("distribution/activation-server/src/**", "distribution/activation-server/wrangler.toml"):
        assert path in triggers
    tests = WORKER.index("node --test distribution/activation-server/tests/*.mjs")
    uploaded = WORKER.index('r2 object get "pitwall-downloads/$key"')
    deploy = WORKER.index("run: $WRANGLER deploy")
    served = WORKER.index('curl -sS --fail -I "$API/android"')
    assert tests < uploaded < deploy < served
