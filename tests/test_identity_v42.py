from __future__ import annotations

from pitwall.identity import SessionIdentityRegistry


def test_identity_enrichment_does_not_relabel_history_but_conflict_creates_revision() -> None:
    registry = SessionIdentityRegistry()
    session = registry.begin_session(99)
    first = registry.observe(4, 10, {"name": "Unknown", "is_player": False})
    enriched = registry.observe(
        4,
        11,
        {
            "name": "VERSTAPPEN",
            "driver_id": 9,
            "race_number": 1,
            "team_id": 2,
            "is_player": False,
        },
    )
    assert enriched.id == first.id
    assert enriched.identity_revision == 0
    assert enriched.first_frame == 10
    assert enriched.last_frame == 11
    assert enriched.session_id == session.id

    replacement = registry.observe(
        4,
        50,
        {
            "name": "NORRIS",
            "driver_id": 54,
            "race_number": 4,
            "team_id": 8,
        },
    )
    assert replacement.id != first.id
    assert replacement.identity_revision == 1
    assert "driver_id" in replacement.change_reason
    assert [item.identity_revision for item in registry.revisions(4)] == [0, 1]


def test_same_game_uid_restart_gets_new_session_key_and_resets_car_revisions() -> None:
    registry = SessionIdentityRegistry()
    original = registry.begin_session("123")
    original_car = registry.observe(0, 1, {"name": "Player", "is_player": True})
    assert registry.begin_session("123") == original

    restarted = registry.begin_session("123", restart_evidence=True)
    restarted_car = registry.observe(0, 1, {"name": "Player", "is_player": True})
    assert restarted.restart_epoch == 1
    assert restarted.id != original.id
    assert restarted_car.identity_revision == 0
    assert restarted_car.id != original_car.id


def test_identity_export_anonymizes_name_and_network_id_with_stable_alias() -> None:
    registry = SessionIdentityRegistry()
    registry.begin_session(456)
    identity = registry.observe(
        6,
        12,
        {
            "name": "League Driver",
            "network_id": "platform-secret-id",
            "race_number": 27,
        },
    )
    public = identity.as_record(anonymize=True)
    assert public["display_name"] == "Driver 07"
    assert public["anonymized_name"] == "Driver 07"
    assert public["name"] is None
    assert public["network_id"] is None

