"""Curated runtime settings, saved in the database — never written to .env.

config.py's contract is that environment values are installation defaults and
mutable profiles live elsewhere (the network profile store, ptt.json, driver
preferences). This module is the "elsewhere" for general app settings: a
whitelisted subset of Settings fields the dashboard may change, persisted as
one JSON dict under the ``app_settings`` preference key.

Several services (capture, storage, web server) read Settings at module
import, so saved overrides are applied synchronously at import time from the
SQLite file directly — before any service constructs. Fields whose consumers
only construct at startup are flagged ``restart`` and take effect on the
next launch; everything else applies live.

Deliberately absent: wake_enabled (ptt.json owns it — two writers would
fight), network binds (the Connection page's proven-bind store owns those),
LAN access and its token (coupled to a security validator), and credentials.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

OPENAI_VOICES = (
    "alloy", "ash", "ballad", "coral", "echo",
    "fable", "nova", "onyx", "sage", "shimmer", "verse",
)

# name -> spec. type: bool | int | float | str | choice.
SETTINGS_SPEC: dict[str, dict[str, Any]] = {
    "llm_provider": {
        "group": "Engineer", "type": "choice",
        "choices": ("openai", "anthropic", "deepseek", "kimi", "custom", "auto"),
        "label": "AI provider",
        "description": (
            "Which service reasons for the engineer. Needs that provider's "
            "API key (Connection tab). Voice always runs on OpenAI."
        ),
    },
    "llm_fallback_provider": {
        "group": "Engineer", "type": "choice",
        "choices": ("none", "openai", "anthropic", "deepseek", "kimi", "custom"),
        "label": "Fallback provider",
        "description": (
            "Answers when the primary provider fails mid-race. none keeps "
            "failures explicit instead of silently switching."
        ),
    },
    "engineer_name": {
        "group": "Engineer", "type": "str", "min_len": 1, "max_len": 24,
        "label": "Engineer name",
        "description": "What the engineer calls himself on the radio.",
    },
    "radio_verbosity": {
        "group": "Engineer", "type": "choice",
        "choices": ("terse", "standard", "chatty"),
        "label": "Radio verbosity",
        "description": "How much the engineer says when he calls you.",
    },
    "voice": {
        "group": "Engineer", "type": "choice", "choices": OPENAI_VOICES,
        "label": "Engineer voice",
        "description": "Applies from the next radio message.",
    },
    "voice_ack_enabled": {
        "group": "Engineer", "type": "bool",
        "label": "Radio acknowledgement beep",
        "description": "Play a short cue when the engineer hears you.",
    },
    "wake_phrase": {
        "group": "Engineer", "type": "str", "min_len": 2, "max_len": 24,
        "lower": True,
        "label": "Wake phrase",
        "description": "The word that opens the radio hands-free.",
    },
    "wake_aliases": {
        "group": "Engineer", "type": "str", "min_len": 0, "max_len": 200,
        "lower": True,
        "label": "Wake phrase aliases",
        "description": "Comma-separated alternatives the wake word also accepts.",
    },
    "wake_cue_volume": {
        "group": "Engineer", "type": "float", "min": 0.0, "max": 1.0,
        "label": "Wake cue volume",
        "description": "Loudness of the listening cue, 0 to 1.",
    },
    "proactive_enabled": {
        "group": "Proactive engineer", "type": "bool",
        "label": "Proactive calls",
        "description": "Let the engineer call you without being asked.",
    },
    "proactive_cadence_laps": {
        "group": "Proactive engineer", "type": "int", "min": 1, "max": 10,
        "label": "Call cadence (laps)",
        "description": "How many laps between routine check-ins.",
    },
    "proactive_narration_enabled": {
        "group": "Proactive engineer", "type": "bool",
        "label": "Race narration",
        "description": "Narrate incidents and battles, not just strategy.",
    },
    "field_trace_scope": {
        "group": "Telemetry & storage", "type": "choice",
        "choices": ("focused", "all"),
        "label": "Race telemetry scope",
        "description": (
            "focused keeps full traces for you, your teammate, the podium "
            "and your grid neighbours; all keeps every car (more disk)."
        ),
    },
    "capture_mode": {
        "group": "Telemetry & storage", "type": "choice",
        "choices": ("minimal", "balanced", "full_fidelity"),
        "restart": True,
        "label": "Trace detail",
        "description": "Sampling density of your own lap traces.",
    },
    "raw_capture": {
        "group": "Telemetry & storage", "type": "choice",
        "choices": ("off", "rolling", "full"),
        "restart": True,
        "label": "Raw packet capture",
        "description": "Keep replayable raw UDP captures of sessions.",
    },
    "capture_max_gb": {
        "group": "Telemetry & storage", "type": "float", "min": 1.0, "max": 500.0,
        "restart": True,
        "label": "Capture budget (GB)",
        "description": "Disk budget before storage warnings.",
    },
    "retention_days": {
        "group": "Telemetry & storage", "type": "int", "min": 0, "max": 3650,
        "restart": True,
        "label": "Retention window (days)",
        "description": (
            "Sessions older than this appear in the cleanup preview. "
            "Nothing is ever deleted automatically."
        ),
    },
    "web_port": {
        "group": "Application", "type": "int", "min": 1024, "max": 65535,
        "restart": True,
        "label": "Dashboard port",
        "description": "Where this dashboard is served.",
    },
    "open_browser": {
        "group": "Application", "type": "bool", "restart": True,
        "label": "Open browser on launch",
        "description": "Open the dashboard automatically when the app starts.",
    },
}

PREFERENCE_KEY = "app_settings"

# Pre-override values captured when overrides are applied, for provenance.
_baseline: dict[str, Any] = {}


def coerce(name: str, value: Any) -> Any:
    """Validate and coerce one setting value; raises ValueError when bad."""
    spec = SETTINGS_SPEC.get(name)
    if spec is None:
        raise ValueError(f"{name} is not an adjustable setting.")
    kind = spec["type"]
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            return value.lower() == "true"
        raise ValueError(f"{name} must be true or false.")
    if kind == "int":
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a whole number.") from exc
        if not spec["min"] <= number <= spec["max"]:
            raise ValueError(
                f"{name} must be between {spec['min']} and {spec['max']}."
            )
        return number
    if kind == "float":
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a number.") from exc
        if not spec["min"] <= number <= spec["max"]:
            raise ValueError(
                f"{name} must be between {spec['min']} and {spec['max']}."
            )
        return number
    if kind == "choice":
        text = str(value).strip().lower()
        if text not in spec["choices"]:
            raise ValueError(
                f"{name} must be one of: {', '.join(spec['choices'])}."
            )
        return text
    text = str(value).strip()
    if spec.get("lower"):
        text = text.lower()
    if not spec.get("min_len", 0) <= len(text) <= spec["max_len"]:
        raise ValueError(
            f"{name} must be {spec.get('min_len', 0)}-{spec['max_len']} characters."
        )
    return text


def load_saved(db_path: Path | str) -> dict[str, Any]:
    """Read saved overrides straight from SQLite, tolerating absence.

    Runs at module import before the async database wrapper exists, so it
    opens its own read-only connection. A missing file, missing table, or
    unparseable row all mean "no overrides" — first launch must never fail
    on its own settings.
    """
    path = Path(db_path)
    if not path.exists():
        return {}
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT value_json FROM user_preferences WHERE key=?",
                (PREFERENCE_KEY,),
            ).fetchone()
    except sqlite3.Error:
        return {}
    if not row or not row[0]:
        return {}
    try:
        saved = json.loads(row[0])
    except (TypeError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def apply_saved_overrides(settings: Any, db_path: Path | str) -> dict[str, Any]:
    """Apply saved overrides onto the live settings object at import time.

    Returns what was applied. Values that no longer validate (e.g. after an
    upgrade tightens a range) are skipped rather than crashing the launch.
    """
    saved = load_saved(db_path)
    applied: dict[str, Any] = {}
    for name in SETTINGS_SPEC:
        _baseline[name] = getattr(settings, name, None)
    for name, value in saved.items():
        if name not in SETTINGS_SPEC:
            continue
        try:
            coerced = coerce(name, value)
        except ValueError:
            continue
        setattr(settings, name, coerced)
        applied[name] = coerced
    return applied


def _default_for(settings_cls: Any, name: str) -> Any:
    field = settings_cls.model_fields.get(name)
    return None if field is None else field.default


def settings_view(settings: Any, saved: dict[str, Any]) -> list[dict[str, Any]]:
    """What the dashboard renders: value, provenance, and restart state."""
    view: list[dict[str, Any]] = []
    for name, spec in SETTINGS_SPEC.items():
        live = getattr(settings, name, None)
        default = _default_for(type(settings), name)
        if name in saved:
            source = "saved in app"
        elif _baseline and _baseline.get(name) != default:
            source = ".env"
        else:
            source = "default"
        entry = {
            "name": name,
            "group": spec["group"],
            "label": spec["label"],
            "description": spec["description"],
            "type": spec["type"],
            "value": live,
            "default": default,
            "source": source,
            "restart_required": bool(spec.get("restart")),
            "pending_restart": bool(
                spec.get("restart") and name in saved and saved[name] != live
            ),
        }
        if spec["type"] == "choice":
            entry["choices"] = list(spec["choices"])
        if spec["type"] in {"int", "float"}:
            entry["min"], entry["max"] = spec["min"], spec["max"]
        view.append(entry)
    return view


def apply_runtime(settings: Any, name: str, value: Any) -> str:
    """Apply one validated setting to the running app.

    Returns "applied" or "restart_required". Restart-flagged fields are NOT
    set live: their consumers already constructed from the old value, and a
    half-applied setting that looks applied is worse than an honest badge.
    """
    if SETTINGS_SPEC[name].get("restart"):
        return "restart_required"
    setattr(settings, name, value)
    return "applied"
