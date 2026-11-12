"""Native encrypted credential persistence, registered before config is imported.

Desktop installations retain their existing .env behavior. Android supplies an
Android Keystore adapter; any failure is fatal rather than falling back to text.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Protocol

NAMES = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "KIMI_API_KEY",
    "MOONSHOT_API_KEY", "PITWALL_CUSTOM_LLM_API_KEY", "CUSTOM_LLM_API_KEY",
)


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...
    def put(self, name: str, value: str | None) -> None: ...


_store: SecretStore | None = None


def active() -> bool:
    return _store is not None


def persist(name: str, value: str | None) -> bool:
    if _store is None:
        return False
    aliases = {"KIMI_API_KEY": ("MOONSHOT_API_KEY",),
               "CUSTOM_LLM_API_KEY": ("PITWALL_CUSTOM_LLM_API_KEY",)}
    _store.put(name, value)
    for alias in aliases.get(name, ()):
        _store.put(alias, None)
        os.environ.pop(alias, None)
    if value:
        os.environ[name] = value
    else:
        os.environ.pop(name, None)
    return True


def initialize(store: SecretStore, env_file: Path) -> None:
    """Encrypt and verify legacy values BEFORE removing their assignments.

    No plaintext backup is made. A failed vault write leaves the original file
    intact so startup can report the failure without silently losing a key.
    dotenv interpolation is disabled to avoid expanding unrelated secrets.
    """
    from dotenv import dotenv_values

    global _store
    existing = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    values = dotenv_values(env_file, interpolate=False) if existing else {}
    normalized = {str(k).upper(): v for k, v in values.items()}
    for name in NAMES:
        value = normalized.get(name)
        if value:
            store.put(name, value)
            if store.get(name) != value:
                raise RuntimeError("Encrypted credential verification failed")
    # dotenv permits quoted multiline values. Remove entire assignments, not
    # just their first lines, using its parser's original-source boundaries.
    from io import StringIO

    from dotenv.parser import parse_stream

    kept = []
    for binding in parse_stream(StringIO(existing)):
        if binding.key and binding.key.upper() in NAMES:
            if binding.error:
                raise RuntimeError("Repair the API key assignment before migration")
            continue
        if binding.error and any(re.search(rf"\b{re.escape(n)}\s*=", binding.original.string, re.IGNORECASE) for n in NAMES):
            raise RuntimeError("Repair the API key assignment before migration")
        kept.append(binding.original.string)
    rendered = "".join(kept)
    if rendered != existing:
        temp = env_file.with_suffix(".secure-migration.tmp")
        temp.write_text(rendered, encoding="utf-8")
        temp.chmod(0o600)
        temp.replace(env_file)
    # Install only after all writes/removals succeed. Reads must never fall
    # back to an old value left in the process environment.
    for name in NAMES:
        value = store.get(name)
        if value:
            os.environ[name] = value
        else:
            os.environ.pop(name, None)
    _store = store
