import csv
import io

from sqlalchemy import func, select, text

from app.config import settings
from app.database import session
from app.models import (
    Candidate,
    ExportBatch,
    Failure,
    Job,
    Lead,
    Metric,
    PipelineState,
    SourceCursor,
    SourceRecord,
    Usage,
    now,
)

LEAD_FIELDS = [
    "id",
    "email",
    "email_hash",
    "identity_hash",
    "source",
    "source_url",
    "email_source_url",
    "excerpt",
    "product_type",
    "score",
    "status",
    "validation_status",
    "created_at",
    "profile",
    "rank_score",
]


def row_dict(row, fields=None):
    fields = fields or [c.name for c in row.__table__.columns]
    return {name: getattr(row, name) for name in fields}


def summary():
    with session() as db:
        metrics = db.execute(
            select(Metric.name, func.sum(Metric.value))
            .where(Metric.day == now().date(), Metric.source != "all")
            .group_by(Metric.name)
        ).all()
        cfg = settings()
        state = db.get(PipelineState, 1)
        return {
            "as_of": now().isoformat(),
            "environment": cfg.environment,
            "paused": state is None or state.paused,
            "flags": {
                k: getattr(cfg, k)
                for k in [
                    "acquisition_enabled",
                    "processing_enabled",
                    "bluesky_enabled",
                    "youtube_enabled",
                    "grok_enabled",
                    "groq_enabled",
                    "final_delivery_enabled",
                    "scheduler_enabled",
                ]
            },
            "today": dict(metrics),
            "candidates": db.scalar(select(func.count()).select_from(Candidate)),
            "validated": db.scalar(select(func.count()).select_from(Lead)),
            "final_output_location": "Google Sheets VALIDATED_001 and subsequent shards; Drive backup",
            "delivered": db.scalar(select(func.sum(ExportBatch.count)).where(ExportBatch.status == "ACKNOWLEDGED")) or 0,
            "semantic_provider": cfg.semantic_provider,
            "raw_pending": db.scalar(
                select(func.count()).select_from(SourceRecord).where(SourceRecord.status == "PENDING")
            ),
            "oldest_pending_raw": db.scalar(
                select(func.min(SourceRecord.created_at)).where(SourceRecord.status == "PENDING")
            ),
            "database_bytes": db.scalar(text("SELECT pg_database_size(current_database())")),
            "api_usage": [row_dict(u) for u in db.scalars(select(Usage).order_by(Usage.day.desc()).limit(12))],
            "grok_cost_configured": bool(cfg.grok_input_usd_per_million and cfg.grok_output_usd_per_million),
        }


def source_stats():
    with session() as db:
        result = []
        for source in db.scalars(select(SourceCursor).order_by(SourceCursor.source)):
            totals = dict(
                db.execute(
                    select(Metric.name, func.sum(Metric.value))
                    .where(Metric.source == source.source)
                    .group_by(Metric.name)
                ).all()
            )
            result.append(
                {
                    **row_dict(source),
                    **totals,
                    "conversion_rate": totals.get("validated", 0) / max(1, totals.get("raw_scanned", 0)),
                }
            )
        return result


def queue():
    with session() as db:
        jobs = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())
        candidates = dict(db.execute(select(Candidate.status, func.count()).group_by(Candidate.status)).all())
        return {
            "jobs": jobs,
            "candidates": candidates,
            "recent_jobs": [row_dict(j) for j in db.scalars(select(Job).order_by(Job.created_at.desc()).limit(50))],
        }


def failures():
    with session() as db:
        return [row_dict(f) for f in db.scalars(select(Failure).order_by(Failure.created_at.desc()).limit(100))]


def lead_page(after=0, through=None, limit=1000):
    with session() as db:
        if through is None:
            through = db.scalar(select(func.max(Lead.id))) or 0
        rows = db.scalars(
            select(Lead).where(Lead.id > after, Lead.id <= through).order_by(Lead.id).limit(limit + 1)
        ).all()
        more = len(rows) > limit
        rows = rows[:limit]
        return {
            "items": [row_dict(row, LEAD_FIELDS) for row in rows],
            "through": through,
            "next_after": rows[-1].id if rows and more else None,
        }


def csv_safe(value):
    value = "" if value is None else str(value)
    if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def csv_text(rows, fields):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow(fields)
    for row in rows:
        writer.writerow([csv_safe(row.get(field)) for field in fields])
    return stream.getvalue()
