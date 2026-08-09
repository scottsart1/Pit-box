"""End-to-end proof of the licensing crypto and activation design.

Signs like the code-gen tool, verifies like the app, simulates the server's
response, binds to a device, and confirms every tamper path is rejected.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DIST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIST.parent))

from distribution.licensing import gate  # noqa: E402
from distribution.licensing.codes import generate_code, is_valid_code, normalize_code  # noqa: E402
from distribution.licensing.entitlement import (  # noqa: E402
    ENTITLEMENT_VERSION,
    Entitlement,
    canonical_bytes,
)
from distribution.licensing.license_store import (  # noqa: E402
    License,
    LicenseInvalid,
    load_and_validate,
    save_license,
)
from distribution.licensing.verify import VerificationError, verify_entitlement  # noqa: E402


def _sign(entitlement: Entitlement) -> str:
    key_b64 = (DIST / ".secrets" / "signing_key.ed25519").read_text().strip()
    private = Ed25519PrivateKey.from_private_bytes(base64.b64decode(key_b64))
    return base64.b64encode(private.sign(canonical_bytes(entitlement))).decode()


def _entitlement(code_id: str = "PITW-ABCDE-FGHJK-MNPQR") -> Entitlement:
    return Entitlement(ENTITLEMENT_VERSION, code_id, "pitwall-desktop-1", "2026-08")


# --------------------------------------------------------------------------
# Code format
# --------------------------------------------------------------------------


def test_generated_codes_are_valid_and_canonical():
    for _ in range(200):
        code = generate_code()
        assert is_valid_code(code)
        assert normalize_code(code) == code


def test_normalize_tolerates_common_typos():
    # lower case, spaces, missing prefix, Crockford O/I/L substitutions
    assert normalize_code("pitw abcde fghjk mnpqr") == "PITW-ABCDE-FGHJK-MNPQR"
    assert normalize_code("ABCDE-FGHJK-MNPQR") == "PITW-ABCDE-FGHJK-MNPQR"
    assert normalize_code("PITW-0BCDE-FGHJK-MNPQR") == "PITW-0BCDE-FGHJK-MNPQR"
    assert normalize_code("PITW-OBCDE-FGHJK-MNPQR") == "PITW-0BCDE-FGHJK-MNPQR"
    assert normalize_code("not a code") is None


# --------------------------------------------------------------------------
# Signature verification (the trust anchor)
# --------------------------------------------------------------------------


def test_genuine_signature_verifies():
    ent = _entitlement()
    verify_entitlement(ent, _sign(ent))  # must not raise


def test_tampered_entitlement_is_rejected():
    ent = _entitlement()
    signature = _sign(ent)
    forged = Entitlement(ENTITLEMENT_VERSION, ent.code_id, "pitwall-PRO-unlimited", ent.issued)
    with pytest.raises(VerificationError):
        verify_entitlement(forged, signature)


def test_garbage_signature_is_rejected():
    ent = _entitlement()
    with pytest.raises(VerificationError):
        verify_entitlement(ent, base64.b64encode(b"\x00" * 64).decode())
    with pytest.raises(VerificationError):
        verify_entitlement(ent, "not-base64!!")


# --------------------------------------------------------------------------
# Device-bound local license (offline launch)
# --------------------------------------------------------------------------


def _install_license(tmp_path, device: str, entitlement=None) -> Path:
    ent = entitlement or _entitlement()
    lic = License(ent, _sign(ent), device, "2026-08-05T00:00:00Z")
    return save_license(tmp_path, lic)


def test_license_loads_offline_on_the_bound_device(tmp_path, monkeypatch):
    monkeypatch.setattr("distribution.licensing.license_store.device_hash", lambda: "a" * 64)
    _install_license(tmp_path, "a" * 64)
    lic = load_and_validate(tmp_path)
    assert lic.entitlement.sku == "pitwall-desktop-1"


def test_license_is_rejected_on_a_different_device(tmp_path, monkeypatch):
    # Signed license bound to device A, but we are now device B: reject.
    _install_license(tmp_path, "a" * 64)
    monkeypatch.setattr("distribution.licensing.license_store.device_hash", lambda: "b" * 64)
    with pytest.raises(LicenseInvalid):
        load_and_validate(tmp_path)


def test_forged_local_license_is_rejected(tmp_path, monkeypatch):
    # Attacker writes a license with a real device hash but a bogus signature.
    monkeypatch.setattr("distribution.licensing.license_store.device_hash", lambda: "c" * 64)
    ent = _entitlement()
    bogus = License(ent, base64.b64encode(b"\x00" * 64).decode(), "c" * 64, "2026-08-05T00:00:00Z")
    save_license(tmp_path, bogus)
    with pytest.raises(LicenseInvalid):
        load_and_validate(tmp_path)


def test_missing_license_reads_as_needs_activation(tmp_path):
    with pytest.raises(LicenseInvalid):
        load_and_validate(tmp_path)


# --------------------------------------------------------------------------
# The gate flow and activation
# --------------------------------------------------------------------------


def test_gate_reports_needs_activation_without_a_license(tmp_path):
    result = gate.check(tmp_path)
    assert result.status is gate.GateStatus.NEEDS_ACTIVATION


def test_complete_activation_verifies_then_persists(tmp_path, monkeypatch):
    ent = _entitlement()
    signature = _sign(ent)

    # Stub the network: the server returns the pre-signed entitlement.
    from distribution.licensing import gate as gate_module
    from distribution.licensing.activation_client import ActivationResult

    monkeypatch.setattr(gate_module, "device_hash", lambda: "d" * 64)
    monkeypatch.setattr(
        gate_module, "activate",
        lambda endpoint, code, dev: ActivationResult(ent, signature),
    )
    monkeypatch.setattr("distribution.licensing.license_store.device_hash", lambda: "d" * 64)

    lic = gate.complete_activation(tmp_path, "https://x/activate", ent.code_id)
    assert lic.device_hash == "d" * 64
    # And it now loads offline on the next launch.
    assert load_and_validate(tmp_path).entitlement.code_id == ent.code_id


def test_activation_rejects_a_server_entitlement_that_does_not_verify(tmp_path, monkeypatch):
    ent = _entitlement()
    from distribution.licensing import gate as gate_module
    from distribution.licensing.activation_client import ActivationResult

    monkeypatch.setattr(gate_module, "device_hash", lambda: "e" * 64)
    # Server tries to hand out an entitlement with a forged signature.
    monkeypatch.setattr(
        gate_module, "activate",
        lambda endpoint, code, dev: ActivationResult(
            ent, base64.b64encode(b"\x00" * 64).decode()
        ),
    )
    with pytest.raises(LicenseInvalid):
        gate.complete_activation(tmp_path, "https://x/activate", ent.code_id)


def test_activation_reports_an_unreadable_device_id_instead_of_crashing(tmp_path, monkeypatch):
    # launch() catches only (ActivationError, LicenseInvalid), so a DeviceIdError
    # escaping complete_activation took down the whole app on the activation
    # screen. The license-read path already guarded this; activation did not.
    from distribution.licensing import gate as gate_module
    from distribution.licensing.activation_client import ActivationError
    from distribution.licensing.device import DeviceIdError

    def unreadable():
        raise DeviceIdError("MachineGuid was empty")

    monkeypatch.setattr(gate_module, "device_hash", unreadable)

    with pytest.raises(ActivationError) as caught:
        gate.complete_activation(tmp_path, "https://x/activate", _entitlement().code_id)
    assert caught.value.code == "device_unavailable"


def test_activation_identifies_itself_instead_of_sending_the_urllib_default():
    """The header that keeps activation from being blocked at the edge.

    Cloudflare's bot protection bans the literal "Python-urllib/<ver>" default
    signature: the POST is refused with a 403 carrying neither `code` nor
    `message`, so _error_detail falls through and every buyer sees
    "Activation failed. Please try again." with no way to tell a blocked
    request from a mistyped code. Dropping this header breaks activation for
    every customer at once, and nothing else in the suite would notice —
    the other activation tests stub the network out entirely.
    """
    import json as _json

    from distribution.licensing import activation_client

    ent = _entitlement()
    # activate() does not verify the signature (gate does, afterwards), so an
    # unsigned stand-in is enough to get through the response parsing.
    body = _json.dumps(
        {"entitlement": ent.to_dict(), "signature": "unchecked-at-this-layer"}
    ).encode()
    captured: dict[str, object] = {}

    class _Response:
        def read(self) -> bytes:
            return body

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> bool:
            return False

    class _Opener:
        def open(self, request, timeout=None):
            captured["request"] = request
            return _Response()

    activation_client.activate(
        "https://x/activate", ent.code_id, "d" * 64, opener=_Opener()
    )

    # urllib title-cases header keys, so it is stored as "User-agent".
    agent = captured["request"].get_header("User-agent")
    assert agent, "activation must send a User-Agent or Cloudflare blocks it"
    assert not agent.startswith("Python-urllib"), agent


# --------------------------------------------------------------------------
# Integrity / tamper
# --------------------------------------------------------------------------


def test_integrity_ok_without_a_manifest_is_true():
    # A dev tree (no baked manifest) is not treated as tampered.
    assert gate.integrity_ok() is True


def test_device_module_is_guarded():
    # device.py computes the hash license_store binds against. If it is not in
    # the manifest, patching it to return a constant defeats device binding
    # while every other guarded file still hashes correctly.
    assert "device.py" in gate._GUARDED


def test_a_modified_guarded_module_fails_the_integrity_check(monkeypatch, tmp_path):
    manifest = tmp_path / "integrity_manifest.txt"
    manifest.write_text("0" * 64 + "\n", encoding="ascii")
    monkeypatch.setattr(gate, "_MANIFEST", manifest)
    assert gate.integrity_ok() is False


def test_tampered_gate_refuses_to_run_without_deleting_anything(monkeypatch, tmp_path):
    install = tmp_path / "install"
    install.mkdir()
    (install / "pitwall.exe").write_text("payload", encoding="utf-8")

    manifest = tmp_path / "integrity_manifest.txt"
    manifest.write_text("0" * 64 + "\n", encoding="ascii")
    monkeypatch.setattr(gate, "_MANIFEST", manifest)

    result = gate.check(tmp_path)

    assert result.status is gate.GateStatus.TAMPERED
    assert result.license is None
    # The response is a message, not a deletion: the install is intact.
    assert (install / "pitwall.exe").read_text(encoding="utf-8") == "payload"
    assert "has not started" in result.detail
