from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.config import Settings


def test_explicit_test_configuration_and_safe_defaults(tmp_path: Path):
    cfg = Settings(_env_file=None, environment="test", config_dir=tmp_path)
    assert cfg.environment == "test"
    assert not cfg.acquisition_enabled
    assert not cfg.processing_enabled
    assert not cfg.bluesky_enabled
    assert not cfg.youtube_enabled
    assert not cfg.grok_enabled
    assert not cfg.groq_enabled
    assert not cfg.scheduler_enabled
    assert cfg.processing_batch_size == 600
    assert cfg.campaign_duration_days == 7
    assert cfg.campaign_raw_target == 200_000
    assert cfg.groq_daily_request_soft_cap == 800
    assert cfg.groq_daily_token_soft_cap == 150_000


def test_token_validation_and_aliases():
    with pytest.raises(ValidationError, match="at least 32"):
        Settings(_env_file=None, processor_trigger_token=SecretStr("short"))
    with pytest.raises(ValidationError, match="separate"):
        Settings(
            _env_file=None,
            processor_trigger_token=SecretStr("x" * 32),
            dashboard_api_token=SecretStr("x" * 32),
        )
    assert Settings(_env_file=None, DELETE_REJECTED_RAW="false").delete_rejected_immediately is False
    assert Settings(_env_file=None, GROQ_DAILY_CANDIDATE_CAP="321").groq_daily_request_soft_cap == 321


def test_invalid_environment_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="live")
