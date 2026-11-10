"""What the game's rain percentage has meant, measured from recorded sessions.

Every finished race stores its forecast log (each change to the published
samples, with the sky at that moment). This reads every stored log and
prints:

* for each published chance, in bands of 20 points, and each sample's sky:
  how often the sky was wet when that sample fell due;
* persistence: the share of wet-now moments still wet 5, 10 and 15 minutes
  later, split by the present chance;
* the value of ``rain._FORECAST_PERSISTENCE`` that best fits the outcomes;
* the same reliability split by forecast accuracy (perfect or approximate).

    python tools/forecast_reliability.py [--database PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pitwall import rain  # noqa: E402

WET_SKIES = {"light rain", "heavy rain", "storm"}


def _wet(sky: Any) -> bool:
    return str(sky or "").strip().lower() in WET_SKIES


def sky_at(log: list[dict[str, Any]], when_s: float) -> str | None:
    """The sky at a session time, from the last log entry at or before it.

    Unknown past the end of the log: nothing says the sky held after the last
    recorded change.
    """
    if not log or when_s > float(log[-1]["session_time_s"]) + 60.0:
        return None
    known = None
    for entry in log:
        if float(entry["session_time_s"]) <= when_s:
            known = entry.get("sky_now")
        else:
            break
    return known


def outcomes(log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every future sample with what the sky was when it fell due."""
    rows = []
    for entry in log:
        samples = [tuple(sample) for sample in entry.get("samples") or []]
        now = next((chance for offset, _, chance in samples if int(offset) == 0), None)
        for offset, sky, chance in samples:
            if int(offset) <= 0:
                continue
            due = sky_at(log, float(entry["session_time_s"]) + 60.0 * int(offset))
            if due is None:
                continue
            rows.append({
                "chance": int(chance), "sample_sky": sky, "offset_min": int(offset),
                "now_chance": now, "raining_now": _wet(entry.get("sky_now")),
                "accuracy": int(entry.get("accuracy", 0) or 0), "wet": _wet(due),
            })
    return rows


def reliability(rows: list[dict[str, Any]]) -> dict[tuple[int, str], tuple[int, int]]:
    """(chance band, sample sky) -> (wet when due, samples)."""
    table: dict[tuple[int, str], list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        band = min(80, (row["chance"] // 20) * 20)
        cell = table[(band, str(row["sample_sky"]))]
        cell[0] += int(row["wet"])
        cell[1] += 1
    return {key: (value[0], value[1]) for key, value in table.items()}


def persistence(logs: list[list[dict[str, Any]]]) -> dict[tuple[int, int], tuple[int, int]]:
    """(minutes later, present-chance band) -> (still wet, wet-now moments)."""
    table: dict[tuple[int, int], list[int]] = defaultdict(lambda: [0, 0])
    for log in logs:
        for entry in log:
            if not _wet(entry.get("sky_now")):
                continue
            now = next((int(chance) for offset, _, chance in entry.get("samples") or [] if int(offset) == 0), None)
            band = -1 if now is None else min(80, (now // 20) * 20)
            for minutes in (5, 10, 15):
                later = sky_at(log, float(entry["session_time_s"]) + 60.0 * minutes)
                if later is None:
                    continue
                cell = table[(minutes, band)]
                cell[0] += int(_wet(later))
                cell[1] += 1
    return {key: (value[0], value[1]) for key, value in table.items()}


def best_persistence(rows: list[dict[str, Any]]) -> tuple[float, float] | None:
    """The persistence with the lowest Brier score over every outcome, and the score."""
    usable = [row for row in rows if row["now_chance"] is not None]
    if not usable:
        return None
    original = rain._FORECAST_PERSISTENCE
    best: tuple[float, float] | None = None
    try:
        for step in range(21):
            rain._FORECAST_PERSISTENCE = step / 20.0
            score = sum(
                (rain.conditioned_rain_probability(row["chance"] / 100.0, row["now_chance"] / 100.0,
                                                   row["raining_now"]) - float(row["wet"])) ** 2
                for row in usable
            ) / len(usable)
            if best is None or score < best[1]:
                best = (step / 20.0, score)
    finally:
        rain._FORECAST_PERSISTENCE = original
    return best


async def stored_logs(database_path: Path) -> list[list[dict[str, Any]]]:
    from pitwall.database import PitWallDatabase

    database = PitWallDatabase(database_path)
    stored = await database.load_preferences_with_prefix("forecast_log:")
    return [log for log in stored.values() if isinstance(log, list) and log]


def report(logs: list[list[dict[str, Any]]]) -> str:
    rows = [row for log in logs for row in outcomes(log)]
    lines = [f"{len(logs)} sessions, {len(rows)} forecast samples that fell due inside a session.", ""]
    if not rows:
        lines.append("Nothing to measure yet: forecast logs are stored at the end of each finished race.")
        return "\n".join(lines)
    lines.append("Published chance  sample sky       wet when due")
    for (band, sky), (wet, total) in sorted(reliability(rows).items()):
        lines.append(f"{band:>3}-{band + 19:<3}%         {sky:<16} {wet}/{total} ({100 * wet / total:.0f}%)")
    lines += ["", "Still wet later    present chance   share"]
    for (minutes, band), (wet, total) in sorted(persistence(logs).items()):
        label = "unknown" if band < 0 else f"{band}-{band + 19}%"
        lines.append(f"+{minutes:<2} min           {label:<16} {wet}/{total} ({100 * wet / total:.0f}%)")
    best = best_persistence(rows)
    if best is not None:
        fitted = (f"Best-fitting forecast persistence: {best[0]:.2f} (Brier {best[1]:.3f}); "
                  f"the model uses {rain._FORECAST_PERSISTENCE:.2f}.")
        lines += ["", fitted]
    lines += ["", "By forecast accuracy (0 perfect, 1 approximate)"]
    for accuracy in sorted({row["accuracy"] for row in rows}):
        subset = [row for row in rows if row["accuracy"] == accuracy]
        wet = sum(row["wet"] for row in subset)
        mean_chance = sum(row["chance"] for row in subset) / len(subset)
        lines.append(f"accuracy {accuracy}: {len(subset)} samples, mean published {mean_chance:.0f}%, "
                     f"wet when due {100 * wet / len(subset):.0f}%")
    return "\n".join(lines)


def main() -> int:
    from pitwall.config import settings

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--database", type=Path, default=settings.data_dir / "pitwall.sqlite3")
    args = parser.parse_args()
    print(report(asyncio.run(stored_logs(args.database))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
