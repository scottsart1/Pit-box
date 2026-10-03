"""Freeze spoken lap references at the start of a radio question."""

from __future__ import annotations

from contextvars import ContextVar, copy_context
from functools import wraps

lap_note_origin: ContextVar[dict | None] = ContextVar("lap_note_origin", default=None)


def lap_note_context(function):
    @wraps(function)
    async def scoped(self, *args, **kwargs):
        state = lap_note_origin.get()
        if state is None:
            state = await self.store.peek(
                "session_uid", "restart_epoch", "timeline_epoch", "session_generation", "current_lap"
            )
        token = lap_note_origin.set(state)
        try:
            return await function(self, *args, **kwargs)
        finally:
            lap_note_origin.reset(token)

    return scoped


def lap_note_task_context(origin: dict | None):
    context = copy_context()
    context.run(lap_note_origin.set, origin)
    return context
