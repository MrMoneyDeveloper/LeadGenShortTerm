# LeadGenShortTerm

Short-term insurance lead-generation MVP focused first on **data acquisition, cleaning, qualification, deduplication, and contact validation**.

The first milestone is **not email delivery**. The immediate goal is to prove that we can pull a large raw dataset, process it over several days, and retain only genuinely useful, contactable prospects.

## Current architecture decision

For the MVP, use:

```text
GitHub
  ↓
Render Web Service (Python)
  ↓
Bluesky / YouTube / approved public-web collectors
  ↓
Deterministic filtering + dedupe
  ↓
Local scoring / classifier
  ↓
Grok only for ambiguous semantic classification
  ↓
Email discovery + deterministic validation
  ↓
Render PostgreSQL
  ↓
Validated lead pool
  ↓
Google Apps Script / Sheets dashboard later
```

Cloudflare R2/D1 is deliberately **not required for the first MVP**. It can be introduced later if scale, retention, or operational requirements justify it.

## Why this approach

### 1. Prove lead quality before adding infrastructure

The difficult problem is not storing 200,000 rows. It is turning a large public-data stream into useful short-term-insurance prospects.

We first prove:

```text
200k raw records
  ↓
relevant candidates
  ↓
high-intent prospects
  ↓
contactable / validated leads
```

Only then should we add more permanent or paid infrastructure.

### 2. Pull quickly, retain selectively

The system is a processing pipeline, not an archive.

Raw records should pass through quickly and be deleted once they are no longer useful.

Recommended lifecycle:

| Data | Retention |
|---|---|
| Raw scraped record | 1–7 days maximum |
| Rejected record | Delete after processing |
| Candidate | Keep until qualification completes |
| Validated lead | Keep through campaign lifecycle |
| Dedupe hash | Keep long term |
| Suppression / opt-out hash | Keep long term |

The database should retain **useful state**, not a permanent copy of the internet.

### 3. Render PostgreSQL is an MVP working database

The MVP uses Render PostgreSQL for:

- source cursors/checkpoints;
- processing jobs;
- candidate records;
- dedupe hashes;
- final validated leads;
- processing metrics.

It is intentionally treated as temporary MVP infrastructure. The free database is not the permanent production archive, so the application must support export and cleanup.

### 4. Do cheap work before AI work

Do not send every raw record to Grok.

Processing order:

```text
raw
 ↓
normalise
 ↓
keyword / deterministic filter
 ↓
dedupe
 ↓
local score / classifier
 ↓
Grok for uncertain records only
 ↓
email discovery
 ↓
email validation
 ↓
validated lead
```

Grok should answer semantic questions such as:

- Is this person actually expressing insurance intent?
- Is this consumer demand or an insurance advertisement?
- Is this relevant to short-term insurance?
- What product category does the text imply?

Grok should **not** be used for trivial deterministic checks such as email syntax or DNS/MX validation.

## Acquisition target

Initial design target:

```text
~200,000 raw records over 7 days
≈ 28,600 raw records/day
```

This is a throughput target, not a promise that 200,000 records will become 20,000 usable emails. Conversion must be measured per source.

The pipeline must be checkpointed so that each collector can stop and resume without restarting from the beginning.

## Initial data sources

### Bluesky Jetstream

Primary low-cost source for public post streams.

Use for detecting explicit intent phrases such as:

- looking for insurance;
- car insurance recommendations;
- premium increased;
- switch insurance;
- cheaper insurance;
- insurance quote;
- new car / first car context.

### YouTube

Collect comments from approved finance, vehicle, insurance, and South African consumer-content sources.

Examples of useful signals:

- asking for an insurer recommendation;
- complaining about current premium;
- asking whether another insurer is cheaper;
- asking for contact or quote information.

### Public web

Optional adapters can later inspect approved public pages/forums where the site's terms and the business's compliance policy allow collection.

## Source configuration

Search phrases and scoring rules must live in configuration files, not be hard-coded in collectors.

Suggested structure:

```text
config/
  keywords.yaml
  scoring.yaml
  sources.yaml
  runtime.yaml
```

This lets us tune intent without rewriting the collection code.

## Example intent scoring

```text
+35 explicit quote request
+30 asks for recommendation
+25 wants to switch insurer
+20 premium complaint
+15 recently bought vehicle
+15 South Africa signal
+10 named insurer

-60 broker / agent selling insurance
-50 job / recruitment content
-50 advertisement
-40 news / press content
```

These are starting values only. They must be calibrated from real labelled examples.

## Database philosophy

Do not store large raw payloads indefinitely.

A useful final record looks more like:

```json
{
  "source": "youtube",
  "source_url": "...",
  "display_name": "Jane",
  "intent_excerpt": "...",
  "product_type": "motor",
  "intent_score": 91,
  "email": "jane@example.com",
  "email_hash": "...",
  "status": "validated"
}
```

Rejected raw payloads should disappear after processing.

## Processing strategy

Use small batches so Render can work within modest resource limits.

Recommended starting batch size:

```text
500–1,000 raw records
```

Each batch must be:

- idempotent;
- checkpointed;
- retryable;
- independently measurable.

A failure in one source must not reset another source.

## Expected service responsibilities

### Render Web Service

- collection orchestration;
- normalisation;
- deterministic filtering;
- deduplication;
- local classification;
- Grok calls;
- email discovery/validation;
- API endpoints for status/export.

### Render PostgreSQL

- source cursors;
- jobs;
- candidate state;
- dedupe hashes;
- validated leads;
- metrics.

### Grok / xAI

- semantic classification only;
- structured JSON output;
- limited to candidates that actually need an LLM pass.

### Google Apps Script / Sheets

Later phase only:

- dashboard;
- reporting;
- scheduled trigger calls;
- daily exports/backups;
- manual review.

Sheets is not the canonical raw database.

### MWEB

Later phase only.

Email sending, consent-request templates, reply forwarding, and suppression workflow are deliberately outside the first data-engine milestone.

## MVP milestone

The first successful end-to-end milestone is:

```text
Bluesky or YouTube
  ↓
500–1,000 records
  ↓
filter / dedupe / score
  ↓
Grok where required
  ↓
email validation
  ↓
validated records in PostgreSQL
```

Once that is reliable, increase batch volume and add additional sources.

## Repository direction

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the implementation design and [`AGENTS.md`](AGENTS.md) for coding-agent rules.
