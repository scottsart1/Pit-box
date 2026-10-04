"""Library categories include the game's numbered sessions without broad matches."""
import pytest

from pitwall.database import PitWallDatabase


@pytest.mark.asyncio
async def test_library_session_families_exact_variants_and_pagination(tmp_path):
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    families = {
        "Practice": ["Practice", "Practice 1", "Practice 2", "Practice 3", "Short Practice"],
        "Qualifying": ["Qualifying", "Qualifying 1", "Qualifying 2", "Qualifying 3",
                       "Short Qualifying", "One-Shot Qualifying", "Sprint Shootout 1",
                       "Sprint Shootout 2", "Sprint Shootout 3", "Short Sprint Shootout",
                       "One-Shot Sprint Shoot"],
        "Race": ["Race", "Race 2", "Race 3", "Sprint"],
        "Time Trial": ["Time Trial"],
    }
    labels = [label for group in families.values() for label in group] + ["Custom Practice Review", "Unknown"]
    for index, label in enumerate(labels, start=1):
        await database.catalog.upsert_live_session({"session_uid": index, "track_id": 10,
                                                     "session_type": label})
    for category, expected in families.items():
        found = []
        cursor = None
        while True:
            page = await database.catalog.list_sessions(session_type=category.swapcase(), limit=2, cursor=cursor)
            found.extend(row["session_type"] for row in page["items"])
            cursor = page["next_cursor"]
            if not cursor:
                break
        assert sorted(found) == sorted(expected)
    for label in ("Practice 2", "Short Qualifying", "Sprint Shootout 1", "Race 3"):
        page = await database.catalog.list_sessions(session_type=" " + label + " ")
        assert [row["session_type"] for row in page["items"]] == [label]
    assert len((await database.catalog.list_sessions(limit=200))["items"]) == len(labels)
    assert not (await database.catalog.list_sessions(session_type="Practice", track_id=0))["items"]
    assert not (await database.catalog.list_sessions(session_type="Practice' OR 1=1 --"))["items"]
