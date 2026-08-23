"""The 4.8 race-view toggle: original board untouched, new view opt-in.

The redesigned DRIVE board from the 4.7 design canvas ships behind an
explicit header toggle. One DOM serves both arrangements, so every live
binding keeps its id; these tests pin the contracts that make that safe.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


def test_the_view_toggle_is_in_the_header() -> None:
    assert 'id="viewToggle"' in INDEX
    # Persisted like every other board preference.
    assert "localStorage.setItem('pitwall.view'" in INDEX
    assert "localStorage.getItem('pitwall.view')" in INDEX


def test_the_call_strip_tops_the_drive_board() -> None:
    for element_id in (
        "callStrip", "callLabel", "callText", "callSub",
        "callChanges", "callConfidence", "callWhy", "callOverride",
    ):
        assert f'id="{element_id}"' in INDEX, element_id
    # The strip sits inside DRIVE, above the grid, and announces itself.
    live = INDEX.index('id="live"')
    strip = INDEX.index('id="callStrip"')
    grid = INDEX.index('class="live-grid"')
    assert live < strip < grid
    assert 'aria-live="polite"' in INDEX[strip:grid]


def test_the_original_view_is_the_default_and_stays_untouched() -> None:
    # Everything the new view changes is scoped under body[data-view="new"];
    # with the toggle off, no new-view CSS can touch the original board.
    assert "viewMode='original'" in INDEX
    assert '#callStrip{display:none' in INDEX
    for rule in (
        'body[data-view="new"] #callStrip{display:flex}',
        'body[data-view="new"] #live.active{display:flex',
    ):
        assert rule in INDEX, rule


def test_admin_cards_leave_the_new_race_view() -> None:
    # The 4.7 board map: DRIVE holds only what a driver reads mid-race.
    hidden = INDEX[INDEX.index('body[data-view="new"] #latencyCard'):]
    hidden = hidden[:hidden.index("}") + 1]
    for card in (
        "latencyCard", "providerCard", "wakeCard", "pttCard",
        "proactiveCard", "driverControlCard", "wingCard", "preRaceCard",
    ):
        assert f"#{card}" in hidden, card
    assert "display:none!important" in hidden


def test_fuel_merges_into_the_car_card_and_returns() -> None:
    # DOM move, not duplication: the ids keep their bindings both ways.
    assert "car.appendChild(fuel)" in INDEX
    assert "car.after(fuel)" in INDEX
    assert 'body[data-view="new"] #carCard #fuelCard' in INDEX


def test_the_new_view_hook_chains_after_the_normal_render() -> None:
    # The hook must never replace the original render pipeline.
    assert re.search(r"const renderPreView=render;render=function\(s\)\{renderPreView\(s\);", INDEX)
    assert "renderNewView(s)" in INDEX


def test_call_strip_actions_use_real_affordances() -> None:
    # Ask why routes through the existing radio; Override opens Strategy.
    assert "$('callWhy').onclick" in INDEX
    assert "$('askBtn').click()" in INDEX
    assert "$('callOverride').onclick=()=>selectPage('strategy')" in INDEX


def test_tower_movers_are_svg_not_dingbats() -> None:
    mover_block = INDEX[INDEX.index("Tower movers"):INDEX.index("renderPreView")]
    assert "<svg" in mover_block
    assert "▲" not in mover_block and "▼" not in mover_block
