import logging
import threading
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.collectors.base import SourceError
from app.config import rules, settings
from app.database import advisory_lock, session
from app.models import Failure, Job, PipelineState, SourceCursor, now
from app.repositories import metric
from app.services import pipeline

KINDS = ("collect", "normalize", "classify", "validate", "cleanup")
logger = logging.getLogger("leadgen.jobs")


def initialize():
    with session() as db:
        db.execute(insert(PipelineState).values(id=1, paused=True).on_conflict_do_nothing())
        for name in ("bluesky", "youtube"):
            db.execute(insert(SourceCursor).values(source=name, enabled=False, cursor={}).on_conflict_do_nothing())


def permitted(db, kind, source=None):
    cfg = settings()
    if kind == "cleanup":
        return True
    state = db.get(PipelineState, 1)
    if state is None or state.paused:
        return False
    if kind == "collect":
        row = db.get(SourceCursor, source)
        return bool(
            cfg.acquisition_enabled
            and getattr(cfg, source + "_enabled", False)
            and rules("sources").get(source, {}).get("enabled", False)
            and row
            and row.enabled
            and (row.retry_at is None or row.retry_at <= now())
        )
    return cfg.processing_enabled


def enqueue(kind, source, key, batch_size=None):
    with session() as db:
        existing = db.scalar(select(Job).where(Job.idempotency_key == key))
        if existing:
            if existing.kind != kind or existing.source != source or (batch_size and existing.batch_size != batch_size):
                raise ValueError("idempotency_key_conflict")
            return existing
        if not permitted(db, kind, source):
            raise ValueError("pipeline_or_source_disabled")
        db.execute(
            insert(Job)
            .values(
                idempotency_key=key, kind=kind, source=source, batch_size=batch_size or settings().processing_batch_size
            )
            .on_conflict_do_nothing()
        )
        stored = db.scalar(select(Job).where(Job.idempotency_key == key))
        if stored.kind != kind or stored.source != source or (batch_size and stored.batch_size != batch_size):
            raise ValueError("idempotency_key_conflict")
        return stored


def run_next():
    # One bounded executor across all web processes. A killed worker loses the advisory lock;
    # the next executor reclaims RUNNING jobs immediately without stale lease races.
    with advisory_lock(7112001) as acquired:
        if not acquired:
            return {"status": "busy"}
        with session() as db:
            for abandoned in db.scalars(select(Job).where(Job.status == "RUNNING")):
                abandoned.status = "QUEUED"
            db.flush()
            jobs = db.scalars(
                select(Job).where(Job.status == "QUEUED", Job.available_at <= now()).order_by(Job.created_at).limit(100)
            ).all()
            job = next((j for j in jobs if permitted(db, j.kind, j.source)), None)
            if job is None:
                return {"status": "idle"}
            if job.attempts >= settings().job_max_attempts:
                job.status, job.completed_at = "FAILED", now()
                db.add(Failure(job_id=job.id, source=job.source, code="attempts_exhausted"))
                return {"status": "failed", "job_id": job.id}
            job.status, job.attempts = "RUNNING", job.attempts + 1
            job_id = job.id
        try:
            if job.kind == "collect":
                result = pipeline.collect(job)
            else:
                fn = {
                    "normalize": pipeline.normalize_batch,
                    "classify": pipeline.classify_batch,
                    "validate": pipeline.validate_batch,
                    "cleanup": pipeline.cleanup,
                }[job.kind]
                result = fn(job.batch_size)
            with session() as db:
                stored = db.get(Job, job_id)
                stored.status, stored.completed_at, stored.result = "COMPLETE", now(), result
            logger.info("job_complete kind=%s job_id=%s", job.kind, job_id)
            return {"status": "complete", "job_id": job_id, "result": result}
        except Exception as exc:
            code = exc.code if isinstance(exc, SourceError) else type(exc).__name__
            delay = exc.retry_seconds if isinstance(exc, SourceError) else min(3600, 30 * 2**job.attempts)
            with session() as db:
                stored = db.get(Job, job_id)
                stored.status = "FAILED" if stored.attempts >= settings().job_max_attempts else "QUEUED"
                stored.available_at = now() + timedelta(seconds=delay)
                stored.result = {"error_code": code}
                if stored.status == "FAILED":
                    stored.completed_at = now()
                db.add(Failure(job_id=job_id, source=job.source, code=code))
                metric(db, job.source or "all", "failures")
                if job.kind == "collect":
                    source = db.get(SourceCursor, job.source)
                    source.failures += 1
                    source.retry_at = now() + timedelta(seconds=delay)
                    if source.failures >= settings().job_max_attempts:
                        source.enabled = False
            logger.warning("job_failed job_id=%s code=%s", job_id, code)
            return {"status": "retry_or_failed", "job_id": job_id, "error_code": code}


def schedule_tick():
    cfg = settings()
    bucket = int(now().timestamp()) // cfg.scheduler_interval_seconds
    for kind, source in [
        ("collect", "bluesky"),
        ("collect", "youtube"),
        ("normalize", None),
        ("classify", None),
        ("validate", None),
        ("cleanup", None),
    ]:
        with session() as db:
            pending = db.scalar(
                select(Job.id)
                .where(Job.kind == kind, Job.source == source, Job.status.in_(["QUEUED", "RUNNING"]))
                .limit(1)
            )
        if pending:
            continue
        try:
            enqueue(kind, source, f"schedule:{bucket}:{kind}:{source}")
        except ValueError:
            continue


class Scheduler:
    def __init__(self):
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.loop, name="leadgen-scheduler", daemon=True)

    def loop(self):
        while not self.stop.is_set():
            try:
                with advisory_lock(7112002) as leader:
                    if leader:
                        schedule_tick()
                run_next()
            except Exception as exc:
                # Exception strings can contain credentials or SQL values; log the class only.
                logger.error("scheduler_error code=%s", type(exc).__name__)
            self.stop.wait(settings().scheduler_interval_seconds)

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=5)
