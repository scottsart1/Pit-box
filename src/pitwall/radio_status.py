"""Bounded radio lookups over the same current state used by the dashboard.

These grammars match whole requests. Advice, hypothetical plans and compound
questions must still reach the reasoning path, never a keyword-only pit call.
"""
from __future__ import annotations

import math
import re
from typing import Any, Callable

from .identity import match_drivers


def words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def strategy_overview_request(text: str) -> bool:
    subject = r"(?:(?:our|my|the) )?(?:(?:current|overall|full|complete|whole|entire) )?(?:race strategy|strategy|race plan|pit strategy|pit plan|tyre plan|tire plan)"
    return bool(re.fullmatch(
        rf"(?:please )?(?:"
        rf"(?:any |what is |what s )?{subject}(?: updates?| status)?|"
        rf"what does {subject} look like|how (?:is|s) {subject} looking|"
        rf"(?:(?:can|could|would) you (?:please )?)?(?:give me|tell me|read out|summarize) (?:a (?:rundown|summary|update) of )?{subject}|"
        rf"(?:walk|talk) me through {subject}|"
        rf"(?:can|could) you (?:please )?run down (?:of )?{subject}"
        rf")(?: please)?", words(text)
    ))


def strategy_overview(state: dict[str, Any], instruction: Callable[..., str]) -> str:
    strategy = state.get("strategy") or {}
    plan = strategy.get("recommended") or {}
    if state.get("mode_profile") in {"practice", "qualifying", "time_trial"}:
        return "This is not a race session; there is no live race pit plan to read."
    if not plan:
        return "The current race plan is still building; no confirmed pit schedule yet."
    epoch = plan.get("session_epoch")
    if epoch is not None and list(epoch) != [int(state.get(k, 0) or 0) for k in ("session_uid", "restart_epoch", "timeline_epoch")]:
        return "The plan is updating for this session; the previous pit schedule is no longer current."
    tyre = state.get("tyre") or {}
    if plan.get("source_compound") and tyre.get("compound") and plan["source_compound"] != tyre["compound"]:
        return "The plan is updating for the tyres now fitted; the previous pit schedule is no longer current."
    if any(plan.get(key) is False for key in ("legal", "feasible", "inventory_feasible")):
        return "The displayed plan is provisional and has not passed its race or tyre checks; no confirmed pit schedule yet."
    stale = not state.get("connected")
    prefix = "Last confirmed plan while paused: " if stale and state.get("game_paused") else "Telemetry stale; last confirmed plan: " if stale else ""
    lap = int(state.get("current_lap", 0) or 0)
    hold = state.get("strategy_hold") or {}
    if hold.get("active") and int(hold.get("until_lap", 0) or 0) >= lap:
        return prefix + f"Your pit call is on hold until lap {int(hold['until_lap'])}; the plan will be reviewed then."
    override = plan.get("driver_override") or {}
    if (state.get("strategy_override") or {}).get("enabled"):
        if not override.get("honored"):
            return prefix + "Your selected plan is not yet confirmed by the current strategy; review the pit schedule before committing."
        prefix += "Your selected plan: "
    confidence = str(strategy.get("confidence") or "low").lower()
    qualifier = "; provisional, low confidence" if confidence == "low" else f"; {confidence} confidence"
    stops = plan.get("stops_remaining")
    if stops is None:
        stops = 1 if plan.get("box_lap") is not None else 0
    stops = int(stops)
    call = instruction(plan, lap, tyre)
    # Preserve the existing guards for a pointless stop or an already-fitted tyre.
    if stops > 0 and call.lower().startswith("stay out"):
        return prefix + call.rstrip(".") + qualifier + "."
    if stops == 0:
        return prefix + "No more stops planned; stay out to the finish" + qualifier + "."
    laps = list(plan.get("box_laps") or [plan.get("box_lap")])
    compounds = list(plan.get("compounds") or [])
    fits = compounds[1:] if len(compounds) == stops + 1 else [plan.get("fit_compound")]
    if len(laps) != stops or len(fits) != stops or any(x is None for x in laps) or not all(fits):
        return prefix + "The full pit schedule is still updating; no complete plan confirmed yet."
    entries = []
    for i, (box_lap, compound) in enumerate(zip(laps, fits)):
        when = "this lap" if int(box_lap) <= lap and not stale else f"lap {int(box_lap)}"
        entries.append(f"{'box' if i == 0 else 'then'} {when} for {str(compound).lower()}s")
    answer = prefix + f"{stops} stop{'s' if stops != 1 else ''} remaining: " + ", ".join(entries) + ", then to the finish" + qualifier + "."
    reason = str(plan.get("stop_required_reason") or "").lower()
    if "mandatory" in reason:
        answer += " The compound change is required for race legality."
    elif "cannot reach" in reason:
        answer += " The current tyres are not projected to reach the finish."
    return answer


