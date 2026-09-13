# Phase 2 — controlled testing procedure

Do not skip directly to the seven-day run. This procedure was partially executed on 2026-09-13;
results and remaining gates are recorded in [PHASE_2_TEST_REPORT.md](PHASE_2_TEST_REPORT.md).

## 1. Prepare isolated configuration

Use a disposable PostgreSQL database, a test Sheet and a test Drive subfolder. Do not use the
operational database for schema tests. Rotate previously exposed legacy credentials. Keep every
scheduler off. Set actual process DATABASE_URL to the disposable DB so it overrides local `.env`.
Use separate read/operator test tokens, each at least 32 characters.

```powershell
$env:DATABASE_URL='<disposable PostgreSQL URL>'
$env:TEST_DATABASE_URL='<another disposable PostgreSQL URL using postgresql+psycopg://>'
$env:ACQUISITION_ENABLED='false'
$env:PROCESSING_ENABLED='false'
$env:BLUESKY_ENABLED='false'
$env:YOUTUBE_ENABLED='false'
$env:GROK_ENABLED='false'
$env:SCHEDULER_ENABLED='false'
$env:PROCESSING_BATCH_SIZE='5'
$env:GROK_DAILY_CANDIDATE_CAP='2'
```

Replace placeholders privately; never save real URLs/tokens in this document or terminal transcripts.

## 2. Offline component tests

```powershell
.\.venv\Scripts\python -m pytest -m 'not integration' -q
```

These cover normalization/hashes/scoring/recency/geography, contact syntax/MX with fake DNS,
Grok JSON validation/redaction, mocked Bluesky checkpointing, YouTube quota stop, and CSV injection.
No real source or AI calls should occur. Tests override `.env` and use fake providers.

## 3. Migrations and transactional tests

```powershell
.\.venv\Scripts\python -m alembic -c services/render_api/alembic.ini upgrade head
.\.venv\Scripts\python -m alembic -c services/render_api/alembic.ini current
.\.venv\Scripts\python -m alembic -c services/render_api/alembic.ini upgrade head
.\.venv\Scripts\python -m pytest -m integration -q
```

The ORM integration fixtures create/drop random `test_*` schemas only in TEST_DATABASE_URL.
They cover atomic hashes, replay/cursor/candidate/lead idempotency, retention and disabled jobs.
The separate Alembic commands verify the actual migration; fixture create_all does not substitute
for migration testing. On a disposable DB only, verify downgrade/upgrade as an additional migration
check, then inspect constraints/indexes and run `alembic check` for unexpected schema drift.

## 4. API and operational controls

Start the app locally using the README command. Check health, 401 with no/wrong token, read token
rejected for writes, 409 when paused/disabled, source gating, queue status and pagination. Enqueue a
tiny cleanup or processing job with an Idempotency-Key. Call `/jobs/process-next` and poll its durable
status. Repeat the same enqueue key and verify it returns the same job. Check conflicting key reuse.
Verify no automatic work occurs without explicit executor/scheduler calls.

## 5. Tiny source checks, individually

Use a temporary CONFIG_DIR copy with only one source enabled at a time. Set that source environment
flag and ACQUISITION_ENABLED true; keep processing and Grok off. Enable the database source control,
resume the pipeline, enqueue `collect` with `batch_size=1` or `5`, then call process-next once.

For Bluesky, verify the configured host accepts the legacy protocol, bounded timeout, saved cursor,
same-event replay dedupe and reconnect. A keyword-free sample may produce zero stored rows; that is
not a collector failure. For YouTube, initially use one approved known video ID with query/channel
lists empty and a small unit cap. Verify pagination, disabled comments, empty pages and quota stop.
Do not use broad discovery until a later controlled check. Pause and disable acquisition afterwards.

## 6. Tiny classification and contacts

Enable processing only. Advance normalize with explicit jobs. Inspect deterministic rejection and
short evidence/provenance. Validate local-model absent behaviour; train only after enough labelled
examples exist. Enable Grok for at most 1–2 ambiguous, de-identified examples with an explicit tiny
cap. Verify structured JSON, retry/cap counters, token accounting and REVIEW on unavailability.
Disable Grok afterwards. Test public-contact DNS with a tiny sample, null MX/disposable cases and
transient failures. Verify domain validation is never labelled mailbox confirmation.

## 7. Google dashboard and Drive

Upload repository Apps Script code to the test project, set test origin/IDs/tokens, authorize scopes,
and leave trigger flags false. Manually refresh the Sheet. Check all six tabs, safe string handling,
sample bounds, absence of raw rows, and failure visibility when Render is unavailable.

Run dailyBackup against tiny validated fixtures; inspect CSV, sidecar checksum, stats, summary,
manifest and EXPORT_HISTORY. Run it twice and confirm no duplicate daily files. Force an interruption
between parts and resume. Test failure after file creation but before checkpoint, downtime, invalid
folder permissions, and snapshot boundary while new leads appear. Verify restore of exported lead
values in an isolated recovery exercise; do not assume CSV backups are full database recovery.

## 8. Restart, concurrency and retention

Interrupt a running tiny job, restart, and request execution. Verify reclaim, monotonic source cursor,
no duplicate candidates/leads and conservative paid-call accounting. Send two executor requests and
confirm one advisory-lock owner. Check same-key enqueue, duplicate email across identities, and old
raw/candidate cleanup without deleting unfinished raw or validated leads. Inspect oldest-pending alerts.

## Exit gate

Record results and failures, fix discovered issues, and stop for the user's Phase-3 instruction.
Phase 3 runs ONE small real source-to-Sheet-to-Drive flow after Phase 2 passes. Only after that
succeeds may Phase 4 explicitly activate the approximately 200,000-record/seven-day campaign.
No phase described here authorizes email sending or MWEB implementation.
