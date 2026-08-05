# Pit Wall 4.2 operator and developer guide

This guide describes the behavior implemented in Pit Wall 4.2.0. It is the
practical companion to the release notes in the main README: use it to connect
a PS5, understand what Pit Wall records, reopen a session, run a replay, or
diagnose a local installation.

Pit Wall is local-first, but it is not a network appliance. It does not change
router settings, expose ports to the internet, or configure the Windows
firewall for you.

## Connect a PS5

1. Start Pit Wall with `start_pitwall.bat` and open **Connection**.
2. Copy the large recommended IPv4 address shown under **Enter this UDP IP on
   PS5**.
3. In the F1 telemetry settings on the PS5, enable UDP telemetry, enter that
   IPv4 address, use port `20777` unless you deliberately changed it, and select
   packet format `2026`.
4. Start or join an on-track session.
5. Wait for **Receiving telemetry**. **Listening** alone is not proof that the
   console is sending data.

The address entered on the console must identify the PC on the console's local
network:

| Address | Meaning | Use on PS5? |
|---|---|---|
| `127.0.0.1` | This PC talking to itself | No |
| `0.0.0.0` | A server bind setting meaning all local IPv4 adapters | No |
| `192.168.x.x`, `10.x.x.x`, or another private LAN IPv4 | The PC's reachable home-network address | Usually |
| `169.254.x.x` | A self-assigned address, commonly caused by missing DHCP/network connectivity | Usually no |
| VPN or virtual-adapter address | An address on a tunnel or virtual network | Only if the console can actually route to it |

Pit Wall ranks active private Ethernet and Wi-Fi interfaces ahead of loopback,
VPN, virtual, and link-local interfaces. A previously working adapter is also
favored. If DHCP changes the address of a pinned adapter in the saved network
profile, the Connection Center warns that the destination on the console must
be updated.

### Listening, receiving, stale, and error

- **Off** means the UDP listener is stopped.
- **Listening** means Windows allowed Pit Wall to bind the configured IPv4 and
  port. No valid F1 packet has arrived yet.
- **Receiving** means a valid 2026 F1 packet recently arrived.
- **Stale** means valid telemetry arrived before, but not within the current
  freshness window.
- **Error** means the bind or listener failed. The screen shows the specific
  error when one is available.

The listener starts automatically with the application. It can be stopped or
rebound from Connection. A successful bind change is saved in the local network
profile and restored on the next start; `.env` supplies the initial default and
is not rewritten.

If the listener remains at **Listening**:

1. Confirm that the PS5 destination matches the recommended PC IPv4, not
   `0.0.0.0` or `127.0.0.1`.
2. Confirm that the game is sending packet format `2026` to the same UDP port.
3. Start an on-track session; a menu screen may not emit the packet set Pit Wall
   needs.
4. Click **Run diagnostics**. The safe checks cover adapter discovery, bind
   availability, packet reception, format, and forwarding state.
5. Check `PitWallData\pitwall.log` and the Windows firewall. Pit Wall reports
   when the firewall could not be inspected; basic diagnostics do not require
   administrator rights.
6. Stop any other telemetry receiver that owns the same address and port, or
   choose a different port in both Pit Wall and the game.

### What packet health means

UDP has no connection handshake and no delivery acknowledgement. Packets may be
lost, duplicated, or reordered. Pit Wall therefore measures each packet type
separately, scoped by session UID and source endpoint. The Connection matrix
shows observed rates, age, counts, provisional gaps, confirmed loss, reordered
packets, and duplicates.

A provisional gap can still be filled by a late packet. It becomes confirmed
loss only after the reorder window. Frame wraparound, a new session UID, and an
F1 flashback are treated as discontinuities rather than enormous packet loss.

## Forward telemetry to another tool

Forwarding is application-level fan-out:

```text
PS5 -> Pit Wall on PC:20777
          +-> another local app on 127.0.0.1:20778
          +-> another LAN device on 192.168.1.50:20777
```

Add a target under **Connection > UDP forwarding**. Supply a stable ID, label,
IPv4/hostname, port, and either `all` or comma-separated numeric packet IDs.
Forwarding targets and their enabled state survive an application restart.

The forwarder sends the original datagram bytes. It does not parse and
reserialize them. DNS resolution and sends occur outside the receiver callback
through a bounded queue, so an unavailable sink does not block local telemetry
ingestion. On overload, old optional forward copies are dropped and counted.

