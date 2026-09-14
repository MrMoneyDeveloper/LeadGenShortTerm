"""Backlog hysteresis, campaign state and database safety shared by collection entry points."""

from sqlalchemy import func, select, text

from app.config import settings
from app.database import session
from app.models import Candidate, ExportBatch, Lead, PipelineState, SourceRecord
from app.services import campaign


def inspect(db, database_bytes=None):
    cfg = settings()
    state = db.scalar(select(PipelineState).where(PipelineState.id == 1).with_for_update())
    counts = {
        "raw": db.scalar(select(func.count()).select_from(SourceRecord)),
        "candidates": db.scalar(select(func.count()).select_from(Candidate)),
        "exports": db.scalar(select(func.count()).select_from(Lead)),
    }
    total = sum(counts.values())
    claimed_batches = db.scalar(
        select(func.count()).select_from(ExportBatch).where(ExportBatch.status == "CLAIMED")
    )
    state = campaign.observe(db, total, claimed_batches) if state is not None else None

    database_bytes = (
        database_bytes
        if database_bytes is not None
        else db.scalar(text("SELECT pg_database_size(current_database())"))
    )
    queue_draining = total >= cfg.queue_high_water or bool(
        state and state.draining and total > cfg.queue_low_water
    )
    pressure = database_bytes >= cfg.database_high_water_bytes or bool(
        state and state.storage_pressure and database_bytes > cfg.database_low_water_bytes
    )
    campaign_status = state.campaign_status if state else "READY"
    campaign_draining = campaign_status in {"DRAINING", "FINALIZING"}
    if state:
        state.draining = queue_draining or campaign_draining
        state.storage_pressure = pressure

    paused = state is None or state.paused
    if pressure:
        mode = "storage_pressure"
    elif paused and campaign_status != "COMPLETE":
        mode = "paused"
    elif campaign_status == "COMPLETE":
        mode = "campaign_complete"
    elif campaign_draining or queue_draining:
        mode = "drain"
    else:
        mode = "collect"

    collect_allowance = 0
    if mode == "collect":
        collect_allowance = max(
            0,
            min(cfg.queue_high_water - total, cfg.max_pending_raw - counts["raw"]),
        )
        if campaign_status == "RUNNING" and state.campaign_raw_target:
            collect_allowance = min(
                collect_allowance,
                max(0, state.campaign_raw_target - state.campaign_raw_scanned),
            )

    return {
        "mode": mode,
        "counts": counts,
        "total_work_rows": total,
        "database_bytes": database_bytes,
        "queue_high_water": cfg.queue_high_water,
        "queue_low_water": cfg.queue_low_water,
        "database_high_water_bytes": cfg.database_high_water_bytes,
        "collect_allowance": collect_allowance,
        "campaign": campaign.payload(state) if state else None,
        "export_required": counts["exports"] > 0,
        "claimed_batches": claimed_batches,
        "note": "Acquisition/source flags still apply; export ACK remains available while paused or full",
    }


def status():
    with session() as db:
        return inspect(db)
