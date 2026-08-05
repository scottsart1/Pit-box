# Pit Wall 4.2 implementation baseline

This file records the verified starting point for the 4.2 implementation. It is
intentionally about the repository as found, rather than the version implied by
the enclosing folder name.

## Repository state

- Feature branch: `feature/full-field-analysis-ui`
- Baseline commit: `0a7eb4b`
- Application version reported by the source: `3.8.0`
- Existing user-owned working-tree changes, excluded from Pit Wall commits:
  `.claude/settings.local.json` and `.claude/launch.json`
- Full baseline test run on 2026-08-05: `355 passed in 426.13s`

The project is therefore being evolved directly from the working 3.8.0 code.
There is no hidden 4.1 architecture to assume.

## Contracts that remain supported

- `F1DatagramProtocol` construction and packet-handler behavior.
- `StateStore` snapshots, mutation methods, queues, live delta, and lap
  transition behavior.
- Existing SQLite repository methods and the signed-SQLite/unsigned-game
  session UID conversion.
- Existing deterministic strategy, analysis, racing-line, setup, briefing,
  proactive-radio, voice, and Realtime behavior.
- The current telemetry-tool names and result meanings.
- Legacy `/api/*`, `/ws`, `/overlay`, and export contracts while `/api/v1` is
  introduced additively.
- Legacy `laps.trace_json` reads as a fallback until external trace manifests
  have verified parity.

## Reconciled differences from the 4.2 plan

- The current UI is a single dependency-free `static/index.html`, plus
  `overlay.html`; it is not already componentized.
- The database has no migration version table and its existing `sessions` and
  `laps` primary keys cannot represent restart/timeline epochs. New catalogs
  must be additive before any destructive schema change.
- UDP parsing currently occurs in the datagram callback and original bytes are
  discarded. Capture, forwarding, and health hooks must be added without
  changing existing parsed-packet ordering.
- Full-field state exists in memory, but historical high-rate traces are
  player-only and availability/provenance is not generalised.
- Current analysis is player/PB-centric and uses heuristic corner windows; a
  distance-aligned, versioned telemetry package is new work.
- No binary replay fixture is checked in. Initial replay tests will construct
  deterministic framed captures and continue using the real 2026 packet
  structures already exercised by the suite.

## Implementation order

1. Versioned migrations and backups; configuration/network foundations.
2. Packet health, byte-identical forwarding, raw capture, and replay.
3. Additive session/car/lap catalogs and external trace manifests.
4. Deterministic alignment, track/segment/comparison/coaching/field services.
5. Versioned APIs and bounded WebSocket projections.
6. Incremental responsive UI shell, Connection Center, Library, Field Lab,
   Session Review, and Lap Lab.
7. Full regression, replay, migration, API, accessibility, and visual checks.