Pit Wall rejects an unresolvable or non-IPv4 host, an invalid port,
`0.0.0.0` as a destination, a destination that loops back into the active Pit
Wall listener, duplicate enabled destinations, reserved addresses, and
broadcast/multicast destinations. A public IP requires explicit confirmation in
the form. Public forwarding is strongly discouraged and does not configure NAT,
TLS, authentication, or a firewall.

By default, only valid 2026 datagrams matching the packet filter are forwarded.
**Forward unknown but sane packets** also permits a datagram whose basic header
can be read but whose packet/version validation is not recognized. A datagram
with no readable header is never forwarded.

The counters report sends attempted successfully by the local UDP socket,
bytes, queue drops, socket errors, last success, and the resolved address. They
cannot prove that the downstream application accepted or parsed a packet; UDP
has no such acknowledgement.

## Keep the dashboard local or enable LAN access

The telemetry bind and browser bind are independent. The safe default is:

```env
PITWALL_UDP_BIND_HOST=0.0.0.0
PITWALL_UDP_PORT=20777
PITWALL_WEB_HOST=127.0.0.1
PITWALL_WEB_PORT=8000
PITWALL_WEB_LAN_ACCESS=false
```

This lets the PC receive console telemetry while keeping the dashboard private
to that PC. To open the dashboard from a trusted phone or tablet on the same
LAN, stop Pit Wall, configure all three values below, and restart:

```env
PITWALL_WEB_HOST=0.0.0.0
PITWALL_WEB_LAN_ACCESS=true
PITWALL_WEB_ACCESS_TOKEN=replace_with_at_least_16_random_characters
```

Then browse to the PC LAN address once with the token:

```text
http://192.168.1.42:8000/?access_token=YOUR_TOKEN
```

An authenticated GET sets an HTTP-only, same-site cookie, which also
authenticates the live WebSocket. For scripts, prefer either
`X-Pitwall-Token: YOUR_TOKEN` or `Authorization: Bearer YOUR_TOKEN` instead of a
query string. Keep the token secret: a query-string token may remain in browser
history or access logs.

When LAN access is enabled, non-loopback HTTP and WebSocket clients require the
token, cross-origin requests are rejected, and cookie-authenticated mutations
must be same-origin. Loopback access on the PC remains available without the
token. Pit Wall does not provide HTTPS or internet-facing hardening; do not
publish this port through a router.

Configuration validation intentionally refuses:

- a non-loopback `PITWALL_WEB_HOST` without
  `PITWALL_WEB_LAN_ACCESS=true`; and
- LAN access without a token of at least 16 characters.

## Capture, storage, and recovery

The default data root is `%USERPROFILE%\PitWallData` and can be changed with
`PITWALL_DATA_DIR`. Current 4.2 data is organized as follows:

```text
PitWallData/
  pitwall.sqlite3
  backups/
  captures/<year>/capture-<UTC timestamp>.pwcap
  traces/manifests/*.json
  traces/chunks/**/*.pwt
  track-models/**/*.pwm
  pitwall.log
```

SQLite stores searchable metadata, identities, laps, jobs, comparisons,
findings, and file manifests. High-rate typed arrays are stored in checksummed
trace chunks instead of one SQLite row per sample. Raw captures use independent
zlib-compressed blocks with frame CRCs and a content checksum. The automatic
file extension is `.pwcap`; compression is part of the file format and does not
require a `.zst` suffix.

### Current capture settings

```env
PITWALL_CAPTURE_MODE=balanced
PITWALL_RAW_CAPTURE=rolling
PITWALL_FIELD_TRACE_HZ=20
PITWALL_CAPTURE_MAX_GB=20
PITWALL_CAPTURE_MIN_FREE_GB=2
PITWALL_RETENTION_DAYS=90
PITWALL_CAPTURE_QUEUE_SIZE=8192
PITWALL_TRACE_INGEST_QUEUE_SIZE=512
PITWALL_TRACE_CACHE_MAX_MB=128
```

The implemented behavior is deliberately specific:

- `PITWALL_RAW_CAPTURE=off` disables the raw datagram archive.
- `rolling` and `full` currently both create one raw capture for the
  application run. Rotation and a size-bounded rolling window are not yet
  implemented.
- `PITWALL_CAPTURE_MODE` accepts `minimal`, `balanced`, or `full_fidelity` and
  is stored with session/capture provenance. In this release it does not select
  three materially different normalized trace policies.
