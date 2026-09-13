from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.collectors.base import Batch, Record
from app.models import (
    Candidate,
    Dedupe,
    EmailValidation,
    Identity,
    Job,
    Lead,
    PipelineState,
    SourceCursor,
    SourceRecord,
    now,
)
from app.repositories import claim_hashes
from app.services import pipeline

pytestmark = pytest.mark.integration


def test_hash_claim_is_atomic(postgres):
    with postgres() as db:
        assert claim_hashes(db, ["content:a"])
        assert not claim_hashes(db, ["content:a", "record:b"])
        assert claim_hashes(db, ["record:b"])


def test_cursor_and_replay(postgres, monkeypatch):
    class Fake:
        def collect(self, cursor, limit):
            return Batch(
                records=[
                    Record(
                        "post1",
                        "person1",
                        "need a quote for car insurance in Johannesburg premium increased changing insurer user@example.org",
                        "https://example.org/post1",
                        now(),
                    )
                ],
                cursor={"time_us": 123},
                scanned=1,
            )

    monkeypatch.setitem(pipeline.ADAPTERS, "bluesky", Fake)
    with postgres() as db:
        db.add(SourceCursor(source="bluesky", enabled=True))
        job = Job(idempotency_key="test-collect", kind="collect", source="bluesky", batch_size=5)
        db.add(job)
        db.flush()
    pipeline.collect(job)
    pipeline.collect(job)
    with postgres() as db:
        assert db.get(SourceCursor, "bluesky").cursor == {"time_us": 123}
        assert db.scalar(select(func.count()).select_from(SourceRecord)) == 1
    pipeline.normalize_batch(5)
    pipeline.normalize_batch(5)
    monkeypatch.setattr(pipeline, "validate_contact", lambda email: ("DELIVERABLE_DOMAIN", "syntax_and_mx_passed"))
    pipeline.validate_batch(5)
    pipeline.validate_batch(5)
    pipeline.classify_batch(5)
    with postgres() as db:
        assert db.scalar(select(func.count()).select_from(Lead)) == 1
        assert db.scalar(select(func.count()).select_from(SourceRecord)) == 0


def test_retention_preserves_pending_raw(postgres):
    with postgres() as db:
        for i, state in enumerate(["PENDING", "REJECTED"]):
            db.add(
                SourceRecord(
                    source="bluesky",
                    source_record_id=str(i),
                    source_url="https://example.org",
                    identity_hash=str(i),
                    content_hash=str(i),
                    text="old",
                    published_at=now(),
                    created_at=now() - timedelta(days=10),
                    status=state,
                )
            )
        db.add(
            Candidate(
                source="bluesky",
                source_url="https://example.org",
                identity_hash="expired",
                excerpt="evidence",
                product_type="MOTOR",
                score=40,
                status="NO_CONTACT",
                created_at=now() - timedelta(days=40),
            )
        )
    pipeline.cleanup(100)
    with postgres() as db:
        assert db.scalar(select(func.count()).select_from(SourceRecord)) == 1
        assert db.scalar(select(SourceRecord.status)) == "PENDING"
        assert db.scalar(select(func.count()).select_from(Candidate)) == 0


def test_retention_keeps_durable_lead_and_checkpoint_state(postgres):
    old = now() - timedelta(days=40)
    identity_hash = "i" * 64
    email_hash = "e" * 64
    with postgres() as db:
        db.add(PipelineState(id=1, paused=True, updated_at=old))
        db.add(SourceCursor(source="retention", enabled=False, cursor={"checkpoint": 17}, updated_at=old))
        db.add(Identity(identity_hash=identity_hash, email_hash=email_hash, first_seen_at=old, last_seen_at=old))
        db.add_all(
            [
                Dedupe(key="record:" + "r" * 64, created_at=old),
                Dedupe(key="content:" + "c" * 64, created_at=old),
                Dedupe(key="email:" + email_hash, created_at=old),
                Dedupe(key="lead:" + identity_hash, created_at=old),
                Dedupe(key="candidate:" + identity_hash, created_at=old),
            ]
        )
        db.add(
            Candidate(
                source="retention",
                source_url="https://example.org/old",
                identity_hash=identity_hash,
                excerpt="old candidate",
                product_type="MOTOR",
                score=50,
                status="NO_CONTACT",
                created_at=old,
            )
        )
        db.add(
            Lead(
                email="retained@example.org",
                email_hash=email_hash,
                identity_hash=identity_hash,
                source="retention",
                source_url="https://example.org/lead",
                email_source_url="https://example.org/lead",
                excerpt="retained lead",
                product_type="MOTOR",
                score=90,
                created_at=old,
            )
        )
        db.add(EmailValidation(email_hash="stale", status="NO_MX", reason="no_mx", checked_at=old))

    pipeline.cleanup(100)

    with postgres() as db:
        assert db.get(SourceCursor, "retention").cursor == {"checkpoint": 17}
        assert db.get(PipelineState, 1) is not None
        assert db.scalar(select(func.count()).select_from(Lead)) == 1
        assert db.get(Identity, identity_hash) is not None
        durable = {"record:" + "r" * 64, "content:" + "c" * 64, "email:" + email_hash, "lead:" + identity_hash}
        # Unacknowledged final output protects its working candidate and claim by identity,
        # including pre-migration leads that do not yet have candidate_id.
        durable.add("candidate:" + identity_hash)
        assert set(db.scalars(select(Dedupe.key)).all()) == durable
        assert db.scalar(select(func.count()).select_from(Candidate)) == 1
        assert db.get(EmailValidation, "stale") is None
