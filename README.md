# LeadGenShortTerm — short-term insurance lead data engine

LeadGenShortTerm collects bounded public source records, reduces them with deterministic rules and
optional local ML, validates public contact data, selectively uses GroqCloud for high-value semantic
ambiguity, and delivers only final usable leads into sharded Google Sheets with verified Drive copies.

**Phase 2 is still controlled testing. No seven-day production campaign, outbound email, MWEB sender,
or production schedule is authorized by this repository state.** See
[`docs/PHASE_2_TEST_REPORT.md`](docs/PHASE_2_TEST_REPORT.md); that report must be regenerated after the
current post-report architecture changes complete their live gates.

```text
Bluesky / YouTube source adapters
 -> source-specific public profile normalization
 -> Render FastAPI + PostgreSQL transient queues
 -> deterministic short-term-insurance filter / dedupe / contact extraction
 -> syntax + DNS/MX validation
 -> local TF-IDF/logistic regression when configured
 -> auditable 0-10 rank
      -> strong deterministic finals
      -> selected ambiguous records -> GroqCloud
      -> low/negative records -> reject/defer
 -> final PostgreSQL delivery queue
 -> Apps Script -> VALIDATED_001 / VALIDATED_002 / ...
 -> Drive delivery copy + Sheet readback
 -> ACK to Render
 -> delete heavy delivered payloads from PostgreSQL
```

An optional Cloudflare Worker is the intended lightweight external scheduler/wake-up coordinator.
It does not process or store leads. Render performs the data work, PostgreSQL owns durable queues and
checkpoints, Apps Script handles output-side delivery/dashboard/backups, and Google Sheets is the
primary finished dataset.

## Autonomous campaign lifecycle

The backend now persists campaign state instead of relying on a manually calculated deadline:

```text
READY -> RUNNING -> DRAINING -> FINALIZING -> COMPLETE
```

`POST /admin/campaign/start` records the actual start timestamp, applies the configured duration
(default seven days) and raw target (default 200,000), and resumes the pipeline. Reaching either the
raw target or deadline stops **new acquisition** while downstream validation, selective inference,
delivery and cleanup continue. Production collection is not permitted until this explicit campaign
start has occurred.

## Final lead output

Google receives only final usable rows. Each row contains the stable campaign/lead ID, reliable public
name fields when available, safe salutation (`Good day,` when no reliable given name exists), email,
source/evidence URLs, bounded evidence, product, rank and validation state. Rows are assigned by Render
into `VALIDATED_###` tabs, default 5,000 data rows per tab.

Apps Script writes at most 200 final rows per delivery call, reads them back, creates/reuses verified
Drive JSON/CSV copies, then ACKs the exact batch. Render deletes the heavy lead/candidate payload only
after that ACK. This makes PostgreSQL a bounded conveyor belt rather than the historical final archive.

## GroqCloud

GroqCloud (`GROQ_*`) is the default optional semantic provider. It is not xAI/Grok. It receives only
ranked ambiguous candidates after deterministic contact validation and local processing. Persisted
request and token soft caps protect free-tier usage. Legacy xAI/Grok support remains separately named
and disabled unless explicitly selected.

## Repository map

```text
config/                         Short-term-insurance terms, ranking and source targets
services/render_api/
  app/
    api/                        Auth, campaign, job, dashboard and export endpoints
    collectors/                 Common protocol + source-specific profiles/adapters
    classifiers/                Local model + GroqCloud + optional legacy xAI clients
    services/                   Pipeline, campaign, backpressure, delivery, reporting
  migrations/                   Explicit Alembic revisions
  tests/                        Unit/PostgreSQL/recovery/domain/delivery coverage
services/cloudflare_coordinator/ Optional wake/status/tick Worker; no lead payload processing
apps-script/                    Final delivery, dashboard, backups and gated trigger helpers
ml/                             TF-IDF/logistic-regression training/evaluation
```

## Safe defaults

All acquisition, processing, semantic-provider, final-delivery and scheduler gates default off.
PostgreSQL starts paused and source rows start disabled. Migrations are never applied automatically on
web-service startup, and uploading Apps Script does not install triggers.

Use Python 3.12 or 3.13. For controlled local work:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r services/render_api/requirements-dev.txt
.\.venv\Scripts\python -m alembic -c services/render_api/alembic.ini upgrade head
```

Never point destructive/integration tests at an operational database.

## Reading guide

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — authoritative data/delivery architecture.
- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) — current variables, gates and ownership.
- [`PLATFORM_SETUP.md`](PLATFORM_SETUP.md) — platform setup and explicit deployment steps.
- [`docs/API.md`](docs/API.md) — authenticated API surface.
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md) — scheduling, retention, backpressure and recovery.
- [`docs/PHASE_2_TESTING.md`](docs/PHASE_2_TESTING.md) — controlled release gates.

The 200,000-raw/seven-day figure is a throughput objective, not a promise of 20,000 final leads.
`DELIVERABLE_DOMAIN` means syntax/domain/MX checks passed; it does not prove mailbox existence or grant
permission to send marketing email.