- Player samples and event groups retain their useful received rate. Opponent
  high-rate sample groups are coalesced to `PITWALL_FIELD_TRACE_HZ`; only fields
  actually exposed by the game are stored.
- Capture, trace-ingest, forwarding, live-client, and analysis queues are
  bounded. Optional data can show a drop counter under load rather than
  blocking UDP reception.
- `PITWALL_CAPTURE_MAX_GB`, `PITWALL_CAPTURE_MIN_FREE_GB`, and
  `PITWALL_RETENTION_DAYS` drive storage warnings and the retention preview.
  They do not trigger automatic deletion.

Inspect storage accounting at `GET /api/v1/storage/status` and the exact
read-only cleanup proposal at `GET /api/v1/storage/retention/preview`. Starred
and currently recording sessions are protected. Cleanup remains a per-session
Library action with an impact preview and confirmation; there is no background
glob deletion.

### Crash recovery

Capture blocks and trace chunks are written to temporary files and atomically
published. On the next start:

- complete blocks in unfinished `.pwcap.tmp` files are recovered into a valid
  capture with `recovered=true` and `clean_close=false`;
- complete trace temporary files are promoted;
- invalid temporary files, missing chunks, and orphan chunks remain
  diagnosable in the log instead of preventing startup.

A recovered capture can contain a tail gap. It remains listable and replayable
up to its last complete block.

### Capture privacy

A private raw capture contains the original game datagrams and source/timing
metadata. Treat it as sensitive. Participant names or other identifiers may be
inside the packet payload.

The `anonymize` replay command below always removes the source endpoint and
redacts known transport metadata, but the CLI intentionally preserves datagram
payload bytes. It requires an explicit `--transport-only` acknowledgement and
labels the result `transport_metadata_only` with `payload_anonymized=false`; it
must not be described as participant-name anonymization.

## Inspect, validate, anonymize, and replay a capture

Run these commands from the project directory with the installed virtual
environment. Quote paths that contain spaces.

Show the block index, metadata, counts, checksums, close/recovery flags, and any
errors:

```powershell
.\.venv\Scripts\python.exe -m pitwall.replay inspect "C:\Users\YOU\PitWallData\captures\2026\capture-example.pwcap"
```

Fully validate headers, blocks, frames, CRCs, footer, and content checksum. The
command exits nonzero for an invalid capture:

```powershell
.\.venv\Scripts\python.exe -m pitwall.replay validate "C:\Users\YOU\PitWallData\captures\2026\capture-example.pwcap"
```

Create a transport-redacted copy without overwriting the source:

```powershell
.\.venv\Scripts\python.exe -m pitwall.replay anonymize "C:\Users\YOU\PitWallData\captures\2026\capture-example.pwcap" ".\data\capture-transport-redacted.pwcap" --transport-only
```

Replay the original bytes to the normal local Pit Wall listener at recorded
timing:

```powershell
.\.venv\Scripts\python.exe -m pitwall.replay play "C:\Users\YOU\PitWallData\captures\2026\capture-example.pwcap" --host 127.0.0.1 --port 20777 --speed 1
```

Use `--speed 4` for four-times speed. Start Pit Wall first, make sure its
listener is on the selected host/port, and stop live console telemetry so the
two sources do not mix. Consider setting `PITWALL_RAW_CAPTURE=off` before the
replay if you do not want the replayed datagrams recorded into a new archive.

The command-line `play` path currently supports host, port, and speed and
streams capture blocks without loading the complete recording into memory. The
Python `ReplayController` also supports pause, resume, packet stepping, stop,
and deterministic loss/duplicate/reorder/jitter injection for tests. Those
explicit fault/controller workflows build a bounded eager plan and are not
exposed as CLI flags in 4.2.

## Reopen and analyze a session

### Library

Library is backed by the versioned session catalog and works without the game
running. It can:

- page and filter saved sessions by text, session type, and starred state;
- open a session in Session Review or Field;
- star/unstar a session so retention previews protect it; and
- delete a completed session through a two-step impact preview.

Deletion shows the catalog rows and linked trace/capture files affected, then
uses a short-lived confirmation token. If the session changes after the
preview, deletion is refused and must be previewed again. A session still marked
`recording` cannot be deleted. Confirmed deletion is irreversible from the UI.

### Session Review

Session Review loads session metadata, participant identity revisions, quality,
lap validity/context, trace coverage, and derived counts. **Analyze / reprocess**
queues a durable deterministic job. A duplicate request for the same algorithm
bundle reuses the existing job.

