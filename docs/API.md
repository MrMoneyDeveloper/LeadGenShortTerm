# LeadGen API

Only `/health` is anonymous. Read endpoints require `Authorization: Bearer <DASHBOARD_API_TOKEN>`.
Write/control endpoints require `Authorization: Bearer <PROCESSOR_TRIGGER_TOKEN>`. Missing token
configuration fails closed with 503; invalid credentials return 401. Tokens must be distinct.
Interactive API docs and public OpenAPI are disabled. Responses use `Cache-Control: no-store`.

| Method | Route | Behaviour |
|---|---|---|
| GET | `/health` | Database/migration readiness; 503 when unavailable/unmigrated |
| GET | `/dashboard/summary` | Flags, pause, counts, daily metrics, API usage and DB size |
| GET | `/dashboard/source-stats` | Source checkpoints/gates/retries/totals/conversion |
| GET | `/dashboard/queue` | Job/candidate counts and recent jobs |
| GET | `/dashboard/backpressure` | Queue/storage mode, collect allowance, campaign snapshot and export pressure |
| GET | `/dashboard/campaign` | Persisted campaign lifecycle/counters/deadline |
| GET | `/dashboard/failures` | Last sanitized failure records |
| GET | `/dashboard/candidates` | Bounded review sample |
| POST | `/admin/campaign/start` | Persist actual start time, target and calculated duration/deadline; resume pipeline |
| POST | `/jobs/collect` | Queue bounded source acquisition |
| POST | `/jobs/process` | Queue `normalize`, `classify`, or `validate` |
| POST | `/jobs/cleanup` | Queue bounded retention pass |
| POST | `/jobs/process-next` | Request background execution of one durable job; 202 |
| POST | `/jobs/tick` | Re-evaluate lifecycle/backpressure, enqueue eligible missing stages and run one job; 202 |
| GET | `/jobs/{job_id}` | Durable job status, attempts, cursor and result |
| POST | `/admin/pipeline/pause` | Pause new acquisition/processing execution |
| POST | `/admin/pipeline/resume` | Remove DB pause; environment/source/campaign gates still apply |
| POST | `/admin/sources/{source}/enable` | Enable DB source gate and reset circuit breaker |
| POST | `/admin/sources/{source}/disable` | Disable DB source gate |
| POST | `/admin/jobs/{job_id}/retry` | Reset a failed job |
| POST | `/admin/candidates/{id}/retry` | Retry `REVIEW` / `CONTACT_REVIEW` through normal gates |
| POST | `/exports/claim` | Claim/replay one immutable final Google delivery batch, max 200 rows |
| POST | `/exports/ack` | Verify delivery receipt/placements/checksums, then delete delivered heavy PG payload |
| GET | `/exports/history` | Compact final-delivery receipt history |
| GET | `/exports/validated` | Legacy pending-lead CSV/JSON view; not the final campaign archive |
| GET | `/exports/source-stats` | Source-statistics CSV |
| GET | `/exports/processing-summary` | Summary, queue and failures JSON |

Compatibility aliases remain `/metrics/summary`, `/leads/validated`, `/export/validated.csv` and
`POST /jobs/run-batch`.

## Campaign start contract

The final production clock begins only when the operator explicitly starts a campaign:

```json
{
  "raw_target": 200000,
  "duration_days": 7
}
```

Both fields are optional and default to `CAMPAIGN_RAW_TARGET` / `CAMPAIGN_DURATION_DAYS`. A successful
start stores the actual `started_at` timestamp and calculated `acquisition_deadline`, resets the
campaign raw counter and enters `RUNNING`. Starting is rejected with 409 if another campaign is active
or transient source/candidate/lead data or an unacknowledged delivery batch remains.

The state machine is `READY -> RUNNING -> DRAINING -> FINALIZING -> COMPLETE`. Reaching the raw target
or deadline stops new collection but does not stop downstream work. Production collection additionally
requires the persisted state to be `RUNNING`.

## Enqueue contract

Provide a unique `Idempotency-Key` header (8–160 characters) for explicit `/jobs/collect`,
`/jobs/process`, `/jobs/cleanup`, and `/jobs/run-batch` calls.

Collection example:

```json
{"source":"bluesky","batch_size":5}
```

Processing example:

```json
{"stage":"normalize","batch_size":5}
```

Reusing a key for the same operation returns the existing durable job. Reusing it for a different
operation returns 409. A 202 response means work was accepted/requested, not completed. Queued state
survives a web-process crash and a later tick can reclaim abandoned `RUNNING` jobs.

## Final Google delivery contract

`POST /exports/claim` accepts:

```json
{"limit":200}
```

The backend returns either `{"status":"empty","items":[]}` or one immutable delivery envelope with:

- `batch_id`
- persisted `campaign_id`
- `spreadsheet_id`
- `drive_folder_id`
- `shard_rows`
- ordered `fields`
- assigned `items` (`lead_id`, `tab`, `row`, `values`)
- SHA-256 `checksum`

Until ACK, a repeated claim returns the same outstanding batch. Render, not Apps Script, owns final row
placement. Apps Script writes/read-backs those rows, verifies a Drive JSON copy and submits the exact
receipt to `/exports/ack` including the batch checksum, Drive checksum/file ID and all placements.

ACK fails closed on any destination/checksum/placement mismatch. Only a verified ACK makes the
corresponding final lead/candidate/classification payload deletion-eligible. Identical ACK replay is
safe; conflicting replay is rejected.

## Legacy pending-lead export

`/exports/validated?after=0&limit=1000` still supports keyset pagination for operational/debug use. It
shows leads currently waiting in PostgreSQL, not the complete campaign after ACKed rows have moved to
Google. CSV cells with formula prefixes are apostrophe escaped.

`DELIVERABLE_DOMAIN` means email syntax/domain/MX checks passed. It does not prove mailbox existence or
permission to send marketing email.
