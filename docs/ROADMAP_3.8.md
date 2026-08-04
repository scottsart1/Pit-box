# Pit Wall 3.8 — implementation specification

**Audience:** an engineer (or coding model) implementing this against the 3.7.0 tree.
**Baseline:** commit `c45bc3e` on `main`, 348 tests passing.
**Source of requirements:** the Texas race of 2026-08-03 (28 laps, finished P10), its stored
telemetry, the 28 driver radio questions, and the strategy snapshots for laps 19–24.

This document is deliberately concrete. Every requirement names the file and function it belongs
in, states the deterministic rule, and gives an acceptance test. Nothing here needs invention about
what the game exposes — the packet fields cited are all already parsed and in `SessionState`.

---

## 0. Context the implementer must hold

### 0.1 The one architectural invariant

**Deterministic code computes every number. The language model only chooses words.**

This is not a style preference. It is the reason the engineer can be trusted mid-race, and it is
enforced in three places:

- `TelemetryTools` (`src/pitwall/tools.py`) returns computed conclusions, not raw fields.
- `EngineerBrain.PERSONA` forbids inventing a number and requires tool-sourced facts.
- `ProactiveEngineer._narrate()` passes a fully-computed payload and falls back to a deterministic
  template on any model failure.

Every feature below follows the same shape: **compute the judgement in Python, hand the model a
payload, let it speak.** If a feature cannot be computed deterministically, it must report its own
uncertainty rather than guess.

### 0.2 Where things live

| File | Responsibility |
|---|---|
| `src/pitwall/udp.py` | Packet decode → `SessionState`. All 24 cars are captured. |
| `src/pitwall/state.py` | `SessionState`, `DriverState`, `StateStore` (async lock, snapshots) |
| `src/pitwall/strategy.py` | Monte Carlo stint/stop model, `compute()`, `_stabilize_radio_plan()` |
| `src/pitwall/analysis.py` | Per-lap corner segmentation, degradation, fuel, target lap |
| `src/pitwall/tools.py` | The ~51 tools the model may call. **Add new capability here.** |
| `src/pitwall/brain.py` | `PERSONA`, fast-path routing, `_defers_to_model()` |
| `src/pitwall/proactive.py` | Unsolicited radio: detection, priority queue, delivery |
| `src/pitwall/realtime.py` | Speech-to-speech session (optional, `RADIO_TOOLS` allow-list) |
| `src/pitwall/database.py` | SQLite persistence |
| `static/index.html` | Dashboard. Single file, vanilla JS, WebSocket at `/ws` |
| `static/overlay.html` | Second-screen/OBS overlay |
| `tools/replay_demo.py` | Synthetic 20-car race over real UDP. **Use this to test everything.** |

### 0.3 How to develop and verify

```bash
# terminal 1 — app on a scratch database so real history is untouched
PITWALL_DATA_DIR=/tmp/pw PITWALL_NATIVE_VOICE=false python -m uvicorn pitwall.app:app --port 8000

# terminal 2 — synthetic race
python tools/replay_demo.py --laps 44 --speed 9

# tests
python -m pytest -q
```

`replay_demo.py` emits all nine packet types through the real `f1-packets` resolver. Extend it
where a feature needs a scenario it does not yet produce (a safety car, a wet crossover, a
qualifying session) rather than testing against hand-built dicts only.

### 0.4 Adding a tool — the pattern

1. Write `async def get_x(...)` on `TelemetryTools`, returning a dict of **conclusions plus the
   evidence behind them**, and an `interpretation` string stating sign conventions.
2. Register it in `TelemetryTools.schemas()` with a description that tells the model *when* to use
   it, not just what it returns.
3. If it should be reachable by voice, add the name to `RADIO_TOOLS` in `realtime.py`.
4. If the fast path would swallow the question, extend `EngineerBrain._defers_to_model()`.
5. Add a test asserting the *judgement*, not just the field's presence.

### 0.5 Two known defects to fix while in these files

