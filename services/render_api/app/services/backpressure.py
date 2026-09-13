"""Backlog hysteresis and database safety shared by every collection entry point."""

from sqlalchemy import func, select, text

from app.config import settings
from app.database import session
from app.models import Candidate, ExportBatch, Lead, PipelineState, SourceRecord, now


def inspect(db, database_bytes=None):
    cfg = settings()
    state = db.scalar(select(PipelineState).where(PipelineState.id == 1).with_for_update())
    counts = {
        "raw": db.scalar(select(func.count()).select_from(SourceRecord)),
        "candidates": db.scalar(select(func.count()).select_from(Candidate)),
        "exports": db.scalar(select(func.count()).select_from(Lead)),
    }
    total = sum(counts.values())
    database_bytes = database_bytes if database_bytes is not None else db.scalar(text("SELECT pg_database_size(current_database())"))
    draining = total >= cfg.queue_high_water or bool(state and state.draining and total > cfg.queue_low_water)
    pressure = database_bytes >= cfg.database_high_water_bytes or bool(state and state.storage_pressure and database_bytes > cfg.database_low_water_bytes)
    if state:
        state.draining, state.storage_pressure = draining, pressure
    expired = bool(cfg.campaign_deadline and now() >= cfg.campaign_deadline)
    paused = state is None or state.paused
    mode = "storage_pressure" if pressure else "paused" if paused else "campaign_complete" if expired else "drain" if draining else "collect"
    return {
        "mode": mode, "counts": counts, "total_work_rows": total, "database_bytes": database_bytes,
        "queue_high_water": cfg.queue_high_water, "queue_low_water": cfg.queue_low_water,
        "database_high_water_bytes": cfg.database_high_water_bytes,
        "collect_allowance": max(0, min(cfg.queue_high_water - total, cfg.max_pending_raw - counts["raw"])) if mode == "collect" else 0,
        "campaign_id": cfg.campaign_id, "campaign_deadline": cfg.campaign_deadline,
        "export_required": counts["exports"] > 0,
        "claimed_batches": db.scalar(select(func.count()).select_from(ExportBatch).where(ExportBatch.status == "CLAIMED")),
        "note": "Acquisition/source flags still apply; export ACK remains available while paused or full",
    }


def status():
    with session() as db:
        return inspect(db)
