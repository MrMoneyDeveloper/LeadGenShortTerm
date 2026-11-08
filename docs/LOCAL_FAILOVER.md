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
