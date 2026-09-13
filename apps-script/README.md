# Apps Script installation

All files are authored but have not been uploaded or executed. Follow [platform setup](../PLATFORM_SETUP.md)
to use the existing project, spreadsheet and folder from the ignored local environment.

Functions:

| Function | Purpose |
|---|---|
| refreshDashboard | Fetch summary/source/jobs/failures and a 500-lead sample; write values |
| dailyBackup | Start/resume one date's paginated useful-data snapshot |
| pausePipeline / resumePipeline | Authenticated controls; environment flags remain authoritative |
| processorTick | Gated enqueue + bounded execution request |
| installReportingTriggers | Explicitly install 15-minute dashboard and hourly backup continuation |
| installProcessorTrigger | Explicitly install gated processing ticks; only after Phase 3 |
| removeLeadgenTriggers | Remove only this solution's named trigger handlers |

No function sends email. No Google API key or service account is required. `DriveApp` runs with the
Google account that authorizes the script. The supplied manifest declares required scopes; do not put
tokens in Sheet cells or source code. Keep the project private.

Backup filenames:

```text
validated-leads-YYYY-MM-DD-part-000001.csv
validated-leads-YYYY-MM-DD-part-000001.csv.json
source-stats-YYYY-MM-DD.csv
processing-summary-YYYY-MM-DD.json
backup-manifest-YYYY-MM-DD.json
```

Sidecars contain pagination/checksum metadata. The completion manifest is written last. Keep all
parts for a date together. Backups are snapshots of useful leads, not full database dumps.
Runtime limits, downtime and permissions are tested in Phase 2. No triggers have been installed.
