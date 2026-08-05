from __future__ import annotations

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
