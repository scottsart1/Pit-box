# Pit Wall 3.3 implementation notes

## Provider-neutral engineer brain

Version 3.3 adds `ProviderRouter`, `DeepSeekChatProvider`, and
`OpenAIResponsesProvider`. F1 prompts and deterministic tools remain in
`EngineerBrain`; providers only translate the same compact request and tool
schemas to their API format. Request-scoped tool memoization prevents duplicate
execution during failover. DeepSeek thinking-mode tool turns preserve
`reasoning_content` internally as required by the provider.


## Reliability boundaries

- `F1DatagramProtocol` enqueues packets into one ordered bounded queue.
- `StateStore.snapshot_live()` is deliberately lightweight.
- `NativeVoiceController` latches a received UDP Action press and ignores unrelated button events.
- Production release mode is microphone post-speech silence plus a hard cap.
- `ProactiveEngineer` uses a due-lap threshold and delivery deadline.
- SQLite writes for session events use their own bounded queue.

## Strategy objective

The deterministic layer creates legal feasible plans and returns transparent stint models. It uses per-wheel wear, degradation, setup effects, set life, traffic, pit loss and race-control state. A seeded Monte Carlo layer adds bounded uncertainty and ranks by a configurable quantile. The language model explains and interrogates these outputs; it is not allowed to replace them with invented numbers.

## Historical race regression

`data/strategy_benchmarks_2026.json` contains public-race-inspired invariants. The tests do not encode an exact winner's stop laps as universal truth. They validate mechanisms: neutralisation discount, late-SC track position, red-flag tyre change, legality and high-degradation multi-stop behavior.

## Racing line

World X/Z is aligned by lap distance to the stored PB. Signed cross-track distance is calculated from the local PB tangent. Persistent zones combine path, speed, brake and throttle differences. No claim is made that the PB line is globally optimal.

## Radio latency

- Bounded capture remains the stable STT path in 3.2.
- A cached acknowledgement runs concurrently with answer generation.
- Raw PCM TTS is played as chunks arrive, with a WAV compatibility fallback.
- Simple live facts bypass the reasoning/tool loop.
- Independent function calls are awaited concurrently.
- The dashboard exposes cumulative stage timestamps and the server-driven radio rail.
