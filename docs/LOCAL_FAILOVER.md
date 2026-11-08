# Local-device failover

An explicit-start watchdog that lets the operator's Windows device keep the existing PostgreSQL
work queue moving when Render processing or its scheduler is unavailable. It is a fallback, not a
second pipeline: it runs the **same** durable jobs through the same executor lock, source cursors,
idempotency keys, campaign controls and dedupe hashes.

Nothing starts automatically. There is no scheduled task, startup entry or service. The device
can only help while it has **power, connectivity and access to the configured database**, including
an entry for its current public IP on the Render PostgreSQL allow list. A sleeping or offline
device does nothing; the cloud path remains independently usable.

## Commands

From the repository root (Windows PowerShell 5.1 or later):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\failover\Start-LeadgenFailover.ps1 -Command status
powershell -ExecutionPolicy Bypass -File scripts\failover\Start-LeadgenFailover.ps1 -Command once
powershell -ExecutionPolicy Bypass -File scripts\failover\Start-LeadgenFailover.ps1 -Command run
powershell -ExecutionPolicy Bypass -File scripts\failover\Start-LeadgenFailover.ps1 -Command stop
```

| Command | Effect |
|---|---|
| `status` | Observe-only report: Render health, cloud executor heartbeat age, work due, identity problems. Never processes or changes state. |
| `once` | One bounded cycle (assessment, threshold update, and processing only if warranted), then exit. Thresholds persist in `.failover/state.json`, so repeated `once` runs behave like a slow loop. |
| `run` | Loop every `FAILOVER_INTERVAL_SECONDS` until `stop`. A single-instance OS lock prevents two watchdogs. |
| `stop` | Writes `.failover/STOP`; a running watchdog exits after its current bounded job. |

The launcher creates or reuses `.venv`, installs `services/render_api/requirements.txt` only when
that file's hash changes, refuses to run without the ignored `.env`, and sets
`EXECUTOR_ROLE=device`. Without `FAILOVER_ENABLED=true` in `.env` the watchdog is observe-only.

## How it decides

Each cycle:

1. `GET <RENDER_BASE_URL>/health` with a bounded timeout and no redirects.
2. Reads durable evidence: the age of the **cloud** executor heartbeat (`provider_rate_state` row
   `executor_cloud`, written by `jobs.run_next` on Render each time it is ticked) and whether work
   is due. The device writes its own `executor_device` row, so device work never looks like cloud
   recovery.
3. Classifies: `cloud_healthy`, `render_unreachable`, `scheduler_stalled` (healthy HTTP but no cloud
   executor tick for `FAILOVER_STALL_SECONDS`), or `database_unavailable`.
4. Switches to device mode only after `FAILOVER_FAILURE_THRESHOLD` consecutive unhealthy cycles and
   back only after `FAILOVER_RECOVERY_THRESHOLD` consecutive healthy cycles (no flapping).
5. In device mode, with `FAILOVER_ENABLED=true` and verified identity, runs `jobs.schedule_tick`
   then at most `FAILOVER_MAX_JOBS_PER_CYCLE` bounded `jobs.run_next` calls. It stops early when
   nothing is eligible or another executor (for example recovered Render) holds the lock.

Identity verification before any processing: executor role is `device`; environment is
`test`/`staging`/`production`; database migration equals the repository head; `pipeline_state`
exists; the persisted campaign ID (if any) equals `CAMPAIGN_ID`; and, if set,
`FAILOVER_EXPECTED_DATABASE` equals `current_database()`.

## Failure behaviour

| Failure | Response |
|---|---|
| Vercel/Netlify unavailable | Not a device concern: Render's router already falls back to local processing |
| Render processing or scheduler unavailable | After the failure threshold, the device processes the same PostgreSQL queue |
| Groq allowance exhausted | Semantic candidates are deferred with priority intact; deterministic stages and direct finals continue. No local classifier substitutes for semantic review unless a trained, hash-pinned model is configured |
| PostgreSQL unavailable | Watchdog pauses: no processing, no mode change, never a competing database |
| Google unavailable | Finals stay in PostgreSQL; backpressure applies; nothing is deleted without ACK |
| Device asleep/offline | No fallback; cloud remains independently usable |

Apps Script cannot reach `localhost`. While Render is down, pending finals wait in PostgreSQL until
an authenticated delivery path (Render) recovers. The device never claims Google delivery.

Execution gates (acquisition, processing, sources, Groq) are read from the device's `.env`. The
watchdog enables none of them; keep them consistent with Render when you intend the device to
continue acquisition.

## Requirements the identity check enforces

- `DATABASE_URL` in the device's `.env` must be the **external** Render URL (a fully qualified host)
  with `sslmode=require` (or stricter). Render's internal hostname only works inside Render; the
  check refuses it (`database_url_internal_host`, `database_tls_not_required`). The URL is never
  printed or logged.
- The device's current public IP must be on the database's IP allow list.
- Cloud and device share one PostgreSQL advisory executor lock, one job table and the same source
  cursors. While Render holds the lock the device gets `busy` and does nothing. If Render's
  connection drops mid-job, the lock is released and the next executor reclaims the abandoned
  job exactly once (tested). A failed health check alone never runs work twice.

## Outage versus quota

A cloud outage (Render unreachable or not ticking) is what the watchdog reacts to. Provider quota
exhaustion is not an outage: Render keeps ticking, so the watchdog stays in cloud mode. Running on
the device does **not** reset Groq, YouTube, Bluesky, Google or Render limits; those counters live
in the shared database and at the providers. Semantic work deferred for quota stays deferred.

No local ML model is configured. A model may only be used after it earns its confidence
threshold on reviewed examples and is hash-pinned (`LOCAL_MODEL_PATH`, `LOCAL_MODEL_SHA256`);
until then ambiguous records wait for semantic capacity.

## Exact commands

```powershell
# Setup (once): .env must hold the external DATABASE_URL with sslmode=require and RENDER_BASE_URL.
powershell -ExecutionPolicy Bypass -File scripts\failover\Start-LeadgenFailover.ps1 -Command status

