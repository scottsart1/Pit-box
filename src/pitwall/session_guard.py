"""Cancel asynchronous radio work when its telemetry context is replaced."""

from __future__ import annotations

import asyncio
from functools import wraps
from typing import Any


class SessionChangedError(RuntimeError):
    """The request belonged to a session which is no longer on screen."""


def session_key(state: Any) -> tuple[int, int, int, int]:
    get = (
        state.get
        if isinstance(state, dict)
        else lambda key, default: getattr(state, key, default)
    )
    return tuple(
        int(get(key, 0) or 0)
        for key in (
            "session_uid",
            "restart_epoch",
            "timeline_epoch",
            "session_generation",
        )
    )


def session_identity(state: Any) -> str:
    """Exact browser-safe identity, including 64-bit UIDs and reset epochs."""
    return ":".join(str(part) for part in session_key(state))


def session_scoped(*, discarded: Any = None, raise_on_change: bool = True):
    """Race a request against the store's boundary signal, including playback.

    A boundary cancels the whole coroutine tree before it can publish another
    result. Frozen history writes are allowed to finish in their worker thread.
    """

    def decorate(function):
        @wraps(function)
        async def guarded(self, *args, **kwargs):
            signal = self.store.session_changed
            work = asyncio.create_task(function(self, *args, **kwargs))
            changed = asyncio.create_task(signal.wait())
            try:
                await asyncio.wait((work, changed), return_when=asyncio.FIRST_COMPLETED)
                if signal.is_set():
                    if raise_on_change:
                        raise SessionChangedError(
                            "Session changed; the previous radio request was discarded."
                        )
                    return discarded
                return await work
            finally:
                for task in (work, changed):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(work, changed, return_exceptions=True)

        return guarded

    return decorate
