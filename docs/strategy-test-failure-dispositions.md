# Test failure dispositions

This record explains changed expectations and failed experiments. A green
suite alone must not conceal either an observed defect or a weakened oracle.
All experiments used isolated synthetic data.

## Existing test expectations

The first integrating full run passed 1,371 tests and failed four. The second
passed 1,460 and failed two. The third, at `35b33c6`, passed all 1,529 tests.
The complete logs are retained in the handoff; later final runs are identified
separately in the validation record.

| Failure | Disposition and stronger check |
|---|---|
| Championship fixture expected 15 points from rejoin P3 | The product now scores finishing position. The fixture explicitly separates rejoin P8 from finish P3, preserving the expected 15 points for the correct reason. Illegal/infeasible projection tests cover the public tool and UI. |
| Every red-flag recommendation expected a red-flag instruction | Only a change during the current suspension is free. Tests now verify all candidate stop ledgers, including later green costs, plus a worn-tyre case that actually selects and speaks an immediate free change. |
| VSC globally best plan had to become cheaper | A current VSC cannot discount an unrelated future stop. Tests compare identical physical-set schedules, remove separately reported traffic effects and verify the exact current-stop saving, with an explicit current-opportunity fixture. |
| Held-plan test pinned raw future box lap 27 | Improved search chose lap 26 while correctly retaining the held lap 25. The test checks a genuinely later raw winner and complete atomic refresh instead of an arbitrary optimizer lap. |
| An explicit 57-lap plan was expected to be followed | Its final SOFT stint projected 117.9% wear and was unsafe. That original request now must receive an unsafe explanation. A separate physically valid 46-lap request verifies following the driver. This also exposed a real omission of exact schedules between sampled laps; the production search was fixed and a safe exact three-stop regression added. |
| Manual radio-hold fixture omitted feasibility | The guarded hold correctly declines an unqualified candidate. The safe fixture now explicitly supplies `feasible=True`; separate regressions require unsafe/unknown holds to be rejected without dropping warnings. |

None of these dispositions restores an invalid strategy merely to preserve an
old expected value.

## Independent evaluator and harness corrections

- EA tyre-set `life_span` means remaining life, whereas `usable_life` is the
  maximum recommended total age. The initial adapter confused them. Both
  released and candidate versions were rerun after correcting the same adapter.
- Production box laps are end-of-lap actions. The oracle acts at lap boundaries.
  The adapter now uses `box_lap - current_lap + 1`; the oracle's offset-zero
  opportunity remains an explicitly disclosed lower-bound advantage.
- Four initial random-fuzz warnings rejected WET without standing water even
  when INTER was unavailable. The validator now applies that comparative
  restriction only when INTER is offered; choosing the least-bad available wet
  tyre is not itself a contradiction. Actual legality/output defects exposed
  by that run were reproduced and fixed separately.
- The new app harness initially queried nonexistent `recommended.confidence`
  and `/api/v1/sessions.sessions` fields. It now checks the public top-level
  strategy confidence and the actual `items` response. Independent copied
  database integrity failures remain failures; these harness corrections do
  not explain them away.

The scenario worlds, hidden seeds, physical oracle and observations were not
retuned to improve the candidate score.

## New defects that required production fixes

The final audit's weather/inventory fixture failed four reachable/passed-entry
cases before the fix and passed all six cases afterward. A forced weather
one-stop must not override a feasible WET/WET or INTER/INTER complete strategy.
The stop also cannot precede the next reachable pit entry.

The capture barrier probe persisted only 6/208 offered datagrams before the
fix and all 208 afterward. Additional forced-timeout and writer-open failures
exposed unresolved rotation futures and a missing error count; those required
production corrections and direct regressions. They are separate from
unresolved upstream UDP reception deficits.

Observed overlay-storage corruption in both release and candidate remains
unresolved. Passing tmpfs controls do not convert that failure into a product
fix. See `strategy-sqlite-runtime-blocker-20260913.md` for the complete controls.
