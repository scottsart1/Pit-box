"""A development installer must not silently become a public signed release."""
import base64
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from distribution.packaging import build, signing

ROOT = Path(__file__).resolve().parents[2]


def test_package_version_does_not_require_an_editable_install():
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-c", "from distribution.packaging.build import package_version; print(package_version())"],
        cwd=ROOT, env=environment, capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == build.package_version()


@pytest.fixture
def configured(monkeypatch, tmp_path):
    tool = tmp_path / "signtool.exe"
    tool.write_bytes(b"fixture only")
    monkeypatch.setenv("PITBOX_WINDOWS_CERT_SHA1", "A" * 40)
    monkeypatch.setenv("PITBOX_TIMESTAMP_URL", "https://timestamp.test.invalid")
    monkeypatch.setenv("PITBOX_SIGNTOOL_PATH", str(tool))
    return tool


def test_unsigned_candidate_is_allowed_but_production_requires_configuration(monkeypatch):
    for key in ("PITBOX_WINDOWS_CERT_SHA1", "PITBOX_TIMESTAMP_URL", "PITBOX_SIGNTOOL_PATH"):
        monkeypatch.delenv(key, raising=False)
    assert signing.configuration() is None
    with pytest.raises(ValueError, match="publisher certificate"):
        signing.configuration(required=True)


@pytest.mark.parametrize("endpoint", ["http://timestamp.invalid", "https://user:secret@host.invalid",
    "https://host.invalid/?token=secret", "https://host.invalid/#secret", "", "file:///temp/time"])
def test_timestamp_configuration_never_accepts_credentials_or_insecure_urls(configured, monkeypatch, endpoint):
    monkeypatch.setenv("PITBOX_TIMESTAMP_URL", endpoint)
    with pytest.raises(ValueError, match="credential-free HTTPS"):
        signing.configuration(required=True)


def test_signing_uses_store_identity_sha256_timestamp_and_verification(configured, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(signing.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)))
    artifact = tmp_path / "PitWall-Setup.exe"
    assert signing.sign_windows_artifact(artifact)
    assert calls[0][0] == [str(configured), "sign", "/s", "My", "/sha1", "A" * 40,
        "/fd", "SHA256", "/tr", "https://timestamp.test.invalid", "/td", "SHA256", str(artifact)]
    assert calls[1][0] == [str(configured), "verify", "/pa", "/all", "/tw", str(artifact)]
    assert all(options == {"check": True} for _, options in calls)


def test_signing_failure_never_proceeds(configured, monkeypatch, tmp_path):
    def fail(args, **kwargs):
        raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(signing.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        signing.sign_windows_artifact(tmp_path / "app.exe")


def test_windows_integrity_manifest_is_stamped_after_signing(monkeypatch, tmp_path):
    app_dir = tmp_path / "dist" / build.APP_NAME
    app_dir.mkdir(parents=True)
    executable = app_dir / (build.APP_NAME + ".exe")
    executable.write_bytes(b"unsigned")
    monkeypatch.setattr(build, "BUILD_DIR", tmp_path)
    monkeypatch.setattr(build, "OUTPUT_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(build, "_pyinstaller", lambda: None)
    monkeypatch.setattr(build, "write_eula", lambda path: None)
    monkeypatch.setattr(build, "sign_windows_artifact", lambda path: path.write_bytes(b"signed"))
    seen = []
    monkeypatch.setattr(build, "stamp_manifest", lambda app, path: seen.append(path.read_bytes()) or "digest")
    build.build_windows()
    assert seen == [b"signed"]


def test_installer_uses_engine_version_and_signs_finished_file(monkeypatch, tmp_path):
    from pitwall import __version__
    calls, signed = [], []
    monkeypatch.setattr(build, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(build, "_inno_compiler", lambda: tmp_path / "ISCC.exe")

    def compile(args):
        calls.append(args)
        (tmp_path / "PitWall-Setup.exe").write_bytes(b"fixture installer")

    monkeypatch.setattr(build, "_run", compile)
    monkeypatch.setattr(build, "sign_windows_artifact", signed.append)
    result = build.build_installer(tmp_path / "app")
    assert f"/DAppVersion={__version__}" in calls[0]
    assert signed == [result]


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell verification guard")
@pytest.mark.parametrize("status,publisher,timestamp,allowed", [
    ("NotSigned", "A" * 40, True, False), ("HashMismatch", "A" * 40, True, False),
    ("Valid", "B" * 40, True, False), ("Valid", "A" * 40, False, False),
    ("Valid", "A" * 40, True, True),
])
def test_production_signature_guard_behavior(tmp_path, status, publisher, timestamp, allowed):
    # Only the OS signature lookup is stubbed. Exercise the actual public gate.
    artifact = tmp_path / "fixture.exe"
    artifact.write_bytes(b"not a real signed binary")
    guard = ROOT / "distribution/packaging/verify-production-signature.ps1"
    script = f"""
. '{str(guard).replace("'", "''")}'
function Get-AuthenticodeSignature {{
  param($LiteralPath)
  [pscustomobject]@{{Status='{status}';SignerCertificate=[pscustomobject]@{{Thumbprint='{publisher}'}};
     TimeStamperCertificate={"[pscustomobject]@{Subject='test'}" if timestamp else "$null"}}}
}}
try {{ Assert-ProductionSignature -Path '{str(artifact).replace("'", "''")}' -ExpectedThumbprint '{'A' * 40}'; exit 0 }}
catch {{ exit 7 }}
"""
    result = subprocess.run([shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-NonInteractive",
        "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode()], timeout=20)
    assert result.returncode == (0 if allowed else 7)
