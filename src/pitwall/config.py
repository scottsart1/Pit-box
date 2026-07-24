from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
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
    llm_provider: str = "deepseek"
    llm_fallback_provider: str = "openai"

    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="OPENAI_API_KEY",
    )
    model: str = "gpt-5.6-terra"
    reasoning_effort: str = "low"
    deep_reasoning_effort: str = "high"
    openai_timeout_s: float = 30.0

    deepseek_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="DEEPSEEK_API_KEY",
    )
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_fast_model: str = "deepseek-v4-flash"
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

    udp_host: str = "0.0.0.0"
    udp_port: int = 20777
    disconnect_after_s: float = 3.0
    web_host: str = "127.0.0.1"
    web_port: int = 8000
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
    ptt_release_mode: str = "silence"
    ptt_release_watchdog_s: float = 0.0
    ptt_release_ignore_ms: int = 450
    ptt_silence_release_s: float = 1.15
    ptt_speech_rms: float = 220.0

    # Hands-free radio. The microphone is gated locally by a lightweight RMS
    # voice-activity detector; only bounded speech candidates are uploaded to
    # OpenAI transcription. Prefix-only matching prevents ordinary mentions of
    # the engineer name from opening the radio. L3 / UDP Action 1 remains a
    # fully supported fallback and its saved binding is never changed.
    wake_enabled: bool = True
    wake_phrase: str = "mark"
    wake_aliases: str = "hey mark,mark radio,hey marc"
    wake_preroll_s: float = 0.80
    wake_speech_rms: float = 260.0
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
    # Logs are written to data_dir/pitwall.log so a session can be diagnosed
    # after the console window has closed.
    log_level: str = "info"
    log_max_bytes: int = 2_000_000
    log_backup_count: int = 3

    proactive_enabled: bool = True
    proactive_cadence_laps: int = 2
    proactive_min_interval_s: float = 25.0
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

    strategy_monte_carlo_samples: int = 320
    strategy_risk_quantile: float = 0.75
    strategy_max_stops: int = 3
    map_distance_bin_m: float = 6.0
    map_deviation_threshold_m: float = 1.25

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

    @field_validator("llm_provider")
    @classmethod
    def validate_primary_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        return normalized if normalized in {"openai", "deepseek", "auto"} else "auto"

    @field_validator("llm_fallback_provider")
    @classmethod
    def validate_fallback_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        return normalized if normalized in {"openai", "deepseek", "auto", "none"} else "openai"

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
