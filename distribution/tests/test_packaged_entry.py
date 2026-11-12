"""The callbacks `distribution/main.py` hands to `launch()`.

The launcher tests drive `launch()` with recorders; this file checks the
real implementations the packaged build wires in, without opening a window.
The shipped 4.8.1 installer crashed on every first launch because the prompt
wrapper took no arguments while `launch()` passes one, and nothing caught it.
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

    # The wrappers close the splash through pitwall.desktop, which loads no
    # settings: they run before a driver profile is chosen.
    import pitwall.desktop

    monkeypatch.setattr(pitwall.desktop, "close_splash", lambda: calls["splash_closed"].append(True))
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


def test_the_welcome_key_waits_for_the_open_profile(monkeypatch, stubbed_screen):
    """Saving at the welcome screen would load settings before a profile is chosen."""
    import pitwall.credentials

    saved: list[str] = []
    monkeypatch.setattr(packaged, "_pending_api_key", [])
    monkeypatch.setattr(pitwall.credentials, "save_key", saved.append)
    packaged._save_api_key("sk-test")
    assert saved == []
    packaged._store_pending_api_key()
    assert saved == ["sk-test"]
    packaged._store_pending_api_key()
    assert saved == ["sk-test"]


def test_a_welcome_key_that_cannot_be_saved_is_reported_not_fatal(monkeypatch, stubbed_screen):
    import pitwall.credentials

    def refuse(key: str) -> None:
        raise OSError("read-only folder")

    monkeypatch.setattr(packaged, "_pending_api_key", ["sk-test"])
    monkeypatch.setattr(pitwall.credentials, "save_key", refuse)
    packaged._store_pending_api_key()
    assert "read-only folder" in stubbed_screen["messages"][0]


def test_the_packaged_app_starts_through_the_driver_profile_screen(monkeypatch, tmp_path):
    import pitwall.desktop

    calls: list[tuple] = []
    monkeypatch.setenv("PITWALL_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(pitwall.desktop, "run", lambda root, before_run=None: calls.append((root, before_run)))
    packaged._start_app()
    assert calls == [(tmp_path, packaged._store_pending_api_key)]
