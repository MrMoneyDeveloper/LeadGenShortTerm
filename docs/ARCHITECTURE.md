# Architecture — final Google delivery

This supersedes the Phase-1 sample-dashboard design and the historical root architecture.
Phase 2 remains controlled testing. No production acquisition, schedules, email or MWEB are authorized.

## Responsibilities

| Platform | Responsibility |
|---|---|
| Source adapters | Bounded Bluesky/YouTube collection and source-specific profiles |
| Render FastAPI | Normalize, extract, dedupe, validate, rules/local ML, rank, selective inference, jobs and retention |
| PostgreSQL | Temporary payloads/queues, atomic cursors, campaign hashes, metrics, delivery allocation/receipts |
| GroqCloud | Promising ambiguous evidence only; no contact or name invention |
| Apps Script | Pull finals, write/read back Sheet rows, verify Drive copies, ACK, dashboard and backups |
| Google Sheets | Final dataset: VALIDATED_001, VALIDATED_002, etc. |
| Google Drive | Verified delivery copies and resumable daily final-Sheet backups |
| Optional Cloudflare | Wake Render, inspect pressure, request one bounded tick; no lead processing/storage |

Roles are distributed, not arbitrary percentages of records. No R2/D1 or GitHub Actions are required.
Schedulers remain disabled; select one acquisition scheduler owner only in a future approved phase.

## Flow

1. Collect bounded scans. Source/content hashes, accepted rows, metrics, checkpoint and collection
   completion commit atomically. Preserve usable source references and bounded public metadata.
2. Source profiles map source-specific public fields to canonical labels, URLs and whitelisted
   metadata. YouTube display names and Bluesky DIDs are not reliable given names.
3. Normalize, run configurable product/intent/negative/recency/geography rules, dedupe identities,
   extract public contacts and remove rejected/unnecessary raw payloads.
4. Validate syntax, role/disposable status and DNS/MX before inference. Transient DNS retries are
   bounded. No SMTP probing, guessed contacts or private-profile enrichment.
5. Rules, contact quality, product/geography and optional TF-IDF/logistic regression produce an
   ordinal rank. YAML thresholds initially direct-final 9.5, semantic 8, deferred 6. These are
   provisional decision thresholds, not calibrated probabilities; hard negatives still reject.
6. CLASSIFY orders by rank. GroqCloud requires strict JSON, reserves every attempt against the daily
   cap, records tokens/estimated cost and defers failures. Explicit provider selection separates
   GroqCloud from the legacy xAI/Grok client. No model can invent names/contacts or grant a perfect score.
7. Finals enter the delivery queue. Apps Script writes assigned Sheet rows, reads them back, creates
   and verifies immutable Drive delivery JSON/CSV, then sends the exact receipt to Render.
8. Only matching ACK deletes the lead/candidate/classification payload. Compact receipts, metrics,
   record/content/email/identity hashes remain for campaign idempotency.

## Delivery contract

POST /exports/claim locks pipeline state and replays an outstanding immutable batch or allocates up
 to 200 leads. One active claim serializes delivery. Destination and shard size are pinned on first
allocation; changing them requires an explicit migration. SHEET_SHARD_ROWS defaults to 5,000 data
rows, excluding the header. The envelope carries shard_rows and Apps Script validates that same
value. Smaller shards are tested; there is no separate Apps Script row-limit setting.

Final fields: lead_id, campaign_id, display_name, first_name, salutation, username, business_name,
email, source, source_evidence, source_url, email_source_url, product_type, rank_score, score,
validation_status, created_at. Evidence is a bounded redacted excerpt.

Only explicit given-name or operator-verified provenance with a reliable name produces
`Good day Mohammed,`. Missing, unreliable, malformed or business-only names produce `Good day,`.
Never split a display name, guess from email or use model-inferred personalization.

Stable campaign/lead IDs, assigned rows and literal-text writes prevent duplicates and formulas.
Conflicting existing rows fail closed. SHA-256 uses canonical item ordering reconstructed after
JSONB reads. ACK requires exact batch/checksum/destinations/placements and Drive file/checksum.
Identical replay succeeds; conflicting replay fails. The authenticated Google agent attests to
readback; Render does not independently query Google. Protect the operator token accordingly.

Retries after Sheet write, Drive write or lost ACK reuse the same batch/files/receipt. Claim and
timeout never permit deletion. Daily backups read final Sheet shards because ACKed payloads no
longer live in PG. Legacy /exports/validated now lists pending leads, not the complete campaign.

## Schema

0001_phase1 creates 13 tables. 0002_final_delivery adds profiles, ranks, candidate linkage,
pressure/destination state and export_batches: 14 application tables, excluding alembic_version.

| Table | Purpose/lifecycle |
|---|---|
| source_cursors | Durable checkpoints, source gate and circuit breaker |
| pipeline_state | Pause/pressure, pinned destination, next final row |
| processing_jobs | Idempotent durable execution/retries, operational retention |
| source_records | Temporary text/profile while work remains |
| dedupe_index | Compact durable hashes and temporary candidate claims |
| identity_index | Source identity and optional email hash |
| candidates | Temporary evidence/contacts/profile/rank/stage |
| classification_results | Unique candidate/provider result, cascade deletion |
| email_validation | Hashed validation cache, bounded retention |
| validated_leads | Final payload awaiting delivery ACK |
| pipeline_metrics | Aggregate daily counters |
| api_usage | Provider reservations/tokens/estimated cost |
| failed_jobs | Sanitized failure codes |
| export_batches | Active immutable payload, then compact ACK receipt |

Unique constraints enforce hash/contact/source identity uniqueness. Indexes cover source/hash,
stage/rank/availability and timestamps. Migrations are explicit, absent from startup. Downgrade
refuses to discard delivery receipts. Rollback testing belongs in disposable empty databases.

## Backpressure and retention

Default high/low queue watermarks: 1,800/400 work rows. Raw, candidates and pending finals count;
a candidate with a pending lead counts twice, conservatively. DB high/low: 750/600 MB. High water
stops acquisition until low water. Storage pressure permits cleanup/export only. Campaign deadline
stops collection and drains work. Pause and every environment/source flag still apply.

Cleanup protects pending final leads and unfinished raw. Terminal review/deferred/no-contact
payloads have a short retention window and may be evicted in bounded chunks under pressure.
Active work remains recoverable. Compact hashes/receipts survive through campaign lifecycle.
DELETE frees reusable PostgreSQL pages but does not normally shrink allocated files. Monitor
actual DB size/autovacuum; row caps alone cannot guarantee the 1 GB limit. Storage stops may
require maintenance before resuming.

Advisory executor ownership, bounded transactions and atomic cursors support restart/replay.
There is no 200,000-row transaction. Measure CPU, DNS, API and Google latency in controlled tests.
20,000 useful contacts from 200,000 raw records remains an aspiration, not measured yield.
DELIVERABLE_DOMAIN means syntax and domain MX passed, not mailbox confirmation or sending permission.
