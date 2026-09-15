"""Persistent campaign lifecycle for the autonomous seven-day run."""

from datetime import timedelta

from sqlalchemy import func, select

from app.config import settings
from app.database import session
from app.models import Candidate, ExportBatch, Lead, PipelineState, SourceRecord, now

ACTIVE = {"RUNNING", "DRAINING", "FINALIZING"}
TERMINAL = {"COMPLETE", "FAILED"}


class CampaignError(ValueError):
    pass


def payload(state):
    target = int(state.campaign_raw_target or 0)
    scanned = int(state.campaign_raw_scanned or 0)
    return {
        "campaign_id": state.campaign_id,
        "status": state.campaign_status,
        "started_at": state.campaign_started_at,
        "acquisition_deadline": state.campaign_deadline,
        "raw_target": target,
        "raw_scanned": scanned,
        "raw_remaining": max(0, target - scanned) if target else None,
        "last_progress_at": state.campaign_last_progress_at,
        "completed_at": state.campaign_completed_at,
        "semantic_deadline": state.campaign_semantic_deadline,
        "paused": state.paused,
    }


def start(raw_target=None, duration_days=None):
    cfg = settings()
    raw_target = raw_target or cfg.campaign_raw_target
    duration_days = duration_days or cfg.campaign_duration_days
    if raw_target < 1 or raw_target > 10_000_000:
        raise CampaignError("campaign_raw_target_invalid")
    if duration_days < 1 or duration_days > 30:
        raise CampaignError("campaign_duration_invalid")

    with session() as db:
        state = db.scalar(select(PipelineState).where(PipelineState.id == 1).with_for_update())
        if state is None:
            raise CampaignError("pipeline_uninitialized")
        if state.campaign_status in ACTIVE:
            raise CampaignError("campaign_already_active")

        # A new campaign cannot inherit transient payloads or an unacknowledged Google delivery.
        work = sum(
            db.scalar(select(func.count()).select_from(table))
            for table in (SourceRecord, Candidate, Lead)
        )
        claimed = db.scalar(
            select(func.count()).select_from(ExportBatch).where(ExportBatch.status == "CLAIMED")
        )
        if work or claimed:
            raise CampaignError("campaign_work_not_empty")

        started = now()
        state.campaign_id = cfg.campaign_id
        state.campaign_status = "RUNNING"
        state.campaign_started_at = started
        state.campaign_deadline = started + timedelta(days=duration_days)
        state.campaign_raw_target = raw_target
        state.campaign_raw_scanned = 0
        state.campaign_last_progress_at = started
        state.campaign_completed_at = None
        state.campaign_semantic_deadline = None
        state.paused = False
        state.draining = False
        state.storage_pressure = False
        state.updated_at = started
        return payload(state)


def record_scan(db, scanned):
    """Advance the campaign raw counter in the same transaction as the source cursor."""
    if scanned <= 0:
        return
    state = db.scalar(select(PipelineState).where(PipelineState.id == 1).with_for_update())
    if state is None or state.campaign_status != "RUNNING":
        return
    state.campaign_raw_scanned += int(scanned)
    state.campaign_last_progress_at = now()
    if state.campaign_raw_target and state.campaign_raw_scanned >= state.campaign_raw_target:
        state.campaign_status = "DRAINING"
        state.draining = True


def record_progress(db):
    state = db.get(PipelineState, 1)
    if state is not None and state.campaign_status in ACTIVE:
        state.campaign_last_progress_at = now()


def observe(db, total_work_rows, claimed_batches):
    """Advance deadline/target/drain completion without creating any new work."""
    state = db.scalar(select(PipelineState).where(PipelineState.id == 1).with_for_update())
    if state is None:
        return None
    current = now()

    if state.campaign_status == "RUNNING" and (
        (state.campaign_deadline is not None and current >= state.campaign_deadline)
        or (state.campaign_raw_target and state.campaign_raw_scanned >= state.campaign_raw_target)
    ):
        state.campaign_status = "DRAINING"
        state.draining = True
        state.campaign_last_progress_at = current
    elif state.campaign_status == "DRAINING" and total_work_rows == 0 and claimed_batches == 0:
        state.campaign_status = "FINALIZING"
        state.campaign_last_progress_at = current
    elif state.campaign_status == "FINALIZING" and total_work_rows == 0 and claimed_batches == 0:
        state.campaign_status = "COMPLETE"
        state.campaign_completed_at = current
        state.campaign_last_progress_at = current
        state.draining = False
        state.paused = True

    if state.campaign_status in {"DRAINING", "FINALIZING"} and state.campaign_semantic_deadline is None:
        state.campaign_semantic_deadline = current + timedelta(hours=settings().campaign_semantic_drain_hours)

    state.updated_at = current
    return state


def semantic_expired(state, current):
    return bool(state and state.campaign_status in {"DRAINING", "FINALIZING"}
                and state.campaign_semantic_deadline is not None
                and current >= state.campaign_semantic_deadline)


def status():
    with session() as db:
        state = db.get(PipelineState, 1)
        if state is None:
            raise CampaignError("pipeline_uninitialized")
        return payload(state)
