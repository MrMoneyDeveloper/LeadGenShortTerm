from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.classifiers.grok import SemanticResult
from app.models import Candidate, Classification, Dedupe, Lead, PipelineState, now
from app.qualification import digest
from app.services import pipeline

pytestmark = pytest.mark.integration


def candidate(db, name, score, email=True):
    row = Candidate(source="fixture", source_url="https://example.org/" + name,
                    identity_hash=digest(name), excerpt=name, product_type="MOTOR", score=score,
                    status="VALIDATE", profile={"display_name": "Public name", "business_name": ""},
                    contacts=[{"email": name + "@example.org", "source_url": "https://example.org/" + name}] if email else [])
    db.add(row)
    db.flush()
    db.add(Classification(candidate_id=row.id, provider="rules", version="fixture", result={
        "score": score, "product_type": "MOTOR", "signals": ["south_africa"], "rejected": False,
    }))
    return row.id


def semantic_pass():
    return SemanticResult(is_short_term_insurance_relevant=True, is_consumer=True,
                          is_advertisement=False, is_broker_or_agent=False,
                          intent_level="HIGH", product_type="MOTOR", south_africa_signal=True,
                          score=85, reason="Public consumer intent")


def test_contacts_precede_selective_priority_inference_and_public_profile_survives(postgres, monkeypatch):
    with postgres() as db:
        for name, value, has_email in [("direct", 100, True), ("medium", 80, True), ("priority", 90, True),
                                       ("low", 50, True), ("nomx", 90, True), ("missing", 100, False)]:
            candidate(db, name, value, has_email)
    monkeypatch.setattr(pipeline.local, "classify", lambda _text: None)
    calls = []
    monkeypatch.setattr(pipeline.semantic, "classify", lambda _text, score: calls.append(score) or semantic_pass())
    monkeypatch.setattr(pipeline, "validate_contact", lambda email: (
        ("NO_MX", "no_mx") if email.startswith("nomx") else ("DELIVERABLE_DOMAIN", "fixture")))
    assert pipeline.classify_batch(10) == {"classified": 0}
    assert not calls
    pipeline.validate_batch(10)
    pipeline.classify_batch(10)
    assert calls == [90, 80]
    with postgres() as db:
        leads = db.scalars(select(Lead)).all()
        assert {lead.email for lead in leads} == {name + "@example.org" for name in ("direct", "priority", "medium")}
        assert all(lead.candidate_id and lead.profile["display_name"] == "Public name" for lead in leads)
        assert set(db.scalars(select(Candidate.status))) == {"VALIDATED", "DEFERRED", "CONTACT_REVIEW", "NO_CONTACT"}
    assert pipeline.classify_batch(10) == {"classified": 0}
    assert calls == [90, 80]


def test_semantic_retry_budget_and_stale_contacts_cannot_bypass_validation(postgres, isolated_settings, monkeypatch):
    isolated_settings.job_max_attempts = 2
    with postgres() as db:
        row_id = candidate(db, "retry", 85)
    monkeypatch.setattr(pipeline.local, "classify", lambda _text: None)
    monkeypatch.setattr(pipeline, "validate_contact", lambda _email: ("DELIVERABLE_DOMAIN", "fixture"))
    calls = []

    def unavailable(_text, score):
        calls.append(score)
        raise pipeline.semantic.SemanticUnavailable("groq_invalid_json")

    monkeypatch.setattr(pipeline.semantic, "classify", unavailable)
    pipeline.validate_batch(1)
    pipeline.classify_batch(1)
    assert pipeline.classify_batch(1) == {"classified": 0}
    with postgres() as db:
        row = db.get(Candidate, row_id)
        assert row.attempts == 1
        row.available_at = now() - timedelta(seconds=1)
    pipeline.classify_batch(1)
    with postgres() as db:
        row = db.get(Candidate, row_id)
        assert row.status == "REVIEW" and row.attempts == 2
        row.status, row.available_at = "CLASSIFY", now()
        row.contacts = [{**contact, "validated_at": (now() - timedelta(days=4)).isoformat()} for contact in row.contacts]
    pipeline.classify_batch(1)
    assert len(calls) == 2
    with postgres() as db:
        assert db.get(Candidate, row_id).status == "VALIDATE"
        assert db.scalar(select(func.count()).select_from(Lead)) == 0


def test_quota_wait_preserves_candidate_and_attempts(postgres, isolated_settings, monkeypatch):
    isolated_settings.job_max_attempts = 1
    with postgres() as db:
        row_id = candidate(db, "quota", 85)
    monkeypatch.setattr(pipeline.local, "classify", lambda _text: None)
    monkeypatch.setattr(pipeline, "validate_contact", lambda _email: ("DELIVERABLE_DOMAIN", "fixture"))
    retry = now() + timedelta(minutes=10)

    def limited(*_args):
        raise pipeline.semantic.SemanticUnavailable("groq_rate_limit", retry)

    monkeypatch.setattr(pipeline.semantic, "classify", limited)
    pipeline.validate_batch(1)
    pipeline.classify_batch(1)
    with postgres() as db:
        row = db.get(Candidate, row_id)
        assert row.attempts == 0 and row.status == "CLASSIFY"
        assert row.available_at == retry
    assert pipeline.classify_batch(1) == {"classified": 0}


