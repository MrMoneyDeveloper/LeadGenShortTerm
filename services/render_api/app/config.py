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
    acquisition_enabled: bool = False
    processing_enabled: bool = False
    bluesky_enabled: bool = False
    youtube_enabled: bool = False
    grok_enabled: bool = False
    scheduler_enabled: bool = False
    processing_batch_size: int = Field(600, ge=1, le=2000)
    raw_daily_target: int = Field(30000, ge=1)
    max_pending_raw: int = Field(12000, ge=1)
    raw_retention_days: int = Field(3, ge=1, le=30)
    candidate_retention_days: int = Field(30, ge=1)
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
        return self


@lru_cache
def settings():
    return Settings()


@lru_cache
def rules(name):
    with (settings().config_dir / f"{name}.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)
