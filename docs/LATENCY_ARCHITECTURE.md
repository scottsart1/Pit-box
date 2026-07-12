# Pit Wall 3.2 latency architecture

## Production path

```text
speech / L3
  -> local bounded capture
  -> yellow listening rail
  -> 0.60 s wake silence boundary (1.15 s for the existing L3 mode)
  -> green processing rail
  -> OpenAI transcription
  -> fast / normal / deep route
  -> cached acknowledgement in parallel with answer work
  -> deterministic answer or Responses tool loop
  -> streaming 24 kHz PCM TTS
  -> blue speaking rail on first PCM chunk
```

## Routing

**Fast**: gap, position, tyres, fuel, ERS/Manual Override, recent own laps, target, weather, damage, warnings.

**Normal**: coaching, general progress, racecraft discussion which is not a multi-plan decision.

**Deep**: strategy, pit/hold decisions, undercut/overcut, SC/VSC/red flag planning, setup, stint comparisons, make-it-to-the-end questions.

The route is visible on the dashboard.

## Timings

Every accepted call records elapsed milliseconds from capture finalization:

- `transcript_ms`
- `model_ms` (answer ready, including any deterministic/tool work)
- `first_audio_ms`
- `complete_ms`

These are cumulative timestamps, not isolated stage durations. Stage duration can be derived by subtraction.

## Deliberately deferred

### Incremental Realtime STT

Not enabled by default in 3.2. It requires a persistent WebSocket, partial text correction, wake-prefix decisions on changing transcripts, reconnect/backpressure behavior and real headset/game-audio evaluation.

### Sentence-level model-to-speech streaming

Not enabled in this patch because responses may contain tool calls before final text. Speaking a provisional sentence before a strategy tool completes could create contradictory race instructions. The safe fast path plus streamed final TTS is used instead.

### `previous_response_id`

The current explicit input-item tool loop is retained. It has deterministic local context and avoids coupling a live race session to server-side response-chain availability. Parallel independent tools provide the safe latency gain.
