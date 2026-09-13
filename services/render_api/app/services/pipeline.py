import time
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from app.classifiers import grok, local, semantic
from app.collectors import ADAPTERS
from app.collectors.profiles import profile
from app.config import settings
from app.database import session
from app.models import (
    Candidate,
    Classification,
    Dedupe,
    EmailValidation,
    Failure,
    Identity,
    Job,
    Lead,
    Metric,
    SourceCursor,
    SourceRecord,
    now,
)
from app.qualification import digest, normalize, score
from app.ranking import rank, semantic_qualified
from app.repositories import claim_hashes, metric
from app.validators import contacts, validate_contact
from app.services import backpressure


def collect(job):
    cfg = settings()
    with session() as db:
        source = db.get(SourceCursor, job.source)
        today = (
            db.scalar(
                select(Metric.value).where(
                    Metric.day == now().date(), Metric.source == "all", Metric.name == "raw_scanned"
                )
            )
            or 0
        )
        limit = min(job.batch_size, max(0, cfg.raw_daily_target - int(today)))
        if not limit:
            return {"stop_reason": "daily_target"}
        pending = db.scalar(select(func.count()).select_from(SourceRecord).where(SourceRecord.status == "PENDING"))
        limit = min(limit, max(0, cfg.max_pending_raw - pending))
        if not limit:
            return {"stop_reason": "raw_backlog_cap"}
        pressure = backpressure.inspect(db)
        if pressure["mode"] in {"storage_pressure", "drain", "campaign_complete"}:
            return {"stop_reason": pressure["mode"]}
        # Defense in depth for internal direct calls; HTTP/executor additionally enforce pause/flags.
        limit = min(limit, max(0, cfg.queue_high_water - pressure["total_work_rows"]))
        if not limit:
            return {"stop_reason": "queue_high_water"}
        cursor = dict(source.cursor)
    batch = ADAPTERS[job.source]().collect(cursor, limit)
    # All accepted source rows, hashes, metrics and checkpoint commit together.
    with session() as db:
        source = db.get(SourceCursor, job.source)
        if source.cursor != cursor:
            # An unexpected second collector cannot overwrite a newer committed checkpoint.
            from app.collectors.base import SourceError

            raise SourceError("source_cursor_conflict")
        accepted = duplicates = 0
        for record in batch.records:
            text = normalize(record.text)
            identity_hash = digest(job.source + ":" + record.identity)
            content_hash = digest(text)
            record_hash = digest(job.source + ":" + record.source_record_id)
            if not claim_hashes(db, ["record:" + record_hash, "content:" + content_hash]):
                duplicates += 1
                continue
            db.execute(
                insert(Identity)
                .values(identity_hash=identity_hash)
                .on_conflict_do_update(index_elements=["identity_hash"], set_={"last_seen_at": now()})
            )
            db.add(
                SourceRecord(
                    source=job.source,
                    source_record_id=record.source_record_id,
                    source_url=record.url,
                    identity_hash=identity_hash,
                    content_hash=content_hash,
                    text=text,
                    profile=profile(record),
                    published_at=record.published_at,
                )
            )
            accepted += 1
        source.cursor = batch.cursor
        source.updated_at = now()
        if batch.retry_seconds:
            source.retry_at = now() + timedelta(seconds=batch.retry_seconds)
            source.failures += 1
            db.add(Failure(job_id=job.id, source=job.source, code=batch.stop_reason))
            metric(db, job.source, "failures")
        else:
            source.last_success_at, source.failures, source.retry_at = now(), 0, None
        if source.failures >= cfg.job_max_attempts:
            source.enabled = False
        metric(db, "all", "raw_scanned", batch.scanned)
        metric(db, job.source, "raw_scanned", batch.scanned)
        metric(db, job.source, "stored", accepted)
        metric(db, job.source, "duplicates", duplicates)
        metric(db, job.source, "discovery_rejected", max(0, batch.scanned - len(batch.records)))
        stored_job = db.get(Job, job.id)
        stored_job.cursor_in, stored_job.cursor_out = cursor, batch.cursor
        result = {
            "scanned": batch.scanned,
            "stored": accepted,
            "duplicates": duplicates,
            "stop_reason": batch.stop_reason,
        }
        # Collection is complete atomically with cursor; crash cannot replay this logical batch.
        stored_job.status, stored_job.completed_at, stored_job.result = "COMPLETE", now(), result
    return result


