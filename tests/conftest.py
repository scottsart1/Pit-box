import os

import pytest_asyncio

# Never let a developer's real credentials enter the test process. Apart from
# preventing accidental network calls, this keeps pytest assertion output from
# disclosing secrets when a configuration test fails. Individual tests that
# exercise dotenv loading explicitly remove these sentinels with monkeypatch.
os.environ["OPENAI_API_KEY"] = "test-openai-key"
os.environ["DEEPSEEK_API_KEY"] = "test-deepseek-key"

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
