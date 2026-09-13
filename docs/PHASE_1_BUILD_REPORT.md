# Phase-1 build report — 2026-09-13

Phase 1 is implemented in the repository. Runtime correctness and platform access are **not yet
verified**: those are Phase-2 work. No production services were deployed, migrations applied,
sources queried, paid inference performed, Google files changed, triggers installed or email sent.

## 1. Implemented solution

Python FastAPI/PostgreSQL acquisition and qualification engine; independent Bluesky/YouTube
collectors; configuration-driven rules; local-model framework; strict Grok classifier; deterministic
public-contact validation; durable jobs, checkpoint/retry/dedupe controls; retention; authenticated
dashboard/export APIs; Apps Script dashboard, controls and resumable daily Drive backups.

## 2. Repository structure

`services/render_api/app` contains API, collectors, classifiers, pipeline/reporting services,
models, database/repositories, qualification, validators, jobs and CLI. `migrations` contains
Alembic and immutable SQL. `tests` contains deferred unit/integration tests. `config` holds four
editable YAML files; `ml` has training/evaluation tooling; `apps-script` has four script modules and
manifest; root `render.yaml` defines the safe service. README and docs describe setup/operations.

## 3. Tables and migrations

Revision `0001_phase1` creates source_cursors, pipeline_state, processing_jobs, source_records,
dedupe_index, identity_index, candidates, classification_results, email_validation, validated_leads,
pipeline_metrics, api_usage and failed_jobs. Includes unique hash/idempotency constraints, FKs,
timestamps and query indexes. Migrations are explicit and never run on application startup.

## 4. API

Health; dashboard summary/source-stats/queue/failures/candidates; collect/process/cleanup enqueue;
bounded process-next/tick; job status; pipeline pause/resume; independent source controls;
job/candidate retries; paginated validated CSV/JSON, source CSV and processing JSON exports.
Compatibility aliases are retained. Full contracts are in [API.md](API.md).

## 5. Collectors

Bluesky uses bounded public Jetstream WebSocket windows, configured collection/keywords,
microsecond checkpoint/replay overlap and reconnect bounds. YouTube implements configured video,
channel and search discovery; top-level comment pagination; refresh/checkpoints; request bounds;
persistent search/unit caps and graceful quota stops. Both preserve source URLs and participate
in atomic checkpoint/idempotent ingestion.

## 6. Qualification

Normalization, keyword/product/negative/recency/geography filtering, configurable scoring,
record/content/identity/email hashes, optional local TF-IDF/logistic classification, selective Grok,
public-text contact discovery, syntax/disposable/role/DNS/MX validation and unique lead storage.
Unresolved AI/contact cases remain retryable/reviewable. Raw text is reduced/deleted progressively.
No SMTP probing, guessed contacts, private-profile lookup or outbound is implemented.

## 7. Grok

Configured model/key, explicit off-by-default gate, strict JSON Schema/Pydantic result,
PII-reduced bounded input, retry/backoff, persistent daily attempt cap, token accounting and
operator-configured cost estimate. Classification jobs process bounded batches sequentially.
Absent/bad local models and unavailable Grok degrade to rules/review, never automatic approval.

## 8. Apps Script

HTTP client with separate read/operator tokens, bounded retries and sanitized status; values-only
SUMMARY, SOURCE_STATS, VALIDATED, FAILED, PROCESSING and EXPORT_HISTORY tabs. Sample is capped
at 500 leads. Manual controls and explicitly installed, gated reporting/processing triggers exist.
All Google authorization and project installation remain for the next phase.

## 9. Drive backups

GOOGLE_DRIVE_BACKUP_FOLDER_ID selects the folder. Lead snapshots freeze a high-water ID and use
1000-row CSV parts, deterministic daily names, checksum sidecars, persisted page/stage state,
script locking and completion manifests. Source statistics and processing/failure reports are
included. Retries resume without intentionally duplicating daily files; failures surface in status,
Sheet and logs. No raw source-record exports exist. These are useful-data exports, not a full
PostgreSQL/state backup; full recovery needs a separate private database backup.

