# Race strategy model: review and target architecture

A structural review of `src/pitwall/strategy.py` and the evidence modules that
feed it, against how race-strategy models are built in motorsport engineering
and in the published literature. No code changes accompany this document; it
exists to decide what the model should become before anything is rewritten.

Every claim about the current model cites the line that supports it, so each
one can be checked rather than taken on trust.

---

## 1. What the model does today

### 1.1 The lap-time equation as implemented

`_simulate_stint` (`src/pitwall/strategy.py:2214`) is the whole forward model.
Stripped of bookkeeping, each projected lap is:

```
lap = base_lap_s                       # median of last 4 matched laps
    - reference_adjustment_s           # deg + wear already inside that median
    + compound_delta                   # dry offset + wetness penalty
    + set_delta_s                      # game's fitted-to-spare pace delta
    + setup_delta_s                    # only when setup is not already in pace
    + deg * (age + 1)                  # linear in tyre age
    + wear_penalty(wear[4])            # piecewise thresholds at 52/58/70 %
    + warm_up                          # 1.1 s out-lap, 0.55 s on lap 2
```

with per-corner wear accumulating at `wheel_rates[i] * (1 + max(0, age-8)*0.012)`
and hard feasibility gates at the operational wear limit, the set's reported
life, and 92 % wear.

Plans are whole-race enumerations over a grid of box laps (every lap for
one-stops, every second or third lap for two-stops, `strategy.py:3303-3420`),
each scored, then ranked.

### 1.2 Where the numbers come from

| Quantity | Source chain | Line |
| --- | --- | --- |
| Degradation | track default x severity -> personal circuit history -> live fuel-corrected fit, blended at `min(1, n/8)` | `strategy.py:1174` |
| Wear rate | track default x severity x style -> condition regression -> personal history -> inferred -> live, blended at `min(1, n/6)` | `strategy.py:1135`, `2089` |
| Driving style | one scalar: observed wear per lap / default wear per lap, clamped 0.60-2.60 | `strategy.py:1043` |
| Driver feedback | "tyres gone/going/fine" -> fixed multipliers, decayed over 5 laps | `strategy.py:1090` |
| Pit loss | hardcoded circuit table, 19.5-28.0 s | `strategy.py:26` |
| Compound offsets | fixed: SOFT -0.55, MEDIUM 0.0, HARD +0.65 | `strategy.py:260` |
| Overtaking | hardcoded circuit difficulty 0.22-0.95 | `strategy.py:226` |
| Weather | full wetness model with field-level inference | `src/pitwall/rain.py` |

The learning that produces the personal slope is in `tyre_learning.py:72`:
runs are separated by session, age reset, compound, weather and setup; each run
gets its own intercept; lap times are fuel-corrected at 0.030 s/kg
(`tyre_learning.py:14`) before a robust median-of-pairwise-slopes fit; noisy or
implausible fits return `None` rather than a clamped number.

### 1.3 The objective

`ranking_key` (`strategy.py:3735`) sorts plans lexicographically:

```
(illegal, infeasible, projected_position_without_feedback, projected_position,
 appetite_position, -appetite_points, -first_stop_lap, risk_adjusted_time, stops)
```

### 1.4 Uncertainty

`_monte_carlo_profile` (`strategy.py:2398`) draws 80-1200 samples from three
independent Gaussians (tyre, pit, traffic) with heuristic sigmas, and reports
its own honesty in the payload: `"uncertainty_basis": "heuristic_per_stint_model"`,
`"calibrated": False`.

---

## 2. What is good and must survive any rewrite

These are not small things, and a rewrite that loses them would be a
regression however much better its physics.

1. **Every estimate carries its provenance.** Source string, sample size,
   assumptions list. `deg_source: "blended_live_pace+personal_track_history"`
   is worth more than a better number with no label, because it is the only
   reason anyone can debug a bad call after the race.
2. **The model refuses rather than guesses.** `stint_pace_model` returns
   `slope_s_per_lap: None` when the fit is bad instead of clamping to a
   plausible-looking value. `_pace_reference` declines a matched reference
   when wetness > 0.05. That discipline is rarer than it should be.
3. **The fuel/age collinearity is handled correctly.** Independent intercepts
   per run, comparisons only within a run, a fixed fuel prior rather than a
   free parameter. This is the right answer to a genuinely hard identification
   problem, and the docstring explains why.
