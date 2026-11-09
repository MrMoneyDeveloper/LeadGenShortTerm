"""Qualification gaps from the source-quality audit: profile geography, broker requests, review route."""

from datetime import timedelta

import pytest

from app.classifiers.grok import SemanticResult
from app.models import now
from app.qualification import score
from app.ranking import rank, semantic_qualified


def test_profile_bio_can_supply_explicit_sa_geography_with_provenance():
    post = "Need car insurance, can anyone recommend an insurer?"
    without = score(post, "bluesky", now())
    assert "south_africa" not in without["signals"] and without["geography_evidence"] is None
    with_bio = score(post, "bluesky", now(), profile_text="Dad of two, Johannesburg based")
    assert "south_africa" in with_bio["signals"] and with_bio["geography_evidence"] == "profile_bio"
    assert with_bio["score"] == without["score"] + 15
    in_post = score("Need car insurance in Durban", "bluesky", now(), profile_text="Johannesburg")
    assert in_post["geography_evidence"] == "post"


def test_profile_bio_never_supplies_intent_product_or_negative_signals():
    post = "Need car insurance, any recommendations?"
    result = score(post, "bluesky", now(), profile_text="insurance broker in Cape Town, get a quote, home insurance")
    assert result["geography_evidence"] == "profile_bio"
    assert "broker" not in result["signals"] and result["product_type"] == "MOTOR"
    assert result["signals"].count("quote") <= 1 and not result["rejected"]
    # Unrelated bio words never become location: no inference.
    assert score(post, "bluesky", now(), profile_text="I love braai and rugby")["geography_evidence"] is None


def test_consumer_asking_for_a_broker_is_not_rejected_as_broker_advertising():
    ask = score("Can anyone recommend an insurance broker in Durban? I need car insurance", "bluesky", now())
    assert {"broker", "broker_request"} <= set(ask["signals"]) and not ask["rejected"]
    advert = score("Insurance broker in Johannesburg; contact me for insurance", "bluesky", now())
    assert advert["rejected"]


def test_narrow_review_route_sends_clear_sa_consumer_requests_to_semantic():
    result = score("Need car insurance in Johannesburg", "bluesky", now())
    ranked = rank(result, True)
    assert ranked["rank_score"] < 8.0 and ranked["route"] == "SEMANTIC" and ranked["review_route"]
    no_sa = rank(score("Need car insurance", "bluesky", now()), True)
    assert no_sa["route"] != "SEMANTIC" and not no_sa["review_route"]
    no_product = rank(score("Johannesburg: any insurance recommendations?", "bluesky", now()), True)
    assert not no_product["review_route"]
    stale = score("Need car insurance in Johannesburg", "bluesky", now() - timedelta(days=60))
    assert rank(stale, True)["route"] == "REJECT"  # rules-rejected records never use the review route


def test_review_route_does_not_change_final_acceptance():
    base = dict(is_insurance_relevant=True, intent_level="HIGH", product_type="MOTOR", is_consumer=True,
                is_advertisement=False, is_broker_or_agent=False, south_africa_signal=True, reason="fixture")
    assert semantic_qualified(SemanticResult(**base, score=85))
    assert not semantic_qualified(SemanticResult(**base, score=70))  # semantic_accept_score unchanged
    assert not semantic_qualified(SemanticResult(**{**base, "is_consumer": False}, score=95))


def test_quality_fixture_keeps_precision():
    from scripts.phase2_quality import evaluate

    result = evaluate()
    assert result["positive_precision"] == 1.0 and result["negative_false_positive_rate"] == 0.0


@pytest.mark.integration
def test_normalize_uses_same_author_bio_for_geography(postgres, isolated_settings):
    from app import jobs
    from app.models import Candidate, Classification, SourceRecord
    from app.services import pipeline

    jobs.initialize()
    with postgres() as db:
        db.add(SourceRecord(source="bluesky", source_record_id="did:plc:x/1", source_url="https://bsky.app/profile/x/post/1",
                            identity_hash="g" * 64, content_hash="h" * 64, text="Need car insurance, need a quote",
                            published_at=now(), profile={"bio": "Pretoria", "profile_url": "https://bsky.app/profile/x"}))
    pipeline.normalize_batch(5)
    with postgres() as db:
        candidate = db.query(Candidate).one()
        rules_result = db.query(Classification).filter(Classification.candidate_id == candidate.id,
                                                       Classification.provider == "rules").one().result
    assert rules_result["geography_evidence"] == "profile_bio" and "south_africa" in rules_result["signals"]