## 10. Environment values used

The supplied legacy `env.example` contained the existing platform details. They were preserved
in ignored `.env`; both tracked example files now contain empty placeholders. Existing external
database URL was mapped to local DATABASE_URL. Git repository metadata was reused. Missing
read/operator tokens were generated privately in `.env`. No secret values are in this report.

Present locally: database connection, Render API metadata/key, GitHub metadata, xAI key/model,
YouTube key, Bluesky host/collection, Sheet URL/ID, Drive folder ID, Apps Script project ID,
read/operator tokens, batch/retention/volume and disabled acquisition/processing settings.
All supported variables and defaults are documented in [CONFIGURATION.md](CONFIGURATION.md).

## 11. Missing configuration

- RENDER_BASE_URL: obtain from the deployed service; needed by Apps Script.
- LOCAL_MODEL_PATH and LOCAL_MODEL_SHA256: no trained model yet; requires labelled examples.
- GROK_INPUT_USD_PER_MILLION and GROK_OUTPUT_USD_PER_MILLION: supply current model pricing;
  tokens are counted regardless, and the dashboard marks monetary cost unconfigured.
- Approved YouTube video/channel IDs are not supplied in the source YAML. Configurable search
  queries exist; choose one approved known video for the first tiny quota-controlled test.
- TEST_DATABASE_URL and isolated Google test destinations must be supplied in Phase 2.

These do not prevent the code build. Account access, current model availability, host reachability
and actual quota limits remain unverified because no live requests were made.

## 12. Manual platform configuration

Rotate credentials previously present in the tracked legacy example; sanitizing current files
does not erase Git history. Set Render's internal DATABASE_URL and matched tokens; apply migrations
first to a disposable DB; deploy only after the appropriate test gate. Upload Apps Script files
to the existing project, set Script Properties and authorize the Google account. Configure the
appropriate test Sheet/folder. Explicitly install reporting triggers only after testing; processing
schedules stay off until later approval. See [PLATFORM_SETUP.md](../PLATFORM_SETUP.md).

## 13. Known risks and limits

- No runtime/integration tests have run. Build completion is not a production-readiness claim.
- Free-service lifetime, uptime and resources may not sustain the target; throughput is unmeasured.
- Public social/comment text may yield few usable emails; no mailbox deliverability guarantee exists.
- Conservative rules/geography/identity dedupe may lose leads; labelled calibration is needed.
- The disposable-domain list is a starter list, and the local model is not trained.
- Jetstream replay is finite; YouTube discovery/quota bounds intentionally limit coverage.
- Grok requests are at least once across crashes; conservative persistent attempt accounting applies.
- Pending raw is protected for unfinished work, with collection stopped at the 12000-row backlog cap.
- Useful CSV backups do not preserve all operational state; Drive copies/validated leads need a
  reviewed retention policy and full DB backups for complete recovery.
- No test-suite execution, live migration, tiny scrape, inference sample, or Google verification
  was performed during this build, in accordance with the phase boundary.

Static checks performed: Python parsing/formatting/Ruff lint, dependency installation, import and
OpenAPI schema construction without startup, YAML/JSON parsing, and JavaScript syntax checking.
The Render Blueprint also passed validation against Render's official JSON schema. A local
credential-value scan found no supplied secrets in tracked/trackable source; `.env` and `.venv`
are ignored. No Git commit or push was performed.

## 14. Exact next phase

Follow [PHASE_2_TESTING.md](PHASE_2_TESTING.md): isolated configuration; offline unit tests;
disposable PostgreSQL migrations/integration tests; API auth/flags; separate 1–5-record source
checks; at most 1–2 Grok classifications; test Sheet updates; repeat/resume Drive exports;
dedupe, retention, crash and checkpoint verification. Fix failures and stop for Phase-3 approval.

Phase 3 runs one small real source → processing → PostgreSQL → Sheet → Drive flow.
Phase 4 begins the one-week acquisition only after that succeeds and the user explicitly starts it.
No outbound email or MWEB work is authorized by any of these data-phase instructions.