4. **Wet compounds are priced against conditions, not by a constant**
   (`compound_pace_delta_s`, `strategy.py:267`). A fixed "+7 s for an inter"
   would make every wet plan unrankable.
5. **Legality and inventory are hard constraints**, not penalties.
6. **"A position is bought with time, never granted for circulating"**
   (`strategy.py:1784`). The comment records a real race where the old model
   gave away nine free positions. That lesson should be preserved explicitly
   in whatever replaces it.

---

## 3. Diagnosis: why the model feels wrong

Ordered by how much each one changes the advice a driver actually receives.

### 3.1 The objective function discards the distribution it just computed

`_position_distribution` (`strategy.py:1874`) produces a full probability mass
function over finishing positions, `points_expected`, `upside_p10`,
`downside_p90`. Then `ranking_key` sorts primarily on
`int(projected_finish_position)` — a point estimate.

Consequences:

- A plan projecting P4 with a 35 % chance of P2 loses to a plan projecting P4
  with no upside at all, because the integer is equal and the tiebreak falls
  through to `-first_stop` before it ever reaches points.
- The primary key is an integer, so the ranking is a step function of every
  continuous input. A 0.05 s/lap change in the deg estimate can flip the whole
  recommendation, with nothing in between.
- That instability is real and already documented in the repository. The
  machinery built to suppress it — `_stabilize_radio_plan`, `_pending_switch`,
  `strategy_min_hold_laps`, `strategy_switch_confirm_s`,
  `strategy_change_min_gain_s` (`strategy.py:2498`, `config.py:303-309`) —
  carries its own justification in `config.py:306`: "at Sakhir the call swung
  between a one-stop on hards and a two-stop on softs several times a lap on
  gains that lasted a second at a time." A 20-second confirmation timer is a
  reasonable response to that symptom. It is not a response to the cause.
- Risk appetite is implemented by *switching sort keys* between three
  different quantities rather than by varying one risk parameter, so
  "conservative" and "aggressive" are not points on a scale and cannot be
  compared or interpolated.

This is the single biggest structural problem. The model computes the right
information and then throws it away at the last step.

### 3.2 There is one tyre model, and it is yours alone

`_project_rival_finish_times` (`strategy.py:1565`) calls:

```python
clean_state["analysis"] = {}                 # discard live fits
deg, _, _ = self._deg_for(clean_state, compound, {})   # empty history
```

With empty history and no live analysis, `_deg_for` falls straight through to
`DEFAULT_DEG[compound] * TRACK_TYRE_SEVERITY[track]` — a constant from a table
written by hand. On lap 30 of a race where nineteen other cars have been
circulating on the same compounds on the same surface, the model has learned
nothing from any of them.

Rival stop timing is equally fixed: `TYPICAL_STINT_LAPS` (`strategy.py:322`) is
derived arithmetically from the default wear table, so "when will they stop"
is a constant divided by another constant.

Everything downstream inherits that constant: rejoin position, positions
recovered, the finish projection, the position distribution, the undercut and
overcut tools (`_cut_rival_laps`, `strategy.py:4346`, which uses the same
`_deg_for(clean, compound, {})` path). The most information-rich signal
available in a race — nineteen cars running a controlled experiment on your
tyre compounds, on your track, in your conditions — is unused.

The data is present. `state.py` carries, per rival: `tyre_compound` (line 66),
`tyre_age` (68), `tyre_wear[4]` (81), `lap_history` (114), `tyre_stints` (115),
`gap_to_player_s` (61), `delta_to_leader_s` (63).

The precedent is also already in the repo. `rain.py` does exactly this for
weather: `field_pace_observations`, `compound_split`, `fit_wetness` infer the
state of the track from what the whole field is doing. The tyre model needs the
same architecture and does not have it.

### 3.3 Degradation and wear are two models of one phenomenon

Pace loss is computed twice through two independent channels:

- `deg * age` — linear, from the fuel-corrected slope fit.
- `_wear_pace_penalty(wear)` (`strategy.py:441`) — piecewise-linear in wear
  percentage, with knees at 52 %, 58 % and 70 %.

Both are learned from the same laps but with different estimators, different
sample-size rules, and different fallbacks. Nothing makes them consistent.
When the live slope is learned from laps that already included the wear
penalty, the slope absorbs part of it, and the forward model then charges it
again. `_pace_reference` (`strategy.py:472`) is an explicit correction for this
double-count at the reference point only — it subtracts both terms back out of
the observed baseline. That correction exists because the decomposition is
ambiguous; the ambiguity is the actual problem.

