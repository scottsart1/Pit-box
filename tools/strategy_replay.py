"""Replay a race through the strategy engine and the radio, and record what they said.

Every change in the assessment changes what the engine says during a race;
the unit tests check functions, not the sequence of calls over a race. This
feeds a race through the same stack the app builds (state store, database on
a temporary file, lap analysis, strategy engine, proactive engineer) and
records a timeline:

* every change of the recommended plan;
* every change of the weather call's level;
* every call queued and every call said, with lap, lap distance and session
  time.

There is no provider and no audio device: the voice records the text and
reports it delivered, and the narrator always fails, so every call is its
template. One clock, advanced from the packets' session time, stands in for
``time`` in the modules that read it, so the timeline does not depend on how
fast the machine is and is identical between runs.

    python -m tools.strategy_replay CAPTURE.pwcap [--expect expectations.json] [--timeline out.json]
    python -m tools.strategy_replay --synthetic --laps 20 --rain-from-lap 8 --forecast-chance 50

``--expect`` takes a list of assertions over the timeline:

    [{"first": {"type": "weather_crossover", "level": 4}, "by_lap": 13},
     {"never": {"text_contains": "Stay out — boxing costs"}, "while": {"weather_level_at_least": 3}},
     {"count": {"type": "weather_crossover", "level": 4}, "at_most": 2},
     {"every": {"type": "rival_pitting"}, "satisfies": {"relevance_at_least": 2}}]

A match names ``kind`` ("said" unless given: "queued", "plan", "weather"),
``type``, ``level`` and ``text_contains``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time as real_time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

# The clock the replay starts from: a fixed instant, so stored timestamps and
# every cooldown are the same on every run.
EPOCH_S = 1_800_000_000.0
MONOTONIC_START_S = 10_000.0
# Facts of a call worth keeping in the timeline.
CALL_FACTS = ("level", "compound", "box_lap", "relevance", "car_idx", "to", "direction")


class ReplayClock:
    """``time`` for the modules under replay, driven by the packets' session time."""

    def __init__(self) -> None:
        self.session_time = 0.0

    def advance_to(self, session_time: float) -> None:
        self.session_time = max(self.session_time, float(session_time))

    def time(self) -> float:
        return EPOCH_S + self.session_time

    def time_ns(self) -> int:
        return int(self.time() * 1e9)

    def monotonic(self) -> float:
        return MONOTONIC_START_S + self.session_time

    def monotonic_ns(self) -> int:
        return int(self.monotonic() * 1e9)

    perf_counter = monotonic
    perf_counter_ns = monotonic_ns

    def __getattr__(self, name: str) -> Any:
        return getattr(real_time, name)


class _Brain:
    """No model: every call is its template."""

    def __init__(self, database: Any) -> None:
        self.database = database

    async def proactive(self, event: dict[str, Any]) -> str:
        raise RuntimeError("no narrator in a replay")

    async def record_spoken_call(self, text: str) -> None:
        return None


class _Voice:
    is_busy = False
    busy_reason = ""
    realtime_active = False

    async def speak_text(self, text: str) -> bool:
        return True


class _Setup:
    async def learn_current_session(self) -> None:
        return None


