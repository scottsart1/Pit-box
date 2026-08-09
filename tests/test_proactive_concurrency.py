"""Detection must not stop while the engineer is speaking.

Detection and delivery used to share one task, so an utterance was a blackout.
Every race-control trigger is edge-based, comparing the current reading with
the last one seen, so a flag that both appeared and cleared inside one sentence
was never announced late — it was never seen. Replaying a real Mexico race
through the parser produced a safety car lasting 7.7 seconds, which fits
comfortably inside a single 10-16 second race-control call.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pitwall import proactive as proactive_module  # noqa: E402


class _Recorder:
    """Stands in for the engine, recording when detection actually ran."""

    def __init__(self) -> None:
        self.detections = 0
        self.speaking = False
        self.detections_while_speaking = 0


@pytest.mark.asyncio
async def test_detection_continues_while_the_engineer_speaks(monkeypatch) -> None:
    engine = object.__new__(proactive_module.ProactiveEngineer)
    rec = _Recorder()

    class _Store:
        async def snapshot_analysis(self):
            return {"session_uid": 1}

        async def mutate(self, fn):
            return None

        async def update(self, **kw):
            return None

    engine.store = _Store()
    engine.pending = []
    engine._session_uid = 1
    engine._task = None
    engine._deliver_task = None

    async def fake_detect(state):
        rec.detections += 1
        if rec.speaking:
            rec.detections_while_speaking += 1

    async def fake_deliver(state):
        # Stand in for narration plus playback: the real path blocks for the
        # whole utterance.
        rec.speaking = True
        await asyncio.sleep(0.6)
        rec.speaking = False

    engine._detect = fake_detect
    engine._deliver = fake_deliver

    monkeypatch.setattr(proactive_module, "DETECT_INTERVAL_S", 0.02)
    monkeypatch.setattr(proactive_module, "DELIVER_INTERVAL_S", 0.01)

    await proactive_module.ProactiveEngineer.start(engine)
    await asyncio.sleep(0.9)
    await proactive_module.ProactiveEngineer.stop(engine)

    # The whole point: the race kept being watched through the utterance.
    assert rec.detections_while_speaking > 5, (
        "detection stalled while speaking: "
        f"{rec.detections_while_speaking} of {rec.detections} ran during speech"
    )


def test_race_control_is_not_coalesced_away() -> None:
    """A flag change is an event, not a level.

    Coalescing kept only the newest pending call of a type, which is right for
    a reading that a later one replaces and wrong for a flag: it discarded
    "safety car deployed" and spoke only "safety car ending", so twice in one
    real race the driver was told a neutralisation had finished without ever
    being told it began.
    """
    source = (Path(__file__).resolve().parents[1] / "src" / "pitwall" / "proactive.py").read_text(
        encoding="utf-8"
    )
    start = source.index("coalesced = {")
    body = source[start : source.index("}", start)]
    assert '"race_control"' not in body
    assert '"blue_flag"' not in body
    # These genuinely are levels and must stay coalesced.
    assert '"tyre_wear"' in body
    assert '"progress_update"' in body
