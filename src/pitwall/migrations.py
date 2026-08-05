from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Migration:
    """One additive, checksummed SQLite migration."""

    version: int
    app_version: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        payload = "\n-- statement --\n".join(
            statement.strip() for statement in self.statements
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


V4_2_CATALOG = Migration(
    version=4200,
    app_version="4.2.0",
    statements=(
        """
        CREATE TABLE IF NOT EXISTS recorded_sessions (
            id TEXT PRIMARY KEY,
            legacy_session_uid INTEGER,
            game_session_uid TEXT,
            restart_epoch INTEGER NOT NULL DEFAULT 0,
            track_id INTEGER,
            track_layout_signature TEXT,
            session_type TEXT,
            mode_profile TEXT,
            started_at TEXT,
            ended_at TEXT,
            status TEXT NOT NULL DEFAULT 'recording',
            packet_format INTEGER,
            capture_mode TEXT NOT NULL DEFAULT 'balanced',
            starred INTEGER NOT NULL DEFAULT 0 CHECK(starred IN (0, 1)),
            display_name TEXT,
            tags_json TEXT NOT NULL DEFAULT '[]',
            quality_score REAL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(game_session_uid, restart_epoch)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS session_cars (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES recorded_sessions(id) ON DELETE CASCADE,
            car_index INTEGER NOT NULL,
            identity_revision INTEGER NOT NULL DEFAULT 0,
            driver_id INTEGER,
            network_id TEXT,
            display_name TEXT,
            anonymized_name TEXT,
            race_number INTEGER,
            team_id INTEGER,
            nationality_id INTEGER,
            is_ai INTEGER,
            is_player INTEGER,
            first_frame INTEGER,
            last_frame INTEGER,
            change_reason TEXT,
            identity_confidence REAL,
            availability_mask BLOB,
            UNIQUE(session_id, car_index, identity_revision)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS recorded_laps (
            id TEXT PRIMARY KEY,
            session_car_id TEXT NOT NULL REFERENCES session_cars(id) ON DELETE CASCADE,
            legacy_lap_id INTEGER,
            lap_number INTEGER NOT NULL,
            timeline_epoch INTEGER NOT NULL DEFAULT 0,
            lap_time_ms INTEGER,
            valid INTEGER NOT NULL DEFAULT 0 CHECK(valid IN (0, 1)),
            invalid_reason_mask INTEGER NOT NULL DEFAULT 0,
            started_game_ms INTEGER,
            ended_game_ms INTEGER,
            tyre_compound TEXT,
            tyre_age_laps INTEGER,
            fuel_start_kg REAL,
            fuel_end_kg REAL,
            weather_class TEXT,
            pit_context INTEGER NOT NULL DEFAULT 0,
            flag_context INTEGER NOT NULL DEFAULT 0,
            coverage_ratio REAL,
            quality_score REAL,
            trace_manifest_id TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(session_car_id, lap_number, timeline_epoch)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS trace_manifests (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES recorded_sessions(id) ON DELETE CASCADE,
            session_car_id TEXT REFERENCES session_cars(id) ON DELETE CASCADE,
            lap_id TEXT REFERENCES recorded_laps(id) ON DELETE CASCADE,
            encoding_version INTEGER NOT NULL,
            axis_type TEXT NOT NULL,
            field_mask BLOB NOT NULL,
            sample_count INTEGER NOT NULL,
            coverage_ratio REAL NOT NULL,
            checksum TEXT,
            state TEXT NOT NULL DEFAULT 'ready',
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS trace_chunks (
            id TEXT PRIMARY KEY,
            manifest_id TEXT NOT NULL REFERENCES trace_manifests(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            relative_path TEXT NOT NULL,
            start_axis REAL NOT NULL,
            end_axis REAL NOT NULL,
            sample_count INTEGER NOT NULL,
            byte_count INTEGER NOT NULL,
            checksum TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'ready',
            UNIQUE(manifest_id, ordinal),
            UNIQUE(relative_path)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS raw_captures (
            id TEXT PRIMARY KEY,
            session_id TEXT REFERENCES recorded_sessions(id) ON DELETE SET NULL,
            relative_path TEXT NOT NULL UNIQUE,
            format_version INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            byte_count INTEGER NOT NULL DEFAULT 0,
            packet_count INTEGER NOT NULL DEFAULT 0,
            checksum TEXT,
            clean_close INTEGER NOT NULL DEFAULT 0,
            recovered INTEGER NOT NULL DEFAULT 0,
            privacy_mode TEXT NOT NULL DEFAULT 'private',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS track_models (
            id TEXT PRIMARY KEY,
            track_id INTEGER NOT NULL,
            layout_signature TEXT NOT NULL,
            model_version INTEGER NOT NULL,
            algorithm_version TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            length_m REAL NOT NULL,
            quality_score REAL NOT NULL,
            checksum TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0, 1)),
            created_at TEXT NOT NULL,
            UNIQUE(track_id, layout_signature, model_version)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS segment_models (
            id TEXT PRIMARY KEY,
            track_model_id TEXT NOT NULL REFERENCES track_models(id),
            version INTEGER NOT NULL,
            source TEXT NOT NULL,
            checksum TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0, 1)),
            created_at TEXT NOT NULL,
            UNIQUE(track_model_id, version)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS segments (
            id TEXT PRIMARY KEY,
            segment_model_id TEXT NOT NULL REFERENCES segment_models(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            label TEXT NOT NULL,
            start_m REAL NOT NULL,
            end_m REAL NOT NULL,
            phase_json TEXT NOT NULL,
            direction TEXT,
            confidence REAL NOT NULL,
            UNIQUE(segment_model_id, ordinal)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS comparisons (
            id TEXT PRIMARY KEY,
            candidate_lap_id TEXT NOT NULL REFERENCES recorded_laps(id) ON DELETE CASCADE,
            reference_kind TEXT NOT NULL,
            reference_key TEXT NOT NULL,
            compatibility_class TEXT NOT NULL,
            compatibility_json TEXT NOT NULL,
            track_model_id TEXT REFERENCES track_models(id),
            segment_model_id TEXT REFERENCES segment_models(id),
            algorithm_bundle TEXT NOT NULL,
            input_hash TEXT NOT NULL UNIQUE,
            lap_delta_ms INTEGER,
            coverage_ratio REAL NOT NULL,
            quality_score REAL NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS segment_metrics (
            comparison_id TEXT NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
            segment_id TEXT NOT NULL REFERENCES segments(id),
            metric_key TEXT NOT NULL,
            candidate_value REAL,
            reference_value REAL,
            delta_value REAL,
            unit TEXT NOT NULL,
            availability TEXT NOT NULL,
            confidence REAL NOT NULL,
            evidence_json TEXT NOT NULL,
            PRIMARY KEY(comparison_id, segment_id, metric_key)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS findings (
            id TEXT PRIMARY KEY,
            comparison_id TEXT NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
            segment_id TEXT REFERENCES segments(id),
            finding_type TEXT NOT NULL,
            rank INTEGER NOT NULL,
            measured_loss_ms INTEGER,
            attributed_low_ms INTEGER,
            attributed_high_ms INTEGER,
            confidence REAL NOT NULL,
            repeatability REAL,
            opportunity_score REAL NOT NULL,
            facts_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            action_key TEXT NOT NULL,
            algorithm_version TEXT NOT NULL,
            UNIQUE(comparison_id, rank)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS network_profiles (
            id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            adapter_id TEXT,
            last_ipv4 TEXT,
            bind_host TEXT NOT NULL,
            udp_port INTEGER NOT NULL CHECK(udp_port BETWEEN 1 AND 65535),
            pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0, 1)),
            last_working_at TEXT,
            config_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS forward_targets (
            id TEXT PRIMARY KEY,
            profile_id TEXT REFERENCES network_profiles(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
            host TEXT NOT NULL,
            port INTEGER NOT NULL CHECK(port BETWEEN 1 AND 65535),
            packet_filter_json TEXT NOT NULL DEFAULT '"all"',
            forward_unknown INTEGER NOT NULL DEFAULT 0 CHECK(forward_unknown IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(profile_id, host, port)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS analysis_jobs (
            id TEXT PRIMARY KEY,
            session_id TEXT REFERENCES recorded_sessions(id) ON DELETE CASCADE,
            job_kind TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            state TEXT NOT NULL,
            progress REAL NOT NULL DEFAULT 0,
            error_code TEXT,
            error_detail TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            UNIQUE(job_kind, input_hash)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            subject_id TEXT,
            detail_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_recorded_sessions_library ON recorded_sessions(track_id, started_at DESC, session_type, starred)",
        "CREATE INDEX IF NOT EXISTS idx_session_cars_session_player ON session_cars(session_id, is_player, car_index)",
        "CREATE INDEX IF NOT EXISTS idx_recorded_laps_car_valid_time ON recorded_laps(session_car_id, valid, lap_time_ms)",
        "CREATE INDEX IF NOT EXISTS idx_trace_chunks_manifest ON trace_chunks(manifest_id, ordinal)",
        "CREATE INDEX IF NOT EXISTS idx_comparisons_candidate ON comparisons(candidate_lap_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_findings_comparison_rank ON findings(comparison_id, rank, finding_type)",
        "CREATE INDEX IF NOT EXISTS idx_analysis_jobs_state ON analysis_jobs(state, created_at)",
    ),
)


V4_2_COMPARISON_RESULTS = Migration(
    version=4201,
    app_version="4.2.0",
    statements=(
        """
        CREATE TABLE IF NOT EXISTS comparison_segment_results (
            comparison_id TEXT NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            segment_key TEXT NOT NULL,
            label TEXT NOT NULL,
            start_m REAL NOT NULL,
            end_m REAL NOT NULL,
            delta_s REAL,
            coverage_ratio REAL NOT NULL,
            metrics_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(comparison_id, ordinal),
            UNIQUE(comparison_id, segment_key)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_comparison_segments_key ON comparison_segment_results(comparison_id, segment_key)",
    ),
)


V4_2_ARCHIVE_PROVENANCE = Migration(
    version=4202,
    app_version="4.2.0",
    statements=(
        """
        CREATE TABLE IF NOT EXISTS full_field_lap_batches (
            batch_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES recorded_sessions(id) ON DELETE CASCADE,
            lap_id TEXT NOT NULL REFERENCES recorded_laps(id) ON DELETE CASCADE,
            timeline_epoch INTEGER NOT NULL,
            first_overall_frame INTEGER NOT NULL,
            last_overall_frame INTEGER NOT NULL,
            started_session_time_s REAL NOT NULL,
            ended_session_time_s REAL NOT NULL,
            finalization_reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_field_batches_timeline ON full_field_lap_batches(session_id, timeline_epoch, last_overall_frame, ended_session_time_s)",
        "CREATE INDEX IF NOT EXISTS idx_field_batches_lap ON full_field_lap_batches(lap_id)",
    ),
)


MIGRATIONS: tuple[Migration, ...] = (
    V4_2_CATALOG,
    V4_2_COMPARISON_RESULTS,
    V4_2_ARCHIVE_PROVENANCE,
)
LATEST_SCHEMA_VERSION = MIGRATIONS[-1].version
