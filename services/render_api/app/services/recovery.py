"""Recover a fresh/replaced database from the finished Google dataset before any delivery or acquisition.

Inputs:

* the **verified Drive delivery JSON files** (``lead-delivery-*.json``), one per acknowledged batch:
  lossless raw values (no CSV formula escaping) and exact ``tab``/``row`` placements;
* a **current destination snapshot** taken from the live Sheet (Apps Script ops ``snapshot``):
  the spreadsheet ID's SHA-256, the capture time and every ``VALIDATED_*``/``PROSPECTS_*`` tab's
  data-row count. It must be fresh and match the configured spreadsheet, so a stale backup paired
  with a stale count file cannot pass.

Every row ``2..n+1`` of every snapshot tab must be covered exactly once by a file whose envelope,
schema, identifiers and checksum validate. Then, in bounded chunks with bulk inserts, recovery
restores: dedupe hashes for real rows; per-letter, prospect and legacy row cursors; the lead-ID
sequence above every imported ID; one compact ACKNOWLEDGED receipt per delivered batch (counts and
qualified counts, no personal payload), so campaign progress toward the target survives; and the
destination binding with its exact approval tuple.
"""

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.database import session
from app.models import Dedupe, ExportBatch, PipelineState
from app.qualification import digest
from app.services import campaign
from app.services.delivery import FIELDS, PROSPECTS, checksum, destination_tuple

TAB = re.compile(r"^(?:VALIDATED_(?:([A-Z]|OTHER)_)?|(PROSPECTS)_)(\d{3,})$")
CHUNK = 1000
MAX_VALUE_CHARS = 2048
SNAPSHOT_MAX_AGE = timedelta(minutes=30)
INDEX = {name: i for i, name in enumerate(FIELDS)}


class RecoveryError(ValueError):
    pass


def keys_for_values(row):
    """Dedupe keys computed exactly as the collector/pipeline compute them."""
    keys = set()
    if row.get("validation_status") == "SYNTHETIC_TEST":
        return keys
    email = row.get("email") or ""
    if email:
        keys.add("email:" + digest(email))
    source, url = row.get("source") or "", row.get("source_url") or ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return keys
    if source == "bluesky" and parts.hostname == "bsky.app":
        seg = parts.path.strip("/").split("/")
        if len(seg) == 4 and seg[0] == "profile" and seg[2] == "post" and seg[1].startswith("did:"):
            keys.add("record:" + digest(f"bluesky:{seg[1]}/{seg[3]}"))
            keys.add("lead:" + digest(f"bluesky:{seg[1]}"))
    elif source == "youtube" and parts.hostname in {"www.youtube.com", "youtube.com"}:
        comment = parse_qs(parts.query).get("lc", [""])[0]
        if comment:
            keys.add("record:" + digest(f"youtube:{comment}"))
    return keys


def _files(inputs):
    files = []
    for item in inputs or []:
        path = Path(item)
        files += sorted(path.glob("lead-delivery-*.json")) if path.is_dir() else [path]
    return files


