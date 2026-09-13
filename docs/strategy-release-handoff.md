# Strategy 4.10.0 candidate: release handoff

**Unpublished candidate. Do not interpret this handoff as release approval.**

The user authorized scenario-first refinement, isolated testing, fixes and
publication only after satisfactory validation. The scenario specification was
committed before implementation. All data used here is synthetic; existing
user data and live provider credentials were not accessed.

The base is released v4.9.8, commit
`daa029ad41ce29c101254e54a53ade5dbaf8b852`. GitHub `main` was independently
rechecked and still resolved to that commit during final review. The handoff
archive's manifest identifies the exact candidate commit and tree. The patch
series preserves the scenario-first history and all observed-failure fixes.

## Apply and inspect

Use a new checkout or branch, leaving any running installation and its data
directory alone. Apply the supplied `strategy-refinement.mbox` with `git am`
from the base commit. The archive also includes a Git bundle so commit identities
and complete source can be recovered without replaying the patches. Compare the
resulting tree with the manifest. If upstream has moved, integrate in an isolated
branch and repeat the affected validation; do not overwrite newer work.

From the extracted archive directory, the self-contained bundle can be opened
without a network fetch:

```bash
git clone --branch codex/strategy-refinement-20260913 pitbox-strategy.bundle pitbox-strategy-review
cd pitbox-strategy-review
git rev-parse HEAD
git rev-parse HEAD^{tree}
```

Alternatively, apply `strategy-refinement.mbox` with `git am` on a new branch
at the base SHA in an existing repository. Patch application can change commit
identities through committer timestamps; the resulting tree must still match
the manifest. Bundle cloning preserves the original commit identities.

Start with `strategy-scenarios-2026-09.md`, then
`strategy-coverage-2026-09.md`, `strategy-qa-findings-2026-09.md` and the
independent validation reports. The coverage ledger contains 51 covered,
20 partial and one unsupported scenario. Those labels deliberately do not
claim complete real-game validation.

## Required evidence

1. Run `python -m pytest -q` after installing `.[dev]` in a clean environment.
   Retain failures as well as passing logs. Old fixtures corrected in this
   series have their specific semantic changes documented; do not restore
   globally discounted future stops or illegal finishing-point claims just
   to match older expected values.
2. Run the frozen independent evaluator against both base and candidate:
   `python tools/strategy_validation.py --source <checkout> --output <report>`.
   Use `tools/strategy_validation_compare.py` to compare the two reports. Keep
   the same manifest, oracle, observations and adapter. Feasibility gains and
   common-world pace improvements are separate measures.
3. Reproduce normal and adversarial fuzz with the recorded seeds in the final
   validation record. Run the app harness against the supplied fixed capture
   using fresh output directories and owned loopback ports. Compare sent,
   parsed and captured counts independently. A zero `packets_dropped` counter
   does not establish lossless reception.
4. Resolve or bound the observed storage and load failures on the supported
   target. Byte-identical replay corrupted both v4.9.8 and the candidate on
   this workspace's overlay storage; tmpfs controls passed. The investigation
   establishes neither a production database fix nor a Windows failure.
   Tmpfs is a diagnostic control, not durable storage for users.
5. Run **Build Windows installer** with **attach_release=false** first. The
   updated smoke step installs the actual frozen EXE into disposable runner
   paths, verifies isolated startup, sends sustained 25-lap UDP telemetry,
   checks persisted strategies and full SQLite integrity, restarts and reads
   back both sessions, then uninstalls and verifies data retention. Inspect
   the diagnostics artifact even when the workflow is green. Portable tests
   and the Linux source-helper run are not substitutes for this result.
6. First-run browser rendering on clean Windows, real-game pit-entry/red-flag
   behavior, audio hardware and live narration still require observations on
   the supported product. Forecast probabilities, traffic response and
   temperature warm-up are not empirically calibrated by synthetic tests.

## Publication and access

GitHub branch creation and issue creation returned HTTP403,
`Resource not accessible by integration`. This is a connector permission
failure, not an automatic approval-review rejection. No remote strategy branch,
new issue, candidate release or deployment is claimed. The reproduced findings
are preserved in the repository for issue creation by an authorized publisher;
the ledger separates existing issues to avoid duplicates.

After the outstanding gates pass, the already authorized publisher can publish
4.10.0: release artifact first, R2 installer upload next, verify the public
`/installer` bytes and SHA-256 against that exact release asset, and deploy any
corresponding website changes afterward. This strategy series does not modify
the marketing website. Use existing authorized publishing access; the old token
quoted in earlier chat is not part of this handoff.

The final validation record identifies what actually passed and what remains
blocked. Do not turn a prepared candidate or a successful synthetic benchmark
into a claim that the product was deployed.
