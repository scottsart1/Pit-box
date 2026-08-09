"""The API key can be set from the dashboard without ever being disclosed."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pitwall import credentials
from pitwall.api.credentials import create_credentials_router
from pitwall.config import settings

REAL_KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setenv("PITWALL_ENV_FILE", str(path))
    # A key exported in the real environment would otherwise shadow the file
    # and make current_status report "environment".
    monkeypatch.delenv(credentials.ENV_VAR, raising=False)
    monkeypatch.setattr(settings, "openai_api_key", None)
    return path


@pytest.fixture
def client(env_file):
    changes: list[int] = []
    app = FastAPI()
    app.include_router(create_credentials_router(on_change=lambda: changes.append(1)))
    with TestClient(app) as test_client:
        test_client.rebind_calls = changes
        yield test_client


# ---------------------------------------------------------------------------
# The key must never leave the machine
# ---------------------------------------------------------------------------


def test_mask_never_shows_enough_to_reuse():
    masked = credentials.mask_key(REAL_KEY)
    assert REAL_KEY not in masked
    assert masked.endswith(REAL_KEY[-4:])
    assert len(masked) < len(REAL_KEY)


def test_short_values_are_masked_completely():
    assert credentials.mask_key("sk-abc") == "******"


def test_status_response_carries_no_key(client):
    client.put("/api/v1/credentials/openai", json={"api_key": REAL_KEY, "verify": False})
    response = client.get("/api/v1/credentials/openai")
    assert response.status_code == 200
    assert REAL_KEY not in response.text
    body = response.json()
    assert body["configured"] is True
    assert body["masked"].endswith(REAL_KEY[-4:])


# ---------------------------------------------------------------------------
# Validation happens before anything is written
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["", "   ", "your_openai_api_key", "<your key>", "sk-short", "sk-with space"],
)
def test_obvious_mistakes_are_rejected(value):
    with pytest.raises(credentials.CredentialError):
        credentials.validate_key(value)


def test_a_plausible_key_is_accepted():
    assert credentials.validate_key(f"  {REAL_KEY}  ") == REAL_KEY


def test_a_rejected_key_never_overwrites_a_working_one(client, env_file):
    client.put("/api/v1/credentials/openai", json={"api_key": REAL_KEY, "verify": False})
    response = client.put(
        "/api/v1/credentials/openai",
        json={"api_key": "nope", "verify": False},
    )
    assert response.status_code == 422
    assert REAL_KEY in env_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# .env is edited, not rewritten
# ---------------------------------------------------------------------------


def test_unrelated_settings_and_comments_survive():
    existing = (
        "# Your Pit Box settings\n"
        "PITWALL_UDP_PORT=20777\n"
        "OPENAI_API_KEY=sk-old-value-that-is-long-enough\n"
        "PITWALL_VOICE=coral\n"
    )
    rendered = credentials._render_env(existing, REAL_KEY)
    assert "# Your Pit Box settings" in rendered
    assert "PITWALL_UDP_PORT=20777" in rendered
    assert "PITWALL_VOICE=coral" in rendered
    assert f"OPENAI_API_KEY={REAL_KEY}" in rendered
    assert "sk-old-value" not in rendered


def test_the_key_is_appended_when_absent():
    rendered = credentials._render_env("PITWALL_UDP_PORT=20777\n", REAL_KEY)
    assert rendered.endswith(f"OPENAI_API_KEY={REAL_KEY}\n")


def test_duplicate_assignments_collapse_to_one():
    # Two assignments would leave the effective value depending on parse order.
    existing = "OPENAI_API_KEY=sk-first-one-long-enough\nOPENAI_API_KEY=sk-second-one-long\n"
    rendered = credentials._render_env(existing, REAL_KEY)
    assert rendered.count("OPENAI_API_KEY=") == 1
    assert REAL_KEY in rendered


def test_clearing_removes_the_assignment_and_keeps_the_rest():
    existing = f"PITWALL_VOICE=coral\nOPENAI_API_KEY={REAL_KEY}\n"
    rendered = credentials._render_env(existing, None)
    assert "OPENAI_API_KEY" not in rendered
    assert "PITWALL_VOICE=coral" in rendered


def test_an_exported_assignment_is_replaced_too():
    rendered = credentials._render_env("export OPENAI_API_KEY=sk-old-long-enough\n", REAL_KEY)
    assert rendered.count("OPENAI_API_KEY=") == 1
    assert "sk-old-long-enough" not in rendered


# ---------------------------------------------------------------------------
# Saving applies to the running app
# ---------------------------------------------------------------------------


def test_saving_persists_applies_and_rebinds(client, env_file):
    response = client.put(
        "/api/v1/credentials/openai",
        json={"api_key": REAL_KEY, "verify": False},
    )
    assert response.status_code == 200
    assert f"OPENAI_API_KEY={REAL_KEY}" in env_file.read_text(encoding="utf-8")
    assert settings.api_key == REAL_KEY
    assert client.rebind_calls, "live OpenAI clients were not rebound"


def test_deleting_clears_the_key(client, env_file):
    client.put("/api/v1/credentials/openai", json={"api_key": REAL_KEY, "verify": False})
    response = client.delete("/api/v1/credentials/openai")
    assert response.status_code == 200
    assert response.json()["configured"] is False
    assert settings.api_key is None
    assert "OPENAI_API_KEY" not in env_file.read_text(encoding="utf-8")


def test_status_flags_an_overriding_environment_variable(client, monkeypatch):
    monkeypatch.setenv(credentials.ENV_VAR, REAL_KEY)
    monkeypatch.setattr(settings, "openai_api_key", None)
    credentials.apply_to_runtime(REAL_KEY)
    body = client.get("/api/v1/credentials/openai").json()
    assert body["source"] == "environment"
    assert "environment variable" in body["detail"]


# ---------------------------------------------------------------------------
# Writes are restricted to this machine
# ---------------------------------------------------------------------------


def _lan_client(app) -> TestClient:
    # TestClient's default host is treated as loopback, so a LAN caller has to
    # be simulated with a real routable address.
    return TestClient(app, client=("192.168.1.50", 51000))


def test_a_lan_client_cannot_write_or_delete_the_key(env_file):
    app = FastAPI()
    app.include_router(create_credentials_router())
    with _lan_client(app) as lan:
        assert lan.put(
            "/api/v1/credentials/openai",
            json={"api_key": REAL_KEY, "verify": False},
        ).status_code == 403
        assert lan.delete("/api/v1/credentials/openai").status_code == 403
        assert lan.post("/api/v1/credentials/openai/test").status_code == 403
        # Reading status stays available: it discloses nothing.
        assert lan.get("/api/v1/credentials/openai").status_code == 200
    assert not env_file.exists()