# Dry run: with FAILOVER_ENABLED unset/false, one cycle only observes and reports what it would do.
powershell -ExecutionPolicy Bypass -File scripts\failover\Start-LeadgenFailover.ps1 -Command once

# Start (set FAILOVER_ENABLED=true in .env first), status from another window, and stop.
powershell -ExecutionPolicy Bypass -File scripts\failover\Start-LeadgenFailover.ps1 -Command run
powershell -ExecutionPolicy Bypass -File scripts\failover\Start-LeadgenFailover.ps1 -Command status
powershell -ExecutionPolicy Bypass -File scripts\failover\Start-LeadgenFailover.ps1 -Command stop
```

Recovery after a crash or reboot: just start again. Watchdog thresholds persist in
`.failover/state.json`; job progress, cursors and dedupe live in PostgreSQL. A stale `STOP` file is
cleared on start.

## What happens when

| Event | Result |
|---|---|
| Laptop sleeps or goes offline | Device fallback stops; cloud continues independently if healthy. On wake, `run` resumes from durable state |
| PostgreSQL expires or is replaced | Everything pauses (no competing database). Before acquisition resumes on a fresh database, migrate it and restore dedupe hashes from the delivered Google data (below) |
| Google stays unavailable | Finals stay in PostgreSQL; nothing is deleted without ACK; once queued work reaches the high-water mark, acquisition stops until delivery recovers |

## Recovering a fresh or replaced database

Before any delivery or acquisition on a new database, recover from the finished Google dataset.
Inputs: the verified Drive delivery files (`lead-delivery-*.json`, downloaded into one folder) and a
JSON file of each `VALIDATED_*` tab's data-row count from the `SHARD_INDEX` tab
(e.g. `{"VALIDATED_A_001": 812, "VALIDATED_001": 6}`).

```powershell
cd services\render_api
..\..\.venv\Scripts\python -m alembic -c alembic.ini upgrade head
..\..\.venv\Scripts\python -m app.cli recover --deliveries C:\path\deliveries --tab-rows C:\path\tab_rows.json
..\..\.venv\Scripts\python -m app.cli recover --deliveries C:\path\deliveries --tab-rows C:\path\tab_rows.json --apply
```

The dry run validates every file's checksum and requires every row `2..n+1` of every tab to be
covered exactly once. Empty, partial, tampered or conflicting inputs are refused. The delivery JSON
is lossless (no CSV formula escaping), so a value like `+alice@...` hashes exactly as delivered.
`--apply` (fresh database only) bulk-inserts the `email:`, `record:` and Bluesky `lead:` hashes in
1,000-key chunks, restores the per-letter and legacy row cursors so the next claim continues after
the last occupied Google row, binds and verifies the destination, then re-reads everything and fails
unless it all matches. Until recovery (or `confirm-empty-destination` for a brand-new empty Sheet),
a fresh database refuses delivery claims (`destination_unverified`). Labelled `SYNTHETIC_TEST` rows
keep their positions but create no hashes. YouTube identities aren't in the delivered URL, so YouTube
people are protected by comment and email hashes only.

## Tested guarantees

Database guarantees (covered by tests against PostgreSQL):

- One executor at a time through the advisory lock; a second executor gets `busy`.
- Fencing: every executor run increments `executor_fence.epoch`. All database transactions of that
  run verify the epoch under a shared lock. After a takeover, a still-running stale executor cannot
  commit anything, including job status (tested with both workers live and the old lock connection
  terminated server-side). Takeover waits for an in-flight stale transaction to finish.
- An abandoned job is reclaimed and completed by the new owner; its records are processed once.
- Database errors anywhere in a watchdog cycle pause that cycle and retry; the watchdog keeps running.

Not guaranteed: exactly-once external API execution. A stale worker's HTTP request already in
flight (Groq, Bluesky, YouTube, Netlify) can still complete after takeover; its result is discarded
by fencing, but the provider call (and any quota it used) has happened.
