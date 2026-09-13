# Capture rotation: observed gap and bounded handoff

At baseline `35b33c6`, a deterministic temporary-file probe offered three
packets before a normalized session change, 202 while catalog registration
was paused with an asyncio event, and three afterward. It accepted and
persisted only **6 of 208**. The 202 intervening submissions returned false,
while capture queue drops, write errors and rotation drops all remained zero.
The otherwise identical no-rotation control persisted all 208 packets.

The reproduced report is
`/workspace/scratch/f60f47f45c6d/capture-rotation-evidence-crxd_ycv/report.json`.
The independent probe is
`/workspace/scratch/f60f47f45c6d/capture-rotation-coverage-probe.py` (its original
source-path and baseline assertions must be adjusted to evaluate the fixed
candidate). This isolates recording availability from UDP transport loss:
packets are submitted directly, with deterministic barriers and no sleeps.

Session observation now inserts a boundary into the capture FIFO before
subsequent admitted packets. The single writer drains earlier packets, closes
the old file and opens the new one in order. Packets buffer in the existing
bounded queue while that happens. Catalog scanning/registration consumes the
completed old file independently and no longer stops current recording.
PWCAP bytes, original timestamps/source metadata and file format are unchanged.

When the packet queue is full, incoming packets are rejected and counted;
older boundary commands cannot be evicted. When a boundary is waiting for
space, newer packets are also rejected and counted until that boundary is
enqueued. Coordinator request capacity remains bounded. An excess session
change pauses admission until the newest identity can be inserted, so an
overflow cannot silently relabel another session's packets. Rotation-overflow
and packet-drop counters report those separate events. This is bounded
buffering, not an unbounded-lossless or overload-retention guarantee.

Six deterministic regressions in `tests/test_capture_rotation_handoff.py`
verify the 202-packet catalog pause, a paused writer-open followed by shutdown,
rapid UID/epoch-labelled boundaries, packet-capacity overflow, rotation-capacity
overflow and direct service finalization after a queued boundary. All six pass;
the combined capture lifecycle/service/format/replay suites pass **36 tests**.

Ownership is exact relative to `observe_session()`. The existing receiver
captures raw datagrams before asynchronous normalized-session observation;
this fix does not move that normalization into the receive callback or prove
that every raw UID transition is classified before capture. Packets arriving
before the callback can therefore remain in the preceding or initially
unclassified archive. Real UDP delivery loss, capture queue overflow and
pre-observation attribution must be measured separately. User data is not
used: these tests create temporary synthetic archives.
