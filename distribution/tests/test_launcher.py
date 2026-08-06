"""The packaged launch decision tree, exercised without opening a window."""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DIST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIST.parent))

from distribution import launcher  # noqa: E402
from distribution.licensing import gate  # noqa: E402
from distribution.licensing.activation_client import ActivationError  # noqa: E402
from distribution.licensing.entitlement import (  # noqa: E402
    ENTITLEMENT_VERSION,
    Entitlement,
    canonical_bytes,
)
from distribution.licensing.license_store import License, save_license  # noqa: E402

CODE = "PITW-ABCDE-FGHJK-MNPQR"
DEVICE = "f" * 64


def _sign(entitlement: Entitlement) -> str:
    key_b64 = (DIST / ".secrets" / "signing_key.ed25519").read_text().strip()
    private = Ed25519PrivateKey.from_private_bytes(base64.b64decode(key_b64))
    return base64.b64encode(private.sign(canonical_bytes(entitlement))).decode()


def _entitlement() -> Entitlement:
    return Entitlement(ENTITLEMENT_VERSION, CODE, "pitwall-desktop-1", "2026-08")


class _Recorder:
    """Captures what the launcher asked the UI and the app to do."""

    def __init__(self, entries: list[launcher.FirstRunInput | None] | None = None) -> None:
        self.entries = list(entries or [])
        self.prompts: list[str] = []
        self.errors: list[str] = []
        self.started = 0
        self.saved_keys: list[str] = []

    def prompt(self, message: str) -> launcher.FirstRunInput | None:
        self.prompts.append(message)
        return self.entries.pop(0) if self.entries else None

    def show_error(self, message: str) -> None:
        self.errors.append(message)

    def start_app(self) -> None:
        self.started += 1

    def save_api_key(self, key: str) -> None:
        self.saved_keys.append(key)


def _launch(recorder: _Recorder, config_dir: Path) -> launcher.LaunchResult:
    return launcher.launch(
        config_dir,
        endpoint="https://activation.test/activate",
        prompt=recorder.prompt,
        show_error=recorder.show_error,
        start_app=recorder.start_app,
        save_api_key=recorder.save_api_key,
    )


@pytest.fixture
def bound_device(monkeypatch):
    monkeypatch.setattr("distribution.licensing.license_store.device_hash", lambda: DEVICE)
    monkeypatch.setattr("distribution.licensing.gate.device_hash", lambda: DEVICE)
    return DEVICE


def test_a_licensed_install_starts_without_asking_anything(tmp_path, bound_device):
    entitlement = _entitlement()
    save_license(
        tmp_path,
        License(entitlement, _sign(entitlement), DEVICE, "2026-08-06T00:00:00Z"),
    )
    recorder = _Recorder()

    result = _launch(recorder, tmp_path)

    assert result.outcome is launcher.LaunchOutcome.RUN
    assert recorder.started == 1
    assert recorder.prompts == []


def test_first_run_activates_saves_the_key_then_starts(tmp_path, bound_device, monkeypatch):
    entitlement = _entitlement()
    signature = _sign(entitlement)
    from distribution.licensing import gate as gate_module
    from distribution.licensing.activation_client import ActivationResult

    monkeypatch.setattr(
        gate_module, "activate",
        lambda endpoint, code, dev: ActivationResult(entitlement, signature),
    )
    recorder = _Recorder([launcher.FirstRunInput(CODE, "sk-live-key-long-enough-here")])

    result = _launch(recorder, tmp_path)

    assert result.outcome is launcher.LaunchOutcome.ACTIVATED
    assert recorder.started == 1
    assert recorder.saved_keys == ["sk-live-key-long-enough-here"]
    # And the licence now persists for the next launch.
    assert (tmp_path / "license.json").exists()


def test_a_bad_code_returns_to_the_form_instead_of_exiting(tmp_path, bound_device, monkeypatch):
    entitlement = _entitlement()
    signature = _sign(entitlement)
    from distribution.licensing import gate as gate_module
    from distribution.licensing.activation_client import ActivationResult

    calls: list[str] = []

    def flaky(endpoint, code, dev):
        calls.append(code)
        if len(calls) == 1:
            raise ActivationError("That activation code was not recognized.", "code_not_found")
        return ActivationResult(entitlement, signature)

    monkeypatch.setattr(gate_module, "activate", flaky)
    recorder = _Recorder([
        launcher.FirstRunInput("PITW-WRONG-WRONG-WRONG", "sk-key-long-enough-value"),
        launcher.FirstRunInput(CODE, "sk-key-long-enough-value"),
    ])

    result = _launch(recorder, tmp_path)

    assert result.outcome is launcher.LaunchOutcome.ACTIVATED
    assert len(recorder.prompts) == 2
    # The second prompt carries the reason the first attempt failed.
    assert "not recognized" in recorder.prompts[1]
    assert recorder.started == 1


def test_closing_the_first_run_window_starts_nothing(tmp_path, bound_device):
    recorder = _Recorder([])  # prompt returns None immediately

    result = _launch(recorder, tmp_path)

    assert result.outcome is launcher.LaunchOutcome.CANCELLED
    assert recorder.started == 0
    assert not (tmp_path / "license.json").exists()


def test_a_tampered_build_is_blocked_and_nothing_is_deleted(tmp_path, monkeypatch):
    install = tmp_path / "install"
    install.mkdir()
    (install / "pitwall.exe").write_text("payload", encoding="utf-8")
    manifest = tmp_path / "integrity_manifest.txt"
    manifest.write_text("0" * 64 + "\n", encoding="ascii")
    monkeypatch.setattr(gate, "_MANIFEST", manifest)
    recorder = _Recorder()

    result = _launch(recorder, tmp_path)

    assert result.outcome is launcher.LaunchOutcome.BLOCKED
    assert recorder.started == 0
    assert recorder.prompts == []
    assert recorder.errors and "has not started" in recorder.errors[0]
    assert (install / "pitwall.exe").read_text(encoding="utf-8") == "payload"


def test_a_failed_key_save_still_lets_the_app_start(tmp_path, bound_device, monkeypatch):
    entitlement = _entitlement()
    signature = _sign(entitlement)
    from distribution.licensing import gate as gate_module
    from distribution.licensing.activation_client import ActivationResult

    monkeypatch.setattr(
        gate_module, "activate",
        lambda endpoint, code, dev: ActivationResult(entitlement, signature),
    )
    recorder = _Recorder([launcher.FirstRunInput(CODE, "sk-key-long-enough-value")])

    def refuse(_key: str) -> None:
        raise OSError("read-only filesystem")

    recorder.save_api_key = refuse  # type: ignore[method-assign]

    result = _launch(recorder, tmp_path)

    # A paid, activated licence must never be held hostage by a settings write.
    assert result.outcome is launcher.LaunchOutcome.ACTIVATED
    assert recorder.started == 1
    assert any("Connection tab" in message for message in recorder.errors)


def test_the_shipped_endpoint_placeholder_is_obviously_not_real():
    # A build that forgot to point at the deployed Worker must fail loudly at
    # activation rather than quietly talking to somewhere unintended.
    assert ".invalid" in launcher.ACTIVATION_ENDPOINT
