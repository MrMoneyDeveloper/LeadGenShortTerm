"""Bounded per-source sample store (SAMPLE_STORE_MAX_PER_SOURCE, default off).

Keeps at most N records per source with their public provenance (source URL, profile URL,
published time) and the pipeline's decision, for pre-Phase-4 testing and model training.
Higher-priority outcomes replace the lowest-priority (then oldest) sample once a source is full:

    0 rejected, no insurance term   1 rejected but insurance-relevant   2 candidate
    3 contact validated / semantic   4 final lead
"""

from sqlalchemy import delete, func, select

from app.config import settings
from app.models import SampleRecord, now

PRIORITY = {"rejected": 0, "rejected_relevant": 1, "candidate": 2, "no_contact": 2, "contact_review": 2,
            "rejected_late": 2, "deferred": 3, "review": 3, "semantic_rejected": 3, "final": 4}


def capture(db, record, outcome, detail, candidate_id=None):
    cap = settings().sample_store_max_per_source
    if cap <= 0:
        return False
    priority = PRIORITY.get(outcome, 0)
    existing = db.scalar(select(SampleRecord).where(
        SampleRecord.source == record.source, SampleRecord.source_record_id == record.source_record_id))
    if existing:
        _apply(existing, outcome, priority, detail, candidate_id)
        return True
    count = db.scalar(select(func.count()).select_from(SampleRecord).where(SampleRecord.source == record.source))
    if count >= cap:
        weakest = db.scalar(select(SampleRecord).where(SampleRecord.source == record.source)
                            .order_by(SampleRecord.priority, SampleRecord.captured_at).limit(1))
        if weakest is None or weakest.priority >= priority:
            return False
        db.delete(weakest)
        db.flush()
    db.add(SampleRecord(
        source=record.source, source_record_id=record.source_record_id, candidate_id=candidate_id,
        source_url=record.source_url, text=record.text[:8000], profile=record.profile or {},
        published_at=record.published_at, outcome=outcome, detail=detail, priority=priority,
    ))
    return True


def update(db, candidate_id, outcome, detail=None):
    """Advance a sample's outcome as its candidate moves through later stages."""
    if settings().sample_store_max_per_source <= 0 or not candidate_id:
        return
    sample = db.scalar(select(SampleRecord).where(SampleRecord.candidate_id == candidate_id))
    if sample:
        outcome = outcome or sample.outcome  # None annotates detail without changing the outcome
        _apply(sample, outcome, PRIORITY.get(outcome, sample.priority), detail or {}, candidate_id)


def _apply(sample, outcome, priority, detail, candidate_id):
    sample.outcome = outcome
    sample.priority = max(sample.priority, priority)
    sample.detail = {**(sample.detail or {}), **detail}
    sample.candidate_id = candidate_id or sample.candidate_id
    sample.updated_at = now()


FIELDS = ["id", "source", "source_record_id", "source_url", "published_at", "outcome", "priority",
          "text", "profile", "detail", "captured_at", "updated_at"]


def listing(db, source=None, limit=200):
    query = select(SampleRecord).order_by(SampleRecord.source, SampleRecord.priority.desc(), SampleRecord.captured_at)
    if source:
        query = query.where(SampleRecord.source == source)
    return [{f: getattr(row, f) for f in FIELDS} for row in db.scalars(query.limit(limit))]


def remove(db, source=None, source_record_id=None):
    """Explicit operator deletion, e.g. when a person declines; returns rows removed."""
    query = delete(SampleRecord)
    if source:
        query = query.where(SampleRecord.source == source)
    if source_record_id:
        query = query.where(SampleRecord.source_record_id == source_record_id)
    return db.execute(query).rowcount
