# Your Pit Box 5.0.0 — GPT-6 and optional Realtime radio

The normal OpenAI route now uses `gpt-6-luna` with low reasoning effort;
the deep strategy route uses `gpt-6-sol` with high effort. Both retain the
Responses API, validated deterministic telemetry tools, token limits and
route deadlines. Explicit older model pins and other providers are preserved.

File transcription defaults to `gpt-transcribe`, with JSON responses and the
plural `languages` hint required by its API. Explicit legacy transcription
models keep their previous request format. Wake-word and silence filters remain.

Settings → Engineer → **Realtime radio** saves a per-device choice and applies
without restarting. It is off by default. Enabling prepares the radio without
opening a connection; wake/PTT starts a conversation. Disabling closes it and
discards queued playback. Session opening and switching are serialized, and the
socket must receive `session.updated` before taking over from standard voice.
Rejection, timeout or cancellation releases the connection. The existing
25-second idle timeout, five-minute ceiling, bounded replies and telemetry tool
allowlist remain. Realtime may increase API usage; an idle connection by itself
is not billed.

## Validation in progress

- Live stored-key Responses probes: Luna/low and Sol/high each called a telemetry
  tool and correctly reported its 1.4-second gap. These are synthetic fixtures,
  not measurements of driving performance. Observed total round-trip times were
  5.17 and 2.69 seconds, respectively; one sample does not establish a speed gain.
- Live `gpt-transcribe` recognized a bounded racing audio clip, including the
  wake name, Norris, hard tyres and lap 18.
- Live Realtime accepted the new input transcription settings. An audio replay
  produced a telemetry tool call and spoken answer using its gap, while declining
  to invent a pit recommendation without strategy evidence. The session closed
  after the check. This verifies the wire and audio path, not a human microphone
  conversation during a race.
- Focused tests cover reasoning continuation across tools, both transcription
  formats, saved mode restoration, missing-key rejection, switching during
  connection, rejected sessions and cancellation cleanup.

Production artifacts, device acceptance, complete suite results and deployment
receipts will be recorded here before release sign-off. No credentials, driver
recordings or private logs are included in this repository.
