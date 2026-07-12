# Pit Wall 3.2 verification record

Verification performed on the packaged source tree:

```text
python -m compileall -q src tests                 PASS
ruff check src tests                              PASS
pytest -q                                         57 passed
node --check extracted-dashboard-script.js        PASS
FastAPI TestClient startup                        PASS
GET /api/health                                   HTTP 200, version 3.2.0
```

## Added regression coverage

- Routine target/corner questions do not silently escalate to deep reasoning.
- Genuine strategy/stint planning does use deep routing.
- Long STT prompt echo and repeated wake-word garbage are discarded.
- Wake enabled/disabled state survives a controller restart.
- The dashboard includes the listening/processing/speaking state rail and timing card.
- Setup track fallback includes Spa ID 10 and Monza ID 11.
- Player FIA blue flag, Safety Car delta and unserved penalties enter hot state.
- A qualifying lap invalidation creates an operational radio event.
- A nearby car behind pitting creates an undercut-threat event.
- Existing PTT tests still prove unrelated throttle/brake/gear/MFD button packets cannot release the configured UDP Action radio.

## Hardware boundary

The container cannot reproduce the user’s PS5, DualSense/wheel, Windows microphone, speakers or live account latency. Streaming audio therefore includes a tested compatibility fallback to the previous WAV path. Final acceptance remains one live Windows shakedown with the dashboard timing card visible.