The physical reality being approximated is one curve: pace loss as a function
of tyre life, roughly flat-then-linear-then-cliff. It should be one function
with one set of parameters.

### 3.4 Fuel is corrected out during learning and never added back

`FUEL_SECONDS_PER_KG = 0.030` appears in `tyre_learning.py` and nowhere in the
forward simulation. A grep of `strategy.py` for "fuel" returns only comments
and source labels. The only other fuel constant, `strategy_fuel_save_s_per_lap`
(`config.py:316`), is used exclusively in `tools.py:1400`, outside the model.

Over a whole race the fuel term is common to every candidate plan and cancels
in the total, which is why this has not produced an obviously wrong answer. It
does not cancel anywhere else:

- **Projected lap times shown to the driver** are wrong by a growing amount —
  roughly 0.05-0.07 s/lap of unmodelled gain, about 1.5-2.0 s across a 30-lap
  stint. Those numbers are rendered on the dashboard and spoken on the radio.
- **Undercut and overcut deltas** are early-race, heavy-fuel quantities. The
  fresh-tyre advantage is larger on a heavy car, which is most of why the
  undercut is strong at the first stop and weak at the last. The model has no
  term that can express this.
- **Degradation itself depends on fuel load.** A car 50 kg heavier degrades
  measurably faster. The current model has one slope for the whole stint.
- **Rival comparisons.** A rival who has stopped is lighter than one who has
  not; their lap times are not comparable without the term.

### 3.5 "Driving style" is a single wear ratio, and is not a decision

`_driver_wear_factor` returns `observed_wear_per_lap / default_wear_per_lap`,
clamped to [0.60, 2.60]. That ratio absorbs the car, the setup, track
temperature, traffic, fuel load and the driver, and attributes all of it to
style. It is a calibration residual with a misleading name.

More importantly, style is modelled as something *observed about* the driver,
not as something the driver can *choose*. The most valuable advice a strategy
engineer gives is conditional: "this becomes a one-stop if you take three
tenths off for eight laps". The current model cannot represent the antecedent
of that sentence, so it can never say it.

### 3.6 Overtaking is a time budget divided by a circuit constant

`_expected_positions_recovered` (`strategy.py:1756`):

```
time_budget      = pace_advantage * laps_after_stop * 0.60
overtake_tax     = 1.0 + 6.0 * circuit_difficulty
cost_per_position = median_adjacent_gap + overtake_tax
recovered        = pit_cycle + time_budget / cost_per_position
```

The limitations are structural rather than parametric:

- `pace_advantage` is measured against the **median of the whole field**
  (`reference_pace = median(nearby)` where `nearby` is every rival with a
  pace). The cars you actually have to pass are not the median of the field —
  they are the specific cars between your rejoin position and your target, and
  their pace is individually known.
- Passes are fungible. Rejoining behind one slow car and six quick ones costs
  exactly as much as the reverse.
- No DRS. No dirty air. In a real race the lap-time penalty for following
  peaks around 0.5 s at a 0.6 s gap and vanishes beyond about 2.5 s — and that
  penalty feeds back into tyre degradation, which is the mechanism by which
  losing track position actually costs a race. None of that loop exists.
- `0.60` (`_ADVANTAGE_RETENTION`) and `1.0 + 6.0 * difficulty` are unfitted
  constants applied to every circuit, car and situation.

**The rejoin position is computed from the field as it stands now, not as it
will stand at the proposed stop.** `_rejoin_position` (`strategy.py:1460`)
counts cars whose *present* `gap_to_player_s` falls inside the pit loss. For a
stop proposed twelve laps out, that is the wrong grid: it prices the traffic
you would rejoin into today. Because this is the main term distinguishing one
box lap from another, the model is close to indifferent between lap 14 and lap
16 for exactly the reason a driver would care about them — which cars are
there. Advancing each rival's projected lap times to the candidate box lap
before counting is a contained fix, and the projections needed for it are
already computed in `_project_rival_finish_times`.

### 3.7 Neutralisation is priced only where it has already happened

`_neutralisation` (`strategy.py:1247`) discounts the pit loss for the *current*
phase: 0.46 under safety car, 0.64 under VSC, 0.0 under red flag. The payload
then states the limitation plainly:

