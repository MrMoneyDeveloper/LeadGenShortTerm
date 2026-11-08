from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import AliasChoices, BaseModel, Field, SecretStr, model_validator
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
    campaign_semantic_drain_hours: int = Field(24, ge=0, le=168)
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
    # Temporary migration alias lets an existing gsk_ value stored under XAI_API_KEY be tested
    # without another rotation. GROQ_API_KEY is canonical and wins when both are present.
    groq_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("GROQ_API_KEY", "XAI_API_KEY", "groq_api_key"),
    )
    groq_model: str = ""
    groq_plan: Literal["free", "paid"] = "free"
    groq_rpm_limit: int = Field(30, ge=1)
    groq_rpd_limit: int = Field(1000, ge=1)
    groq_tpm_limit: int = Field(8000, ge=1)
    groq_tpd_limit: int = Field(200000, ge=1)
    groq_rpm_soft_cap: int = Field(24, ge=0)
    groq_tpm_soft_cap: int = Field(6500, ge=0)
    groq_daily_request_soft_cap: int = Field(
        800,
        ge=0,
        validation_alias=AliasChoices(
            "GROQ_DAILY_REQUEST_SOFT_CAP", "GROQ_DAILY_CANDIDATE_CAP", "groq_daily_request_soft_cap"
        ),
    )
    groq_daily_token_soft_cap: int = Field(150_000, ge=0)
    groq_input_usd_per_million: float = Field(0, ge=0)
    groq_output_usd_per_million: float = Field(0, ge=0)
    # Two independent Free-tier organizations. Each has its own persisted quota state; the
    # GROQ_*_SOFT_CAP values apply per organization. Without org keys, GROQ_API_KEY acts as org A.
    groq_org_a_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias=AliasChoices("GROQ_ORG_A_API_KEY", "GROQ_API_KEY1", "groq_org_a_api_key")
    )
    groq_org_b_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias=AliasChoices("GROQ_ORG_B_API_KEY", "GROQ_API_KEY2", "groq_org_b_api_key")
    )
    groq_org_a_enabled: bool = True
    groq_org_b_enabled: bool = True
    groq_org_a_rpm_limit: int | None = Field(None, ge=1)
    groq_org_a_rpd_limit: int | None = Field(None, ge=1)
    groq_org_a_tpm_limit: int | None = Field(None, ge=1)
    groq_org_a_tpd_limit: int | None = Field(None, ge=1)
    groq_org_b_rpm_limit: int | None = Field(None, ge=1)
    groq_org_b_rpd_limit: int | None = Field(None, ge=1)
    groq_org_b_tpm_limit: int | None = Field(None, ge=1)
    groq_org_b_tpd_limit: int | None = Field(None, ge=1)

    # Optional stateless remote workers (Vercel/Netlify). Render remains authoritative and falls
    # back to local execution whenever a worker is disabled, failing, cooling down or over budget.
    remote_workers_enabled: bool = False
    vercel_enabled: bool = False
    vercel_base_url: str = ""
    vercel_shared_secret: SecretStr = SecretStr("")
    vercel_daily_call_cap: int = Field(2000, ge=0)
    netlify_enabled: bool = False
    netlify_base_url: str = ""
    netlify_shared_secret: SecretStr = SecretStr("")
    netlify_daily_call_cap: int = Field(300, ge=0)
    remote_worker_timeout_seconds: float = Field(8, ge=1, le=30)
    remote_worker_max_items: int = Field(50, ge=1, le=200)
    remote_worker_cooldown_seconds: int = Field(900, ge=30, le=86400)
    remote_worker_failure_threshold: int = Field(3, ge=1, le=20)
    # Fraction of remote results recomputed locally; any mismatch opens that worker's circuit.
    remote_worker_verify_rate: float = Field(1.0, ge=0, le=1)
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
        for name in ("vercel", "netlify"):
            url, secret = getattr(self, name + "_base_url"), getattr(self, name + "_shared_secret").get_secret_value()
            if url and not url.startswith("https://"):
                raise ValueError(f"{name.upper()}_BASE_URL must use https")
            if secret and len(secret) < 32:
                raise ValueError(f"{name.upper()}_SHARED_SECRET must be at least 32 characters")
        return self

    def groq_orgs(self):
        """Configured, enabled GroqCloud organizations in stable order; never exposes keys in repr."""
        orgs = []
        primary = self.groq_org_a_api_key.get_secret_value() or self.groq_api_key.get_secret_value()
        for name, key in (("a", primary), ("b", self.groq_org_b_api_key.get_secret_value())):
            if not key or not getattr(self, f"groq_org_{name}_enabled"):
                continue
            if name == "b" and key == primary:
                continue  # One organization configured twice must not double its allowance.
            orgs.append(GroqOrg(
                name=name, provider="groq_" + name, api_key=SecretStr(key),
                rpm_limit=getattr(self, f"groq_org_{name}_rpm_limit") or self.groq_rpm_limit,
                rpd_limit=getattr(self, f"groq_org_{name}_rpd_limit") or self.groq_rpd_limit,
                tpm_limit=getattr(self, f"groq_org_{name}_tpm_limit") or self.groq_tpm_limit,
                tpd_limit=getattr(self, f"groq_org_{name}_tpd_limit") or self.groq_tpd_limit,
            ))
        return orgs


class GroqOrg(BaseModel):
    name: str
    provider: str
    api_key: SecretStr
    rpm_limit: int
    rpd_limit: int
    tpm_limit: int
    tpd_limit: int


@lru_cache
def settings():
    return Settings()


@lru_cache
def rules(name):
    with (settings().config_dir / f"{name}.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)
