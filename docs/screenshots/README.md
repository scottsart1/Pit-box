# Screenshots

Captured from a running Pit Wall 3.7.0 driven by `tools/replay_demo.py`, which
generates real F1 26 UDP packets (format 2026) for a 20-car race at Suzuka. No
PS5 and no wheel involved — every value shown was produced by the application's
own parsing, analysis and strategy code from synthetic telemetry.

To reproduce:

```bash
python -m uvicorn pitwall.app:app --host 127.0.0.1 --port 8000
python tools/replay_demo.py --laps 60 --speed 9
```

| File | Surface | What it shows |
|---|---|---|
| `01-live-dashboard.png` | Live dashboard | Full 20-car timing tower with real gaps, the player marked once, a retired car shown as `— RETIRED`, ranked strategy plans, corner coaching with measured deltas, and a delivered proactive call |
| `02-review-history.png` | Review & History | Persisted laps with sector-backed times and compounds, plus stored strategy snapshots |
| `03-setup-lab.png` | Setup Lab | Setup generator and the persistent five-axis driver profile |
| `04-overlay.png` | OBS / second screen | Position, live delta to personal best, per-wheel wear, fuel margin, current call and the last radio message |

Notes on what is visible in `01`:

- **Timing tower** — the player (RUSSELL) is identified by car index, not by a
  gap of approximately zero. Before 3.7.0 every car within 10 ms of the player
  was labelled `YOU`, which at lights-out meant the entire grid.
- **`— RETIRED`** — retired cars report position 0. They are kept in the
  snapshot so the engineer can explain the race, sorted last rather than shown
  as "P0".
- **Proactive radio** — `Car behind has stopped. Push for up to two clean laps…`
  is an `undercut_threat` call, raised from the rival's pit-lane timer.
- **Strategy** — the ranked alternatives, projected wear and risk-adjusted times
  come from the deterministic Monte Carlo engine, not from a language model.
