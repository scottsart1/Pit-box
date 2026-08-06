"""Same-origin token gate for explicitly enabled LAN dashboard access."""

from __future__ import annotations

import hmac
import ipaddress
import json
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send


def _headers(scope: Scope) -> dict[str, str]:
    return {
        key.decode("latin-1").casefold(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }


def is_loopback_host(host: str) -> bool:
    """True when a client address belongs to this machine.

    Used both by the LAN token gate and by endpoints that are restricted to
    the machine running Pit Wall regardless of any token.
    """
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.casefold() in {"localhost", "testclient"}


def _loopback(host: str) -> bool:
    return is_loopback_host(host)


def _web_scheme(scheme: str) -> str:
    """Return the HTTP origin scheme corresponding to an ASGI connection."""

    normalized = scheme.casefold()
    return {"ws": "http", "wss": "https"}.get(normalized, normalized)


def _default_port(scheme: str) -> int | None:
    return {"http": 80, "https": 443}.get(scheme)


def _authority(value: str, scheme: str) -> tuple[str, int] | None:
    """Parse a Host/origin authority into a normalized host and effective port."""

    try:
        parsed = urlsplit(f"//{value}")
        hostname = (parsed.hostname or "").casefold().rstrip(".")
        port = parsed.port
    except ValueError:
        return None
    if not hostname or parsed.username is not None or parsed.password is not None:
        return None
    effective_port = port if port is not None else _default_port(scheme)
    if effective_port is None:
        return None
    return hostname, effective_port


def _same_origin(headers: dict[str, str], scope: Scope) -> bool:
    origin = headers.get("origin")
    if not origin:
        return True
    try:
        parsed = urlsplit(origin)
        origin_scheme = parsed.scheme.casefold()
        origin_host = (parsed.hostname or "").casefold().rstrip(".")
        origin_port = parsed.port
    except ValueError:
        return False
    if (
        origin_scheme not in {"http", "https"}
        or not origin_host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return False

    request_scheme = _web_scheme(str(scope.get("scheme", "http")))
    if request_scheme not in {"http", "https"} or origin_scheme != request_scheme:
        return False

    host_header = headers.get("host", "")
    if host_header:
        request_authority = _authority(host_header, request_scheme)
    else:
        server = scope.get("server")
        if not server:
            return False
        server_host, server_port = server
        request_authority = (str(server_host).casefold().rstrip("."), int(server_port))
    if request_authority is None:
        return False

    effective_origin_port = (
        origin_port if origin_port is not None else _default_port(origin_scheme)
    )
    return request_authority == (origin_host, effective_origin_port)


def _cookie_token(headers: dict[str, str]) -> str | None:
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get("cookie", ""))
    except Exception:  # noqa: BLE001 - malformed cookies simply do not authenticate
        return None
    item = cookie.get("pitwall_access")
    return item.value if item is not None else None


class LanAccessMiddleware:
    """Require a configured token only when LAN browser access is enabled."""

    def __init__(self, app: ASGIApp, *, enabled: bool, token: str | None) -> None:
        self.app = app
        self.enabled = bool(enabled)
        self.token = str(token or "")
        if self.enabled and len(self.token) < 16:
            raise ValueError(
                "LAN access requires an access token of at least 16 characters"
            )

    def _credential(self, scope: Scope, headers: dict[str, str]) -> tuple[str, str]:
        header_token = headers.get("x-pitwall-token", "")
        authorization = headers.get("authorization", "")
        if authorization.casefold().startswith("bearer "):
            header_token = authorization[7:].strip()
        if header_token:
            return header_token, "header"
        cookie = _cookie_token(headers)
        if cookie:
            return cookie, "cookie"
        query = parse_qs(scope.get("query_string", b"").decode("utf-8", "ignore"))
        values = query.get("access_token", [])
        if values and scope.get("type") == "http" and scope.get("method") == "GET":
            return values[-1], "query"
        if values and scope.get("type") == "websocket":
            return values[-1], "query"
        return "", "none"

    @staticmethod
    async def _reject(scope: Scope, send: Send, status: int, message: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401, "reason": message})
            return
        payload = json.dumps({"detail": message}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.enabled or scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = _headers(scope)
        if headers.get("sec-fetch-site", "").casefold() == "cross-site":
            await self._reject(
                scope, send, 403, "Cross-site requests are not allowed by Pit Wall"
            )
            return
        if not _same_origin(headers, scope):
            await self._reject(
                scope, send, 403, "Origin is not allowed for Pit Wall LAN access"
            )
            return
        client = scope.get("client")
        client_host = str(client[0]) if client else ""
        if _loopback(client_host):
            await self.app(scope, receive, send)
            return
        credential, source = self._credential(scope, headers)
        if not credential or not hmac.compare_digest(credential, self.token):
            await self._reject(scope, send, 401, "Pit Wall LAN access token required")
            return
        if (
            source == "cookie"
            and scope.get("type") == "http"
            and scope.get("method")
            not in {
                "GET",
                "HEAD",
                "OPTIONS",
            }
            and "origin" not in headers
        ):
            await self._reject(scope, send, 403, "A same-origin request is required")
            return

        async def secured_send(message: Message) -> None:
            if source == "query" and message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                secure = b"; Secure" if scope.get("scheme") == "https" else b""
                response_headers.append(
                    (
                        b"set-cookie",
                        b"pitwall_access="
                        + self.token.encode()
                        + b"; Path=/; HttpOnly; SameSite=Strict"
                        + secure,
                    )
                )
                message = {**message, "headers": response_headers}
            await send(message)

        await self.app(scope, receive, secured_send)


__all__ = ["LanAccessMiddleware", "is_loopback_host"]
