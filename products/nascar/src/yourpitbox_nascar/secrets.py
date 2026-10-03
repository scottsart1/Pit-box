from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes
from pathlib import Path


def _dpapi(value: bytes, decrypt: bool = False) -> bytes:
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]
    raw = ctypes.create_string_buffer(value)
    src = Blob(len(value), ctypes.cast(raw, ctypes.POINTER(ctypes.c_byte)))
    dst = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst)):
        raise OSError("Windows could not protect this credential")
    try:
        return ctypes.string_at(dst.data, dst.size)
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(dst.data)


class Credentials:
    def __init__(self, root: Path):
        self.path = root / "engineer.credential"

    def save(self, value: str):
        if not value:
            self.path.unlink(missing_ok=True)
            return
        raw = value.strip().encode()
        protected = b"DPAPI:" + base64.b64encode(_dpapi(raw)) if os.name == "nt" else b"LOCAL:" + raw
        self.path.write_bytes(protected)
        self.path.chmod(0o600)

    def read(self) -> str:
        configured = os.environ.get("NASCAR_OPENAI_API_KEY", "")
        if configured:
            return configured
        if not self.path.exists():
            return ""
        raw = self.path.read_bytes()
        if raw.startswith(b"DPAPI:"):
            return _dpapi(base64.b64decode(raw[6:]), decrypt=True).decode()
        return raw[6:].decode() if raw.startswith(b"LOCAL:") else ""
