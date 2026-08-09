"""A recorded session must point at its own history.

The catalog stores the game's unsigned uid as text; the legacy store puts it in
a signed SQLite INTEGER column. `legacy_session_uid` is the bridge, and the
live recording path hardcoded it to NULL — so a finished race had no link to
the events recorded during it. A real Mexico race carried 198 session events
and 207 proactive calls that Session Review could not find.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pitwall.catalog import _legacy_session_uid  # noqa: E402

# The uid from the race that exposed this. Above 2**63-1, which is exactly the
# range where the two stores disagree.
MEXICO_UNSIGNED = 15846396385962814608
MEXICO_SIGNED = -2600347687746737008


def test_the_real_race_uid_maps_to_the_form_its_events_are_stored_under() -> None:
    assert _legacy_session_uid(MEXICO_UNSIGNED) == MEXICO_SIGNED
    # Same 64-bit value, two representations.
    assert MEXICO_SIGNED & ((1 << 64) - 1) == MEXICO_UNSIGNED


def test_small_uids_are_unchanged() -> None:
    # Below 2**63 the two stores already agree; the mapping must be identity or
    # it would break every session that currently works.
    assert _legacy_session_uid(195040773089631105) == 195040773089631105
    assert _legacy_session_uid(0) == 0
    assert _legacy_session_uid(1) == 1


def test_a_value_already_signed_is_left_alone() -> None:
    # Re-applying the mapping must not push a negative value back out of range.
    assert _legacy_session_uid(MEXICO_SIGNED) == MEXICO_SIGNED


def test_the_mapping_round_trips_across_the_boundary() -> None:
    for unsigned in (
        (1 << 63) - 1,   # largest value needing no conversion
        1 << 63,         # first value that does
        (1 << 64) - 1,   # largest possible uid
    ):
        signed = _legacy_session_uid(unsigned)
        assert -(1 << 63) <= signed <= (1 << 63) - 1, "must fit a signed column"
        assert signed & ((1 << 64) - 1) == unsigned


def test_unusable_input_does_not_break_recording() -> None:
    # Recording a session matters more than the pointer; a bad uid must not
    # raise and abort the insert.
    assert _legacy_session_uid(None) is None
    assert _legacy_session_uid("not-a-uid") is None


def test_the_live_insert_no_longer_hardcodes_null() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "src" / "pitwall" / "catalog.py"
    ).read_text(encoding="utf-8")
    assert "quality_score, created_at, updated_at\n                ) VALUES (?, NULL," not in source
    assert "_legacy_session_uid(game_uid)" in source
