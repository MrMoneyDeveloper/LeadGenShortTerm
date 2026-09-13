from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.models import Candidate, Classification, Lead, SourceRecord, now
from app.qualification import digest
from app.services import pipeline

pytestmark = pytest.mark.integration


def raw(number):
    body = (
        "Need a quote for car insurance in Johannesburg; premium increased and I am changing insurer. "
        f"recovery-{number}@example.org"
    )
    return SourceRecord(
        source="recovery",
        source_record_id=str(number),
        source_url=f"https://example.test/{number}",
        identity_hash=digest(f"recovery:{number}"),
        content_hash=digest(body),
        text=body,
        published_at=now(),
    )


def test_normalize_transaction_rolls_back_then_restarts(postgres, monkeypatch):
    with postgres() as db:
        db.add_all([raw(1), raw(2)])
    original = pipeline.score
    calls = 0

    def interrupt(text, source, published_at):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("controlled interruption")
        return original(text, source, published_at)

    monkeypatch.setattr(pipeline, "score", interrupt)
    with pytest.raises(RuntimeError, match="controlled interruption"):
        pipeline.normalize_batch(2)
    with postgres() as db:
        assert db.scalar(select(func.count()).select_from(Candidate)) == 0
        assert db.scalar(select(func.count()).select_from(SourceRecord)) == 2
    monkeypatch.setattr(pipeline, "score", original)
    assert pipeline.normalize_batch(2) == {"normalized": 2}
    with postgres() as db:
        assert db.scalar(select(func.count()).select_from(Candidate)) == 2


def add_classify_candidate(db, number):
    candidate = Candidate(
        source="recovery",
        source_url=f"https://example.test/classify/{number}",
        identity_hash=digest(f"classify:{number}"),
        excerpt=f"ambiguous car insurance request {number}",
        product_type="MOTOR",
        score=100,
        contacts=[{"email": f"classify-{number}@example.org", "source_url": "https://example.test",
                   "status": "DELIVERABLE_DOMAIN", "validated_at": now().isoformat()}],
        status="CLASSIFY",
        created_at=now() + timedelta(microseconds=number),
    )
    db.add(candidate)
    db.flush()
    db.add(
        Classification(
            candidate_id=candidate.id,
            provider="rules",
            version="fixture",
            result={"signals": ["south_africa"], "score": 100, "product_type": "MOTOR"},
        )
    )


def test_classification_commits_completed_items_and_resumes(postgres, monkeypatch):
    with postgres() as db:
        add_classify_candidate(db, 1)
        add_classify_candidate(db, 2)
    calls = 0

    def interrupt(_text):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("controlled interruption")
        return {"label": "HIGH_INTENT", "confidence": 0.99, "version": "fixture"}

    monkeypatch.setattr(pipeline.local, "classify", interrupt)
    with pytest.raises(RuntimeError, match="controlled interruption"):
        pipeline.classify_batch(2)
    with postgres() as db:
        assert list(db.scalars(select(Candidate.status).order_by(Candidate.created_at))) == ["VALIDATED", "CLASSIFY"]
    monkeypatch.setattr(
        pipeline.local,
        "classify",
        lambda _text: {"label": "HIGH_INTENT", "confidence": 0.99, "version": "fixture"},
    )
    assert pipeline.classify_batch(2) == {"classified": 1}
    with postgres() as db:
        assert set(db.scalars(select(Candidate.status))) == {"VALIDATED"}
        assert db.scalar(select(func.count()).select_from(Classification).where(Classification.provider == "local")) == 2


def test_validation_commits_completed_items_and_resumes_without_duplicates(postgres, monkeypatch):
    with postgres() as db:
        for number in (1, 2):
            db.add(
                Candidate(
                    source="recovery",
                    source_url=f"https://example.test/validate/{number}",
                    identity_hash=digest(f"validate:{number}"),
                    excerpt="strong car insurance request",
                    contacts=[{"email": f"validation-{number}@example.org", "source_url": "https://example.test"}],
                    product_type="MOTOR",
                    score=90,
                    status="VALIDATE",
                    created_at=now() + timedelta(microseconds=number),
                )
            )
    calls = 0

    def interrupt(_email):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("controlled interruption")
        return "DELIVERABLE_DOMAIN", "controlled_fixture"

    monkeypatch.setattr(pipeline, "validate_contact", interrupt)
    with pytest.raises(RuntimeError, match="controlled interruption"):
        pipeline.validate_batch(2)
    with postgres() as db:
        assert db.scalar(select(func.count()).select_from(Lead)) == 0
        assert list(db.scalars(select(Candidate.status).order_by(Candidate.created_at))) == ["CLASSIFY", "VALIDATE"]
    monkeypatch.setattr(pipeline, "validate_contact", lambda _email: ("DELIVERABLE_DOMAIN", "controlled_fixture"))
    assert pipeline.validate_batch(2) == {"validated_checked": 1}
    assert pipeline.validate_batch(2) == {"validated_checked": 0}
    with postgres() as db:
        assert db.scalar(select(func.count()).select_from(Lead)) == 0
        assert set(db.scalars(select(Candidate.status))) == {"CLASSIFY"}
