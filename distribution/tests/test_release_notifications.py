"""No network or subscriber sends: exercise the final publication gate."""
import hashlib
from pathlib import Path

import httpx
import pytest

from distribution.tools.publish_release import publish

ARTIFACT = b"fixture installer"
SHA = hashlib.sha256(ARTIFACT).hexdigest()
TOKEN = "fixture-private-publisher-key-not-a-secret"


def test_verified_public_bytes_and_site_precede_authenticated_publication():
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path == "/installer":
            assert request.headers["range"] == "bytes=0-"
            assert "authorization" not in request.headers
            return httpx.Response(206, headers={"content-range": f"bytes 0-{len(ARTIFACT)-1}/{len(ARTIFACT)}"}, content=ARTIFACT)
        if request.url.host == "yourpitbox.com":
            assert "authorization" not in request.headers
            return httpx.Response(200, text=f'<section id="windows-release">4.12.0 {SHA}</section>')
        assert request.headers["authorization"] == "Bearer " + TOKEN
        assert b'"announce":false' in request.content
        return httpx.Response(200, json={"ok": True, "release": {"sha256": SHA}, "email": "not_requested"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = publish(client, platform="windows", version="4.12.0", size=len(ARTIFACT), sha256=SHA, notes="Update", token=TOKEN, announce=False)
    assert result["email"] == "not_requested"
    assert [request.method for request in calls] == ["GET", "GET", "POST"]


@pytest.mark.parametrize("failure", ["hash", "range", "redirect", "site"])
def test_no_publication_on_stale_or_unverified_artifact(failure):
    calls = []

    def handle(request):
        calls.append(request)
        assert request.method == "GET"
        assert "authorization" not in request.headers
        if request.url.path == "/installer":
            if failure == "redirect":
                return httpx.Response(302, headers={"location": "https://untrusted.test"})
            return httpx.Response(206, headers={"content-range": "bad" if failure == "range" else f"bytes 0-{len(ARTIFACT)-1}/{len(ARTIFACT)}"}, content=b"corrupt" if failure == "hash" else ARTIFACT)
        return httpx.Response(200, text="Previous version")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client, pytest.raises(ValueError):
        publish(client, platform="windows", version="4.12.0", size=len(ARTIFACT), sha256=SHA, notes="Update", token=TOKEN)
    assert len(calls) <= 2


def test_windows_release_publishes_notifications_last_and_migrates_before_worker():
    script = (Path(__file__).parents[2] / "release_windows.ps1").read_text()
    assert script.index("migrations/0008_release_announcements.sql") < script.index('Step "Deploy the activation Worker"')
    assert script.index("pages deploy _site") < script.index("'publish_release.ps1'")
