import json

import pytest
from pydantic import SecretStr

from app.classifiers import grok


def payload():
    return {
        "is_short_term_insurance_relevant": True,
        "intent_level": "HIGH",
        "product_type": "MOTOR",
        "is_consumer": True,
        "is_advertisement": False,
        "is_broker_or_agent": False,
        "south_africa_signal": True,
        "score": 91,
        "reason": "Explicit consumer request",
    }


class Response:
    status_code = 200
    headers = {}

    def __init__(self, content):
        self.content = content

    def json(self):
        return {
            "choices": [{"finish_reason": "stop", "message": {"content": self.content}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }


class Client:
    def __init__(self, response, calls):
        self.response, self.calls = response, calls

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def post(self, url, json, headers):
        self.calls.append({"url": url, "body": json, "auth_present": bool(headers.get("Authorization"))})
        return self.response


class RateLimitedResponse:
    status_code = 429
    headers = {"Retry-After": "1"}


def configure(isolated_settings):
    isolated_settings.grok_enabled = True
    isolated_settings.xai_api_key = SecretStr("rotated-test-key")
    isolated_settings.xai_model = "test-model"
    isolated_settings.grok_daily_candidate_cap = 2
    isolated_settings.grok_input_usd_per_million = 1.0
    isolated_settings.grok_output_usd_per_million = 2.0


def test_mocked_grok_strict_output_and_accounting(isolated_settings, monkeypatch):
    configure(isolated_settings)
    calls, accounting = [], []
    monkeypatch.setattr(grok, "reserve", lambda *args: True)
    monkeypatch.setattr(grok, "account_tokens", lambda *args: accounting.append(args))
    monkeypatch.setattr(grok.httpx, "Client", lambda **kwargs: Client(Response(json.dumps(payload())), calls))
    result = grok.classify("email me user@example.org or @person about cover", 50)
    assert result.score == 91 and result.intent_level == "HIGH"
    assert calls[0]["url"] == "https://api.x.ai/v1/chat/completions"
    assert calls[0]["body"]["response_format"]["type"] == "json_schema"
    sent = calls[0]["body"]["messages"][1]["content"]
    assert "user@example" not in sent and "@person" not in sent
    assert accounting == [("grok", 100, 20, 0.00014)]


def test_mocked_grok_rejects_invalid_model_output(isolated_settings, monkeypatch):
    configure(isolated_settings)
    bad = {**payload(), "is_consumer": "yes"}
    monkeypatch.setattr(grok, "reserve", lambda *args: True)
    monkeypatch.setattr(grok, "account_tokens", lambda *args: None)
    monkeypatch.setattr(grok.httpx, "Client", lambda **kwargs: Client(Response(json.dumps(bad)), []))
    with pytest.raises(grok.GrokUnavailable, match="grok_invalid_json"):
        grok.classify("ambiguous insurance", 30)


def test_mocked_grok_retries_one_rate_limit_then_accounts_once(isolated_settings, monkeypatch):
    configure(isolated_settings)
    responses = [RateLimitedResponse(), Response(json.dumps(payload()))]
    reservations, accounting = [], []

    class RetryClient(Client):
        def post(self, url, json, headers):
            self.calls.append({"url": url, "auth_present": bool(headers.get("Authorization"))})
            return responses.pop(0)

    monkeypatch.setattr(grok, "reserve", lambda *args: reservations.append(args) or True)
    monkeypatch.setattr(grok, "account_tokens", lambda *args: accounting.append(args))
    monkeypatch.setattr(grok.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(grok.httpx, "Client", lambda **kwargs: RetryClient(None, []))
    result = grok.classify("ambiguous insurance", 40)
    assert result.score == 91
    assert len(reservations) == 2
    assert accounting == [("grok", 100, 20, 0.00014)]
