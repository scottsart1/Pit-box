"""Phone layout contracts for the dashboard shell and DRIVE.

These read the shipped markup and stylesheet. The rendered layout is checked
in a browser; what is asserted here is the part that silently regresses when
a card is added or renamed: the race order DRIVE takes in one column, and the
hooks the phone rules depend on.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "css" / "v42.css").read_text(encoding="utf-8")
WORKSPACES = (ROOT / "static" / "js" / "workspaces.js").read_text(encoding="utf-8")


def _media_block(marker: str) -> str:
    """Return the body of the @media rule whose body contains ``marker``."""
    position = CSS.index(marker)
    start = CSS.rindex("@media", 0, position)
    depth, index = 0, CSS.index("{", start)
    for index in range(index, len(CSS)):
        depth += {"{": 1, "}": -1}.get(CSS[index], 0)
        if depth == 0:
            break
    return CSS[start:index]


def _drive_orders() -> dict[str, int]:
    block = _media_block(".live-grid > .panel { display: contents; }")
    orders: dict[str, int] = {}
    for selectors, value in re.findall(r"([^{}]+)\{\s*order:\s*(\d+);\s*\}", block):
        for selector in selectors.split(","):
            orders[selector.strip()] = int(value)
    return orders


def test_drive_reads_in_race_order_once_it_is_one_column() -> None:
    block = _media_block(".live-grid > .panel { display: contents; }")
    assert block.startswith("@media (max-width: 1024px)")
    orders = _drive_orders()
    default = orders[".live-grid > .panel > *"]
    headline = orders[".live-grid .drive-headline"]
    call = orders[".live-grid #strategyCard"]
    car = orders[".live-grid #carCard"]
    radio = orders[".live-grid #radio"]
    tower = orders[".live-grid .drive-tower > *"]
    setup_cards = {
        orders[f".live-grid #{card}"]
        for card in ("latencyCard", "providerCard", "wakeCard", "pttCard", "proactiveCard")
    }
    assert headline < call < car < radio < tower < default
    assert setup_cards == {max(orders.values())}
    assert orders[".live-grid #preRaceCard"] == call
    assert orders[".live-grid .drive-ask"] == radio


def test_drive_markup_carries_the_hooks_the_phone_order_uses() -> None:
    for hook in (
        '<section class="panel drive-tower">',
        '<section class="panel drive-centre">',
        '<section class="panel drive-car">',
        '<div class="row drive-headline">',
        '<div class="controls drive-ask">',
    ):
        assert INDEX.count(hook) == 1, hook


def test_phone_header_keeps_its_buttons_named() -> None:
    # The phone header shows icons; the buttons keep their text, so their
    # accessible names are unchanged, and the icons add none of their own.
    assert ">⚙ Dashboard</button>" in INDEX
    assert ">Quit Your Pit Box</button>" in INDEX
    shell = _media_block(".top > #dashConfig::before")
    assert "@media (max-width: 700px), (max-height: 500px) and (orientation: landscape)" in shell
    assert 'content: "";' in shell


def test_selected_tab_is_scrolled_into_the_phone_strip() -> None:
    assert "function revealTab(btn)" in INDEX
    assert "revealTab(btn);document.querySelectorAll('.page')" in INDEX
    assert "if(on)revealTab(b)" in INDEX


def test_settings_rows_are_styled_by_class_not_inline_widths() -> None:
    assert '<div class="setting-row" data-kind="${esc(s.type)}">' in INDEX
    assert 'style="width:110px"' not in INDEX
    assert 'style="width:220px;max-width:100%"' not in INDEX
    assert "#settingsGroups .setting-control input[type=\"number\"] { width: 110px; }" in CSS


def test_library_cards_name_the_values_that_do_not_name_themselves() -> None:
    assert 'class="table workspace-table library-table"' in INDEX
    assert 'quality.dataset.label = i18n.t("Quality");' in WORKSPACES
    assert 'storage.dataset.label = i18n.t("Storage");' in WORKSPACES
    assert '.library-table td[data-label]::before { content: attr(data-label) " "' in CSS
