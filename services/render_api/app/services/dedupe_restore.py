"""Rebuild compact dedupe hashes from the finished Google dataset before acquisition resumes.

The temporary PostgreSQL database can expire or be replaced. The delivered rows (Sheet exports or
the verified Drive delivery/backup CSVs) still contain enough to restore the hashes that prevent
re-admitting the same people and posts:

* ``email:<digest(email)>`` for leads that had an email;
* ``record:<digest(source:source_record_id)>`` parsed from the public post URL;
* ``lead:<digest(source:identity)>`` for Bluesky, whose post URL carries the account DID.

The digests are computed exactly as the collector/pipeline compute them. No personal data is stored.
"""

import csv
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.database import session
from app.models import Dedupe
from app.qualification import digest


def keys_for_row(row):
    keys = set()
    email = (row.get("email") or "").strip()
    if email:
        keys.add("email:" + digest(email))
    source = (row.get("source") or "").strip()
    url = (row.get("source_url") or "").strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return keys
    if source == "bluesky" and parts.hostname == "bsky.app":
        segments = parts.path.strip("/").split("/")
        if len(segments) == 4 and segments[0] == "profile" and segments[2] == "post" and segments[1].startswith("did:"):
            did, rkey = segments[1], segments[3]
            keys.add("record:" + digest(f"bluesky:{did}/{rkey}"))
            keys.add("lead:" + digest(f"bluesky:{did}"))
    elif source == "youtube" and parts.hostname in {"www.youtube.com", "youtube.com"}:
        comment = parse_qs(parts.query).get("lc", [""])[0]
        if comment:
            keys.add("record:" + digest(f"youtube:{comment}"))
    return keys


def read_rows(paths):
    for path in paths:
        with Path(path).open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "source_url" not in reader.fieldnames:
                raise ValueError(f"not a delivered-lead CSV (missing source_url header): {Path(path).name}")
            for row in reader:
                if (row.get("validation_status") or "") == "SYNTHETIC_TEST":
                    continue  # labelled test rows are not real people
                yield row


def restore(paths, apply=False):
    expected, rows = set(), 0
    for row in read_rows(paths):
        rows += 1
        expected |= keys_for_row(row)
    report = {"rows": rows, "keys": len(expected),
              "by_kind": {kind: sum(1 for k in expected if k.startswith(kind + ":")) for kind in ("email", "record", "lead")}}
    with session() as db:
        present = set(db.scalars(select(Dedupe.key).where(Dedupe.key.in_(expected)))) if expected else set()
        report["already_present"] = len(present)
        if apply:
            for key in sorted(expected - present):
                db.execute(insert(Dedupe).values(key=key).on_conflict_do_nothing())
    if apply:
        with session() as db:
            verified = set(db.scalars(select(Dedupe.key).where(Dedupe.key.in_(expected)))) if expected else set()
        report["inserted"] = len(expected - present)
        report["verified"] = verified == expected
    report["applied"] = apply
    return report
