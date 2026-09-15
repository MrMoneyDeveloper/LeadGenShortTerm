# Configuration reference

Copy `.env.example` to `.env` only if `.env` does not already exist. Process environment overrides
`.env`; empty values use code defaults. The backend reads the repository-root `.env` regardless of
working directory. Both example files contain placeholders only. Real secrets belong in ignored
local files or platform secret stores.

## Backend settings

| Variable | Default / use |
|---|---|
| `ENVIRONMENT` | `development`; use `test` for isolated Phase 2 and `production` only after approval |
| `DATABASE_URL` | Required PostgreSQL URL; Render internal URL in same-region deployment, external TLS URL for local/test access |
| `PROCESSOR_TRIGGER_TOKEN` | Operator/write bearer secret, minimum 32 characters |
| `DASHBOARD_API_TOKEN` | Independent read bearer secret, minimum 32 characters |
| `ACQUISITION_ENABLED` | `false`; master source-collection gate |
| `PROCESSING_ENABLED` | `false`; normalize/validate/classify gate |
| `BLUESKY_ENABLED` / `YOUTUBE_ENABLED` | `false`; source environment gates |
| `SCHEDULER_ENABLED` | `false`; optional Render-local scheduler. Keep false when Cloudflare owns orchestration |
| `PROCESSING_BATCH_SIZE` | `600`, bounded 1–2000 |
| `RAW_DAILY_TARGET` | `30000`; per-UTC-day scanned-record ceiling |
| `MAX_PENDING_RAW` | `12000`; secondary unfinished-raw cap |
| `QUEUE_HIGH_WATER` / `QUEUE_LOW_WATER` | `1800` / `400`; hysteresis for collect-versus-drain mode |
| `DATABASE_HIGH_WATER_BYTES` / `DATABASE_LOW_WATER_BYTES` | `750000000` / `600000000`; storage-pressure hysteresis |
| `JOB_SECONDS` | `45`; bounded stage wall-time target checked between records |
| `JOB_MAX_ATTEMPTS` | `5`; job/source retry ceiling |
| `SCHEDULER_INTERVAL_SECONDS` | `60`; Render-local scheduler interval when explicitly enabled |

The database starts paused and both source rows start disabled. Collection needs the master
acquisition flag, the source environment flag, the matching `config/sources.yaml` flag, the database
source flag and a resumed pipeline. In `production`, collection additionally requires a persisted
campaign in `RUNNING` state.

## Campaign controller

`CAMPAIGN_SEMANTIC_DRAIN_HOURS` defaults to 24 (range 0–168). Migration
`0005_semantic_drain_deadline` persists a fixed semantic-work deadline when a
draining campaign is first observed. Restarts and subsequent progress do not extend
it. Ranked ambiguous candidates can use available quota until then; afterward,
bounded cleanup retires that unfinished semantic work. Raw records, contact-validation
work and final leads awaiting verified delivery remain protected. The campaign still
waits for those protected stages to finish; this is not a forced delivery timeout.

| Variable | Default / use |
|---|---|
| `CAMPAIGN_ID` | `phase2`; stable ID placed in final delivery rows |
| `CAMPAIGN_DURATION_DAYS` | `7`; duration applied when the campaign start endpoint is called |
| `CAMPAIGN_RAW_TARGET` | `200000`; approximate raw scan target for one campaign |
| `CAMPAIGN_DEADLINE` | Legacy/manual safety ceiling only; new campaigns persist their own calculated deadline |

`POST /admin/campaign/start` records the actual start time and computes the deadline from that moment.
The persisted lifecycle is `READY -> RUNNING -> DRAINING -> FINALIZING -> COMPLETE`. Hitting either
the raw target or deadline stops new acquisition but does not discard downstream work. The campaign
only reaches `COMPLETE` after transient work and claimed deliveries have drained.

A new campaign refuses to start while transient source/candidate/lead rows or an unacknowledged
Google delivery batch remain. This prevents one run from inheriting another run's working payloads.

## GroqCloud semantic provider

### September 15 rate-control update

