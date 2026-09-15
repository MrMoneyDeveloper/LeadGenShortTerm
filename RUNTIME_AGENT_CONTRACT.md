# LeadGenShortTerm Runtime Agent Contract

This file is a mandatory execution contract for Codex and any other coding agent working on this repository.
Read it **before any external account lookup, deployment, migration, API call, Google write, Cloudflare action, or semantic-provider test**.

`AGENTS.md` and this file override older operational wording in historical reports or prompts when there is a conflict.

## 1. Account selection: `.env` is authoritative

The ignored repository-root `.env` plus repository configuration are the source of truth for every runtime/platform target.

**Do not select an account, workspace, service, project, database, Sheet, Drive folder, or AI provider from a connected plugin/connector's current account.**

For this project, runtime/platform plugins and connectors are **not an authorization source and are not a target-selection mechanism**.

### Forbidden

- Do not use a connected Render plugin/connector to discover or mutate whichever Render account it happens to expose.
- Do not use a connected Cloudflare plugin/connector instead of `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN`.
- Do not use a connected Google Drive/Sheets plugin to choose a Sheet, folder, or Apps Script project.
- Do not use a connected AI-provider plugin instead of the API key/model configured in `.env`.
- Do not fall back to a plugin when an `.env` credential is missing, rejected, or points at an unexpected account.
- Do not infer that a connected account belongs to this project merely because a tool can access it.

If required `.env` configuration is missing or invalid, **stop only that integration step and report the blocker**. Never silently switch accounts.

## 2. Required preflight before external mutation

Before mutating any external platform:

1. Load the ignored repository-root `.env` without printing secret values.
2. Confirm `ENVIRONMENT` and the requested phase/test scope.
3. Use the credential from `.env` to perform a harmless read-only identity/resource lookup through the provider's direct API/CLI.
4. Compare the returned identity/resource to the IDs/names expected from `.env` and this project.
5. Abort the mutation on any mismatch.
6. Log only sanitized identifiers/names needed to prove the target, never credentials or database URLs containing passwords.

A successful plugin lookup does not satisfy this preflight.

## 3. Provider-specific access rules

### Render

Use direct Render API/CLI calls authenticated only with:

- `RENDER_API_KEY`
- `RENDER_API_BASE_URL`
- `RENDER_WORKSPACE_ID`
- the exact service/database identifiers from the project configuration/runtime test plan

The Phase-2 target is the isolated LeadGenShortTerm test resource in the privately configured workspace. Never touch unrelated Render resources or unrelated databases/accounts.

Before every mutation, verify the authenticated workspace and target service/database with the same `RENDER_API_KEY` that will perform the mutation.

### Cloudflare

Use only direct Cloudflare API/Wrangler authentication sourced from:

- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN`

If the token is absent, stop the Cloudflare deployment step. Do not use a plugin/connector as a substitute.

### Google / Apps Script

Use the configured project/resource IDs:

- `APPS_SCRIPT_SCRIPT_ID`
- `GOOGLE_SPREADSHEET_ID`
- `GOOGLE_DRIVE_BACKUP_FOLDER_ID`

`clasp` may require interactive Google authentication, but the target project and destination IDs must still come from project configuration, not connector discovery.

### GroqCloud

The semantic provider is **GroqCloud**, not xAI, unless `SEMANTIC_PROVIDER=xai` is explicitly and intentionally configured.

Canonical GroqCloud runtime values are:

- `GROQ_API_KEY`
- `GROQ_MODEL`
- `GROQ_ENABLED`

Direct API base: `https://api.groq.com/openai/v1`.

Never send a GroqCloud credential to `api.x.ai`.

## 4. GroqCloud plan: Free tier is intentional

The project is designed to run on the **GroqCloud Free tier**. Do not upgrade, enable paid Batch/Flex capacity, or design assuming paid inference unless the user explicitly changes this requirement.

For the currently used GPT-OSS models, Groq's published Free-tier envelope is presently:

- 30 requests/minute (RPM)
- 1,000 requests/day (RPD)
- 8,000 tokens/minute (TPM)
- 200,000 tokens/day (TPD)

This applies to `openai/gpt-oss-20b` and `openai/gpt-oss-120b` in the current published rate-limit table.

**The exact organization limits and response headers are authoritative and may change.** The implementation must not depend only on these static numbers.

Official reference: `https://console.groq.com/docs/rate-limits`

## 5. Required GroqCloud rate-control behavior

The solution must enforce a conservative local safety envelope below the provider limits and also consume provider rate-limit headers.

Recommended initial soft defaults for the Free tier:

```text
GROQ_PLAN=free
GROQ_RPM_LIMIT=30
GROQ_RPD_LIMIT=1000
GROQ_TPM_LIMIT=8000
GROQ_TPD_LIMIT=200000

GROQ_RPM_SOFT_CAP=24
GROQ_DAILY_REQUEST_SOFT_CAP=800
GROQ_TPM_SOFT_CAP=6500
GROQ_DAILY_TOKEN_SOFT_CAP=150000
```