def normalize_batch(limit):
    cfg = settings()
    with session() as db:
        records = db.scalars(
            select(SourceRecord)
            .where(SourceRecord.status == "PENDING")
            .order_by(SourceRecord.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for record in records:
            result = score(record.text, record.source, record.published_at)
            metric(db, record.source, "processed")
            if result["rejected"]:
                metric(db, record.source, "rejected")
                if cfg.delete_rejected_immediately:
                    db.delete(record)
                else:
                    record.status = "REJECTED"
                continue
            found_contacts = contacts(record.text, record.source_url)
            if found_contacts and all(db.get(Dedupe, "email:" + digest(c["email"])) for c in found_contacts):
                metric(db, record.source, "email_duplicates")
                db.delete(record)
                continue
            if db.get(Dedupe, "lead:" + record.identity_hash):
                metric(db, record.source, "identity_duplicates")
                db.delete(record)
                continue
            # Deduplicate qualified identities before any classifier call. Low quality early posts do not claim identities.
            if not claim_hashes(db, ["candidate:" + record.identity_hash]):
                metric(db, record.source, "identity_duplicates")
                db.delete(record)
                continue
            candidate = Candidate(
                record_id=record.id,
                source=record.source,
                source_url=record.source_url,
                identity_hash=record.identity_hash,
                excerpt=grok.redact(record.text),
                contacts=found_contacts,
                profile=record.profile,
                product_type=result["product_type"],
                score=result["score"],
                status="VALIDATE",
            )
            db.add(candidate)
            db.flush()
            db.add(
                Classification(candidate_id=candidate.id, provider="rules", version=result["version"], result=result)
            )
            metric(db, record.source, "candidates")
            if cfg.delete_processed_raw:
                db.delete(record)
            else:
                record.status = "PROCESSED"
        return {"normalized": len(records)}


def _reject(db, candidate):
    metric(db, candidate.source, "rejected")
    if candidate.record_id and settings().delete_rejected_immediately:
        db.execute(delete(SourceRecord).where(SourceRecord.id == candidate.record_id))
    db.execute(delete(Dedupe).where(Dedupe.key == "candidate:" + candidate.identity_hash))
    db.delete(candidate)


def _usable_contacts(candidate):
    fresh = []
    for contact in candidate.contacts:
        try:
            checked = datetime.fromisoformat(contact.get("validated_at", ""))
            if checked.tzinfo and checked > now() - timedelta(days=3) and contact.get("status") == "DELIVERABLE_DOMAIN":
                fresh.append(contact)
        except (TypeError, ValueError):
            pass
    return fresh


def _finalize(db, candidate):
    # Model output never supplies or edits any of these contact/profile fields.
    for contact in _usable_contacts(candidate):
        email_hash = digest(contact["email"])
        if not claim_hashes(db, ["email:" + email_hash, "lead:" + candidate.identity_hash]):
            metric(db, candidate.source, "email_duplicates")
            continue
        db.add(Lead(
            candidate_id=candidate.id, email=contact["email"], email_hash=email_hash,
            identity_hash=candidate.identity_hash, source=candidate.source,
            source_url=candidate.source_url, email_source_url=contact["source_url"],
            excerpt=candidate.excerpt, product_type=candidate.product_type,
            score=candidate.score, rank_score=candidate.rank_score, profile=candidate.profile,
        ))
        identity = db.get(Identity, candidate.identity_hash)
        if identity:
            identity.email_hash = email_hash
        metric(db, candidate.source, "validated")
        candidate.status, candidate.contacts = "VALIDATED", []
        return True
    _reject(db, candidate)
    return False


def classify_batch(limit):
    deadline = time.monotonic() + settings().job_seconds
    count = 0
    while count < limit and time.monotonic() < deadline:
        with session() as db:
            candidate = db.scalar(
                select(Candidate)
                .where(Candidate.status == "CLASSIFY", Candidate.available_at <= now())
                .order_by(Candidate.rank_score.desc(), Candidate.score.desc(), Candidate.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if not candidate:
                break
            if not _usable_contacts(candidate):
                # Older/retried candidates cannot bypass deterministic contact validation.
                candidate.status, candidate.available_at = "VALIDATE", now()
                count += 1
                continue
            classification = db.scalar(
                select(Classification).where(
                    Classification.candidate_id == candidate.id, Classification.provider == "rules"
                )
            )
            if not classification:
                candidate.status = "REVIEW"
                db.add(Failure(source=candidate.source, code="rules_result_missing"))
                count += 1
                continue
            result = classification.result
            ml = local.classify(candidate.excerpt)
            if ml:
                db.execute(
                    insert(Classification)
                    .values(candidate_id=candidate.id, provider="local", version=ml["version"], result=ml)
                    .on_conflict_do_nothing()
                )
                metric(db, candidate.source, "ml_classified")
            ranked = rank(result, True, ml)
            candidate.rank_score = ranked["rank_score"]
            db.execute(insert(Classification).values(
                candidate_id=candidate.id, provider="ranking", version=ranked["version"], result=ranked,
            ).on_conflict_do_update(
                index_elements=["candidate_id", "provider"], set_={"version": ranked["version"], "result": ranked},
            ))
            route = ranked["route"]
            qualified = route == "DIRECT_FINAL"
            if route == "SEMANTIC":
                try:
                    semantic_result = semantic.classify(candidate.excerpt, candidate.score)
                except semantic.SemanticUnavailable as exc:
                    candidate.attempts += 1
                    candidate.available_at = now() + timedelta(hours=min(24, 2**candidate.attempts))
                    if candidate.attempts >= settings().job_max_attempts:
                        candidate.status = "REVIEW"
                    metric(db, candidate.source, semantic.provider() + "_deferred")
                    db.add(Failure(source=candidate.source, code=str(exc)))
                    count += 1
                    continue
                db.add(
                    Classification(
                        candidate_id=candidate.id,
                        provider=semantic.provider(),
                        version=semantic.model(),
                        result=semantic_result.model_dump(),
                    )
                )
                metric(db, candidate.source, semantic.provider() + "_classified")
                qualified = semantic_qualified(semantic_result)
                # A semantic pass is a decision, never an automatic or calibrated 10/10.
                candidate.score, candidate.product_type = semantic_result.score, semantic_result.product_type
            if qualified:
                _finalize(db, candidate)
            elif route == "DEFERRED":
                candidate.status = "DEFERRED"
                metric(db, candidate.source, "rank_deferred")
            else:
                _reject(db, candidate)
            count += 1
    return {"classified": count}


def validate_batch(limit):
    count = 0
    deadline = time.monotonic() + settings().job_seconds
    while count < limit and time.monotonic() < deadline:
        with session() as db:
            candidate = db.scalar(
                select(Candidate)
                .where(Candidate.status == "VALIDATE", Candidate.available_at <= now())
                .order_by(Candidate.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if not candidate:
                break
            if not candidate.contacts:
                candidate.status = "NO_CONTACT"
                metric(db, candidate.source, "no_contact")
                count += 1
                continue
            temporary, accepted_contacts = False, []
            for contact in candidate.contacts:
                email_hash = digest(contact["email"])
                if db.get(Dedupe, "email:" + email_hash):
                    metric(db, candidate.source, "email_duplicates")
                    continue
                cached = db.get(EmailValidation, email_hash)
                if cached and cached.checked_at > now() - timedelta(days=3) and cached.reason != "dns_temporary":
                    status, reason = cached.status, cached.reason
                else:
                    status, reason = validate_contact(contact["email"])
                    db.execute(
                        insert(EmailValidation)
                        .values(email_hash=email_hash, status=status, reason=reason)
                        .on_conflict_do_update(
                            index_elements=["email_hash"],
                            set_={"status": status, "reason": reason, "checked_at": now()},
                        )
                    )
                temporary |= reason == "dns_temporary"
                if status != "DELIVERABLE_DOMAIN":
                    continue
                accepted_contacts.append({**contact, "status": status, "validated_at": now().isoformat()})
            if accepted_contacts:
                candidate.contacts = accepted_contacts
                candidate.status, candidate.attempts, candidate.available_at = "CLASSIFY", 0, now()
                classification = db.scalar(select(Classification).where(
                    Classification.candidate_id == candidate.id, Classification.provider == "rules",
                ))
                if classification:
                    candidate.rank_score = rank(classification.result, True)["rank_score"]
                metric(db, candidate.source, "contacts_validated")
            else:
                if temporary and candidate.attempts < settings().job_max_attempts:
                    candidate.attempts += 1
                    candidate.available_at = now() + timedelta(minutes=2**candidate.attempts)
                else:
                    candidate.status = "CONTACT_REVIEW"
                    metric(db, candidate.source, "contact_review")
            count += 1
    return {"validated_checked": count}


def cleanup(limit):
    cfg = settings()
    with session() as db:
        # Pending raw remains necessary for an unfinished job. Never erase it just for age.
        ids = list(
            db.scalars(
                select(SourceRecord.id)
                .where(
                    SourceRecord.status != "PENDING",
                    SourceRecord.created_at < now() - timedelta(days=cfg.raw_retention_days),
                )
                .limit(limit)
            )
        )
        if ids:
            db.execute(delete(SourceRecord).where(SourceRecord.id.in_(ids)))
        expired_candidates = db.scalars(
            select(Candidate)
            .where(
                Candidate.created_at < now() - timedelta(days=cfg.candidate_retention_days),
                ~select(Lead.id).where(Lead.identity_hash == Candidate.identity_hash).exists(),
            )
            .limit(limit)
        ).all()
        candidates = [c.id for c in expired_candidates]
        if candidates:
            # Release temporary identity admission, while permanent record/content/email hashes remain.
            db.execute(
                delete(Dedupe).where(Dedupe.key.in_(["candidate:" + c.identity_hash for c in expired_candidates]))
            )
            db.execute(delete(Candidate).where(Candidate.id.in_(candidates)))
        cutoff = now() - timedelta(days=cfg.operational_retention_days)
        for table in (Failure, Job):
            query = select(table.id).where(table.created_at < cutoff)
            if table is Job:
                query = query.where(Job.status.in_(["COMPLETE", "FAILED"]))
            expired = list(db.scalars(query.limit(limit)))
            if expired:
                db.execute(delete(table).where(table.id.in_(expired)))
        expired_emails = list(
            db.scalars(select(EmailValidation.email_hash).where(EmailValidation.checked_at < cutoff).limit(limit))
        )
        if expired_emails:
            db.execute(delete(EmailValidation).where(EmailValidation.email_hash.in_(expired_emails)))
        metric(db, "all", "raw_cleaned", len(ids))
        metric(db, "all", "candidates_expired", len(candidates))
    return {"raw_deleted": len(ids), "candidates_expired": len(candidates)}