The current reprocessor chooses the fastest valid traced player lap as its
reference, or the fastest valid traced field lap when no player lap is
available. It builds comparisons for the other valid traced laps and records
skipped laps with error codes. A job interrupted by shutdown is returned to the
queue on the next start. Refresh the session to see updated derived counts after
the job finishes.

### Lap Lab

1. Open a valid recorded lap from Session Review, a Field matrix cell, or the
   candidate selector.
2. Select one of the stored reference laps. Suggested references prioritize
   compatibility, trace completeness, performance, and context.
3. Review the compatibility badge. A caveated reference requires explicit
   confirmation and may disable prescriptive coaching.
4. Choose **Compare laps**. Pit Wall aligns the traces by distance, calculates
   cumulative/local timing, segment metrics, quality, confidence, and
   deterministic findings, then caches the result by its input hash.
5. Use the shared scrubber/playback controls, trace tabs, segment rail, map, and
   synchronized instruments. Selecting a segment or finding moves the shared
   distance cursor to its evidence.

The sign convention is fixed: a positive delta means the candidate reached
that distance later than the reference. A world-position map is shown when the
recorded traces contain enough position data, and can show only the available
side when the other side is missing. Aligned timing and control traces can
still work when the map is unavailable.

Opponent comparisons never assume player-level sensor parity. Missing brake,
steering, line, or other opponent inputs appear as **Unavailable** and disable
rules that require them. Pit Wall does not replace missing telemetry with zero.

## Availability, provenance, and confidence

The analysis contracts distinguish:

- **observed** — directly supplied by a valid packet;
- **derived** — calculated deterministically from observed inputs;
- **estimated** — produced by an explicit model and labeled as such;
- **stale** — previously observed but older than the field's freshness budget;
- **unavailable** — not supplied or not safely inferable.

Trace series carry units, availability, coverage, and masks. Derived results
carry algorithm versions, input checksums, compatibility, evidence IDs, and
confidence. The UI shows `Unavailable` rather than a magic zero. Coaching is
built from typed deterministic findings; a language model may phrase those
findings but is not the source of telemetry values or time deltas.

F1 flashbacks and restarts also retain provenance. Rewound samples are kept in
the raw archive, while superseded normalized lap batches are invalidated by
timeline epoch so pre- and post-flashback samples are not joined into a
synthetic lap.

## Versioned API quick reference

Interactive OpenAPI documentation is available locally at
`http://127.0.0.1:8000/docs`.

Network and diagnostics:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/network/interfaces` | Ranked local IPv4 interfaces |
| `GET` | `/api/v1/network/status` | Listener, source, packet, queue, and forwarder health |
| `POST` | `/api/v1/network/listener/start` | Validate and bind an IPv4/port |
| `POST` | `/api/v1/network/listener/stop` | Stop and drain the listener |
| `GET/POST` | `/api/v1/network/forwarders` | List or create forwarding targets |
| `PATCH/DELETE` | `/api/v1/network/forwarders/{id}` | Update or remove one target |
| `POST` | `/api/v1/network/diagnose` | Run safe local checks |

Saved telemetry and analysis:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/sessions` | Paginated Library summaries and filters |
| `GET/PATCH/DELETE` | `/api/v1/sessions/{id}` | Read metadata, edit name/tags/star, or preview/confirm deletion |
| `GET` | `/api/v1/sessions/{id}/laps` | Filterable recorded laps |
| `GET` | `/api/v1/sessions/{id}/quality` | Session and coverage quality |
| `POST` | `/api/v1/sessions/{id}/reprocess` | Queue/reuse a durable analysis job |
| `GET` | `/api/v1/laps/{id}/trace` | Field/range-selectable distance trace |
| `GET` | `/api/v1/laps/{id}/references` | Ranked stored reference laps |
| `POST` | `/api/v1/comparisons` | Create/reuse a lap comparison |
| `GET` | `/api/v1/comparisons/{id}` | Comparison metadata, segments, and findings |
| `GET` | `/api/v1/comparisons/{id}/trace` | Synchronized candidate/reference trace |
| `GET` | `/api/v1/sessions/{id}/field` | Classification and coverage summary |
| `GET` | `/api/v1/sessions/{id}/field/{pace,corners,positions,stints}` | Field analysis views |
| `GET` | `/api/v1/sessions/{id}/field/drivers/{car_id}` | One driver's coverage and strengths |
| `GET` | `/api/v1/storage/status` | Managed storage and disk accounting |
| `GET` | `/api/v1/storage/retention/preview` | Read-only exact retention candidates |
| `WS` | `/api/v1/live/ws` | Versioned topic subscription with bounded update rate |

