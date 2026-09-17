"""Optional candidate signing, mandatory at the public release boundary.

Uses an existing certificate in the Windows user's certificate store. No PFX
password or private key is passed on a command line or copied into the build.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit


def configuration(*, required: bool = False) -> tuple[str, str, str] | None:
    thumbprint = os.environ.get("PITBOX_WINDOWS_CERT_SHA1", "").strip()
    timestamp = os.environ.get("PITBOX_TIMESTAMP_URL", "").strip()
    if not thumbprint and not timestamp and not required:
        return None
    if not re.fullmatch(r"[A-Fa-f0-9]{40}", thumbprint):
        raise ValueError("Set PITBOX_WINDOWS_CERT_SHA1 to the existing publisher certificate thumbprint.")
    url = urlsplit(timestamp)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment):
        raise ValueError("Set PITBOX_TIMESTAMP_URL to a credential-free HTTPS RFC 3161 timestamp endpoint.")
    executable = os.environ.get("PITBOX_SIGNTOOL_PATH") or shutil.which("signtool")
    if not executable or not Path(executable).is_file():
        raise ValueError("SignTool is unavailable; configure PITBOX_SIGNTOOL_PATH from the Windows SDK.")
    return executable, thumbprint, timestamp


def sign_windows_artifact(path: Path) -> bool:
    config = configuration()
    if config is None:
        print("Unsigned candidate artifact; not approved for public deployment.")
        return False
    executable, thumbprint, timestamp = config
    subprocess.run([executable, "sign", "/s", "My", "/sha1", thumbprint,
                    "/fd", "SHA256", "/tr", timestamp, "/td", "SHA256", str(path)], check=True)
    subprocess.run([executable, "verify", "/pa", "/all", "/tw", str(path)], check=True)
    return True
