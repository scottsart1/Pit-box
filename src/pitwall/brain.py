from __future__ import annotations

import asyncio
import json
import re
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
For attack/defence advice, use gap trend, rival tyres/laps, racing-line/corner evidence, energy
and the current 2026 overtaking aid. Give a concrete preparation point and overtaking/defending
phase, or say evidence is absent. In 2026-regulation sessions call the overtaking aid Manual
Override, not DRS. Treat blue flags, invalid qualifying laps, unserved drive-through/stop-go
penalties, and a rival pitting from behind as high-priority operational facts.

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
In qualifying, replace race gaps and race strategy with pole/session-best time, the driver's best,
theoretical best, target time, required delta, clear-air/run status, and one lap-building opportunity.
""".strip()

# High reasoning is reserved for actual planning. Routine pace/corner/target questions
# stay on the low-latency path instead of being escalated merely because they contain
# words such as "why", "corner", or "best lap".
_DEEP_TERMS = (
    "strategy",
    "undercut",
    "overcut",
    "what if",
    "compare stint",
    "compare strategy",
    "make it to the end",
    "make these tyres last",
    "pit now",
    "stay out",
    "safety car pit",
    "vsc pit",
    "red flag strategy",
    "setup",
    "race plan",
    "tyre plan",
    "tire plan",
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
        overtaking_label = (
            "manual override"
            if state.get("regulations_2026")
            else "DRS"
        )
        return (
            f"SESSION {state['session_type']} | {state['track_name']} | "
            f"LAP {state['current_lap']}/{state['total_laps']} | "
            f"P{state['player_position']} | CONNECTED {state['connected']} "
            f"PAUSED {state['game_paused']} | "
            f"{state['tyre']['compound']} age {state['tyre']['age_laps']} "
            f"wear FL/FR/RL/RR {state['tyre']['wear']} | "
            f"inner temps FL/FR/RL/RR {state['tyre']['inner_temps_c']} | "
            f"fuel delta {state['fuel_laps_delta']:+.1f} laps | "
            f"ERS {state['ers_pct']:.0f}% | {overtaking_label} "
            f"{'active' if state.get('overtake_active') else 'inactive'} | "
            f"weather {state['weather']} rain15 {state['rain_next_15_pct']}% | "
            f"race control {state.get('race_control_phase', 'green')} | "
            f"FIA flag {state.get('fia_flag', 'none')} | "
            f"penalties {state.get('penalties_s', 0)}s, drive-through "
            f"{state.get('unserved_drive_through_penalties', 0)}, stop-go "
            f"{state.get('unserved_stop_go_penalties', 0)} | "
            f"compound rule {state.get('strategy', {}).get('compound_rule', {})} | "
            f"target {target.get('target')} | strategy {strategy_text} | "
            f"opportunity {corner_text}"
        )

    @staticmethod
    def classify_request(utterance: str) -> str:
        """Return fast, normal, or deep for acknowledgement/routing."""
        text = " ".join(utterance.lower().split())
        if any(term in text for term in _DEEP_TERMS) or len(text.split()) >= 32:
            return "deep"

        fast_subjects = (
            "gap ahead",
            "gap behind",
            "what position",
            "my position",
            "what tyres",
            "what tires",
            "tyre condition",
            "tire condition",
            "fuel",
            "battery",
            "ers",
            "manual override",
            "overtake available",
            "last lap",
            "last two laps",
            "last three laps",
            "best lap",
            "target lap",
            "weather",
            "rain",
            "damage",
            "warnings",
            "penalty",
        )
        planning_words = ("should", "why", "how do", "approach", "attack", "defend")
        if any(subject in text for subject in fast_subjects) and not any(
            word in text for word in planning_words
        ):
            return "fast"
        return "normal"

    @staticmethod
    def _format_ms(ms: int) -> str | None:
        if not ms:
            return None
        minutes, remainder = divmod(int(ms), 60_000)
        seconds, millis = divmod(remainder, 1000)
        return f"{minutes}:{seconds:02d}.{millis:03d}"

    @staticmethod
    def _requested_lap_count(text: str) -> int:
        lowered = text.lower()
        if "three" in lowered or re.search(r"\b3\b", lowered):
            return 3
        if "two" in lowered or re.search(r"\b2\b", lowered):
            return 2
        if "five" in lowered or re.search(r"\b5\b", lowered):
            return 5
        return 1

    async def _fast_answer(self, utterance: str) -> str | None:
        """Answer simple telemetry questions without an LLM/tool round trip."""
        text = " ".join(utterance.lower().split())
        state = await self.store.snapshot_analysis()
        if not state.get("connected") and not state.get("game_paused"):
            return None

        if state.get("mode_profile") == "qualifying" and (
            "best lap" in text or "target lap" in text or "pole" in text
        ):
            summary = self.tools.qualifying_summary(state, 3)
            field = summary.get("field", [])
            leaders = ", ".join(
                f"{item.get('name')} {item.get('best_lap')}"
                for item in field
                if item.get("best_lap")
            )
            target = summary.get("target") or "still building"
            player = summary.get("player_best") or "no valid lap yet"
            return f"Best times: {leaders or 'not available yet'}. Your best {player}; target {target}."

        if "gap ahead" in text or "car ahead" in text:
            result = await self.tools.get_gap("ahead")
            if result.get("available"):
                return (
                    f"{result['driver']} ahead, {float(result['gap_s']):.1f} seconds. "
                    f"Last lap {result.get('last_lap') or 'unavailable'}."
                )
            return "No reliable gap to the car ahead right now."

        if "gap behind" in text or "car behind" in text:
            result = await self.tools.get_gap("behind")
            if result.get("available"):
                return (
                    f"{result['driver']} behind, {float(result['gap_s']):.1f} seconds. "
                    f"Last lap {result.get('last_lap') or 'unavailable'}."
                )
            return "No reliable gap to the car behind right now."

        if "position" in text:
            return f"You are P{int(state.get('player_position', 0))}."

        if any(term in text for term in ("what tyres", "what tires", "tyre condition", "tire condition")):
            tyre = state.get("tyre", {})
            wear = [float(value) for value in tyre.get("wear", [0, 0, 0, 0])]
            temps = [float(value) for value in tyre.get("inner_temps_c", [0, 0, 0, 0])]
            limiting = max(range(len(wear)), key=wear.__getitem__) if wear else 0
            labels = ["front-left", "front-right", "rear-left", "rear-right"]
            temp_text = (
                f" Front cores {temps[0]:.0f}/{temps[1]:.0f} C."
                if len(temps) >= 2
                else ""
            )
            return (
                f"{tyre.get('compound', 'Unknown').title()}s, {int(tyre.get('age_laps', 0))} laps old. "
                f"Limiting tyre is {labels[limiting]} at {wear[limiting]:.0f} percent.{temp_text}"
            )

        if "fuel" in text:
            delta = float(state.get("fuel_laps_delta", 0.0))
            direction = "plus" if delta >= 0 else "minus"
            return f"Fuel is {direction} {abs(delta):.1f} laps."

        if any(term in text for term in ("battery", "ers", "manual override", "overtake available")):
            percent = float(state.get("ers_pct", 0.0))
            if state.get("regulations_2026"):
                status = "active" if state.get("overtake_active") else (
                    "available" if state.get("overtake_available") else "not available"
                )
                return f"Battery {percent:.0f} percent; Manual Override is {status}."
            return f"ERS {percent:.0f} percent; DRS is {'available' if state.get('drs_allowed') else 'not available'}."

        if "last lap" in text or "last two laps" in text or "last three laps" in text:
            count = self._requested_lap_count(text)
            history = await self.tools.get_driver_lap_history("me", count)
            laps = [item.get("lap") for item in history.get("laps", []) if item.get("lap")]
            if laps:
                return f"Your last {len(laps)} lap{'s' if len(laps) != 1 else ''}: " + ", ".join(laps) + "."
            last = self._format_ms(int(state.get("last_lap_ms", 0)))
            return f"Last lap {last}." if last else "No completed valid lap is available yet."

        if "target lap" in text:
            target = state.get("analysis", {}).get("target", {})
            value = target.get("target") or target.get("target_lap")
            return f"Target lap is {value}." if value else "Target lap is still building."

        if "weather" in text or "rain" in text:
            return (
                f"{state.get('weather', 'Unknown')}; rain risk "
                f"{int(state.get('rain_next_15_pct', 0))} percent in 15 minutes."
            )

        if "damage" in text:
            damage = state.get("damage", {})
            wing = max(
                int(damage.get("front_left_wing", 0)),
                int(damage.get("front_right_wing", 0)),
            )
            floor = int(damage.get("floor", 0))
            return f"Front-wing damage {wing} percent; floor damage {floor} percent."

        if "warning" in text or "penalty" in text:
            warnings = int(state.get("corner_cutting_warnings", 0))
            penalties = int(state.get("penalties_s", 0))
            return f"Track-limit warnings {warnings}; time penalties {penalties} seconds."

        return None

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
            return item.model_dump(exclude_none=True, exclude={"id", "status"})
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
            if effort in {"high", "xhigh", "max"}:
                token_budget = 2000
            elif effort == "medium":
                token_budget = 1100
            else:
                token_budget = 520
            request: dict[str, Any] = {
                "model": settings.model,
                "instructions": instructions,
                "input": input_items,
                "tools": self.tools.schemas(),
                "max_output_tokens": token_budget,
                "parallel_tool_calls": True,
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

            async def execute(call: Any) -> tuple[Any, dict[str, Any]]:
                try:
                    arguments = json.loads(call.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                result = await self.tools.call(call.name, arguments)
                return call, result

            results = await asyncio.gather(*(execute(call) for call in calls))
            for call, result in results:
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(result, ensure_ascii=False),
                    }
                )

        return "Stand by. I could not resolve that cleanly from the available telemetry."

    async def ask(self, utterance: str) -> str:
        await self._capture_feedback(utterance)
        await self.store.append_radio("driver", utterance)
        state = await self.store.snapshot_analysis()
        await self.database.save_radio_message(state, "driver", utterance, "user")

        route = self.classify_request(utterance)
        if route == "fast":
            direct = await self._fast_answer(utterance)
            if direct:
                await self.store.append_radio("engineer", direct)
                await self.database.save_radio_message(
                    await self.store.snapshot_analysis(),
                    "engineer",
                    direct,
                    "fast_response",
                )
                return direct

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
            if route == "deep"
            else settings.reasoning_effort
        )
        text = await self._run(input_items, effort, PERSONA)
        await self.store.append_radio("engineer", text)
        await self.database.save_radio_message(
            await self.store.snapshot_analysis(), "engineer", text, "response"
        )
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
        await self.database.save_radio_message(
            await self.store.snapshot_analysis(), "engineer", text, "proactive"
        )
        return text
