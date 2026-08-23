"""Storing and applying engineer API keys from the dashboard.

Keys are the secrets a driver has to supply, and editing `.env` by hand before
the first race is the step most likely to go wrong. This module lets the
Connection Center do it instead — for every supported engine provider — under
three rules:

  * A key is **never sent back to the browser**. Reads return only whether one
    is configured plus a masked tail, which is enough to confirm *which* key is
    in use without disclosing it.
  * Writes are **loopback only** (enforced by the router). Telemetry may be read
    from a phone on the LAN; credentials are set at the machine itself.
  * The existing `.env` is preserved. Only the one assignment is rewritten, so
    hand-tuned settings and comments around it survive.

Nothing here logs a key, and values are not echoed in error messages.
"""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr

from .config import settings

ENV_VAR = "OPENAI_API_KEY"

# Deliberately permissive. Providers change key prefixes and lengths, so
# rejecting an unfamiliar-but-real key would be worse than letting the live
# check below be the real arbiter. This only catches the obvious mistakes:
# a pasted placeholder, a truncated fragment, or something with whitespace.
_MIN_KEY_LENGTH = 20
_PLACEHOLDERS = {
    "your_openai_api_key",
    "your_anthropic_api_key",
    "your_deepseek_api_key",
    "your_kimi_api_key",
    "your_api_key",
    "your_key_here",
    "replace_me",
    "api_key",
    "sk-",
    "sk-ant-",
}


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """One engine provider whose key the Connection Center can manage."""

    id: str
    label: str
    env_var: str
    settings_attr: str
    key_hint: str
    console_name: str


PROVIDERS: dict[str, ProviderSpec] = {
    spec.id: spec
    for spec in (
        ProviderSpec(
            id="openai",
            label="OpenAI",
            env_var="OPENAI_API_KEY",
            settings_attr="openai_api_key",
            key_hint="sk-…",
            console_name="the OpenAI dashboard",
        ),
        ProviderSpec(
            id="anthropic",
            label="Anthropic (Claude)",
            env_var="ANTHROPIC_API_KEY",
            settings_attr="anthropic_api_key",
            key_hint="sk-ant-…",
            console_name="the Anthropic console",
        ),
        ProviderSpec(
            id="deepseek",
            label="DeepSeek",
            env_var="DEEPSEEK_API_KEY",
            settings_attr="deepseek_api_key",
            key_hint="sk-…",
            console_name="the DeepSeek platform",
        ),
        ProviderSpec(
            id="kimi",
            label="Kimi (Moonshot AI)",
            env_var="KIMI_API_KEY",
            settings_attr="kimi_api_key",
            key_hint="sk-…",
            console_name="the Moonshot AI platform",
        ),
        ProviderSpec(
            id="custom",
            label="Custom endpoint",
            env_var="CUSTOM_LLM_API_KEY",
            settings_attr="custom_llm_api_key",
            key_hint="any token your endpoint expects",
            console_name="your endpoint's provider",
        ),
    )
}


class CredentialError(ValueError):
    """The supplied key was rejected before anything was written."""


def provider_spec(provider: str) -> ProviderSpec:
    spec = PROVIDERS.get(str(provider).strip().lower())
    if spec is None:
        known = ", ".join(sorted(PROVIDERS))
        raise CredentialError(f"Unknown provider '{provider}'. Known: {known}.")
    return spec


@dataclass(frozen=True, slots=True)
class CredentialStatus:
    """What the UI is allowed to know about the stored key."""

    configured: bool
    masked: str | None
    source: str | None
    provider: str = "openai"

    def to_dict(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "masked": self.masked,
            "source": self.source,
            "provider": self.provider,
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
            "That key looks truncated. Copy the whole value from the provider "
            "dashboard."
        )
    return cleaned


def _stored_key(provider: str) -> str | None:
    spec = provider_spec(provider)
    secret = getattr(settings, spec.settings_attr, None)
    if secret is None:
        return None
    return settings._usable_secret(secret)


def current_status(provider: str = "openai") -> CredentialStatus:
    """Report whether a usable key is configured, without disclosing it."""
    spec = provider_spec(provider)
    key = _stored_key(spec.id)
    if not key:
        return CredentialStatus(
            configured=False, masked=None, source=None, provider=spec.id
        )
    # An environment variable set outside .env wins in pydantic-settings, and
    # the user needs to know that editing here will not change what is used.
    source = "environment" if os.environ.get(spec.env_var) else "env_file"
    return CredentialStatus(
        configured=True, masked=mask_key(key), source=source, provider=spec.id
    )


def all_statuses() -> dict[str, CredentialStatus]:
    return {provider: current_status(provider) for provider in PROVIDERS}


def _render_env(existing: str, key: str | None, env_var: str = ENV_VAR) -> str:
    """Rewrite only the one assignment, preserving everything else."""
    assignment = f"{env_var}={key}" if key is not None else None
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(env_var)}\s*=", re.IGNORECASE)

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


