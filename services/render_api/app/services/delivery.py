"""Google is the final store; no personal payload may be deleted before verified delivery ACK."""

import hashlib
import json
import secrets
import unicodedata

from sqlalchemy import delete, select

from app.config import settings
from app.database import session
from app.models import Candidate, Dedupe, ExportBatch, Lead, PipelineState, SourceRecord, now
from app.repositories import metric

FIELDS = [
    "lead_id", "campaign_id", "display_name", "first_name", "salutation", "username", "business_name", "email", "source",
    "source_evidence",
    "source_url", "email_source_url", "product_type", "rank_score", "score", "validation_status", "created_at",
]


class DeliveryError(ValueError):
    pass


def checksum(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def canonical_items(items):
    # PostgreSQL JSONB reorders keys. Reconstruct the agreed insertion order before every response/hash.
    return [{"lead_id": i["lead_id"], "tab": i["tab"], "row": i["row"], "values": i["values"]} for i in items]


def envelope(batch):
    return {
        "batch_id": batch.id, "campaign_id": batch.campaign_id, "status": batch.status,
        "spreadsheet_id": batch.spreadsheet_id, "drive_folder_id": batch.drive_folder_id,
        "checksum": batch.checksum, "fields": batch.fields, "items": canonical_items(batch.items),
        "shard_rows": batch.shard_rows,
    }


def personalization(profile):
    name = profile.get("first_name", "")
    reliable = profile.get("name_reliable") is True and profile.get("name_source") in {"explicit_given_name", "operator_verified"}
    valid = isinstance(name, str) and 1 < len(name) <= 60 and any(c.isalpha() for c in name)
    valid = valid and all(c.isalpha() or unicodedata.category(c).startswith("M") or c in "-'’" for c in name)
    name = name if reliable and valid and not profile.get("business_name") else ""
    return {"first_name": name, "salutation": f"Good day {name}," if name else "Good day,"}


def claim(limit=None):
    cfg = settings()
    if not cfg.final_delivery_enabled:
        raise DeliveryError("final_delivery_disabled")
    if not cfg.google_spreadsheet_id or not cfg.google_drive_backup_folder_id:
        raise DeliveryError("final_destination_unconfigured")
    limit = min(limit or cfg.export_batch_size, cfg.export_batch_size, 200)
    with session() as db:
        # Serializes claim, ACK, and permanent row allocation across all clients/processes.
        state = db.scalar(select(PipelineState).where(PipelineState.id == 1).with_for_update())
        if not state:
            raise DeliveryError("pipeline_uninitialized")
        if state.export_spreadsheet_id and (
            state.export_spreadsheet_id != cfg.google_spreadsheet_id
            or state.export_folder_id != cfg.google_drive_backup_folder_id
            or state.export_shard_rows != cfg.sheet_shard_rows
        ):
            raise DeliveryError("final_destination_changed")
        batch = db.scalar(select(ExportBatch).where(ExportBatch.status == "CLAIMED").order_by(ExportBatch.created_at).limit(1))
        if batch:
            return envelope(batch)
        rows = db.scalars(select(Lead).order_by(Lead.id).limit(limit).with_for_update()).all()
        if not rows:
            return {"status": "empty", "items": []}

        # The persisted campaign ID survives Render restarts and cannot drift if an env value changes mid-run.
        campaign_id = state.campaign_id or cfg.campaign_id
        items = []
        for offset, lead in enumerate(rows):
            if lead.id > 9_007_199_254_740_991:
                raise DeliveryError("lead_id_exceeds_google_integer_range")
            position = state.export_next_position + offset
            profile = lead.profile or {}
            values = {
                **{key: getattr(lead, key) for key in ("email", "source", "source_url", "email_source_url", "product_type", "score", "validation_status")},
                **{key: profile.get(key, "") for key in ("display_name", "username", "business_name")},
                **personalization(profile),
                "source_evidence": lead.excerpt[:2000],
                "lead_id": f"{campaign_id}:{lead.id}", "campaign_id": campaign_id,
                "rank_score": format(lead.rank_score, ".2f"), "created_at": lead.created_at.isoformat(),
            }
            items.append({
                "lead_id": lead.id, "tab": f"VALIDATED_{position // cfg.sheet_shard_rows + 1:03d}",
                "row": position % cfg.sheet_shard_rows + 2,
                "values": [str(values[field] or "") for field in FIELDS],
            })
        batch = ExportBatch(
            campaign_id=campaign_id, spreadsheet_id=cfg.google_spreadsheet_id,
            drive_folder_id=cfg.google_drive_backup_folder_id, checksum=checksum(items), fields=FIELDS,
            items=items, count=len(items), first_position=state.export_next_position,
            shard_rows=cfg.sheet_shard_rows,
        )
        db.add(batch)
        state.export_next_position += len(items)
        state.export_spreadsheet_id = cfg.google_spreadsheet_id
        state.export_folder_id = cfg.google_drive_backup_folder_id
        state.export_shard_rows = cfg.sheet_shard_rows
        db.flush()
        return envelope(batch)


def acknowledge(body):
    # ACK remains available when delivery is disabled, allowing an in-flight verified batch to finish.
    ack_hash = checksum(body)
    with session() as db:
        state = db.scalar(select(PipelineState).where(PipelineState.id == 1).with_for_update())
        batch = db.get(ExportBatch, body["batch_id"])
        if not batch:
            raise DeliveryError("export_batch_not_found")
        if batch.status == "ACKNOWLEDGED":
            if not secrets.compare_digest(batch.ack_digest or "", ack_hash):
                raise DeliveryError("acknowledgement_conflict")
            return {"status": "ACKNOWLEDGED", "deleted_leads": batch.count, "replayed": True}
        if (
            not secrets.compare_digest(batch.checksum, body["checksum"])
            or not secrets.compare_digest(batch.checksum, body["drive_checksum"])
            or body["spreadsheet_id"] != batch.spreadsheet_id
            or body["drive_folder_id"] != batch.drive_folder_id
        ):
            raise DeliveryError("export_receipt_mismatch")
        expected = [{"lead_id": i["lead_id"], "tab": i["tab"], "row": i["row"]} for i in batch.items]
        if sorted(body["placements"], key=lambda x: x["lead_id"]) != sorted(expected, key=lambda x: x["lead_id"]):
            raise DeliveryError("export_placement_mismatch")
        leads = db.scalars(select(Lead).where(Lead.id.in_([i["lead_id"] for i in batch.items])).with_for_update()).all()
        if len(leads) != batch.count:
            raise DeliveryError("export_pending_data_missing")
        for lead in leads:
            candidate = db.get(Candidate, lead.candidate_id) if lead.candidate_id else None
            # Migration/legacy rows might not have a candidate_id; exact identity fallback is safe.
            if candidate is None:
                candidate = db.scalar(select(Candidate).where(Candidate.identity_hash == lead.identity_hash, Candidate.status == "VALIDATED").limit(1))
            metric(db, lead.source, "exported")
            db.delete(lead)
            db.flush()
            if candidate:
                if candidate.record_id:
                    db.execute(delete(SourceRecord).where(SourceRecord.id == candidate.record_id))
                db.execute(delete(Dedupe).where(Dedupe.key == "candidate:" + candidate.identity_hash))
                db.delete(candidate)  # Classification payloads cascade; campaign dedupe remains.
        batch.status, batch.ack_digest = "ACKNOWLEDGED", ack_hash
        batch.drive_file_id, batch.acknowledged_at = body["drive_file_id"], now()
        batch.items, batch.fields = [], []
        if state is not None and state.campaign_status in {"RUNNING", "DRAINING", "FINALIZING"}:
            state.campaign_last_progress_at = now()
        metric(db, "all", "delivery_acknowledged", batch.count)
        return {"status": "ACKNOWLEDGED", "deleted_leads": batch.count, "replayed": False}


def history(limit=100):
    with session() as db:
        return [{
            "batch_id": b.id, "campaign_id": b.campaign_id, "status": b.status, "count": b.count,
            "checksum": b.checksum, "drive_file_id": b.drive_file_id,
            "spreadsheet_id": b.spreadsheet_id, "created_at": b.created_at,
            "acknowledged_at": b.acknowledged_at,
        } for b in db.scalars(select(ExportBatch).order_by(ExportBatch.created_at.desc()).limit(limit))]
