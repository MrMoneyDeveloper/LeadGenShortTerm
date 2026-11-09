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
from app.services import campaign

FIELDS = [
    "lead_id", "campaign_id", "display_name", "first_name", "salutation", "username", "business_name", "email", "source",
    "source_evidence",
    "source_url", "email_source_url", "product_type", "rank_score", "score", "validation_status", "created_at",
]


class DeliveryError(ValueError):
    pass


BUCKETS = [chr(code) for code in range(ord("A"), ord("Z") + 1)] + ["OTHER"]
# Leads without an email are prospects: kept out of the VALIDATED_* email dataset in PROSPECTS_###.
PROSPECTS = "PROSPECTS"
# Conservative default for any Google account type; Workspace files may allow more.
WORKBOOK_CELL_LIMIT = 10_000_000


def source_evidence(excerpt, profile):
    """Post excerpt plus, when used, the labelled same-author profile location and its URL (<=2000)."""
    geography = (profile or {}).get("geography")
    if not geography:
        return (excerpt or "")[:2000]
    suffix = (f" || SA location evidence (same author's public profile bio, {geography.get('source_url', '')[:300]}): "
              f"\"{(geography.get('excerpt') or '')[:200]}\"")
    return (excerpt or "")[: max(0, 2000 - len(suffix))] + suffix


def bucket(email):
    """Alphabetical bucket from the normalized email's first character; digits/other -> OTHER."""
    first = (email or "").strip().casefold()[:1]
    return first.upper() if "a" <= first <= "z" else "OTHER"


def placement(layout, position, shard_rows, bucket_name=None):
    shard, row = position // shard_rows + 1, position % shard_rows + 2
    if bucket_name == PROSPECTS:
        return f"PROSPECTS_{shard:03d}", row
    if layout == "alphabetical":
        return f"VALIDATED_{bucket_name}_{shard:03d}", row
    return f"VALIDATED_{shard:03d}", row


def capacity_plan(rows_per_group, shard_rows, fields=None, existing_cells=0, limit=WORKBOOK_CELL_LIMIT):
    """Cells needed when tabs are sized exactly (header + data rows, one column per field).

    ``rows_per_group`` maps a bucket (or "SEQUENTIAL") to its total allocated data rows.
    """
    fields = fields or len(FIELDS)
    tabs, cells = 0, 0
    for rows in rows_per_group.values():
        full, rest = divmod(int(rows), shard_rows)
        group_tabs = full + (1 if rest else 0)
        tabs += group_tabs
        cells += (int(rows) + group_tabs) * fields  # data rows plus one header row per tab
    total = cells + existing_cells
    return {"tabs": tabs, "delivery_cells": cells, "existing_cells": existing_cells, "total_cells": total,
            "cell_limit": limit, "within_limit": total <= limit, "headroom_cells": max(0, limit - total)}


def destination_tuple(cfg):
    return {"spreadsheet_id": cfg.google_spreadsheet_id, "folder_id": cfg.google_drive_backup_folder_id,
            "layout": cfg.sheet_layout, "shard_rows": cfg.sheet_shard_rows}


def bound_layout(state):
    return state.export_layout or ("sequential" if state.export_spreadsheet_id else None)


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
        "shard_rows": batch.shard_rows, "layout": batch.layout,
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
        if (not state.export_spreadsheet_id and cfg.require_verified_destination
                and state.export_destination_approval != destination_tuple(cfg)):
            # Fresh/replaced database: rows may already exist in Google. Recover or confirm first.
            raise DeliveryError("destination_unverified")
        if bound_layout(state) and bound_layout(state) != cfg.sheet_layout:
            # Never silently switch an occupied destination between layouts.
            raise DeliveryError("final_layout_changed")
        batch = db.scalar(select(ExportBatch).where(ExportBatch.status == "CLAIMED").order_by(ExportBatch.created_at).limit(1))
        if batch:
            return envelope(batch)
        rows = db.scalars(select(Lead).order_by(Lead.id).limit(limit).with_for_update()).all()
        if not rows:
            return {"status": "empty", "items": []}

        # The persisted campaign ID survives Render restarts and cannot drift if an env value changes mid-run.
        campaign_id = state.campaign_id or cfg.campaign_id
        items = []
        layout = cfg.sheet_layout
        buckets = dict(state.export_bucket_positions or {})
        sequential = state.export_next_position
        for lead in rows:
            if lead.id > 9_007_199_254_740_991:
                raise DeliveryError("lead_id_exceeds_google_integer_range")
            if not lead.email:
                name = PROSPECTS  # no email: separate prospect dataset, never a VALIDATED_* tab
            elif layout == "alphabetical":
                name = bucket(lead.email)
            else:
                name = None
            if name is None:
                tab, row = placement(layout, sequential, cfg.sheet_shard_rows)
                sequential += 1
            else:
                tab, row = placement(layout, buckets.get(name, 0), cfg.sheet_shard_rows, name)
                buckets[name] = buckets.get(name, 0) + 1
            profile = lead.profile or {}
            values = {
                **{key: getattr(lead, key) for key in ("email", "source", "source_url", "email_source_url", "product_type", "score", "validation_status")},
                **{key: profile.get(key, "") for key in ("display_name", "username", "business_name")},
                **personalization(profile),
                "source_evidence": source_evidence(lead.excerpt, profile),
                "lead_id": f"{campaign_id}:{lead.id}", "campaign_id": campaign_id,
                "rank_score": format(lead.rank_score, ".2f"), "created_at": lead.created_at.isoformat(),
            }
            items.append({
                "lead_id": lead.id, "tab": tab, "row": row,
                "values": [str(values[field] or "") for field in FIELDS],
            })
        batch = ExportBatch(
            campaign_id=campaign_id, spreadsheet_id=cfg.google_spreadsheet_id,
            drive_folder_id=cfg.google_drive_backup_folder_id, checksum=checksum(items), fields=FIELDS,
            items=items, count=len(items), qualified_count=sum(1 for lead in rows if campaign.is_qualified(lead)),
            # Alphabetical placements live in the items and the durable bucket cursors.
            first_position=state.export_next_position if layout == "sequential" else 0,  # receipts only
            shard_rows=cfg.sheet_shard_rows, layout=layout,
        )
        db.add(batch)
        state.export_bucket_positions = buckets  # new dict object so the JSONB change persists
        state.export_next_position = sequential
        state.export_layout = layout
        state.export_spreadsheet_id = cfg.google_spreadsheet_id
        state.export_folder_id = cfg.google_drive_backup_folder_id
        state.export_shard_rows = cfg.sheet_shard_rows
        db.flush()
        return envelope(batch)


