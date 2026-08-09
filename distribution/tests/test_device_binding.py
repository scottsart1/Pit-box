"""Device binding on both target platforms.

The macOS path cannot be exercised on the build machine (Windows), so it is
tested against captured `ioreg` output instead. That covers the parsing, which
is where the platform-specific bugs live; what remains untestable here is only
whether `ioreg` exists and returns this shape, which is stable macOS behaviour.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

DIST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIST.parent))

from distribution.licensing import device  # noqa: E402

# Real `ioreg -rd1 -c IOPlatformExpertDevice` output, trimmed to the lines that
# matter. Note the surrounding quotes and the leading whitespace: both have
# broken naive parsers before.
IOREG_OUTPUT = """
+-o J316sAP  <class IOPlatformExpertDevice, id 0x100000278, registered>
    {
      "IOPolledInterface" = "AppleARMWatchdogTimerHibernateHandler is not seri"
      "IOPlatformUUID" = "564D5A3C-1A2B-4C5D-9E8F-0A1B2C3D4E5F"
      "IOPlatformSerialNumber" = "C02XY1ZZJGH5"
      "IOBusyInterest" = "IOCommand is not serializable"
    }
"""


class _Completed:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.returncode = 0


@pytest.fixture
def on_macos(monkeypatch):
    monkeypatch.setattr(device.platform, "system", lambda: "Darwin")
    return monkeypatch


def test_mac_uuid_is_parsed_from_real_ioreg_output(on_macos):
    on_macos.setattr(subprocess, "run", lambda *a, **k: _Completed(IOREG_OUTPUT))
    assert device.raw_device_id() == "564D5A3C-1A2B-4C5D-9E8F-0A1B2C3D4E5F"


def test_mac_hash_is_stable_and_hides_the_raw_id(on_macos):
    on_macos.setattr(subprocess, "run", lambda *a, **k: _Completed(IOREG_OUTPUT))
    first = device.device_hash()
    second = device.device_hash()
    assert first == second
    assert len(first) == 64
    assert "564D5A3C" not in first


def test_mac_and_windows_hashes_differ_for_the_same_id(monkeypatch):
    same_id = "564D5A3C-1A2B-4C5D-9E8F-0A1B2C3D4E5F"
    monkeypatch.setattr(device, "raw_device_id", lambda: same_id)

    monkeypatch.setattr(device.platform, "system", lambda: "Darwin")
    mac_hash = device.device_hash()
    monkeypatch.setattr(device.platform, "system", lambda: "Windows")
    windows_hash = device.device_hash()

    # The OS family is folded in, so the two can never collide.
    assert mac_hash != windows_hash


def test_missing_ioreg_field_is_a_clear_error(on_macos):
    on_macos.setattr(subprocess, "run", lambda *a, **k: _Completed("+-o J316sAP\n{}\n"))
    with pytest.raises(device.DeviceIdError, match="IOPlatformUUID"):
        device.raw_device_id()


def test_empty_ioreg_output_is_a_clear_error(on_macos):
    on_macos.setattr(subprocess, "run", lambda *a, **k: _Completed(""))
    with pytest.raises(device.DeviceIdError):
        device.raw_device_id()


def test_an_unsupported_platform_is_refused_rather_than_guessed(monkeypatch):
    monkeypatch.setattr(device.platform, "system", lambda: "Linux")
    with pytest.raises(device.DeviceIdError, match="unsupported platform"):
        device.raw_device_id()


def test_an_unreadable_windows_registry_is_reported_as_a_device_id_error(monkeypatch):
    # A damaged install can make the MachineGuid key unreadable. That used to
    # escape as a bare FileNotFoundError — a type no caller catches — so it
    # crashed the activation screen with a traceback instead of being reported.
    monkeypatch.setattr(device.platform, "system", lambda: "Windows")

    def unreadable():
        raise FileNotFoundError(2, "The system cannot find the file specified")

    monkeypatch.setattr(device, "_windows_machine_guid", unreadable)
    with pytest.raises(device.DeviceIdError, match="could not read the Windows device id"):
        device.raw_device_id()


def test_a_missing_ioreg_binary_is_reported_as_a_device_id_error(on_macos):
    # Same contract on the other platform: subprocess raises OSError, not
    # DeviceIdError, if the binary is absent.
    def missing(*args, **kwargs):
        raise FileNotFoundError(2, "ioreg not found")

    on_macos.setattr(subprocess, "run", missing)
    with pytest.raises(device.DeviceIdError, match="could not read the Darwin device id"):
        device.raw_device_id()
