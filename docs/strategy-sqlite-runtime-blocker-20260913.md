# Observed SQLite runtime blocker

**Status: unresolved; do not treat the strategy candidate as ready to deploy.**

The source application intermittently produced SQLite corruption under an
isolated 25-lap Monza telemetry run. This is an observed persistence failure,
not a source-review suspicion. It can coexist with zero UDP drops and plausible
live strategy output, so packet parsing alone is not a sufficient release gate.

All runs used newly created QA directories, independent localhost ports and
synthetic telemetry. No user's existing database or credentials were used.
Forensic SQL queries ran on copies made after the relevant server process had
exited, copying the database and any WAL/SHM companions together. Originals
were not repaired, migrated or opened with SQLite during investigation.

## Observations

Candidate source: `e1781b19da35aab7bcc426892ef790ea91a88252`.
Released control: `daa029ad41ce29c101254e54a53ade5dbaf8b852` (v4.9.8).
Runtime: Python 3.12.14 with built-in SQLite 3.53.1; ordinary workspace storage
is overlayfs, and the memory-filesystem control used `/dev/shm` tmpfs.

| Run suffix | Variant | Packets / drops | Storage result |
| --- | --- | ---: | --- |
| `mrmk8g2z` | Original candidate, 25 laps at 25x | 74,835 / 212 | Corrupted; first archive errors 14 seconds after startup; session API 500; shutdown failed |
| `bdcla1j4` | Candidate, shortened 8 laps at 25x | 24,176 / 0 | No SQLite errors; catalog read and orderly shutdown succeeded |
| `ljt7eqb2` | Fresh candidate repeat, 25 laps at 25x | 53,820 / 0 | Corrupted; final session API 500; copied integrity check reported missing pages, overflow-chain errors and index mismatches |
| `c2thd__x` | Candidate on tmpfs, 25 laps at 25x | 53,921 / 0 | Catalog read and orderly shutdown succeeded; copied integrity check `ok` |
| `26vrw7dt` | Candidate with Python raw-file-open audit hook, 25 laps at 25x | 49,130 / 0 | No matching raw DB/WAL/SHM open events; catalog read succeeded; copied integrity check `ok` |
| `3hn0nlfb` | Exact released source, 25 laps at 25x | 70,695 / 0 | Two orderly shutdowns and a relaunch succeeded; catalog contained one item before and after reopening; copied integrity check `ok` |
| `_utbaf15` | Candidate with startup VACUUM disabled only in the QA launcher | 51,094 / 0 | Still corrupted; first archive errors about 28 seconds after startup; session API 500; shutdown failed |

These are controlled configuration comparisons, not byte-identical packet
replays: the synthetic emitter's timing yields different packet totals under
different scheduling. One successful released run does not prove that the
failure was introduced by this strategy change, and one successful tmpfs run
does not prove that overlayfs caused it. A further causal comparison should
replay one finalized QA capture at its recorded timing on both source versions.

## Forensic evidence

The original failed main file contained 185 physical 4096-byte pages while its
header declared 195. Its WAL contained eleven frames with final commit sizes
of 185 pages. Both ordinary copied DB+WAL reads and immutable reads of the main
copy failed. Altering only the header count in an additional disposable
forensic copy did not restore validity; the problem was not just that header.

Original main-file SHA-256:
`9d7b6d4fbb6b62ec1ffa9e323d712897f64ebeb0d98b7f24d0c1b60bddaef28b`.

The second full candidate run's copied integrity check reported invalid page
references in the 1554–1560 range, an overflow chain of length 2 where 24 was
required, a duplicate reference to page 129 and index entry-count mismatches.
The no-VACUUM run's copied database also raised `database disk image is malformed`.

## Investigations that did not establish a cause

- Runtime code review found no ordinary path that directly overwrites,
  truncates or unlinks the main database. Storage accounting uses `stat()`;
  backups use SQLite's backup API; capture/trace files have separate paths.
- The audited application/dependency import set exposed one built-in SQLite
  implementation. No second SQLite implementation writing the database was
  identified.
- Several connection factories rely on garbage collection because SQLite's
  context manager commits/rolls back without closing. This is a resource
  lifetime concern, but it was not demonstrated to cause corruption.
- A standalone concurrent-WAL probe exercised overlayfs and tmpfs, each with
  implicit connection cleanup and explicit closing. Every variant committed
  1,500 random-blob transactions with three writers plus an integrity reader
  and forced garbage collection: 6,000 transactions total, correct row counts,
  atomic counters and payload hashes, all integrity checks `ok`.
- The raw-open audited app run emitted no Python `open` events for the SQLite
  database, WAL or SHM. Since that run did not corrupt, this cannot exclude a
  path unique to failing runs.
- Disabling startup VACUUM did not prevent corruption, so that operation is
  not a necessary trigger.
- System-call tracing was unavailable: `strace` was rejected because ptrace
  operations are not permitted in this runtime. No escalation was attempted.

SQLite's [corruption guidance](https://www.sqlite.org/howtocorrupt.html)
describes raw-file access, duplicate SQLite implementations, locking and memory
corruption as distinct possible causes. The installed version already includes
the older WAL-reset fix; the existence of later
[3.53.4 patches](https://www.sqlite.org/releaselog/3_53_4.html) does not establish
that an unspecified engine bug explains these observations.

## Separate QA harness defects

Two failures belonged to the new harness rather than the product:

1. It queried `recommended.confidence`, a field absent from the observed
   recommendation. The actual public strategy and model-summary confidence
   were both `low`, and stop instructions explicitly required confirming spare
   tyres. The check must use the public confidence field and apply the spare
   inventory requirement only to plans with remaining stops.
2. It expected `/api/v1/sessions` to return a `sessions` list. The actual API
   returns `items`; the released control had one item before and after reopen.

Neither harness defect accounts for the independently verified database
corruption. No speculative production database fix was committed here.

## Evidence locations

Workspace runs are under
`/workspace/scratch/f60f47f45c6d/pitbox-strategy-e2e-<suffix>` with `server.log`,
`summary.json`, `snapshots.jsonl` and isolated `data/` contents. The tmpfs run
was `/dev/shm/pitbox-strategy-e2e-c2thd__x`; its stopped-process database copy,
log and summary were preserved at
`/workspace/scratch/f60f47f45c6d/pitbox-tmpfs-control-iibp31x3`.

Additional stopped-process forensic copies are at:

- `pitbox-db-forensics-n7441852` — original corrupted run.
- `pitbox-sqlite-control-csnfn26s` — released control, integrity `ok`.
- `pitbox-sqlite-control-_1h7t2yf` — audit-hook control, integrity `ok`.
- `pitbox-no-vacuum-copy-zf4h67id` — no-VACUUM corruption.

All paths above are QA evidence, not user data. The individual runtime
directories are transient; the factual findings are preserved in this report.
