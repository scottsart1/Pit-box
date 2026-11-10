"""Snapshot the planner's answer for a fixed set of race states.

A refactor that only makes the planner faster must not move its answer. This
records, for ten seeded race states from ``tools.strategy_fuzz``, the
recommended stop and the top five plans with their finishing position and
risk-adjusted time, so a test can compare the two.

    python -m tools.strategy_projection_snapshot --write tests/fixtures/strategy_projection_baseline.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from pitwall.database import PitWallDatabase  # noqa: E402
from pitwall.state import StateStore  # noqa: E402
from pitwall.strategy import StrategyEngine  # noqa: E402
from tools.strategy_fuzz import apply, build_scenario  # noqa: E402

SEED = 7
SCENARIOS = 10


def _plan_row(plan: dict[str, Any]) -> list[Any]:
    return [
        list(plan.get("box_laps") or []),
        list(plan.get("compounds") or []),
        plan.get("projected_finish_position"),
        round(float(plan.get("risk_adjusted_time_s", 0.0)), 3),
    ]


async def snapshot() -> list[dict[str, Any]]:
    """The planner's answer for the first ten race states that have one."""
    store = StateStore()
    database = PitWallDatabase(Path(tempfile.mkdtemp(prefix="projection_")) / "db.sqlite3")
    await database.initialize()
    engine = StrategyEngine(store, database)
    results: list[dict[str, Any]] = []
    index = 0
    while len(results) < SCENARIOS and index < 400:
        rng = random.Random(SEED * 100_003 + index)
        scenario = build_scenario(index, rng)
        index += 1
        if scenario.setup["mode_profile"] not in {"race", "sprint"}:
            continue
        await store.mutate(lambda state, setup=scenario.setup: apply(state, setup))
        plan = await engine.recompute()
        if not plan.get("available") or not plan.get("plans"):
            continue
        recommended = plan.get("recommended") or {}
        results.append({
            "scenario": index - 1,
            "recommended": [recommended.get("box_lap"), recommended.get("fit_compound"),
                            recommended.get("stops_remaining")],
            "top": [_plan_row(item) for item in plan["plans"][:5]],
        })
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", type=Path, help="write the snapshot to this file")
    args = parser.parse_args()
    results = asyncio.run(snapshot())
    text = "[\n" + ",\n".join(json.dumps(item) for item in results) + "\n]"
    if args.write:
        args.write.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
