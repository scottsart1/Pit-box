"""The Strategy workspace: markup contracts and its two deterministic APIs.

The workspace was asked for by name after the 2026-08-09 Brazil GP: strategy
conversation is a core part of the engineer-driver relationship, and the DRIVE
column's compressed plan lines were not enough to reason about a race with.
"""

from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from pitwall.app import app

ROOT = Path(__file__).parents[1]
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
STRATEGY_JS = (ROOT / "static" / "js" / "strategy.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "css" / "v42.css").read_text(encoding="utf-8")


def test_strategy_is_a_first_class_tab() -> None:
    assert 'id="tab-strategy"' in INDEX
    assert 'aria-controls="strategy"' in INDEX
    assert 'data-page="strategy"' in INDEX
    assert 'id="strategy"' in INDEX
    assert 'aria-labelledby="tab-strategy"' in INDEX
    # The tab sits between DRIVE and CONNECTION, where the plan conversation
    # belongs in the flow of a race evening.
    assert INDEX.index('data-page="live"') < INDEX.index('data-page="strategy"') < INDEX.index(
        'data-page="connection"'
    )


def test_workspace_carries_all_five_promised_sections() -> None:
    for element_id in (
        "stratInstruction",  # current call
        "stratPlanRows",  # plan board
        "stratTimeline",  # stint timeline
        "stratWhatIfForm",  # what-if row
        "stratRadio",  # strategy conversation
        "stratLog",  # decision log
    ):
        assert f'id="{element_id}"' in INDEX, element_id
    assert "strategy.js" in INDEX
    assert "pitwall:state" in INDEX, "live state must be broadcast to modules"


def test_strategy_js_uses_only_real_endpoints() -> None:
    for endpoint in (
        "/api/strategy/what-if",
        "/api/strategy/rivals",
        "/api/strategy/plan",
        "/api/strategy/recompute",
        "/api/ask",
        "/api/history?scope=current_session",
    ):
        assert endpoint in STRATEGY_JS, endpoint


def test_strategy_css_supports_the_layout() -> None:
    for selector in (".strategy-side", ".whatif-controls", ".decision-log"):
        assert selector in CSS, selector


def test_what_if_endpoint_answers_deterministically() -> None:
    client = TestClient(app)
    response = client.post("/api/strategy/what-if", json={"scenario": "box lap 18 for hards"})
    assert response.status_code == 200
    payload = response.json()
    # With no live session the simulation must refuse honestly, not invent.
    assert "available" in payload
    if not payload["available"]:
        assert payload.get("reason")


def test_what_if_rejects_an_empty_scenario() -> None:
    client = TestClient(app)
    response = client.post("/api/strategy/what-if", json={"scenario": "   "})
    assert response.status_code == 400


def test_rival_stop_projection_endpoint_is_bounded() -> None:
    client = TestClient(app)
    response = client.get("/api/strategy/rivals?top_n=4")
    assert response.status_code == 200
    payload = response.json()
    assert "rivals" in payload
    assert len(payload["rivals"]) <= 4
    assert client.get("/api/strategy/rivals?top_n=99").status_code == 422
