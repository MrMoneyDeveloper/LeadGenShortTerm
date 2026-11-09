"""Review round 3: hard campaign scan ceiling, experiment-wide Groq budget, profile geography carried
through classification and delivery, and explicit seller evidence that a broker request cannot cancel."""

import json
from datetime import timedelta

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

# Loaded before the postgres fixture patches module-level sessions.
from app import jobs  # noqa: F401
from app.classifiers import (
    groq,
    groq_limits,  # noqa: F401
)
from app.collectors.base import Batch, SourceError
from app.models import now
from app.qualification import score
from app.ranking import rank
from app.services import campaign, delivery, pipeline  # noqa: F401

SELLER = ("Need an insurance broker? I sell insurance. Get a quote for the best car insurance in Johannesburg.")
CONSUMER = "Can anyone recommend an insurance broker in Johannesburg? I need a car insurance quote"


# --- 4. broker request vs explicit seller ---------------------------------------------------------

def test_explicit_seller_cannot_be_cancelled_by_a_broker_request():
    result = score(SELLER, "bluesky", now())
    assert {"broker_request", "seller"} <= set(result["signals"])
    assert result["rejected"] and "seller" not in result["conflicting_signals"]
    assert rank(result, True)["route"] == "REJECT"


def test_legitimate_consumer_broker_request_is_kept_but_never_direct_final():
    result = score(CONSUMER, "bluesky", now())
    assert {"broker", "broker_request"} <= set(result["signals"]) and "seller" not in result["signals"]
    assert not result["rejected"] and result["conflicting_signals"] == ["broker", "broker_request"]
    ranked = rank(result, True)
    assert ranked["route"] == "SEMANTIC"  # conflicting broker evidence: Groq must review it


def test_conflicting_record_with_maximum_score_still_cannot_route_direct_final():
    result = {"score": 100, "product_type": "MOTOR", "signals": ["quote", "south_africa", "broker", "broker_request"],
              "rejected": False, "conflicting_signals": ["broker"]}
    assert rank(result, True)["route"] == "SEMANTIC"
    assert rank({**result, "conflicting_signals": []}, True)["route"] == "DIRECT_FINAL"


# --- 3. profile geography: labelled, bounded, separate from intent --------------------------------

def test_profile_geography_excerpt_is_bounded_and_redacted():
    bio = "x" * 300 + " mail me a@b.co.za, proud Joburg dad, call 082 555 1234 " + "y" * 300
    result = score("Need car insurance, any recommendations?", "bluesky", now(), profile_text=bio)
    excerpt = result["geography_excerpt"]
    assert result["geography_evidence"] == "profile_bio" and "joburg" in excerpt and len(excerpt) <= 200
    assert "a@b.co.za" not in excerpt and "082 555 1234" not in excerpt
    assert score("Need car insurance in Durban", "bluesky", now(), profile_text=bio)["geography_excerpt"] is None


def test_groq_receives_geography_as_a_separate_labelled_field(isolated_settings, monkeypatch):
    isolated_settings.groq_enabled, isolated_settings.groq_model = True, "fixture-model"
    isolated_settings.groq_org_a_api_key = SecretStr("key-a")
    sent = []

    class Client:
        def __init__(self, **_kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            pass

        def post(self, url, json, headers):
            sent.append(json)
            content = {"is_insurance_relevant": True, "intent_level": "HIGH", "product_type": "MOTOR",
                       "is_consumer": True, "is_advertisement": False, "is_broker_or_agent": False,
                       "south_africa_signal": True, "score": 90, "reason": "fixture"}
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "content": __import__("json").dumps(content)}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    monkeypatch.setattr(groq.httpx, "Client", Client)
    monkeypatch.setattr(groq, "reserve_request", lambda tokens, org: ("ok", None))
    monkeypatch.setattr(groq, "record_response", lambda *a: None)
    monkeypatch.setattr(groq, "account_tokens", lambda *a: None)
    groq.classify("Need car insurance, any recommendations?", 45, geography="proud joburg dad")
    user = json.loads(sent[0]["messages"][1]["content"])
    assert user["text"] == "Need car insurance, any recommendations?"
    assert user["same_author_profile_location"] == "proud joburg dad"
    assert "same_author_profile_location" in sent[0]["messages"][0]["content"]
    groq.classify("Need car insurance", 45)
    assert "same_author_profile_location" not in json.loads(sent[1]["messages"][1]["content"])