def _validate_envelope(envelope, name):
    """Supported schema only: exact fields, typed identifiers, bounded values, consistent lead IDs."""
    def fail(reason):
        raise RecoveryError(f"invalid_delivery:{name}:{reason}")

    if not isinstance(envelope, dict):
        fail("envelope")
    try:
        uuid.UUID(str(envelope.get("batch_id")))
    except ValueError:
        fail("batch_id")
    campaign_id = envelope.get("campaign_id")
    if not isinstance(campaign_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", campaign_id):
        fail("campaign_id")
    if envelope.get("fields") != FIELDS:
        fail("fields")  # checksum covers items only; the schema must match exactly
    if type(envelope.get("shard_rows")) is not int or not 1 <= envelope["shard_rows"] <= 5000:
        fail("shard_rows")
    items = envelope.get("items")
    if not isinstance(items, list) or not items or len(items) > 200:
        fail("items")
    if checksum(items) != envelope.get("checksum"):
        fail("checksum")
    for item in items:
        if not isinstance(item, dict) or set(item) != {"lead_id", "tab", "row", "values"}:
            fail("item_shape")
        if type(item["lead_id"]) is not int or not 1 <= item["lead_id"] <= 9_007_199_254_740_991:
            fail("lead_id")
        if not isinstance(item["tab"], str) or not TAB.match(item["tab"]):
            fail("tab")
        if type(item["row"]) is not int or not 2 <= item["row"] <= envelope["shard_rows"] + 1:
            fail("row")
        vals = item["values"]
        if (not isinstance(vals, list) or len(vals) != len(FIELDS)
                or not all(isinstance(v, str) and len(v) <= MAX_VALUE_CHARS for v in vals)):
            fail("values")
        if vals[INDEX["lead_id"]] != f"{campaign_id}:{item['lead_id']}" or vals[INDEX["campaign_id"]] != campaign_id:
            fail("identifier_mismatch")
        if not vals[INDEX["email"]] and not item["tab"].startswith("PROSPECTS_") \
                and vals[INDEX["validation_status"]] == "NO_EMAIL":
            fail("prospect_in_email_dataset")
    return envelope


def _snapshot_tabs(snapshot, cfg, now):
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("tabs"), dict):
        raise RecoveryError("no_destination_snapshot")
    expected = hashlib.sha256(cfg.google_spreadsheet_id.encode("utf-8")).hexdigest()
    if snapshot.get("spreadsheet_sha256") != expected:
        raise RecoveryError("snapshot_destination_mismatch")
    try:
        captured = datetime.fromisoformat(str(snapshot.get("captured_at")).replace("Z", "+00:00"))
    except ValueError:
        raise RecoveryError("snapshot_time_invalid") from None
    if captured.tzinfo is None or not timedelta(minutes=-5) <= now - captured <= SNAPSHOT_MAX_AGE:
        raise RecoveryError("snapshot_not_current")
    return {tab: int(rows) for tab, rows in snapshot["tabs"].items() if TAB.match(tab) and int(rows) > 0}


def plan(inputs, snapshot, now=None):
    """Validate inputs and compute everything to restore. Raises RecoveryError on any gap."""
    cfg = settings()
    tab_rows = _snapshot_tabs(snapshot, cfg, now or datetime.now(UTC))
    files = _files(inputs)
    if not files:
        raise RecoveryError("no_delivery_inputs")
    placements, receipts, keys, destination, max_lead_id = {}, {}, set(), None, 0
    for path in files:
        try:
            envelope = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise RecoveryError(f"unreadable_delivery_file:{Path(path).name}") from None
        _validate_envelope(envelope, Path(path).name)
        dest = (envelope.get("spreadsheet_id"), envelope.get("drive_folder_id"), envelope["shard_rows"])
        if destination is None:
            destination = dest
        elif dest != destination:
            raise RecoveryError("mixed_destinations")
        qualified, created = 0, []
        for item in envelope["items"]:
            where = (item["tab"], item["row"])
            if where in placements and placements[where] != item["values"]:
                raise RecoveryError(f"conflicting_placement:{item['tab']}:{item['row']}")
            placements[where] = item["values"]
            row = dict(zip(FIELDS, item["values"], strict=True))
            keys |= keys_for_values(row)
            qualified += bool(row["email"]) and row["validation_status"] not in campaign.NOT_QUALIFIED
            created.append(row["created_at"])
            max_lead_id = max(max_lead_id, item["lead_id"])
        receipts[envelope["batch_id"]] = {
            "campaign_id": envelope["campaign_id"], "checksum": envelope["checksum"],
            "count": len(envelope["items"]), "qualified_count": qualified,
            "layout": "alphabetical" if any(TAB.match(i["tab"]).group(1) for i in envelope["items"]) else "sequential",
            "created_at": min(created), "acknowledged_at": max(created),
        }
    if (destination[0], destination[1]) != (cfg.google_spreadsheet_id, cfg.google_drive_backup_folder_id):
        raise RecoveryError("destination_differs_from_configuration")
    if destination[2] != cfg.sheet_shard_rows:
        raise RecoveryError("shard_rows_differs_from_configuration")
    covered = {}
    for tab, row in placements:
        if tab not in tab_rows or not 2 <= row <= tab_rows[tab] + 1:
            raise RecoveryError(f"placement_outside_sheet:{tab}:{row}")
        covered.setdefault(tab, set()).add(row)
    missing = {tab: n - len(covered.get(tab, ())) for tab, n in tab_rows.items() if len(covered.get(tab, ())) != n}
    if missing:
        raise RecoveryError("incomplete_coverage:" + json.dumps(missing, sort_keys=True))
    buckets, sequential = {}, 0
    for tab, n in tab_rows.items():
        match = TAB.match(tab)
        end = (int(match.group(3)) - 1) * destination[2] + n  # next free position after this tab
        name = PROSPECTS if match.group(2) else match.group(1)
        if name:
            buckets[name] = max(buckets.get(name, 0), end)
        else:
            sequential = max(sequential, end)
    per_campaign = {}
    for receipt in receipts.values():
        per_campaign[receipt["campaign_id"]] = per_campaign.get(receipt["campaign_id"], 0) + receipt["qualified_count"]
    return {"files": len(files), "rows": len(placements), "keys": keys, "receipts": receipts,
            "bucket_positions": buckets, "next_position": sequential, "max_lead_id": max_lead_id,
            "qualified_by_campaign": per_campaign}