def _write_env(key: str | None, env_var: str = ENV_VAR) -> Path:
    path = env_path()
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    rendered = _render_env(existing, key, env_var)

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


def apply_to_runtime(key: str | None, provider: str = "openai") -> None:
    """Point the live settings object at the new key."""
    spec = provider_spec(provider)
    setattr(settings, spec.settings_attr, SecretStr(key) if key else None)


def save_key(
    key: str,
    *,
    provider: str = "openai",
    on_change: Callable[[], None] | None = None,
) -> CredentialStatus:
    """Validate, persist, and apply a key. Returns the masked status."""
    spec = provider_spec(provider)
    cleaned = validate_key(key)
    _write_env(cleaned, spec.env_var)
    apply_to_runtime(cleaned, spec.id)
    if on_change is not None:
        on_change()
    return current_status(spec.id)


def clear_key(
    *,
    provider: str = "openai",
    on_change: Callable[[], None] | None = None,
) -> CredentialStatus:
    """Remove the stored key and drop the live clients that used it."""
    spec = provider_spec(provider)
    _write_env(None, spec.env_var)
    apply_to_runtime(None, spec.id)
    if on_change is not None:
        on_change()
    return current_status(spec.id)


async def _close_quietly(client: object) -> None:
    close = getattr(client, "close", None) or getattr(client, "aclose", None)
    if callable(close):
        # Closing must never mask the verification result.
        with contextlib.suppress(Exception):
            await close()


async def _verify_openai_compatible(
    key: str, *, base_url: str | None, timeout_s: float
) -> None:
    """Listing models costs no tokens on every OpenAI-compatible endpoint."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=key,
        base_url=base_url.rstrip("/") if base_url else None,
        timeout=timeout_s,
        max_retries=0,
    )
    try:
        await client.models.list()
    finally:
        await _close_quietly(client)


async def _verify_anthropic(key: str, *, timeout_s: float) -> None:
    import httpx

    url = f"{settings.anthropic_base_url.rstrip('/')}/v1/models"
    headers = {
        "x-api-key": key,
        "anthropic-version": settings.anthropic_version,
    }
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        response = await client.get(url, headers=headers)
        if response.status_code >= 400:
            raise _AnthropicVerificationError(response.status_code)


class _AnthropicVerificationError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"Anthropic API error {status_code}")
        self.status_code = status_code


async def verify_key(key: str | None = None, provider: str = "openai") -> tuple[bool, str]:
    """Check a key against its provider with the cheapest call available.

    Model listing costs no tokens, so "Test key" is free to press. Returns
    (ok, human-readable detail); the key never appears in the detail.
    """
    spec = provider_spec(provider)
    candidate = (key or _stored_key(spec.id) or "").strip()
    if not candidate and spec.id != "custom":
        return False, "No API key is configured."

    try:
        if spec.id == "anthropic":
            await _verify_anthropic(candidate, timeout_s=15.0)
        elif spec.id == "openai":
            await _verify_openai_compatible(candidate, base_url=None, timeout_s=15.0)
        elif spec.id == "deepseek":
            await _verify_openai_compatible(
                candidate, base_url=settings.deepseek_base_url, timeout_s=15.0
            )
        elif spec.id == "kimi":
            await _verify_openai_compatible(
                candidate, base_url=settings.kimi_base_url, timeout_s=15.0
            )
        else:  # custom
            base_url = settings.custom_llm_base_url.strip()
            if not base_url:
                return False, (
                    "Set PITWALL_CUSTOM_LLM_BASE_URL (and the two model names) "
                    "before testing the custom endpoint."
                )
            await _verify_openai_compatible(
                candidate or "local", base_url=base_url, timeout_s=15.0
            )
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as text
        return False, _friendly_verification_error(exc, spec)
    return True, f"Key accepted by {spec.label}."


def _friendly_verification_error(exc: BaseException, spec: ProviderSpec) -> str:
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if status in (401, 403) or "Authentication" in name or "PermissionDenied" in name:
        return (
            f"{spec.label} rejected that key. Check it was copied in full and "
            "is active."
        )
    if status == 429 or "RateLimit" in name:
        return (
            "The key is valid but rate limited or out of quota. Check billing "
            f"on {spec.console_name}."
        )
    if status is not None and 500 <= int(status) < 600:
        return f"{spec.label} is unavailable right now. Try again shortly."
    if "Connection" in name or "Timeout" in name:
        return f"Could not reach {spec.label}. Check this PC's internet connection."
    return "Could not verify the key. Check the connection and try again."


__all__ = [
    "ENV_VAR",
    "PROVIDERS",
    "CredentialError",
    "CredentialStatus",
    "ProviderSpec",
    "all_statuses",
    "apply_to_runtime",
    "clear_key",
    "current_status",
    "env_path",
    "mask_key",
    "provider_spec",
    "save_key",
    "validate_key",
    "verify_key",
]