def adopt_destination(next_position):
    """Start row allocation after rows already present in a destination Sheet.

    Only allowed while this database has never claimed a delivery (fresh state), so it can
    never move allocation backwards or skip rows that this database itself assigned.
    """
    if type(next_position) is not int or not 0 <= next_position <= 50_000_000:
        raise DeliveryError("invalid_position")
    cfg = settings()
    if cfg.sheet_layout != "sequential":
        # A single sequential offset cannot describe per-letter positions; use recover instead.
        raise DeliveryError("adopt_destination_sequential_only")
    with session() as db:
        state = db.scalar(select(PipelineState).where(PipelineState.id == 1).with_for_update())
        if not state:
            raise DeliveryError("pipeline_uninitialized")
        if (state.export_spreadsheet_id or state.export_next_position or state.export_bucket_positions
                or db.scalar(select(ExportBatch.id).limit(1))):
            raise DeliveryError("destination_already_in_use")
        state.export_next_position = next_position
        state.export_destination_verified = True
        state.export_destination_approval = destination_tuple(cfg)
        return {"export_next_position": next_position}


def confirm_empty_destination(confirm_spreadsheet_id):
    """Operator confirms the configured Sheet has no VALIDATED_* rows (first use of a new Sheet)."""
    cfg = settings()
    with session() as db:
        state = db.scalar(select(PipelineState).where(PipelineState.id == 1).with_for_update())
        if not state:
            raise DeliveryError("pipeline_uninitialized")
        if state.export_spreadsheet_id or db.scalar(select(ExportBatch.id).limit(1)):
            raise DeliveryError("destination_already_in_use")
        if not cfg.google_spreadsheet_id or not secrets.compare_digest(str(confirm_spreadsheet_id), cfg.google_spreadsheet_id):
            raise DeliveryError("destination_not_confirmed")
        state.export_destination_verified = True
        state.export_destination_approval = destination_tuple(cfg)
        return {"export_destination_verified": True, "approved": {k: v for k, v in destination_tuple(cfg).items()
                                                                  if k in {"layout", "shard_rows"}}}


def adopt_layout(layout, confirm_spreadsheet_id):
    """Explicit, verified layout switch for a destination. Never resets any allocation cursor.

    Legacy sequential tabs/rows and receipts are preserved. Alphabetical tabs use distinct names,
    so they cannot collide with legacy rows, and Apps Script still refuses any occupied target row.
    """
    if layout not in {"sequential", "alphabetical"}:
        raise DeliveryError("invalid_layout")
    cfg = settings()
    with session() as db:
        state = db.scalar(select(PipelineState).where(PipelineState.id == 1).with_for_update())
        if not state:
            raise DeliveryError("pipeline_uninitialized")
        if db.scalar(select(ExportBatch.id).where(ExportBatch.status == "CLAIMED").limit(1)):
            raise DeliveryError("claimed_delivery_pending")
        target = state.export_spreadsheet_id or cfg.google_spreadsheet_id
        if not target or not secrets.compare_digest(str(confirm_spreadsheet_id), target):
            raise DeliveryError("destination_not_confirmed")
        previous = bound_layout(state)
        state.export_layout = layout
        return {"previous_layout": previous, "layout": layout, "legacy_next_position": state.export_next_position,
                "bucket_positions": dict(state.export_bucket_positions or {})}


def shard_status(db, existing_cells=0):
    """Per-bucket tabs, rows and remaining capacity, plus an exact-size workbook cell estimate."""
    state = db.get(PipelineState, 1)
    cfg = settings()
    shard_rows = (state.export_shard_rows if state and state.export_spreadsheet_id else None) or cfg.sheet_shard_rows
    positions = dict((state.export_bucket_positions if state else None) or {})
    groups = {name: int(positions[name]) for name in BUCKETS + [PROSPECTS] if positions.get(name)}
    if state and state.export_next_position:
        groups["SEQUENTIAL"] = int(state.export_next_position)
    tabs = []
    for name, rows in sorted(groups.items()):
        for index in range(rows // shard_rows + (1 if rows % shard_rows else 0)):
            used = min(shard_rows, rows - index * shard_rows)
            tab = (f"VALIDATED_{index + 1:03d}" if name == "SEQUENTIAL" else f"PROSPECTS_{index + 1:03d}"
                   if name == PROSPECTS else f"VALIDATED_{name}_{index + 1:03d}")
            tabs.append({"tab": tab, "bucket": name, "data_rows": used, "capacity": shard_rows,
                         "remaining": shard_rows - used})
    return {"layout": bound_layout(state) if state else None, "configured_layout": cfg.sheet_layout,
            "shard_rows": shard_rows, "tabs": tabs,
            "capacity": capacity_plan(groups, shard_rows, existing_cells=existing_cells)}


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
