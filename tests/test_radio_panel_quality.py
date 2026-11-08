"""The live frames must tell the dashboards when the radio log has moved on.

A frame carries only the newest 40 radio entries. The DRIVE and STRATEGY radio
panels used to redraw when that list's length changed, so from the 40th entry
of a session they froze: a typed question's answer, every later reply and every
spoken call stayed off the screen. ``radio_seq`` changes with every change to
the log, and the panels redraw on it.
"""

from __future__ import annotations

import json

import pytest

from pitwall.state import StateStore


async def _frame(store: StateStore) -> dict:
    return json.loads(await store.snapshot_live_json())


@pytest.mark.asyncio
async def test_every_new_radio_entry_changes_the_live_frame_after_the_window_fills() -> None:
    store = StateStore()
    seen: list[int] = []
    for number in range(1, 46):
        role = "driver" if number % 2 else "engineer"
        await store.append_radio(role, f"message {number}")
        frame = await _frame(store)
        assert len(frame["radio_log"]) == min(number, 40)
        assert frame["radio_log"][-1]["text"] == f"message {number}"
        seen.append(frame["radio_seq"])

    # The frame length is stuck at 40 from the 40th entry; the sequence is not.
    assert all(later > earlier for earlier, later in zip(seen, seen[1:]))
    assert len(set(seen[39:])) == len(seen[39:]) == 6


@pytest.mark.asyncio
async def test_a_rejected_radio_entry_does_not_move_the_sequence() -> None:
    store = StateStore()
    await store.append_radio("driver", "gap to Norris?")
    before = (await _frame(store))["radio_seq"]
    stale_origin = {**(await store.snapshot_live()), "timeline_epoch": 7}
    assert await store.append_radio("engineer", "1.2 seconds", expected=stale_origin) is False
    assert (await _frame(store))["radio_seq"] == before


@pytest.mark.asyncio
async def test_clearing_the_log_never_reuses_a_sequence_a_dashboard_has_drawn() -> None:
    store = StateStore()
    for number in range(3):
        await store.append_radio("engineer", f"call {number}")
    drawn = {(await _frame(store))["radio_seq"]}

    # A flashback clears the log in place.
    await store.synchronize_session_epoch(0, 0, 1)
    frame = await _frame(store)
    assert frame["radio_log"] == []
    assert frame["radio_seq"] not in drawn
    drawn.add(frame["radio_seq"])

    for number in range(3):
        await store.append_radio("engineer", f"after flashback {number}")
        drawn.add((await _frame(store))["radio_seq"])

    # A restart replaces the whole session state, log included.
    await store.synchronize_session_epoch(0, 1, 1)
    frame = await _frame(store)
    assert frame["radio_log"] == []
    assert frame["radio_seq"] not in drawn
    await store.append_radio("engineer", "first call of the restart")
    assert (await _frame(store))["radio_seq"] not in drawn
