"""The Strategy workspace: markup contracts and its two deterministic APIs.

The workspace was asked for by name after the 2026-08-09 Brazil GP: strategy
conversation is a core part of the engineer-driver relationship, and the DRIVE
column's compressed plan lines were not enough to reason about a race with.
"""

from __future__ import annotations

import re
from pathlib import Path

from starlette.testclient import TestClient

from pitwall.app import app

ROOT = Path(__file__).parents[1]
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
STRATEGY_JS = (ROOT / "static" / "js" / "strategy.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "css" / "v42.css").read_text(encoding="utf-8")


def _css_rules(source: str, scopes: tuple[str, ...] = ()):
    """Read this stylesheet's rule blocks without losing their media scope.

    These are source contracts, not a substitute for browser layout checks.
    Balanced braces matter: a regex ending at the first closing brace would
    accidentally treat later rules in a media query as global rules.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    offset = 0
    while (opening := source.find("{", offset)) != -1:
        heading = " ".join(source[offset:opening].split())
        closing, depth = opening + 1, 1
        while depth and closing < len(source):
            depth += (source[closing] == "{") - (source[closing] == "}")
            closing += 1
        assert depth == 0, f"Unclosed CSS rule: {heading}"
        body = source[opening + 1 : closing - 1]
        if heading.startswith("@"):
            yield from _css_rules(body, scopes + (heading,))
        else:
            declarations = {}
            for declaration in body.split(";"):
                if ":" in declaration:
                    name, value = declaration.split(":", 1)
                    declarations[name.strip()] = " ".join(value.split())
            for selector in heading.split(","):
                yield scopes, selector.strip(), declarations
        offset = closing


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


def test_strategy_stacks_at_the_shared_workspace_breakpoint() -> None:
    """961–1180px must not gain an implicit, clipped Strategy column."""
    rules = list(_css_rules(CSS))
    scope = ("@media (max-width: 1180px)",)
    for selector, property_name, expected in (
        (".workspace-shell", "grid-template-columns", "1fr"),
        (".strategy-main-col", "grid-column", "1 / -1"),
        (".strategy-side", "grid-column", "1 / -1"),
        (".whatif-controls", "grid-template-columns", "1fr 1fr"),
    ):
        assert any(
            media == scope and target == selector and values.get(property_name) == expected
            for media, target, values in rules
        ), f"{selector} must follow the shared 1180px single-column breakpoint"


def test_strategy_row_span_and_sticky_scroll_are_desktop_only() -> None:
    """The radio/log rail must never become a mobile fixed-header overlay."""
    rail_rules = [
        (scope, values)
        for scope, selector, values in _css_rules(CSS)
        if selector in {".strategy-side", ".strategy-workspace .strategy-side"}
    ]
    desktop = "@media (min-width: 1181px)"
    tall_desktop = "@media (min-width: 1181px) and (min-height: 600px)"
    row_spans = [(scope, values) for scope, values in rail_rules if "span" in values.get("grid-row", "")]
    assert row_spans, "Keep the side-by-side rail on desktop"
    for scope, _ in row_spans:
        assert desktop in scope or tall_desktop in scope, "Row spans must not cover stacked cards"
    sticky_rules = [(scope, values) for scope, values in rail_rules if values.get("position") in {"sticky", "fixed"}]
    assert sticky_rules, "Keep desktop rail behavior"
    for scope, values in sticky_rules:
        assert scope == (tall_desktop,), "Short and narrow screens must use normal page scrolling"
        assert values["position"] == "sticky"
        assert values["max-height"] == "calc(100dvh - 152px)", "Account for the app shell"
    for scope, values in rail_rules:
        if values.get("overflow") in {"auto", "scroll"} or values.get("max-height", "none") != "none":
            assert scope == (tall_desktop,), "Do not trap mobile scrolling inside the rail"


def test_strategy_radio_and_log_follow_the_plans_in_document_order() -> None:
    strategy = INDEX.split('<main id="strategy"', 1)[1].split("</main>", 1)[0]
    aside = strategy.split('<aside class="panel-surface strategy-side"', 1)[1].split("</aside>", 1)[0]
    assert strategy.index('id="stratWhatIfForm"') < strategy.index('class="panel-surface strategy-side"')
    assert aside.index('id="stratRadio"') < aside.index('id="stratLog"')


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


def test_the_race_planner_is_present_and_wired() -> None:
    """Item 1 from Las Vegas: settle the race before the lights.

    The ranked plans on the live path all start from where the car is now,
    so a race could not be planned until telemetry arrived.
    """
    for element_id in (
        "stratPlannerForm",
        "stratPlannerTrack",
        "stratPlannerLaps",
        "stratPlannerStart",
        "stratPlannerRows",
        "stratPlannerEvidence",
    ):
        assert f'id="{element_id}"' in INDEX, element_id
    assert "/api/strategy/plan-race" in STRATEGY_JS
    assert "buildRacePlans" in STRATEGY_JS


def test_planner_endpoint_validates_and_answers_honestly() -> None:
    client = TestClient(app)
    # No session and no distance: it must refuse rather than invent a race.
    empty = client.post("/api/strategy/plan-race", json={})
    assert empty.status_code == 200
    assert empty.json()["available"] is False

    assert client.post(
        "/api/strategy/plan-race", json={"total_laps": 1}
    ).status_code == 400
    assert client.post(
        "/api/strategy/plan-race", json={"total_laps": 50, "start_compound": "CONCRETE"}
    ).status_code == 400

    planned = client.post(
        "/api/strategy/plan-race",
        json={"track_id": 31, "total_laps": 50, "start_compound": "MEDIUM"},
    )
    assert planned.status_code == 200
    payload = planned.json()
    assert payload["total_laps"] == 50
    assert payload["start_compound"] == "MEDIUM"
    assert "tyre_evidence" in payload
    assert "inferred" in payload["basis"]


def test_single_lap_analysis_is_reachable_from_lap_lab() -> None:
    """Item 8: analyze a lap with no counterpart."""
    assert 'id="analyzeLapAlone"' in INDEX
    assert 'id="soloAnalysisPane"' in INDEX
    workspaces = (ROOT / "static" / "js" / "workspaces.js").read_text(encoding="utf-8")
    assert "/analysis`" in workspaces
    assert "analyzeLapAlone" in workspaces


def test_race_control_blip_covers_every_neutralisation() -> None:
    """Item 10: speech can be late, the screen cannot."""
    assert 'id="raceControlBlip"' in INDEX
    assert "renderRaceControl" in INDEX
    for phase in ("red_flag", "safety_car", "vsc", "formation", "yellow", "blue"):
        assert phase in INDEX, phase