def test_campaign_expires_quota_wait_and_completes_after_cleanup(postgres, isolated_settings, monkeypatch):
    from app import jobs
    from app.services import campaign

    jobs.initialize()
    campaign.start(raw_target=1)
    with postgres() as db:
        row_id = candidate(db, "quota-drain", 85)
        db.get(PipelineState, 1).campaign_raw_scanned = 1
        campaign.observe(db, 1, 0)
    monkeypatch.setattr(pipeline.local, "classify", lambda _text: None)
    monkeypatch.setattr(pipeline, "validate_contact", lambda _email: ("DELIVERABLE_DOMAIN", "fixture"))

    def limited(*_args):
        raise pipeline.semantic.SemanticUnavailable("groq_rate_limit", now() + timedelta(days=2))

    monkeypatch.setattr(pipeline.semantic, "classify", limited)
    pipeline.validate_batch(1)
    pipeline.classify_batch(1)
    assert pipeline.cleanup(1)["candidates_expired"] == 0
    with postgres() as db:
        assert db.get(Candidate, row_id).attempts == 0
        db.get(PipelineState, 1).campaign_semantic_deadline = now() - timedelta(seconds=1)
    assert pipeline.cleanup(1)["candidates_expired"] == 1
    with postgres() as db:
        assert db.get(Candidate, row_id) is None
        assert campaign.observe(db, 0, 0).campaign_status == "FINALIZING"
    with postgres() as db:
        assert campaign.observe(db, 0, 0).campaign_status == "COMPLETE"


def test_drain_expiry_protects_contact_work_and_pending_final(postgres, monkeypatch):
    with postgres() as db:
        db.add(PipelineState(id=1, paused=False, campaign_status="DRAINING",
                             campaign_semantic_deadline=now() - timedelta(seconds=1)))
        contact_id = candidate(db, "contact-work", 85)
        final_id = candidate(db, "pending-final", 85)
        row = db.get(Candidate, final_id)
        row.status = "CLASSIFY"
        db.add(Classification(candidate_id=row.id, provider="ranking", version="fixture", result={"route": "SEMANTIC"}))
        db.add(Lead(candidate_id=row.id, email="pending@example.org", email_hash=digest("pending@example.org"),
                    identity_hash=row.identity_hash, source="fixture", source_url=row.source_url,
                    email_source_url=row.source_url, excerpt="finished", product_type="MOTOR", score=95))
    assert pipeline.cleanup(10)["candidates_expired"] == 0
    with postgres() as db:
        assert db.get(Candidate, contact_id) is not None
        assert db.get(Candidate, final_id) is not None
        assert db.scalar(select(func.count()).select_from(Lead)) == 1


def test_pressure_cleanup_releases_terminal_backlog_but_preserves_active_and_unacknowledged(postgres, isolated_settings):
    isolated_settings.queue_high_water, isolated_settings.queue_low_water = 5, 1
    with postgres() as db:
        db.add(PipelineState(id=1, paused=False))
        ids = [candidate(db, name, 80) for name in ("no-contact", "review", "deferred", "active", "unacknowledged")]
        for row_id, status in zip(ids, ["NO_CONTACT", "CONTACT_REVIEW", "DEFERRED", "CLASSIFY", "REVIEW"], strict=True):
            row = db.get(Candidate, row_id)
            row.status = status
            row.created_at = now() - timedelta(days=40)
            db.add(Dedupe(key="candidate:" + row.identity_hash))
            db.add(Dedupe(key="record:" + digest(row_id)))
        row = db.get(Candidate, ids[-1])
        db.add(Lead(candidate_id=row.id, email="unack@example.org", email_hash=digest("unack@example.org"),
                    identity_hash=row.identity_hash, source="fixture", source_url=row.source_url,
                    email_source_url=row.source_url, excerpt="finished", product_type="MOTOR", score=95))
    # One bounded chunk frees only terminal records. Old unfinished/awaiting-ACK data survives.
    result = pipeline.cleanup(2)
    assert result["candidates_expired"] == 2
    with postgres() as db:
        assert db.get(Candidate, ids[3]) is not None
        assert db.get(Candidate, ids[4]) is not None
        assert db.scalar(select(func.count()).select_from(Dedupe).where(Dedupe.key.like("record:%"))) == 5
    assert pipeline.cleanup(2)["candidates_expired"] == 1
    with postgres() as db:
        assert db.scalar(select(func.count()).select_from(Candidate)) == 2
        assert db.scalar(select(func.count()).select_from(Lead)) == 1


def test_terminal_age_cleanup_uses_review_window(postgres, isolated_settings):
    isolated_settings.terminal_candidate_retention_hours = 24
    with postgres() as db:
        for name, hours in [("new-review", 1), ("old-review", 25)]:
            row = db.get(Candidate, candidate(db, name, 80))
            row.status = "REVIEW"
            row.available_at = now() - timedelta(hours=hours)
    assert pipeline.cleanup(5)["candidates_expired"] == 1
    with postgres() as db:
        assert db.scalar(select(Candidate.excerpt)) == "new-review"


def test_duplicate_email_rechecked_after_validation_before_remote_call(postgres, monkeypatch):
    with postgres() as db:
        for name, score in [("first", 90), ("second", 80)]:
            row = db.get(Candidate, candidate(db, name, score))
            row.contacts = [{"email": "shared@example.org", "source_url": row.source_url}]
    monkeypatch.setattr(pipeline.local, "classify", lambda _text: None)
    monkeypatch.setattr(pipeline, "validate_contact", lambda _email: ("DELIVERABLE_DOMAIN", "fixture"))
    calls = []
    monkeypatch.setattr(pipeline.semantic, "classify", lambda _text, score: calls.append(score) or semantic_pass())
    pipeline.validate_batch(5)
    pipeline.classify_batch(5)
    assert calls == [90]
    with postgres() as db:
        assert db.scalar(select(func.count()).select_from(Lead)) == 1
        assert db.scalar(select(func.count()).select_from(Candidate)) == 1