```
"future_stop_assumption": "Green-flag loss after the current reachable
 opportunity; neutralisation duration is unknown."
```

So every future stop is priced at full green-flag cost, with zero probability
of a cheap one. This removes the central idea of modern race strategy: the
*option value* of tyre state. Staying out is worth something precisely because
a safety car may arrive; stopping early is worth something because it banks a
stop before one does. With no hazard rate, both effects are exactly zero, and
the model systematically prefers whatever is faster in a deterministic race —
which is not the race being driven.

Published strategy simulators handle this with a per-circuit expected-safety-car
prior converted to a per-lap hazard, with an opening-lap spike. The circuit
tables this repo already maintains are the natural place for that number.

### 3.8 Tyre temperature is captured and never read

`state.py:28-29` records `inner_temps_c` and `surface_temps_c` per corner;
`state.py:100-101` records them per rival; `state.py:1095` persists
`temps_end` on every completed lap. `strategy.py` contains no reference to any
of them.

In the EA title this matters more than in real F1, because the game's tyre
model makes temperature a first-order driver of both grip and wear: outside the
working range the tyre slides, which heats it further and wears it faster. A
degradation model with no temperature term cannot distinguish "this compound
degrades quickly here" from "this driver is overheating the rears", and those
two diagnoses call for opposite strategies.

### 3.9 The module has outgrown its shape

4,748 lines, one class, a `compute` method spanning roughly 800 lines with
nine nested closures, candidate enumeration as a hand-written grid over box
laps, and caches keyed by hand-built tuples. The code is unusually well
commented and the comments frequently record the real race that motivated each
fix — that history is valuable. But the structure is why every change to the
model feels risky, and it is a reason the model is hard to like even where it
is right.

---

## 4. What a strategy model should be

### 4.1 One lap-time identity, shared by learning and prediction

The deepest fix is architectural rather than numerical. Today the learning
module and the forward simulator use *different decompositions* of a lap time:
`tyre_learning.stint_pace_model` fits `time - fuel_correction ~ intercept +
slope * age`, while `_simulate_stint` predicts `base + compound + set + setup +
deg*age + wear_penalty + warmup`. Terms estimated under one decomposition are
consumed under another, and `_pace_reference` exists to patch the seam.

Both should be views of one identity:

```
t(car, lap) = t_base(circuit, car)                       # reference pace
            + k_fuel * m_fuel(lap)                       # fuel mass
            + d_compound(c)                              # compound offset
            + warmup(a)                                  # out-lap / first laps
            + deg(c, a, T, load; theta_driver)           # tyre life
            + evolution(lap)                             # track rubbering in
            + weather(wetness, c)                        # already modelled well
            + push(p)                                    # driver's chosen pace
            + dirty_air(gap_ahead) - drs(gap_ahead)      # interaction
            + epsilon                                    # driver noise
```

Estimation is then "measure the terms of this equation"; prediction is
"evaluate this equation forward". Every term carries `(value, source, n, sigma)`
— the discipline this codebase already has, applied to a decomposition that
does not double-count.

### 4.2 Hierarchical tyre degradation: yours, the field's, and the prior

This is the core of what the model is missing, and the data for it is already
in `state.py`.

Three levels of evidence for the degradation of compound `c` at this circuit:

- **Level 0 — prior.** Compound base rate x circuit severity. What exists now
  (`DEFAULT_DEG`, `TRACK_TYRE_SEVERITY`). Always available, never precise.
- **Level 1 — field.** Every car observed running compound `c` in this
  session. Nineteen cars x their stint lengths is one to two orders of
  magnitude more tyre-life observations than the player generates alone, and it
  is gathered under *today's* track temperature, rubber and fuel-load schedule.
- **Level 2 — you.** Your own fuel-corrected slope from
  `tyre_learning.stint_pace_model`. Most relevant, always the smallest sample.

Combine by precision-weighted shrinkage rather than the current
first-match-wins cascade:

```
theta_hat = (n_you/s2_you * theta_you + n_field/s2_field * theta_field + 1/s2_prior * theta_prior)
          / (n_you/s2_you + n_field/s2_field + 1/s2_prior)
```

Three properties follow that the current cascade cannot produce:

1. **Graceful evidence.** Three laps on a compound no longer either
   over-commit to a noisy slope or fall back to a hand-written constant; they
   move the estimate partway from the field's answer toward yours.