@pytest.mark.integration
def test_profile_geography_reaches_groq_and_the_delivered_google_record(postgres, isolated_settings, monkeypatch):
    from app.classifiers.grok import SemanticResult
    from app.models import Lead, SourceRecord

    jobs.initialize()
    isolated_settings.final_delivery_enabled = True
    isolated_settings.google_spreadsheet_id, isolated_settings.google_drive_backup_folder_id = "sheet_t", "folder_t"
    isolated_settings.sheet_layout, isolated_settings.sheet_shard_rows = "alphabetical", 5000
    monkeypatch.setattr(pipeline.local, "classify", lambda _text: None)
    monkeypatch.setattr(pipeline, "validate_contact", lambda _email: ("DELIVERABLE_DOMAIN", "fixture"))
    seen = []

    def fake_semantic(text, score_value, geography=None):
        seen.append({"text": text, "geography": geography})
        return SemanticResult(is_insurance_relevant=True, intent_level="HIGH", product_type="MOTOR", is_consumer=True,
                              is_advertisement=False, is_broker_or_agent=False, south_africa_signal=True,
                              score=90, reason="fixture")

    monkeypatch.setattr(pipeline.semantic, "classify", fake_semantic)
    with postgres() as db:
        db.add(SourceRecord(
            source="bluesky", source_record_id="did:plc:geo/1", source_url="https://bsky.app/profile/geo/post/1",
            identity_hash="e" * 64, content_hash="f" * 64,
            text="Need car insurance, any recommendations? mail me sam@example.com", published_at=now(),
            profile={"bio": "Proud Joburg dad", "profile_url": "https://bsky.app/profile/geo"}))
    pipeline.normalize_batch(5)
    pipeline.validate_batch(5)
    pipeline.classify_batch(5)
    assert seen == [{"text": seen[0]["text"], "geography": "proud joburg dad"}]
    assert "joburg" not in seen[0]["text"]  # location evidence is not mixed into the intent text
    with postgres() as db:
        lead = db.scalar(select(Lead))
        assert lead.profile["geography"] == {"provenance": "profile_bio", "excerpt": "proud joburg dad",
                                             "source_url": "https://bsky.app/profile/geo"}
    batch = delivery.claim()
    evidence = batch["items"][0]["values"][delivery.FIELDS.index("source_evidence")]
    assert evidence.startswith("Need car insurance")
    assert "same author's public profile bio, https://bsky.app/profile/geo" in evidence
    assert '"proud joburg dad"' in evidence and len(evidence) <= 2000


def test_source_evidence_suffix_survives_long_excerpts():
    from app.services.delivery import source_evidence

    profile = {"geography": {"excerpt": "cape town", "source_url": "https://bsky.app/profile/z"}}
    value = source_evidence("a" * 5000, profile)
    assert len(value) == 2000 and value.endswith('"cape town"')
    assert source_evidence("a" * 5000, {}) == "a" * 2000


# --- 1. hard campaign scan ceiling ---------------------------------------------------------------

class Adapter:
    seen = []
    overscan = 0

    def collect(self, cursor, limit):
        Adapter.seen.append(limit)
        return Batch(cursor={"n": cursor.get("n", 0) + 1}, scanned=limit + Adapter.overscan, records=[])


def _collect_job(postgres, batch_size):
    from app.models import Job

    with postgres() as db:
        job = Job(idempotency_key="k" + str(len(Adapter.seen)) + str(Adapter.overscan), kind="collect",
                  source="bluesky", batch_size=batch_size)
        db.add(job)
        db.flush()
        db.expunge(job)
        return job


@pytest.mark.integration
def test_campaign_raw_target_is_a_hard_ceiling(postgres, isolated_settings, monkeypatch):
    from app.models import PipelineState

    jobs.initialize()
    Adapter.seen, Adapter.overscan = [], 0
    monkeypatch.setitem(pipeline.ADAPTERS, "bluesky", Adapter)
    campaign.start(raw_target=500)
    with postgres() as db:
        db.get(PipelineState, 1).campaign_raw_scanned = 499
    result = pipeline.collect(_collect_job(postgres, 100))
    assert Adapter.seen == [1] and result["scanned"] == 1
    with postgres() as db:
        state = db.get(PipelineState, 1)
        assert state.campaign_raw_scanned == 500 and state.campaign_status == "DRAINING"
    # Once DRAINING nothing more is requested from the adapter.
    assert pipeline.collect(_collect_job(postgres, 100))["stop_reason"] in {"drain", "campaign_raw_target"}
    assert Adapter.seen == [1]


