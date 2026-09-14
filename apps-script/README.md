# Apps Script final-lead delivery and backups

Render performs qualification. Apps Script moves only finished leads into the configured Google
spreadsheet, verifies a Drive copy and acknowledges successful delivery. Postgres can then remove
the heavy working records. The spreadsheet is the primary finished dataset. Ranking, source
normalization and semantic classification do not run in Apps Script.

Upload all `.gs` files and `appsscript.json` to the isolated test Apps Script project using the
setup process in [platform setup](../PLATFORM_SETUP.md). Configure these **Script Properties**
using the ignored local configuration; never place secrets in source code or Sheet cells:

| Property | Purpose |
|---|---|
| `RENDER_BASE_URL` | HTTPS origin of the correct Render test service |
| `GOOGLE_SPREADSHEET_ID` | Final-output test spreadsheet; must match Render's configured ID |
| `GOOGLE_DRIVE_BACKUP_FOLDER_ID` | Test folder; must match Render's configured ID |
| `PROCESSOR_TRIGGER_TOKEN` | Operator bearer token for claim/ACK and manual controls |
| `DASHBOARD_API_TOKEN` | Read token for dashboard and operational statistics |
| `DELIVERY_ENABLED` | Exactly `true` permits `deliveryTick` and delivery-trigger installation |
| `PROCESSOR_SCHEDULE_ENABLED` | Keep `false` when Cloudflare owns processing orchestration |
| `DASHBOARD_ENABLED` / `BACKUPS_ENABLED` | Gates for their installed scheduled wrappers |

Google runs `SpreadsheetApp` and `DriveApp` under the account that authorizes this project. No
Google API key or service account is required. Keep the project and output resources private.
No triggers are installed by loading or manually executing these files.

## Manual functions and trigger helpers

| Function | Purpose |
|---|---|
| `deliverFinalLeads()` | Claim and deliver at most 200 finished leads, or recover the previous ACK |
| `deliveryTick()` | Gated wrapper around one `deliverFinalLeads()` call |
| `installDeliveryTrigger()` | Explicitly install a five-minute delivery trigger; requires `DELIVERY_ENABLED=true` |
| `refreshDashboard()` | Replace small SUMMARY, SOURCE_STATS, PROCESSING and FAILED value tables |
| `dailyBackup()` | Start/resume a daily snapshot of final Sheet shards plus operational statistics |
| `pausePipeline()` / `resumePipeline()` | Authenticated controls; environment/source/campaign gates still apply |
| `processorTick()` | Gated bounded Render tick; keep disabled when Cloudflare is scheduler owner |
| `installReportingTriggers()` | Explicitly install dashboard + backup triggers only |
| `installProcessorTrigger()` | Explicit legacy/fallback processing trigger; not used with Cloudflare ownership |
| `removeLeadgenTriggers()` | Remove processor, delivery, dashboard and backup handlers |

Phase 2 may exercise these functions manually or with isolated test triggers. Do not install final
production triggers until the live runtime gates pass. There is no outbound email implementation.

## Delivery protocol

`POST /exports/claim` returns one immutable batch until acknowledged. Render assigns each row in
`VALIDATED_001`, `VALIDATED_002`, and subsequent shards. The configured server shard capacity
defaults to 5,000 **data rows**; the header is separate. The claim pins `shard_rows`, so a retry
uses its original capacity even if configuration later changes. Apps Script never independently
calculates a placement or assumes row 5,001 is the boundary.

1. Validate destination IDs, stable lead IDs, placement bounds and the batch SHA-256 checksum.
2. Read all destination rows. Empty rows and exact matches are safe; a conflicting row or formula
   stops the batch. Final tabs are never cleared, sorted, or replaced by the dashboard.
3. Write literal rich text, flush Sheets and read back all values. This prevents values beginning
   with formula characters from executing as spreadsheet formulas.
4. Create/reuse an immutable JSON delivery envelope and CSV in the configured Drive folder.
   Reread the JSON and verify its `items` checksum. CSV independently escapes formula prefixes.
5. Save only batch/file/checksum references in `DELIVERY_STATE`, record `EXPORT_INDEX`, then
   `POST /exports/ack` with the exact placements and verified Drive file reference.
6. Clear retry state after the server returns `ACKNOWLEDGED` and the index is updated.

The JSON checksum is SHA-256 of UTF-8 `JSON.stringify(items)`. Each item has ordered keys
`lead_id`, `tab`, `row`, `values`; values are raw strings. The `drive_checksum` sent in the ACK
is that same checksum recomputed from the JSON file's parsed items. The JSON envelope includes
batch, campaign, destination, field and shard-capacity information. CSV and JSON content hashes
are separate because CSV escaping changes bytes.

If the process dies after writing Sheets, the next claim returns the same rows. If it dies after
writing Drive, filenames derived from the batch ID recover the same files. If the ACK response
is lost, the saved file reference reconstructs and repeats that ACK **before claiming a new batch**.
Personal lead fields are never stored in Script Properties or status logs.

Do not manually edit, sort or delete final-shard rows during delivery. Use a separate analysis
copy for sorting. Render's persisted destination positions must continue to identify the same rows.
The legacy `VALIDATED` sample tab is not part of the final dataset.

## Daily backups

Postgres may already have drained accepted leads, so `dailyBackup()` reads the final Sheet shards.
It pins each shard's last row when starting the snapshot, writes up to 1,000 records per CSV part,
and checkpoints between parts within a 180-second work window. Reinvoke it or let an approved backup
trigger continue later. Newly delivered rows after the snapshot boundary appear in the next day's
backup; their per-delivery JSON/CSV files already protect them immediately before ACK.

```text
lead-delivery-<hash-of-batch-id>.json
lead-delivery-<hash-of-batch-id>.csv
validated-leads-YYYY-MM-DD-VALIDATED_001-part-000001.csv
validated-leads-YYYY-MM-DD-VALIDATED_001-part-000001.csv.json
source-stats-YYYY-MM-DD.csv
processing-summary-YYYY-MM-DD.json
backup-manifest-YYYY-MM-DD.json
```

Part sidecars record file IDs, SHA-256 checksums, shard and row boundaries. The completion
manifest pins the snapshot and lists the statistics files/checksums. `EXPORT_HISTORY` has one
row per date, while `EXPORT_INDEX` records one row per delivered batch. Repeated execution
reuses files and repairs history if execution stopped after creating the completion manifest.
Mismatched file content, duplicate filenames, missing shard rows, or changed destinations stop
the operation and surface a code in FAILED and SUMMARY. Raw source records are never exported.

The Google workbook's overall cell limit still applies even with shards. Four 5,000-row shards
with the current lead fields are comfortably smaller than that limit. A later campaign must
provision a new configured workbook before the full workbook limit is approached; changing a
campaign ID alone must not reset placements in an existing workbook.

## Local evidence and remaining live gate

`node tests/apps_script_harness.cjs` executes the actual functions against offline Sheets,
Drive, properties and Render mocks. It covers checksum/literal preservation, shard rollover,
conflicting rows, ACK rejection/lost response, crashes after Sheet/Drive writes, repeat dashboard
and daily export, paginated CSVs and manifest/history recovery. The Python suite invokes the same
harness.

Offline checks do not prove Google authorization, quotas, latency, trigger scheduling or Apps Script
runtime behavior. The controlled Phase-2 integration gate must still execute delivery and backup
against the isolated test Sheet/folder, verify final rows/files and server deletion after ACK, then
prove the bounded `deliveryTick` can run independently with the local development machine stopped.
