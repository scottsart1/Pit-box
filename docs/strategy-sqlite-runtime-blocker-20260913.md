# Observed SQLite runtime blocker

**Status: the same fixed capture corrupts the released application and strategy
candidate on workspace overlay storage; it passes the candidate on tmpfs.
The underlying cause remains unresolved. Do not treat this as a verified
production persistence fix or a completed Windows release gate.**

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

## Fixed-input source, runtime and filesystem controls

The follow-up controls used exactly one finalized synthetic Monza capture,
without regenerating telemetry between runs. Its 31,930 frames occupy
6,925,392 bytes and span 99.589909 seconds. The original recording was already
accelerated; replay at `--speed 1` preserves that roughly 100-second timeline.
No personal recording or database was used.

Input SHA-256:
`c713ee4391e5f22755c16c240a4ae7a75bfc5c5aa599d6c6bc625f98d88cc436`.

Each run started with fresh data, used the corrected source-app harness,
and completed before the next control began. The candidate source was fixed
at `97688bae9a70b57ed0f8901d36478f8722e0a0e7`; the released source remained
exactly `daa029ad41ce29c101254e54a53ade5dbaf8b852`.

| Run suffix | Source / runtime / output filesystem | Packets / reported drops | Observed result |
| --- | --- | ---: | --- |
| `y61elrwi` | Released / Python 3.12.14, SQLite 3.53.1 / overlay | 30,615 / 0 | Live state reached lap 25 and orderly shutdown completed, but final integrity check raised malformed database; independently copied DB also failed |
| `370luqge` | Candidate / Python 3.12.14, SQLite 3.53.1 / overlay | 24,324 / 5 | First malformed error 41 seconds after startup; session API 500; copied DB failed integrity |
| `q5v4g46h` | Candidate / stock Python 3.12.3, SQLite 3.45.1 / overlay | 24,222 / 13 | First malformed error 12 seconds after startup; session API 500 and failed shutdown; copied DB failed integrity |
| `r3voqicd` | Candidate / Python 3.12.14, SQLite 3.53.1 / tmpfs | 24,707 / 0 | Complete harness passed: 178 snapshots, zero strategy violations, catalog read, orderly shutdown, integrity `ok`, relaunch, retained catalog and sentinel |
| `hv3bblhb` | Same candidate / primary runtime / tmpfs, second fresh replay | 25,314 / 0 | Complete harness passed again: 171 snapshots, zero strategy violations, integrity `ok`, relaunch and retention |
| `sykq2wvi` | Same candidate / primary runtime / tmpfs, slower `0.5x` replay | 29,294 / 0 | Complete harness passed: 408 snapshots, zero strategy violations, integrity `ok`, relaunch and retention; still 2,636 input frames absent from parsed count |

The stock-Python control used a separate virtual environment pointing to the
same installed dependency directory. Python and SQLite changed together; this
is not an isolated SQLite-version test. The stock run's database header records
writer version 3.45.1, confirming that the alternative engine wrote it. This
old engine is a diagnostic control, not a proposed production downgrade.

The fixed-input comparison establishes that corruption is not exclusive to
the strategy changes or to the custom Python/SQLite versions. Changing only
the candidate run's output filesystem produced a complete pass and slightly
more received packets than the failing overlay candidate. The workspace and
`/tmp` share overlay storage configured with `fsync=volatile`; `/dev/shm` is a
separate tmpfs filesystem. These results support a storage-environment
association, not proof of a particular filesystem bug. Scheduling and UDP
delivery still vary: a fixed source capture does not imply that every frame
reaches the application's receive queue, and its reported drop counter does
not measure all possible kernel-level loss.

An offline audit successfully parsed every input frame. All 31,930 have the
same session UID, with no UID transitions or backward session-time transitions;
the application's assembler reports zero restarts. A session-counter reset
therefore does not explain the difference between sent and parsed counts.
The two passing tmpfs runs still lack 7,223 and 6,616 of the sent frames in
their parsed-packet counters. These passes validate the observed strategy and
persistence paths, not lossless reception of the whole input stream.
The single slower control extended replay to roughly 200 seconds and improved
reception to 29,294 frames, but 2,636 of 31,930 sent frames (8.26%) still did not
appear in the parsed count. Its zero reported drops do not invalidate that
independent comparison. The improvement supports a load/timing contribution;
it does not identify the exact loss mechanism or establish a production limit.

Capture retention is also distinct from parsing. Across all finalized files,
the first tmpfs run recorded 24,505 original input frames, 202 fewer than its
parsed count; a multiset comparison found no foreign frames. The second run
recorded 25,112 frames, also 202 fewer than its parsed count. Both report zero
capture queue drops and write errors. Each has an initial 64-frame capture,
then its main session capture. The lifecycle stops capture while it registers
the old file and starts the identified-session file; `submit()` declines
datagrams during that interval. This provides a concrete coverage limitation
to investigate separately from upstream delivery loss. No claim is made that
all missing frames have been localized to one layer.
The slower run captured 29,129 source frames with no foreign frames, leaving
165 fewer captured than parsed. Its capture queue-drop and write-error counters
were also zero. The input file hash was unchanged after all controls.

A separate deterministic probe then established the rotation loss itself:
with the real capture service, a fake catalog paused registration using an
asynchronous event. Three datagrams before rotation and three after it were
accepted and persisted; all 202 submitted during registration returned false
and were absent from the finalized captures. Queue-drop, write-error and
rotation-drop counters all remained zero. A control without rotation persisted
all 208 labeled datagrams. Both variants produced valid, cleanly closed capture
files. This proves a silent capture-rotation gap without claiming that it
accounts for the entire application's upstream receive deficit.

No application persistence change was made to obtain the tmpfs pass, and tmpfs
is not a durable production storage remedy. The practical release requirement
remains a passing installer/persistence gate on the supported Windows target.

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

In the fixed-input stock-runtime failure, the main file had 577 physical pages
but its header declared 582; the WAL was empty. Both a read-only DB-plus-sidecar
copy and an immutable main-file copy returned `SQLITE_CORRUPT`. Approximately
29.45 GB and 2.02 million inodes were free, so capacity exhaustion was not
supported by observation. The fixed-input released and candidate main files
had internally matching file/header page counts, but both still failed the
copied integrity check; page-count agreement alone is not a valid health test.

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
- `sqlite-fixed-input-evidence-hnma0cva` — copied fixed-input released and candidate corruption, with hashes and integrity results.
- `sqlite-stock-corruption-evidence-06v2uodq` — preserved stock-runtime originals, disposable query copies and forensic report.
- `pitbox-fixed-tmpfs-evidence-dfr0b4a8` — fixed-input tmpfs pass, copied stopped-process DB, complete snapshots, logs and summary.
- `pitbox-fixed-tmpfs-repeat-evidence-1k0rma_g` — second fixed-input tmpfs pass and stopped-process database copy.
- `pitbox-fixed-tmpfs-half-evidence-gniw9jpd` — slower fixed-input tmpfs pass, stopped-process database and independent frame-count audit.
- `capture-rotation-evidence-l874in66` — deterministic capture-rotation and no-rotation controls.
- `sqlite-fixedcapture-frame-audit.json` — input UID/type/parse audit and first tmpfs output-frame multiset comparison.

All paths above are QA evidence, not user data. The individual runtime
directories are transient; the factual findings are preserved in this report.
