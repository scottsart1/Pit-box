"""Engineer answer evaluation: driver commands through the real EngineerBrain.ask.

Each case in the frozen manifest (docs/engineer-eval-manifest.json) builds a
synthetic race state, asks one driver command through the real ``ask()`` and
grades the words the driver would hear:

* required facts: values read from the state or from a deterministic tool that
  the answer must state (numbers matched at the precision spoken, with the
  same unit conversions as the grounding check), or literal text;
* forbidden fragments that must not appear;
* a sentence limit;
* the command matrix's output-format rule: no braces, snake_case names, tool
  names or raw null values;
* an optional verdict: a yes or no in the first sentence, or no
  stay-out-to-the-flag endorsement while the engine's own compound rule still
  needs a stop;
* grounding, reported separately: numbers in the answer that nothing the model
  was shown backs (the prompt and the tool results), from
  ``src/pitwall/grounding.py``.

Offline, the default, every provider call is answered by a scripted provider
from the case's ``scripted`` reply, after it makes the case's scripted tool
calls through the real tool layer. That exercises the harness, routing,
header, tools and post-processing with no network, and the test suite runs it.
``--live`` sends the same cases to the configured providers, or to those named
with ``--provider``, and reports pass/fail per provider. Live runs cost money
and are never made by the test suite.

    python tools/engineer_eval.py --output /tmp/engineer-eval.json
    python tools/engineer_eval.py --live --provider openai --output /tmp/live.json
    python tools/engineer_eval.py --source /path/to/other/worktree --output /tmp/other.json

The graders and scenarios are loaded from this file's checkout, so one
evaluator grades both revisions of a paired comparison; only the app under
test comes from ``--source``. Findings are data: the exit status is 0 unless
the harness itself fails.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO / "docs/engineer-eval-manifest.json"
SCRIPTED = "scripted"


def _load_grounding():
    """The grader's own copy of the grounding check, whatever --source is."""
    spec = importlib.util.spec_from_file_location(
        "_engineer_eval_grounding", REPO / "src/pitwall/grounding.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


grounding = _load_grounding()

# The command matrix's seed: the same fields tests/test_engineer_command_matrix.py
# starts every command from.
MATRIX_PACKET_GROUPS = (1, 2, 5, 6, 7, 10, 16)
MATRIX_SESSION_UID = 54321
MATRIX_SEED: dict[str, Any] = {
    "regulations_2026": True, "session_type": "Race", "mode_profile": "race",
    "current_lap": 8, "total_laps": 20, "player_position": 3, "player_car_index": 0,
    "fuel_laps_delta": -0.4, "fuel_kg": 25, "ers_pct": 62,
    "overtake_available": True, "overtake_active": False,
    "active_aero_mode": 0, "active_aero_available": True,
    "weather": "Clear", "weather_forecast": [{"time_offset_min": 15, "rain_pct": 20}],
    "rain_next_15_pct": 20, "corner_cutting_warnings": 2, "penalties_s": 3,
    "damage": {"front_left_wing": 6, "floor": 4},
    "tyre": {"compound": "MEDIUM", "age_laps": 5, "wear": [21, 22, 24, 28],
             "inner_temps_c": [92, 94, 96, 98]},
}

_YES = re.compile(r"\b(?:yes|yeah|yep|affirmative|go for it)\b|\byou can\b(?!['’]t|not)", re.I)
_NO = re.compile(
    r"\b(?:no|nope|negative|don't|do not|can't|cannot|won't|not yet|not now|hold off|not without)\b",
    re.I,
)
# From the stay-out check: an answer may not endorse running the current set
# to the flag, or affirm a question about doing so, while a stop is still owed.
# A sentence that negates the stay-out ("you cannot stay out to the end") is
# the opposite of an endorsement.
_ENDORSES_FINISH = re.compile(
    r"\b(?:serviceable|last|make it|fine|good)\b[^.]{0,20}\bto the (?:flag|end|finish)\b"
    r"|\bstay(?:ing)? out (?:to|until) the (?:end|flag|finish)\b",
    re.I,
)
_NO_STOP_NEEDED = re.compile(r"\bno (?:need|reason) to (?:stop|pit|box)\b", re.I)
_NEGATION = re.compile(r"\b(?:not|no|never|can't|cannot|won't|don't|shouldn't|isn't|aren't)\b|n['’]t\b", re.I)
_ASKS_FINISH = re.compile(r"stay out (?:to|until) the (?:end|flag|finish)|to the end|to the flag", re.I)
_AFFIRMS = re.compile(r"^\s*(?:yes|yeah|affirm)\b|^\s*you can\b(?!['’]t|not)", re.I)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")
_FORMAT_RULES = (
    ("brace or bracket", re.compile(r"[{}\[\]]")),
    ("snake_case name", re.compile(r"\b[a-z]+(?:_[a-z0-9]+)+\b")),
    ("raw null value", re.compile(r"\b(?:null|NaN|undefined)\b|\bNone\b(?!\s+of\b)")),
)


# ---------------------------------------------------------------- the app


def _app_modules(source: Path) -> dict[str, Any]:
    """Import the app under test from ``source``."""
    src = str(source / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from pitwall.analysis import AnalysisEngine
    from pitwall.brain import EngineerBrain
    from pitwall.database import PitWallDatabase
    from pitwall.providers import ProviderResult
    from pitwall.setup_advisor import SetupAdvisor
    from pitwall.state import StateStore
    from pitwall.strategy import StrategyEngine
    from pitwall.tools import TelemetryTools
    from pitwall.udp import F1DatagramProtocol

    return {
        "AnalysisEngine": AnalysisEngine, "EngineerBrain": EngineerBrain,
        "PitWallDatabase": PitWallDatabase, "ProviderResult": ProviderResult,
        "SetupAdvisor": SetupAdvisor, "StateStore": StateStore,
        "StrategyEngine": StrategyEngine, "TelemetryTools": TelemetryTools,
        "F1DatagramProtocol": F1DatagramProtocol,
    }


class _Stack:
    """The app's engines on a throwaway database, as the test fixture builds them."""

    def __init__(self, app: dict[str, Any], directory: Path) -> None:
        self.app = app
        self.directory = directory

    async def __aenter__(self) -> "_Stack":
        app = self.app
        self.store = app["StateStore"]()
        self.database = app["PitWallDatabase"](self.directory / "pitwall.sqlite3")
        await self.database.initialize()
        self.strategy = app["StrategyEngine"](self.store, self.database)
        self.setup = app["SetupAdvisor"](self.store, self.database)
        self.analysis = app["AnalysisEngine"](self.store, self.database, self.strategy)
        self.tools = app["TelemetryTools"](
            self.store, self.database, self.analysis, self.strategy, self.setup
        )
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        stop = getattr(self.strategy, "stop", None)
        if stop is not None:
            with contextlib.suppress(Exception):
                await stop()


# ------------------------------------------------------------ state builders


async def _build_matrix(stack: _Stack, spec: dict[str, Any]) -> None:
    store = stack.store
    for group in MATRIX_PACKET_GROUPS:
        await store.mark_packet(2026, 26, MATRIX_SESSION_UID, packet_id=group)
    await store.update(**copy.deepcopy(MATRIX_SEED))
    rivals = spec.get("rivals") or []
    if rivals:
        def place(state: Any) -> None:
            for rival in rivals:
                driver = state.drivers[int(rival["index"])]
                for key, value in rival.items():
                    if key != "index":
                        setattr(driver, key, value)
        await store.mutate(place)
    if spec.get("update"):
        await store.update(**copy.deepcopy(spec["update"]))
    missing = [str(group) for group in spec.get("missing_groups", [])]
    if missing:
        await store.mutate(lambda state: [state.packet_group_freshness.pop(group, None) for group in missing])
    stale = [str(group) for group in spec.get("stale_groups", [])]
    if stale:
        old = time.time() - 100
        await store.mutate(lambda state: state.packet_group_freshness.update({group: old for group in stale}))
        # An unrelated packet still arrives, so the receiver stays connected.
        await store.mark_packet(2026, 26, MATRIX_SESSION_UID, packet_id=0)
    if spec.get("new_session"):
        await store.mark_packet(2026, 26, MATRIX_SESSION_UID + 1, packet_id=1)


async def _build_scenario(stack: _Stack, spec: dict[str, Any]) -> None:
    """Replay a tools/scenarios.py race through the real packet parser."""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from tools.demo_broadcast import Race
    from tools.scenarios import SCENARIOS

    scenario = SCENARIOS[spec["scenario"]]
    protocol = stack.app["F1DatagramProtocol"](stack.store)
    consumer = asyncio.create_task(protocol._consume_packets())
    race = Race(4, scenario)
    race.lap = scenario.start_lap
    race.distance = [distance * 0.2 for distance in race.distance]

    def send_all() -> None:
        for packet in (
            race.session_packet(), race.participants_packet(), race.lap_packet(),
            race.motion_packet(), race.telemetry_packet(), race.status_packet(),
            race.damage_packet(),
        ):
            protocol.datagram_received(packet, ("127.0.0.1", 9999))

    try:
        ticks = 0
        while race.lap < scenario.display_lap and ticks < 4000:
            ticks += 1
            race.advance(warm_up=True)
            send_all()
            if ticks % 50 == 0:
                await protocol.packet_queue.join()
        race.set_display_state()
        for _ in range(30):
            race.advance()
            race.set_display_state()
            send_all()
        await protocol.packet_queue.join()
    finally:
        consumer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consumer
    if spec.get("update"):
        await stack.store.update(**copy.deepcopy(spec["update"]))
    await stack.strategy.recompute()


BUILDERS = {"matrix": _build_matrix, "scenario": _build_scenario}


# --------------------------------------------------------------- the facts


def _lookup(context: dict[str, Any], path: str) -> Any:
    """``state.tyre.wear.3`` or ``tool.get_pit_strategy.recommended.box_laps.0``."""
    parts = path.split(".")
    if parts[0] == "tool":
        value: Any = context["tool"].get(parts[1])
        parts = parts[2:]
    else:
        value = context.get(parts[0])
        parts = parts[1:]
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.lstrip("-").isdigit() and -len(value) <= int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def _compound_word(value: str) -> str:
    name = value.strip().lower()
    return name if name.endswith("s") else f"{name}s"


_FILTERS = {
    "abs": lambda value: abs(value) if isinstance(value, (int, float)) else value,
    "lower": lambda value: str(value).lower(),
    "plural": _compound_word,
    "round1": lambda value: f"{float(value):.1f}",
    "int": lambda value: f"{round(float(value))}",
    "max": lambda value: max(value) if isinstance(value, list) and value else value,
    "capital": lambda value: str(value)[:1].upper() + str(value)[1:],
}


def _render(template: str, context: dict[str, Any]) -> str:
    """A scripted reply with ``{path|filter}`` placeholders filled from the facts."""
    def fill(match: re.Match[str]) -> str:
        path, *filters = match.group(1).split("|")
        value = _lookup(context, path.strip())
        for name in filters:
            value = _FILTERS[name.strip()](value)
        if isinstance(value, float):
            return f"{value:g}"
        return str(value)

    return re.sub(r"\{([a-z_][\w.|-]*)\}", fill, template)


def _states(answer: str, value: Any, unit: str | None = None) -> bool:
    """Whether the answer states one fact value, in ``unit`` when one is named."""
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        target = float(value)
        if unit is None:
            return any(
                grounding.is_backed(spoken, [target]) for spoken in grounding.spoken_numbers(answer)
            )
        if unit == "temp_f":
            target = target * 9 / 5 + 32
        return any(
            spoken.unit == unit and abs(spoken.value - abs(target)) <= 0.5 * 10.0 ** -spoken.decimals + 1e-9
            for spoken in grounding.spoken_numbers(answer)
        )
    text = str(value).strip().lower()
    lowered = answer.lower()
    if text in {"soft", "medium", "hard", "inter", "wet"}:
        return text in lowered
    return text.replace("_", " ") in lowered.replace("-", " ") or text in lowered


def _check_required(answer: str, requirements: list[dict[str, Any]], context: dict[str, Any]) -> list[str]:
    """What the answer should state and does not.

    A requirement is literal ``text`` (any of a list), or a fact ``path`` whose
    value, or every value of a list, the answer must state. ``unit`` asks for
    the value in that unit: ``temp_f`` converts a Celsius fact.
    """
    missing = []
    for requirement in requirements:
        if "text" in requirement:
            options = requirement["text"] if isinstance(requirement["text"], list) else [requirement["text"]]
            if not any(option.lower() in answer.lower() for option in options):
                missing.append(" or ".join(repr(option) for option in options))
            continue
        path = requirement["path"]
        value = _lookup(context, path)
        values = value if isinstance(value, list) else [value]
        if value is None or not values:
            missing.append(f"{path} (no value in the evidence)")
            continue
        unstated = [item for item in values if not _states(answer, item, requirement.get("unit"))]
        if unstated:
            missing.append(f"{path} = {', '.join(map(str, unstated))}")
    return missing


def _check_state(expected: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return [
        f"{path} is {_lookup({'after': after}, 'after.' + path)!r}, expected {value!r}"
        for path, value in expected.items()
        if _lookup({"after": after}, "after." + path) != value
    ]


def _sentences(answer: str) -> list[str]:
    return [part for part in _SENTENCE_BREAK.split(answer.strip()) if part.strip()]


def _format_problems(answer: str, tool_names: set[str]) -> list[str]:
    problems = [label for label, pattern in _FORMAT_RULES if pattern.search(answer)]
    problems += [f"tool name {name}" for name in sorted(tool_names) if name in answer]
    return problems


def _stop_still_required(context: dict[str, Any]) -> bool:
    plan = context["tool"].get("get_pit_strategy") or (context.get("state") or {}).get("strategy") or {}
    rule = plan.get("compound_rule") or {}
    return bool(rule.get("applies")) and bool(
        rule.get("conditional_on_future_use")
        or rule.get("change_outstanding")
        or rule.get("compliant") is False
    )


def _first_verdict(sentence: str) -> str | None:
    """Whichever of yes or no the sentence says first."""
    said = [(match.start(), word) for word, pattern in (("yes", _YES), ("no", _NO))
            if (match := pattern.search(sentence))]
    return min(said)[1] if said else None


def _check_verdict(verdict: dict[str, Any], utterance: str, answer: str, context: dict[str, Any]) -> list[str]:
    problems = []
    sentences = _sentences(answer)
    expected = verdict.get("yes_no")
    if expected:
        said = _first_verdict(sentences[0] if sentences else "")
        if said is None:
            problems.append("no yes or no in the first sentence")
        elif expected in {"yes", "no"} and said != expected:
            problems.append(f"first sentence says {said}, expected {expected}")
    if verdict.get("compound_rule") and _stop_still_required(context):
        if _NO_STOP_NEEDED.search(answer) or any(
            _ENDORSES_FINISH.search(sentence) and not _NEGATION.search(sentence)
            for sentence in sentences
        ):
            problems.append("endorses finishing on the current set while the compound rule needs a stop")
        if _ASKS_FINISH.search(utterance) and _AFFIRMS.search(answer):
            problems.append("affirms staying out to the end while the compound rule needs a stop")
    return problems


# ------------------------------------------------------------- the provider


class _Recorder:
    """Sits in front of the provider router: what the model was shown and given."""

    def __init__(self, generate: Any) -> None:
        self.generate = generate
        self.prompts: list[str] = []
        self.tool_results: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> Any:
        self.prompts.append(str(kwargs.get("prompt", "")))
        execute = kwargs["execute_tool"]

        async def recorded(name: str, arguments: dict[str, Any]) -> Any:
            result = await execute(name, arguments)
            self.tool_results.append({"name": name, "arguments": arguments, "result": result})
            return result

        kwargs["execute_tool"] = recorded
        return await self.generate(**kwargs)


def _scripted_provider(app: dict[str, Any], case: dict[str, Any], context: dict[str, Any]) -> Any:
    script = case.get("scripted") or {}

    async def generate(**kwargs: Any) -> Any:
        for name, arguments in script.get("tools", []):
            context["tool"][name] = await kwargs["execute_tool"](name, arguments)
        reply = _render(script.get("reply", "I do not have that yet."), context)
        return app["ProviderResult"](reply, SCRIPTED, "scripted-reply", 0.0, tool_rounds=len(script.get("tools", [])))

    return generate


def _live_provider(router: Any, name: str) -> Any:
    original = router.generate

    async def generate(**kwargs: Any) -> Any:
        kwargs["provider"] = name  # an explicit provider is honoured alone
        return await original(**kwargs)

    return generate


# ------------------------------------------------------------------ a case


async def run_case(app: dict[str, Any], case: dict[str, Any], provider: str) -> dict[str, Any]:
    """Build the case's state, ask its command once and grade the answer.

    A case that cannot be built or asked is reported as failed with its
    error; the run goes on.
    """
    row: dict[str, Any] = {
        "case": case["id"], "matrix_row": case.get("matrix_row", ""),
        "provider": provider, "utterance": case["utterance"],
    }
    try:
        row.update(await _ask_and_grade(app, case, provider))
    except Exception as exc:  # the harness reports, it does not stop
        row.update({
            "answer": "", "answered_by": "none", "model": "", "provider_calls": 0,
            "tool_calls": [], "checks": {}, "ungrounded": [],
            "error": f"{type(exc).__name__}: {exc}", "passed": False, "grounded": False,
        })
    return row


async def _ask_and_grade(app: dict[str, Any], case: dict[str, Any], provider: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="engineer-eval-") as directory:
        async with _Stack(app, Path(directory)) as stack:
            spec = case.get("state", {"builder": "matrix"})
            await BUILDERS[spec.get("builder", "matrix")](stack, spec)
            brain = app["EngineerBrain"](stack.store, stack.tools, stack.database)
            context: dict[str, Any] = {"state": {}, "tool": {}}
            # In place before the earlier turns too: offline, nothing reaches
            # a real provider.
            recorder = _Recorder(
                _scripted_provider(app, case, context) if provider == SCRIPTED
                else _live_provider(brain.router, provider)
            )
            brain.router.generate = recorder
            for earlier in case.get("before", []):
                await brain.ask(earlier)
            recorder.prompts.clear()
            recorder.tool_results.clear()
            state = await stack.store.snapshot_analysis()
            # The facts the answer is graded on, from the deterministic tool
            # layer on the state the command is asked in.
            context["state"] = state
            context["tool"].clear()
            for name in case.get("tools", []):
                context["tool"][name] = await stack.tools.call(name, {})
            graded_tools = dict(context["tool"])
            header = await brain.situation_header(include_strategy=True)
            started = time.perf_counter()
            answer = await brain.ask(case["utterance"])
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            after = await stack.store.snapshot_analysis()
            result = brain.last_provider_result
            tool_names = {schema["name"] for schema in stack.tools.schemas_for_route("deep")}

    called = bool(recorder.prompts)
    answered_by = result.provider if result is not None and called else "local"
    shown = [item["result"] for item in recorder.tool_results]
    # A model answer is graded against what the model was shown; a local one,
    # deterministic by construction, against the state it was computed from.
    evidence: list[Any] = [*recorder.prompts, shown]
    if answered_by == "local":
        # The driver's own words count: "box lap 12 for hards" read back.
        evidence += [state, header, graded_tools, case["utterance"], *case.get("before", [])]
    plan = (state.get("strategy") or {}).get("recommended") or {}
    graded_plan = (graded_tools.get("get_pit_strategy") or {}).get("recommended") or {}
    ungrounded = grounding.unsupported(
        answer, *evidence,
        laps=grounding.lap_counts(
            state.get("current_lap"), state.get("total_laps"),
            plan, graded_plan, state.get("strategy_hold") or {},
        ),
        places=grounding.place_counts(
            state.get("player_position"), {"active_cars": state.get("active_cars")}, shown,
        ),
    )
    context["after"] = after
    checks = {
        "required_missing": _check_required(answer, case.get("required", []), context),
        "forbidden_found": [
            fragment for fragment in case.get("forbidden", [])
            if fragment.lower() in answer.lower()
        ],
        "state_mismatches": _check_state(case.get("expect_state", {}), after),
        "sentences": len(_sentences(answer)),
        "sentence_limit": case.get("max_sentences"),
        "format_problems": _format_problems(answer, tool_names),
        "verdict_problems": _check_verdict(case.get("verdict", {}), case["utterance"], answer, context),
    }
    over_limit = checks["sentence_limit"] is not None and checks["sentences"] > checks["sentence_limit"]
    # A provider that was called but did not produce the answer failed over to
    # the local fallback; say why.
    error = str(after.get("llm_last_error") or "") if called and answered_by == "local" else ""
    passed = bool(answer) and not error and not over_limit and not any(
        checks[key] for key in (
            "required_missing", "forbidden_found", "state_mismatches",
            "format_problems", "verdict_problems",
        )
    )
    return {
        "answer": answer,
        "answered_by": answered_by,
        "model": result.model if result is not None and called else "local",
        "latency_ms": latency_ms,
        "provider_calls": len(recorder.prompts),
        "tool_calls": [item["name"] for item in recorder.tool_results],
        "checks": checks,
        "ungrounded": [number.text for number in ungrounded],
        "error": error,
        "passed": passed,
        "grounded": not ungrounded,
    }


# -------------------------------------------------------------- the report


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for provider in dict.fromkeys(row["provider"] for row in rows):
        selected = [row for row in rows if row["provider"] == provider]
        summary[provider] = {
            "cases": len(selected),
            "passed": sum(row["passed"] for row in selected),
            "failed": [row["case"] for row in selected if not row["passed"]],
            "grounded": sum(row["grounded"] for row in selected),
            "ungrounded": {row["case"]: row["ungrounded"] for row in selected if row["ungrounded"]},
            "answered_locally": sum(row["answered_by"] == "local" for row in selected),
            "errors": {row["case"]: row["error"] for row in selected if row["error"]},
        }
    return summary


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


async def run_cases(
    cases: list[dict[str, Any]],
    providers: list[str],
    *,
    source: Path = REPO,
    progress: Any = None,
) -> list[dict[str, Any]]:
    app = _app_modules(source)
    rows = []
    for provider in providers:
        for case in cases:
            row = await run_case(app, case, provider)
            rows.append(row)
            if progress is not None:
                progress(row)
    return rows


def provider_names(source: Path = REPO) -> dict[str, bool]:
    """Every provider the router knows, and whether it has credentials here."""
    _app_modules(source)
    from pitwall.providers import ProviderRouter

    return {name: provider.available for name, provider in ProviderRouter().providers.items()}


def _git(source: Path, *args: str) -> bytes:
    completed = subprocess.run(["git", *args], cwd=source, capture_output=True, check=False)
    return completed.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=REPO, help="Revision worktree whose src/pitwall is evaluated")
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--output", type=Path, help="Write the full JSON report here")
    parser.add_argument("--live", action="store_true", help="Call real providers; costs money")
    parser.add_argument("--provider", action="append", default=[], help="Live provider to evaluate (repeatable)")
    parser.add_argument("--only", action="append", default=[], help="Case id to run (repeatable)")
    args = parser.parse_args(argv)
    if args.provider and not args.live:
        parser.error("--provider needs --live: offline runs use the scripted provider")
    source = args.source.resolve()
    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    cases = [case for case in manifest["cases"] if not args.only or case["id"] in args.only]
    if args.live:
        known = provider_names(source)
        unknown = [name for name in args.provider if name not in known]
        if unknown:
            parser.error(f"unknown provider {', '.join(unknown)}; known: {', '.join(known)}")
        providers = args.provider or [name for name, available in known.items() if available]
        if not providers:
            parser.error("--live found no provider with credentials configured")
    else:
        providers = [SCRIPTED]

    def progress(row: dict[str, Any]) -> None:
        verdict = "pass" if row["passed"] else "FAIL"
        grounded = "" if row["grounded"] else f" ungrounded={row['ungrounded']}"
        print(f"{row['provider']:<10} {row['case']:<28} {verdict} ({row['answered_by']}){grounded}", flush=True)

    rows = asyncio.run(run_cases(cases, providers, source=source, progress=progress))
    diff = _git(source, "diff", "HEAD", "--", "src")
    report = {
        "evaluated_commit": _git(source, "rev-parse", "HEAD").decode().strip(),
        "source_has_uncommitted_changes": bool(diff),
        "source_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "grounding_sha256": hashlib.sha256((REPO / "src/pitwall/grounding.py").read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "mode": "live" if args.live else "offline",
        "providers": providers,
        "summary": summarize(rows),
        "cases": rows,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
