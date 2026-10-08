from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("requested,expected_id,expected_name", [
    ("Singapore", 12, "Singapore"), ("12", 12, "Singapore"),
    ("current", 13, "Suzuka"), ("sUzUkA", 13, "Suzuka"),
])
async def test_personal_history_queries_requested_track_not_current_track(stack, monkeypatch, requested, expected_id, expected_name):
    store, database, _, _, _, tools = stack
    await store.update(track_id=13, track_name="Suzuka")
    review = AsyncMock(return_value={"laps": [{"lap_time_ms": 94000}], "corner_opportunities": []})
    best = AsyncMock(return_value={"lap_time_ms": 93000})
    monkeypatch.setattr(database, "track_review", review)
    monkeypatch.setattr(database, "get_personal_best", best)
    result = await tools.get_personal_history(requested)
    review.assert_awaited_once_with(expected_id, 30)
    best.assert_awaited_once_with(expected_id)
    assert result["track_id"] == expected_id and result["track"] == expected_name
    assert result["personal_best"] == "1:33.000"


@pytest.mark.asyncio
async def test_unrecognized_track_cannot_relabel_current_history(stack, monkeypatch):
    store, database, _, _, _, tools = stack
    await store.update(track_id=13, track_name="Suzuka")
    review = AsyncMock()
    monkeypatch.setattr(database, "track_review", review)
    result = await tools.get_personal_history("invented circuit")
    assert result["available"] is False
    review.assert_not_awaited()
