"""Bounded, full-field session and lap assembly for normalized telemetry events.

The UDP receiver and raw capture writer deliberately live outside this module.
This boundary consumes already-normalized, typed events and turns them into
per-car/per-lap batches that can be handed to :class:`pitwall.trace_store.TraceStore`.
It never deletes or rewrites raw capture data.  A flashback instead closes the
current normalized branch, emits an invalidation record, and starts a new
``timeline_epoch`` so samples from opposite sides of a rewind cannot be mixed.

The assembler is synchronous and intentionally has no database, socket, or app
dependencies.  Sinks are injected callables and should enqueue work if their
implementation performs I/O.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Literal, Protocol, TypeAlias

from .identity import SessionCarIdentity, SessionIdentityRegistry, SessionKey

Availability: TypeAlias = Literal[
    "observed", "derived", "estimated", "stale", "unavailable"
]
Provenance: TypeAlias = Literal["observed", "derived", "estimated", "unavailable"]

_AVAILABILITY_VALUES = {"observed", "derived", "estimated", "stale", "unavailable"}
_PROVENANCE_VALUES = {"observed", "derived", "estimated", "unavailable"}
_EVENT_SAMPLE_GROUPS = frozenset(
    {"event", "events", "session", "lap", "history", "status", "damage"}
)
_UINT32_MAX = (1 << 32) - 1
_STRUCTURAL_FIELDS = frozenset(
    {
        "session_time_s",
        "monotonic_ns",
        "wall_ns",
        "frame_identifier",
        "overall_frame_identifier",
    }
)


def _require_finite(value: float, label: str) -> None:
    if not math.isfinite(float(value)):
        raise ValueError(f"{label} must be finite")


def _validate_car_index(value: int) -> None:
    if not 0 <= int(value) < 24:
        raise ValueError("car_index must be between 0 and 23")


@dataclass(frozen=True, slots=True)
class EventStamp:
    """Timing and identity shared by every normalized event."""

    session_uid: int | str
    frame_identifier: int
    overall_frame_identifier: int
    session_time_s: float
    monotonic_ns: int
    wall_ns: int

    def __post_init__(self) -> None:
        if not str(self.session_uid):
            raise ValueError("session_uid must not be blank")
        if self.frame_identifier < 0 or self.overall_frame_identifier < 0:
            raise ValueError("frame identifiers must be non-negative")
        if self.monotonic_ns < 0 or self.wall_ns < 0:
            raise ValueError("event clocks must be non-negative")
        _require_finite(self.session_time_s, "session_time_s")


@dataclass(frozen=True, slots=True)
class SessionEvent:
    """Session metadata or explicit evidence that the game restarted."""

    stamp: EventStamp
    track_id: int | None = None
    layout_signature: str | None = None
    session_type: int | None = None
    packet_format: int | None = None
    player_car_index: int | None = None
    restart_evidence: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.player_car_index is not None:
            _validate_car_index(self.player_car_index)


@dataclass(frozen=True, slots=True)
class ParticipantEvent:
    """One participant observation; conflicts create identity revisions."""

    stamp: EventStamp
    car_index: int
    values: Mapping[str, Any]

    def __post_init__(self) -> None:
        _validate_car_index(self.car_index)


@dataclass(frozen=True, slots=True)
class SampleEvent:
    """One normalized sample group for one car and lap.

    ``availability``, ``provenance``, ``units`` and ``freshness_ms`` are keyed
    by field name.  A scalar freshness budget applies to every field.  Event or
    status groups can set ``retain_all``; player samples are always retained up
    to the configured hard buffer bound.
    """

    stamp: EventStamp
    car_index: int
    lap_number: int
    sample_group: str
    values: Mapping[str, int | float | bool | None]
    availability: Mapping[str, Availability] = field(default_factory=dict)
    provenance: Mapping[str, Provenance] = field(default_factory=dict)
    units: Mapping[str, str] = field(default_factory=dict)
    freshness_ms: int | Mapping[str, int] = 250
    retain_all: bool = False

    def __post_init__(self) -> None:
        _validate_car_index(self.car_index)
        if self.lap_number < 0:
            raise ValueError("lap_number must be non-negative")
        if not self.sample_group.strip():
            raise ValueError("sample_group must not be blank")
        for name, value in self.availability.items():
            if value not in _AVAILABILITY_VALUES:
                raise ValueError(f"invalid availability for '{name}': {value}")
        for name, value in self.provenance.items():
            if value not in _PROVENANCE_VALUES:
                raise ValueError(f"invalid provenance for '{name}': {value}")
        for name, value in self.values.items():
            if name in _STRUCTURAL_FIELDS:
                raise ValueError(f"'{name}' is reserved event metadata")
            if value is not None and not isinstance(value, (bool, int, float)):
                raise TypeError(
                    f"sample field '{name}' must be numeric, boolean, or null"
                )
        budgets = (
            self.freshness_ms.values()
            if isinstance(self.freshness_ms, Mapping)
            else (self.freshness_ms,)
        )
        if any(int(value) <= 0 for value in budgets):
            raise ValueError("freshness budgets must be positive")


@dataclass(frozen=True, slots=True)
class LapEvent:
    """A completed-lap boundary for one car."""

    stamp: EventStamp
    car_index: int
    completed_lap_number: int
    next_lap_number: int | None = None
    lap_time_ms: int | None = None
    valid: bool = True
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_car_index(self.car_index)
        if self.completed_lap_number < 0:
            raise ValueError("completed_lap_number must be non-negative")
        if self.next_lap_number is not None and self.next_lap_number < 0:
            raise ValueError("next_lap_number must be non-negative")
        if self.lap_time_ms is not None and self.lap_time_ms < 0:
            raise ValueError("lap_time_ms must be non-negative")


@dataclass(frozen=True, slots=True)
class FlashbackEvent:
    """Evidence that active game time rewound to an earlier point."""

    stamp: EventStamp
    target_overall_frame_identifier: int
    target_session_time_s: float
    target_lap_number: int | None = None
    reason: str = "game_flashback"

    def __post_init__(self) -> None:
        if self.target_overall_frame_identifier < 0:
            raise ValueError("target_overall_frame_identifier must be non-negative")
        _require_finite(self.target_session_time_s, "target_session_time_s")
        if self.target_lap_number is not None and self.target_lap_number < 0:
            raise ValueError("target_lap_number must be non-negative")


NormalizedEvent: TypeAlias = (
    SessionEvent | ParticipantEvent | SampleEvent | LapEvent | FlashbackEvent
)


class BatchSink(Protocol):
    def __call__(self, batch: FinalizedLapBatch) -> None: ...


class InvalidationSink(Protocol):
    def __call__(self, invalidation: BranchInvalidation) -> None: ...


@dataclass(frozen=True, slots=True)
class TraceGroupBatch:
    """One trace-store-ready sample group within a finalized lap."""

    sample_group: str
    axis_field: str
    axis_unit: str
    samples: tuple[Mapping[str, int | float | bool | None], ...]
    field_metadata: Mapping[str, Mapping[str, Any]]
    received_samples: int
    accepted_samples: int
    coalesced_samples: int
    dropped_samples: int

    @property
    def sample_count(self) -> int:
        return len(self.samples)

    def append_to(self, trace_store: Any, session_car_id: str) -> int:
        """Append this group through the public ``TraceStore`` duck type."""

        return int(
            trace_store.append_samples(
                session_car_id,
                self.sample_group,
                self.samples,
                axis_field=self.axis_field,
                axis_unit=self.axis_unit,
                field_metadata=self.field_metadata,
            )
        )


@dataclass(frozen=True, slots=True)
class FinalizedLapBatch:
    """Immutable per-car/lap result emitted by the assembler."""

    batch_id: str
    lap_id: str
    session: SessionKey
    identity: SessionCarIdentity
    lap_number: int
    timeline_epoch: int
    groups: tuple[TraceGroupBatch, ...]
    complete: bool
    valid: bool | None
    invalidated: bool
    finalization_reason: str
    lap_time_ms: int | None
    context: Mapping[str, Any]
    first_overall_frame: int
    last_overall_frame: int
    started_session_time_s: float
    ended_session_time_s: float
    coverage_ratio: float
    quality_score: float

    @property
    def session_car_id(self) -> str:
        return self.identity.id

    @property
    def sample_count(self) -> int:
        return sum(group.sample_count for group in self.groups)

    def write_to_trace_store(
        self,
        trace_store: Any,
        *,
        manifest_id: str | None = None,
    ) -> Any:
        """Write the batch immediately and finalize a trace manifest.

        Invalidated branches are deliberately rejected.  Consumers that have
        already persisted an earlier branch should process
        :class:`BranchInvalidation` in their metadata catalog instead.
        """

        if self.invalidated:
            raise ValueError("cannot write an invalidated lap batch")
        if not self.groups:
            raise ValueError("cannot write an empty lap batch")
        for group in self.groups:
            group.append_to(trace_store, self.session_car_id)
        return trace_store.finalize_lap(
            self.lap_id,
            session_car_id=self.session_car_id,
            manifest_id=manifest_id,
        )


@dataclass(frozen=True, slots=True)
class BranchInvalidation:
    """Catalog instruction produced by a flashback.

    The criteria are authoritative even when some affected batches have fallen
    out of the bounded in-memory history.
    """

    session: SessionKey
    invalidated_timeline_epoch: int
    replacement_timeline_epoch: int
    target_overall_frame_identifier: int
    target_session_time_s: float
    target_lap_number: int | None
    affected_batch_ids: tuple[str, ...]
    reason: str
    raw_archive_preserved: bool = True


@dataclass(frozen=True, slots=True)
class AssemblyResult:
    finalized_batches: tuple[FinalizedLapBatch, ...] = ()
    invalidations: tuple[BranchInvalidation, ...] = ()
    identities: tuple[SessionCarIdentity, ...] = ()


@dataclass(slots=True)
class AssemblerCounters:
    events_received: int = 0
    sessions_started: int = 0
    session_uid_reuses: int = 0
    restarts: int = 0
    participant_observations: int = 0
    identity_revisions: int = 0
    samples_received: int = 0
    samples_accepted: int = 0
    samples_coalesced: int = 0
    samples_dropped: int = 0
    lap_batches_emitted: int = 0
    incomplete_laps: int = 0
    invalidated_laps: int = 0
    flashbacks: int = 0
    implicit_flashbacks: int = 0
    open_lap_evictions: int = 0
    live_group_evictions: int = 0
    event_history_drops: int = 0
    finalized_history_drops: int = 0
    sink_errors: int = 0


@dataclass(frozen=True, slots=True)
class FieldQuality:
    availability: Availability
    provenance: Provenance
    unit: str
    coverage_ratio: float
    last_age_ms: float | None
    freshness_ms: int


@dataclass(frozen=True, slots=True)
class GroupQuality:
    session_car_id: str
    car_index: int
    timeline_epoch: int
    lap_number: int
    sample_group: str
    is_player: bool
    status: str
    received_samples: int
    retained_samples: int
    coalesced_samples: int
    dropped_samples: int
    fields: Mapping[str, FieldQuality]


@dataclass(frozen=True, slots=True)
class AssemblerQualityReport:
    session: SessionKey | None
    timeline_epoch: int
    closed: bool
    open_laps: int
    current_cars: int
    counters: AssemblerCounters
    groups: tuple[GroupQuality, ...]


@dataclass(frozen=True, slots=True)
class LiveFieldValue:
    value: int | float | bool | None
    availability: Availability
    provenance: Provenance
    unit: str
    freshness_ms: int
    age_ms: float


@dataclass(frozen=True, slots=True)
class LiveGroupSnapshot:
    sample_group: str
    last_monotonic_ns: int
    fields: Mapping[str, LiveFieldValue]


@dataclass(frozen=True, slots=True)
class LiveCarSnapshot:
    identity: SessionCarIdentity
    timeline_epoch: int
    groups: Mapping[str, LiveGroupSnapshot]


@dataclass(frozen=True, slots=True)
class EventRecord:
    event_type: str
    session_id: str
    timeline_epoch: int
    overall_frame_identifier: int
    session_time_s: float


@dataclass(frozen=True, slots=True)
class _StoredSample:
    row: Mapping[str, int | float | bool | None]
    availability: Mapping[str, Availability]
    provenance: Mapping[str, Provenance]
    units: Mapping[str, str]
    freshness_ms: Mapping[str, int]
    monotonic_ns: int


class _GroupAccumulator:
    def __init__(self, sample_group: str) -> None:
        self.sample_group = sample_group
        self.samples: deque[_StoredSample] = deque()
        self.received = 0
        self.accepted = 0
        self.coalesced = 0
        self.dropped = 0
        self.bucket_anchor_ns: int | None = None

    def add(
        self,
        sample: _StoredSample,
        *,
        coalesce_interval_ns: int | None,
        max_samples: int,
    ) -> Literal["accepted", "coalesced"]:
        self.received += 1
        if (
            coalesce_interval_ns is not None
            and self.samples
            and self.bucket_anchor_ns is not None
            and sample.monotonic_ns < self.bucket_anchor_ns + coalesce_interval_ns
        ):
            if sample.monotonic_ns >= self.samples[-1].monotonic_ns:
                self.samples[-1] = sample
            self.coalesced += 1
            return "coalesced"
        self.samples.append(sample)
        self.accepted += 1
        self.bucket_anchor_ns = sample.monotonic_ns
        if len(self.samples) > max_samples:
            self.samples.popleft()
            self.dropped += 1
        return "accepted"


@dataclass(slots=True)
class _LapAccumulator:
    identity: SessionCarIdentity
    lap_number: int
    timeline_epoch: int
    created_order: int
    first_overall_frame: int
    last_overall_frame: int
    started_session_time_s: float
    ended_session_time_s: float
    groups: dict[str, _GroupAccumulator] = field(default_factory=dict)

    def observe_stamp(self, stamp: EventStamp) -> None:
        self.first_overall_frame = min(
            self.first_overall_frame, stamp.overall_frame_identifier
        )
        self.last_overall_frame = max(
            self.last_overall_frame, stamp.overall_frame_identifier
        )
        self.started_session_time_s = min(
            self.started_session_time_s, stamp.session_time_s
        )
        self.ended_session_time_s = max(self.ended_session_time_s, stamp.session_time_s)


@dataclass(frozen=True, slots=True)
class _LatestGroup:
    identity_id: str
    sample_group: str
    sample: _StoredSample


class _BoundedSessionIdentityRegistry(SessionIdentityRegistry):
    """Default registry variant with bounded diagnostic history.

    ``SessionIdentityRegistry`` owns the identity rules.  This subclass changes
    only the collection types/capacity used by a long-running assembler.
    Injected registries remain under their caller's lifecycle policy.
    """

    def __init__(self, *, max_identities: int, max_session_uids: int) -> None:
        super().__init__()
        self._history = deque(maxlen=max_identities)  # type: ignore[assignment]
        self._session_uid_order: deque[str] = deque()
        self._max_session_uids = max_session_uids

    def begin_session(
        self,
        game_session_uid: int | str,
        *,
        restart_evidence: bool = False,
    ) -> SessionKey:
        uid = str(game_session_uid)
        known = uid in self._last_epoch_by_uid
        session = super().begin_session(uid, restart_evidence=restart_evidence)
        if not known:
            self._session_uid_order.append(uid)
        while len(self._session_uid_order) > self._max_session_uids:
            oldest = self._session_uid_order.popleft()
            if self.session is None or oldest != self.session.game_session_uid:
                self._last_epoch_by_uid.pop(oldest, None)
        return session


def _worst_availability(values: Sequence[Availability]) -> Availability:
    if not values:
        return "unavailable"
    if all(value == "unavailable" for value in values):
        return "unavailable"
    for candidate in ("stale", "estimated", "derived", "observed"):
        if candidate in values:
            return candidate  # type: ignore[return-value]
    return "unavailable"


def _worst_provenance(values: Sequence[Provenance]) -> Provenance:
    if not values:
        return "unavailable"
    if all(value == "unavailable" for value in values):
        return "unavailable"
    for candidate in ("estimated", "derived", "observed"):
        if candidate in values:
            return candidate  # type: ignore[return-value]
    return "unavailable"


def _hash_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256(
        "\x1f".join(str(part) for part in parts).encode()
    ).hexdigest()[:24]
    return f"{prefix}_{digest}"


class SessionAssembler:
    """Assemble bounded, revision-safe, full-field lap traces."""

    def __init__(
        self,
        *,
        identity_registry: SessionIdentityRegistry | None = None,
        batch_sink: BatchSink | Callable[[FinalizedLapBatch], None] | None = None,
        invalidation_sink: (
            InvalidationSink | Callable[[BranchInvalidation], None] | None
        ) = None,
        field_trace_hz: float = 20.0,
        max_samples_per_group: int = 12_000,
        player_max_samples_per_group: int = 24_000,
        max_open_laps: int = 96,
        max_finalized_batches: int = 512,
        max_event_history: int = 2_048,
        max_live_groups: int = 768,
        max_groups_per_lap: int = 64,
        max_fields_per_sample: int = 256,
        max_identity_history: int = 2_048,
        max_session_uid_history: int = 1_024,
        max_session_metadata_fields: int = 128,
        rewind_tolerance_frames: int = 5,
        rewind_time_tolerance_s: float = 0.25,
    ) -> None:
        if not math.isfinite(field_trace_hz) or field_trace_hz < 0:
            raise ValueError("field_trace_hz must be finite and non-negative")
        positive_limits = {
            "max_samples_per_group": max_samples_per_group,
            "player_max_samples_per_group": player_max_samples_per_group,
            "max_open_laps": max_open_laps,
            "max_finalized_batches": max_finalized_batches,
            "max_event_history": max_event_history,
            "max_live_groups": max_live_groups,
            "max_groups_per_lap": max_groups_per_lap,
            "max_fields_per_sample": max_fields_per_sample,
            "max_identity_history": max_identity_history,
            "max_session_uid_history": max_session_uid_history,
            "max_session_metadata_fields": max_session_metadata_fields,
        }
        for name, value in positive_limits.items():
            if int(value) <= 0:
                raise ValueError(f"{name} must be positive")
        if rewind_tolerance_frames < 0 or rewind_time_tolerance_s < 0:
            raise ValueError("rewind tolerances must be non-negative")

        self.identity_registry = identity_registry or _BoundedSessionIdentityRegistry(
            max_identities=int(max_identity_history),
            max_session_uids=int(max_session_uid_history),
        )
        self.batch_sink = batch_sink
        self.invalidation_sink = invalidation_sink
        self.field_trace_hz = float(field_trace_hz)
        self.max_samples_per_group = int(max_samples_per_group)
        self.player_max_samples_per_group = int(player_max_samples_per_group)
        self.max_open_laps = int(max_open_laps)
        self.max_groups_per_lap = int(max_groups_per_lap)
        self.max_fields_per_sample = int(max_fields_per_sample)
        self.max_session_metadata_fields = int(max_session_metadata_fields)
        self.rewind_tolerance_frames = int(rewind_tolerance_frames)
        self.rewind_time_tolerance_s = float(rewind_time_tolerance_s)

        self._open: dict[tuple[str, int, int], _LapAccumulator] = {}
        self._finalized: deque[FinalizedLapBatch] = deque(maxlen=max_finalized_batches)
        self._event_history: deque[EventRecord] = deque(maxlen=max_event_history)
        self._latest_groups: dict[tuple[str, str], _LatestGroup] = {}
        self._latest_order: deque[tuple[str, str]] = deque(maxlen=max_live_groups)
        self._max_live_groups = int(max_live_groups)
        self._timeline_epoch = 0
        self._player_car_index: int | None = None
        self._session_metadata: dict[str, Any] = {}
        self._last_overall_frame: int | None = None
        self._last_session_time_s: float | None = None
        self._seen_uids: set[str] = set()
        self._seen_uid_order: deque[str] = deque()
        self._max_seen_uids = int(max_session_uid_history)
        self._creation_sequence = 0
        self._batch_sequence = 0
        self._closed = False
        self._shutdown_batches: tuple[FinalizedLapBatch, ...] = ()
        self._counters = AssemblerCounters()

    @property
    def session(self) -> SessionKey | None:
        return self.identity_registry.session

    @property
    def timeline_epoch(self) -> int:
        return self._timeline_epoch

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def finalized_batches(self) -> tuple[FinalizedLapBatch, ...]:
        return tuple(self._finalized)

    @property
    def event_history(self) -> tuple[EventRecord, ...]:
        return tuple(self._event_history)

    @property
    def counters(self) -> AssemblerCounters:
        return replace(self._counters)

    def _record(self, event: NormalizedEvent) -> None:
        if len(self._event_history) == self._event_history.maxlen:
            self._counters.event_history_drops += 1
        session = self.session
        if session is None:
            return
        self._event_history.append(
            EventRecord(
                event_type=type(event).__name__,
                session_id=session.id,
                timeline_epoch=self._timeline_epoch,
                overall_frame_identifier=event.stamp.overall_frame_identifier,
                session_time_s=event.stamp.session_time_s,
            )
        )

    def _remember_batch(self, batch: FinalizedLapBatch) -> None:
        if len(self._finalized) == self._finalized.maxlen:
            self._counters.finalized_history_drops += 1
        self._finalized.append(batch)
        self._counters.lap_batches_emitted += 1
        if not batch.complete:
            self._counters.incomplete_laps += 1
        if batch.invalidated:
            self._counters.invalidated_laps += 1
        if self.batch_sink is not None:
            try:
                self.batch_sink(batch)
            except Exception:
                self._counters.sink_errors += 1
                raise

    def _emit_invalidation(self, invalidation: BranchInvalidation) -> None:
        if self.invalidation_sink is not None:
            try:
                self.invalidation_sink(invalidation)
            except Exception:
                self._counters.sink_errors += 1
                raise

    def _start_session(
        self,
        session_uid: int | str,
        *,
        restart_evidence: bool = False,
    ) -> tuple[FinalizedLapBatch, ...]:
        uid = str(session_uid)
        current = self.session
        if (
            current is not None
            and current.game_session_uid == uid
            and not restart_evidence
        ):
            return ()
        reason = "session_restart" if restart_evidence else "session_changed"
        finalized = tuple(self._finalize_all(reason=reason, complete=False, valid=None))
        reused = uid in self._seen_uids
        if restart_evidence:
            self._counters.restarts += 1
        elif reused:
            self._counters.session_uid_reuses += 1
        self.identity_registry.begin_session(uid, restart_evidence=restart_evidence)
        if uid not in self._seen_uids:
            if len(self._seen_uid_order) >= self._max_seen_uids:
                oldest = self._seen_uid_order.popleft()
                self._seen_uids.discard(oldest)
            self._seen_uid_order.append(uid)
            self._seen_uids.add(uid)
        self._counters.sessions_started += 1
        self._timeline_epoch = 0
        self._last_overall_frame = None
        self._last_session_time_s = None
        self._latest_groups.clear()
        self._latest_order.clear()
        self._session_metadata = {}
        return finalized

    def _ensure_session(self, session_uid: int | str) -> tuple[FinalizedLapBatch, ...]:
        return self._start_session(session_uid)

    @staticmethod
    def _is_uint32_wrap(previous: int, current: int) -> bool:
        return previous >= _UINT32_MAX - 1_024 and current <= 1_024

    def _is_implicit_rewind(self, stamp: EventStamp) -> bool:
        if self._last_overall_frame is None or self._last_session_time_s is None:
            return False
        if self._is_uint32_wrap(
            self._last_overall_frame, stamp.overall_frame_identifier
        ):
            return False
        frame_rewind = self._last_overall_frame - stamp.overall_frame_identifier
        time_rewind = self._last_session_time_s - stamp.session_time_s
        return (
            frame_rewind > self.rewind_tolerance_frames
            and time_rewind > self.rewind_time_tolerance_s
        )

    def _update_last_stamp(self, stamp: EventStamp) -> None:
        if self._last_overall_frame is None or self._is_uint32_wrap(
            self._last_overall_frame, stamp.overall_frame_identifier
        ):
            self._last_overall_frame = stamp.overall_frame_identifier
        else:
            self._last_overall_frame = max(
                self._last_overall_frame, stamp.overall_frame_identifier
            )
        if self._last_session_time_s is None:
            self._last_session_time_s = stamp.session_time_s
        else:
            self._last_session_time_s = max(
                self._last_session_time_s, stamp.session_time_s
            )

    def _identity_for_sample(self, event: SampleEvent) -> SessionCarIdentity:
        values: dict[str, Any] = {}
        if self._player_car_index is not None:
            values["is_player"] = event.car_index == self._player_car_index
        return self.identity_registry.observe(
            event.car_index,
            event.stamp.frame_identifier,
            values,
        )

    def _stored_sample(self, event: SampleEvent) -> _StoredSample:
        if len(event.values) > self.max_fields_per_sample:
            raise ValueError(
                f"sample has {len(event.values)} fields; maximum is "
                f"{self.max_fields_per_sample}"
            )
        row: dict[str, int | float | bool | None] = {
            "session_time_s": float(event.stamp.session_time_s),
            "monotonic_ns": int(event.stamp.monotonic_ns),
            "wall_ns": int(event.stamp.wall_ns),
            "frame_identifier": int(event.stamp.frame_identifier),
            "overall_frame_identifier": int(event.stamp.overall_frame_identifier),
        }
        availability: dict[str, Availability] = {
            "session_time_s": "observed",
            "monotonic_ns": "derived",
            "wall_ns": "derived",
            "frame_identifier": "observed",
            "overall_frame_identifier": "observed",
        }
        provenance: dict[str, Provenance] = {
            "session_time_s": "observed",
            "monotonic_ns": "derived",
            "wall_ns": "derived",
            "frame_identifier": "observed",
            "overall_frame_identifier": "observed",
        }
        units = {
            "session_time_s": "s",
            "monotonic_ns": "ns",
            "wall_ns": "ns",
            "frame_identifier": "frame",
            "overall_frame_identifier": "frame",
        }
        freshness: dict[str, int] = {}
        scalar_freshness = (
            int(event.freshness_ms)
            if not isinstance(event.freshness_ms, Mapping)
            else 250
        )
        for name in row:
            freshness[name] = scalar_freshness

        for raw_name, raw_value in event.values.items():
            name = str(raw_name)
            if not name:
                raise ValueError("sample field names must not be blank")
            value: int | float | bool | None = raw_value
            if isinstance(value, float) and not math.isfinite(value):
                value = None
            state = event.availability.get(
                name, "unavailable" if value is None else "observed"
            )
            source = event.provenance.get(
                name,
                "unavailable"
                if state == "unavailable"
                else (
                    state
                    if state in {"observed", "derived", "estimated"}
                    else "observed"
                ),
            )
            if state == "unavailable":
                value = None
            row[name] = value
            availability[name] = state
            provenance[name] = source
            units[name] = str(event.units.get(name, ""))
            freshness[name] = int(
                event.freshness_ms.get(name, scalar_freshness)
                if isinstance(event.freshness_ms, Mapping)
                else scalar_freshness
            )
        return _StoredSample(
            row=row,
            availability=availability,
            provenance=provenance,
            units=units,
            freshness_ms=freshness,
            monotonic_ns=event.stamp.monotonic_ns,
        )

    def _remember_latest(
        self,
        identity: SessionCarIdentity,
        sample_group: str,
        sample: _StoredSample,
    ) -> None:
        key = (identity.id, sample_group)
        if key not in self._latest_groups:
            if len(self._latest_groups) >= self._max_live_groups:
                oldest = self._latest_order.popleft()
                self._latest_groups.pop(oldest, None)
                self._counters.live_group_evictions += 1
            self._latest_order.append(key)
        self._latest_groups[key] = _LatestGroup(identity.id, sample_group, sample)

    def _new_accumulator(
        self,
        identity: SessionCarIdentity,
        event: SampleEvent,
    ) -> tuple[_LapAccumulator, list[FinalizedLapBatch]]:
        finalized: list[FinalizedLapBatch] = []
        older = [
            accumulator
            for accumulator in self._open.values()
            if accumulator.identity.id == identity.id
            and accumulator.timeline_epoch == self._timeline_epoch
            and accumulator.lap_number < event.lap_number
        ]
        for accumulator in sorted(older, key=lambda item: item.lap_number):
            finalized.append(
                self._finalize_accumulator(
                    accumulator,
                    reason="inferred_lap_transition",
                    complete=True,
                    valid=None,
                )
            )
        if len(self._open) >= self.max_open_laps:
            oldest = min(self._open.values(), key=lambda item: item.created_order)
            finalized.append(
                self._finalize_accumulator(
                    oldest,
                    reason="open_lap_buffer_eviction",
                    complete=False,
                    valid=None,
                )
            )
            self._counters.open_lap_evictions += 1
        self._creation_sequence += 1
        accumulator = _LapAccumulator(
            identity=identity,
            lap_number=event.lap_number,
            timeline_epoch=self._timeline_epoch,
            created_order=self._creation_sequence,
            first_overall_frame=event.stamp.overall_frame_identifier,
            last_overall_frame=event.stamp.overall_frame_identifier,
            started_session_time_s=event.stamp.session_time_s,
            ended_session_time_s=event.stamp.session_time_s,
        )
        self._open[(identity.id, self._timeline_epoch, event.lap_number)] = accumulator
        return accumulator, finalized

    def _add_sample(self, event: SampleEvent) -> list[FinalizedLapBatch]:
        identity = self._identity_for_sample(event)
        sample = self._stored_sample(event)
        key = (identity.id, self._timeline_epoch, event.lap_number)
        finalized: list[FinalizedLapBatch] = []
        accumulator = self._open.get(key)
        if accumulator is None:
            accumulator, finalized = self._new_accumulator(identity, event)
        accumulator.observe_stamp(event.stamp)
        group = None
        if (
            event.sample_group in accumulator.groups
            or len(accumulator.groups) < self.max_groups_per_lap
        ):
            group = accumulator.groups.setdefault(
                event.sample_group,
                _GroupAccumulator(event.sample_group),
            )
        self._counters.samples_received += 1
        if group is None:
            self._counters.samples_dropped += 1
            return finalized
        # Whether this is the driver's own car is decided by the index the
        # session currently reports, not by a label attached to the identity
        # when it was first seen.
        #
        # The header carries a player_car_index before the participants packet
        # names anyone, so the first identity registered can be flagged as the
        # player and keep that flag after the real car is known. A real race
        # recorded exactly that: car 0 held the flag and the driver's actual
        # car 6 did not, so the driver was treated as just another car and
        # decimated to field_trace_hz while the phantom was retained in full.
        # The result was 403 samples per lap for the human against 1736 for the
        # phantom and up to 3213 for the AI cars — the driver's own laps were
        # the worst-sampled in the field, which is precisely the data corner
        # coaching and lap comparison are built on.
        is_player_now = (
            self._player_car_index is not None
            and event.car_index == self._player_car_index
        )
        retain_all = (
            is_player_now
            or identity.is_player
            or event.retain_all
            or event.sample_group.lower() in _EVENT_SAMPLE_GROUPS
        )
        interval_ns = None
        if not retain_all and self.field_trace_hz > 0:
            interval_ns = max(1, round(1_000_000_000 / self.field_trace_hz))
        max_samples = (
            self.player_max_samples_per_group
            if (is_player_now or identity.is_player)
            else self.max_samples_per_group
        )
        previous_drops = group.dropped
        disposition = group.add(
            sample,
            coalesce_interval_ns=interval_ns,
            max_samples=max_samples,
        )
        if disposition == "accepted":
            self._counters.samples_accepted += 1
        else:
            self._counters.samples_coalesced += 1
        self._counters.samples_dropped += group.dropped - previous_drops
        self._remember_latest(identity, event.sample_group, group.samples[-1])
        return finalized

    @staticmethod
    def _axis_for_samples(samples: Sequence[_StoredSample]) -> tuple[str, str]:
        names = set().union(*(sample.row.keys() for sample in samples))
        for name, unit in (
            ("lap_distance_m", "m"),
            ("distance_m", "m"),
            ("distance", "m"),
            ("d", "m"),
            ("session_time_s", "s"),
        ):
            if name in names and any(
                sample.row.get(name) is not None for sample in samples
            ):
                return name, unit
        return "session_time_s", "s"

    @staticmethod
    def _trace_group(group: _GroupAccumulator) -> TraceGroupBatch:
        retained = list(group.samples)
        axis_field, default_axis_unit = SessionAssembler._axis_for_samples(retained)
        retained.sort(
            key=lambda item: (
                float(item.row.get(axis_field) or 0.0),
                item.monotonic_ns,
            )
        )
        names = sorted(set().union(*(sample.row.keys() for sample in retained)))
        metadata: dict[str, dict[str, Any]] = {}
        for name in names:
            states = [
                sample.availability.get(name, "unavailable") for sample in retained
            ]
            sources = [
                sample.provenance.get(name, "unavailable") for sample in retained
            ]
            present = sum(sample.row.get(name) is not None for sample in retained)
            unit = next(
                (
                    sample.units.get(name, "")
                    for sample in retained
                    if sample.units.get(name)
                ),
                default_axis_unit if name == axis_field else "",
            )
            metadata[name] = {
                "unit": unit,
                "availability": _worst_availability(states),
                "provenance": _worst_provenance(sources),
                "coverage": present / len(retained) if retained else 0.0,
                "freshness_ms": max(
                    (sample.freshness_ms.get(name, 250) for sample in retained),
                    default=250,
                ),
            }
        rows = tuple(MappingProxyType(dict(sample.row)) for sample in retained)
        frozen_metadata = MappingProxyType(
            {
                name: MappingProxyType(dict(field_metadata))
                for name, field_metadata in metadata.items()
            }
        )
        return TraceGroupBatch(
            sample_group=group.sample_group,
            axis_field=axis_field,
            axis_unit=metadata.get(axis_field, {}).get("unit", default_axis_unit),
            samples=rows,
            field_metadata=frozen_metadata,
            received_samples=group.received,
            accepted_samples=group.accepted,
            coalesced_samples=group.coalesced,
            dropped_samples=group.dropped,
        )

    def _finalize_accumulator(
        self,
        accumulator: _LapAccumulator,
        *,
        reason: str,
        complete: bool,
        valid: bool | None,
        invalidated: bool = False,
        lap_time_ms: int | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> FinalizedLapBatch:
        key = (
            accumulator.identity.id,
            accumulator.timeline_epoch,
            accumulator.lap_number,
        )
        self._open.pop(key, None)
        groups = tuple(
            self._trace_group(group)
            for _, group in sorted(accumulator.groups.items())
            if group.samples
        )
        field_coverages = [
            float(meta.get("coverage", 0.0))
            for group in groups
            for name, meta in group.field_metadata.items()
            if name not in _STRUCTURAL_FIELDS
        ]
        coverage = (
            sum(field_coverages) / len(field_coverages) if field_coverages else 0.0
        )
        drops = sum(group.dropped_samples for group in groups)
        received = sum(group.received_samples for group in groups)
        retention_quality = 1.0 - (drops / received if received else 0.0)
        quality = max(0.0, min(1.0, coverage * retention_quality))
        self._batch_sequence += 1
        batch_id = _hash_id(
            "lb",
            accumulator.identity.session_id,
            accumulator.identity.id,
            accumulator.timeline_epoch,
            accumulator.lap_number,
            accumulator.first_overall_frame,
            accumulator.last_overall_frame,
            self._batch_sequence,
        )
        batch = FinalizedLapBatch(
            batch_id=batch_id,
            lap_id=_hash_id("lap", batch_id),
            session=self.session
            or SessionKey(accumulator.identity.session_id, accumulator.timeline_epoch),
            identity=accumulator.identity,
            lap_number=accumulator.lap_number,
            timeline_epoch=accumulator.timeline_epoch,
            groups=groups,
            complete=complete,
            valid=valid,
            invalidated=invalidated,
            finalization_reason=reason,
            lap_time_ms=lap_time_ms,
            context=MappingProxyType({**self._session_metadata, **dict(context or {})}),
            first_overall_frame=accumulator.first_overall_frame,
            last_overall_frame=accumulator.last_overall_frame,
            started_session_time_s=accumulator.started_session_time_s,
            ended_session_time_s=accumulator.ended_session_time_s,
            coverage_ratio=coverage,
            quality_score=quality,
        )
        self._remember_batch(batch)
        return batch

    def _finalize_all(
        self,
        *,
        reason: str,
        complete: bool,
        valid: bool | None,
        invalidated: bool = False,
    ) -> list[FinalizedLapBatch]:
        return [
            self._finalize_accumulator(
                accumulator,
                reason=reason,
                complete=complete,
                valid=valid,
                invalidated=invalidated,
            )
            for accumulator in sorted(
                self._open.values(), key=lambda item: item.created_order
            )
        ]

    def _observe_participant(
        self, event: ParticipantEvent
    ) -> tuple[SessionCarIdentity, list[FinalizedLapBatch]]:
        prior = self.identity_registry.current(event.car_index)
        values = dict(event.values)
        if self._player_car_index is not None:
            values["is_player"] = values.get(
                "is_player", event.car_index == self._player_car_index
            )
        identity = self.identity_registry.observe(
            event.car_index, event.stamp.frame_identifier, values
        )
        self._counters.participant_observations += 1
        finalized: list[FinalizedLapBatch] = []
        if prior is not None and prior.id != identity.id:
            self._counters.identity_revisions += 1
            affected = [
                accumulator
                for accumulator in self._open.values()
                if accumulator.identity.id == prior.id
            ]
            for accumulator in sorted(affected, key=lambda item: item.created_order):
                finalized.append(
                    self._finalize_accumulator(
                        accumulator,
                        reason="identity_changed",
                        complete=False,
                        valid=None,
                    )
                )
            stale_keys = [key for key in self._latest_groups if key[0] == prior.id]
            for key in stale_keys:
                self._latest_groups.pop(key, None)
                try:
                    self._latest_order.remove(key)
                except ValueError:
                    pass
        return identity, finalized

    def _complete_lap(self, event: LapEvent) -> list[FinalizedLapBatch]:
        identity = self.identity_registry.current(event.car_index)
        if identity is None:
            identity = self.identity_registry.observe(
                event.car_index,
                event.stamp.frame_identifier,
                {
                    "is_player": self._player_car_index is not None
                    and event.car_index == self._player_car_index
                },
            )
        affected = [
            accumulator
            for accumulator in self._open.values()
            if accumulator.identity.id == identity.id
            and accumulator.timeline_epoch == self._timeline_epoch
            and accumulator.lap_number == event.completed_lap_number
        ]
        return [
            self._finalize_accumulator(
                accumulator,
                reason="lap_complete",
                complete=True,
                valid=event.valid,
                lap_time_ms=event.lap_time_ms,
                context=event.context,
            )
            for accumulator in affected
        ]

    def _apply_flashback(
        self,
        *,
        target_overall_frame_identifier: int,
        target_session_time_s: float,
        target_lap_number: int | None,
        reason: str,
        implicit: bool,
    ) -> tuple[list[FinalizedLapBatch], BranchInvalidation]:
        if self.session is None:
            raise RuntimeError("cannot apply a flashback without an active session")
        invalidated_epoch = self._timeline_epoch
        open_batches = self._finalize_all(
            reason=reason,
            complete=False,
            valid=False,
            invalidated=True,
        )
        affected_ids = {batch.batch_id for batch in open_batches}
        rewritten: deque[FinalizedLapBatch] = deque(maxlen=self._finalized.maxlen)
        for batch in self._finalized:
            affected = (
                batch.session.id == self.session.id
                and batch.timeline_epoch == invalidated_epoch
                and not batch.invalidated
                and (
                    batch.last_overall_frame > target_overall_frame_identifier
                    or batch.ended_session_time_s > target_session_time_s
                )
            )
            if affected:
                batch = replace(
                    batch,
                    invalidated=True,
                    valid=False,
                    finalization_reason=reason,
                )
                affected_ids.add(batch.batch_id)
                self._counters.invalidated_laps += 1
            rewritten.append(batch)
        self._finalized = rewritten
        self._timeline_epoch += 1
        self._latest_groups.clear()
        self._latest_order.clear()
        self._last_overall_frame = target_overall_frame_identifier
        self._last_session_time_s = target_session_time_s
        self._counters.flashbacks += 1
        if implicit:
            self._counters.implicit_flashbacks += 1
        invalidation = BranchInvalidation(
            session=self.session,
            invalidated_timeline_epoch=invalidated_epoch,
            replacement_timeline_epoch=self._timeline_epoch,
            target_overall_frame_identifier=target_overall_frame_identifier,
            target_session_time_s=target_session_time_s,
            target_lap_number=target_lap_number,
            affected_batch_ids=tuple(sorted(affected_ids)),
            reason=reason,
        )
        self._emit_invalidation(invalidation)
        return open_batches, invalidation

    def consume(self, event: NormalizedEvent) -> AssemblyResult:
        """Consume one event and return emissions caused synchronously."""

        if self._closed:
            raise RuntimeError("session assembler is closed")
        if not isinstance(
            event,
            (SessionEvent, ParticipantEvent, SampleEvent, LapEvent, FlashbackEvent),
        ):
            raise TypeError(f"unsupported normalized event: {type(event).__name__}")
        self._counters.events_received += 1
        finalized: list[FinalizedLapBatch] = []
        invalidations: list[BranchInvalidation] = []
        identities: list[SessionCarIdentity] = []

        if isinstance(event, SessionEvent):
            current = self.session
            automatic_restart = (
                not event.restart_evidence
                and current is not None
                and current.game_session_uid == str(event.stamp.session_uid)
                and event.stamp.session_time_s <= 1.0
                and self._is_implicit_rewind(event.stamp)
            )
            transitioning = (
                current is None
                or current.game_session_uid != str(event.stamp.session_uid)
                or event.restart_evidence
                or automatic_restart
            )
            finalized.extend(
                self._start_session(
                    event.stamp.session_uid,
                    restart_evidence=event.restart_evidence or automatic_restart,
                )
            )
            if transitioning or event.player_car_index is not None:
                self._player_car_index = event.player_car_index
            incoming_metadata = {
                key: value
                for key, value in {
                    "track_id": event.track_id,
                    "layout_signature": event.layout_signature,
                    "session_type": event.session_type,
                    "packet_format": event.packet_format,
                    **dict(event.metadata),
                }.items()
                if value is not None
            }
            if len(set(self._session_metadata) | set(incoming_metadata)) > (
                self.max_session_metadata_fields
            ):
                raise ValueError(
                    "session metadata exceeds max_session_metadata_fields="
                    f"{self.max_session_metadata_fields}"
                )
            self._session_metadata.update(incoming_metadata)
            self._record(event)
            self._update_last_stamp(event.stamp)
            return AssemblyResult(tuple(finalized), (), ())

        finalized.extend(self._ensure_session(event.stamp.session_uid))
        if not isinstance(event, FlashbackEvent) and self._is_implicit_rewind(
            event.stamp
        ):
            batches, invalidation = self._apply_flashback(
                target_overall_frame_identifier=event.stamp.overall_frame_identifier,
                target_session_time_s=event.stamp.session_time_s,
                target_lap_number=(
                    event.lap_number
                    if isinstance(event, SampleEvent)
                    else (
                        event.completed_lap_number
                        if isinstance(event, LapEvent)
                        else None
                    )
                ),
                reason="implicit_frame_rewind",
                implicit=True,
            )
            finalized.extend(batches)
            invalidations.append(invalidation)

        if isinstance(event, ParticipantEvent):
            identity, batches = self._observe_participant(event)
            identities.append(identity)
            finalized.extend(batches)
            self._update_last_stamp(event.stamp)
        elif isinstance(event, SampleEvent):
            finalized.extend(self._add_sample(event))
            self._update_last_stamp(event.stamp)
        elif isinstance(event, LapEvent):
            finalized.extend(self._complete_lap(event))
            self._update_last_stamp(event.stamp)
        else:
            batches, invalidation = self._apply_flashback(
                target_overall_frame_identifier=event.target_overall_frame_identifier,
                target_session_time_s=event.target_session_time_s,
                target_lap_number=event.target_lap_number,
                reason=event.reason,
                implicit=False,
            )
            finalized.extend(batches)
            invalidations.append(invalidation)

        self._record(event)
        return AssemblyResult(
            finalized_batches=tuple(finalized),
            invalidations=tuple(invalidations),
            identities=tuple(identities),
        )

    @staticmethod
    def _field_quality(
        samples: Sequence[_StoredSample], name: str, now_ns: int
    ) -> FieldQuality:
        states = [sample.availability.get(name, "unavailable") for sample in samples]
        sources = [sample.provenance.get(name, "unavailable") for sample in samples]
        present_samples = [
            sample for sample in samples if sample.row.get(name) is not None
        ]
        latest = max(present_samples, key=lambda item: item.monotonic_ns, default=None)
        freshness = max(
            (sample.freshness_ms.get(name, 250) for sample in samples), default=250
        )
        age_ms = (
            max(0.0, (now_ns - latest.monotonic_ns) / 1_000_000)
            if latest is not None
            else None
        )
        availability = _worst_availability(states)
        if latest is not None and age_ms is not None and age_ms > freshness:
            availability = "stale"
        unit = next(
            (
                sample.units.get(name, "")
                for sample in samples
                if sample.units.get(name)
            ),
            "",
        )
        return FieldQuality(
            availability=availability,
            provenance=_worst_provenance(sources),
            unit=unit,
            coverage_ratio=len(present_samples) / len(samples) if samples else 0.0,
            last_age_ms=age_ms,
            freshness_ms=freshness,
        )

    def quality_report(
        self, *, now_monotonic_ns: int | None = None
    ) -> AssemblerQualityReport:
        """Return current coverage, freshness, coalescing, and drop counters."""

        now = int(
            now_monotonic_ns if now_monotonic_ns is not None else time.monotonic_ns()
        )
        groups: list[GroupQuality] = []
        for accumulator in sorted(
            self._open.values(), key=lambda item: item.created_order
        ):
            for group_name, group in sorted(accumulator.groups.items()):
                samples = list(group.samples)
                names = sorted(set().union(*(sample.row.keys() for sample in samples)))
                fields = {
                    name: self._field_quality(samples, name, now) for name in names
                }
                statuses = [item.availability for item in fields.values()]
                status = (
                    "stale"
                    if statuses
                    and all(item in {"stale", "unavailable"} for item in statuses)
                    else "partial"
                    if any(item == "unavailable" for item in statuses)
                    else "fresh"
                )
                groups.append(
                    GroupQuality(
                        session_car_id=accumulator.identity.id,
                        car_index=accumulator.identity.car_index,
                        timeline_epoch=accumulator.timeline_epoch,
                        lap_number=accumulator.lap_number,
                        sample_group=group_name,
                        is_player=accumulator.identity.is_player,
                        status=status,
                        received_samples=group.received,
                        retained_samples=len(samples),
                        coalesced_samples=group.coalesced,
                        dropped_samples=group.dropped,
                        fields=fields,
                    )
                )
        current_cars = sum(
            self.identity_registry.current(car_index) is not None
            for car_index in range(24)
        )
        return AssemblerQualityReport(
            session=self.session,
            timeline_epoch=self._timeline_epoch,
            closed=self._closed,
            open_laps=len(self._open),
            current_cars=current_cars,
            counters=self.counters,
            groups=tuple(groups),
        )

    def live_snapshot(
        self, *, now_monotonic_ns: int | None = None
    ) -> tuple[LiveCarSnapshot, ...]:
        """Latest bounded values for every currently observed session car."""

        now = int(
            now_monotonic_ns if now_monotonic_ns is not None else time.monotonic_ns()
        )
        if self.session is None:
            return ()
        identities = {
            identity.id: identity
            for car_index in range(24)
            if (identity := self.identity_registry.current(car_index)) is not None
            and identity.session_id == self.session.id
        }
        result: list[LiveCarSnapshot] = []
        for identity in sorted(identities.values(), key=lambda item: item.car_index):
            group_snapshots: dict[str, LiveGroupSnapshot] = {}
            for (identity_id, group_name), latest in self._latest_groups.items():
                if identity_id != identity.id:
                    continue
                sample = latest.sample
                values: dict[str, LiveFieldValue] = {}
                for name, value in sample.row.items():
                    budget = sample.freshness_ms.get(name, 250)
                    age_ms = max(0.0, (now - sample.monotonic_ns) / 1_000_000)
                    state = sample.availability.get(name, "unavailable")
                    if value is not None and age_ms > budget:
                        state = "stale"
                    values[name] = LiveFieldValue(
                        value=value,
                        availability=state,
                        provenance=sample.provenance.get(name, "unavailable"),
                        unit=sample.units.get(name, ""),
                        freshness_ms=budget,
                        age_ms=age_ms,
                    )
                group_snapshots[group_name] = LiveGroupSnapshot(
                    sample_group=group_name,
                    last_monotonic_ns=sample.monotonic_ns,
                    fields=values,
                )
            result.append(
                LiveCarSnapshot(
                    identity=identity,
                    timeline_epoch=self._timeline_epoch,
                    groups=group_snapshots,
                )
            )
        return tuple(result)

    def finalize_current_session(
        self, *, reason: str = "session_finalize"
    ) -> tuple[FinalizedLapBatch, ...]:
        if self._closed:
            return ()
        return tuple(self._finalize_all(reason=reason, complete=False, valid=None))

    def shutdown(self) -> tuple[FinalizedLapBatch, ...]:
        """Finalize all open buffers once and reject subsequent events."""

        if self._closed:
            return self._shutdown_batches
        self._shutdown_batches = tuple(
            self._finalize_all(reason="shutdown", complete=False, valid=None)
        )
        self._closed = True
        return self._shutdown_batches


__all__ = [
    "AssemblerCounters",
    "AssemblerQualityReport",
    "AssemblyResult",
    "Availability",
    "BranchInvalidation",
    "EventRecord",
    "EventStamp",
    "FieldQuality",
    "FinalizedLapBatch",
    "FlashbackEvent",
    "GroupQuality",
    "LapEvent",
    "LiveCarSnapshot",
    "LiveFieldValue",
    "LiveGroupSnapshot",
    "NormalizedEvent",
    "ParticipantEvent",
    "Provenance",
    "SampleEvent",
    "SessionAssembler",
    "SessionEvent",
    "TraceGroupBatch",
]