def _facts(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload or {}
    return {key: payload[key] for key in CALL_FACTS if payload.get(key) is not None}


def _round(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, dict):
        return {key: _round(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round(item) for item in value]
    return value


class Timeline:
    def __init__(self, clock: ReplayClock) -> None:
        self.clock = clock
        self.entries: list[dict[str, Any]] = []
        self.lap = 0
        self.distance = 0.0
        self.weather_level = 0
        self._plan: tuple[Any, ...] | None = None

    def add(self, kind: str, **fields: Any) -> None:
        self.entries.append(_round({
            "t": round(self.clock.session_time, 1), "lap": self.lap, "distance_m": round(self.distance),
            "kind": kind, "weather_level": self.weather_level, **fields,
        }))

    def said(self) -> list[dict[str, Any]]:
        return [entry for entry in self.entries if entry["kind"] == "said"]

    def observe(self, state: dict[str, Any]) -> None:
        from pitwall.proactive import ProactiveEngineer

        self.lap = int(state.get("current_lap", 0) or 0)
        self.distance = float(state.get("lap_distance_m", 0) or 0)
        strategy = state.get("strategy") or {}
        rec = strategy.get("recommended") or {}
        plan = (tuple(rec.get("box_laps") or []), tuple(rec.get("compounds") or []), rec.get("instruction"))
        if rec and plan != self._plan:
            self._plan = plan
            self.add("plan", instruction=rec.get("instruction"), box_laps=list(plan[0]), compounds=list(plan[1]))
        call = ProactiveEngineer._weather_call(state)
        if int(call["level"]) != self.weather_level:
            self.weather_level = int(call["level"])
            self.add("weather", compound=call.get("compound") or None, box_lap=call.get("box_lap"),
                     direction=call.get("direction"))

    def as_json(self) -> str:
        return json.dumps(self.entries, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


# --- packets ------------------------------------------------------------------------------


def capture_packets(path: Path) -> Iterable[tuple[float, bytes]]:
    from f1.packets import resolve

    from pitwall.capture import CaptureReader

    for frame in CaptureReader(path, require_footer=False):
        try:
            session_time = float(resolve(frame.data).header.session_time)
        except Exception:  # noqa: BLE001 - an unparseable datagram is replayed as it was received
            session_time = -1.0
        yield session_time, frame.data


def save_capture(packets: Iterable[tuple[float, bytes]], path: Path) -> Path:
    """Write packets to a PWCAP capture, stamped at their session time."""
    from pitwall.capture import CaptureWriter

    with CaptureWriter(path, metadata={"source": "tools.replay_demo", "created_wall_ns": 0}) as writer:
        for session_time, data in packets:
            stamp = int(max(0.0, session_time) * 1e9)
            writer.write(data, ("127.0.0.1", 20777), monotonic_ns=stamp, wall_ns=int(EPOCH_S * 1e9) + stamp)
    return path


def synthetic_packets(
    *, laps: int = 20, seed: int = 7, rain_from_lap: int | None = None, rain: str = "Light rain",
    forecast_chance: int = 50, tick: float = 0.5,
) -> Iterable[tuple[float, bytes]]:
    from tools import replay_demo

    spell = replay_demo.RainSpell(rain_from_lap, rain, forecast_chance) if rain_from_lap else None
    return replay_demo.race_packets(laps, seed, spell=spell, tick=tick, session_uid=0xF1_5EED_0000_0007)


# --- the replay -------------------------------------------------------------------------------


CLOCKED_MODULES = ("pitwall.proactive", "pitwall.strategy", "pitwall.state", "pitwall.udp", "pitwall.analysis")


async def replay(packets: Iterable[tuple[float, bytes]], *, detect_every_s: float = 1.0) -> Timeline:
    """Feed the packets at packet time and record what the engine and the radio did."""
    import importlib

    from f1.packets import resolve

    clock = ReplayClock()
    patched = []
    for name in CLOCKED_MODULES:
        module = importlib.import_module(name)
        if getattr(module, "time", None) is real_time:
            module.time = clock  # type: ignore[attr-defined]
            patched.append(module)
    try:
        return await _replay(packets, clock, resolve, detect_every_s)
    finally:
        for module in patched:
            module.time = real_time  # type: ignore[attr-defined]


async def _replay(packets: Iterable[tuple[float, bytes]], clock: ReplayClock, resolve: Any,
                  detect_every_s: float) -> Timeline:
    from pitwall.analysis import AnalysisEngine
    from pitwall.database import PitWallDatabase
    from pitwall.proactive import ProactiveEngineer
    from pitwall.session_guard import session_key
    from pitwall.state import StateStore
    from pitwall.strategy import StrategyEngine
    from pitwall.udp import F1DatagramProtocol

    timeline = Timeline(clock)
    with tempfile.TemporaryDirectory(prefix="strategy-replay-") as folder:
        store = StateStore()
        database = PitWallDatabase(Path(folder) / "replay.sqlite3")
        await database.initialize()
        strategy = StrategyEngine(store, database)
        analysis = AnalysisEngine(store, database, strategy)
        engineer = ProactiveEngineer(store, _Brain(database), _Voice(), _Setup(), strategy)  # type: ignore[arg-type]
        protocol = F1DatagramProtocol(store)

        enqueue = engineer._enqueue

        def queued(kind: str, payload: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
            result = enqueue(kind, payload, *args, **kwargs)
            if result:
                timeline.add("queued", type=kind, **_facts(payload))
            return result

        speak = engineer._speak

        async def said(event: dict[str, Any], text: str, state: dict[str, Any]) -> None:
            # Where the car was and what the weather call was when it was said.
            timeline.lap = int(state.get("current_lap", 0) or 0)
            timeline.distance = float(state.get("lap_distance_m", 0) or 0)
            level = int(ProactiveEngineer._weather_call(state)["level"])
            timeline.add("said", type=str(event.get("type", "")), text=text,
                         **{**_facts(event.get("payload")), "weather_level": level})
            await speak(event, text, state)

        engineer._enqueue = queued  # type: ignore[method-assign]
        engineer._speak = said  # type: ignore[method-assign]

        next_detect = 0.0
        for session_time, data in packets:
            if session_time >= 0:
                clock.advance_to(session_time)
            try:
                packet = resolve(data)
            except Exception:  # noqa: BLE001, S112 - the receiver drops what it cannot parse
                continue
            await protocol._handle(packet)
            while not store.lap_queue.empty():
                lap = store.lap_queue.get_nowait()
                try:
                    await analysis.process_lap(lap)
                except Exception as exc:  # noqa: BLE001 - as the analysis worker does
                    await store.update(last_error=f"Lap analysis failed: {exc}")
                finally:
                    store.lap_queue.task_done()
            if clock.session_time < next_detect:
                continue
            next_detect = clock.session_time + detect_every_s
            state = await store.snapshot_radio()
            if session_key(state) != engineer._session_context:
                await engineer._reset_for_session(int(state.get("session_uid", 0)))
                state = await store.snapshot_radio()
            timeline.observe(state)
            await engineer._detect(state)
            # As the delivery loop does: only when something is waiting.
            for _ in range(3):
                if not engineer.pending and not engineer._discarded:
                    break
                await engineer._deliver(await store.snapshot_radio())
        timeline.observe(await store.snapshot_radio())
    return timeline


# --- expectations -----------------------------------------------------------------------------


def _matches(entry: dict[str, Any], match: dict[str, Any]) -> bool:
    if entry.get("kind") != match.get("kind", "said"):
        return False
    for key, wanted in match.items():
        if key == "kind":
            continue
        if key == "text_contains":
            if str(wanted).lower() not in str(entry.get("text", "")).lower():
                return False
        elif entry.get(key) != wanted:
            return False
    return True


def _holds(entry: dict[str, Any], condition: dict[str, Any] | None) -> bool:
    for key, value in (condition or {}).items():
        if key == "weather_level_at_least" and int(entry.get("weather_level", 0)) < int(value):
            return False
        if key == "relevance_at_least" and int(entry.get("relevance", 0) or 0) < int(value):
            return False
        if key == "lap_at_least" and int(entry.get("lap", 0)) < int(value):
            return False
    return True


def check(entries: list[dict[str, Any]], expectations: list[dict[str, Any]]) -> list[str]:
    """The expectations the timeline breaks, each as a sentence."""
    failures = []
    for rule in expectations:
        if "first" in rule:
            found = next((entry for entry in entries if _matches(entry, rule["first"])), None)
            if found is None:
                failures.append(f"never happened: {rule['first']}")
            elif "by_lap" in rule and int(found["lap"]) > int(rule["by_lap"]):
                failures.append(f"{rule['first']} first on lap {found['lap']}, expected by lap {rule['by_lap']}")
        elif "never" in rule:
            hits = [entry for entry in entries if _matches(entry, rule["never"]) and _holds(entry, rule.get("while"))]
            if hits:
                failures.append(f"{rule['never']} happened {len(hits)} times, first on lap {hits[0]['lap']}")
        elif "count" in rule:
            total = sum(1 for entry in entries if _matches(entry, rule["count"]))
            if "at_most" in rule and total > int(rule["at_most"]):
                failures.append(f"{rule['count']} happened {total} times, at most {rule['at_most']} expected")
            if "at_least" in rule and total < int(rule["at_least"]):
                failures.append(f"{rule['count']} happened {total} times, at least {rule['at_least']} expected")
        elif "every" in rule:
            misses = [entry for entry in entries
                      if _matches(entry, rule["every"]) and not _holds(entry, rule.get("satisfies"))]
            if misses:
                failures.append(f"{len(misses)} of {rule['every']} did not satisfy {rule.get('satisfies')}")
        else:
            failures.append(f"unknown expectation: {rule}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("capture", nargs="?", type=Path, help="a PWCAP capture of a race")
    parser.add_argument("--synthetic", action="store_true", help="replay the synthetic race instead")
    parser.add_argument("--laps", type=int, default=20)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--rain-from-lap", type=int, default=None)
    parser.add_argument("--rain", default="Light rain")
    parser.add_argument("--forecast-chance", type=int, default=50)
    parser.add_argument("--expect", type=Path, help="a JSON list of assertions over the timeline")
    parser.add_argument("--timeline", type=Path, help="write the timeline as JSON")
    parser.add_argument("--save-capture", type=Path, help="with --synthetic: also write the race as a PWCAP capture")
    args = parser.parse_args(argv)
    if args.synthetic == bool(args.capture):
        parser.error("give a capture or --synthetic")
    packets = (
        synthetic_packets(laps=args.laps, seed=args.seed, rain_from_lap=args.rain_from_lap,
                          rain=args.rain, forecast_chance=args.forecast_chance)
        if args.synthetic else capture_packets(args.capture)
    )
    if args.synthetic and args.save_capture:
        packets = capture_packets(save_capture(packets, args.save_capture))
    timeline = asyncio.run(replay(packets))
    if args.timeline:
        args.timeline.write_text(timeline.as_json(), encoding="utf-8")
    for entry in timeline.entries:
        if entry["kind"] in {"said", "weather"}:
            what = entry.get("text") or f"weather level {entry['weather_level']}"
            print(f"lap {entry['lap']:>3}  {entry['distance_m']:>5} m  {entry['kind']:<7} {what}")
    if args.expect:
        failures = check(timeline.entries, json.loads(args.expect.read_text(encoding="utf-8")))
        for failure in failures:
            print(f"FAIL {failure}")
        if failures:
            return 1
        print("every expectation held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
