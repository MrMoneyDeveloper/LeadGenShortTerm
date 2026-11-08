# Data-collection hardening, local failover and alphabetical delivery

Updated: 2026-11-08 · Branch `codex/collection-hardening`

```text
BUILD_AND_TEST_STATUS=PASS
LOCAL_FAILOVER_STATUS=PASS (disposable PostgreSQL + mocks; launcher verified; not enabled or run live)
ALPHABETICAL_DELIVERY_STATUS=PASS (offline suites + live delivery after deployment)
SOURCE_YIELD_STATUS=BLOCKED (0 real finals; no source yet supplies intent plus a usable email)
READY_FOR_SMALL_CANARY=NO (infrastructure deployed and verified; needs a source that supplies emails)
```

Objective: prepare the system to collect up to 100,000 **distinct, qualified South African
personal-insurance leads with a usable public email**, delivered to Google Sheets. Raw records,
no-email prospects, synthetic fixtures and duplicates never count. No sending, SMTP, MWEB,
forwarding or outreach was built. No acquisition, campaign or schedule was started. The live
deployment below was run separately at the operator's request.

## What was built

### Alphabetical delivery (migration `0007_alphabetical_delivery`, additive)

- `SHEET_LAYOUT=alphabetical` places each final by its normalized email's first character:
  `VALIDATED_A_001` ... `VALIDATED_Z_001`, everything else in `VALIDATED_OTHER_001`.
  Each bucket rolls over after `SHEET_SHARD_ROWS` (default 5,000) data rows.
- Durable per-bucket cursors (`pipeline_state.export_bucket_positions`) never move backwards.
  Placements are immutable; acknowledged rows are never sorted or overwritten.
- The layout is pinned at a destination's first claim. A mismatch fails closed
  (`final_layout_changed`). Switching an occupied destination needs the explicit, verified
  `adopt-layout` command, which refuses while a claimed batch is outstanding and preserves the
  legacy sequential cursor, tabs and receipts. Alphabetical names cannot collide with legacy tabs.
- Apps Script accepts the new tab names, creates new tabs at exactly header + needed rows and one
  column per field (no empty 1,000×26 grids), checks **actual allocated workbook cells before any
  write** (refusing above 90% of `WORKBOOK_CELL_LIMIT`, default 10,000,000), and maintains a
  `SHARD_INDEX` tab (tab, bucket, rows, capacity, remaining, allocated cells).
- Readback, verified Drive copies, exact ACK, and payload deletion **only after ACK** are unchanged.
- Defect found and fixed: daily backups selected shards with a sequential-only pattern and sorted
  by a numeric slice, so alphabetical tabs would have been silently excluded from backups.
- `GET /dashboard/shards` reports tabs per bucket and an exact-size cell estimate.

**Capacity:** 100,000 rows × 17 fields, spread over the 27 buckets at 5,000 rows per tab, is
27 tabs and (100,000 + 27 header rows) × 17 = **1,700,459 cells**. That's about 17% of the
conservative 10M guard. Google announced 20 million cells per spreadsheet as generally available on
2026-09-10; the guard stays at 10M until the account's limit is confirmed. A skewed distribution
(e.g. 60k `A` + 40k `OTHER`) needs 20 tabs and 1,700,340 cells. Synthetic allocation tests cover
both; no 100,000-row data was generated or published.

### Final-lead target accounting

- `FINAL_LEAD_TARGET` (default 100,000) is persisted per campaign at start (`final_target`).
- Accepted finals = pending `validated_leads` + acknowledged deliveries of the current campaign.
  A claimed batch is counted once; earlier campaigns' deliveries are excluded.
- Reaching the target puts acquisition into the existing `drain` mode (the Cloudflare coordinator
  allowlists mode names, so no new mode was introduced) and moves a running campaign to
  `DRAINING`. Processing, delivery and cleanup continue. Reported fields: `final_target`,
  `accepted_finals`, `finals_remaining`, `final_target_reached`.
- Existing safety budgets still apply: campaign raw target and deadline, `RAW_DAILY_TARGET`,
  YouTube unit caps, Groq soft caps, queue and storage watermarks.

### Qualification

- Policy change: `require_south_africa: true`. A final needs explicit South African evidence.
  Records without it are not hard-rejected; they go to semantic review and never count while
  uncertain. Thresholds were not lowered; the labelled fixture keeps precision 1.0 and 0 false
  positives.
- Dedupe hashes (`email:`, `lead:`) survive delivery cleanup, so a delivered email from another
  source is rejected (tested).
- Exhausted Groq allowance defers semantic candidates with priority and attempts intact while
  deterministic stages and direct finals keep flowing (tested). No local classifier substitutes
  for semantic review: none is trained and hash-pinned yet.

### Local-device failover

See [LOCAL_FAILOVER.md](LOCAL_FAILOVER.md). In short:
- **Launch:** `scripts/failover/Start-LeadgenFailover.ps1` (`status` / `once` / `run` / `stop`) runs `app/failover.py`.
- **Detection:** bounded health probes, plus a durable per-role executor heartbeat that `jobs.run_next` now writes. It catches a stalled scheduler even when HTTP is healthy.
- **Switching:** failure and recovery thresholds; identity verification before any processing.
- **Execution:** the same advisory executor lock as Render.
- **Safety:** single-instance OS lock and stop file; it pauses on a database outage and is observe-only unless `FAILOVER_ENABLED=true`.

