# Architecture — autonomous short-term-insurance data pipeline

This supersedes the Phase-1 sample-dashboard design and historical root architecture. Phase 2 remains
controlled testing. No production seven-day campaign, outbound email or MWEB sender is authorized yet.

## Responsibilities

| Platform | Responsibility |
|---|---|
| Source adapters | Bounded Bluesky/YouTube collection and source-specific public profiles |
| Render FastAPI | Normalize, extract, dedupe, validate, rules/local ML, rank, selective inference, jobs and retention |
| PostgreSQL | Temporary payloads/queues, campaign state, atomic cursors, hashes, metrics and delivery receipts |
| GroqCloud | Promising ambiguous evidence only; no contact/name invention |
| Apps Script | Pull finals, write/read back Sheet shards, verify Drive copies, ACK, dashboard and backups |
| Google Sheets | Primary finished dataset: `VALIDATED_001`, `VALIDATED_002`, etc. |
| Google Drive | Verified per-delivery copies and resumable daily final-Sheet backups |
| Optional Cloudflare | Wake Render, inspect pressure, request one bounded tick; no lead processing/storage |

Distribution is by role, not arbitrary percentages of records. No R2/D1 or GitHub Actions are required.
Only one acquisition/processing scheduler owner should be enabled for a production campaign.

## End-to-end flow

1. Collect bounded source scans. Accepted rows, source/content hashes, metrics and source cursor commit
   atomically. Source-specific adapters preserve only explicit public profile fields and provenance.
2. Normalize text, run configurable short-term-insurance product/intent/negative/recency rules,
   dedupe identities, extract public contacts and remove obvious rejected/unnecessary raw payloads.
3. Validate email syntax, role/disposable rules and DNS/MX before any semantic inference. There is no
   aggressive SMTP mailbox probing and AI cannot override hard-invalid contact data.
4. Rules, contact quality, product signals, South African evidence and optional TF-IDF/logistic
   regression produce an auditable ordinal 0–10 rank. South African evidence is a boost; absence is
   not an automatic hard rejection when other intent evidence is strong.
5. Very strong deterministic candidates can become final directly. Selected high-value ambiguous
   candidates enter the GroqCloud semantic path; low/negative records are rejected or deferred.
6. GroqCloud uses strict structured output, persisted request/token soft budgets and bounded retries.
   It never receives the entire raw dataset and cannot invent or repair contact/name fields.
7. Finals wait in PostgreSQL only until delivery. Render assigns stable positions in Sheet shards.
   Apps Script writes literal values, reads them back, writes/reuses immutable Drive JSON/CSV copies,
   then ACKs the exact batch/placements/checksum.
8. Only a matching ACK deletes the heavy lead/candidate/classification payload. Compact hashes,
   metrics, campaign state and delivery receipts remain for idempotency and operations.

## Persistent campaign lifecycle

The production one-week clock is not a manually calculated environment date. `pipeline_state`
persists:

- `campaign_id`
- `campaign_status`
- `campaign_started_at`
- `campaign_deadline`
- `campaign_raw_target`
- `campaign_raw_scanned`
- `campaign_last_progress_at`
- `campaign_completed_at`

`POST /admin/campaign/start` records the real start timestamp and calculates the deadline using
`CAMPAIGN_DURATION_DAYS` (default seven) and the target using `CAMPAIGN_RAW_TARGET` (default 200,000).
The persisted state machine is:

```text
READY -> RUNNING -> DRAINING -> FINALIZING -> COMPLETE
```

Reaching the target or deadline switches to `DRAINING`: no new acquisition, but normalize/validate,
selective inference, delivery and cleanup continue. Once transient work and claimed deliveries reach
zero, the state advances through `FINALIZING` to `COMPLETE` and the pipeline is paused. Cloudflare or
another approved scheduler can advance these transitions through ordinary status/tick calls after
Render sleeps/restarts. Production collection is denied unless the campaign is actually `RUNNING`.

A new campaign refuses to start while transient raw/candidate/final rows or an unacknowledged delivery
batch remain, preventing campaign cross-contamination.

## Delivery contract

`POST /exports/claim` locks pipeline state and replays an outstanding immutable batch or allocates up
to 200 leads. One active claim serializes delivery. Destination and shard size are pinned on first
allocation; `SHEET_SHARD_ROWS` defaults to 5,000 data rows, excluding the header. The envelope carries
that capacity so Apps Script never keeps a second hard-coded boundary.

Final fields are:

`lead_id`, `campaign_id`, `display_name`, `first_name`, `salutation`, `username`, `business_name`,
`email`, `source`, `source_evidence`, `source_url`, `email_source_url`, `product_type`, `rank_score`,
`score`, `validation_status`, `created_at`.

Only explicit given-name or operator-verified provenance can produce `Good day Mohammed,`. Missing,
unreliable, malformed or business-only names produce `Good day,`. Display names, email usernames and
AI output are never guessed into personalization.

Stable campaign/lead IDs, assigned rows and literal-text writes prevent duplicate/formula corruption.
A conflicting existing row fails closed. ACK requires exact batch/checksum/destination/placement and
verified Drive file/checksum. Identical replay succeeds; conflicting replay fails. Retries after Sheet
write, Drive write or a lost ACK response reuse the same batch and files.

`deliveryTick()` is an output-side Apps Script wrapper. When `DELIVERY_ENABLED=true`, an explicitly
installed five-minute trigger can drain one bounded batch per invocation independently of acquisition
scheduling. Dashboard and backup triggers remain separate.

## Semantic provider and free-tier protection

`SEMANTIC_PROVIDER=groq` is the default. GroqCloud and xAI/Grok are separate providers.

GroqCloud uses:

- `GROQ_ENABLED`
- `GROQ_API_KEY`
- `GROQ_MODEL`
- `GROQ_DAILY_REQUEST_SOFT_CAP` (default 800)
- `GROQ_DAILY_TOKEN_SOFT_CAP` (default 150,000)

Every request attempt is reserved persistently before the HTTP call. Returned token usage is then
accounted. The token soft cap is necessarily based on already-accounted usage, so one admitted request
may take the total slightly above the threshold. Quota/rate failures leave candidate state durable for
later attempts instead of stopping collection/validation/export work.

Legacy xAI/Grok remains an explicit optional provider through `GROK_ENABLED` + `XAI_*`; it is not the
default path and must not be confused with GroqCloud.

## Schema

`0001_phase1` creates the original 13 application tables. `0002_final_delivery` adds profiles, ranks,
candidate linkage, pressure/destination state and `export_batches`, bringing the application to 14
tables. `0003_campaign_lifecycle` extends `pipeline_state` with the persistent campaign clock/counters;
it does not create another table.

| Table | Purpose / lifecycle |
|---|---|
| `source_cursors` | Durable checkpoints, source gate and circuit breaker |
| `pipeline_state` | Pause/pressure, campaign lifecycle, pinned destination and next final row |
| `processing_jobs` | Idempotent durable execution/retries, operational retention |
| `source_records` | Temporary text/profile while work remains |
| `dedupe_index` | Compact durable hashes and temporary candidate claims |
| `identity_index` | Source identity and optional email hash |
| `candidates` | Temporary evidence/contacts/profile/rank/stage |
| `classification_results` | Unique candidate/provider result, cascade deletion |
| `email_validation` | Hashed validation cache, bounded retention |
| `validated_leads` | Final payload awaiting verified delivery ACK |
| `pipeline_metrics` | Aggregate daily counters |
| `api_usage` | Provider request reservations/tokens/estimated cost |
| `failed_jobs` | Sanitized failure codes |
| `export_batches` | Active immutable delivery payload, then compact ACK receipt |

Migrations are explicit and never applied on web-service startup. Rollback tests belong in disposable
empty databases. Delivery downgrade protection must never discard existing receipts silently.

## Backpressure and retention

Default queue high/low watermarks are 1,800/400 work rows. Raw, candidates and pending finals count; a
candidate plus its pending final lead is intentionally counted conservatively. DB high/low defaults
are 750/600 MB. High queue pressure stops acquisition until the low watermark; storage pressure stops
new processing/collection paths that could grow the database and prioritizes cleanup/export.

Rejected/processed raw data and terminal intermediate candidates are continuously reclaimed according
to retention settings. Pending finals and unfinished raw remain protected. PostgreSQL is a conveyor
belt, not the permanent final-lead archive. `DELETE` reuses pages but does not normally shrink the
allocated database file, so actual database size/autovacuum must still be monitored below the 1 GB
free-tier ceiling.

Advisory executor ownership, bounded transactions, atomic source cursors and delivery ACKs support
restart/replay. There is no 200,000-row transaction. Twenty thousand final leads from 200,000 raw
records is an aspirational yield, not a measured or guaranteed conversion rate. `DELIVERABLE_DOMAIN`
means syntax/domain/MX passed, not mailbox existence or permission to send marketing email.
