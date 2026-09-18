from __future__ import annotations

import pytest

from pitwall.app import _report_trace_recovery
from pitwall.trace_store import RecoveryReport


def test_trace_recovery_reporting_accepts_real_recovery_contract(caplog) -> None:
    report = RecoveryReport(
        invalid_temporary_files=["bad.tmp: checksum mismatch"],
        orphan_chunks=["chunks/orphan.pwt"],
    )

    _report_trace_recovery(report)

    assert "bad.tmp" in caplog.text
    assert "orphan.pwt" in caplog.text


def test_the_dashboard_directory_can_be_pointed_elsewhere(tmp_path, monkeypatch) -> None:
    # An embedded host (the Android app) extracts static/ to its own storage
    # and names it through settings; the desktop defaults stay untouched.
    from pitwall import app as app_module
    from pitwall.config import settings

    assert app_module._static_root_path().name == "static"
    elsewhere = tmp_path / "dashboard"
    elsewhere.mkdir()
    monkeypatch.setattr(settings, "static_dir", elsewhere)
    assert app_module._static_root_path() == elsewhere


@pytest.mark.asyncio
async def test_health_poll_uses_bounded_assembler_metadata(monkeypatch) -> None:
    from pitwall import app as app_module

    def forbidden(*args, **kwargs):
        raise AssertionError("routine health polls must not scan full trace quality")

    monkeypatch.setattr(app_module.session_assembler, "quality_report", forbidden)
    result = await app_module.health()
    assert result["ok"] is True
    assert result["session_assembler"]["group_details_included"] is False
    assert result["session_assembler"]["groups"] == ()
    assert "samples_received" in result["session_assembler"]["counters"]
