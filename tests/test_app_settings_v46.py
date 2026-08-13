"""Dashboard settings: saved in the database, never in .env, never lying.

Pins the tier-2 Settings design: a whitelisted subset of Settings fields is
persisted as one preference row and applied over the .env installation
defaults at import time (before services construct). Restart-flagged fields
must refuse to pretend they applied live, saved garbage must never break a
launch, and wake_enabled must stay out — ptt.json already owns it and two
writers would fight.
"""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from pitwall.settings_service import (
    PREFERENCE_KEY,
    SETTINGS_SPEC,
    apply_runtime,
    apply_saved_overrides,
    coerce,
    load_saved,
    settings_view,
)


def _seed(db_path, saved: dict) -> None:
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_preferences (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        db.execute(
            "INSERT OR REPLACE INTO user_preferences VALUES (?, ?, 0)",
            (PREFERENCE_KEY, json.dumps(saved)),
        )


def test_coercion_accepts_good_values_and_names_the_bad():
    assert coerce("proactive_enabled", "true") is True
    assert coerce("proactive_cadence_laps", "3") == 3
    assert coerce("wake_phrase", "  Boxbox ") == "boxbox"
    assert coerce("field_trace_scope", "ALL") == "all"
    with pytest.raises(ValueError, match="between 1 and 10"):
        coerce("proactive_cadence_laps", 99)
    with pytest.raises(ValueError, match="one of"):
        coerce("capture_mode", "turbo")
    with pytest.raises(ValueError, match="not an adjustable setting"):
        coerce("openai_api_key", "sk-nope")
    with pytest.raises(ValueError, match="characters"):
        coerce("wake_phrase", "x")


def test_wake_enabled_is_deliberately_not_adjustable_here():
    # ptt.json persists it; a second writer would silently fight the first.
    assert "wake_enabled" not in SETTINGS_SPEC


def test_missing_database_or_table_means_no_overrides(tmp_path):
    assert load_saved(tmp_path / "absent.sqlite3") == {}
    empty = tmp_path / "empty.sqlite3"
    sqlite3.connect(empty).close()
    assert load_saved(empty) == {}


def test_overrides_apply_over_env_defaults_and_garbage_is_skipped(tmp_path):
    db = tmp_path / "pitwall.sqlite3"
    _seed(
        db,
        {
            "engineer_name": "Bono",
            "proactive_cadence_laps": 4,
            "capture_mode": "not-a-mode",  # tightened range must not crash launch
            "unknown_future_field": 1,
        },
    )
    live = SimpleNamespace(
        **{name: object() for name in SETTINGS_SPEC}
    )
    applied = apply_saved_overrides(live, db)
    assert applied == {"engineer_name": "Bono", "proactive_cadence_laps": 4}
    assert live.engineer_name == "Bono"
    assert live.proactive_cadence_laps == 4


def test_restart_fields_refuse_to_pretend(tmp_path):
    live = SimpleNamespace(web_port=8000, engineer_name="Mark")
    assert apply_runtime(live, "web_port", 8100) == "restart_required"
    assert live.web_port == 8000  # untouched: the server bound the old one
    assert apply_runtime(live, "engineer_name", "Bono") == "applied"
    assert live.engineer_name == "Bono"


def test_view_reports_value_provenance_and_pending_restart(tmp_path):
    from pitwall.config import Settings

    db = tmp_path / "pitwall.sqlite3"
    _seed(db, {"web_port": 8100, "engineer_name": "Bono"})
    live = Settings()
    apply_saved_overrides(live, db)
    # web_port was applied at import time in production; simulate the running
    # server still holding the old port by resetting the live attribute.
    live.web_port = 8000
    view = {entry["name"]: entry for entry in settings_view(live, load_saved(db))}
    assert view["engineer_name"]["value"] == "Bono"
    assert view["engineer_name"]["source"] == "saved in app"
    assert view["web_port"]["restart_required"] is True
    assert view["web_port"]["pending_restart"] is True
    assert view["proactive_cadence_laps"]["source"] in {"default", ".env"}
    assert view["capture_mode"]["choices"] == ["minimal", "balanced", "full_fidelity"]


@pytest.mark.asyncio
async def test_round_trip_through_the_real_preference_store(stack):
    _, database, _, _, _, _ = stack
    await database.save_preference(PREFERENCE_KEY, {"engineer_name": "Bono"})
    assert load_saved(database.path) == {"engineer_name": "Bono"}