2. **A transferable driver offset.** The useful quantity is not your absolute
   slope but `delta_driver = theta_you - theta_field` on compounds you have
   run: "this driver degrades 12 % faster than the field". That offset is far
   more stable than the slope itself, and it transfers to compounds you have
   *not* run — replacing the `_DEFAULT_DEG_STEP_RATIO = 1.35` extrapolation
   (`strategy.py:102`) with something measured.
3. **Honest rivals.** Each rival gets `theta_field + delta_rival` from their
   own laps, so the undercut/overcut tools stop comparing your measured tyre
   against a rival's table constant.

**Estimating the field level from game telemetry.** Rival fuel mass is not
broadcast, which is the stated reason the current code refuses to fit rival
slopes (`strategy.py:1586-1588`). That reasoning is right for a single rival
and wrong for the field, but the correct argument is narrower than the one
this document first made, and the difference matters enough to state.

The first version of this section claimed that pooling across cars separates
fuel from tyre age because fuel is the common component and age the varying
one. That is not sufficient. Write the model with a car effect and a fully
flexible per-lap effect:

```
lap_time[car, lap] = alpha[car] + gamma[lap] + beta * age[car, lap] + e
```

`gamma` absorbs every effect the field shares on a lap — fuel load, track
evolution, air and track temperature, a safety car, a shower — so no rival
fuel telemetry is needed for any of them. But if a car runs a compound exactly
once, its tyre age is `lap - start + 1`, which is *precisely* a car effect plus
a lap effect. The fixed effects annihilate it and `beta` is not identified, no
matter how many cars or laps are supplied.

What breaks that additivity is the pit stop. Age resets to zero while the lap
counter carries on, so a field whose cars have stopped on different laps
carries a sawtooth in tyre age that no car-plus-lap decomposition can
reproduce. **That sawtooth is the entire identifying signal**, and it implies
the honest operating rule: before the first round of stops the field can teach
the model nothing about absolute degradation, and the model should say so
rather than return a number.

The same fit, run across all dry compounds at once with age-by-compound
interactions, also yields the compound *contrasts* — which is what replaces the
hand-written `_DEFAULT_DEG_STEP_RATIO = 1.35` with a measurement.

This is the same family of trick `rain.py` already uses — it recovers wetness
from the field by comparing cars on different compounds at the same moment
(`field_pace_observations`, `compound_split`).

Requirements for the field fit to be trustworthy:

- **Clean-air filtering.** A rival within ~2.5 s of the car ahead is measuring
  dirty air, not tyre life. `delta_to_leader_s` gives the gaps needed to
  exclude those laps.
- **Per-car intercepts.** Cars have different pace. Same discipline as the
  existing per-run intercepts.
- **Neutralisation and pit-lap exclusion.** `exclusion_reason` in
  `tyre_learning.py` already encodes these rules; they apply unchanged.
- **AI difficulty is a level, not a slope.** A faster AI is quicker at every
  tyre age; it is absorbed by the per-car intercept.
- **Confidence must reflect pooling.** A field-informed estimate is not a
  personal measurement and should never be labelled as one. The existing
  `inferred_*` convention (`strategy.py:109`) is the right precedent.

### 4.3 Degradation as one curve with regimes

Replace `deg * age` plus threshold wear penalties with a single parametric
pace-loss function of tyre life:

```
deg(a) = warmup(a) + linear_phase(a) + cliff(a)
```

A quadratic or piecewise-linear-with-knee form captures what matters: near-flat
for the first laps, close to linear through the working window, sharply
accelerating past the cliff. The knee location is the operationally important
parameter — it *is* the answer to "how long can this stint go". Modelling it
explicitly makes it estimable and reportable; modelling it implicitly, as three
hardcoded wear thresholds, makes it neither.

Wear percentage then becomes what it physically is — a *state variable* that
predicts where the knee falls and enforces feasibility — rather than a second,
parallel pace model.

Temperature enters here: `deg` should take the working-range deviation as an
argument, since the telemetry provides it per corner and per lap.

### 4.4 Fuel, explicitly, in both directions

Two terms, both cheap:

- **Mass on lap time**: `k_fuel * m_fuel(lap)`, `k_fuel ~ 0.03 s/kg`, burn
  1.6-2.2 kg/lap. The constant already exists in `tyre_learning.py`; it needs
  to be applied forward, not only backward.
