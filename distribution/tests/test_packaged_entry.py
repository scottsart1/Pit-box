"""The callbacks `distribution/main.py` hands to `launch()`.

The launcher tests drive `launch()` with recorders; this file checks the
real implementations the packaged build wires in, without opening a window.
The 4.9.0 installer crashed on every first launch because the prompt wrapper
took no arguments while `launch()` passes one, and nothing here caught it.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

DIST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIST.parent))

from distribution import first_run, launcher  # noqa: E402
from distribution import main as packaged  # noqa: E402


@pytest.fixture
def stubbed_screen(monkeypatch):
    """Replace the Tk dialogs and the bootloader splash with recorders."""
    calls: dict[str, list] = {"splash_closed": [], "prompts": [], "messages": []}

    def fake_prompt(message: str = "", free: bool = True) -> None:
        # Returning None is "the window was closed", which needs no input.
        calls["prompts"].append(message)

    def fake_message(message: str, *, title: str = "") -> None:
        calls["messages"].append(message)

    # `pitwall.main` pulls in the whole server; the wrapper only needs the
    # splash closer, so a stand-in module keeps the test cheap and offline.
    fake_pitwall_main = types.SimpleNamespace(
        close_startup_splash=lambda: calls["splash_closed"].append(True)
    )
    monkeypatch.setitem(sys.modules, "pitwall.main", fake_pitwall_main)
    monkeypatch.setattr(first_run, "prompt_first_run", fake_prompt)
    monkeypatch.setattr(first_run, "show_message", fake_message)
    return calls


def test_the_first_run_prompt_accepts_the_message_launch_passes(stubbed_screen):
    result = packaged._prompt_first_run("")
    assert result is None
    assert stubbed_screen["prompts"] == [""]
    assert stubbed_screen["splash_closed"] == [True]


def test_a_retry_message_reaches_the_form(stubbed_screen):
    packaged._prompt_first_run("That code was not recognized.")
    assert stubbed_screen["prompts"] == ["That code was not recognized."]


def test_the_error_dialog_closes_the_splash_and_forwards_the_text(stubbed_screen):
    packaged._show_error("damaged install")
    assert stubbed_screen["messages"] == ["damaged install"]
    assert stubbed_screen["splash_closed"] == [True]


def test_a_fresh_free_install_reaches_the_welcome_screen_and_starts(tmp_path, stubbed_screen):
    """The full first launch with the real callbacks: welcome, then run."""
    started: list[bool] = []
    result = launcher.launch(
        tmp_path,
        prompt=packaged._prompt_first_run,
        show_error=packaged._show_error,
        start_app=lambda: started.append(True),
        save_api_key=None,
        free=True,
    )
    assert result.outcome is launcher.LaunchOutcome.RUN
    assert stubbed_screen["prompts"] == [""]
    assert started == [True]
    assert launcher.welcome_shown(tmp_path)


def test_the_paid_form_gets_the_retry_reason(tmp_path, stubbed_screen):
    # Closing the form cancels; the point is that the call itself is valid.
    result = launcher.launch(
        tmp_path,
        prompt=packaged._prompt_first_run,
        show_error=packaged._show_error,
        start_app=lambda: None,
        free=False,
    )
    assert result.outcome is launcher.LaunchOutcome.CANCELLED
    assert stubbed_screen["prompts"] == [""]
