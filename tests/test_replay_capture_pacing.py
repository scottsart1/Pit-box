from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import replay_capture


@pytest.mark.parametrize("start_offset,expected_times", [(0, [30, 31, 32]), (1, [30, 31])])
def test_initial_capture_scan_does_not_become_a_catch_up_burst(monkeypatch, start_offset, expected_times):
    clock = [0.0]
    sent = []

    def slow_reader(_path):
        clock[0] += 30  # Full-file checksum/format validation precedes playback.
        for second in range(3):
            yield SimpleNamespace(monotonic_ns=second * 1_000_000_000, data=bytes([second]))

    class Socket:
        def sendto(self, data, target):
            sent.append((clock[0], data, target))

        def close(self):
            pass

    monkeypatch.setattr(replay_capture, "CaptureReader", slow_reader)
    monkeypatch.setattr(replay_capture.socket, "socket", lambda *_: Socket())
    monkeypatch.setattr(replay_capture.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(replay_capture.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    count = replay_capture.replay(Path("recording.pwcap"), "127.0.0.1", 20777, 1,
                                  start_offset_s=start_offset, progress_every=0)
    assert count == len(expected_times)
    assert [row[0] for row in sent] == expected_times
    assert [row[1] for row in sent] == [bytes([second]) for second in range(start_offset, 3)]
    assert all(row[2] == ("127.0.0.1", 20777) for row in sent)
