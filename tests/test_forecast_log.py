"""The forecast is recorded through the session, and what it meant is measured.

Two questions the code could not answer: what the game's rain percentage
means, and how long rain that is falling keeps falling. Both are answerable
from the driver's own sessions once the forecast is kept.
"""

from __future__ import annotations

import pytest

from pitwall import rain
from pitwall.state import StateStore
from tools import forecast_reliability as reliability


def _entry(time_s, sky, samples, accuracy=0):
    return {"session_time_s": time_s, "sky_now": sky, "accuracy": accuracy, "samples": samples}


@pytest.mark.asyncio
async def test_an_unchanged_forecast_is_logged_once():
    store = StateStore()
    samples = [[0, "Overcast", 20], [5, "Light rain", 50]]
    await store.log_forecast(_entry(100.0, "Overcast", samples))
    await store.log_forecast(_entry(101.0, "Overcast", samples))
    await store.log_forecast(_entry(102.0, "Light rain", samples))
    assert [entry["session_time_s"] for entry in store.state.forecast_log] == [100.0, 102.0]


@pytest.mark.asyncio
async def test_the_log_stays_out_of_the_live_frame_and_in_the_analysis_snapshot():
    store = StateStore()
    await store.log_forecast(_entry(100.0, "Overcast", [[0, "Overcast", 20]]))
    assert (await store.snapshot_live())["forecast_log"] == []
    assert len((await store.snapshot_analysis())["forecast_log"]) == 1


@pytest.mark.asyncio
async def test_the_log_is_capped():
    from pitwall.state import FORECAST_LOG_CAP

    store = StateStore()
    for index in range(FORECAST_LOG_CAP + 20):
        await store.log_forecast(_entry(float(index), "Overcast", [[5, "Overcast", index % 100]]))
    assert len(store.state.forecast_log) == FORECAST_LOG_CAP


def test_a_sample_is_judged_by_the_sky_when_it_fell_due():
    log = [
        _entry(0.0, "Overcast", [[0, "Overcast", 10], [5, "Light rain", 60], [30, "Light rain", 60]]),
        _entry(240.0, "Light rain", [[0, "Light rain", 60]]),
        _entry(600.0, "Light rain", [[0, "Light rain", 70]]),
    ]
    rows = reliability.outcomes(log)
    # The five-minute sample fell due at 300 s, when it was raining; the
    # thirty-minute one fell due after the log ended and is not judged.
    assert [(row["offset_min"], row["wet"]) for row in rows] == [(5, True)]


def test_persistence_counts_wet_moments_that_stayed_wet():
    log = [
        _entry(0.0, "Light rain", [[0, "Light rain", 80]]),
        _entry(400.0, "Light rain", [[0, "Light rain", 80]]),
        _entry(700.0, "Overcast", [[0, "Overcast", 20]]),
        _entry(1200.0, "Overcast", [[0, "Overcast", 10]]),
    ]
    table = reliability.persistence([log])
    # Wet at 0 s and 400 s. Five minutes on, the first is still wet (300 s)
    # and the second has dried (700 s). Fifteen minutes on, the first has
    # dried and the second falls after the log ends, so it is not judged.
    assert table[(5, 80)] == (1, 2)
    assert table[(15, 80)] == (0, 1)


def test_the_best_persistence_is_recovered_from_synthetic_outcomes():
    """Outcomes drawn at a known persistence: the fit finds it within 0.1."""
    import random

    random.seed(3)
    original = rain._FORECAST_PERSISTENCE
    rain._FORECAST_PERSISTENCE = 0.5
    try:
        rows = []
        for _ in range(4000):
            chance, now, raining = random.choice([0.3, 0.5, 0.7]), random.choice([0.2, 0.5, 0.8]), random.random() < 0.5
            p = rain.conditioned_rain_probability(chance, now, raining)
            rows.append({"chance": int(chance * 100), "now_chance": int(now * 100), "raining_now": raining,
                         "wet": random.random() < p})
    finally:
        rain._FORECAST_PERSISTENCE = original
    best, _ = reliability.best_persistence(rows)
    assert abs(best - 0.5) <= 0.1
    assert rain._FORECAST_PERSISTENCE == original


def test_the_report_says_when_there_is_nothing_yet():
    assert "Nothing to measure yet" in reliability.report([])
