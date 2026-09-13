from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text

from app import jobs
from app.api.auth import dashboard, operator
from app.database import session
from app.models import Candidate, Job, PipelineState, SourceCursor, now
from app.services import reporting
from app.services import backpressure, delivery

router = APIRouter()


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["bluesky", "youtube"] | None = None
    stage: Literal["normalize", "classify", "validate"] = "normalize"
    batch_size: int | None = Field(None, ge=1, le=2000)


@router.get("/health")
def health(response: Response):
    try:
        with session() as db:
            db.execute(text("SELECT 1 FROM alembic_version LIMIT 1"))
        return {"status": "ok"}
    except Exception:
        response.status_code = 503
        return {"status": "database_unavailable_or_unmigrated"}


@router.get("/dashboard/summary", dependencies=[Depends(dashboard)])
@router.get("/metrics/summary", dependencies=[Depends(dashboard)])
def summary():
    return reporting.summary()


@router.get("/dashboard/source-stats", dependencies=[Depends(dashboard)])
def source_stats():
    return reporting.source_stats()


@router.get("/dashboard/queue", dependencies=[Depends(dashboard)])
def queue():
    return reporting.queue()


@router.get("/dashboard/backpressure", dependencies=[Depends(dashboard)])
def pressure():
    return backpressure.status()


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int | None = Field(None, ge=1, le=200)


class Placement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    lead_id: int = Field(ge=1, le=9_007_199_254_740_991)
    tab: str = Field(pattern=r"^VALIDATED_[0-9]{3,}$", max_length=40)
    row: int = Field(ge=2, le=5001)


class AckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    batch_id: str = Field(pattern=r"^[a-f0-9-]{36}$")
    checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    spreadsheet_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,160}$")
    drive_folder_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,160}$")
    drive_file_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,160}$")
    drive_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    placements: list[Placement] = Field(min_length=1, max_length=200)


@router.post("/exports/claim", dependencies=[Depends(operator)])
def claim_export(body: ClaimRequest):
    try:
        return delivery.claim(body.limit)
    except delivery.DeliveryError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post("/exports/ack", dependencies=[Depends(operator)])
def ack_export(body: AckRequest):
    try:
        return delivery.acknowledge(body.model_dump())
    except delivery.DeliveryError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/exports/history", dependencies=[Depends(dashboard)])
def delivery_history(limit: int = Query(100, ge=1, le=200)):
    return delivery.history(limit)


@router.get("/dashboard/failures", dependencies=[Depends(dashboard)])
def failures():
    return reporting.failures()


@router.post("/jobs/process-next", dependencies=[Depends(operator)], status_code=202)
def process_next(background: BackgroundTasks):
    background.add_task(jobs.run_next)
    return {"status": "executor_requested", "note": "Inspect dashboard/queue for durable job status"}


@router.post("/jobs/tick", dependencies=[Depends(operator)], status_code=202)
def tick(background: BackgroundTasks):
    jobs.schedule_tick()
    background.add_task(jobs.run_next)
    return {"status": "scheduled"}


@router.post("/jobs/{action}", dependencies=[Depends(operator)], status_code=202)
def submit(
    action: Literal["collect", "process", "cleanup", "run-batch"],
    body: JobRequest,
    key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
):
    kind = body.stage if action in {"process", "run-batch"} else action
    if kind == "collect" and body.source is None:
        raise HTTPException(422, "source required")
    source = body.source if kind == "collect" else None
    try:
        job = jobs.enqueue(kind, source, key, body.batch_size)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return reporting.row_dict(job)


@router.get("/jobs/{job_id}", dependencies=[Depends(dashboard)])
def job_status(job_id: str):
    with session() as db:
        job = db.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "Job not found")
        return reporting.row_dict(job)


@router.post("/admin/pipeline/{action}", dependencies=[Depends(operator)])
def control(action: Literal["pause", "resume"]):
    with session() as db:
        state = db.get(PipelineState, 1)
        state.paused, state.updated_at = action == "pause", now()
        return {
            "paused": state.paused,
            "note": "Environment and per-source flags still apply; in-flight stage may finish",
        }


@router.post("/admin/sources/{source}/{action}", dependencies=[Depends(operator)])
def source_control(source: Literal["bluesky", "youtube"], action: Literal["enable", "disable"]):
    with session() as db:
        row = db.get(SourceCursor, source)
        row.enabled, row.updated_at = action == "enable", now()
        if row.enabled:
            row.failures, row.retry_at = 0, None
        return reporting.row_dict(row)


@router.post("/admin/candidates/{candidate_id}/retry", dependencies=[Depends(operator)])
def retry_candidate(candidate_id: str):
    with session() as db:
        row = db.get(Candidate, candidate_id)
        if row is None:
            raise HTTPException(404, "Candidate not found")
        if row.status not in {"REVIEW", "CONTACT_REVIEW"}:
            raise HTTPException(409, "Candidate is not in a retryable review state")
        row.status = "CLASSIFY" if row.status == "REVIEW" else "VALIDATE"
        row.available_at, row.attempts = now(), 0
        return {"status": row.status}


@router.post("/admin/jobs/{job_id}/retry", dependencies=[Depends(operator)])
def retry_job(job_id: str):
    with session() as db:
        row = db.get(Job, job_id)
        if row is None:
            raise HTTPException(404, "Job not found")
        if row.status != "FAILED":
            raise HTTPException(409, "Only failed jobs can be reset")
        row.status, row.attempts, row.available_at, row.completed_at = "QUEUED", 0, now(), None
        return reporting.row_dict(row)


@router.get("/dashboard/candidates", dependencies=[Depends(dashboard)])
def candidates(status: str = Query("REVIEW", max_length=32), limit: int = Query(100, ge=1, le=500)):
    with session() as db:
        return [
            reporting.row_dict(
                c, ["id", "source", "source_url", "excerpt", "product_type", "score", "status", "created_at"]
            )
            for c in db.scalars(
                select(Candidate).where(Candidate.status == status).order_by(Candidate.created_at).limit(limit)
            )
        ]


@router.get("/exports/validated", dependencies=[Depends(dashboard)])
@router.get("/leads/validated", dependencies=[Depends(dashboard)])
@router.get("/export/validated.csv", dependencies=[Depends(dashboard)])
def leads(
    after: int = Query(0, ge=0),
    through: int | None = Query(None, ge=0),
    limit: int = Query(1000, ge=1, le=2000),
    format: Literal["json", "csv"] = "csv",
):
    page = reporting.lead_page(after, through, limit)
    if format == "json":
        return page
    return Response(
        reporting.csv_text(page["items"], reporting.LEAD_FIELDS),
        media_type="text/csv",
        headers={
            "X-Export-Through": str(page["through"]),
            "X-Next-After": "" if page["next_after"] is None else str(page["next_after"]),
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="validated-leads.csv"',
        },
    )


@router.get("/exports/source-stats", dependencies=[Depends(dashboard)])
def export_sources():
    rows = reporting.source_stats()
    fields = sorted(set().union(*(row.keys() for row in rows)))
    return Response(reporting.csv_text(rows, fields), media_type="text/csv")


@router.get("/exports/processing-summary", dependencies=[Depends(dashboard)])
def export_summary():
    return {"summary": reporting.summary(), "queue": reporting.queue(), "failures": reporting.failures()}
