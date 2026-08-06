"""Stable per-device identifier, hashed.

The raw hardware id never leaves the machine and is never stored. Only its
salted SHA-256 is used, so the cached license and the activation record hold a
hash, not the real machine id. The salt is a public constant baked into the
build; it is not a secret, it only stops the hash being a plain rainbow of a
known-format id.
"""

from __future__ import annotations

import hashlib
import platform
import subprocess

# Public, build-constant. Changing it re-binds every existing license, so it is
# fixed for the life of the product line.
_SALT = b"pitwall-device-binding-v1"


class DeviceIdError(RuntimeError):
    """The stable hardware id could not be read on this platform."""


def _windows_machine_guid() -> str:
    # HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid — stable per Windows
    # install, readable without elevation.
    import winreg  # local import: only exists on Windows

    with winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Microsoft\Cryptography",
        0,
        winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
    ) as key:
        value, _ = winreg.QueryValueEx(key, "MachineGuid")
    guid = str(value).strip()
    if not guid:
        raise DeviceIdError("MachineGuid was empty")
    return guid


def _mac_platform_uuid() -> str:
    # IOPlatformUUID from ioreg — stable per Mac.
    out = subprocess.run(
        ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    ).stdout
    for line in out.splitlines():
        if "IOPlatformUUID" in line:
            # ... "IOPlatformUUID" = "XXXXXXXX-...."
            uuid = line.split("=", 1)[1].strip().strip('"')
            if uuid:
                return uuid
    raise DeviceIdError("IOPlatformUUID not found in ioreg output")


def raw_device_id() -> str:
    system = platform.system()
    if system == "Windows":
        return _windows_machine_guid()
    if system == "Darwin":
        return _mac_platform_uuid()
    raise DeviceIdError(f"unsupported platform for device binding: {system}")


def device_hash() -> str:
    """Salted SHA-256 of the stable machine id, plus the OS family.

    Includes the platform so the same id string on two OSes (it will not
    happen, but defensively) cannot collide.
    """
    digest = hashlib.sha256()
    digest.update(_SALT)
    digest.update(platform.system().encode("utf-8"))
    digest.update(b"\x00")
    digest.update(raw_device_id().encode("utf-8"))
    return digest.hexdigest()
