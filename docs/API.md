# Phase-1 API

Only `/health` is anonymous. Read endpoints require `Authorization: Bearer <DASHBOARD_API_TOKEN>`.
Write endpoints require `Authorization: Bearer <PROCESSOR_TRIGGER_TOKEN>`. Missing token
configuration fails closed with 503; invalid credentials return 401. Tokens must be distinct.
Interactive API docs and public OpenAPI are disabled. All responses use `Cache-Control: no-store`.

| Method | Route | Behaviour |
|---|---|---|
| GET | /health | Database/migration readiness; 503 when unavailable |
| GET | /dashboard/summary | Flags, pause, counts, daily metrics, API usage, DB size, oldest raw |
| GET | /dashboard/source-stats | Checkpoints, source gates/retries, totals and conversion |
| GET | /dashboard/queue | Job/candidate counts and 50 recent jobs |
| GET | /dashboard/failures | Last 100 sanitized failure records |
| GET | /dashboard/candidates | Review sample; `status=REVIEW`, `limit=100` defaults |
| POST | /jobs/collect | Queue source acquisition, no immediate collection |
| POST | /jobs/process | Queue `normalize`, `classify`, or `validate` |
| POST | /jobs/cleanup | Queue a bounded retention pass |
| POST | /jobs/process-next | Request background execution of one durable job; 202 |
| POST | /jobs/tick | Enqueue eligible missing stages and request one bounded execution; 202 |
| GET | /jobs/{job_id} | Durable status, attempt count, cursor and result |
| POST | /admin/pipeline/pause | Pause new acquisition/processing execution |
| POST | /admin/pipeline/resume | Remove DB pause; environment/source gates still apply |
| POST | /admin/sources/{source}/enable | Enable DB source gate and reset its circuit breaker |
| POST | /admin/sources/{source}/disable | Disable DB source gate |
| POST | /admin/jobs/{job_id}/retry | Reset a FAILED job for explicit retry |
| POST | /admin/candidates/{id}/retry | Retry REVIEW or CONTACT_REVIEW through the same qualification gates |
| GET | /exports/validated | CSV (default) or `format=json`, keyset pagination |
| GET | /exports/source-stats | Source-statistics CSV |
| GET | /exports/processing-summary | Summary, queue and failures JSON |

Compatibility aliases: `/metrics/summary`, `/leads/validated`, `/export/validated.csv`,
`POST /jobs/run-batch` (queues the requested processing stage). `/leads/validated?format=json`
returns JSON; the CSV alias is paginated too.

## Enqueue contract

Provide a unique `Idempotency-Key` header (8–160 characters) for `/jobs/collect`,
`/jobs/process`, `/jobs/cleanup`, and `/jobs/run-batch`.

```json
{"source":"bluesky","batch_size":5}
```

For processing:

```json
{"stage":"normalize","batch_size":5}
```

For cleanup use `{}` or a batch size. Reusing a key returns the same job. Reusing a key for
a different operation returns 409. Jobs are queued durably; invoke `/jobs/process-next`
explicitly in Phase 2. Background execution requests themselves are not durable, but queued
jobs survive a web-process crash. A later tick resumes them. A 202 is not completion.

## Export contract

`after=0&limit=1000` starts a page. The response supplies a `through` high-water ID.
Carry that same `through` into every subsequent page. JSON includes `items`, `through`,
and `next_after` (null when complete). CSV exposes `X-Export-Through` and `X-Next-After`
(empty when complete). At most 2000 leads per page. Exports never include raw source records.
CSV cells with formula prefixes are apostrophe escaped for spreadsheet safety.

VALIDATED means the candidate passed intent gates and its public contact has valid syntax
and a mail-accepting domain. `validation_status=DELIVERABLE_DOMAIN` preserves that narrower
DNS result. Neither status proves mailbox existence or permission to send email.
