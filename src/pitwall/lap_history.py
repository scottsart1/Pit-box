"""Authoritative completed-lap timing from the game's session-history packet."""
from __future__ import annotations

from typing import Any


def authoritative_timing(row: dict[str, Any]) -> dict[str, Any]:
    """Accept complete history timing, allowing the game's millisecond rounding."""
    lap_num = int(row.get("lap_num", 0) or 0)
    lap_ms = int(row.get("lap_ms", 0) or 0)
    sectors = [int(row.get(key, 0) or 0) for key in ("s1_ms", "s2_ms", "s3_ms")]
    if lap_num <= 0 or lap_ms <= 0 or min(sectors) <= 0 or abs(sum(sectors) - lap_ms) > 5:
        return {}
    flags = int(row.get("valid_flags", 0) or 0)
    return {"lap_time_ms": lap_ms, **dict(zip(("s1_ms", "s2_ms", "s3_ms"), sectors)),
            "valid_flags": flags, "valid": bool(flags & 1), "timing_source": "session_history"}