Migration `0004_provider_rate_state` is required before running the updated client.
`GROQ_PLAN` defaults to `free`. New limits default to `GROQ_RPM_LIMIT=30`,
`GROQ_RPD_LIMIT=1000`, `GROQ_TPM_LIMIT=8000`, `GROQ_TPD_LIMIT=200000`,
`GROQ_RPM_SOFT_CAP=24`, and `GROQ_TPM_SOFT_CAP=6500`.
The effective limits also respect smaller provider headers when supplied.

The client reserves a conservative UTF-8 prompt estimate plus framing allowance and
the full completion ceiling before each attempt. Reservations are not refunded,
including failed/ambiguous calls; actual returned input/output usage is tracked separately.
This is deliberately conservative and needs a live throughput benchmark before tuning.
Rolling 60-second reservations, daily reservations, response headers, 429 counts,
deferral counts and cooldowns persist in `provider_rate_state`. Daily counters use UTC;
provider cooldowns and reset timestamps survive midnight and restarts.

Quota waits preserve candidate attempts and set the durable candidate availability time.
Oversized prompts take the bounded failure/review path instead of deferring forever.
Single-record inference remains the default; batching requires a measured benefit.
Free-plan dashboard usage reports `actual_pipeline_cost=0`; historical list-price
arithmetic is exposed only as `reference_list_price`. Other platform costs are not included.

The daily token admission description below predates this update: admission now uses
reserved upper-bound estimates, rather than waiting for actual usage to cross the cap.

GroqCloud is the default semantic provider and is intentionally separate from xAI/Grok.

| Variable | Default / use |
|---|---|
| `SEMANTIC_PROVIDER` | `groq`; optional legacy value `xai` |
| `GROQ_ENABLED` | `false`; GroqCloud inference gate |
| `GROQ_API_KEY` | GroqCloud `gsk_...` credential; secret |
| `GROQ_MODEL` | No implicit model; configure the tested GroqCloud model explicitly |
| `GROQ_DAILY_REQUEST_SOFT_CAP` | `800`; conservative persisted request/attempt budget |
| `GROQ_DAILY_TOKEN_SOFT_CAP` | `150000`; stop new Groq requests once accounted daily tokens reach this value |
| `GROQ_INPUT_USD_PER_MILLION` / `GROQ_OUTPUT_USD_PER_MILLION` | `0`; optional accounting metadata, not a requirement to use a paid tier |
| `SEMANTIC_MAX_ATTEMPTS` | `2`; bounded retry count inside one semantic call |
| `SEMANTIC_TIMEOUT_SECONDS` | `20`; request timeout |

`GROQ_DAILY_CANDIDATE_CAP` remains accepted as a legacy alias for
`GROQ_DAILY_REQUEST_SOFT_CAP`. The application reserves each request attempt before the HTTP call,
then records returned input/output tokens. A soft token limit can overshoot by at most one already
admitted request because exact token use is only known after the response.

Legacy xAI/Grok support remains explicit and disabled by default through `GROK_ENABLED`,
`XAI_API_KEY`, `XAI_MODEL`, `GROK_DAILY_CANDIDATE_CAP`, `GROK_INPUT_USD_PER_MILLION` and
`GROK_OUTPUT_USD_PER_MILLION`. These variables do not configure GroqCloud.

## Sources and retention

| Variable | Default / use |
|---|---|
| `BLUESKY_JETSTREAM_HOST` | `jetstream2.us-east.bsky.network` |
| `BLUESKY_COLLECTION` | `app.bsky.feed.post` |
| `YOUTUBE_API_KEY` | Required only when YouTube is enabled |
| `YOUTUBE_DAILY_UNIT_CAP` | `9000`; local YouTube-unit guard |
| `YOUTUBE_DAILY_SEARCH_CAP` | `80`; local search-call guard |
| `RAW_RETENTION_DAYS` | `3` |
| `CANDIDATE_RETENTION_DAYS` | `30` |
| `TERMINAL_CANDIDATE_RETENTION_HOURS` | `24`; short-lived review/deferred payloads under normal operation |
| `OPERATIONAL_RETENTION_DAYS` | `30`; jobs/failures/validation cache |
| `DELETE_REJECTED_IMMEDIATELY` | `true` |
| `DELETE_REJECTED_RAW` | Legacy alias when the preferred name is absent |
| `DELETE_PROCESSED_RAW` | `true` |
| `LOCAL_MODEL_PATH` / `LOCAL_MODEL_SHA256` | Reviewed TF-IDF/logistic-regression artifact and required hash |
| `CONFIG_DIR` | Repository `config` directory unless explicitly overridden |