@pytest.mark.integration
def test_scans_are_recorded_and_an_overscanning_adapter_commits_nothing(postgres, isolated_settings, monkeypatch):
    from app.models import PipelineState, SourceCursor

    jobs.initialize()
    Adapter.seen, Adapter.overscan = [], 0
    monkeypatch.setitem(pipeline.ADAPTERS, "bluesky", Adapter)
    campaign.start(raw_target=500)
    pipeline.collect(_collect_job(postgres, 100))
    with postgres() as db:
        assert db.get(PipelineState, 1).campaign_raw_scanned == 100  # counter advances with the cursor
        cursor = dict(db.get(SourceCursor, "bluesky").cursor)
    Adapter.overscan = 5
    with pytest.raises(SourceError) as exc:
        pipeline.collect(_collect_job(postgres, 100))
    assert exc.value.code == "adapter_limit_exceeded"
    with postgres() as db:
        assert db.get(PipelineState, 1).campaign_raw_scanned == 100
        assert db.get(SourceCursor, "bluesky").cursor == cursor


# --- 2. experiment-wide Groq request budget -------------------------------------------------------

def _orgs(cfg):
    cfg.groq_enabled, cfg.groq_model = True, "fixture-model"
    cfg.groq_api_key = SecretStr("")
    cfg.groq_org_a_api_key, cfg.groq_org_b_api_key = SecretStr("key-a"), SecretStr("key-b")
    return cfg.groq_orgs()


@pytest.mark.integration
def test_campaign_budget_spans_days_orgs_and_cannot_be_raised(postgres, isolated_settings, monkeypatch):
    from app.database import session
    from app.models import ProviderRateState
    from app.services import campaign

    jobs.initialize()
    a, b = _orgs(isolated_settings)
    isolated_settings.groq_daily_request_soft_cap = 5
    isolated_settings.groq_campaign_request_cap = 7
    campaign.start(raw_target=500)
    start = now()
    results = []
    for day in range(3):  # three UTC dates: per-org daily caps alone would allow 30
        monkeypatch.setattr(groq_limits, "now", lambda d=day: start + timedelta(days=d))
        for org in (a, b):
            for _ in range(5):
                results.append(groq_limits.reserve_request(100, org)[0])
    assert results.count("ok") == 7 and results.count("campaign_cap") == 23
    isolated_settings.groq_campaign_request_cap = 50  # raising configuration never grants more
    assert groq_limits.reserve_request(100, a)[0] == "campaign_cap"
    isolated_settings.groq_campaign_request_cap = 3  # lowering still applies
    with session() as db:
        state = db.get(ProviderRateState, "groq_campaign").state
    assert state[isolated_settings.campaign_id]["used"] == 7 and state[isolated_settings.campaign_id]["cap"] == 7


