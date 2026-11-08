"""Two independent GroqCloud Free-tier organizations: config, routing, fallthrough and deferral."""

from datetime import timedelta

import pytest
from pydantic import SecretStr
from test_groq_client import Response, setup_client

from app.classifiers import groq, semantic
from app.config import Settings
from app.models import now


def configure(cfg, a="key-a", b="key-b"):
    cfg.groq_enabled, cfg.groq_model = True, "fixture-structured-model"
    cfg.groq_api_key = SecretStr("")
    cfg.groq_org_a_api_key, cfg.groq_org_b_api_key = SecretStr(a), SecretStr(b)


def keys_used(calls):
    return [c["key"] for c in calls]


@pytest.fixture
def router(isolated_settings, monkeypatch):
    """Fake network with per-org scripted responses and per-org admission results."""
    configure(isolated_settings)
    script = {"key-a": [], "key-b": []}
    admission = {"groq_a": ("ok", None), "groq_b": ("ok", None)}
    calls, accounting = [], []
    setup_client(isolated_settings, monkeypatch, [])
    configure(isolated_settings)

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, url, json, headers):
            key = headers["Authorization"].removeprefix("Bearer ")
            calls.append({"key": key})
            return script[key].pop(0)

    monkeypatch.setattr(groq.httpx, "Client", Client)
    monkeypatch.setattr(groq, "reserve_request", lambda tokens, org: admission[org.provider])
    monkeypatch.setattr(groq, "record_response", lambda headers, status, org: now() + timedelta(minutes=5))
    monkeypatch.setattr(groq, "account_tokens", lambda *args: accounting.append(args))
    return script, admission, calls, accounting


def test_settings_build_two_independent_orgs_with_per_org_limits():
    cfg = Settings(_env_file=None, groq_org_a_api_key="a" * 10, groq_org_b_api_key="b" * 10,
                   groq_org_b_rpd_limit=500)
    orgs = cfg.groq_orgs()
    assert [(o.name, o.provider, o.rpd_limit, o.tpm_limit) for o in orgs] == [
        ("a", "groq_a", 1000, 8000), ("b", "groq_b", 500, 8000)]
    assert "a" * 10 not in repr(orgs)


def test_legacy_single_key_is_org_a_and_duplicate_key_is_not_double_counted():
    assert [o.name for o in Settings(_env_file=None, groq_api_key="k" * 10).groq_orgs()] == ["a"]
    same = Settings(_env_file=None, groq_org_a_api_key="k" * 10, groq_org_b_api_key="k" * 10)
    assert [o.name for o in same.groq_orgs()] == ["a"]
    disabled = Settings(_env_file=None, groq_org_a_api_key="a" * 10, groq_org_b_api_key="b" * 10,
                        groq_org_a_enabled=False)
    assert [o.name for o in disabled.groq_orgs()] == ["b"]


def test_first_eligible_org_serves_and_is_recorded(router):
    script, _admission, calls, accounting = router
    script["key-a"].append(Response())
    result = groq.classify("Need a car insurance quote", 80)
    assert result._provider_org == "a" and "_provider_org" not in result.model_dump()
    assert keys_used(calls) == ["key-a"] and accounting[0][0] == "groq_a"


def test_cooling_org_falls_through_to_other_org(router):
    script, admission, calls, accounting = router
    admission["groq_a"] = ("cooldown", now() + timedelta(minutes=3))
    script["key-b"].append(Response())
    assert groq.classify("text", 80)._provider_org == "b"
    assert keys_used(calls) == ["key-b"] and accounting[0][0] == "groq_b"


def test_rate_limited_org_falls_through(router):
    script, _admission, calls, _ = router
    script["key-a"].append(Response(429))
    script["key-b"].append(Response())
    assert groq.classify("text", 80)._provider_org == "b"
    assert keys_used(calls) == ["key-a", "key-b"]