- **Mass on degradation**: heavier car, higher slip energy, faster wear. A
  modest multiplier on the deg rate as a function of remaining fuel.

The second is what makes the undercut strong at the first stop and weak at the
last, and it is currently inexpressible.

### 4.5 Push level as a control variable

Promote driving style from an observation to a decision. Define a push level
`p` mapping to a trade curve `(delta_lap_time, delta_deg, delta_wear,
delta_fuel)`. The driver's own frontier is *suggested* by data already stored — lap time
against wear increment, within a stint, at matched tyre age — but that
relationship is observational, and the distinction is not pedantic. Laps that
were slower may have been slower because of a cooler track, traffic, or a
mistake, and their lower wear may have the same cause. Fitting the curve to
such laps estimates "slower laps had less wear", which is not the quantity the
advice depends on: the causal effect of *choosing* to go slower. The
trustworthy version needs matched runs — a deliberate management stint against
a deliberate push stint in comparable conditions — which practice sessions can
supply and a race cannot. Until then the trade curve should be offered as an
assumption with a stated width, not as a measurement.

The strategy search then optimises over `(box_laps, compounds, push_profile)`
instead of `(box_laps, compounds)`, and the model gains an entire class of
advice it cannot currently give:

> "One-stop is 8 s quicker but needs 0.3 s/lap of management from lap 22.
> Two-stop needs nothing from you. Your call."

That is what a race engineer says. It is also, notably, the kind of advice that
does not flip every time an estimate wobbles.

### 4.6 Overtaking as probability, traffic as lap time

Two separate mechanisms, currently conflated into one budget:

**Passing.** Per-rival probability rather than a fungible budget:
`P(pass | delta_pace, gap, circuit, DRS)` — a logistic in pace delta with a
circuit-specific scale that the existing `TRACK_OVERTAKING_DIFFICULTY` table
can seed. Expected positions recovered becomes a sum of per-rival
probabilities against the *specific* cars in the way, each with their own
projected pace. DRS enters as a pace bonus conditional on being within a
second at the detection point.

**Being stuck.** A lap-time cost while following, peaking around 0.5 s at a
0.6 s gap and decaying to zero by about 2.5 s. This is the mechanism the model
most needs, because it closes the loop: being stuck costs lap time, which costs
tyre life through the deg term, which is the real reason track position is
worth defending. Today `_traffic_cost` (`strategy.py:2367`) charges a flat
ranking penalty at the moment of rejoin and nothing thereafter, so a plan that
rejoins into a twelve-lap train is charged once and then modelled as though the
train were not there.

### 4.7 Neutralisation as a hazard, and the decision as an expectation

Add a per-circuit safety-car rate `lambda_SC` with a per-lap hazard and an
opening-lap spike, then evaluate every decision as an expectation over
scenarios rather than as a deterministic race:

```
E[U | box now]  = sum_s P(s) * U(outcome | box now, s)
E[U | stay out] = sum_s P(s) * U(outcome | stay out, s)
```

Scenarios include no neutralisation, an SC during your window, an SC after your
window, and a red flag. This converts "neutralisation duration is unknown" from
a disclaimer into a number, and it is the only way to express the two
statements a strategist makes most often: *bank the stop before the risk
window*, and *stay out and keep the option*.

The same scenario machinery should absorb rival strategy uncertainty. A rival's
stop lap is not a constant derived from a wear table; it is a distribution, and
the undercut calculation is only meaningful as an expectation over it.

### 4.8 The objective: expected utility over the distribution

Replace the lexicographic tuple with a scalar utility of the position
distribution the Monte Carlo already produces:

```
U(plan) = sum_p P(finish = p) * value(p)     [with a risk tilt]
```

- `value(p)` is selectable by intent: championship points, finishing position,
  or beating a nominated rival. A league racer defending a gap to one car does
  not have the same objective as someone maximising points, and the model
  should be able to hold either.
- Risk appetite becomes **one parameter** — the weight on the lower tail (a
  CVaR-style tilt) — rather than three different sort keys. Conservative and
  aggressive become points on a scale, and can be interpolated, explained and
  tuned.
- Legality and feasibility stay as hard constraints, not as terms.

Two things fall out for free. First, the recommendation becomes a continuous
function of its inputs, so a small change in evidence produces a small change
in `U` — and the stability problem can be handled by a principled hysteresis
threshold on `delta U` instead of the current plan-hold state machine. Second,
"how much does this plan cost me" becomes answerable in the units the driver
cares about, rather than in seconds of a race time nobody experiences.

