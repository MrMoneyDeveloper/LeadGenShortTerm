# Phase 2 controlled test report — 2026-09-13

Phase 2 exercised the Phase-1 implementation with isolated local databases, synthetic data,
bounded public-source traffic and private Google test artifacts. No production schedule, outbound
email, MWEB integration, large scrape, production dataset or one-week workflow was started.

The internal application is passing. Phase 2 is not complete because credential rotation and three
credentialed runtime checks remain outstanding. Phase 3 must not begin yet.

## Security audit

- `.env`, `.env.test`, `.clasprc.json`, `.clasp.json` and `credentials.json` are ignored. `.env` is
  not tracked, and supplied secret values were not found in current tracked/trackable source.
- All seven reachable commits were inspected without printing credential values.
- Commit `35cad792bb31` contains values that match the current local Render API key, both Render
  PostgreSQL connection URLs, xAI API key and YouTube API key. Sanitizing the current tree did not
  remove those values from Git history.
- The generated dashboard and processor tokens were not found in reachable history.
- Required rotations: Render API key; Render PostgreSQL user/password (and both URLs); xAI API key;
  YouTube API key. The exposed values were not used for external testing.
- Consider history rewriting only after credential rotation and coordination with every clone. Key
  rotation is the immediate containment action.

## Isolated environment

- All test processes explicitly used `ENVIRONMENT=test` and local test tokens.
- Acquisition, processing, Bluesky, YouTube, Grok and scheduler flags remained false except for
  direct bounded fixture calls that bypassed scheduling. No continuous worker was started.
- PostgreSQL 17.11 ran on loopback port 55433 with separate `leadgen_phase2`, `leadgen_fixture` and
  performance databases. No Render database was mutated.
- Google artifacts were created in the private
  Phase-2 test folder (private resource ID omitted).
  The native test dashboard (private resource ID omitted)
  is separate from configured operational destinations.

## Automated tests

`pytest -q` completed with **33 passed, 0 failed** in 14.87 seconds. Two warnings come from the
Starlette/FastAPI test client compatibility layer: deprecated `httpx` TestClient integration and an
AnyIO `BlockingPortal` alias. They do not affect runtime behavior but should be removed during a
future dependency refresh.

Coverage includes configuration isolation and aliases; ORM/repositories; schemas and API auth;
normalization, keyword boundaries, scoring, recency and South African signals; SHA-256 admission;
source/content/identity/email dedupe; email syntax/disposable/role/DNS/MX fixtures; retention;
collectors and checkpoints; mocked YouTube pagination/quota; strict Grok JSON, redaction,
rate-limit retry and token/cost accounting; export pagination/CSV safety; job feature flags,
idempotency, retries and sanitized failures; Apps Script checksum/checkpoint/create-once logic; and
stage interruption recovery.

The TF-IDF/logistic-regression path was exercised rather than only mocked. It trained all eight
labels from 520 deduplicated controlled examples, produced a stratified 416/104 train/holdout
split, wrote and hash-pinned the artifact, loaded it for inference and refused a checksum mismatch.
This fixture proves the mechanism, not production model quality.

`ruff check .`, `git diff --check` and `alembic check` passed.

## Defects found and fixed

1. `ENVIRONMENT` was absent from validated application settings, so a requested test-mode label
   could be silently ignored. It is now a validated development/test/staging/production setting and
   appears in dashboard status.
2. Apps Script backup checkpoint and pagination transitions were embedded in the live Drive loop.
   Pure validation/transition helpers now enforce snapshot, cursor and forward-progress invariants;
   the Node harness covers resume, terminal pages, immutable state, checksum and create-once behavior.
3. The PostgreSQL test fixture did not create an Alembic revision marker, causing a false unhealthy
   API result. It now creates the marker in each isolated schema.
4. The advisory-lock path initially escaped each test schema through the cached application engine.
   Tests now use a lock connection from the same disposable schema, preventing cross-database work.
5. An API test used a nine-character key named `too-short`; this exercised idempotency conflict
   instead of validation. The fixture now uses a genuinely short key.
6. Retention coverage omitted durable lead/hash/cursor state. The suite now proves cleanup removes
   expired raw/candidate/validation data while preserving leads, identities, permanent dedupe hashes,
   source cursors and pipeline state.
7. Recovery coverage was too generic. Controlled interruptions now verify normalization rollback,
   classification resume and validation resume without duplicate leads.

No unresolved internal application failure remains in the executed suite.

## Database migrations

- A zero-state upgrade applied revision `0001_phase1` cleanly.
- Exactly 13 application tables exist: `api_usage`, `candidates`, `classification_results`,
  `dedupe_index`, `email_validation`, `failed_jobs`, `identity_index`, `pipeline_metrics`,
  `pipeline_state`, `processing_jobs`, `source_cursors`, `source_records` and `validated_leads`.