Neither blocks the work below, both were confirmed by review and are cheap:

- `PitWallDatabase.maintain()` holds `self._lock` across the `VACUUM`, serialising every database
  operation on first start against a large file. Do the `VACUUM` outside the lock.
- `NativeVoiceController._consume_audio_block()` schedules one unreferenced `asyncio.Task` per
  30 ms audio block. Replace with a single consumer draining an `asyncio.Queue`.

---

## 1. The dashboard should answer the question before it is asked

### 1.1 The requirement, derived from real data

In the Texas race the driver asked 28 questions in 28 laps. Classified by what would have removed
the need to ask:

| What was asked | Times | Removed by |
|---|---|---|
| "how am I doing vs *rival*", "is Verstappen closing in on me" | 3 | **Rival compare panel** (1.2) |
| "what's my front wing damage", "what's my tire wear right now" | 2 | **Car condition panel** (1.3) |
| "how have my last three laps been", "average degradation over last five laps" | 2 | **Pace & degradation strip** (1.4) |
| "what should be my target lap time for points" (×2), "anything I could do to improve position" (×2) | 4 | **Objective panel** (1.5) |
| "what is Max's fastest sector" | 1 | **Rival compare panel** (1.2) |
| "give me a race update", "check" (×2) | 3 | **Race flow panel** (1.6) |
| Arguing about the pit call | 6 | **Strategy rationale panel** (1.7 + §4) |

**Design principle: the dashboard is glanceable, the radio is conversational.** A driver at 300 km/h
gets ~0.5 s of glance. Every panel must lead with one large value and a colour state. Detail is
secondary text, readable only on a straight or in the pits.

### 1.2 Rival compare panel (new)

**Backing tool:** `TelemetryTools.get_pace_verdict(driver)` — already implemented in 3.7.0.
Returns `verdict`, `gap_change_s_per_lap`, `laps_to_contact`, `sector_deltas_s`, `losing_most_in`,
`tyre_context`.

**New work:** the tool exists but nothing on the dashboard consumes it.

- Add `GET /api/compare?driver=ahead|behind|<name>` in `app.py` returning `get_pace_verdict`.
- Panel shows, for **car ahead** and **car behind** simultaneously:
  - Driver name, gap, and a **closing-rate arrow** (▲ closing / ▬ steady / ▼ dropping) coloured
    green/amber/red by whether it favours the player.
  - `laps_to_contact` as a large number when under 10, else blank.
  - A three-cell sector strip S1/S2/S3, each cell coloured by `sector_deltas_s` sign, showing the
    delta in hundredths. This alone answers "where am I losing time" and "what is Max's fastest
    sector" without a radio call.
  - `tyre_context` as one line of secondary text ("his are 4 laps fresher on the same compound").
- Poll on the existing `/ws` state push; compute client-side from `state.drivers` where possible to
  avoid a second request per tick, and call `/api/compare` only when the panel is expanded.

**Acceptance:** with `replay_demo.py` running, the panel shows a non-null closing rate within 3 laps
of green flag, and the sector strip matches `get_pace_verdict`'s `sector_deltas_s` exactly.

### 1.3 Car condition panel (extend existing)

The existing CAR panel shows compound, age, per-wheel wear and temperature. Add:

- **Damage row**, always visible, one line: only components with damage > 0, as
  `FW 6% · FLOOR 5% · GBX 12%`, with front wing rendered in a distinct colour because it is the
  only thing a pit stop can change (see §4.5 in the 3.7.0 persona rules).
- **Wear projection**: alongside current max wear, show `→ 66% @ flag` from
  `strategy.recommended.projected_finish_wear_pct`. This is the single number that makes the
  stay-out-vs-box argument legible.
- **Cliff marker**: if `analysis.deg_model.projected_cliff_lap` is set and within 5 laps, show
  `CLIFF ~L23` in amber.

### 1.4 Pace & degradation strip (new)

A compact sparkline row above the lap counter:

- Last 8 valid lap times as a sparkline, with the target lap as a horizontal reference line.
- **Degradation number**: seconds per lap, from `analysis.deg_model.current_slope_s_per_lap`,
  displayed as `DEG +0.20 s/lap` and coloured by magnitude. The driver explicitly asked for this
  ("average degradation, which is time lost per lap, over the last five laps").
- **Stint clock**: laps on the current set, and laps remaining in the projected stint.

### 1.5 Objective panel (new) — "what am I actually racing for"

**Backing tool:** `TelemetryTools.get_position_target(target_position)` — implemented in 3.7.0,
currently only reachable by voice.

- Add `GET /api/objective?target=<n>` in `app.py`.
- Panel shows: current position, target position (default 10, user-settable via a small stepper),
  `positions_needed`, `time_to_find_s`, `required_gain_s_per_lap`, `required_lap_time`, and
  `feasible_on_pace` rendered as a coloured verdict chip
  (`ON FOR IT` / `MARGINAL` / `NEEDS HELP`).
- List `cars_in_the_way` with gap and tyre age, so the driver can see which one is the real
  obstacle.

This panel alone removes four of the 28 questions.

### 1.6 Race flow panel (new)

**Backing tool:** `TelemetryTools.get_race_flow()` — implemented in 3.7.0, not on the dashboard.

Shows: cars yet to stop, cars currently in the pit lane, retirements, biggest movers, and cars
within undercut range. This is the "give me a race update" answer, standing.

### 1.7 Strategy rationale panel (extend existing)

The strategy card currently shows the call and ranked alternatives. It must also show **why**, in a
form the driver can argue with:

- `net_gain_vs_stay_out_s` — the time the stop is worth.
- `projected_rejoin_position` — **already computed and currently invisible on the dashboard.**
- **New:** `projected_finish_position` and `positions_lost_by_stopping` (see §4).
- A one-line rationale and the condition that would change the call.

### 1.8 Implementation notes for `static/index.html`

The file is a single vanilla-JS page with one `render(state)` function driven by `/ws`. Keep that
shape — do not introduce a framework. Practical guidance:

- Add panels as siblings in the existing grid; the CSS uses `.card`, `.label`, `.hero`, `.small`,
  `.muted` — reuse them.
- Everything that can be derived from `state` should be, to avoid extra requests at 4 Hz.
- Tabs are deep-linkable via `location.hash` (`#live`, `#review`, `#setup`) as of 3.7.0 — use the
  same `selectPage()` mechanism if adding a tab.
- The overlay (`static/overlay.html`) paints from `/api/state` on load then follows `/ws`. Mirror
  only the two or three highest-value numbers there; it is a glance surface, not a dashboard.

---

## 2. Session briefings

### 2.1 The problem

Telemetry does not flow until the session is live, so there is no natural moment for the engineer to
set up the session — and no debrief at the end. The driver asked for exactly this: a pre-race brief
triggered by a button, a post-quali-lap comparison, and a post-race summary.

### 2.2 New module: `src/pitwall/briefing.py`

Create a `BriefingEngine` with one method per briefing type. Each returns a **deterministic payload**;
`EngineerBrain` narrates it with a briefing-specific persona. Persist each briefing to a new
`briefings` table (`session_uid`, `kind`, `payload_json`, `text`, `created_at`) so it can be
re-read on the Review tab.

### 2.3 Pre-session briefs (button-triggered, no telemetry required)

Add `POST /api/briefing/pre?mode=race|qualifying|practice`. The dashboard shows a **"Session brief"**
button that is enabled whenever a track is known — from the last Session packet, or from a manual
track picker when nothing has arrived yet.

Sources available **before** the lights, all already in the codebase:

- `database.track_review(track_id)` — your historical laps and recurring weak corners here.
- `database.tyre_history_model(track_id)` — your measured wear and degradation per compound.
- `database.progress_trend(track_id)` — whether you are getting faster at this circuit.
- `database.sector_bests(track_id)` — personal best sectors and theoretical best.
- `data/strategy_benchmarks_2026.json` + `strategy.TRACK_TYRE_SEVERITY` / `PIT_LOSS_SECONDS`.
- `setup_advisor.generate(profile)` — a baseline setup with rationale.

**Race brief payload** must contain: expected stop count and the two leading strategies with their
projected times; pit loss at this circuit; the two-compound requirement; your historical degradation
per compound with sample size and confidence; the three corners you historically lose most time in;
your realistic target lap and the grid slot's clean/dirty side; weather forecast if a Session packet
has arrived; and **three explicit session goals** (e.g. "clear lap 1", "hold delta under +0.3 to the
leader on used mediums", "keep track limits clean").

**Qualifying brief** must additionally contain: run-plan advice — when to go out and when not, from
`lobby`/field activity if available, otherwise from track evolution heuristics; how many timed laps
the tyre set supports; the delta required to reach a target grid slot from `sector_bests`.

**Practice brief** must contain: what to actually measure — a long run on the compound with least
personal data (`tyre_history_model` sample sizes make this deterministic), the corners to work on,
and a consistency target from `analysis.get_consistency()`.

### 2.4 Post-lap qualifying debrief — the highest-value item

**Trigger:** on `PacketLapData` transition into `pit_status != 0` (in-lap) during a qualifying mode
profile, or on `driver_status == 2` (in lap). Fire once per timed lap.

**Payload** — everything needed already exists per car:

- Your lap and sector times vs **your best**, vs the **session best**, and vs your **teammate**
  (`DriverState.is_teammate` is populated).
- Sector-by-sector deltas to both, using `get_rival_sector_comparison` matched on lap number.
- **Where they were deploying:** `DriverState.ers_deployed_lap_j` / `ers_harvested_lap_j` and
  `overtake_active` are captured for all 24 cars as of 3.7.0. Report deploy energy per lap and
  whether the reference driver used Manual Override on their best lap.
- **Where you lost it on track:** `analysis.corner_metrics` for the lap — brake point, minimum
  speed, throttle-on distance, `loss_vs_pb_s` — plus `racing_line` deviation zones.
- Speed trap: `DriverState.speed_trap_kph` for both, which separates a power/drag deficit from a
  cornering deficit.
- A verdict: the single biggest loss, with metres and tenths, and one instruction for the next run.

**Delivery:** speak it automatically on the in-lap (it is a safe moment — the driver is slow), and
render it on the dashboard as a persistent card.

### 2.5 Post-race debrief

**Trigger:** the existing `on_final_classification` hook in `udp.py`, which already fires exactly
once per session as of 3.7.0.

`database.session_debrief(session_uid)` already computes pace, consistency, compounds used and the
top corner losses. Extend the payload with: start vs finish position and net places, the lap-by-lap
position chart (`DriverState.position_history`), every stop with its stationary time
(`pit_stop_timer_ms`), the strategy actually run vs the strategy recommended, the biggest single
gain and loss of the race, and points scored.

Add a **"Race report"** card on the Review tab, and speak a two-sentence version on the slow-down
lap.

### 2.6 Briefing personas

Add to `brain.py` next to `PROACTIVE_PERSONA`:

- `PRE_SESSION_PERSONA` — calm, structured, allowed to be longer than radio (5–8 sentences); must
  state goals explicitly and name the confidence of every projection.
- `DEBRIEF_PERSONA` — analytical, blunt about where time went, exactly one actionable instruction
  at the end.

Both must inherit the base `PERSONA` numeric discipline via `compose_persona()`.

---

## 3. Pre-emptive engineer questions and driver feedback

### 3.1 The requirement

The engineer should ask before it needs to decide — "how are the tyres feeling?" two laps before the
pit window — and fold the answer into the tyre model. It must **not** blindly obey: if the
deterministic plan is clearly better, it says so and explains.

### 3.2 New unsolicited call type: `driver_check`

In `proactive.py`:

- Add `"driver_check"` to `EVENT_PRIORITY` at `IMPORTANT`, and to the coalesced set.
- Detection in `ProactiveEngineer._detect()`: fire when `strategy.recommended.box_lap` is 2–3 laps
  ahead, at most once per stint, only under green flag, and only when the driver is not already in
  conversation. Payload carries the current wear, degradation slope, projected finish wear, and the
  two candidate plans.
- Wording: a **closed, answerable** question — "Two laps to the window. How are the rears — holding,
  or going away?" Never open-ended.
- This is the one place the engineer is allowed to ask a question; `PROACTIVE_PERSONA` currently
  forbids it, so add an explicit exception for this event type only.

### 3.3 Capturing the answer

Extend `EngineerBrain._FEEDBACK_PATTERNS` (which already maps phrases to categories such as
`understeer`, `traction`, `no_grip`) with tyre-state categories:

| Category | Phrases | Model effect |
|---|---|---|
| `tyres_gone` | "they're gone", "no grip left", "falling off a cliff" | wear rate ×1.25, deg slope ×1.3 |
| `tyres_going` | "starting to go", "dropping off", "losing the rears" | ×1.10 / ×1.15 |
| `tyres_fine` | "still good", "plenty left", "happy" | ×0.92 / ×0.90 |

Store as a new `driver_tyre_feedback` entry in `SessionState`, with `lap`, `category`, `confidence`
and a decay: feedback older than 5 laps loses weight linearly, because a tyre report from 8 laps ago
is not evidence about now.

### 3.4 Applying it in the strategy model

In `strategy.py`, `_wear_rate()` and `_deg_for()` currently blend track defaults with the personal
regression from `database.tyre_history_model()`. Add driver feedback as a **third, bounded** input:

- Multiply the blended rate by the feedback factor, **clamped to ±25%**. The driver's perception is
  evidence, not ground truth; it must never be able to invert a call on its own.
- Record the applied factor in the plan as `driver_feedback_factor` and `driver_feedback_lap`, so
  the rationale can say "brought forward one lap on your report that the rears are going".
- If feedback and measured wear **disagree** — the driver says gone, telemetry shows 40% — surface
  that explicitly rather than silently averaging: `feedback_conflict: true`, and have the engineer
  say so. That disagreement is itself useful information (often it means temperature, not wear).

### 3.5 Not blindly obeying

Add to `PERSONA`: when driver feedback and the deterministic model disagree by more than the clamp,
state the model's position once, in one sentence, with the number that drives it — then follow the
driver's decision if they repeat it. The 3.7.0 negation and standing-instruction work already
ensures a repeated refusal is honoured; this adds the *reasoned* single push-back before it.

---

## 4. Strategy: optimise for finishing position, not elapsed time

### 4.1 The defect, from the Texas race

At lap 21, P10, on used mediums, 7 laps to go, the engineer said:

> Box lap 21 for softs. You'll rejoin around P19, but this is **10.8 seconds quicker
> risk-adjusted** than staying out.

The driver's reply is the correct engineering critique:

> "and what world does it really faster if I lose track position? Does that really help me achieve
> anything?"

He was right. **Finishing 10.8 seconds sooner in P18 is worth nothing; finishing P10 is worth a
point.** The engine ranks plans by `risk_adjusted_time_s` — a pure elapsed-time objective — while
`projected_rejoin_position` sits in the payload, computed and unused by the ranking. It re-issued
the same call at laps 21, 22 and 23.

### 4.2 The change: a position-aware objective

In `strategy.py`, `compute()` builds plans and sorts by `risk_adjusted_time_s`. Introduce a
**projected finishing position** for each plan and rank on that first.

**Deterministic method** — no new data required, everything below is already in `SessionState`:

1. For each rival with a classified position, project their race time to the flag using their
   current pace (`DriverState.lap_history` median of last 5 valid), their tyre age and compound
   degradation (reuse `_deg_for`), and their likely remaining stops (`predict_rival_strategy`
   already estimates this).
2. Project the player's race time under each candidate plan (this is `projected_time_s`, already
   computed).