The precise soft caps may be tuned after controlled tests, but they must remain below the provider/account limits.

Persist enough state that Render sleep/restart does not forget daily usage or a provider cooldown. At minimum track:

- requests used for the current provider day/window
- input tokens
- output tokens
- last provider response time
- `next_allowed_at` / cooldown when rate-limited
- latest known provider request/token limits and remaining values where headers expose them
- latest request/token reset information where headers expose it
- count of 429 responses
- deferred semantic-candidate count

Use the Groq response headers when present, including:

- `retry-after`
- `x-ratelimit-limit-requests`
- `x-ratelimit-remaining-requests`
- `x-ratelimit-reset-requests`
- `x-ratelimit-limit-tokens`
- `x-ratelimit-remaining-tokens`
- `x-ratelimit-reset-tokens`

Do not confuse the token header with daily token usage: Groq documents the token header as the active token-rate window. Maintain the project's own conservative daily token counter as well.

## 6. What happens when GroqCloud reaches a limit

A Groq rate limit is **not a pipeline failure**.

When RPM/TPM is temporarily exhausted:

1. parse `retry-after` / reset headers when available;
2. persist a provider cooldown;
3. return the current semantic candidate to a durable deferred state with its priority intact;
4. do not burn candidate/job failure attempts merely because the provider told us to wait;
5. continue deterministic collection, normalization, validation, ranking, cleanup and export work;
6. resume semantic work after the provider becomes eligible again.

When the daily request/token soft limit is reached:

1. stop new GroqCloud requests for that allowance period;
2. keep AI-eligible records durably queued and priority ordered;
3. continue all non-Groq pipeline stages;
4. resume the highest-ranked deferred semantic work after reset.

At campaign finalization, do not wait forever for low-value semantic work. Use the configured finalization policy to process the highest-ranked remaining candidates within available free allowance and then finish cleanly.

## 7. Batching policy

Small GroqCloud batches may be used **only if controlled tests prove they improve request efficiency without harming structured-output reliability**.

Batching reduces request-count pressure. It does **not** create more TPM/TPD allowance.

Requirements for any semantic batch:

- every input item has a stable `candidate_id`;
- every output item must return that same `candidate_id`;
- never map only by array position;
- strict JSON schema remains required;
- a missing/invalid item is retried/deferred individually, not by blindly replaying the whole batch;
- batch size must be configurable and bounded;
- estimated/requested token footprint must respect current TPM headroom;
- never construct a huge prompt merely to reduce RPD.

Start with the current single-record path unless benchmarks show a clear benefit. A sensible experimental maximum is a small batch such as 2–5 records, not hundreds.

## 8. GroqCloud Free-tier telemetry, not billing

The user is intentionally using the Free tier.

Token/request accounting is still required because it controls throughput and reset behavior.

Do not present list-price arithmetic as an amount the user is actually being charged.

For `GROQ_PLAN=free`:

- actual configured pipeline cost should be reported as `0` unless Groq reports otherwise for the account;
- dashboards/reports should emphasize requests/tokens/remaining allowance/deferred queue;
- if a list-price comparison is retained for engineering reference, label it explicitly as `reference_list_price`, never `estimated_cost` or `amount_spent`.

## 9. Pipeline independence from GroqCloud

GroqCloud only receives the highest-value ambiguous subset after deterministic work, contact validation, local ML and ranking.

The pipeline must continue when Groq is unavailable.

```text
raw
-> normalize
-> dedupe
-> contact extraction/validation
-> deterministic short-term-insurance scoring
-> local ML/rank
-> DIRECT_FINAL or REJECT where sufficiently clear
-> only ambiguous high-ranked subset enters GROQ queue
```

GroqCloud never validates hard email syntax/MX, invents contacts, or decides dedupe state.

## 10. Phase-2 account safety

Until Phase 2 is complete:

- use isolated test resources only;
- keep dangerous source/acquisition/scheduler gates off except during bounded explicit tests;
- do not start the real seven-day campaign;
- do not send outbound email;
- do not rotate working exposed credentials repeatedly while debugging;
- after all integrations work, list the exposed active credentials that must be rotated once before Phase 3.

## 11. Agent start checklist

Before doing any work, explicitly confirm in your internal execution plan:

```text
[ ] Read AGENTS.md
[ ] Read RUNTIME_AGENT_CONTRACT.md
[ ] Read docs/ARCHITECTURE.md
[ ] Read current docs/PHASE_2_TEST_REPORT.md
[ ] Loaded ignored .env without printing secrets
[ ] Will not use runtime/platform plugins/connectors as account selectors
[ ] Will abort rather than fall back to another account
[ ] GroqCloud plan is Free
[ ] Groq rate limits are treated as throughput constraints, not billing estimates
[ ] Phase 3 / real seven-day run / outbound email remain prohibited
```

If any task instruction conflicts with this contract, stop and resolve the conflict before mutating an external platform.