### 4.9 Output a policy, not a plan

The engine currently enumerates whole-race plans, ranks them, picks one, and
then works hard to stop the pick from oscillating. But a whole-race plan is a
fiction the moment anything happens.

The natural output is a **policy with explicit triggers**:

> Target: box lap 24-27 for hard.
> Box immediately if: safety car, or rear wear > 78 %, or the gap to P6 exceeds
> 23 s.
> Extend to lap 30 if: you can hold 0.3 s/lap of management and P5 stays within
> 4 s.

This is what a pit wall actually communicates, it is robust to the race
changing, and it makes the stability machinery unnecessary because the advice
already contains its own conditions.

---

## 5. Coverage scorecard

| Input | Today | Data available? | Priority |
| --- | --- | --- | --- |
| Own tyre degradation | Fuel-corrected slope, well done | yes | keep |
| **Field / rival degradation** | **learned (4.13)** | yes | done |
| Compound contrasts | learned (4.13); was a 1.35 multiplier | yes | done |
| Driver deg offset vs field | absent | yes | 2 |
| Tyre wear per corner | modelled, and used as a second pace channel | yes | refactor |
| Degradation cliff | implicit in wear thresholds | yes | 3 |
| Tyre temperature | **captured, never read** | yes (`state.py:28-29`) | 3 |
| **Fuel mass on lap time** | **learning only, never forward** | yes | **2** |
| Fuel mass on degradation | absent | yes | 2 |
| Fuel saving / lift-and-coast | a config constant, outside the model | yes | 4 |
| Push level as a decision | absent | yes | 4 |
| Compound offsets | fixed table | learnable | 3 |
| Warm-up / out-lap | flat 1.1 s | yes | keep |
| Track evolution | absent | partially | 5 |
| Weather / wetness | strong, field-informed | yes | keep |
| Pit loss | circuit table | partially learnable | keep |
| Pit loss variance | fixed 1.5 % / 4 % | yes | 5 |
| SC/VSC now | priced | yes | keep |
| **SC/VSC probability** | **absent** | prior + history | **2** |
| Rival pace | matched-stint median | yes | keep |
| Rival stop schedule | constant from a wear table | yes | 1 |
| Gaps / traffic at rejoin | flat one-off penalty, on today's grid | yes | 3 |
| Dirty air while following | absent | yes | 3 |
| DRS | absent | yes | 4 |
| Overtake probability | time budget / circuit constant | yes | 3 |
| Undercut / overcut | present, on constant rival deg | yes | 1 |
| Tyre inventory & rules | hard constraints, correct | yes | keep |
| Penalties | priced once | yes | keep |
| Damage | absent from strategy | yes | 5 |
| Risk appetite | three sort keys | n/a | 2 |
| Objective | lexicographic position | n/a | **2** |
| Calibration | `"calibrated": false` | needs a harness | **2** |

---

## 6. Sequence

Ordered so that each stage is independently shippable and each one makes the
next cheaper.

**Stage 0 — Evidence record and retrospective prediction logging.** Store each
prediction before its outcome is known, with the model version and the evidence
that produced it. This moved to the front on reflection: without stored
pre-outcome predictions there is no way to tell whether any later stage helped,
so every stage below is otherwise unfalsifiable.

**Stage 1 — Field-learned degradation. _Implemented._** `field_learning.py`
beside `tyre_learning.py`: a two-way fixed-effects fit over the field's
session-history laps, with clean-air filtering, neutralised-lap exclusion,
car-clustered standard errors and an explicit identification diagnostic. It
feeds `_deg_for` as an evidence level combined by precision, and reaches
`_project_rival_finish_times` and the undercut/overcut tools, which previously
ran on `DEFAULT_DEG * TRACK_TYRE_SEVERITY`. Rival stop-lap estimates are *not*
derived from it: a rival's stop lap is a decision, not a tyre limit, and
learning tyre life from observed stop laps would fold strategy into physics.

**Stage 2 — Expected-utility objective.** Replace the lexicographic key with a
scalar utility over the existing position distribution; make risk appetite one
parameter. Then reassess how much of the plan-hold machinery is still needed —
some of it should become deletable, which is the clearest signal that the fix
was structural. Promoted above fuel because it changes which plan is chosen,
which the fuel term mostly does not.