3. Sort the projected finishing times → **`projected_finish_position`** for that plan.
4. Compute `positions_gained_vs_stay_out`.

**Ranking rule:**

```
primary   : projected_finish_position           (lower is better)
secondary : championship points for that position (points_for_position() already exists)
tertiary  : risk_adjusted_time_s                (the current metric, as a tie-break)
```

A plan that finishes 8 places lower can never outrank one that holds position, however much elapsed
time it saves.

### 4.3 Overtaking difficulty — why rejoining P19 is worse than it looks

The projection above still assumes you can pass the cars you rejoin behind. That assumption is
track-dependent and must be explicit.

Add `TRACK_OVERTAKING_DIFFICULTY: dict[int, float]` in `strategy.py` alongside the existing
`PIT_LOSS_SECONDS` and `TRACK_TYRE_SEVERITY` tables — a 0.0–1.0 scale where Monaco ≈ 0.95 and Monza
≈ 0.25. Use it to compute, per plan:

- `expected_positions_recovered` = f(pace advantage on fresh tyres, laps remaining, difficulty).
- With **7 laps left and a 0.9 difficulty**, recovering 9 places is impossible; the model must say
  so rather than implying the elapsed-time gain is achievable.

Where a value is not known for a circuit, default to 0.6 and mark the plan's confidence lower
rather than omitting the factor.