def test_all_orgs_exhausted_defers_to_earliest_eligible_time(router):
    _script, admission, calls, _ = router
    early, late = now() + timedelta(minutes=2), now() + timedelta(hours=3)
    admission["groq_a"] = ("cooldown", late)
    admission["groq_b"] = ("minute_cap", early)
    with pytest.raises(groq.GroqUnavailable) as exc:
        groq.classify("text", 80)
    assert exc.value.retry_at == early and exc.value.code == "groq_minute_cap" and not calls


def test_broken_org_plus_waiting_org_defers_without_burning_attempt(router):
    script, admission, _calls, _ = router
    script["key-a"].append(Response(401))
    admission["groq_b"] = ("unit_cap", None)
    with pytest.raises(semantic.SemanticUnavailable) as exc:
        semantic.classify("text", 80)
    assert exc.value.retry_at is not None  # pipeline defers instead of incrementing attempts


def test_access_error_on_one_org_uses_the_other(router):
    script, _admission, calls, _ = router
    script["key-a"].append(Response(401))
    script["key-b"].append(Response())
    assert groq.classify("text", 80)._provider_org == "b"


def test_content_error_does_not_spend_the_second_org(router):
    script, _admission, calls, _ = router
    script["key-a"].append(Response(content={"bad": True}))
    script["key-b"].append(Response())
    with pytest.raises(groq.GroqUnavailable, match="groq_invalid_json"):
        groq.classify("text", 80)
    assert keys_used(calls) == ["key-a"]


def test_orgs_are_used_one_after_the_other_a_then_b(isolated_settings):
    configure(isolated_settings)
    orgs = isolated_settings.groq_orgs()
    assert [[o.name for o in groq.route(orgs)] for _ in range(4)] == [["a", "b"]] * 4


def test_org_a_serves_every_call_while_eligible(router):
    script, _admission, calls, accounting = router
    script["key-a"].extend([Response(), Response(), Response()])
    assert [groq.classify("text", 80)._provider_org for _ in range(3)] == ["a", "a", "a"]
    assert keys_used(calls) == ["key-a"] * 3 and [a[0] for a in accounting] == ["groq_a"] * 3


def test_org_b_takes_over_then_a_resumes_after_its_cooldown(router):
    script, admission, calls, _ = router
    admission["groq_a"] = ("unit_cap", None)  # A exhausted for the day
    script["key-b"].extend([Response(), Response()])
    assert [groq.classify("text", 80)._provider_org for _ in range(2)] == ["b", "b"]
    admission["groq_a"] = ("ok", None)  # A eligible again (e.g. after reset)
    script["key-a"].append(Response())
    assert groq.classify("text", 80)._provider_org == "a"
    assert keys_used(calls) == ["key-b", "key-b", "key-a"]


@pytest.mark.integration
def test_per_org_quota_state_persists_separately(postgres, isolated_settings):
    from app.classifiers.groq_limits import record_response, reserve_request
    from app.database import session
    from app.models import ProviderRateState, Usage
    from app.services.reporting import semantic_orgs

    configure(isolated_settings)
    isolated_settings.groq_daily_request_soft_cap = 1
    a, b = isolated_settings.groq_orgs()
    assert reserve_request(100, a)[0] == "ok"
    assert reserve_request(100, a)[0] == "unit_cap"
    assert reserve_request(100, b)[0] == "ok"  # org B has its own allowance
    retry = record_response({"retry-after": "60", "x-ratelimit-remaining-requests": "0"}, 429, b)
    with session() as db:
        states = {r.provider: r.state for r in db.query(ProviderRateState)}
        usage = {u.provider: u.units for u in db.query(Usage)}
        orgs = {o["org"]: o for o in semantic_orgs(db, isolated_settings)}
    assert usage == {"groq_a": 1, "groq_b": 1}
    assert states["groq_a"]["deferred_count"] == 1 and "next_allowed_at" not in states["groq_a"]
    assert states["groq_b"]["responses_429"] == 1 and states["groq_b"]["next_allowed_at"] == retry.timestamp()
    assert orgs["a"]["requests_remaining"] == 0 and not orgs["a"]["cooling_down"]
    assert orgs["b"]["cooling_down"] and orgs["b"]["responses_429"] == 1
    assert orgs["a"]["plan"] == "free"
