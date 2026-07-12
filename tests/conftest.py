import pytest_asyncio

from pitwall.analysis import AnalysisEngine
from pitwall.database import PitWallDatabase
from pitwall.setup_advisor import SetupAdvisor
from pitwall.state import StateStore
from pitwall.strategy import StrategyEngine
from pitwall.tools import TelemetryTools


@pytest_asyncio.fixture
async def stack(tmp_path):
    store = StateStore()
    database = PitWallDatabase(tmp_path / "pitwall.sqlite3")
    await database.initialize()
    strategy = StrategyEngine(store, database)
    setup = SetupAdvisor(store, database)
    analysis = AnalysisEngine(store, database, strategy)
    tools = TelemetryTools(store, database, analysis, strategy, setup)
    return store, database, strategy, setup, analysis, tools
