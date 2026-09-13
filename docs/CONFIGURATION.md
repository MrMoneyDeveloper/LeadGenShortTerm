# Configuration reference

Copy `.env.example` to `.env` only if `.env` does not already exist. Existing local values were
preserved during the build. Process environment overrides `.env`; empty values use code defaults.
The application reads `.env` at repository root, regardless of working directory. It never reads `env.example`.
Both example files intentionally contain empty placeholders only.

## Backend settings

| Variable | Default / use |
|---|---|
| ENVIRONMENT | development; set `test` for Phase 2 isolated execution |
| DATABASE_URL | Required for startup/migrations; PostgreSQL URL; Render internal URL in Render, external URL locally |
| PROCESSOR_TRIGGER_TOKEN | Required for writes, scheduling and controls; independent secret, at least 32 characters |
| DASHBOARD_API_TOKEN | Required for read/export endpoints; independent secret, at least 32 characters |
| ACQUISITION_ENABLED | false; master acquisition permission |
| PROCESSING_ENABLED | false; normalization, classification and validation permission |
| BLUESKY_ENABLED / YOUTUBE_ENABLED | false; source environment gates |
| GROK_ENABLED | false; paid inference gate |
| SCHEDULER_ENABLED | false; optional in-process periodic execution |
| PROCESSING_BATCH_SIZE | 600, bounded 1–2000 |
| RAW_DAILY_TARGET | 30000; UTC aggregate scanned-record cap |
| MAX_PENDING_RAW | 12000; stop collecting when unfinished raw reaches this backlog cap |
| RAW_RETENTION_DAYS | 3; processed/rejected source-record expiry |
| CANDIDATE_RETENTION_DAYS | 30; temporary candidate/evidence lifetime, including unresolved review |
| OPERATIONAL_RETENTION_DAYS | 30; completed/failed job records, failures, validation cache |
| DELETE_REJECTED_IMMEDIATELY | true; delete rejected raw on processing |
| DELETE_REJECTED_RAW | Legacy alias, used only if DELETE_REJECTED_IMMEDIATELY is absent |
| DELETE_PROCESSED_RAW | true; discard source text once the reduced candidate is persisted |
| BLUESKY_JETSTREAM_HOST | jetstream2.us-east.bsky.network; hostname or wss hostname only |
| BLUESKY_COLLECTION | app.bsky.feed.post |
| YOUTUBE_API_KEY | Required only for enabled YouTube collector |
| YOUTUBE_DAILY_UNIT_CAP | 9000; conservative local unit budget, Pacific-date accounting |
| YOUTUBE_DAILY_SEARCH_CAP | 80; separate daily search-call budget |
| XAI_API_KEY / XAI_MODEL | Required only for enabled Grok; model intentionally has no assumed default |
| GROK_DAILY_CANDIDATE_CAP | 100; conservatively counts HTTP attempts, including retries, not just successful candidates |
| GROK_INPUT_USD_PER_MILLION | 0; operator supplies current input pricing; zero means cost unconfigured |
| GROK_OUTPUT_USD_PER_MILLION | 0; operator supplies current output pricing |
| LOCAL_MODEL_PATH | Empty; reviewed local joblib artifact |
| LOCAL_MODEL_SHA256 | Empty; mandatory digest to load the artifact |
| CONFIG_DIR | Repository `config` directory; override with an absolute path if needed |
| JOB_SECONDS | 45; stage time budget checked between items; individual bounded HTTP/DNS calls may finish after it |
| JOB_MAX_ATTEMPTS | 5; failed jobs/source circuit breaker and candidate retry threshold |
| SCHEDULER_INTERVAL_SECONDS | 60; optional internal tick interval |

The database additionally starts paused, and each source starts disabled. Acquisition needs
the master flag, source environment flag, `config/sources.yaml` source flag, database source
flag, and resumed pipeline. Resuming alone does not enable acquisition. Cleanup is permitted
while paused; it never deletes validated leads.

YAML files are cached until process restart. Sources use keyword/scoring configuration; adjust
thresholds and search targets before enabling. The starter disposable-domain list is intentionally
small and should be expanded. `require_south_africa` defaults true for qualification.

## Google Script Properties

| Property | Purpose |
|---|---|
| RENDER_BASE_URL | HTTPS backend origin, without a path |
| DASHBOARD_API_TOKEN | Same read token as backend |
| PROCESSOR_TRIGGER_TOKEN | Backend operator token, needed only for controls or processing scheduler |
| GOOGLE_SPREADSHEET_ID | Existing dashboard spreadsheet ID |
| GOOGLE_DRIVE_BACKUP_FOLDER_ID | Existing destination folder ID, not its URL |
| BACKUPS_ENABLED | Explicit `true` permits the installed hourly backup trigger |
| DASHBOARD_ENABLED | Explicit `true` permits the installed dashboard trigger |
| PROCESSOR_SCHEDULE_ENABLED | Explicit `true` permits installing/running processing trigger |

Properties not set to `true` remain off. Manual dashboard/backup functions can run explicitly
without enabling scheduled operations. `BACKUP_STATE` and `STATUS_*` are managed by the script;
do not edit them during a backup. Script editors can access its tokens, so restrict editors.

`GOOGLE_SHEET_URL`, `APPS_SCRIPT_SCRIPT_ID`, `GITHUB_REPOSITORY_URL`, `GITHUB_DEFAULT_BRANCH`,
`RENDER_API_BASE_URL`, `RENDER_API_KEY`, `RENDER_WORKSPACE_ID` are operator/deployment metadata.
The processor does not need Render management credentials or Google credentials. Existing
legacy DATABASE_URL_EXTERNAL was mapped into the ignored local DATABASE_URL during the build;
deployment must set DATABASE_URL explicitly. No MWEB settings are consumed.
