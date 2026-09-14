from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", env_ignore_empty=True)
    environment: Literal["development", "test", "staging", "production"] = "development"
    database_url: SecretStr = SecretStr("")
    processor_trigger_token: SecretStr = SecretStr("")
    dashboard_api_token: SecretStr = SecretStr("")

    # Master execution gates. Everything remains off unless explicitly enabled.
    acquisition_enabled: bool = False
    processing_enabled: bool = False
    bluesky_enabled: bool = False
    youtube_enabled: bool = False
    scheduler_enabled: bool = False

    processing_batch_size: int = Field(600, ge=1, le=2000)
    raw_daily_target: int = Field(30000, ge=1)
    max_pending_raw: int = Field(12000, ge=1)
    queue_high_water: int = Field(1800, ge=1)
    queue_low_water: int = Field(400, ge=0)
    database_high_water_bytes: int = Field(750_000_000, ge=1)
    database_low_water_bytes: int = Field(600_000_000, ge=1)

    # The final one-week clock is persisted by the campaign controller when START is called.
    campaign_id: str = Field("phase2", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    campaign_duration_days: int = Field(7, ge=1, le=30)
    campaign_raw_target: int = Field(200_000, ge=1, le=10_000_000)
    # Legacy/manual safety ceiling. New campaigns do not require this value.
    campaign_deadline: datetime | None = None

    final_delivery_enabled: bool = False
    google_spreadsheet_id: str = ""
    google_drive_backup_folder_id: str = ""
    export_batch_size: int = Field(100, ge=1, le=200)
    sheet_shard_rows: int = Field(5000, ge=1, le=5000)

    # Default semantic provider: GroqCloud free tier. xAI/Grok remains an explicit legacy option.
    semantic_provider: Literal["groq", "xai"] = "groq"
    groq_enabled: bool = False
    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = ""
    groq_daily_request_soft_cap: int = Field(
        800,
        ge=0,
        validation_alias=AliasChoices("GROQ_DAILY_REQUEST_SOFT_CAP", "GROQ_DAILY_CANDIDATE_CAP"),
    )
    groq_daily_token_soft_cap: int = Field(150_000, ge=0)
    groq_input_usd_per_million: float = Field(0, ge=0)
    groq_output_usd_per_million: float = Field(0, ge=0)
    semantic_max_attempts: int = Field(2, ge=1, le=3)
    semantic_timeout_seconds: int = Field(20, ge=5, le=60)

    raw_retention_days: int = Field(3, ge=1, le=30)
    candidate_retention_days: int = Field(30, ge=1)
    terminal_candidate_retention_hours: int = Field(24, ge=0, le=168)
    operational_retention_days: int = Field(30, ge=1)
    delete_rejected_immediately: bool = Field(
        True, validation_alias=AliasChoices("DELETE_REJECTED_IMMEDIATELY", "DELETE_REJECTED_RAW")
    )
    delete_processed_raw: bool = True

    bluesky_jetstream_host: str = "jetstream2.us-east.bsky.network"
    bluesky_collection: str = "app.bsky.feed.post"
    youtube_api_key: SecretStr = SecretStr("")
    youtube_daily_unit_cap: int = Field(9000, ge=0)
    youtube_daily_search_cap: int = Field(80, ge=0)

    # Legacy xAI/Grok provider settings. These do not configure GroqCloud.
    grok_enabled: bool = False
    xai_api_key: SecretStr = SecretStr("")
    xai_model: str = ""
    grok_daily_candidate_cap: int = Field(100, ge=0)
    grok_input_usd_per_million: float = Field(0, ge=0)
    grok_output_usd_per_million: float = Field(0, ge=0)

    local_model_path: str = ""
    local_model_sha256: str = ""
    config_dir: Path = ROOT / "config"
    job_max_attempts: int = Field(5, ge=1, le=20)
    job_seconds: int = Field(45, ge=10, le=120)
    scheduler_interval_seconds: int = Field(60, ge=30)

    @model_validator(mode="after")
    def validate_secrets(self):
        a, b = self.processor_trigger_token.get_secret_value(), self.dashboard_api_token.get_secret_value()
        if any(t and len(t) < 32 for t in (a, b)):
            raise ValueError("API tokens must be at least 32 characters")
        if a and a == b:
            raise ValueError("Use separate processor and dashboard tokens")
        if self.queue_low_water >= self.queue_high_water:
            raise ValueError("QUEUE_LOW_WATER must be below QUEUE_HIGH_WATER")
        if self.database_low_water_bytes >= self.database_high_water_bytes:
            raise ValueError("Database low water must be below high water")
        if self.campaign_deadline and self.campaign_deadline.tzinfo is None:
            raise ValueError("CAMPAIGN_DEADLINE must include a timezone")
        return self


@lru_cache
def settings():
    return Settings()


@lru_cache
def rules(name):
    with (settings().config_dir / f"{name}.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)