def closing_target(text: str, state: dict[str, Any]) -> str | None:
    """Explicit relative/named questions and an unambiguous immediate follow-up."""
    text = words(text)
    if re.fullmatch(r"(?:please )?(?:at what (?:pace|rate)|how (?:fast|quickly)|how much(?: per lap)?)(?: please)?", text):
        recent = [entry for entry in state.get("radio_log", []) if entry.get("role") == "engineer"]
        prior = words(str(recent[-1].get("text", ""))) if recent else ""
        if not re.search(r"\b(?:closing|catching|gaining|pulling away)\b", prior):
            return None
        rivals = match_drivers(state.get("drivers", []), prior)
        if len(rivals) == 1:
            return str(rivals[0]["name"])
        if not rivals and "car behind" in prior:
            return "behind"
        return None
    target = r"(?P<target>(?:the )?(?:car|driver|guy) (?:behind|ahead|in front)|[a-z0-9]+(?: [a-z0-9]+)?)"
    match = re.fullmatch(
        rf"(?:please )?(?:"
        rf"(?:at what (?:pace|rate)|how (?:fast|quickly)|how much(?: per lap)?) (?:is|are) {target} (?:closing|catching|gaining)(?: in|(?: on)? (?:me|us))?(?: per lap)?|"
        rf"(?:is|are) (?P<who>(?:the )?(?:car|driver|guy) behind) (?:closing|catching|gaining)(?: in| on (?:me|us))?|"
        rf"(?:what is|what s) the (?:closing rate|pace) of (?P<pace>(?:the )?(?:car|driver|guy) behind)"
        rf")(?: please)?", text
    )
    if not match:
        return None
    reference = next(value for value in match.groupdict().values() if value)
    if reference in {"he", "she", "they"}:
        return closing_target("at what pace", state)
    if re.fullmatch(r"(?:the )?(?:car|driver|guy) (behind|ahead|in front)", reference):
        return "behind" if reference.endswith("behind") else "ahead"
    rivals = match_drivers(state.get("drivers", []), reference)
    return str(rivals[0]["name"]) if len(rivals) == 1 else None


def gap_evidence(state: dict[str, Any], rival: dict[str, Any]) -> dict[str, Any]:
    """Short measured trend and last-lap comparison; never infer one from the other."""
    result: dict[str, Any] = {"gap_change_s": None, "window_s": None, "pace_delta_s": None}
    player = next((d for d in state.get("drivers", []) if d.get("car_idx") == state.get("player_car_index")), {})
    own = player.get("last_lap_ms") or state.get("last_lap_ms") or 0
    other = rival.get("last_lap_ms") or 0
    if own > 0 and other > 0:
        result["pace_delta_s"] = (own - other) / 1000.0
    history = rival.get("gap_history") or []
    if len(history) < 2 or rival.get("pit_lane_timer_active") or rival.get("pit_status"):
        return result
    latest = history[-1]
    now = state.get("session_time_s")
    latest_time = float(latest.get("session_time_s", 0))
    if now is not None and not 0 <= float(now) - latest_time <= 8:
        return result
    # Use a recent continuous window. Crossing the player or time rewinding
    # invalidates the old side's samples, rather than producing a huge rate.
    current_gap = rival.get("gap_to_player_s")
    if current_gap is None:
        return result
    anchor = None
    for sample in reversed(history[:-1]):
        elapsed = latest_time - float(sample.get("session_time_s", 0))
        gap = sample.get("gap_s")
        if gap is None or not math.isfinite(float(gap)) or float(gap) * float(current_gap) <= 0 or elapsed <= 0 or elapsed > 20:
            break
        anchor = sample
        if elapsed >= 8:
            break
    if anchor is not None:
        window = latest_time - float(anchor["session_time_s"])
        change = abs(float(latest["gap_s"])) - abs(float(anchor["gap_s"]))
        if window >= 3 and math.isfinite(change) and abs(change) <= 5 and float(latest["gap_s"]) * float(current_gap) > 0:
            result.update(gap_change_s=round(change, 2), window_s=round(window, 1))
    return result


def closing_report(report: dict[str, Any]) -> str:
    if not report.get("available") or report.get("telemetry_stale"):
        return "No reliable current gap to that car; I cannot confirm its closing rate yet."
    driver = report.get("driver", "That car")
    behind = report.get("relative") == "behind"
    prefix = "Last confirmed while paused: " if report.get("paused") else ""
    answer = f"{prefix}{driver} is {float(report['gap_s']):.1f} seconds {'behind' if behind else 'ahead'}"
    change, window = report.get("gap_change_s"), report.get("window_s")
    if change is not None and window:
        subject = "they" if behind else "we"
        if change < -0.15:
            answer += f"; {subject} gained {abs(change):.1f} seconds over the last {window:.0f} seconds"
        elif change > 0.15:
            answer += f"; the gap grew {change:.1f} seconds over the last {window:.0f} seconds"
        else:
            answer += "; the measured gap is steady"
    else:
        answer += "; no reliable measured closing rate yet"
    pace = report.get("pace_delta_s")
    if pace is not None:
        answer += f"; {'they were' if pace > 0 else 'you were'} {abs(pace):.1f} seconds faster last lap" if abs(pace) >= 0.05 else "; last-lap pace was equal"
    return answer + "."