- PostgreSQL reports 44 indexes including primary/unique indexes and the explicit job claim,
  candidate stage, status, time, source, score and hash indexes.
- A duplicate `(source, source_record_id)` insert and an invalid candidate `record_id` both raised
  integrity errors. Unique constraints and foreign keys are active.
- `alembic downgrade base` on the disposable database removed every application table and left only
  `alembic_version`; reapplying `head` restored all 13 tables. `alembic current` is
  `0001_phase1 (head)`, and `alembic check` reports no new upgrade operations.
- FastAPI started successfully against the reapplied schema.

## FastAPI results

A real Uvicorn process on `127.0.0.1:18080` passed 20 HTTP checks. `/health`, all dashboard views,
validated/source/processing exports, pause/resume, source enable/disable and `process-next` returned
their expected success codes. Missing dashboard authentication returned 401; disabled collection and
processing returned 409; an undersized idempotency key, invalid source and negative pagination cursor
returned 422. Cleanup remained available while processing was disabled by design. No scheduler ran.

The PostgreSQL API suite additionally proves same-key enqueue replay, conflicting-key rejection,
snapshot pagination, CSV formula-safe values, failed-job retry and a background executor completing
a queued job.

## Synthetic qualification pipeline

Fourteen representative records covered strong motor intent, strong home intent, weak intent,
broker advertising, employment, news, repeated identity, duplicate email, malformed email, no-MX,
South African context, irrelevant content and an ambiguous record. Grok and DNS were deterministic
fixtures.

- 14 raw records entered normalization.
- 8 became candidates (57.1% source-to-candidate in this deliberately intent-heavy fixture).
- 5 became validated leads (62.5% candidate-to-valid-contact; 35.7% raw-to-lead).
- Broker, job, news, weak and irrelevant records were rejected as configured.
- The repeated identity did not create a second candidate; the repeated email produced one lead.
- The malformed/no-MX cases did not become validated leads.
- Processed/rejected source records were reduced to zero.
- Re-running empty stages changed nothing; reintroducing an accepted identity/content did not create
  a second lead.

## Bluesky

The public, credential-free Jetstream collector scanned exactly 30 events: 20 in an initial call, 5
from its returned cursor and 5 through the database-backed collection pipeline. Every call stopped at
the hard cap. The cursor advanced monotonically, persisted in `source_cursors`, set
`last_success_at`, completed the durable job and recorded `raw_scanned=5` for the database-backed
batch.

No configured keyword match appeared in this tiny live window, so zero source records were stored.
That prevents a live source-reference/dedupe assertion; URL construction is covered by the mocked
adapter test and complete qualification/dedupe behavior is covered by the synthetic pipeline.

## YouTube

No real YouTube request was made because the configured key is present in Git history and has not
been rotated. The mocked adapter test passes authentication-header construction, two-page comment
pagination, checkpoint state, bounded requests, source URL construction and persistent quota-cap
stopping without a fetch. A 10–30-comment real check remains required after rotation.

## Grok

No real xAI request was made because the configured key is present in Git history and has not been
rotated. Mocked xAI responses prove strict JSON Schema/Pydantic parsing, rejection of type-invalid
output, personal-data redaction, one retry after HTTP 429, conservative attempt reservation and
single success accounting.

The accounting fixture used 100 input and 20 output tokens and configurable rates of USD 1/M input
and USD 2/M output, producing USD 0.00014. Current model pricing variables are not configured, so a
meaningful production cost estimate cannot yet be reported. A 5–10-record real comparison remains
required after rotation.

## Google Sheet

Google OAuth through the connected Drive integration succeeded. The private native Sheet contains
`SUMMARY`, `SOURCE_STATS`, `VALIDATED`, `FAILED`, `PROCESSING` and `EXPORT_HISTORY`. Metadata and
bounded values were read back from all six tabs. The workbook contains values, no formulas, and the
sample contains no raw source payloads. A targeted repeat update changed the existing source-stat
cell in place without appending a duplicate row.

The actual Apps Script `refreshDashboard` function was not executed. The independent local `clasp`
session failed with `invalid_grant / invalid_rapt` and requires interactive Google reauthentication,
which cannot be automated safely. Render authentication from Apps Script also needs an isolated
public test service using rotated credentials.

## Google Drive backup

The private test folder contains one native Sheet and five date-stamped backup artifacts: a two-row
validated-lead CSV, source-stat CSV, processing-summary JSON, the lead-part checksum sidecar and the
completion manifest. Drive metadata and file content were read back. The CSV SHA-256 was recorded in
the sidecar. Repeating the source-stat write reused the same Drive file ID rather than creating a
second file.

The Apps Script Node harness proves create-once behavior, SHA-256, snapshot-bound validation,
forward-only pagination and interrupted-state resume. Live `dailyBackup` execution, status logging
and a runtime interruption/resume remain blocked by the same `clasp` and public test-Render setup.

