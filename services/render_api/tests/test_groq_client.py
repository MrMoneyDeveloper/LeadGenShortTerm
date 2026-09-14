import json

import pytest
from pydantic import SecretStr

from app.classifiers import groq, semantic


def result():
    return {
        "is_short_term_insurance_relevant": True, "intent_level": "HIGH", "product_type": "MOTOR",
        "is_consumer": True, "is_advertisement": False, "is_broker_or_agent": False,
        "south_africa_signal": True, "score": 91, "reason": "Explicit consumer request",
    }


def setup_client(cfg, monkeypatch, responses):
    cfg.groq_enabled, cfg.groq_model = True, "fixture-structured-model"
    cfg.groq_api_key = SecretStr("fixture-key")
    cfg.groq_daily_request_soft_cap = 2
    cfg.groq_daily_token_soft_cap = 5000
    cfg.groq_input_usd_per_million, cfg.groq_output_usd_per_million = 1.0, 2.0
    calls, reservations, accounting = [], [], []

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, url, json, headers):
            calls.append({"url": url, "body": json, "has_auth": bool(headers.get("Authorization"))})
            return responses.pop(0)

    monkeypatch.setattr(groq.httpx, "Client", Client)
    monkeypatch.setattr(groq, "reserve_budget", lambda *args: reservations.append(args) or "ok")
    monkeypatch.setattr(groq, "account_tokens", lambda *args: accounting.append(args))
    monkeypatch.setattr(groq.time, "sleep", lambda _delay: None)
    return calls, reservations, accounting


class Response:
    headers = {"Retry-After": "1000"}

    def __init__(self, status=200, content=None, finish="stop"):
        self.status_code, self.content, self.finish = status, content or result(), finish

    def json(self):
        return {"choices": [{"finish_reason": self.finish, "message": {"content": json.dumps(self.content)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20}}


def test_groq_strict_payload_redaction_and_estimated_accounting(isolated_settings, monkeypatch):
    calls, reservations, accounting = setup_client(isolated_settings, monkeypatch, [Response()])
    assert groq.classify("email person@example.org @person +27 82 123 4567 https://example.org", 80).score == 91
    assert calls[0]["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert calls[0]["body"]["response_format"]["json_schema"]["strict"]
    assert "person@example.org" not in calls[0]["body"]["messages"][1]["content"]
    assert reservations == [("groq", 1, 2, 5000)]
    assert accounting == [("groq", 100, 20, 0.00014)]


def test_groq_rate_retry_caps_and_strict_rejection(isolated_settings, monkeypatch):
    calls, reservations, _ = setup_client(isolated_settings, monkeypatch, [Response(429), Response()])
    groq.classify("Ambiguous", 80)
    assert len(calls) == len(reservations) == 2

    setup_client(isolated_settings, monkeypatch, [Response(content={**result(), "is_consumer": "true"})])
    with pytest.raises(groq.GroqUnavailable, match="groq_invalid_json"):
        groq.classify("Ambiguous", 80)

    calls, _, _ = setup_client(isolated_settings, monkeypatch, [Response()])
    monkeypatch.setattr(groq, "reserve_budget", lambda *_args: "unit_cap")
    with pytest.raises(groq.GroqUnavailable, match="groq_daily_request_cap"):
        groq.classify("Ambiguous", 80)
    assert not calls

    calls, _, _ = setup_client(isolated_settings, monkeypatch, [Response()])
    monkeypatch.setattr(groq, "reserve_budget", lambda *_args: "token_cap")
    with pytest.raises(groq.GroqUnavailable, match="groq_daily_token_cap"):
        groq.classify("Ambiguous", 80)
    assert not calls


def test_provider_disabled_or_model_missing_never_calls_network(isolated_settings, monkeypatch):
    monkeypatch.setattr(groq.httpx, "Client", lambda **_kwargs: pytest.fail("network client created"))
    with pytest.raises(semantic.SemanticUnavailable, match="groq_unconfigured_or_disabled"):
        semantic.classify("sample", 80)
    isolated_settings.groq_enabled = True
    isolated_settings.groq_api_key = SecretStr("fixture-key")
    with pytest.raises(semantic.SemanticUnavailable, match="groq_unconfigured_or_disabled"):
        semantic.classify("sample", 80)
