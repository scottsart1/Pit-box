"""The free edition: welcome once, never gate, still refuse a damaged install.

Runs entirely offline and needs no signing key, unlike the paid-flow tests in
test_launcher.py — a free launch never talks to the activation server.
"""

from __future__ import annotations

import sys
from pathlib import Path

DIST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIST.parent))

from distribution import edition, launcher  # noqa: E402
from distribution.licensing import gate  # noqa: E402


class _Recorder:
    def __init__(self, entries=None) -> None:
        self.entries = list(entries or [])
        self.prompts: list[str] = []
        self.errors: list[str] = []
        self.started = 0
        self.saved_keys: list[str] = []

    def prompt(self, message: str):
        self.prompts.append(message)
        return self.entries.pop(0) if self.entries else None

    def show_error(self, message: str) -> None:
        self.errors.append(message)

    def start_app(self) -> None:
        self.started += 1

    def save_api_key(self, key: str) -> None:
        self.saved_keys.append(key)


def _launch(recorder: _Recorder, config_dir: Path, *, free=True) -> launcher.LaunchResult:
    return launcher.launch(
        config_dir,
        endpoint="https://activation.test/activate",
        prompt=recorder.prompt,
        show_error=recorder.show_error,
        start_app=recorder.start_app,
        save_api_key=recorder.save_api_key,
        free=free,
    )


def test_the_shipped_build_is_the_free_edition():
    # The website offers the installer without a code; a gated build behind
    # that page would strand every downloader on an activation screen.
    assert edition.FREE_EDITION is True


def test_first_launch_welcomes_saves_the_key_and_starts(tmp_path):
    recorder = _Recorder([launcher.FirstRunInput(activation_code="", api_key=" sk-test-key-1234567890 ")])
    result = _launch(recorder, tmp_path)
    assert result.outcome is launcher.LaunchOutcome.RUN
    assert recorder.prompts == [""]
    assert recorder.saved_keys == ["sk-test-key-1234567890"]
    assert recorder.started == 1
    assert launcher.welcome_shown(tmp_path)


def test_the_welcome_screen_is_shown_only_once(tmp_path):
    first = _Recorder([None])
    _launch(first, tmp_path)
    second = _Recorder()
    result = _launch(second, tmp_path)
    assert result.outcome is launcher.LaunchOutcome.RUN
    assert second.prompts == []
    assert second.started == 1


def test_closing_the_welcome_window_skips_the_key_but_still_starts(tmp_path):
    recorder = _Recorder([None])
    result = _launch(recorder, tmp_path)
    assert result.outcome is launcher.LaunchOutcome.RUN
    assert recorder.started == 1
    assert recorder.saved_keys == []
    assert launcher.welcome_shown(tmp_path)


def test_a_blank_key_is_not_saved(tmp_path):
    recorder = _Recorder([launcher.FirstRunInput(activation_code="", api_key="   ")])
    _launch(recorder, tmp_path)
    assert recorder.saved_keys == []
    assert recorder.started == 1


def test_a_failed_key_save_is_reported_and_the_app_still_starts(tmp_path):
    class Failing(_Recorder):
        def save_api_key(self, key: str) -> None:
            raise OSError("disk full")

    recorder = Failing([launcher.FirstRunInput(activation_code="", api_key="sk-test")])
    result = _launch(recorder, tmp_path)
    assert result.outcome is launcher.LaunchOutcome.RUN
    assert recorder.started == 1
    assert recorder.errors and "Connection tab" in recorder.errors[0]
    # The marker was written before the save was attempted, so the next
    # launch does not re-open the welcome screen.
    assert launcher.welcome_shown(tmp_path)


def test_a_damaged_install_is_still_refused_without_deleting_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "integrity_ok", lambda: False)
    keep = tmp_path / "PitWallData.txt"
    keep.write_text("sessions", encoding="utf-8")
    recorder = _Recorder()
    result = _launch(recorder, tmp_path)
    assert result.outcome is launcher.LaunchOutcome.BLOCKED
    assert recorder.started == 0
    assert recorder.errors == [gate.TAMPER_MESSAGE]
    assert keep.read_text(encoding="utf-8") == "sessions"


def test_the_free_flow_never_calls_the_activation_server(tmp_path, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("the free edition must not activate")

    monkeypatch.setattr(gate, "activate", explode)
    monkeypatch.setattr(gate, "complete_activation", explode)
    recorder = _Recorder([launcher.FirstRunInput(activation_code="PITW-AAAAA-AAAAA-AAAAA", api_key="")])
    assert _launch(recorder, tmp_path).outcome is launcher.LaunchOutcome.RUN


def test_the_paid_flow_is_still_there_behind_the_switch(tmp_path):
    # Flipping FREE_EDITION off must bring back the gate unchanged: with no
    # licence the paid flow asks for a code, and closing the form exits.
    recorder = _Recorder([None])
    result = _launch(recorder, tmp_path, free=False)
    assert result.outcome is launcher.LaunchOutcome.CANCELLED
    assert recorder.prompts == [""]
    assert recorder.started == 0