The legacy dashboard, overlay, export, voice, strategy, setup, and health routes
remain available. New clients should use `/api/v1` contracts and treat opaque
IDs as strings.

## Database migrations, backups, and rollback

On startup, an existing database with unapplied 4.2 migrations is handled in
this order:

1. run SQLite `quick_check` and fall back to a deep integrity check if needed;
2. verify enough free disk exists for a backup plus migration/WAL headroom;
3. create and verify an online SQLite backup under
   `%USERPROFILE%\PitWallData\backups`;
4. apply each additive, checksummed migration in its own transaction;
5. record the version/checksum and run a post-migration `quick_check`.

A new empty database does not need a pre-migration backup. Applied migration
checksums are verified on later starts. The automatic backup covers SQLite, not
independent trace/capture files; 4.2 migrations do not delete those files.

There is no browser rollback button. To restore a specific verified backup,
first stop every Pit Wall process. Then run the administrative snippet below,
replacing the path with a file already inside the Pit Wall `backups` directory:

```powershell
$env:PITWALL_ROLLBACK_BACKUP = "C:\Users\YOU\PitWallData\backups\pitwall-pre-v42-YYYYMMDDTHHMMSS.sqlite3"
@'
import asyncio
import os
from pathlib import Path

from pitwall.config import settings
from pitwall.database import PitWallDatabase

async def restore():
    database = PitWallDatabase(settings.data_dir / "pitwall.sqlite3")
    safety_backup = await database.restore_backup(
        Path(os.environ["PITWALL_ROLLBACK_BACKUP"])
    )
    print(f"Restored. Safety backup of the replaced database: {safety_backup}")

asyncio.run(restore())
'@ | .\.venv\Scripts\python.exe -
```

The restore validates the selected backup and first creates a safety backup of
the database being replaced. Starting 4.2 after restoring an older schema will
migrate it forward again; launch the matching older application if the intent
is to remain on that version.

## Diagnostics and verification

Useful read-only checks while Pit Wall is running:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/health"
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/network/status"
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/network/diagnose" -Method Post
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/storage/status"
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/storage/retention/preview"
```

For a LAN-protected API request, add:

```powershell
-Headers @{ "X-Pitwall-Token" = "YOUR_TOKEN" }
```

The health response includes listener state, telemetry freshness, raw-capture
queue/counters, full-field archive quality, analysis-job queue state, trace
cache information, schema version, voice status, and model/provider status. The
rotating application log is `%USERPROFILE%\PitWallData\pitwall.log` by default.

Developer verification from the project root:

```powershell
.\.venv\Scripts\python.exe -m compileall -q .\src
.\.venv\Scripts\python.exe -m pitwall.replay --help
.\.venv\Scripts\python.exe -m pytest -q
```

Focused 4.2 checks:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_network_api_v42.py tests/test_network_profiles_v42.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_capture_replay_foundations.py tests/test_capture_service_v42.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_migrations_v42.py tests/test_storage_service_v42.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_analysis_api_v42.py tests/test_analysis_jobs_v42.py tests/test_field_service_v42.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_ui_connection_v42.py tests/test_ui_workspaces_v42.py
```

Automated fixtures verify packet gaps/reordering/flashbacks, byte-identical
forwarding, capture recovery, additive migrations, trace persistence,
distance-aligned comparison, field projections, APIs, and UI contracts. They do
not replace the final hardware shakedown with a PS5, the target Windows firewall
and network, real audio devices, or live provider credentials.

## Current 4.2 boundaries

- The receiver accepts the implemented F1 2026 packet format; another year is
  reported as unsupported.
- Opponent analysis is limited to fields the game actually supplies with
  adequate coverage.
- Raw-capture `rolling` and `full` do not yet have different rotation policies.
- Storage policy endpoints warn and preview; they never delete automatically.
- CLI anonymization does not scrub participant data embedded in packet payloads.
- CLI replay does not expose seek, pause/step, or fault-injection flags.
- UDP send counters cannot confirm downstream receipt.
- LAN dashboard access is intended for a trusted local network, not the public
  internet.
- Live PS5, firewall, microphone, speaker, and cloud-account behavior remain
  environment-specific shakedown items.
