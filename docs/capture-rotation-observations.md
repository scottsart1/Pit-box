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

Nine deterministic regressions in `tests/test_capture_rotation_handoff.py`
verify the 202-packet catalog pause, a paused writer-open followed by shutdown,
rapid UID/epoch-labelled boundaries, packet-capacity overflow, rotation-capacity
overflow and direct service finalization after a queued boundary. Three
follow-up checks cover a zero-timeout stop during writer rotation with another
boundary blocked on a full queue, normal stop with three pending boundaries,
and a synthetic writer-open failure. The timeout and open-failure regressions
failed before the follow-up: completion futures remained unresolved and open
failure left `write_errors` at zero. Admission tasks are now tracked and
settled before shutdown removes queue entries, every outstanding boundary
receives a completion or failure, and rotation I/O failures increment the
error counter. The combined capture lifecycle/service/format/replay suites
pass **39 tests**.

The drain timeout bounds waiting for queued work. Finalization still requires
an in-flight OS file operation to return; Python cannot cancel disk I/O already
running in a thread. Cancelled rotation closes await their disk completion,
and a writer created during cancellation is closed instead of leaking its
temporary-file handle. This does not promise a hard filesystem deadline.

Ownership is exact relative to `observe_session()`. The existing receiver
captures raw datagrams before asynchronous normalized-session observation;
this fix does not move that normalization into the receive callback or prove
that every raw UID transition is classified before capture. Packets arriving
before the callback can therefore remain in the preceding or initially
unclassified archive. Real UDP delivery loss, capture queue overflow and
pre-observation attribution must be measured separately. User data is not
used: these tests create temporary synthetic archives.
