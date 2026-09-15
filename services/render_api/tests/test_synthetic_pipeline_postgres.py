from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.classifiers.grok import SemanticResult
from app.models import Candidate, Dedupe, Identity, Lead, SourceCursor, SourceRecord, now
from app.qualification import digest
from app.services import pipeline

pytestmark = pytest.mark.integration


def add_raw(db, number, identity, text, status="PENDING", age_days=0):
    db.add(
        SourceRecord(
            source="synthetic",
            source_record_id=str(number),
            source_url=f"https://example.org/post/{number}",
            identity_hash=digest("synthetic:" + identity),
            content_hash=digest(text),
            text=text,
            published_at=now() - timedelta(days=age_days),
            status=status,
        )
    )
    db.execute(insert(Identity).values(identity_hash=digest("synthetic:" + identity)).on_conflict_do_nothing())


def test_complete_synthetic_funnel_and_idempotency(postgres, isolated_settings, monkeypatch):
    isolated_settings.delete_processed_raw = True
    records = [
        (
            1,
            "motor",
            "Need a quote for car insurance in Johannesburg, premium increased, changing insurer. motor@example.org",
        ),
        (
            2,
            "home",
            "Need a quote and insurance recommendations for home insurance in Cape Town, changing insurer. home@example.org",
        ),
        (3, "broker", "Insurance broker advertisement: contact me for insurance in Johannesburg broker@example.org"),
        (4, "job", "Hiring for an insurance job in Johannesburg jobs@example.org"),
        (5, "news", "Insurance news press release in Johannesburg news@example.org"),
        (6, "weak", "I saw insurance yesterday in Johannesburg"),
        (7, "repeat", "Need car insurance in Johannesburg; premium increased. repeated@example.org"),
        (8, "repeat", "Need a quote for vehicle insurance in Johannesburg. second@example.org"),
        (
            9,
            "dup-email-a",
            "Need a quote for car insurance Johannesburg, premium increased, changing insurer. shared@example.org",
        ),
        (
            10,
            "dup-email-b",
            "Need a quote for home insurance Cape Town, premium increased, changing insurer. shared@example.org",
        ),
        (11, "bad-mail", "Need a quote for car insurance Johannesburg, premium increased, changing insurer. person@@bad"),
        (
            12,
            "no-mx",
            "Need a quote for contents insurance Pretoria, premium increased, changing insurer. person@nomx.example.net",
        ),
        (13, "irrelevant", "Lovely weather and football today"),
        (14, "ambiguous", "Car insurance in Durban is too expensive, any thoughts? ambiguous@example.org"),
    ]
    with postgres() as db:
        for item in records:
            add_raw(db, *item)
    monkeypatch.setattr(pipeline.local, "classify", lambda text: None)
    monkeypatch.setattr(
        pipeline.semantic,
        "classify",
        lambda text, score: SemanticResult(
            is_short_term_insurance_relevant=True,
            intent_level="MEDIUM",
            product_type="MOTOR",
            is_consumer=True,
            is_advertisement=False,
            is_broker_or_agent=False,
            south_africa_signal=True,
            score=85,
            reason="Consumer asks about cost",
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "validate_contact",
        lambda email: (
            ("NO_MX", "no_mx") if email.endswith("@nomx.example.net") else ("DELIVERABLE_DOMAIN", "syntax_and_mx_passed")
        ),
    )

    assert pipeline.normalize_batch(100)["normalized"] == len(records)
    assert pipeline.normalize_batch(100)["normalized"] == 0
    pipeline.validate_batch(100)
    pipeline.validate_batch(100)
    pipeline.classify_batch(100)

    with postgres() as db:
        leads = db.scalars(select(Lead).order_by(Lead.email)).all()
        emails = [lead.email for lead in leads]
        assert "motor@example.org" in emails and "home@example.org" in emails
        assert "ambiguous@example.org" not in emails  # Low evidence stays local-only under the new rank policy.
        assert emails.count("shared@example.org") == 1
        assert db.scalar(select(func.count()).select_from(Lead)) == 3
        # Three accepted leads plus no-contact, malformed-contact and no-MX review rows remain.
        assert db.scalar(select(func.count()).select_from(Candidate)) == 6
        states = dict(db.execute(select(Candidate.identity_hash, Candidate.status)).all())
        assert "CONTACT_REVIEW" in states.values()
        assert db.scalar(select(func.count()).select_from(SourceRecord)) == 0
        assert db.scalar(select(func.count()).select_from(Dedupe).where(Dedupe.key.like("email:%"))) == 3
        assert db.scalar(select(func.count()).select_from(Dedupe).where(Dedupe.key.like("lead:%"))) == 3
        assert db.get(SourceCursor, "synthetic") is None

    # Re-adding a source record after reduction cannot produce another accepted lead.
    with postgres() as db:
        add_raw(db, 99, "motor", records[0][2])
    pipeline.normalize_batch(10)
    pipeline.validate_batch(10)
    with postgres() as db:
        assert db.scalar(select(func.count()).select_from(Lead)) == 3