def _present(db, keys):
    found, ordered = 0, sorted(keys)
    for start in range(0, len(ordered), CHUNK):
        found += db.scalar(select(func.count()).select_from(Dedupe).where(Dedupe.key.in_(ordered[start:start + CHUNK])))
    return found


def recover(inputs, snapshot, apply=False, now=None):
    result = plan(inputs, snapshot, now)
    keys, receipts = result.pop("keys"), result.pop("receipts")
    summary = {**result, "keys": len(keys), "receipts": len(receipts)}
    if not apply:
        summary["applied"] = False
        return summary
    cfg = settings()
    ordered, statements = sorted(keys), 0
    with session() as db:
        state = db.scalar(select(PipelineState).where(PipelineState.id == 1).with_for_update())
        if state is None:
            raise RecoveryError("pipeline_uninitialized")
        if state.export_spreadsheet_id or db.scalar(select(ExportBatch.id).limit(1)):
            raise RecoveryError("database_not_fresh")  # never move a live database's cursors
        for start in range(0, len(ordered), CHUNK):
            db.execute(insert(Dedupe).values([{"key": k} for k in ordered[start:start + CHUNK]]).on_conflict_do_nothing())
            statements += 1
        rows = [{"id": batch_id, "campaign_id": r["campaign_id"], "status": "ACKNOWLEDGED",
                 "spreadsheet_id": cfg.google_spreadsheet_id, "drive_folder_id": cfg.google_drive_backup_folder_id,
                 "checksum": r["checksum"], "fields": [], "items": [], "count": r["count"],
                 "qualified_count": r["qualified_count"], "first_position": 0, "shard_rows": cfg.sheet_shard_rows,
                 "layout": r["layout"], "created_at": datetime.fromisoformat(r["created_at"]),
                 "acknowledged_at": datetime.fromisoformat(r["acknowledged_at"])} for batch_id, r in receipts.items()]
        for start in range(0, len(rows), CHUNK):
            db.execute(insert(ExportBatch).values(rows[start:start + CHUNK]).on_conflict_do_nothing())
        # Stable lead IDs: new leads must never reuse an ID already delivered to Google.
        db.execute(text("SELECT setval(pg_get_serial_sequence('validated_leads', 'id'), :v, true)"),
                   {"v": max(1, result["max_lead_id"])})
        state.export_bucket_positions = dict(result["bucket_positions"])
        state.export_next_position = result["next_position"]
        state.export_layout = cfg.sheet_layout
        state.export_spreadsheet_id, state.export_folder_id = cfg.google_spreadsheet_id, cfg.google_drive_backup_folder_id
        state.export_shard_rows = cfg.sheet_shard_rows
        state.export_destination_verified = True
        state.export_destination_approval = destination_tuple(cfg)
        if cfg.campaign_id in result["qualified_by_campaign"] and not state.campaign_id:
            state.campaign_id = cfg.campaign_id  # resume target accounting for the same campaign
    with session() as db:
        state = db.get(PipelineState, 1)
        next_id = db.execute(text(
            "SELECT last_value FROM " + db.execute(text("SELECT pg_get_serial_sequence('validated_leads', 'id')")).scalar()
        )).scalar()
        verified = (_present(db, keys) == len(keys)
                    and db.scalar(select(func.count()).select_from(ExportBatch)) == len(receipts)
                    and dict(state.export_bucket_positions) == result["bucket_positions"]
                    and state.export_next_position == result["next_position"]
                    and next_id >= result["max_lead_id"])
        accepted = campaign.accepted_finals(db, state)
    summary.update(applied=True, insert_statements=statements, verified=verified, accepted_finals_after=accepted)
    if not verified:
        raise RecoveryError("post_recovery_verification_failed")
    return summary
