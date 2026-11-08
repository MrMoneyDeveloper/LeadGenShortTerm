"""Recover a fresh/replaced database from the finished Google dataset before any delivery or acquisition.

Input is the set of **verified Drive delivery JSON files** (``lead-delivery-*.json``) written by
Apps Script for every acknowledged batch. They are lossless: raw values (no CSV formula escaping)
and the exact ``tab``/``row`` placement of every delivered row. Coverage is proven against the
Sheet's per-tab data-row counts (from the ``SHARD_INDEX`` tab): every row ``2..n+1`` of every
``VALIDATED_*`` tab must be covered exactly once, or recovery refuses.

Recovery restores, in bounded chunks with bulk inserts:

* compact dedupe hashes (``email:``, ``record:``, Bluesky ``lead:``) for real rows;
* the alphabetical per-bucket and legacy sequential row cursors, so the next claim continues after
  the last occupied Google row instead of colliding at row 2;
* the destination binding, marked verified.

Labelled ``SYNTHETIC_TEST`` rows keep their positions but create no dedupe hashes.
"""

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.database import session
from app.models import Dedupe, ExportBatch, PipelineState
from app.qualification import digest
from app.services.delivery import checksum

TAB = re.compile(r"^VALIDATED_(?:([A-Z]|OTHER)_)?(\d{3,})$")
CHUNK = 1000


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


def plan(inputs, tab_rows):
    """Validate inputs and compute keys/cursors. Raises RecoveryError on any gap or conflict."""
    files = _files(inputs)
    if not files:
        raise RecoveryError("no_delivery_inputs")
    if not tab_rows:
        raise RecoveryError("no_expected_tab_rows")
    placements, destination, keys = {}, None, set()
    for path in files:
        try:
            envelope = json.loads(Path(path).read_text(encoding="utf-8"))
            items, fields = envelope["items"], envelope["fields"]
        except (OSError, ValueError, KeyError, TypeError):
            raise RecoveryError(f"unreadable_delivery_file:{Path(path).name}") from None
        if not isinstance(items, list) or checksum(items) != envelope.get("checksum"):
            raise RecoveryError(f"checksum_mismatch:{Path(path).name}")
        dest = (envelope.get("spreadsheet_id"), envelope.get("drive_folder_id"), envelope.get("shard_rows"))
        if destination is None:
            destination = dest
        elif dest != destination:
            raise RecoveryError("mixed_destinations")
        for item in items:
            where = (item["tab"], item["row"])
            if where in placements and placements[where] != item["values"]:
                raise RecoveryError(f"conflicting_placement:{item['tab']}:{item['row']}")
            placements[where] = item["values"]
            keys |= keys_for_values(dict(zip(fields, item["values"], strict=False)))
    if not placements:
        raise RecoveryError("no_delivered_rows")
    covered = {}
    for tab, row in placements:
        if tab not in tab_rows or not 2 <= row <= int(tab_rows[tab]) + 1:
            raise RecoveryError(f"placement_outside_sheet:{tab}:{row}")
        covered.setdefault(tab, set()).add(row)
    missing = {tab: int(n) - len(covered.get(tab, ())) for tab, n in tab_rows.items()
               if len(covered.get(tab, ())) != int(n)}
    if missing:
        raise RecoveryError("incomplete_coverage:" + json.dumps(missing, sort_keys=True))
    shard_rows = int(destination[2])
    buckets, sequential = {}, 0
    for tab, n in tab_rows.items():
        match = TAB.match(tab)
        if not match:
            raise RecoveryError(f"unexpected_tab:{tab}")
        end = (int(match.group(2)) - 1) * shard_rows + int(n)  # next free position after this tab
        if match.group(1):
            buckets[match.group(1)] = max(buckets.get(match.group(1), 0), end)
        else:
            sequential = max(sequential, end)
    return {"files": len(files), "rows": len(placements), "keys": keys, "bucket_positions": buckets,
            "next_position": sequential, "spreadsheet_id": destination[0], "drive_folder_id": destination[1],
            "shard_rows": shard_rows}


def _present(db, keys):
    found = 0
    ordered = sorted(keys)
    for start in range(0, len(ordered), CHUNK):
        found += db.scalar(select(func.count()).select_from(Dedupe).where(Dedupe.key.in_(ordered[start:start + CHUNK])))
    return found


def recover(inputs, tab_rows, apply=False):
    result = plan(inputs, tab_rows)
    keys = result.pop("keys")
    summary = {k: v for k, v in result.items() if k not in {"spreadsheet_id", "drive_folder_id"}}
    summary["keys"] = len(keys)
    cfg = settings()
    if (result["spreadsheet_id"], result["drive_folder_id"]) != (cfg.google_spreadsheet_id, cfg.google_drive_backup_folder_id):
        raise RecoveryError("destination_differs_from_configuration")
    if not apply:
        summary["applied"] = False
        return summary
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
        state.export_bucket_positions = dict(result["bucket_positions"])
        state.export_next_position = result["next_position"]
        state.export_layout = "alphabetical" if result["bucket_positions"] else "sequential"
        state.export_spreadsheet_id, state.export_folder_id = result["spreadsheet_id"], result["drive_folder_id"]
        state.export_shard_rows = result["shard_rows"]
        state.export_destination_verified = True
    with session() as db:
        state = db.get(PipelineState, 1)
        verified = (_present(db, keys) == len(keys)
                    and dict(state.export_bucket_positions) == result["bucket_positions"]
                    and state.export_next_position == result["next_position"])
    summary.update(applied=True, insert_statements=statements, verified=verified)
    if not verified:
        raise RecoveryError("post_recovery_verification_failed")
    return summary
