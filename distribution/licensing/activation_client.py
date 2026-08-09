"""The one network call in the whole licensing system: first activation.

Sends the typed code and this device's hash to the activation endpoint, which
atomically claims the code (single global use) and returns the pre-signed
entitlement. Everything after this runs offline.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from .entitlement import Entitlement

DEFAULT_TIMEOUT_S = 15.0

# Identify the app explicitly, because urllib's default does not survive the
# edge. Left unset, urllib sends "Python-urllib/3.12", and Cloudflare's bot
# protection bans that exact signature: the activation POST is refused at the
# edge with 403 "error code: 1010" before the Worker runs at all. That reply
# carries neither `code` nor `message`, so _error_detail below fell through to
# its last-resort text and every buyer saw "Activation failed. Please try
# again." with no way to tell a blocked request from a bad code.
#
# Any non-urllib value clears the ban, so this is not a fragile spoof of a
# browser — it is the app saying what it actually is. Keep it in step with the
# installer version in packaging/build.py.
USER_AGENT = "PitWall/4.2.0"


class ActivationError(Exception):
    """Activation could not complete. Carries a user-facing reason and a code."""

    def __init__(self, reason: str, code: str = "activation_failed") -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


@dataclass(frozen=True, slots=True)
class ActivationResult:
    entitlement: Entitlement
    signature_b64: str


def activate(
    endpoint: str,
    code: str,
    device_hash: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    opener: urllib.request.OpenerDirector | None = None,
) -> ActivationResult:
    """Claim `code` for `device_hash` at `endpoint`.

    Re-activating the same code on the same device (a reinstall) succeeds and
    returns the same entitlement. Claiming a code already used on a different
    device fails with code_already_claimed.
    """
    body = json.dumps({"code": code, "device_hash": device_hash}).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    _open = (opener or urllib.request).open if opener else urllib.request.urlopen

    try:
        with _open(request, timeout=timeout_s) as response:  # type: ignore[operator]
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = _error_detail(exc)
        raise ActivationError(detail[0], detail[1]) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ActivationError(
            "Could not reach the activation server. Check your internet "
            "connection and try again.",
            "network_unreachable",
        ) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise ActivationError("The activation server returned an unreadable "
                              "response.", "bad_response") from exc

    try:
        entitlement = Entitlement.from_dict(payload["entitlement"])
        signature = str(payload["signature"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ActivationError("The activation server response was incomplete.",
                              "bad_response") from exc
    return ActivationResult(entitlement=entitlement, signature_b64=signature)


def _error_detail(exc: urllib.error.HTTPError) -> tuple[str, str]:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        code = str(payload.get("code", "activation_failed"))
        message = str(payload.get("message", ""))
    except Exception:  # noqa: BLE001
        code, message = "activation_failed", ""
    friendly = {
        "code_not_found": "That activation code was not recognized. Check for "
                          "typos.",
        "code_already_claimed": "That code has already been activated on "
                               "another device. Each code activates once.",
    }.get(code, message or "Activation failed. Please try again.")
    return friendly, code
