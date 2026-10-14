# Observation adapter contract, version 1

This interface accepts already-decoded evidence. No NASCAR 26 UDP, shared-memory
or SDK compatibility is claimed. Authenticate with the local install's
`x-pitbox-key` header. Do not put the token in query strings or logs.

```json
{
  "session_id": "id-returned-by-POST-api-sessions",
  "sequence": 1,
  "source": "adapter",
  "confidence": 1.0,
  "sample": {
    "completed_laps": 14,
    "lap_fraction": 0.25,
    "flag": "green",
    "position": 8,
    "fuel_pct": 71.4,
    "speed_mph": 172,
    "pit_open": true,
    "in_pit": false,
    "last_lap_s": 31.222,
    "laps_down": 0,
    "tires": {"lf": 91, "rf": 79, "lr": 90, "rr": 82},
    "opponents": [{"car": "22", "position": 7, "laps_down": 0,
      "gap_s": 2.1, "last_pit_lap": 0, "fuel_laps": null}]
  }
}
```

Send only fields actually observed. Omission preserves an earlier value until
its own receipt time expires; explicit null clears it. Arrays replace atomically.
Tire corners have individual freshness. Fuel is percent **remaining**, tires are
percent life **remaining**, speed is mph, all times are seconds. `completed_laps`
is zero at the start of lap 1. Do not send the on-screen current lap unchanged.

Quality below 0.85 is rejected. For the built-in OCR observer this is a policy
score assigned only after two consistent readings, not a statistical probability
provided by Windows OCR. Out-of-order and duplicate sequence numbers are ignored.
A regressing lap counter requires a new session. Never reuse an old session ID
after a restart. Resetting an external producer's sequence requires resuming or
starting its session. Monotonically increasing counters should survive reconnects.

Live adapter/OCR evidence expires in eight seconds; manual numeric evidence in five
minutes. Race-control and pit access always expire in eight seconds. A lap advance
without fresh fuel invalidates the previous tank observation. `leader_completed_laps`
is required for a lapped player's finish-distance estimate. A gap in the source
stream breaks fuel-learning continuity. Per-frame
ingestion updates the latest state; only lap boundaries and periodic snapshots
write to disk. The dashboard and proactive radio run on separate clocks.

`POST /api/laps` can explicitly record a completed lap, including `clean`, `flag`,
`fuel_used_pct` and `stint`. CSV import creates a new historical weekend.

No hidden fuel, guaranteed pit intent, collision geometry or restart entitlement
may be filled from assumptions. A third-party adapter is responsible for its
own game's authorization, mapping and real-session verification.
