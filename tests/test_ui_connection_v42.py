from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).parents[1]
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
CONNECTION_JS = (ROOT / "static" / "js" / "connection.js").read_text(
    encoding="utf-8"
)
V42_CSS = (ROOT / "static" / "css" / "v42.css").read_text(encoding="utf-8")


class _MarkupAudit(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.label_targets: list[str] = []
        self.tab_targets: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(attributes["id"] or "")
        if tag == "label" and attributes.get("for"):
            self.label_targets.append(attributes["for"] or "")
        if attributes.get("role") == "tab" and attributes.get("aria-controls"):
            self.tab_targets.append(attributes["aria-controls"] or "")


def test_connection_center_is_a_first_class_accessible_tab() -> None:
    assert 'role="tablist"' in INDEX
    assert 'id="tab-connection"' in INDEX
    assert 'role="tab"' in INDEX
    assert 'aria-controls="connection"' in INDEX
    assert 'id="connection"' in INDEX
    assert 'role="tabpanel"' in INDEX
    assert 'aria-labelledby="tab-connection"' in INDEX
    assert "ArrowRight" in CONNECTION_JS
    assert "ArrowLeft" in CONNECTION_JS
    assert "aria-selected" in CONNECTION_JS


def test_markup_ids_and_control_relationships_are_unambiguous() -> None:
    audit = _MarkupAudit()
    audit.feed(INDEX)
    assert len(audit.ids) == len(set(audit.ids))
    assert set(audit.label_targets) <= set(audit.ids)
    assert set(audit.tab_targets) <= set(audit.ids)


def test_connection_center_exposes_real_network_operations() -> None:
    required_endpoints = {
        'apiRequest("/interfaces")',
        'apiRequest("/status")',
        'apiRequest("/listener/start"',
        'apiRequest("/listener/stop"',
        'apiRequest("/forwarders")',
        'apiRequest("/diagnose"',
        'method: "POST"',
        'method: "PATCH"',
        'method: "DELETE"',
    }
    for endpoint in required_endpoints:
        assert endpoint in CONNECTION_JS

    for element_id in (
        "recommendedIpv4",
        "recommendedPort",
        "connectionStateText",
        "networkSource",
        "networkFormat",
        "networkSessionUid",
        "packetHealthBody",
        "networkInterfaces",
        "forwarderForm",
        "forwarderList",
        "diagnoseChecks",
        "diagnoseActions",
    ):
        assert f'id="{element_id}"' in INDEX


def test_network_ui_distinguishes_listening_receiving_and_unavailable() -> None:
    assert "Listening means the socket is open" in INDEX
    assert "Receiving means valid F1 telemetry" in INDEX
    assert 'listening: "Listening — waiting for telemetry"' in CONNECTION_JS
    assert 'receiving: "Receiving telemetry"' in CONNECTION_JS
    assert "No packet data available" in INDEX
    assert "Unavailable" in INDEX
    assert "Public destinations must be reconfirmed" in CONNECTION_JS


def test_existing_live_review_and_setup_contracts_remain_present() -> None:
    for element_id in (
        "live",
        "review",
        "setup",
        "timing",
        "trace",
        "lineMap",
        "radio",
        "strategyCard",
        "reviewLaps",
        "setupTrack",
        "recommendedSetup",
    ):
        assert f'id="{element_id}"' in INDEX

    for function_name in ("render", "loadHistory", "generateSetup", "selectPage"):
        assert f"function {function_name}" in INDEX


def test_transport_uses_secure_websocket_and_rest_bootstrap() -> None:
    assert "location.protocol==='https:'?'wss:':'ws:'" in INDEX
    assert "new WebSocket(`${protocol}//${location.host}/ws`)" in INDEX
    assert "fetch('/api/state'" in INDEX
    assert "bootstrapLiveState();connect()" in INDEX


def test_frontend_assets_are_local_and_need_no_build_chain() -> None:
    # The version query busts the browser's per-URL cache on upgrade (a stale
    # cached asset against new HTML blanked pages on 2026-08-12); the asset
    # itself is still local and unbuilt. test_ui_analysis_v461 pins the param
    # to the app version.
    assert re.search(
        r'<link rel="stylesheet" href="/static/css/v42\.css(\?v=[\w.]+)?">',
        INDEX,
    )
    assert re.search(
        r'<script type="module" src="/static/js/connection\.js(\?v=[\w.]+)?"></script>',
        INDEX,
    )
    remote_assets = re.findall(
        r"<(?:script|link)\b[^>]+(?:src|href)=['\"]https?://",
        INDEX,
        flags=re.IGNORECASE,
    )
    assert remote_assets == []


def test_accessibility_and_responsive_contracts_are_explicit() -> None:
    assert "min-height: 44px" in V42_CSS
    assert ":focus-visible" in V42_CSS
    assert "@media (prefers-reduced-motion: reduce)" in V42_CSS
    assert "@media (max-width: 1024px)" in V42_CSS
    assert "@media (max-width: 700px)" in V42_CSS
    assert "@media (max-height: 500px) and (orientation: landscape)" in V42_CSS
    assert 'aria-live="polite"' in INDEX
    assert '<caption class="sr-only">' in INDEX
    assert 'scope="col"' in INDEX


def test_connection_module_is_safe_to_parse_without_a_browser() -> None:
    assert 'typeof window !== "undefined"' in CONNECTION_JS
    assert 'typeof document !== "undefined"' in CONNECTION_JS
    assert "if (HAS_DOM)" in CONNECTION_JS