### 4.4 Rational risk — let the driver choose the gamble

Add to each plan:

- `outcome_distribution`: from the existing Monte Carlo samples, the probability of finishing in
  each position band. The engine already runs `strategy_monte_carlo_samples` (default 320)
  simulations — currently collapsed to a P75 time. Keep the distribution instead.
- `points_expected` — the mean of `points_for_position()` over that distribution.
- `downside_p10` and `upside_p90` positions.

Then a stay-out call can be expressed the way a real pit wall expresses it:

> "Staying out: 60% chance of holding P10, 25% we drop to P12, 15% the tyres go and we lose five.
> Boxing: near-certain P16 but 3 seconds a lap quicker at the end. I'd stay out."

Add a `strategy_risk_appetite` setting (`conservative | balanced | aggressive`, default `balanced`)
that selects whether ranking uses `points_expected` (balanced), the P90 downside (conservative), or
the P10 upside (aggressive). Expose it on the dashboard next to the existing driver-control panel,
and let the driver set it by voice through the existing `_preference_updates()` path.

### 4.5 Defending as a modelled option

When staying out on older tyres, the engineer should quantify the defence rather than ignore it:

- `defence_laps_sustainable` — from your degradation slope and the pursuer's closing rate, how many
  laps until they are within striking range.
- Use `TRACK_OVERTAKING_DIFFICULTY` and DRS/active-aero zone count (`state.drs_zones`,
  `state.active_aero_zones`, both captured in 3.7.0) to estimate whether they can actually complete
  the pass.
- The driver's final question of the race was "rate me out of 10 in terms of defending against Max
  with older tires" — a defence-quality score is a legitimate deterministic output: sector time
  retention under pressure, minimum-speed deltas in the DRS-entry corner, and positions held.

### 4.6 Stop re-issuing a refused call

At laps 21, 22 and 23 the engineer repeated the same box call after explicit refusals, because
`_strategy_signature` changed each lap (`box_lap` 21 → 22 → 23) and re-triggered `strategy_change`.

Fix in `brain.py` and `proactive.py`:

- A refusal of a pit call ("not boxing", "not in the mood for a box", "staying out") must set a
  **`strategy_hold`** on `SessionState`: `{until_lap, reason, set_at}`, holding for a default of 5
  laps or until the race state materially changes (safety car, red flag, weather crossover, damage,
  or wear crossing a hard safety limit).
