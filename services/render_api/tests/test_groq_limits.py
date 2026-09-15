import json
from datetime import UTC, datetime, timedelta

import pytest

from app.classifiers.groq_limits import admit, observe, record_response, reserve_request, seconds


def test_rolling_window_survives_serialization_and_expires(isolated_settings):
    cfg = isolated_settings
    cfg.groq_rpm_soft_cap = 2
    current = datetime(2026, 9, 15, tzinfo=UTC)
    state = {}
    assert admit(state, cfg, 100, current)[0] == "ok"
    state = json.loads(json.dumps(state))
    assert admit(state, cfg, 100, current)[0] == "ok"
    assert admit(state, cfg, 100, current)[0] == "minute_cap"
    assert admit(state, cfg, 100, current + timedelta(seconds=60))[0] == "ok"


def test_reserved_tokens_stop_minute_and_day_overshoot(isolated_settings):
    cfg = isolated_settings
    cfg.groq_tpm_soft_cap = 100
    cfg.groq_daily_token_soft_cap = 150
    current = datetime(2026, 9, 15, tzinfo=UTC)
    state = {}
    assert admit(state, cfg, 90, current)[0] == "ok"
    assert admit(state, cfg, 20, current)[0] == "minute_cap"
    assert admit(state, cfg, 70, current + timedelta(minutes=1))[0] == "token_cap"


def test_headers_and_cooldown_survive_midnight(isolated_settings):
    current = datetime(2026, 9, 15, 23, 59, 50, tzinfo=UTC)
    state = {}
    retry = observe(state, {"Retry-After": "120", "x-ratelimit-remaining-tokens": "0",
                            "x-ratelimit-reset-tokens": "2m59.56s"}, 429, current)
    assert retry == current + timedelta(seconds=179.56)
    state = json.loads(json.dumps(state))
    assert admit(state, isolated_settings, 10, current + timedelta(seconds=20))[0] == "cooldown"
    assert state["responses_429"] == 1
    assert admit(state, isolated_settings, 10, retry)[0] == "ok"


@pytest.mark.parametrize("value,expected", [("1h2m3.5s", 3723.5), ("20ms", .02),
                                            ("nan", None), ("inf", None), ("bad", None), ("-1", None)])
def test_duration_validation(value, expected):
    assert seconds(value) == expected


def test_header_headroom_is_consumed_before_reset(isolated_settings):
    state = {}
    current = datetime(2026, 9, 15, tzinfo=UTC)
    observe(state, {"x-ratelimit-remaining-requests": "1", "x-ratelimit-reset-requests": "1h"}, 200, current)
    assert admit(state, isolated_settings, 10, current)[0] == "ok"
    assert admit(state, isolated_settings, 10, current)[0] == "provider_cap"


def test_free_telemetry_labels_historical_list_price(isolated_settings):
    from app.models import Usage
    from app.services.reporting import usage_dict

    row = Usage(provider="groq", units=1, input_tokens=100, output_tokens=20, estimated_cost=.12)
    data = usage_dict(row)
    assert data["actual_pipeline_cost"] == 0
    assert data["reference_list_price"] == .12
    assert "estimated_cost" not in data


def test_oversized_prompt_does_not_wait_forever(isolated_settings):
    state = {}
    code, retry = admit(state, isolated_settings, 99999, datetime(2026, 9, 15, tzinfo=UTC))
    assert code == "prompt_exceeds_minute_budget" and retry is None
    assert not state["events"]


@pytest.mark.integration
def test_separate_transactions_preserve_limits(postgres, isolated_settings):
    isolated_settings.groq_rpm_soft_cap = 1
    assert reserve_request(100)[0] == "ok"
    assert reserve_request(100)[0] == "minute_cap"
    retry = record_response({"retry-after": "120"}, 429)
    code, next_time = reserve_request(100)
    assert code == "cooldown" and next_time == retry
