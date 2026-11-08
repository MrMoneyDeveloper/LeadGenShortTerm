# Operational runbook

[RUNTIME_AGENT_CONTRACT.md](../RUNTIME_AGENT_CONTRACT.md) governs account selection and runtime safety.
[The current Phase-2 report](PHASE_2_TEST_REPORT.md) records the completed controlled validation.
Phase 3 still requires explicit approval. No production schedule, seven-day campaign or outbound
email is authorized by merging code.

Related runbooks: [local-device failover](LOCAL_FAILOVER.md), [source-yield gate](SOURCE_YIELD_GATE.md)
and the [data-collection hardening report](DATA_COLLECTION_HARDENING_REPORT.md) (alphabetical
delivery, final-lead target, capacity).

## Scheduling and campaign state

Cloudflare is the primary lightweight scheduler when explicitly enabled. One invocation wakes
Render, reads backpressure and requests one bounded job tick. It does not process lead payloads.
Select one acquisition/processing scheduler owner. Keep Render `SCHEDULER_ENABLED=false` and Apps
Script `PROCESSOR_SCHEDULE_ENABLED=false` when Cloudflare owns ticks. The in-process scheduler,
Apps Script processor wrapper and CLI tick remain explicit alternatives, not additional owners.

Google's delivery, dashboard and backup triggers are independent output jobs. Uploading code does
not install any triggers. Bounded test expiry guards can stop temporary delivery/Worker calls;
explicitly remove test schedules and restore disabled flags after any authorized runtime test.

The persisted lifecycle is `READY -> RUNNING -> DRAINING -> FINALIZING -> COMPLETE`. Campaign start
records the real timestamp, computes the deadline and stores the raw target/counter. Reaching target
or deadline stops acquisition while existing work drains. Once draining is observed, a fixed
`CAMPAIGN_SEMANTIC_DRAIN_HOURS` grace period bounds unfinished ranked semantic work. Final leads
awaiting delivery remain protected. Starting a campaign refuses existing transient payloads/claims.

## Pause, quota and recovery

Pause stops the next acquisition/processing stage; an in-flight bounded stage may finish. Cleanup
and delivery ACK remain available. Source flags are independent hard gates; repeated source failures
close the source circuit breaker. Quota stopping preserves checkpoints.

PostgreSQL advisory ownership serializes execution. Process loss releases the lock and later ticks
reclaim abandoned RUNNING jobs. Each candidate commits independently. Collection rows, hashes,
metrics, cursor and job completion commit atomically. Idempotency keys and database uniqueness
prevent duplicate storage on replay.

GroqCloud Free-tier admission persists minute/day reservations, provider headers/reset information
and cooldowns. Quota deferral retains priority and does not consume candidate failure attempts.
Other deterministic stages and delivery continue. Ordinary network/model/schema failures use bounded
failure handling. A crash after provider response can still repeat an external call; conservative
reservations remain consumed. Free-plan actual pipeline cost is zero, with historical list-price
arithmetic explicitly labelled reference information.

## Backpressure and retention

Queue high/low watermarks default to 1,800/400 work rows; database limits default to 750/600 MB.
Counts include source rows, candidates and pending finals. High pressure stops acquisition; storage
pressure prioritizes cleanup/export. Monitor actual allocated DB size, autovacuum and oldest work.
DELETE makes pages reusable but does not normally shrink allocated files.

- Full upstream responses are not archived. Source text is bounded to 16,000 characters.
- Rejected raw is removed immediately by default; processed raw is removed after reduction.
- Otherwise terminal raw expires after RAW_RETENTION_DAYS. Unfinished pending raw survives.
- Candidate evidence is bounded to 2,000 redacted characters with bounded public contacts/profiles.
- Terminal no-contact/review/deferred candidates use a 24-hour default review window; bounded
  pressure cleanup may discard them sooner. Active normalization/contact work remains recoverable.
- Ranked ambiguous work can expire after the fixed campaign semantic deadline.
- Every candidate linked to an undelivered final is protected until ACK.
- Matching Google ACK removes the delivered lead/candidate/classification payloads atomically.
- Compact source/contact hashes, checkpoints, metrics and receipts remain for idempotency.
- Completed/failed jobs, failures and validation cache use bounded operational retention.

The 200,000-record target concerns throughput; it does not require retaining 200,000 raw rows.

## Final delivery and backup recovery

Render assigns immutable positions in VALIDATED_### shards, default 5,000 data rows per tab. The
claim pins destination and shard size. Do not change a used destination's allocation state or sort
its rows in place. Use a separate workbook/copy for analysis or isolated rollover tests.

Apps Script writes literal plain-text rows, flushes and reads them back, verifies immutable Drive
JSON/CSV delivery copies, then ACKs exact IDs, destinations, placements and checksum. A row conflict
stops delivery. Never clear a conflict without inspecting and backing up the exact affected range.
After a lost ACK response, the saved delivery reference replays the same receipt before a new claim.

Final Sheets are the finished dataset. Daily backups read their shards, pin each last row, write
at most 1,000 rows per CSV part and checkpoint within a roughly three-minute work window. Source
statistics, processing JSON, sidecar checksums and a completion manifest accompany the snapshot.
New rows beyond the snapshot boundary appear in a later backup; their per-delivery copies already
exist before ACK. Repeating the daily operation reuses checked files and repairs history.

The useful-data backup does not restore pending work, dedupe, source cursors or jobs. Before moving
or losing the temporary PostgreSQL instance, preserve a private operational dump and verify restore
in an isolated environment. Review backup/lead retention before production and keep destinations
private. There is no automatic outbound notification or sender.

## Troubleshooting

| Symptom | Inspect / act |
|---|---|
| 503 health | Intended DATABASE_URL/network and explicit migration head 0005 |
| 401 reads/writes | Separate matching bearer tokens in the caller's private configuration |
| 409 enqueue | Pause, source/environment/campaign gates, retry time or idempotency conflict |
| Jobs stay queued | Approved scheduler/executor, eligible timestamps and source circuit breaker |
| Semantic queue waits | Groq persistent cooldown, request/token reservations and reset times |
| REVIEW / CONTACT_REVIEW | Bounded inference or contact failure reason; retry through normal gates |
| No contacts | Only public text is inspected; no invented or hidden contacts |
| Google row conflict | Exact assigned row/header/formula/readback; preserve payload until resolved |
| Backup failure | Script status, destination permissions, manifest/checksum and Render readiness |
| Storage pressure | Drain/ACK/cleanup, allocated size and autovacuum; do not raise caps blindly |

Do not log credentials, request headers, raw provider bodies or personal data. Application errors use
sanitized codes. The supplied Render command disables access logs.
