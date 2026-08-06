from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).parents[1]
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
WORKSPACES = (ROOT / "static" / "js" / "workspaces.js").read_text(
    encoding="utf-8"
)
CSS = (ROOT / "static" / "css" / "v42.css").read_text(encoding="utf-8")


class _WorkspaceAudit(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.controls: dict[str, str] = {}

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(attributes["id"] or "")
        if attributes.get("role") == "tab" and attributes.get("aria-controls"):
            self.controls[attributes.get("id") or tag] = (
                attributes["aria-controls"] or ""
            )


def test_saved_session_workspaces_are_first_class_tabs() -> None:
    for name in ("library", "session-review", "lap-lab", "field"):
        assert f'id="tab-{name}"' in INDEX
        assert f'aria-controls="{name}"' in INDEX
        assert f'data-page="{name}"' in INDEX
        assert f'id="{name}"' in INDEX
        assert f'aria-labelledby="tab-{name}"' in INDEX

    assert 'id="review"' in INDEX  # The legacy history page remains available.
    assert 'id="setup"' in INDEX
    assert 'id="live"' in INDEX
    assert 'id="connection"' in INDEX


def test_workspace_markup_has_unique_ids_and_resolved_tab_targets() -> None:
    audit = _WorkspaceAudit()
    audit.feed(INDEX)
    assert len(audit.ids) == len(set(audit.ids))
    assert set(audit.controls.values()) <= set(audit.ids)


def test_library_and_review_use_real_versioned_session_contracts() -> None:
    for contract in (
        'api(`/sessions?${buildSessionQuery',
        'api(`/sessions/${encodeURIComponent(session.id)}`',
        'method: "PATCH"',
        'method: "DELETE"',
        '"X-Pitwall-Delete-Token"',
        'api(`/sessions/${encodeURIComponent(sessionId)}/quality`)',
        'api(`/sessions/${encodeURIComponent(sessionId)}/laps`)',
        '/reprocess`, { method: "POST"',
    ):
        assert contract in WORKSPACES

    for element_id in (
        "libraryRows",
        "libraryFilters",
        "reviewSessionSelect",
        "sessionLapRows",
        "reviewQualityBadge",
        "reviewReprocess",
    ):
        assert f'id="{element_id}"' in INDEX


def test_lap_lab_is_distance_synchronized_and_api_backed() -> None:
    for contract in (
        '/laps/${encodeURIComponent(lapId)}/references',
        'api("/comparisons"',
        '/comparisons/${encodeURIComponent(comparison.comparison_id)}/trace',
        'fields: "world_x,world_z,line_n,speed,brake,throttle,steering,gear"',
        'Positive means the candidate arrived later',
    ):
        assert contract in WORKSPACES or contract in INDEX

    for element_id in (
        "candidateLapSelect",
        "referenceLapSelect",
        "comparisonMap",
        "comparisonTrace",
        "playbackRange",
        "instrumentGrid",
        "segmentRail",
        "coachingFindings",
        "cursorDataTable",
    ):
        assert f'id="{element_id}"' in INDEX

    assert 'aria-label="Shared lap distance cursor"' not in INDEX
    assert 'for="playbackRange"' in INDEX
    assert 'role="img"' in INDEX


def test_field_lab_consumes_every_read_only_field_projection() -> None:
    for suffix in (
        'classification: "field"',
        'pace: "field/pace"',
        'corners: "field/corners"',
        'positions: "field/positions"',
        'stints: "field/stints"',
        '/field/drivers/${encodeURIComponent(carId)}',
    ):
        assert suffix in WORKSPACES

    for panel in (
        "field-panel-classification",
        "field-panel-pace",
        "field-panel-corners",
        "field-panel-positions",
        "field-panel-stints",
    ):
        assert f'id="{panel}"' in INDEX

    assert "metric.availability === \"unavailable\"" in WORKSPACES
    assert "zero is not substituted" in WORKSPACES
    assert "n=${payload.n_by_lap" in WORKSPACES
    assert "n=${payload.n_by_segment" in WORKSPACES


def test_unavailable_values_are_explicit_and_color_is_not_the_only_signal() -> None:
    assert INDEX.count("Unavailable") >= 20
    assert "availabilityTitle" in WORKSPACES
    assert "tableCell.textContent = label" in WORKSPACES
    assert 'data-result="gain"' in CSS
    assert 'data-result="loss"' in CSS
    assert 'data-result="unavailable"' in CSS
    assert "repeating-linear-gradient" in CSS
    assert "Faster than lap median" in INDEX
    assert "Excluded / unavailable" in INDEX


def test_saved_session_workspaces_are_responsive_and_local_only() -> None:
    assert '<script type="module" src="/static/js/workspaces.js"></script>' in INDEX
    assert "grid-template-columns: minmax(0, 1.45fr)" in CSS
    assert "@media (max-width: 1180px)" in CSS
    assert "@media (max-width: 700px)" in CSS
    assert "@media (max-width: 440px)" in CSS
    assert "overflow-x: auto" in CSS
    assert "min-height: 44px" in CSS
    assert "https://" not in WORKSPACES
    assert "http://" not in WORKSPACES


def test_workspaces_module_is_guarded_outside_a_browser() -> None:
    assert 'typeof window !== "undefined"' in WORKSPACES
    assert 'typeof document !== "undefined"' in WORKSPACES
    assert "if (HAS_DOM)" in WORKSPACES


def test_workspace_pages_never_scroll_horizontally() -> None:
    """A workspace page must not become a left-right scroll surface.

    `.workspace-page { overflow: auto }` made every analysis page a horizontal
    scroller: one overflowing child - an unbreakable evidence id in a coaching
    card, a long lap label - dragged the whole Lap Lab page sideways. Vertical
    scrolling is kept; horizontal is clipped, and genuinely wide regions keep
    their own overflow:auto containers.
    """
    block = CSS.split(".workspace-page {", 1)[1].split("}", 1)[0]
    assert "overflow-y: auto" in block
    assert "overflow-x: clip" in block
    assert "overflow: auto" not in block, (
        "overflow:auto on .workspace-page reintroduces the page-level "
        "horizontal scroll"
    )
    # The field matrix must still scroll inside its own container.
    assert ".matrix-scroll {" in CSS
    matrix = CSS.split(".matrix-scroll {", 1)[1].split("}", 1)[0]
    assert "overflow" in matrix


def test_finding_cards_wrap_long_evidence_tokens() -> None:
    """Evidence ids like cmp_01J...:brake_onset are long and unbreakable.

    In the narrow coaching column they must wrap rather than spill out of the
    card and force a scroll, the same way instrument values already wrap.
    """
    assert ".finding-facts li { overflow-wrap: anywhere; }" in CSS or (
        "overflow-wrap: anywhere" in CSS.split(".finding-card,", 1)[1].split("}", 1)[0]
    )


def test_lap_lab_has_its_own_session_selector() -> None:
    """Lap Lab must let a user pick the session in place.

    Previously only Session Review and Field carried a session selector, so
    reaching a lap in Lap Lab meant going back to another tab to choose the
    session first. The selector is wired to the same selectSession path and
    kept in sync with the other two.
    """
    assert 'id="lapLabSessionSelect"' in INDEX
    assert 'class="lab-session-bar' in INDEX
    # populated by the shared refresh alongside review and field
    assert 'replaceOptions(byId("lapLabSessionSelect")' in WORKSPACES
    # driven by the same handler as the other selectors
    assert 'byId("lapLabSessionSelect")?.addEventListener("change"' in WORKSPACES
    assert "selectSession(event.target.value)" in WORKSPACES
