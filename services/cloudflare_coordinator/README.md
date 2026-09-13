# Optional Cloudflare coordinator

This Worker distributes orchestration, not lead processing. Render owns collection, profiles,
validation, ranking, inference, queues and retention. PostgreSQL owns durable job/cursor state;
Apps Script delivers finished leads to Sheet shards and Drive, then acknowledges them.
The Worker has no source adapters, raw-data storage, Google tokens or inference keys.

The checked-in configuration is disabled, has **no cron triggers**, and exposes no workers.dev
or preview route. No Cloudflare deployment or account action is part of this change.

## One bounded invocation

1. `GET <RENDER_BASE_URL>/health` wakes the configured Render test service. If the cold start
   exceeds ten seconds, return `wake_pending` and stop. A later authorized tick can try again.
2. `GET /dashboard/backpressure` uses the dashboard bearer token to read current queue mode.
3. Except while paused, `POST /jobs/tick` requests one bounded executor pass using the operator
   bearer token. Render reevaluates flags, backlog, storage pressure and campaign deadline.
   `collect` permits acquisition within Render's caps; `drain` stops acquisition; `storage_pressure`
   permits cleanup only; `campaign_complete` stops acquisition and drains existing work.

An invocation makes at most three outbound HTTP requests, each with a ten-second timeout and
no automatic retries or redirect following. Only the queue response is read, capped at 16 KiB.
It sends a five-minute `Idempotency-Key`; Render's own stage/time-bucket deduplication and
PostgreSQL executor lock remain authoritative. A POST timeout is reported as `tick_unconfirmed`
because work might already have been accepted. The coordinator never retries within that invocation.

`export_required: true` surfaces finished leads awaiting Google delivery. Apps Script must pull,
write, back up and acknowledge those records. This Worker cannot force a Google export and will
not release export backpressure itself. If Google is unavailable, acquisition remains limited
by Render's queue controls until the export queue drains.

## Configuration

Use the explicitly selected Cloudflare account and the intended environment's Render URL;
do not infer either from a previously connected platform session.

| Variable | Required value / purpose |
|---|---|
| ENVIRONMENT | `test`, or `production` only after a separately approved production phase |
| COORDINATOR_ENABLED | Exactly `true` to allow outbound calls; defaults disabled |
| COORDINATOR_ALLOW_PRODUCTION | Additional explicit `true` gate for production; defaults disabled |
| SCHEDULER_OWNER | Exactly `cloudflare` to allow outbound calls; defaults `none` |
| RENDER_BASE_URL | Exact HTTPS origin, with no credentials, path, query or fragment |
| DASHBOARD_API_TOKEN | Render read token; secret, 32–4096 characters |
| PROCESSOR_TRIGGER_TOKEN | Render job token; secret, 32–4096 characters |
| COORDINATOR_TRIGGER_TOKEN | Separate manual Worker token; secret, 32–4096 characters |

All three tokens belong in Worker secret bindings or ignored local `.dev.vars`, never in
`wrangler.jsonc`. The example local file has empty placeholders. Application bearer values
are never included in returned diagnostics. Manual authorization uses a native constant-time
comparison of fixed-length SHA-256 digests. Redirects are rejected to protect authorization headers.

Select **one scheduler owner** before future activation. When Cloudflare owns orchestration,
keep Render `SCHEDULER_ENABLED=false` and Apps Script processing/collection tick triggers disabled.
Apps Script export/dashboard/backup schedules can be managed separately after explicit approval;
they are output delivery, not acquisition scheduling. Cloudflare does not bypass Render's
`ACQUISITION_ENABLED`, `PROCESSING_ENABLED`, source flags or durable pause controls.

## Endpoints and local verification

`GET /health` returns Worker liveness and its enabled flag without contacting Render.
`POST /tick` requires `Authorization: Bearer <COORDINATOR_TRIGGER_TOKEN>`; it returns an
allowlisted status, request count, queue mode/counts and export-required flag. A request body
cannot select a source, override a queue mode or increase any budget.

Run from this directory with Node 22 or newer:

```powershell
npm test
npm run check
```

Tests use fake HTTP responses only, including cold starts, denied authentication, timeouts,
budget exhaustion boundaries, malformed/oversize status data, secret-free failure diagnostics,
disabled configuration, pressure/deadline modes and scheduled-handler behavior.

For a later authorized local Worker runtime check, install a compatible Wrangler release and
use `wrangler dev --test-scheduled` with test secrets in ignored `.dev.vars`. Its scheduled
test endpoint invokes the same disabled-by-default handler. This command does not install a
production trigger. No deployment script or automatic installation is included.

During eventual approved scheduling, manage `triggers.crons` explicitly. An empty array removes
existing triggers; merely omitting the property does not reliably disable an existing schedule.
See [Cloudflare Cron Triggers](https://developers.cloudflare.com/workers/configuration/cron-triggers/)
and [Wrangler configuration](https://developers.cloudflare.com/workers/wrangler/configuration/).
The compatibility date supports native [Node crypto](https://developers.cloudflare.com/workers/runtime-apis/nodejs/crypto/).