Short-term-insurance terms and ranking are editable in `config/keywords.yaml` and
`config/scoring.yaml`. South African evidence is a positive signal rather than an automatic hard
requirement; strong otherwise-valid intent may continue without an explicit location phrase.

## Final Google delivery

| Variable | Default / use |
|---|---|
| `FINAL_DELIVERY_ENABLED` | `false`; backend claim gate |
| `GOOGLE_SPREADSHEET_ID` | Existing final-output workbook ID |
| `GOOGLE_DRIVE_BACKUP_FOLDER_ID` | Existing destination folder ID, not its URL |
| `EXPORT_BATCH_SIZE` | `100`, maximum 200 final rows per claim |
| `SHEET_SHARD_ROWS` | `5000`; data rows per `VALIDATED_###` tab, excluding header |

The backend assigns immutable final row positions. Google readback + Drive verification + ACK must
succeed before the corresponding heavy PostgreSQL lead/candidate payload is deleted.

### Apps Script properties

| Property | Purpose |
|---|---|
| `RENDER_BASE_URL` | Exact HTTPS backend origin |
| `DASHBOARD_API_TOKEN` | Same read token as backend |
| `PROCESSOR_TRIGGER_TOKEN` | Same operator token as backend for claim/ACK and manual controls |
| `GOOGLE_SPREADSHEET_ID` | Must match backend final destination |
| `GOOGLE_DRIVE_BACKUP_FOLDER_ID` | Must match backend final destination |
| `DELIVERY_ENABLED` | Exactly `true` permits `deliveryTick` / delivery-trigger installation |
| `BACKUPS_ENABLED` | Exactly `true` permits scheduled backup continuation |
| `DASHBOARD_ENABLED` | Exactly `true` permits scheduled dashboard refresh |
| `PROCESSOR_SCHEDULE_ENABLED` | Keep false when Cloudflare is the acquisition/processing scheduler |

`installDeliveryTrigger()` creates a five-minute bounded final-delivery trigger only when
`DELIVERY_ENABLED=true`. `deliverFinalLeads()` itself handles at most 200 final leads per invocation.
`installReportingTriggers()` manages dashboard and backup triggers separately. No trigger is installed
merely by uploading the Apps Script project.

## Optional Cloudflare coordinator

Cloudflare performs orchestration only. It does not store/process lead payloads. Deployment metadata
may include `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN`; Worker secrets/settings include
`COORDINATOR_TRIGGER_TOKEN`, `COORDINATOR_ENABLED`, `COORDINATOR_ALLOW_PRODUCTION`,
`SCHEDULER_OWNER`, `RENDER_BASE_URL`, `DASHBOARD_API_TOKEN` and `PROCESSOR_TRIGGER_TOKEN`.

When Cloudflare owns orchestration, set `SCHEDULER_OWNER=cloudflare`, keep Render
`SCHEDULER_ENABLED=false`, and keep Apps Script `PROCESSOR_SCHEDULE_ENABLED=false`. Google delivery,
backup and dashboard schedules are independent output-side jobs.

`GOOGLE_SHEET_URL`, `APPS_SCRIPT_SCRIPT_ID`, `GITHUB_REPOSITORY_URL`, `GITHUB_DEFAULT_BRANCH`,
`RENDER_API_BASE_URL`, `RENDER_API_KEY`, `RENDER_WORKSPACE_ID`, `CLOUDFLARE_ACCOUNT_ID` and
`CLOUDFLARE_API_TOKEN` are operator/deployment metadata; the runtime processor does not need platform
management credentials after deployment.