## Deduplication, retention and recovery

- Multi-hash claims are atomic. Existing source/content, candidate identity, email and lead hashes
  stop repeated work before paid classification or duplicate storage.
- Source replay leaves one source record; pipeline replay leaves one candidate/lead.
- Pending raw data survives age-based cleanup because it can belong to unfinished work.
- Old rejected/processed raw rows, expired candidates and stale email-validation cache rows are
  deleted. Validated leads, identity rows, record/content/email/lead hashes, cursors and pipeline
  state remain.
- An abandoned `RUNNING` job is reclaimed. Failures back off, stop at the configured attempt cap,
  persist only sanitized error codes and can be manually reset.
- A normalization interruption rolls back its batch. Classification and validation preserve prior
  completed records, resume the unfinished record and create no duplicate classifications or leads.
- Source cursor and accepted rows commit atomically; replay overlap is idempotent.

## Measured performance and capacity

A separate local database processed 600 strong synthetic records using the configured 600-record
batch. DNS was a controlled fixture and Grok calls were zero.

| Measure | Result |
|---|---:|
| Insert time | 0.0834 s |
| Normalize + validate wall time | 9.8665 s |
| Throughput | 60.81 records/s |
| Process CPU | 7.6875 s |
| RSS before / after | 81.37 MB / 88.35 MB |
| RSS growth | 6.98 MB |
| Transient raw relation growth | 983 bytes/record |
| Retained candidate + lead/index growth | 5,502 bytes/lead |
| Raw rows after pipeline | 0 |

At that local rate, 30,000 deterministic records require about 493 seconds (8.2 minutes) of pipeline
wall time and about 384 seconds (6.4 minutes) of CPU, excluding source latency, real DNS, Grok and
free-Render cold starts/throttling. The measured deterministic path has ample compute headroom for
50 batches of 600 per day; external latency and quota remain the limiting unknowns.

Thirty thousand simultaneously retained raw rows would add about 29.5 MB (28.1 MiB) before cleanup.
At the intent-heavy synthetic candidate rate, 200,000 raw records could yield roughly 114,000
candidates. Applying the conservative 5.5 KB retained footprint to every candidate is about 629 MB,
before PostgreSQL bloat and fixed overhead. A pathological 100% retained conversion would exceed
1 GB over the week. The backlog cap, immediate rejection deletion and cleanup materially protect the
limit, but database size must be monitored daily and candidate retention may need tightening before
Phase 4.

## Configuration changes

- Added validated `ENVIRONMENT` configuration and documented its placeholder.
- Added `psutil` to development dependencies for controlled memory/CPU measurements.
- Added a guarded local benchmark that refuses non-test, non-loopback or incorrectly named databases.
- Expanded automated tests for live-schema API behavior, YouTube/Grok protocol fixtures, full
  retention state, local-model training and stage recovery.
- Production-safe flags and schedule defaults were not changed.

## Unresolved risks and required manual configuration

1. Rotate the four exposed credential groups and update ignored local/Render values privately.
2. Create an isolated Render test database/service or equivalent public test endpoint with
   `ENVIRONMENT=test`, every acquisition/model/scheduler flag false and new test API tokens. Apply
   migrations before starting it.
3. Reauthenticate `clasp` interactively, create or select the isolated Apps Script test project,
   upload the repository files and set test Script Properties. Do not install triggers.
4. Run one 10–30-comment YouTube collection with the rotated key and a known approved video ID.
5. Run 5–10 de-identified Grok classifications with the rotated key, current configured model, tiny
   daily cap and current price variables.
6. Manually run `refreshDashboard` and `dailyBackup` twice against the public test API; verify auth,
   six-tab replacement, one daily artifact set, runtime status, interruption resume and failure
   reporting. The connector-created artifacts in this report do not substitute for that runtime test.
7. Re-run the automated suite after any fixes and change this report to PASS only when all remaining
   checks succeed.

## Phase 3 gate

Phase 3 is not safe to begin while the items above remain. After Phase 2 is changed to PASS and the
user explicitly approves Phase 3, run one small real source-to-Drive cycle with manual job execution,
hard source/model caps, isolated data, all schedules disabled and no outbound email. Inspect lead
quality, checkpoints, usage, Sheet rows and backup artifacts, then stop. Do not start the seven-day
workflow until a separately approved Phase 4.

The manually initialized loopback PostgreSQL cluster on port 55433 was stopped. The official Windows
installer also registered `leadgen-phase2-postgres` on port 55432. The current non-administrator
session could not stop that Windows service (`Access is denied`); an administrator must stop it and
set its startup type to Manual or remove it if it is no longer wanted. It contains no LeadGen test
data and was not used by the test suite.

PHASE_2_STATUS=FAIL