**Stage 3 — Future-state rejoin and per-rival encounters.** Advance the field
to each candidate box lap before counting the rejoin position (§3.6), then
replace the recovery budget with per-rival pass probabilities and a dirty-air
lap-time cost.

**Stage 4 — Explicit fuel term, forward.** Demoted deliberately. Over a whole
race the fuel term is common to every candidate and cancels in the comparison,
so it barely moves the optimal box lap. It is still worth doing, because it
fixes every lap time the app displays and speaks, makes rival comparisons
sound across differing fuel loads, and lets degradation depend on fuel mass —
which is why the undercut is strong at the first stop and weak at the last.

**Stage 5 — Safety-car hazard and scenario evaluation.** Per-circuit prior,
per-lap hazard, decisions as expectations. Depends on Stage 2, because
scenarios only compose if the objective is a scalar.

**Stage 6 — Unify degradation and wear into one curve**, with temperature as an
argument. Largest refactor; best done once the objective is stable, so the
change can be measured.

**Stage 7 — Push level as a control**, subject to the causal caveat in §4.5.

**Calibration harness, running throughout.** Nothing above is believable
without it, and the model still ships `"calibrated": false` in its own output.
The harness needs:

- Predicted vs actual lap time, per stint, as a function of tyre age
  (MAE by age bucket — this is what detects a wrong cliff).
- Predicted vs actual stint wear at the flag.
- Predicted vs actual finishing position.
- Calibration of the position distribution: reliability diagram, Brier score
  on "finishes in the points", CRPS on the position PMF.
- Replay-based evaluation over saved sessions, so a model change can be scored
  against races already captured rather than only against synthetic fixtures.

`docs/STRATEGY_LEARNING.md` already names this as the next priority: "held-out
real race and practice captures ... Synthetic checks establish regression
correctness, not real-world predictive accuracy." That remains the right call,
and every stage above should be gated on it.

---

## 7. The one-paragraph answer

The model is built on unusually honest foundations — provenance on every
estimate, a refusal to invent numbers, a correct treatment of the fuel/age
identification problem — and it is let down by three things above those
foundations. It learns only from one car when nineteen others are running the
same experiment in front of it. It computes a probability distribution over
outcomes and then ranks on an integer. And it models a deterministic race, with
no safety car probability, no fuel term carried forward, and no way for the
driver to change the answer by driving differently. Fix those three and the
rest is refinement.

---

## References

- Heilmeier et al., *A Race Simulation for Strategy Decisions in Circuit
  Motorsports*, IEEE ITSC 2018 — lap-discrete simulation with tyre
  degradation, fuel mass, pit stops and overtaking.
  https://ieeexplore.ieee.org/document/8570012/ ·
  https://github.com/TUMFTM/race-simulation
- Heilmeier et al., *Virtual Strategy Engineer: Using Artificial Neural
  Networks for Making Race Strategy Decisions in Circuit Motorsport*, Applied
  Sciences 10(21), 2020. https://www.mdpi.com/2076-3417/10/21/7805
- *A State-Space Approach to Modeling Tire Degradation in Formula 1 Racing*,
  arXiv:2512.00640 — lap time as fuel mass plus latent tyre pace, pit stops as
  state resets, compound-specific rates, driver intercepts.
  https://arxiv.org/abs/2512.00640
- *Towards Learning-Based Formula 1 Race Strategies*, arXiv:2512.21570 — on the
  limits of hand-crafted heuristics in Monte Carlo strategy engines.
  https://arxiv.org/pdf/2512.21570
- *Bridging RL and MPC for mixed-integer optimal control with application to
  Formula 1 race strategies*, arXiv:2604.00826 — formal statement of race
  strategy as mixed-integer optimal control.
  https://arxiv.org/pdf/2604.00826
- The Race, *Revealed: the data on F1's worsening dirty air problem* —
  following-car lap-time loss peaking near 0.5 s at a 0.6 s gap and vanishing
  beyond ~2.5 s.
  https://www.the-race.com/formula-1/exclusive-new-data-f1-aero-losses-ruining-close-racing/
- SimRacingSetup, *F1 25 Tyre Temperature & Pressures Explained* — the game's
  tyre model: working range, and the slide/heat/wear feedback loop outside it.
  https://simracingsetup.com/ea-sports-f1/f1-25-tyre-guide/