## Tests

| Suite | Result |
|---|---|
| Python with disposable PostgreSQL | **170 passed** (25 new: 12 failover, 11 delivery/target/dedupe/quota, 2 policy) |
| Ruff / `git diff --check` | Passed |
| Alembic `0007` upgrade → downgrade → upgrade; drift check | Passed; downgrade refuses once alphabetical deliveries exist |
| Apps Script delivery harness | 22 passed (7 new: buckets/OTHER, trimmed grids, rollover, invalid names, capacity refusal, shard index, lost ACK, backups) |
| Apps Script bounded delivery / ops endpoint | 4 / 7 passed |
| Cloudflare coordinator | 21 passed |
| Stateless worker | 10 passed |
| PowerShell launcher | Parses; `status` ran end-to-end against a disposable database (observe-only) |

Coverage includes:
- **Executor and failover:** cloud/device lock contention, a stalled scheduler behind a healthy endpoint, restart/state persistence, handback hysteresis, identity refusal, database outage pause.
- **Delivery:** alphabetical/OTHER/rollover placement, sequential compatibility and guarded switching, claim replay and lost ACK, cleanup after ACK, 100k capacity.
- **Accounting and qualification:** target counting without double counts, acquisition stop at the target, cross-source dedupe after delivery, quota isolation.

## Flagged conflicts (not silently resolved)

- `AGENTS.md` still says the project is in Phase 2 and forbids Phase 3. Its scaling section
  describes a 200,000-raw-record, seven-day objective, which differs from the new 100,000 final-lead
  objective. The default campaign raw target (200,000) would stop acquisition long before 100,000
  finals at any realistic yield. It must be raised deliberately once a source's yield is measured.
- `AGENTS.md` final-lead rules list `VALIDATED_###` tabs; alphabetical tabs are now an option.
- The Cloudflare test Cron expires 2026-11-11. After that the cloud heartbeat goes stale, and a
  running, enabled watchdog would treat the scheduler as stalled. That's correct behaviour, but only
  start the watchdog with `FAILOVER_ENABLED=true` when device takeover is intended.
- The live free database expires 2026-12-08, and its allow list holds one operator IP. The device's
  current public IP must be on that list for failover to work.

## Live deployment (2026-11-08, operator-approved)

Each step was preceded by a read-only identity check with the `.env` credentials (Render
workspace and service, database name and allow list, `.clasp.json` script ID).

| Step | Result |
|---|---|
| Live database migration | `0006` → `0007_alphabetical_delivery`; `alembic check` clean; existing receipts marked `sequential` |
| Layout switch | `adopt-layout alphabetical` confirmed against the bound Sheet; legacy cursor (6 rows) preserved |
| Render | `SHEET_LAYOUT=alphabetical`; deployed `8aa9e9c`; health OK; `/dashboard/shards` bound and configured `alphabetical` |
| Apps Script | Code pushed; ops web deployment moved to version 2 (same URL, still expires 2026-11-15); triggers and Script Properties unchanged |
| Live alphabetical delivery | One labelled synthetic record (reserved `.invalid` address) delivered to a newly created `VALIDATED_P_001`, Drive copy verified, ACKNOWLEDGED, payload removed; `SHARD_INDEX` created; legacy `VALIDATED_001` untouched; replay IDLE |

Notes:
- `VALIDATED_P_001` row 2 and `VALIDATED_001` rows 5–7 are synthetic test rows and can be deleted.
- Before any campaign starts, `accepted_finals` includes those acknowledged test deliveries. Starting a
  campaign scopes the count to its own deliveries, so they never count toward the real target.
- For the failover identity check, set `FAILOVER_EXPECTED_DATABASE` in `.env` to the live database's
  `current_database()` name.

Remaining steps from the original list: the failover `status` check and the source-yield canary.

## Next controlled run (steps 1–4 below were completed on 2026-11-08)

From `C:\Workspace\LeadGenShortTerm` after merging to `main`:

```powershell
# 1. Additive migration of the live database (uses the ignored .env DATABASE_URL)
.\.venv\Scripts\python -m alembic -c services\render_api\alembic.ini upgrade head
.\.venv\Scripts\python -m alembic -c services\render_api\alembic.ini check

# 2. Deploy Render from main (Render dashboard: Manual Deploy > latest commit), then:
#    GET <RENDER_BASE_URL>/health  ->  {"status":"ok"}

# 3. Push Apps Script (alphabetical names, capacity guard, shard index, backup fix)
clasp push --force

# 4. Optional: switch the existing Sheet to alphabetical. Claims fail closed until both steps agree.
cd services\render_api
..\..\.venv\Scripts\python -m app.cli adopt-layout --layout alphabetical --confirm-spreadsheet-id <GOOGLE_SPREADSHEET_ID>
#    then set SHEET_LAYOUT=alphabetical on Render and redeploy
cd ..\..

# 5. Failover readiness check (observe-only)
powershell -ExecutionPolicy Bypass -File scripts\failover\Start-LeadgenFailover.ps1 -Command status

# 6. Source-yield canary: follow docs/SOURCE_YIELD_GATE.md for one approved source only.
```

Stop after the canary report. Do not start the 100,000-lead campaign or send email.
