# Phase 2 platform setup

The runtime contract and docs/CONFIGURATION.md are authoritative. Use only isolated
Phase-2 resources selected by the private repository-root .env. Never select a
platform account through an unrelated connector session. Do not send outbound email
or start the seven-day campaign.

## Private configuration

The application reads .env, not env. Both are ignored, but keep .env authoritative
when updating values. Never paste credentials into reports or terminal output.
Required IDs and credentials are described in RUNTIME_AGENT_CONTRACT.md.

Before each external mutation, verify the identity and resource with the same
credential that will perform it. Stop that integration on a mismatch or missing key.

## Render and PostgreSQL

Use the isolated LeadGenShortTerm test service in the configured Trident Wealth
workspace. The read-only services/render_api/scripts/phase2_render_preflight.py
checks the configured workspace and matches the configured runtime origin to a
service before checking health and a sanitized summary. It never deploys or migrates.
Run it with the local virtual-environment Python from the repository root.

Migrations must be applied explicitly before deploying the updated client. Current
head is 0005_semantic_drain_deadline. Test rollback only on a disposable local DB.
The Render build installs dependencies; startup must not auto-apply migrations.

Use render.yaml with the repository root as build directory. Record the verified
HTTPS RENDER_BASE_URL privately. GroqCloud uses GROQ_API_KEY/GROQ_MODEL, with
GROQ_PLAN=free and conservative request/token caps. Legacy xAI settings do not select
the default provider. Keep Render SCHEDULER_ENABLED=false when Cloudflare owns ticks.

## Google Apps Script

Authenticate clasp interactively with the intended account. Verify access to the
configured APPS_SCRIPT_SCRIPT_ID, GOOGLE_SPREADSHEET_ID and
GOOGLE_DRIVE_BACKUP_FOLDER_ID before uploading. Include all files in apps-script/,
including Delivery.gs. No web-app deployment is necessary.

Set Script Properties privately:

- RENDER_BASE_URL
- GOOGLE_SPREADSHEET_ID
- GOOGLE_DRIVE_BACKUP_FOLDER_ID
- DASHBOARD_API_TOKEN
- PROCESSOR_TRIGGER_TOKEN
- DELIVERY_ENABLED=false initially
- BACKUPS_ENABLED=false initially
- DASHBOARD_ENABLED=false initially
- PROCESSOR_SCHEDULE_ENABLED=false

Uploading code does not install triggers. In bounded tests, enable delivery and run
one tiny batch. Verify literal Sheet rows, readback, Drive JSON/CSV copies, exact ACK
and PostgreSQL deletion. Exercise a tiny configured shard boundary. Run dashboard
and backup twice to verify repeatability.

For the explicit autonomous Phase-2 test, install the independent five-minute
delivery trigger using installDeliveryTrigger after enabling DELIVERY_ENABLED.
Reporting triggers are separate. Keep Google processor scheduling disabled when
Cloudflare owns processing. Remove test schedules after recording evidence.

## Cloudflare

CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN must come from .env. The current token
is missing; no plugin fallback is permitted. Verify identity with that token first.
The Worker processes no lead payloads. Its source and tests are under
services/cloudflare_coordinator/.

Checked-in defaults have no Cron triggers and keep COORDINATOR_ENABLED=false and
SCHEDULER_OWNER=none. Only a bounded authorized test should temporarily enable Cron,
COORDINATOR_ENABLED and SCHEDULER_OWNER=cloudflare on the isolated deployment.
Prove wake, backpressure inspection and one bounded jobs/tick without another owner.

## Exit evidence

Run the local regression and migration checks after fixes. Then prove the tiny
cloud chain and several cycles with the laptop/local processes off. Rotate exposed
active credentials once after integration debugging, update private/platform values
and smoke-test replacements. Record actual evidence in docs/PHASE_2_TEST_REPORT.md.

Phase 3 follows a Phase-2 pass and explicit approval: one small real complete cycle,
hard caps, isolated data, no outbound email, inspect and stop. Phase 4 is the separately
approved approximately 200,000-record/seven-day acquisition run.
