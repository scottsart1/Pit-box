"""Storing and applying the OpenAI API key from the dashboard.

The key is the one secret a driver has to supply, and editing `.env` by hand
before the first race is the step most likely to go wrong. This module lets the
Connection Center do it instead, under three rules:

  * The key is **never sent back to the browser**. Reads return only whether one
    is configured plus a masked tail, which is enough to confirm *which* key is
    in use without disclosing it.
  * Writes are **loopback only** (enforced by the router). Telemetry may be read
    from a phone on the LAN; credentials are set at the machine itself.
  * The existing `.env` is preserved. Only the one assignment is rewritten, so
    hand-tuned settings and comments around it survive.

Nothing here logs the key, and the value is not echoed in error messages.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr

from .config import settings

ENV_VAR = "OPENAI_API_KEY"

# Deliberately permissive. OpenAI has changed key prefixes and lengths more than
# once, so rejecting an unfamiliar-but-real key would be worse than letting the
# live check below be the real arbiter. This only catches the obvious mistakes:
# a pasted placeholder, a truncated fragment, or something with whitespace in it.
_MIN_KEY_LENGTH = 20
_PLACEHOLDERS = {
    "your_openai_api_key",
    "your_api_key",
    "your_key_here",
    "replace_me",
    "api_key",
    "sk-",
}


class CredentialError(ValueError):
    """The supplied key was rejected before anything was written."""


@dataclass(frozen=True, slots=True)
class CredentialStatus:
    """What the UI is allowed to know about the stored key."""

    configured: bool
    masked: str | None
    source: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "masked": self.masked,
            "source": self.source,
        }


def mask_key(key: str) -> str:
    """Show only enough to tell two keys apart."""
    cleaned = key.strip()
    if len(cleaned) <= 8:
        return "*" * len(cleaned)
    return f"{cleaned[:3]}...{cleaned[-4:]}"


def env_path() -> Path:
    """Where the key is persisted.

    pydantic-settings loads `.env` relative to the working directory, so this
    resolves the same file the next launch will read.
    """
    return Path(os.environ.get("PITWALL_ENV_FILE", ".env")).resolve()


def validate_key(key: str) -> str:
    """Return the cleaned key, or raise CredentialError explaining the problem."""
    cleaned = key.strip()
    if not cleaned:
        raise CredentialError("Enter an API key.")
    if any(character.isspace() for character in cleaned):
        raise CredentialError(
            "That key contains a space or line break. Copy it again without "
            "surrounding text."
        )
    if cleaned.lower() in _PLACEHOLDERS or cleaned.startswith("<"):
        raise CredentialError("That is a placeholder, not a key.")
    if len(cleaned) < _MIN_KEY_LENGTH:
        raise CredentialError(
            "That key looks truncated. Copy the whole value from the OpenAI "
            "dashboard."
        )
    return cleaned


def current_status() -> CredentialStatus:
    """Report whether a usable key is configured, without disclosing it."""
    key = settings.api_key
    if not key:
        return CredentialStatus(configured=False, masked=None, source=None)
    # An environment variable set outside .env wins in pydantic-settings, and
    # the user needs to know that editing here will not change what is used.
    source = "environment" if os.environ.get(ENV_VAR) else "env_file"
    return CredentialStatus(configured=True, masked=mask_key(key), source=source)


def _render_env(existing: str, key: str | None) -> str:
    """Rewrite only the OPENAI_API_KEY assignment, preserving everything else."""
    assignment = f"{ENV_VAR}={key}" if key is not None else None
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(ENV_VAR)}\s*=", re.IGNORECASE)

    lines = existing.splitlines()
    kept: list[str] = []
    replaced = False
    for line in lines:
        if pattern.match(line):
            if assignment is not None and not replaced:
                kept.append(assignment)
                replaced = True
            # Any further duplicates are dropped: two assignments would leave
            # the effective value depending on parse order.
            continue
        kept.append(line)

    if assignment is not None and not replaced:
        kept.append(assignment)

    body = "\n".join(kept).rstrip("\n")
    return body + "\n" if body else ""


def _write_env(key: str | None) -> Path:
    path = env_path()
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    rendered = _render_env(existing, key)

    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(rendered, encoding="utf-8")
    try:
        # Best effort on POSIX; Windows ignores the mode bits.
        os.chmod(temp, 0o600)
    except OSError:
        pass
    temp.replace(path)  # atomic publish, so a crash cannot truncate .env
    return path


def apply_to_runtime(key: str | None) -> None:
    """Point the live settings object at the new key."""
    settings.openai_api_key = SecretStr(key) if key else None


def save_key(key: str, *, on_change: Callable[[], None] | None = None) -> CredentialStatus:
    """Validate, persist, and apply a key. Returns the masked status."""
    cleaned = validate_key(key)
    _write_env(cleaned)
    apply_to_runtime(cleaned)
    if on_change is not None:
        on_change()
    return current_status()


def clear_key(*, on_change: Callable[[], None] | None = None) -> CredentialStatus:
    """Remove the stored key and drop the live clients that used it."""
    _write_env(None)
    apply_to_runtime(None)
    if on_change is not None:
        on_change()
    return current_status()


async def verify_key(key: str | None = None) -> tuple[bool, str]:
    """Check a key against OpenAI with the cheapest call available.

    Listing models costs no tokens, so "Test key" is free to press. Returns
    (ok, human-readable detail); the key never appears in the detail.
    """
    candidate = (key or settings.api_key or "").strip()
    if not candidate:
        return False, "No API key is configured."

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=candidate, timeout=15.0, max_retries=0)
    try:
        await client.models.list()
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as text
        return False, _friendly_verification_error(exc)
    finally:
        with_close = getattr(client, "close", None)
        if callable(with_close):
            try:
                await with_close()
            except Exception:  # noqa: BLE001 - closing must never mask the result
                pass
    return True, "Key accepted by OpenAI."


def _friendly_verification_error(exc: BaseException) -> str:
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if status == 401 or "Authentication" in name:
        return "OpenAI rejected that key. Check it was copied in full and is active."
    if status == 429 or "RateLimit" in name:
        return (
            "The key is valid but rate limited or out of quota. Check billing "
            "on the OpenAI dashboard."
        )
    if status is not None and 500 <= int(status) < 600:
        return "OpenAI is unavailable right now. Try again shortly."
    if "Connection" in name or "Timeout" in name:
        return "Could not reach OpenAI. Check this PC's internet connection."
    return "Could not verify the key. Check the connection and try again."


__all__ = [
    "ENV_VAR",
    "CredentialError",
    "CredentialStatus",
    "apply_to_runtime",
    "clear_key",
    "current_status",
    "env_path",
    "mask_key",
    "save_key",
    "validate_key",
    "verify_key",
]
