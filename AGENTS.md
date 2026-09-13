# AGENTS.md

Instructions for Codex and other coding agents working in this repository.

## Current milestone

Phase 1 now includes both Bluesky and YouTube, Google Apps Script dashboard and Google Drive
daily useful-data backups. The code build is complete; **do not run tests, migrations, real
collection, inference, Google writes, or production schedules until the user starts Phase 2**.
Static syntax/lint/build checks are allowed. Read `docs/PHASE_2_TESTING.md` before that phase.
`docs/ARCHITECTURE.md` is authoritative; the root historical architecture is superseded.

Build the **data acquisition and qualification engine only**.

Do not implement production outbound email yet.

The target first milestone is:

```text
source
  -> collect
  -> checkpoint
  -> normalize
  -> filter
  -> dedupe
  -> score
  -> classify uncertain cases
  -> validate email
  -> store useful candidate
  -> export
```

## Architecture rules

1. Render Web Service is the Python processing runtime for the MVP.
2. Render PostgreSQL is the temporary operational database for the MVP.
3. Do not add Cloudflare R2/D1 unless explicitly requested.
4. Do not use Google Sheets as the raw database.
5. Do not store full raw payloads indefinitely.
6. Delete rejected raw material after processing or after the configured short retention period.
7. Keep source cursors/checkpoints so a failed run resumes rather than restarts.
8. Every source adapter must be independently enabled/disabled.
9. Every batch must be idempotent and safely retryable.
10. Search terms and scoring rules belong in configuration files, not hard-coded collectors.

## AI usage rules

1. Run deterministic filters before LLM classification.
2. Prefer a lightweight local classifier when enough labelled examples exist.
3. Use Grok only for semantic ambiguity / high-value classification.
4. Require structured JSON output from Grok.
5. Do not send unnecessary personal data to Grok.
6. Do not use Grok for email syntax, DNS, MX, dedupe, or keyword checks.

## Data-retention rules

Default philosophy:

```text
raw -> short-lived
rejected -> delete
candidate -> temporary
validated lead -> retain through campaign/export lifecycle
dedupe hash -> long-lived
suppression hash -> long-lived once outbound exists
```

Initial config should support:

```text
RAW_RETENTION_DAYS=3
DELETE_REJECTED_RAW=true
```

Do not create an archive of all collected source data unless explicitly requested.

## Database rules

Use migrations.

At minimum implement:

```text
source_cursors
processing_jobs
identity_index
candidates
```

Keep giant source payloads out of `candidates`.

Prefer hashes/short evidence fields over duplicated full documents.

## Secrets

Never commit:

```text
DATABASE_URL
XAI_API_KEY
YOUTUBE_API_KEY
Render credentials
Google credentials
MWEB credentials
API bearer tokens
```

Provide `.env.example` with empty placeholders only.

## Initial API

Implement only what is useful for the data phase:

```http
GET  /health
POST /jobs/run-batch
GET  /jobs/{batch_id}
GET  /metrics/summary
GET  /leads/validated
GET  /export/validated.csv
```

Authentication should be added before exposing any destructive or administrative endpoint.

## Source implementation order

Start with one source.

Preferred order:

```text
1. Bluesky Jetstream
2. YouTube
3. approved public-web adapters
```

Do not implement multiple broken scrapers in parallel.

## Testing

Add tests for:

```text
normalisation
dedupe
scoring
email validation
cursor advancement
batch retry/idempotency
Grok JSON parsing
cleanup/retention
```

A failed batch must not duplicate accepted candidates when retried.

## Scaling

Begin with:

```text
600 records/batch (use 1–5 for Phase-2 source checks)
```

Scale only after the entire flow completes reliably.

The long-term acquisition target is approximately:

```text
200,000 raw records over 7 days
```

This is a processing-throughput target, not a requirement to permanently store 200,000 rows.

## Outbound email

Do not add MWEB SMTP, consent-request sending, or forwarding automation until explicitly requested after the data MVP has been reviewed.

## Implemented operational rules

- Keep acquisition, processing, source, Grok and scheduler defaults disabled; the database starts paused.
- Keep both example environment files empty. Actual local configuration is in ignored `.env`.
- Google Sheets is a values dashboard capped to samples; Google Drive holds paginated useful-lead CSV/JSON exports.
- Never invoke Apps Script trigger installation just because its code exists.
- Migrations are explicit, versioned and absent from application startup.
- Preserve durable job/cursor/hash atomicity and PostgreSQL advisory executor ownership.
- Do not add external messaging or outbound libraries during this phase.

If outbound work is later added, compliance/suppression state must be treated as durable system state and never bypassed.