- While a hold is active, suppress `strategy_change` calls entirely and mark the dashboard strategy
  card "driver hold — automatic calls paused".
- When the hold expires or a material change occurs, the engineer may raise it **once**, and must
  say what changed.

Note `_strategy_override_action()` correctly returns `None` on negation as of 3.7.0 — that stops a
refusal being turned *into* a pit call, but nothing yet registers the refusal as a hold. That is the
gap.

---

## 5. Delivery reliability — fix before adding more calls

The Texas race shows the queue is still losing high-value calls:

| Event | Queued | Spoken |
|---|---:|---:|
| `rival_pace` | 27 | **1** |
| `energy_low` | 4 | **0** |
| `progress_update` | 13 | 3 |
| `damage` | 7 | 4 |

Adding briefings and driver-check questions on top of a queue that delivers 4% of rival-pace calls
will make things worse, not better. Before §2 and §3 ship:

- Instrument `_safe_to_speak()` to record *why* a call was refused (unsafe, interval, expired,
  superseded) into `proactive_calls`, so the loss is measurable rather than inferred.
- `rival_pace` at 27 queued/1 spoken is almost certainly coalescing plus expiry: the same rival
  re-triggers every 8 s, each new event resetting the queue entry, and the 35 s expiry then kills it.
  Make `rival_pace` update an existing queued event in place rather than replacing it, so its
  original deadline survives.
- `energy_low` at 0 spoken suggests the safe-to-speak window never opens while attacking — which is
  exactly when the call matters. Allow `IMPORTANT` energy calls at higher throttle thresholds.

---

## 6. Suggested sequencing

Each phase is independently shippable and testable.

| Phase | Content | Why this order |
|---|---|---|
| **A** | §5 delivery instrumentation + fixes; the two §0.5 defects | Everything else adds calls to this queue |
| **B** | §1 dashboard panels (all six) | Pure front-end over tools that already exist; removes ~20 of 28 questions immediately |
| **C** | §4.1–4.3 position-aware strategy objective | The single biggest correctness win; fixes a wrong call, not a missing feature |
| **D** | §4.4–4.6 risk distribution, defence modelling, strategy hold | Builds on C's projections |
| **E** | §2 briefings (pre-session first, then post-race, then post-quali-lap) | Post-quali-lap is the most complex; do it last |
| **F** | §3 pre-emptive questions and feedback weighting | Depends on D's rationale surface to explain push-back |

### 6.1 Acceptance tests to write

Follow the existing convention in `tests/` — each test names the real-world failure it prevents.

- **§4:** given P10, 7 laps left, a plan that rejoins P19 must **not** be recommended over staying
  out, even when it is 10 seconds quicker on elapsed time. Use the Texas numbers verbatim.
- **§4.6:** after a refusal, no `strategy_change` call fires for 5 laps unless a safety car,
  red flag, weather crossover or wear-limit breach occurs.
- **§3:** a `tyres_gone` report changes the wear rate by at most 25% and never inverts the ranking
  on its own; a conflict between feedback and telemetry sets `feedback_conflict`.
- **§2:** each pre-session brief is generated with zero telemetry connected and contains no
  fabricated numbers — every projection is labelled with its sample size and confidence.
- **§1:** each new panel renders correctly with `replay_demo.py`, and with a cold state where every
  field is null.

### 6.2 Cost note

Briefings and debriefs are long-form and infrequent — route them to `gpt-5.6-sol` via the existing
`route="deep"` path. Dashboard panels must be **pure client-side computation over `/ws` state**, with
no model call at all. The `driver_check` question is short and should use the fast tier.

---

## 7. What this does not change

- The deterministic-numbers invariant (§0.1). Every feature above computes in Python first.
- The safety anchor: standing instructions and driver preferences can silence topics and change
  tone, never a penalty, red flag, safety-car delta or blown engine.
- The tool allow-list boundary for the speech path (`RADIO_TOOLS` in `realtime.py`).
- The requirement that a restricted rival's data is reported as unavailable, never as zero.
