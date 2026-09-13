# LeadGenShortTerm — Phase-1 data engine

The Phase-1 code collects public insurance-intent records, reduces them using deterministic rules and optional ML/Grok, validates public email domains, and exports useful leads to a Google Sheets dashboard and Google Drive through Apps Script.

**Phase-1 build is complete. Phase-2 internal tests pass, but credentialed external checks remain blocked. Nothing is deployed or collecting by default.** See the [Phase-2 test report](docs/PHASE_2_TEST_REPORT.md). No sender, MWEB integration, Cloudflare dependency, GitHub Actions workflow, or seven-day production schedule is activated.

```text
Bluesky Jetstream / YouTube
 -> bounded collection + atomic checkpoints
 -> Render Python/FastAPI + PostgreSQL
 -> normalize / deterministic filter / dedupe / score
 -> local TF-IDF + logistic regression when trained
 -> Grok for unresolved semantic classification when enabled
 -> public-text contacts + syntax / DNS / MX validation
 -> validated PostgreSQL leads
 -> Apps Script -> Sheets dashboard + daily Drive CSV/JSON snapshots
```

## Repository map

```text
config/                         Editable source targets, terms, scores, risky domains
services/render_api/
  app/
    api/                        Auth and HTTP routes
    collectors/                 Common protocol, Bluesky, YouTube
    classifiers/                Local model loader and Grok JSON classifier
    services/                   Pipeline stages and reporting/export
    config.py database.py       Settings and PostgreSQL sessions/locks
    models.py repositories.py   Tables and atomic hashes/budgets/metrics
    qualification.py            Normalization and deterministic scoring
    validators.py               Contact discovery and DNS validation
    jobs.py cli.py main.py      Durable jobs, scheduler, CLI, FastAPI
  migrations/                   Versioned Alembic + immutable SQL
  tests/                        Phase-2 unit/integration/recovery coverage
  scripts/                      Guarded local Phase-2 performance benchmark
apps-script/                    API client, dashboard, backup, controls, manifest
ml/                             Training/evaluation and example data format
docs/                           Architecture, configuration, API, operations, testing plan
render.yaml                     Safe Blueprint using existing PostgreSQL
.env.example / env.example      Empty placeholders; actual .env is ignored
```

## Setup after build approval

Use Python 3.12 or 3.13. From repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r services/render_api/requirements-dev.txt
```

Keep existing `.env` if present; otherwise copy `.env.example` and populate backend values. Existing legacy environment values were preserved in ignored `.env`; both example files are sanitized.

The following commands belong to **Phase 2**, against a disposable database:

```powershell
.\.venv\Scripts\python -m alembic -c services/render_api/alembic.ini upgrade head
.\.venv\Scripts\python -m uvicorn app.main:app --app-dir services/render_api --host 127.0.0.1 --port 8000 --no-access-log
```

Startup initializes safe control rows only and requires an already-migrated database. It does not apply migrations or install schedules. Default batch size is 600; Phase-2 source checks should override to 1–5. Never point integration tests at the operational database.

## Reading guide

- [Architecture](docs/ARCHITECTURE.md): authoritative design, schema, stage and transaction boundaries.
- [Platform setup](PLATFORM_SETUP.md): Render deployment, Google authorization and explicit trigger setup.
- [API](docs/API.md): endpoints, auth, queue semantics and export pagination.
- [Configuration](docs/CONFIGURATION.md): variables, defaults, ownership and source gates.
- [Operations](docs/OPERATIONS.md): scheduling, failures, retention, recovery and known limits.
- [Phase 2](docs/PHASE_2_TESTING.md): exact next-phase sequence and release gates.
- [Build report](docs/PHASE_1_BUILD_REPORT.md): components, static checks and outstanding platform work.

The 200,000-record/seven-day goal is a throughput target, not a retained-row requirement or measured capacity. VALIDATED means qualified intent plus a deliverable email domain; it does not prove mailbox existence or authorize outbound contact.
