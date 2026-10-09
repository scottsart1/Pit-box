"""Comparisons as Lap Lab's automatic comparison drives them.

Since 5.4.0 choosing a lap compares it with the suggested reference at once,
and changing the reference compares again. In 4.13.0 a repeated comparison
failed with 'UNIQUE constraint failed: findings.id' and the owner saw
"moments of success, then failure". Browsing back and forth must stay
idempotent, and a lap a flashback abandoned is never offered as a reference.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from test_comparison_service_v42 import _three_laps


@pytest.mark.asyncio
async def test_switching_references_back_and_forth_stays_idempotent(tmp_path: Path) -> None:
    service, reference_id, candidate_id, other_id, database, _ = await _three_laps(tmp_path)
    ids: dict[str, set[str]] = {reference_id: set(), other_id: set()}
    findings: dict[str, set[int]] = {reference_id: set(), other_id: set()}
    for step in range(12):
        reference = (reference_id, other_id)[step % 2]
        result = await service.create_comparison(candidate_id, reference_kind="lap", reference_lap_id=reference)
        ids[reference].add(result["comparison_id"])
        findings[reference].add(len(result["findings"]))
    assert all(len(values) == 1 for values in ids.values()), ids
    assert all(len(values) == 1 for values in findings.values()), findings
    with sqlite3.connect(database.path) as db:
        rows = db.execute("SELECT COUNT(*) FROM comparisons WHERE candidate_lap_id=?", (candidate_id,)).fetchone()[0]
        stored = {
            comparison_id: db.execute("SELECT COUNT(*) FROM findings WHERE comparison_id=?", (comparison_id,)).fetchone()[0]
            for values in ids.values() for comparison_id in values
        }
    assert rows == 2
    for reference, values in ids.items():
        assert stored[next(iter(values))] == next(iter(findings[reference]))


@pytest.mark.asyncio
async def test_a_lap_a_flashback_abandoned_is_never_offered_as_a_reference(tmp_path: Path) -> None:
    service, reference_id, candidate_id, other_id, database, _ = await _three_laps(tmp_path)
    offered = {item["lap_id"] for item in (await service.list_references(candidate_id))["items"]}
    assert reference_id in offered
    with sqlite3.connect(database.path) as db:
        db.execute("UPDATE recorded_laps SET valid=0, invalid_reason_mask=(invalid_reason_mask | 2) WHERE id=?",
                   (reference_id,))
        db.commit()
    offered = {item["lap_id"] for item in (await service.list_references(candidate_id))["items"]}
    assert reference_id not in offered
    assert other_id in offered


@pytest.mark.asyncio
async def test_an_older_timeline_of_a_lap_is_never_offered_as_a_reference(tmp_path: Path) -> None:
    service, reference_id, candidate_id, other_id, database, _ = await _three_laps(tmp_path)
    with sqlite3.connect(database.path) as db:
        db.row_factory = sqlite3.Row
        row = dict(db.execute("SELECT * FROM recorded_laps WHERE id=?", (other_id,)).fetchone())
        newer = {**row, "id": f"{other_id}-e1", "timeline_epoch": int(row["timeline_epoch"]) + 1, "legacy_lap_id": None}
        columns = ",".join(newer)
        db.execute(f"INSERT INTO recorded_laps({columns}) VALUES ({','.join('?' for _ in newer)})", tuple(newer.values()))
        db.commit()
    offered = {item["lap_id"] for item in (await service.list_references(candidate_id))["items"]}
    assert other_id not in offered
    assert f"{other_id}-e1" in offered
    assert reference_id in offered
