from __future__ import annotations

from pitwall.main import local_dashboard_url


def test_local_dashboard_url_never_uses_bind_wildcard() -> None:
    assert local_dashboard_url("0.0.0.0", 8000) == "http://127.0.0.1:8000"
    assert local_dashboard_url("::", 8000) == "http://[::1]:8000"
    assert local_dashboard_url("192.168.1.42", 9000) == "http://192.168.1.42:9000"
