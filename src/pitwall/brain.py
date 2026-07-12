from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from .config import settings
from .database import PitWallDatabase
from .state import StateStore
from .tools import TelemetryTools


PERSONA = """
You are Pit Wall, a senior Formula 1 simulator race engineer.

Be calm, precise, proactive, numbers-first, and brief. Lead with the action.
Use telemetry tools for facts not already in the situation header. Never invent a number.
If telemetry is unavailable or stale, say so plainly.

Pit calls must specify BOTH the lap and the tyre compound when the strategy tool supports it:
"Box lap 18 for hards." Never repeat an expired game pit window as a current recommendation.
The deterministic Pit Wall strategy plan is primary; the game's window is only a cross-check.
When strategy confidence is low, call the recommendation provisional and name the wear/deg
assumption; do not present a track-default model as proven personal degradation.
Respect the strategy tool's compound-rule legality. Never recommend finishing a dry Race
without two different dry compounds unless inters or wets have been used.
For SC, VSC, and red-flag calls, use the tool's neutralisation state and effective pit loss;
do not treat a red-flag tyre change like an ordinary green-flag pit stop.
For a strategy request, inspect the ranked alternatives, uncertainty, projected per-wheel wear,
traffic/rejoin, compound legality, and evidence source. Explain why the chosen plan beats the
next plan and state the condition that would change the call. Never simply echo the first plan.
For attack/defence advice, use gap trend, rival tyres/laps, racing-line/corner evidence, ERS and
DRS. Give a concrete preparation point and overtaking/defending phase, or say evidence is absent.

In qualifying, do not volunteer race-style gaps ahead or behind. The useful comparison is the
field's best lap times, the driver's best lap, theoretical best, target lap, and the delta required.
Only discuss gaps in qualifying when the driver explicitly asks about traffic or track position.

For driving coaching, separate fuel-saving advice from tyre-temperature advice. Use learned
corner metrics and the lap map when available. Refer to auto-learned corners by their displayed
name or distance. Do not claim a named real-world corner unless the tool provides that name.

For setup advice, state that only the front wing is normally adjustable during a pit stop.
Full Race, Quali, and Hybrid setup recommendations are for the garage or a future session.

Normally answer in one or two short radio sentences. A deeper requested explanation may use
three or four concise sentences. Do not mention being an AI.
""".strip()

PROACTIVE_PERSONA = """
You are making an unsolicited race-engineer radio call. Be useful, not chatty.
Lead with the single most important action or target. Include at most three numbers.
Do not repeat a recent call unless the situation materially changed. Do not ask a question.
For a two-lap progress update, cover pace versus target, tyre/strategy status, and one driving
opportunity only when supported. Keep it to two short radio sentences.
In qualifying, replace gaps and race strategy with pole/session-best time, the driver's best,
theoretical best, target time, required delta, and one lap-building opportunity.
""".strip()

_DEEP_TERMS = (
    "strategy",
    "undercut",
    "overcut",
    "what if",
    "compare",
    "degradation",
    "deg ",
    "why",
    "forecast",
    "last laps",
    "make it to",
    "pit now",
    "stay out",
    "setup",
    "wing",
    "corner",
    "lap analysis",
    "best lap",
    "target lap",
    "pole time",
)

_FEEDBACK_PATTERNS = {
    "understeer": ("understeer", "won't turn", "does not turn", "front won't bite"),
    "no_front_grip": ("no front grip", "fronts are gone", "front tyres are gone"),
    "oversteer": ("oversteer", "rear is loose", "rear steps out"),
    "rear_instability": ("rear unstable", "rear instability", "snappy rear"),
    "traction": ("no traction", "wheelspin", "spinning the rears"),
    "no_grip": ("no grip", "tires are toast", "tyres are toast"),
}


