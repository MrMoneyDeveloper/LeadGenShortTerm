"""Controlled local Phase-2 throughput/storage measurement.

This script refuses remote databases and non-test environments. It inserts a
bounded synthetic batch, runs deterministic qualification and contact
validation, prints aggregate JSON, and never calls source or model APIs.
"""

import json
import os
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

import psutil
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app import jobs
from app.config import settings
from app.models import Candidate, ExportBatch, Lead, SourceRecord
from app.qualification import digest
from app.services import delivery, pipeline


def relation_bytes(db):
    return int(
        db.scalar(
            text(
                """
                SELECT coalesce(sum(pg_total_relation_size(quote_ident(tablename))), 0)
                FROM pg_tables
                WHERE schemaname = current_schema() AND tablename <> 'alembic_version'
                """
            )
        )
        or 0
    )


def main():
    cfg = settings()
    url = cfg.database_url.get_secret_value()
    parsed = urlparse(url.replace("postgresql+psycopg", "postgresql", 1))
    count = int(os.environ.get("PHASE2_BENCHMARK_RECORDS", "200"))
    if cfg.environment != "test":
        raise SystemExit("Refusing benchmark unless ENVIRONMENT=test")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("Refusing benchmark against a non-loopback database")
    if not parsed.path.lstrip("/").startswith("leadgen_phase2_perf"):
        raise SystemExit("Refusing benchmark outside a leadgen_phase2_perf database")
    if not 100 <= count <= 600:
        raise SystemExit("PHASE2_BENCHMARK_RECORDS must be between 100 and 600")
    if any((cfg.acquisition_enabled, cfg.processing_enabled, cfg.scheduler_enabled,
            cfg.groq_enabled, cfg.grok_enabled, cfg.local_model_path)):
        raise SystemExit("Disable acquisition, processing, schedulers and all model providers first")

    engine = create_engine(url, hide_parameters=True)
    process = psutil.Process()
    with Session(engine) as db:
        if any(db.scalar(select(func.count()).select_from(table)) for table in (SourceRecord, Candidate, Lead, ExportBatch)):
            raise SystemExit("Benchmark requires a fresh disposable database")
        baseline = relation_bytes(db)
    jobs.initialize()
    rss_before = process.memory_info().rss
    cpu_before = sum(process.cpu_times()[:2])
    inserted_at = datetime.now(UTC)
    started = time.perf_counter()
    with Session(engine) as db, db.begin():
        for i in range(count):
            body = (
                "Need a quote for car insurance in Johannesburg; premium increased and I am changing insurer. "
                f"phase2-{i}@example.org"
            )
            db.add(
                SourceRecord(
                    source="synthetic_benchmark",
                    source_record_id=f"phase2-{i}",
                    source_url=f"https://example.test/phase2/{i}",
                    identity_hash=digest(f"synthetic_benchmark:identity-{i}"),
                    content_hash=digest(body),
                    text=body,
                    published_at=inserted_at,
                )
            )
    insert_seconds = time.perf_counter() - started
    with Session(engine) as db:
        raw_bytes = relation_bytes(db)

    original_validator = pipeline.validate_contact
    original_semantic = pipeline.semantic.classify
    pipeline.validate_contact = lambda email: ("DELIVERABLE_DOMAIN", "controlled_fixture")
    def deny_semantic(*args):
        raise RuntimeError("Benchmark must never call an external model")
    pipeline.semantic.classify = deny_semantic
    stages = {}
    try:
        processing_started = time.perf_counter()
        stage_started = time.perf_counter()
        remaining = count
        while remaining:
            result = pipeline.normalize_batch(min(cfg.processing_batch_size, remaining))
            if not result["normalized"]:
                break
            remaining -= result["normalized"]
        stages["normalize"] = time.perf_counter() - stage_started
        stage_started = time.perf_counter()
        while True:
            result = pipeline.validate_batch(cfg.processing_batch_size)
            if not result["validated_checked"]:
                break
        stages["validate_fixture_dns"] = time.perf_counter() - stage_started
        stage_started = time.perf_counter()
        while pipeline.classify_batch(cfg.processing_batch_size)["classified"]:
            pass
        stages["rank_classify"] = time.perf_counter() - stage_started
        processing_seconds = time.perf_counter() - processing_started
    finally:
        pipeline.validate_contact = original_validator
        pipeline.semantic.classify = original_semantic

    cpu_after = sum(process.cpu_times()[:2])
    rss_after = process.memory_info().rss
    with Session(engine) as db:
        final_bytes = relation_bytes(db)
        leads = db.scalar(select(func.count()).select_from(Lead))
        candidates = db.scalar(select(func.count()).select_from(Candidate))
        raw_remaining = db.scalar(select(func.count()).select_from(SourceRecord))
    cfg.final_delivery_enabled = True
    cfg.google_spreadsheet_id, cfg.google_drive_backup_folder_id = "benchmark_sheet", "benchmark_folder"
    delivery_started = time.perf_counter()
    delivered = 0
    while True:
        batch = delivery.claim()
        if batch["status"] == "empty":
            break
        # Local protocol benchmark only: no Google writes are claimed or performed.
        delivery.acknowledge({
            "batch_id": batch["batch_id"], "checksum": batch["checksum"],
            "spreadsheet_id": batch["spreadsheet_id"], "drive_folder_id": batch["drive_folder_id"],
            "drive_file_id": "simulated_benchmark_receipt", "drive_checksum": batch["checksum"],
            "placements": [{k: item[k] for k in ("lead_id", "tab", "row")} for item in batch["items"]],
        })
        delivered += len(batch["items"])
    stages["claim_simulated_ack"] = time.perf_counter() - delivery_started
    with Session(engine) as db:
        drained_bytes = relation_bytes(db)
        remaining_payloads = sum(db.scalar(select(func.count()).select_from(t)) for t in (Lead, Candidate, SourceRecord))
    result = {
        "records": count,
        "stage_seconds": {k: round(v, 4) for k, v in stages.items()},
        "delivered_simulated": delivered,
        "remaining_payloads": remaining_payloads,
        "relation_after_ack_bytes": drained_bytes,
        "google_runtime_measured": False,
        "insert_seconds": round(insert_seconds, 4),
        "processing_seconds": round(processing_seconds, 4),
        "records_per_second": round(count / processing_seconds, 2),
        "process_cpu_seconds": round(cpu_after - cpu_before, 4),
        "rss_before_bytes": rss_before,
        "rss_after_bytes": rss_after,
        "rss_delta_bytes": rss_after - rss_before,
        "relation_baseline_bytes": baseline,
        "relation_after_raw_bytes": raw_bytes,
        "relation_after_pipeline_bytes": final_bytes,
        "raw_growth_bytes_per_record": round((raw_bytes - baseline) / count, 2),
        "retained_growth_bytes_per_lead": round((final_bytes - baseline) / max(leads, 1), 2),
        "leads": leads,
        "candidates": candidates,
        "raw_remaining": raw_remaining,
        "grok_calls": 0,
        "source_to_candidate_conversion": round(candidates / count, 4),
        "candidate_to_valid_contact_conversion": round(leads / max(candidates, 1), 4),
    }
    print(json.dumps(result, sort_keys=True))
    engine.dispose()


if __name__ == "__main__":
    main()
