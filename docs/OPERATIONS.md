# Operational runbook

## Build-phase state

No migrations, tests, source calls, inference, Google writes, trigger installations or deployment
are performed by the build. All runtime flags default false; pipeline and source DB gates
start disabled. There is no sender code. The Python import/build checks do not start the app.

## Scheduling after approval

Choose one scheduler:

1. Apps Script `processorTick` sends `/jobs/tick` periodically. The backend queues missing
   collection/normalize/classify/validate/cleanup work and executes one stage asynchronously.
2. An always-running Render web instance can use `SCHEDULER_ENABLED=true`; it enqueues and
   executes one bounded stage per interval. PostgreSQL advisory locks select a scheduler leader
   and a single executor even with multiple processes.
3. A separately configured Render cron can execute `python -m app.cli tick` with
   `PYTHONPATH=services/render_api`. No cron resource is created by this build.

Do not install a scheduler in Phase 1. Google reporting triggers are independent from processing.
The design serializes external work for initial reliability, while different stages are durably
queued. It is not a distributed parallel worker pool. Small transactions and persisted stages
allow scaling later after measurements. 200,000 raw records in seven days is a target, not a
demonstrated throughput or lead-conversion guarantee.

## Pause, retries and crash recovery

Pause prevents the next stage starting. An already running bounded stage may finish. Cleanup
can still run while paused. Source disable is independent; environment flags are hard gates.
After five source failures the database source gate closes; inspect failures, fix configuration,
then explicitly enable it again. Quota exhaustion preserves pagination and records a retry time.

Each job is an immutable operation plus bounded input size and idempotency key. PostgreSQL
session advisory locking serializes executor ownership. Process loss releases the lock, so
the next executor requeues abandoned RUNNING work. Each processed candidate commits independently;
replay sees its new stage. Collection rows/hashes/cursor/job completion commit atomically.
Transient failures use backoff, and attempts stop at the configured limit. Failed jobs can be
reset through the authenticated retry endpoint.

External HTTP requests are at least once: a crash after xAI responds but before candidate commit
can consume a second call on retry. Persistent reservations count each attempt conservatively.
Job result counts report the last execution attempt; aggregate metrics committed with data are
the more reliable total across crashes. Idempotency keys live through operational job retention;
record/content/email hashes persist longer.

## Retention

- Full upstream API payloads are never stored. Source text is bounded to 16,000 characters.
- Rejected normalized source rows are immediately deleted by default.
- Candidates contain at most 2,000 characters of redacted evidence and five public-text contacts.
- Source rows are removed after candidate reduction by default.
- Otherwise processed/rejected raw expires after three days. Pending raw remains necessary for
  unfinished work and is preserved; MAX_PENDING_RAW (12000) stops further collection when the queue fills.
  Monitor oldest pending raw and resolve stalled processing before resuming acquisition.
- All temporary candidates, including unresolved reviews, expire after 30 days. Classification
  rows cascade. Temporary identity admission is released; permanent record/content hashes remain.
- Jobs/failures and email-validation cache age out after 30 days in bounded cleanup passes.
- Validated leads, metrics, API budgets and durable dedupe/identity hashes are retained. There is
  no automatic deletion of validated leads before backup, and no full raw archive.

## Google backups and recovery

Daily snapshots use the highest committed lead ID at snapshot start. New leads enter the next
day's snapshot. Each CSV part contains at most 1000 leads. Sidecars store file ID, SHA-256,
input/output cursor and snapshot boundary. The script lock prevents concurrent backup writers.
It checkpoints after each part and returns after roughly three minutes of work. The next hourly
trigger resumes an unfinished snapshot, including one from yesterday. It writes a completed
manifest only after lead parts, source stats and processing JSON exist.

Files are create-once by deterministic date/name. Existing parts are checksum checked during
retry, and sidecars prevent page duplication. A missing/conflicting state or file is surfaced,
not silently overwritten. Treat `backup-manifest-<date>.json` as the completion marker.
Dashboard EXPORT_HISTORY records completion/failure and links to that manifest. Google or Render
downtime leaves the cursor intact. Status also stays in Script Properties and execution logs,
even if the Sheet itself cannot be updated. There is no automatic email notification.

This is a **useful-data export backup, not a full PostgreSQL backup**. It protects leads and
statistics; it does not restore pending raw, all dedupe hashes, source checkpoints, or jobs.
Before database expiry/migration, additionally take a private `pg_dump` and verify restoration
in Phase 2 or a later explicitly approved recovery exercise. A fresh database restored only
from lead CSVs loses historical rejected-content dedupe. Keep the original DB until recovery
is verified. Drive backups inherit folder access; do not make the folder public.

Completed lead snapshots accumulate in Drive. This build does not automatically delete backups
or leads; set and review a business retention policy before production. Google Drive is not a
replacement for an operational database with an appropriate lifetime.

## Troubleshooting

| Symptom | Inspect / act |
|---|---|
| 503 health | DATABASE_URL, network access, then migration revision |
| 401 reads/writes | Correct separate token in the caller's secret store |
| 409 enqueue | Pause, environment/YAML/database gates, source retry time, key conflict |
| Jobs remain queued | Explicit executor or enabled scheduler; inspect source circuit breaker |
| REVIEW candidates | Grok flag/model/key/cap, or local artifact availability; retry after correction |
| CONTACT_REVIEW | DNS transient errors, role/disposable domains, or duplicate contact |
| No contacts | These collectors inspect public post/comment text only; no hidden emails or enrichment guesses |
| YouTube stops | Both local budgets, provider quota, comments disabled, stale page token, configured targets |
| BACKUP failed | Script Properties status, folder permissions, Render readiness; rerun same backup |

Avoid logging request headers, raw provider bodies, raw exception strings or personal data.
The application stores error codes and IDs only. Access logs are disabled in the supplied Render command.
