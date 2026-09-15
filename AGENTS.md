# AGENTS.md

Mandatory instructions for Codex and any other coding agent working in this repository.

## Read this first

Before doing anything else, read in this order:

1. `RUNTIME_AGENT_CONTRACT.md`
2. `docs/ARCHITECTURE.md`
3. `docs/PHASE_2_TEST_REPORT.md`
4. `docs/CONFIGURATION.md`
5. `docs/PHASE_2_TESTING.md`

`RUNTIME_AGENT_CONTRACT.md` is authoritative for account selection, external tools, GroqCloud Free-tier behavior, and Phase-2 execution safety.

Historical prompts/reports may describe older architecture. Do not resurrect retired behavior merely because an old document mentions it.

## Current milestone

The project is in **Phase 2 controlled runtime validation**.

The architecture is already built. Do not redesign it again unless a test proves a concrete defect.

Do not start Phase 3, the real seven-day campaign, or outbound email.

## Non-negotiable external-account rule

**Use the ignored repository-root `.env` and repository configuration as the source of truth.**

Do not use connected runtime/platform plugins/connectors to choose or mutate external accounts.

This specifically applies to Render, Cloudflare, Google/Apps Script resources, and AI-provider runtime access.

If an `.env` credential/ID is missing, rejected, or mismatched, stop that integration step and report the blocker. **Never fall back to whatever account a plugin happens to expose.**

Before every external mutation, perform a read-only identity/resource verification using the same `.env` credential that will perform the mutation.

## Current architecture

```text
public source adapters
  -> bounded collection + source profile
  -> Render FastAPI processing
  -> PostgreSQL temporary durable queues/checkpoints
  -> deterministic short-term-insurance rules
  -> contact extraction + syntax/DNS/MX validation
  -> local TF-IDF/logistic model + ranking
  -> DIRECT_FINAL / REJECT where clear
  -> selective GroqCloud Free-tier semantic classification for high-ranked ambiguity
  -> final lead queue
  -> Apps Script final delivery
  -> Google Sheets VALIDATED_### shards
  -> verified Google Drive receipt/backup
  -> delivery ACK
  -> transient PostgreSQL payload cleanup
```

Cloudflare is the primary lightweight wake/scheduling coordinator when enabled. It does not process lead payloads.

Google Apps Script performs final delivery/dashboard/backup/watchdog work. It is not the raw-data processing engine.

PostgreSQL is a conveyor belt and durable operational state store, not a permanent raw archive.

## GroqCloud

The default semantic provider is **GroqCloud**, not xAI.

Canonical configuration:

```text
SEMANTIC_PROVIDER=groq
GROQ_ENABLED=
GROQ_API_KEY=
GROQ_MODEL=
```

The user intentionally uses the **GroqCloud Free tier**.

Do not treat token accounting as billing. Tokens/requests are tracked primarily to stay inside free-tier limits.

Current published GPT-OSS Free-tier constraints and required runtime behavior are pinned in `RUNTIME_AGENT_CONTRACT.md`. The implementation must also honor actual provider response headers/account limits because platform limits can change.

GroqCloud is selective. Never send all raw records to it.

Do not use GroqCloud for:

- hard email syntax validation
- DNS/MX validation
- dedupe
- keyword checks
- contact invention
- name invention

When GroqCloud rate limits are reached, defer semantic work and continue the rest of the pipeline.

## Data-retention rules

```text
raw -> short-lived
rejected -> delete
candidate -> temporary
direct/final lead -> retain only until verified Google delivery ACK
exported heavy payload -> delete after ACK
campaign dedupe/checkpoint/compact receipts -> retain as required for idempotency
```

Do not create an archive of rejected/raw source material.

Preserve bounded source provenance needed to explain where a final lead came from.

## Database rules

Use explicit Alembic migrations.

Never use `create_all()` as proof that migration drift is clean.

Keep large source payloads out of final/candidate state where possible.

Every durable stage must be idempotent/restartable.

A failed/retried batch must not duplicate final leads.

Backpressure must stop/reduce acquisition before PostgreSQL storage/queues become unsafe.

## Final lead rules

The finished dataset belongs in sharded Google Sheet tabs such as:

```text
VALIDATED_001
VALIDATED_002
VALIDATED_003
```

Drive holds verified delivery copies/backups.

Final lead data should include, where available:

```text
email
first_name
salutation
source
source_evidence
source_url
product_type
rank/score
validation status
stable lead/campaign IDs
```

Name is optional.

Reliable first name:

```text
Good day Mohammed,
```

No reliable name:

```text
Good day,
```

Never infer a name merely from an email username, handle, company slug, or uncertain model guess.

## Source and ranking rules

This project targets **South African short-term insurance** leads.

Search terms, product terms, exclusions, South African signals, and ranking weights belong in repository configuration/fixtures rather than collector source code.

Test ranking quality, not merely API success.

Strong consumer quote/switch/recommendation intent should outrank generic discussion.

Broker advertising, recruitment, news, and non-target insurance categories should rank low or reject as configured.

South African context is a positive signal, not an automatic reason to fabricate location.

## Secrets

Never commit or print secrets.

Examples include:

```text
DATABASE_URL
RENDER_API_KEY
GROQ_API_KEY
XAI_API_KEY
YOUTUBE_API_KEY
CLOUDFLARE_API_TOKEN
Google credentials
MWEB credentials
API bearer tokens
```

`.env.example` / `env.example` contain empty placeholders only.

Do not display secret-bearing URLs in logs/reports.

## Testing

For meaningful changes, run the relevant tests and then a complete final regression pass before merge.

At minimum the Phase-2 branch expects:

```text
python -m pytest
python -m ruff check .
git diff --check
python -m alembic -c services/render_api/alembic.ini check
node tests/apps_script_harness.cjs
```

Also run Cloudflare coordinator tests/checks when that service changes.

Use only disposable/isolated PostgreSQL for destructive migration testing.

## Scaling

The eventual acquisition target is approximately:

```text
200,000 raw records over 7 days
```

That is a throughput target, not a retained-row requirement.

Process continuously:

```text
collect -> qualify -> deliver -> ACK -> cleanup -> collect more
```

Do not accumulate 200,000 raw rows in PostgreSQL.

## Scheduling/autonomy

The real campaign must eventually run without the user's laptop.

Cloudflare may wake/orchestrate Render.

Apps Script independently handles delivery/dashboard/backup schedules.

Persist state so Render sleep/restart does not lose progress.

Do not install production schedules during Phase 2 merely because scheduling code exists.

## Outbound email

Do not implement or enable production outbound email, MWEB sending, or forwarding automation during this phase.

If outbound work is later authorized, compliance/suppression state becomes durable system state and must never be bypassed.
