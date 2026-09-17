from pitwall import transfer_progress


def test_progress_throttles_counts_but_not_phase_changes_or_stage_completion(monkeypatch):
    now = [1.0]
    monkeypatch.setattr(transfer_progress.time, "monotonic", lambda: now[0])
    events = []
    report = transfer_progress.TransferProgress(events.append)
    report("packing", 0, 100, "files")
    for count in range(1, 100):
        report("packing", count, 100, "files")
    assert len(events) == 1
    now[0] += .3
    report("packing", 99, 100, "files")
    report("packing", 100, 100, "files")
    report("finalizing")
    assert [e.get("done") for e in events] == [0, 99, 100, None]


def test_observer_errors_cannot_interrupt_commit():
    def failing(event):
        raise ValueError("No display")
    transfer_progress.TransferProgress(failing)("committing")