class EngineerBrain:
    def __init__(
        self,
        store: StateStore,
        tools: TelemetryTools,
        database: PitWallDatabase,
    ) -> None:
        self.store = store
        self.tools = tools
        self.database = database
        self.client = (
            AsyncOpenAI(
                api_key=settings.api_key,
                timeout=settings.openai_timeout_s,
                max_retries=2,
            )
            if settings.api_key
            else None
        )

    async def _header(self) -> str:
        state = await self.store.snapshot_analysis()
        target = state.get("analysis", {}).get("target", {})
        top = state.get("analysis", {}).get("flagged_corners", [])
        corner_text = (
            top[0].get("instruction") if top else "no repeated corner loss yet"
        )

        if state.get("mode_profile") == "qualifying":
            qualifying = self.tools.qualifying_summary(state, 5)
            field_text = ", ".join(
                f"{item['name']} {item['best_lap']}"
                for item in qualifying.get("field", [])
                if item.get("best_lap")
            ) or "no representative field laps yet"
            return (
                f"SESSION {state['session_type']} | {state['track_name']} | "
                f"LAP {state['current_lap']} | CONNECTED {state['connected']} "
                f"PAUSED {state['game_paused']} | "
                f"QUALI BEST LAPS {field_text} | "
                f"PLAYER BEST {qualifying.get('player_best')} | "
                f"THEORETICAL {qualifying.get('theoretical_best')} | "
                f"TARGET {qualifying.get('target')} | "
                f"REQUIRED DELTA {qualifying.get('delta_player_to_target_s')}s | "
                f"TYRE {state['tyre']['compound']} age {state['tyre']['age_laps']} | "
                f"WEATHER {state['weather']} rain15 {state['rain_next_15_pct']}% | "
                f"OPPORTUNITY {corner_text}. "
                "Do not volunteer gaps ahead/behind; use best-lap comparisons."
            )

        plan = state.get("strategy", {}).get("recommended", {})
        strategy_text = plan.get("instruction", "strategy building")
        return (
            f"SESSION {state['session_type']} | {state['track_name']} | "
            f"LAP {state['current_lap']}/{state['total_laps']} | "
            f"P{state['player_position']} | CONNECTED {state['connected']} "
            f"PAUSED {state['game_paused']} | "
            f"{state['tyre']['compound']} age {state['tyre']['age_laps']} "
            f"wear FL/FR/RL/RR {state['tyre']['wear']} | "
            f"inner temps FL/FR/RL/RR {state['tyre']['inner_temps_c']} | "
            f"fuel delta {state['fuel_laps_delta']:+.1f} laps | "
            f"ERS {state['ers_pct']:.0f}% | weather {state['weather']} "
            f"rain15 {state['rain_next_15_pct']}% | "
            f"race control {state.get('race_control_phase', 'green')} | "
            f"compound rule {state.get('strategy', {}).get('compound_rule', {})} | "
            f"target {target.get('target')} | strategy {strategy_text} | "
            f"opportunity {corner_text}"
        )

    @staticmethod
    def _is_deep(utterance: str) -> bool:
        text = utterance.lower()
        return len(text.split()) >= 18 or any(term in text for term in _DEEP_TERMS)

    async def _capture_feedback(self, utterance: str) -> None:
        lowered = utterance.lower()
        state = await self.store.snapshot_analysis()
        for category, patterns in _FEEDBACK_PATTERNS.items():
            if any(pattern in lowered for pattern in patterns):
                await self.store.add_feedback(category, utterance)
                await self.database.add_feedback(
                    int(state.get("session_uid", 0)),
                    int(state.get("track_id", -1)),
                    category,
                    utterance,
                )
                break

    @staticmethod
    def _safe_output_item(item: Any) -> dict[str, Any] | None:
        item_type = getattr(item, "type", None)
        if item_type == "function_call":
            return {
                "type": "function_call",
                "call_id": item.call_id,
                "name": item.name,
                "arguments": item.arguments,
            }
        if hasattr(item, "model_dump"):
            dumped = item.model_dump(
                exclude_none=True,
                exclude={"id", "status"},
            )
            return dumped
        return None

    async def _run(
        self,
        input_items: list[dict[str, Any]],
        effort: str,
        instructions: str,
        max_rounds: int = 3,
    ) -> str:
        if self.client is None:
            return "OpenAI key not configured. Add OPENAI_API_KEY to .env and restart Pit Wall."

        for _ in range(max_rounds):
            request: dict[str, Any] = {
                "model": settings.model,
                "instructions": instructions,
                "input": input_items,
                "tools": self.tools.schemas(),
                "max_output_tokens": 650 if effort in {"medium", "high"} else 420,
            }
            if settings.model.startswith("gpt-5"):
                request["reasoning"] = {"effort": effort}
            response = await self.client.responses.create(**request)
            calls = [
                item
                for item in response.output
                if getattr(item, "type", None) == "function_call"
            ]
            if not calls:
                return response.output_text.strip() or "Stand by."

            for output_item in response.output:
                safe = self._safe_output_item(output_item)
                if safe is not None:
                    input_items.append(safe)
            for call in calls:
                try:
                    arguments = json.loads(call.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                result = await self.tools.call(call.name, arguments)
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(result, ensure_ascii=False),
                    }
                )

        return (
            "Stand by. I could not resolve that cleanly from the available telemetry."
        )

    async def ask(self, utterance: str) -> str:
        await self._capture_feedback(utterance)
        await self.store.append_radio("driver", utterance)
        state = await self.store.snapshot_analysis()
        await self.database.save_radio_message(state, "driver", utterance, "user")
        recent = state["radio_log"][-10:]
        history = "\n".join(
            f"{entry['role'].upper()}: {entry['text']}" for entry in recent[:-1]
        )
        input_items = [
            {
                "role": "user",
                "content": (
                    f"{await self._header()}\n"
                    f"RECENT RADIO:\n{history}\n"
                    f"DRIVER: {utterance}"
                ),
            }
        ]
        effort = (
            settings.deep_reasoning_effort
            if self._is_deep(utterance)
            else settings.reasoning_effort
        )
        text = await self._run(input_items, effort, PERSONA)
        await self.store.append_radio("engineer", text)
        await self.database.save_radio_message(await self.store.snapshot_analysis(), "engineer", text, "response")
        return text

    async def proactive(self, event: dict[str, Any]) -> str:
        state = await self.store.snapshot_analysis()
        recent_engineer = [
            entry["text"]
            for entry in state.get("radio_log", [])[-8:]
            if entry.get("role") == "engineer"
        ]
        input_items = [
            {
                "role": "user",
                "content": (
                    f"{await self._header()}\n"
                    f"PROACTIVE EVENT: {json.dumps(event, ensure_ascii=False)}\n"
                    f"RECENT ENGINEER CALLS: {json.dumps(recent_engineer, ensure_ascii=False)}"
                ),
            }
        ]
        text = await self._run(
            input_items,
            settings.reasoning_effort,
            f"{PERSONA}\n\n{PROACTIVE_PERSONA}",
            max_rounds=2,
        )
        await self.store.append_radio("engineer", text)
        await self.database.save_radio_message(await self.store.snapshot_analysis(), "engineer", text, "proactive")
        return text
