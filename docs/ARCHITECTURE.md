# Phase-1 architecture — authoritative

This supersedes the older Cloudflare/MWEB design. Phase 1 is a complete code build; runtime verification belongs to Phase 2. Do not start real acquisition during building.

## Platforms

Render hosts Python/FastAPI and PostgreSQL. PostgreSQL stores bounded source text, stage state, temporary candidates, qualified leads, hashes and metrics. Apps Script calls authenticated HTTP endpoints, writes values to Sheets, and saves daily useful-data exports to Drive under the authorized Google account. Python needs no Google service account or Drive credentials.

Cloudflare R2/D1, GitHub Actions, MWEB, SMTP and forwarding are absent. The historical architecture file is retained for context only.

## Flow and transaction boundaries

1. **Collect:** adapters receive persisted cursors and a raw scan limit, default 600. Bluesky preselects keywords within bounded WebSocket windows. YouTube discovers configured videos/channels/searches and reads top-level comments. Source-record/content hashes, rows, metrics, checkpoint and collection-job completion commit atomically. Full upstream responses are never stored.
2. **Normalize/filter:** Unicode/entity/whitespace normalization precedes keyword, negative and recency rules. Weights, products, geography and thresholds are in YAML. Negatives/low scores are deleted. Accepted emails/identities and active candidate identities are deduplicated before inference. Public contacts/provenance are extracted before evidence redaction/reduction to 2000 characters.
3. **Classify:** high deterministic confidence with product/geography bypasses AI. Otherwise a hash-pinned local TF-IDF/logistic model is used if present. Confident negatives reject; positives still require product/geography gates. Grok handles remaining ambiguity with strict JSON. Input excludes author/profile payloads and redacts email/phone/URL/handle data. Unavailable AI defers to retries/REVIEW.
4. **Validate:** public-text emails pass normalization, syntax, role/disposable checks, DNS MX and null-MX rejection. Temporary DNS failures retry. Email and identity uniqueness prevent duplicate leads. Intent and contact source URLs survive. No SMTP probing, guessed emails, private profiles or cross-site enrichment.
5. **Cleanup/export:** stages commit independently. Raw is removed after reduction by default. Retention removes expired terminal raw/candidates/jobs/cache in bounded chunks. Unfinished raw remains recoverable. Validated leads remain until an explicit later lifecycle policy. Sheets uses samples; Drive gets useful-lead snapshots.

## Database schema

Immutable Alembic revision `0001_phase1` creates 13 tables:

| Table | Purpose |
|---|---|
| source_cursors | Source JSON checkpoint, enabled state, retry/time and last success |
| pipeline_state | Singleton durable pause, initially true |
| processing_jobs | Idempotency key, kind/source, attempts, timestamps, cursors, status/result |
| source_records | Short-lived bounded text, source reference and identity/content hashes |
| dedupe_index | Record/content/email/lead hashes plus temporary candidate identity claims |
| identity_index | Source identity hash, optional email hash, first/last seen |
| candidates | Short evidence, contacts/provenance, scores, product, stage/retry |
| classification_results | Unique candidate/provider result and version; cascades on expiry |
| email_validation | Email-hash DNS result/cache; no duplicated raw email |
| validated_leads | Unique email and source identity, provenance, evidence, product/score |
| pipeline_metrics | Daily per-source counters |
| api_usage | Persistent provider budgets, input/output tokens and estimated cost |
| failed_jobs | Sanitized error codes/IDs, no raw exceptions or source payloads |

Indexes cover source/status, queue availability, timestamps, scores, identity/email/content hashes and composite claims. SHA-256 uses normalized input; primary/unique constraints are final enforcement. Hash admissions use savepoints for all-or-nothing multi-key claims. Hashes are pseudonymous identifiers, not encryption.

## Jobs and controls

HTTP enqueue commits before returning. The optional internal scheduler or Apps Script tick enqueues eligible missing stages; `/jobs/process-next` requests one bounded execution. A PostgreSQL session advisory lock permits one executor across processes. Process loss releases it, allowing abandoned RUNNING jobs to be reclaimed. Each candidate or small source batch commits independently; no 200,000-row transaction exists.

Errors back off; repeated failures enter FAILED/REVIEW and close the failing source's DB gate. Operator retries preserve qualification gates. Environment flags, YAML source flags, durable source flags and pause all apply. Cleanup is permitted while paused. Deploying does not install or enable schedules.

The executor is initially serial. ML/Grok/DNS may yield fewer than 600 candidates within its time budget. Free-service uptime, CPU, API quotas and source quality must be measured before the one-week goal. The schedule is restartable, not a parallel worker fleet.

## Adapters and budgets

- **Bluesky:** legacy-compatible `/subscribe`, configured collection, microsecond cursor with overlap, record/time/connection bounds and limited reconnects. Public replay is finite; long outages may leave gaps.
- **YouTube:** video IDs, channel searches, keyword searches, discovery/video queue, comment-page cursor and refresh windows. Top-level comments only. Disabled comments skip; invalid page tokens replay through dedupe. Search-call and conservative unit caps are persisted before requests; provider denial preserves cursors and delays retry.
- **Grok:** strict JSON Schema/Pydantic contract over `/v1/chat/completions`; two bounded attempts with backoff, daily reservations, token/cost accounting. Classification jobs batch candidates but call inference sequentially. No asynchronous provider Batch API is needed for the initial small workload. A crash after response but before DB commit may repeat a paid request; each attempt consumes budget.

New approved public-web/forum sources can implement `Adapter` and join the registry without changing qualification logic.

## Google

Script Properties contain backend origin, separate read/operator tokens, Sheet/folder IDs and schedule gates. Tabs are SUMMARY, SOURCE_STATS, VALIDATED, FAILED, PROCESSING, EXPORT_HISTORY. Values are server-computed and formula-leading strings escaped. VALIDATED is capped at 500 rows; Sheets is not the raw database.

Drive backups freeze a lead high-water ID, fetch 1000-row CSV parts and save deterministic daily filenames with SHA-256 sidecars. Script lock and persisted stage/page state support retries. Source CSV, processing/failure JSON and completion manifest are also written. These are useful-data exports; full operational-state recovery requires a separate PostgreSQL backup. See [operations](OPERATIONS.md).

## Protocol references checked during build

- [Bluesky legacy Jetstream](https://github.com/bluesky-social/jetstream-legacy)
- [YouTube search](https://developers.google.com/youtube/v3/docs/search/list) and [comment threads](https://developers.google.com/youtube/v3/docs/commentThreads/list)
- [xAI structured outputs](https://docs.x.ai/developers/model-capabilities/text/structured-outputs)
- [Apps Script Drive folders](https://developers.google.com/apps-script/reference/drive/folder), [locks](https://developers.google.com/apps-script/reference/lock/lock-service), [triggers](https://developers.google.com/apps-script/guides/triggers/installable)
- [Render Blueprint schema](https://render.com/schema/render.yaml.json)

These define protocol shapes, not verified account access. Recheck actual quotas/model pricing before live calls.