@pytest.mark.integration
def test_every_http_attempt_and_retry_counts_and_exhaustion_defers(postgres, isolated_settings, monkeypatch):

    jobs.initialize()
    _orgs(isolated_settings)
    isolated_settings.groq_campaign_request_cap = 3
    isolated_settings.semantic_max_attempts = 2
    isolated_settings.groq_tpm_soft_cap = isolated_settings.groq_tpm_limit = 100_000  # isolate the budget
    campaign.start(raw_target=500)
    posts = []

    class Client:
        def __init__(self, **_kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            pass

        def post(self, url, json, headers):
            posts.append(headers["Authorization"])
            return httpx.Response(500, json={})

    monkeypatch.setattr(groq.httpx, "Client", Client)
    monkeypatch.setattr(groq.time, "sleep", lambda _s: None)
    with pytest.raises(groq.GroqUnavailable) as exc:
        groq.classify("Need car insurance in Durban", 45)
    # Org A: 2 attempts (both counted); org B: 1 attempt, then the experiment budget is spent.
    assert len(posts) == 3 and exc.value.code == "groq_campaign_request_cap" and exc.value.retry_at
    with pytest.raises(groq.GroqUnavailable) as again:
        groq.classify("Need car insurance in Durban", 45)
    assert len(posts) == 3 and again.value.code == "groq_campaign_request_cap"


# --- review round 3, adversarial follow-ups -------------------------------------------------------

def test_typographic_apostrophe_cannot_defeat_seller_terms():
    curly = "Need a broker? I’m a broker in Johannesburg. Get a quote for the best car insurance today."
    result = score(curly, "bluesky", now())
    assert "seller" in result["signals"] and result["rejected"]
    assert rank(result, True)["route"] == "REJECT"


def test_broker_request_alone_is_review_only_never_direct_final():
    advert = "Need a broker? Get a quote for the best car insurance in Johannesburg."
    result = score(advert, "bluesky", now())
    assert "broker_request" in result["conflicting_signals"]
    assert rank(result, True)["route"] != "DIRECT_FINAL"


@pytest.mark.parametrize("text", [
    "Can anyone recommend a broker? Our brokerage dropped us, need a car insurance quote in Johannesburg.",
    "Need car insurance quote in Johannesburg. Can someone dm me for a quote? Looking for recommendations",
])
def test_consumer_solicitation_phrasing_goes_to_review_not_rejection(text):
    result = score(text, "bluesky", now())
    assert not result["rejected"] and "seller" not in result["signals"]
    assert rank(result, True)["route"] == "SEMANTIC"


def test_legacy_rules_results_never_route_direct_final():
    legacy = {"score": 100, "product_type": "MOTOR", "signals": ["quote", "south_africa", "broker"],
              "rejected": False, "version": "rules-v3"}
    assert rank(legacy, True)["route"] == "SEMANTIC"
    from app.config import rules
    assert rank({**legacy, "version": rules("scoring")["version"]}, True)["route"] == "DIRECT_FINAL"


@pytest.mark.parametrize("bio", [
    "Johannesburg born and raised. Lover of dogs. Mail thandiwe.mokoena@gmail.com",
    "Johannesburg born and raised. Lover of dogs and coffee!!!!!!! Call 082 123 4567 anytime",
    "Johannesburg born and raised, follow @someone.bsky.social for more dog pictures daily",
])
def test_geography_window_never_contains_partial_contacts(bio):
    from app.qualification import profile_geography

    excerpt = profile_geography(bio)
    assert "johannesburg" in excerpt
    for fragment in ("thandiwe", "gmail", "082", "4567", "someone"):
        assert fragment not in excerpt


def test_sa_domain_inside_email_or_url_is_not_location_evidence():
    from app.qualification import profile_geography

    assert profile_geography("Contact: thandiwe@example.co.za for bookings") is None
    assert profile_geography("Shop at https://shop.example.co.za today") is None
    result = score("Need car insurance, any recommendations?", "bluesky", now(),
                   profile_text="Contact: thandiwe@example.co.za")
    assert result["geography_evidence"] is None and "south_africa" not in result["signals"]


@pytest.mark.integration
def test_stored_campaign_budget_survives_cap_removal(postgres, isolated_settings):
    from app.classifiers import groq_limits

    jobs.initialize()
    a, _b = _orgs(isolated_settings)
    isolated_settings.groq_campaign_request_cap = 2
    campaign.start(raw_target=500)
    assert [groq_limits.reserve_request(100, a)[0] for _ in range(3)] == ["ok", "ok", "campaign_cap"]
    isolated_settings.groq_campaign_request_cap = 0  # unset/zeroed configuration cannot lift it
    assert groq_limits.reserve_request(100, a)[0] == "campaign_cap"


@pytest.mark.integration
def test_backlog_candidate_without_excerpt_gets_geography_rebuilt(postgres, isolated_settings, monkeypatch):
    from app.classifiers.grok import SemanticResult
    from app.models import Candidate, Classification, Lead

    jobs.initialize()
    monkeypatch.setattr(pipeline.local, "classify", lambda _text: None)
    seen = []

    def fake_semantic(text, score_value, geography=None):
        seen.append(geography)
        return SemanticResult(is_insurance_relevant=True, intent_level="HIGH", product_type="MOTOR", is_consumer=True,
                              is_advertisement=False, is_broker_or_agent=False, south_africa_signal=True,
                              score=90, reason="fixture")

    monkeypatch.setattr(pipeline.semantic, "classify", fake_semantic)
    with postgres() as db:
        row = Candidate(source="bluesky", source_url="https://bsky.app/profile/old/post/1", identity_hash="d" * 64,
                        excerpt="Need car insurance, any recommendations?", product_type="MOTOR", score=45,
                        status="CLASSIFY", contacts=[],
                        profile={"bio": "Cape Town surfer", "profile_url": "https://bsky.app/profile/old"})
        db.add(row)
        db.flush()
        db.add(Classification(candidate_id=row.id, provider="rules", version="rules-v3", result={
            "score": 45, "product_type": "MOTOR", "signals": ["recommendation", "south_africa"], "rejected": False,
            "geography_evidence": "profile_bio", "version": "rules-v3"}))
    pipeline.classify_batch(5)
    assert seen == ["cape town surfer"]
    with postgres() as db:
        assert db.scalar(select(Lead)).profile["geography"]["source_url"] == "https://bsky.app/profile/old"
