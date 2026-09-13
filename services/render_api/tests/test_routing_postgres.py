from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.classifiers.grok import SemanticResult
from app.models import Candidate, Classification, Lead, now
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
        raise pipeline.semantic.SemanticUnavailable("groq_daily_cap")

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
