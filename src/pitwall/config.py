from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="PITWALL_",
        extra="ignore",
        case_sensitive=False,
    )

    # Provider routing. Audio remains OpenAI-backed unless the user explicitly
    # changes the STT/TTS implementation in a future release.
    # Race engineer generation is OpenAI-only. Deterministic local fast paths
    # handle simple telemetry questions; model-backed calls use the Responses API.
    llm_provider: str = "openai"
    llm_fallback_provider: str = "none"

    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="OPENAI_API_KEY",
    )
    # ``model`` is the deep-reasoning model: strategy, undercut maths, what-ifs.
    # ``fast_model`` answers ordinary radio questions. The bare "gpt-5.6" alias
    # resolves to the flagship Sol tier, so routing every "what's my fuel"
    # through it paid frontier prices for a lookup; Luna is the same family at a
    # fraction of the cost and is more than capable of narrating tool output.
    model: str = "gpt-5.6-sol"
    fast_model: str = "gpt-5.6-luna"
    tier_routing_enabled: bool = True
    reasoning_effort: str = "low"
    deep_reasoning_effort: str = "high"
    openai_timeout_s: float = 30.0

    deepseek_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="DEEPSEEK_API_KEY",
    )
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_fast_model: str = "deepseek-v4-pro"
    deepseek_deep_model: str = "deepseek-v4-pro"
    deepseek_thinking_effort: str = "high"
    deepseek_timeout_s: float = 30.0
    deepseek_max_tool_rounds: int = 4
    deepseek_strict_tools: bool = False
    llm_failure_cooldown_s: float = 20.0
    llm_normal_deadline_s: float = 12.0
    llm_deep_deadline_s: float = 25.0
    llm_shakedown_timeout_s: float = 20.0
    llm_compare_enabled: bool = False
    llm_compare_cooldown_s: float = 30.0
    openai_deep_max_output_tokens: int = 2200
    openai_deep_retry_max_output_tokens: int = 6000
    deepseek_deep_max_tokens: int = 2200
    deepseek_deep_retry_max_tokens: int = 6000

    tts_model: str = "gpt-4o-mini-tts"
    stt_model: str = "gpt-4o-mini-transcribe"
    voice: str = "coral"

    # Speech-to-speech radio. The Realtime API removes the
    # transcribe -> reason -> synthesise chain, so the engineer can be
    # interrupted mid-sentence and answers without the acknowledgement clip that
    # exists only to mask chain latency. The session is opened when the driver
    # starts talking and closed after a pause, because billing is per second of
    # audio in the session, not per exchange.
    voice_realtime_enabled: bool = False
    realtime_model: str = "gpt-realtime-2.1-mini"
    realtime_voice: str = "coral"
    # Seconds of silence after the last exchange before the session is closed.
    realtime_idle_timeout_s: float = 25.0
    # Hard ceiling on one continuous session, so a stuck session cannot bill
    # indefinitely.
    realtime_max_session_s: float = 300.0
    realtime_silence_ms: int = 520
    realtime_prefix_padding_ms: int = 300
    realtime_noise_reduction: str = "near_field"  # near_field | far_field | off
    realtime_max_output_tokens: int = 700
    realtime_speed: float = 1.05

    # ``PITWALL_UDP_BIND_HOST`` is the unambiguous 4.2 name.  Keep accepting
    # PITWALL_UDP_HOST so an upgrade never strands a working console setup.
    udp_host: str = Field(
        default="0.0.0.0",
        validation_alias=AliasChoices("PITWALL_UDP_BIND_HOST", "PITWALL_UDP_HOST"),
    )
    udp_port: int = 20777
    disconnect_after_s: float = 3.0
    web_host: str = "127.0.0.1"
    web_port: int = 8000
    web_lan_access: bool = False
    web_access_token: SecretStr | None = None

    # Capture, trace, analysis and live-delivery budgets.  Environment values
    # are installation defaults; mutable network/retention profiles are stored
    # separately and never rewrite .env.
    capture_mode: str = "balanced"  # minimal | balanced | full_fidelity
    raw_capture: str = "rolling"  # off | rolling | full
    field_trace_hz: int = 20
    capture_max_gb: float = 20.0
    capture_min_free_gb: float = 2.0
    retention_days: int = 90
    trace_chunk_seconds: float = 30.0
    analysis_workers: int = 2
    live_ws_max_hz: int = 10
    forward_queue_size: int = 4096
    capture_queue_size: int = 8192
    packet_reorder_window: int = 8
    packet_loss_confirm_ms: int = 500
    native_voice: bool = True
    audio_sample_rate: int = 16000
    audio_device: str | int | None = None
    audio_min_rms: float = 120.0
    ptt_mask: int = 0
    ptt_min_hold_ms: int = 200
    ptt_debounce_ms: int = 150
    ptt_max_recording_s: float = 15.0
    # UDP Action packets are edge-like on some controller/game paths. Pit Wall
    # therefore never treats unrelated button packets as a release. A recording
    # ends on a trustworthy explicit release, post-speech silence, or the hard cap.
    ptt_release_mode: str = "explicit_or_silence"
    ptt_release_watchdog_s: float = 0.0
    ptt_release_ignore_ms: int = 120
    ptt_silence_release_s: float = 2.20
    # Shortest recording that post-speech silence may end. Below this the driver
    # is treated as still speaking, whatever the silence threshold says.
    ptt_min_speech_clip_s: float = 0.60
    ptt_speech_rms: float = 150.0
    ptt_release_tail_s: float = 0.20

    # Hands-free radio. The microphone is gated locally by a lightweight RMS
    # voice-activity detector; only bounded speech candidates are uploaded to
    # OpenAI transcription. Prefix-only matching prevents ordinary mentions of
    # the engineer name from opening the radio. L3 / UDP Action 1 remains a
    # fully supported fallback and its saved binding is never changed.
    wake_enabled: bool = True
    wake_phrase: str = "mark"
    wake_aliases: str = "marc,hey mark,mark radio,hey marc"
    wake_preroll_s: float = 0.80
    wake_speech_rms: float = 180.0
    # The configured RMS is a ceiling, not a hard floor. Pit Wall continuously
    # estimates room/microphone noise and lowers the speech threshold in quiet
    # conditions so a soft-spoken "Mark" is still captured.
    wake_min_speech_rms: float = 75.0
    wake_noise_multiplier: float = 1.45
    wake_noise_margin_rms: float = 18.0
    wake_clip_min_rms: float = 55.0
    wake_start_blocks: int = 2
    wake_silence_s: float = 0.60
    wake_min_utterance_s: float = 0.35
    wake_max_utterance_s: float = 15.0
    wake_arm_timeout_s: float = 6.0
    wake_tts_cooldown_s: float = 1.50
    wake_block_ms: int = 30

    # Perceived-latency controls. Acknowledgements are cached once and played
    # while the model works; native TTS streams raw PCM to the output device.
    voice_ack_enabled: bool = True
    voice_stream_tts: bool = True
    voice_clip_queue_size: int = 2

    data_dir: Path = Path.home() / "PitWallData"
    open_browser: bool = True
    # Housekeeping. 60 Hz lap traces and pre-gating strategy snapshots dominate
    # database size; the reference lap for each track is always preserved.
    db_maintenance_on_start: bool = True
    db_keep_trace_sessions: int = 12
    # Logs are written to data_dir/pitwall.log so a session can be diagnosed
    # after the console window has closed.
    log_level: str = "info"
    log_max_bytes: int = 2_000_000
    log_backup_count: int = 3

    proactive_enabled: bool = True
    proactive_cadence_laps: int = 2
    proactive_min_interval_s: float = 25.0
    # Important calls (a rival stopping, a battery warning, a tyre limit) are
    # only useful while they are still true, so they are spaced more tightly
    # than routine progress updates.
    proactive_important_interval_s: float = 8.0
    # Unsolicited calls are narrated by the model from the deterministic event
    # payload. Numbers still come only from that payload; on any failure or
    # timeout the deterministic template is spoken instead.
    proactive_narration_enabled: bool = True
    proactive_narration_timeout_s: float = 6.0
    proactive_safe_throttle: float = 0.65
    proactive_max_brake: float = 0.15
    proactive_max_lateral_g: float = 1.35
    proactive_safe_hold_s: float = 0.45
    proactive_delivery_deadline_s: float = 35.0
    # Under a safety car or VSC the car must stay above the delta time. The
    # telemetry delta goes negative when the driver is running too fast, which
    # is a penalty risk. Raise the threshold for an earlier, more cautious call.
    proactive_sc_delta_min_s: float = 0.0
    proactive_sc_delta_cooldown_s: float = 12.0

    # Engineer personality. engineer_persona, if set, is appended to the base
    # persona so a user can add a call sign, tone or catchphrases without losing
    # the safety-critical instructions. radio_verbosity tunes spoken length.
    engineer_name: str = "Mark"
    engineer_persona: str = ""
    radio_verbosity: str = "standard"  # terse | standard | chatty

    strategy_monte_carlo_samples: int = 320
    strategy_risk_quantile: float = 0.75
    strategy_max_stops: int = 3
    # Prevent tiny simulation changes from moving the radio pit call every lap.
    strategy_change_min_gain_s: float = 2.5
    strategy_min_hold_laps: int = 2
    # Cold-tyre out-lap penalty (seconds) applied to the first lap of a fresh
    # stint, halved on the second lap. Ignoring warm-up makes an undercut look
    # better than it is, because the cold in/out laps are where the time goes.
    strategy_cold_tyre_penalty_s: float = 1.1
    # Fuel-save trade: seconds gained per lap of fuel saved by lifting and
    # coasting, used to cost out a pace mode against a fuel deficit.
    strategy_fuel_save_s_per_lap: float = 0.35
    map_distance_bin_m: float = 6.0
    map_deviation_threshold_m: float = 1.25
    analysis_distance_step_m: float = 0.5
    trace_cache_max_mb: int = 128

    @property
    def udp_bind_host(self) -> str:
        """Explicit 4.2 name while retaining ``udp_host`` compatibility."""
        return self.udp_host

    @property
    def capture_dir(self) -> Path:
        return self.data_dir / "captures"

    @property
    def trace_dir(self) -> Path:
        return self.data_dir / "traces"

    @field_validator("audio_device", mode="before")
    @classmethod
    def parse_audio_device(cls, value: object) -> object:
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return value

    @field_validator("ptt_release_mode")
    @classmethod
    def validate_ptt_release_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        valid = {"explicit_or_silence", "explicit", "silence", "heartbeat"}
        return normalized if normalized in valid else "explicit_or_silence"

    @field_validator("proactive_cadence_laps")
    @classmethod
    def validate_cadence(cls, value: int) -> int:
        return max(1, min(10, value))

    @field_validator("udp_port", "web_port")
    @classmethod
    def validate_port(cls, value: int) -> int:
        port = int(value)
        if not 1 <= port <= 65_535:
            raise ValueError("port must be between 1 and 65535")
        return port

    @field_validator("udp_host", "web_host")
    @classmethod
    def validate_bind_host(cls, value: str) -> str:
        host = value.strip()
        if not host:
            raise ValueError("bind host cannot be blank")
        return host

    @field_validator("capture_mode")
    @classmethod
    def validate_capture_mode(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_")
        if normalized not in {"minimal", "balanced", "full_fidelity"}:
            raise ValueError("capture mode must be minimal, balanced, or full_fidelity")
        return normalized

    @field_validator("raw_capture")
    @classmethod
    def validate_raw_capture(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"off", "rolling", "full"}:
            raise ValueError("raw capture must be off, rolling, or full")
        return normalized

    @field_validator("field_trace_hz")
    @classmethod
    def validate_field_trace_hz(cls, value: int) -> int:
        if not 1 <= int(value) <= 120:
            raise ValueError("field trace rate must be between 1 and 120 Hz")
        return int(value)

    @field_validator("analysis_workers")
    @classmethod
    def validate_analysis_workers(cls, value: int) -> int:
        if not 1 <= int(value) <= 16:
            raise ValueError("analysis workers must be between 1 and 16")
        return int(value)

    @field_validator("live_ws_max_hz")
    @classmethod
    def validate_live_ws_max_hz(cls, value: int) -> int:
        if not 1 <= int(value) <= 30:
            raise ValueError("live WebSocket rate must be between 1 and 30 Hz")
        return int(value)

    @field_validator(
        "forward_queue_size",
        "capture_queue_size",
        "trace_cache_max_mb",
        "packet_reorder_window",
        "packet_loss_confirm_ms",
    )
    @classmethod
    def validate_positive_integer_budget(cls, value: int) -> int:
        if int(value) <= 0:
            raise ValueError("queue, cache, and packet-health budgets must be positive")
        return int(value)

    @field_validator(
        "capture_max_gb",
        "capture_min_free_gb",
        "trace_chunk_seconds",
        "analysis_distance_step_m",
    )
    @classmethod
    def validate_positive_float_budget(cls, value: float) -> float:
        if float(value) <= 0:
            raise ValueError("capture and analysis budgets must be positive")
        return float(value)

    @field_validator("retention_days")
    @classmethod
    def validate_retention_days(cls, value: int) -> int:
        if int(value) < 0:
            raise ValueError("retention days cannot be negative")
        return int(value)

    @field_validator("radio_verbosity")
    @classmethod
    def validate_verbosity(cls, value: str) -> str:
        normalized = value.strip().lower()
        return normalized if normalized in {"terse", "standard", "chatty"} else "standard"

    @field_validator("model")
    @classmethod
    def validate_openai_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            return "gpt-5.6-sol"
        # The bare family alias resolves to the flagship tier. Naming it
        # explicitly makes the cost of each route visible in configuration
        # instead of hiding it behind an alias.
        return "gpt-5.6-sol" if normalized == "gpt-5.6" else normalized

    @field_validator("fast_model")
    @classmethod
    def validate_fast_model(cls, value: str) -> str:
        normalized = value.strip()
        # An unset fast model must not silently fall back to the flagship tier —
        # that is the exact cost this routing exists to avoid. Upgrades preserve
        # .env, so an installation that never had this key still gets the cheap
        # tier rather than paying Sol prices for every gap question.
        if not normalized or normalized == "gpt-5.6":
            return "gpt-5.6-luna"
        return normalized

    @field_validator("llm_provider")
    @classmethod
    def validate_primary_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        # Legacy DeepSeek/auto settings are intentionally migrated to OpenAI so
        # an old .env cannot silently reactivate the unreliable provider path.
        return "openai" if normalized in {"openai", "deepseek", "auto"} else "openai"

    @field_validator("llm_fallback_provider")
    @classmethod
    def validate_fallback_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        return "none" if normalized in {"none", "deepseek", "auto"} else "openai"

    @field_validator("deepseek_thinking_effort")
    @classmethod
    def validate_deepseek_effort(cls, value: str) -> str:
        normalized = value.strip().lower()
        return "max" if normalized in {"max", "xhigh"} else "high"

    @field_validator("deepseek_max_tool_rounds")
    @classmethod
    def validate_deepseek_rounds(cls, value: int) -> int:
        return max(1, min(8, value))

    @field_validator(
        "llm_failure_cooldown_s",
        "llm_normal_deadline_s",
        "llm_deep_deadline_s",
        "llm_shakedown_timeout_s",
        "llm_compare_cooldown_s",
    )
    @classmethod
    def validate_positive_seconds(cls, value: float) -> float:
        return max(0.1, float(value))

    @field_validator(
        "openai_deep_max_output_tokens",
        "openai_deep_retry_max_output_tokens",
        "deepseek_deep_max_tokens",
        "deepseek_deep_retry_max_tokens",
    )
    @classmethod
    def validate_token_budgets(cls, value: int) -> int:
        return max(256, min(128_000, int(value)))

    @property
    def wake_phrases(self) -> list[str]:
        values = [self.wake_phrase, *self.wake_aliases.split(",")]
        # Existing installations preserve .env during upgrades. Inject the
        # common STT spelling variant in code so an older aliases line cannot
        # keep rejecting a correctly heard "Marc".
        if " ".join(self.wake_phrase.strip().lower().split()) == "mark":
            values.append("marc")
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            normalized = " ".join(value.strip().lower().split())
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
        return result

    @staticmethod
    def _usable_secret(secret: SecretStr | None) -> str | None:
        if secret is None:
            return None
        value = secret.get_secret_value().strip()
        placeholders = {
            "",
            "replace_me",
            "your_key_here",
            "your_actual_key",
            "api_key",
        }
        if value.lower() in placeholders or value.startswith("<"):
            return None
        return value

    @property
    def api_key(self) -> str | None:
        return self._usable_secret(self.openai_api_key)

    @property
    def deepseek_key(self) -> str | None:
        return self._usable_secret(self.deepseek_api_key)


settings = Settings()
