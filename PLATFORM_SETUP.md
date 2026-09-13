# Phase-1 platform setup

The code is built; platform setup and execution are separate. Keep schedules and acquisition disabled through the initial Phase-2 checks. Do not configure MWEB or Cloudflare.

## Private environment

Existing legacy example values were preserved in ignored `.env`, with DATABASE_URL mapped from the supplied external PostgreSQL URL for local use. Both example files now have empty placeholders. Never commit `.env`, credentials JSON, `.clasprc.json`, or `.clasp.json`.

The old tracked example contained API/database credentials. Sanitizing the file does not erase Git history: rotate the exposed credentials before live use. History cleanup, if needed, must be coordinated separately and has not been done during this build.

## PostgreSQL

Use the existing Render PostgreSQL database; the Blueprint does not create a duplicate. Set DATABASE_URL to the internal connection URL in Render. Use an external URL with required TLS/network permissions for local access to a disposable test database.

Run migrations first in Phase 2, against the disposable database:

```powershell
.\.venv\Scripts\python -m alembic -c services/render_api/alembic.ini upgrade head
```

Migrations are absent from startup. A newly created service returns 503 until the explicit migration is applied. Run migrations from an authorized local environment if the service tier lacks a pre-deploy command/shell. Later, a reviewed pre-deploy command can run the same migration on a supported tier.

## Render service

Use root `render.yaml` and repository root as the build directory so shared `config/` is available. Build and start commands are in the Blueprint. Automatic deployment is off. Build commands install dependencies only. Select the region matching the existing database.

Set DATABASE_URL and the two independent API tokens. The Blueprint can generate fresh tokens; copy their values privately into Apps Script. Existing local tokens are independent until you explicitly make corresponding values match. Add XAI_API_KEY/XAI_MODEL and YOUTUBE_API_KEY when those components are ready for testing. Runtime does not need Render management credentials.

Record the deployed HTTPS RENDER_BASE_URL in private metadata and Script Properties. Check database lifetime and available compute before production scheduling. A sleeping free service cannot guarantee continuous internal scheduling; use explicitly installed Apps Script ticks or adequate always-running capacity after measurements.

## Google Apps Script

Use the existing project identified by APPS_SCRIPT_SCRIPT_ID. Copy Code.gs, Dashboard.gs, Backups.gs, Controls.gs and appsscript.json from `apps-script/` into that project using the editor or an explicitly configured local clasp project. No web-app deployment is necessary.

Set Script Properties from the private configuration:

```text
RENDER_BASE_URL
GOOGLE_SPREADSHEET_ID
GOOGLE_DRIVE_BACKUP_FOLDER_ID
DASHBOARD_API_TOKEN
PROCESSOR_TRIGGER_TOKEN
BACKUPS_ENABLED=false
DASHBOARD_ENABLED=false
PROCESSOR_SCHEDULE_ENABLED=false
```

The executing Google account must be able to edit the Sheet and create files in the folder. Authorize Drive, Sheets, URL Fetch and trigger-management scopes in the Google UI during Phase 2. No Drive API key/service account is required. Standalone scripts work with the Sheet ID; the onOpen menu needs a Sheet-bound project. Restrict editors and keep the folder/Sheet private.

In Phase 2, manually run `refreshDashboard` and `dailyBackup` with tiny test exports, then rerun backup to check duplicate prevention. Code load never installs triggers.

After reporting tests pass, explicitly set BACKUPS_ENABLED and DASHBOARD_ENABLED true and run `installReportingTriggers`. This replaces only its own reporting handlers: dashboard every 15 minutes and backup/resume hourly. Only one completed snapshot per local date is created. Review the backup loss window before Phase 4.

Processing scheduling belongs after Phase 3: set PROCESSOR_SCHEDULE_ENABLED true and run `installProcessorTrigger`. Do not also enable the internal scheduler unless you intend additional ticks. `removeLeadgenTriggers` removes only the named LeadGen handlers.

## Activation gates, later only

Acquisition requires ACQUISITION_ENABLED, the selected source environment flag, its YAML enabled flag, its authenticated database enable control, and pipeline resume. Processing requires PROCESSING_ENABLED and resume. Grok additionally requires GROK_ENABLED, a supported model/key, credits and bounded caps. No script control bypasses these gates.

Follow [Phase 2](docs/PHASE_2_TESTING.md), then Phase 3's small complete real flow. Phase 4 is the separately approved one-week acquisition.
