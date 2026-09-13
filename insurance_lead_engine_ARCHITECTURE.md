# Short-Term Insurance Lead Engine

> HISTORICAL / SUPERSEDED: This early Cloudflare/MWEB proposal is retained only for context.
> Follow `docs/ARCHITECTURE.md`, `README.md`, and the current Phase-1 instructions instead.
> Do not implement or activate the outbound/Cloudflare components described below.
## Repository Architecture & AI-Agent Build Specification

**Status:** Proposed MVP architecture  
**Primary objective:** Collect approximately 200,000 raw public-source records over a 7-day acquisition window, progressively reduce them to the largest practical set of validated, deduplicated, contactable short-term-insurance prospects, and place valid addresses into a persistent consent-request email queue.

**Delivery model:** The data pipeline finishes first. The outbound MWEB queue may continue draining for weeks after the collection week ends.

**Important:** This document is an engineering architecture, not legal advice. Final consent wording, data sources, MWEB usage, and marketing workflow must be approved by the business's compliance / Information Officer before production release.

---

# 1. Core Operating Model

The system has four separate concerns:

1. **Discover** public records that may indicate short-term-insurance interest.
2. **Qualify** those records using deterministic rules, ML/LLM classification, deduplication, and contact validation.
3. **Queue** validated contactable prospects without keeping unnecessary raw data forever.
4. **Request consent** once, through the dedicated MWEB mailbox. Replies are automatically forwarded to the marketing inbox.

The system does **not** need to treat every cold recipient as a full CRM lead.

A person only becomes commercially actionable after an affirmative response / valid consent event has occurred.

```text
PUBLIC SOURCES
     |
     v
COLLECTORS
     |
     v
R2 TEMP RAW BATCHES
     |
     v
NORMALISE
     |
     v
DEDUPE
     |
     v
RULE FILTER
     |
     v
INTENT SCORE
     |
     +------ uncertain/high-value ------> GROK
     |                                      |
     +--------------------------------------+
     |
     v
EMAIL DISCOVERY + VALIDATION
     |
     v
D1 VALIDATED QUEUE
     |
     v
MWEB CONSENT REQUEST SENDER
     |
     v
RECIPIENT
     |
     +---- no response ----> no action
     |
     +---- refusal/opt-out -> SUPPRESSION
     |
     +---- affirmative reply -> FORWARD TO MARKETING
```

---

# 2. Architectural Principles

## 2.1 Distribute workload, not duplicate work

Every source pipeline must have:

- its own cursor/checkpoint;
- independent enabled/disabled state;
- configurable daily target;
- configurable batch size;
- retry count;
- last successful run;
- current failure state.

Never restart a completed batch because another source failed.

## 2.2 Raw data is temporary

Do not make Google Sheets, D1, or Render hold the full raw 200,000 permanently.

Recommended lifecycle:

| Layer | Storage | Retention |
|---|---|---|
| Raw source batch | Cloudflare R2 | 7-14 days |
| Normalised candidate batch | R2 | Until acquisition campaign closes |
| Deduplication identity/hash | D1 | Long term |
| Validated send-ready lead | D1 | Until sent / expired |
| Sent identity hash | D1 | Long term |
| Suppression identity hash | D1 | Long term |
| Reporting/export | Google Sheets | Human-readable working set |

This preserves replay/debug capability without keeping a digital landfill indefinitely.

## 2.3 No expensive intelligence before cheap filtering

Target processing funnel:

```text
~200,000 RAW
     |
     | deterministic source rules
     v
~60,000 RELEVANT
     |
     | local classifier / weighted scoring
     v
~20,000-40,000 CANDIDATES
     |
     | Grok only for uncertain/high-value cases
     v
QUALIFIED CANDIDATES
     |
     | email discovery / validation / dedupe
     v
VALIDATED SEND QUEUE
```

The exact survival rate is unknown and must be measured by source.

---

# 3. Platform Responsibilities

## Cloudflare R2

Use for:

- raw JSONL/GZIP/Parquet batches;
- intermediate normalised batches;
- failed-batch payloads;
- temporary replay data.

Do **not** create one object per lead. Batch records into files.

Example:

```text
/raw/bluesky/2026-09-12/batch-0001.jsonl.gz
/raw/youtube/2026-09-12/batch-0001.jsonl.gz
/normalized/bluesky/2026-09-12/batch-0001.parquet
```

## Cloudflare D1

Use for operational state only:

- source cursors;
- batch manifests;
- dedupe hashes;
- validated leads;
- send queue;
- suppression list;
- consent-request history;
- basic run metrics.

Do not repeatedly update one row through every raw processing stage. That wastes daily write allowance.

## Cloudflare Workers

Use for:

- API gateway;
- scheduled orchestration;
- source API calls that are lightweight HTTP;
- D1/R2 routing;
- MWEB sender worker;
- feature flags / circuit breakers;
- health/status endpoints.

Do not use free Workers for CPU-heavy document parsing or large ML jobs.

## Render

Use for heavier Python work:

- normalisation;
- larger regex/text-processing batches;
- HTML extraction;
- local ML inference;
- dedupe preparation;
- email-domain validation;
- calling Grok in controlled batches;
- IMAP reply processing if later required.

Render free web services should not be treated as durable state.

## Google Apps Script

Use for:

- dashboard refresh;
- Sheets export/import;
- operational controls;
- daily summaries;
- manual review tools;
- calling the orchestration HTTP API.

Do **not** use Apps Script as the MWEB SMTP sender.

Do **not** make Sheets the canonical database.

## Grok / xAI

Use only for semantic work that deterministic code cannot reliably do:

- insurance-intent classification;
- distinguishing consumer demand from advertisements/broker content;
- identifying likely short-term-insurance category;
- resolving ambiguous text;
- structured scoring reason.

Do not use Grok to check whether `name@example.com` has valid email syntax. Python can survive that intellectual burden.

## MWEB

Use for:

- actual sender identity;
- consent-request email;
- normal mailbox receipt;
- forwarding inbound replies to marketing;
- optionally retaining a mailbox copy.

MWEB is the outbound identity, but the queue must be throttled conservatively and stop automatically when MWEB returns throttling / authentication / policy errors.

---

# 4. Compliance State Machine

The application must enforce state, not merely rely on wording in the template.

Recommended statuses:

```text
DISCOVERED
NORMALIZED
FILTERED_OUT
CANDIDATE
VALIDATED
CONSENT_REQUEST_QUEUED
CONSENT_REQUEST_SENT
REPLIED
CONSENT_GRANTED
CONSENT_WITHHELD
OPTED_OUT
BOUNCED
SUPPRESSED
EXPIRED
```

## Required rules

### Rule A: One consent request

Before queueing:

```text
IF email_hash exists in consent_history:
    DO NOT QUEUE
```

The system must preserve enough history to ensure the same person is not approached repeatedly by future re-scrapes.

### Rule B: Opt-out is not consent

A negative response, unsubscribe request, objection, or opt-out must never be converted into consent.

### Rule C: Suppression survives campaigns

The suppression table is long-lived.

```text
IF email_hash in suppression_list:
    reject candidate
```

### Rule D: A reply is not automatically legal consent

Operationally, every reply may be forwarded to marketing as an interested/engaged contact.

However, the system should distinguish:

```text
REPLIED
```

from:

```text
CONSENT_GRANTED
```

Only an affirmative response satisfying the approved consent wording should become `CONSENT_GRANTED`.

### Rule E: Source attribution

Every prospect should retain:

```text
source_platform
source_url
source_publication_or_profile
source_discovered_at
email_source
name_source
```

If the email came from a different source than the intent signal, preserve both.

Example:

```json
{
  "intent_source": "YouTube comment",
  "intent_source_url": "...",
  "email_source": "public business/profile website",
  "email_source_url": "..."
}
```

---

# 5. Data Collection Strategy

Each source is an adapter implementing the same interface.

```python
class SourceAdapter:
    def collect(self, cursor, limit) -> BatchResult:
        ...

class BatchResult:
    records: list
    next_cursor: str | None
    exhausted: bool
    source_metrics: dict
```

Initial source adapters:

```text
collectors/
  bluesky/
  youtube/
  public_web/
  optional_sources/
```

Avoid tying the core pipeline to one scraper/vendor.

---

# 6. Short-Term Insurance Search Configuration

Search terms belong in configuration, not buried in code.

Example `config/keywords.yaml`:

```yaml
products:
  motor:
    explicit:
      - "car insurance"
      - "vehicle insurance"
      - "motor insurance"
      - "insurance quote"
      - "car insurance quote"
      - "vehicle cover"

    intent:
      - "looking for insurance"
      - "need insurance"
      - "recommend insurance"
      - "who should I insure with"
      - "need a quote"
      - "want a quote"
      - "switch insurance"
      - "changing insurer"
      - "premium increased"
      - "insurance too expensive"
      - "cheaper insurance"

    context:
      - "bought a car"
      - "new car"
      - "first car"
      - "financed car"
      - "new vehicle"

  home:
    explicit:
      - "home insurance"
      - "house insurance"
      - "household insurance"
      - "contents insurance"
      - "building insurance"

    intent:
      - "need home insurance"
      - "home insurance quote"
      - "recommend home insurance"
      - "switch home insurance"

negative:
  - "insurance broker"
  - "insurance agent"
  - "insurance vacancy"
  - "insurance job"
  - "insurance careers"
  - "hiring insurance"
  - "insurance news"
  - "press release"
  - "sponsored"
  - "advertisement"
  - "I sell insurance"
  - "contact me for insurance"
```

Insurer names belong in a separate editable list:

```yaml
insurers:
  - OUTsurance
  - Discovery Insure
  - King Price
  - Santam
  - MiWay
  - Naked
  - Budget Insurance
  - Auto & General
  - Pineapple
```

Do not assume an insurer-name mention alone equals purchasing intent.

---

# 7. Deterministic Scoring

Example initial score:

```yaml
scoring:
  explicit_quote_request: 35
  asks_for_recommendation: 30
  asks_to_switch: 25
  complains_about_premium: 20
  recently_bought_vehicle: 15
  south_africa_signal: 15
  named_competing_insurer: 10

  broker_or_agent_signal: -60
  job_or_recruitment_signal: -50
  news_or_press_signal: -40
  advertisement_signal: -50

thresholds:
  discard_below: 25
  accept_without_llm_above: 85
  llm_review_min: 25
  llm_review_max: 84
```

Tune the values from real labelled samples.

---

# 8. Local Classifier

After approximately 500-1,000 manually labelled examples, train a small classifier.

Suggested first model:

```text
TF-IDF
+
Logistic Regression
```

Labels:

```text
HIGH_INTENT
MEDIUM_INTENT
LOW_INTENT
NOT_INSURANCE
ADVERTISEMENT
BROKER_OR_AGENT
NEWS
JOB_POST
```

Purpose:

- reduce Grok usage;
- cheaply process tens of thousands of records;
- make qualification reproducible.

Store model version in every scored candidate:

```text
classifier_version = "intent-v1.2"
```

---

# 9. Grok Classification

Use structured output.

Recommended prompt contract:

```json
{
  "is_short_term_insurance_relevant": true,
  "intent_level": "HIGH",
  "product": "MOTOR",
  "is_consumer": true,
  "is_advertisement": false,
  "is_broker_or_agent": false,
  "south_africa_signal": true,
  "reason": "User explicitly asks for recommendations after premium increase.",
  "score": 92
}
```

Input should be minimal:

```text
source
post/comment text
public profile description when relevant
date
deterministic score
```

Do not send unnecessary personal data to the model.

Use xAI Batch API where practical for large asynchronous classification passes.

---

# 10. Canonical Candidate Schema

```json
{
  "lead_id": "uuid",
  "source_platform": "youtube",
  "source_record_id": "abc123",
  "source_url": "https://...",
  "source_discovered_at": "2026-09-12T10:00:00Z",

  "display_name": "Jane",
  "public_handle": "@example",
  "country_signal": "ZA",

  "intent_text": "...",
  "product_type": "MOTOR",
  "rule_score": 74,
  "ml_score": 0.88,
  "llm_score": 92,

  "email": "jane@example.com",
  "email_source": "public_profile",
  "email_source_url": "https://...",

  "email_syntax_valid": true,
  "domain_resolves": true,
  "mx_present": true,
  "disposable_domain": false,

  "identity_hash": "...",
  "email_hash": "...",

  "status": "VALIDATED"
}
```

---

# 11. Deduplication

Normalise before hashing.

## Email

```text
lowercase
trim whitespace
unicode normalise
```

Then:

```text
SHA256(normalized_email)
```

## Identity

Where email is unavailable:

```text
SHA256(platform + ":" + normalized_handle)
```

## Content

Use content hash to prevent repeated processing of cross-posts.

```text
SHA256(normalized_text)
```

Dedupe order:

```text
suppression hash
    >
sent/consent-request history
    >
email hash
    >
platform identity hash
    >
content hash
```

---

# 12. Email Discovery and Validation

Do not ask an LLM whether an email looks valid.

Pipeline:

```text
EMAIL DISCOVERED
    |
    v
NORMALISE
    |
    v
SYNTAX
    |
    v
DOMAIN DNS
    |
    v
MX RECORD
    |
    v
DISPOSABLE DOMAIN CHECK
    |
    v
SUPPRESSION CHECK
    |
    v
PREVIOUS APPROACH CHECK
    |
    v
SEND-READY
```

Recommended states:

```text
INVALID_SYNTAX
NO_DOMAIN
NO_MX
RISKY
DELIVERABLE_DOMAIN
VALIDATED
```

MX presence does not prove that a particular mailbox exists.

Avoid aggressive SMTP mailbox probing.

---

# 13. D1 Tables

Minimum schema:

```sql
CREATE TABLE source_cursors (
    source TEXT PRIMARY KEY,
    cursor TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE batch_jobs (
    batch_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    r2_path TEXT NOT NULL,
    status TEXT NOT NULL,
    record_count INTEGER,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE identity_index (
    identity_hash TEXT PRIMARY KEY,
    email_hash TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE validated_leads (
    lead_id TEXT PRIMARY KEY,
    email_enc TEXT,
    email_hash TEXT NOT NULL,
    display_name TEXT,
    source_platform TEXT,
    source_url TEXT,
    email_source TEXT,
    email_source_url TEXT,
    product_type TEXT,
    final_score INTEGER,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_validated_email_hash
ON validated_leads(email_hash);

CREATE TABLE consent_history (
    email_hash TEXT PRIMARY KEY,
    first_request_at TEXT,
    response_at TEXT,
    status TEXT NOT NULL
);

CREATE TABLE suppression_list (
    email_hash TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE send_events (
    event_id TEXT PRIMARY KEY,
    email_hash TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    smtp_code TEXT,
    error_message TEXT
);
```

Prefer application-layer encryption for email values if feasible.

Do not store the MWEB password in D1.

---

# 14. Seven-Day Acquisition Plan

Target:

```text
200,000 / 7 ~= 28,600 raw records/day
```

Suggested starting allocation:

| Source | Daily target |
|---|---:|
| Bluesky / open social | 10,000 |
| YouTube | 8,000 |
| Public web / forums | 6,000 |
| Other approved sources | 5,000 |

Do not force exact percentages. Source quality outranks volume.

Pipeline runs continuously:

```text
Batch A: collect
Batch B: normalise
Batch C: classify
Batch D: validate
Batch E: export
```

No stage waits for the entire 200,000 to finish.

---

# 15. Batch Design

Recommended first batch size:

```text
500-2,000 records
```

Each batch has:

```json
{
  "batch_id": "youtube-2026W37-0012",
  "source": "youtube",
  "cursor_in": "...",
  "cursor_out": "...",
  "record_count": 1000,
  "status": "NORMALIZED",
  "attempt_count": 1
}
```

Retries must be idempotent.

A failed batch may be retried without creating duplicate leads.

---

# 16. Scheduler / Circuit Breakers

Example `config/runtime.yaml`:

```yaml
global:
  acquisition_enabled: true
  processing_enabled: true
  sending_enabled: false

limits:
  raw_daily_target: 30000
  render_batch_size: 1000
  grok_daily_candidate_cap: 5000
  d1_daily_write_soft_cap: 75000

failure_policy:
  pause_source_after_failures: 5
  pause_sender_after_smtp_failures: 3
  exponential_backoff: true

retention:
  raw_days: 10
  normalized_days: 21
```

The soft cap should be below provider hard limits.

---

# 17. MWEB Outbound Sender

Current documented MWEB settings:

```text
SMTP host: smtp.mweb.co.za
Port: 587
Authentication: required
TLS/STARTTLS: enabled
```

Use one recipient per consent-request message.

The sender pulls only:

```text
status = CONSENT_REQUEST_QUEUED
```

Process:

```text
Cron fires
  |
  v
check SENDING_ENABLED
  |
  v
check circuit breaker
  |
  v
fetch next queue item
  |
  v
recheck suppression + history
  |
  v
render approved HTML/text
  |
  v
SMTP through MWEB
  |
  +---- success --> CONSENT_REQUEST_SENT
  |
  +---- transient error --> retry/backoff
  |
  +---- throttled/policy error --> PAUSE SENDER
  |
  +---- permanent bounce --> BOUNCED
```

MWEB currently publishes approximate technical rules including about 400 messages/hour, but also advises against large promotional bulk sends and may dynamically pause high-volume sending.

Therefore:

**Production sending must remain disabled until compliance/business has confirmed that the intended MWEB usage is acceptable.**

Suggested release gate:

```text
MWEB_SENDING_APPROVED=false
```

Codex must not silently change this default.

The throttle configuration is a safety control, not a mechanism to bypass MWEB protections.

---

# 18. Cloudflare SMTP Worker

Cloudflare Workers support outbound TCP sockets and STARTTLS. Port 25 is blocked; MWEB uses 587.

Suggested directory:

```text
cloudflare/sender/
  src/
    index.ts
    smtp.ts
    queue.ts
    templates.ts
    circuit-breaker.ts
  wrangler.jsonc
```

Cloudflare Cron invokes:

```ts
scheduled()
```

The implementation must:

- open a new socket per send or safe batch;
- authenticate using secret bindings;
- perform STARTTLS;
- redact secrets from logs;
- parse SMTP response codes;
- close connection;
- persist only delivery metadata.

Secrets:

```text
MWEB_SMTP_USERNAME
MWEB_SMTP_PASSWORD
MWEB_SMTP_HOST
MWEB_SMTP_PORT
```

Store secrets in Cloudflare secret bindings, never Git.

---

# 19. MWEB Reply Forwarding

Simplest design:

```text
MWEB mailbox
   |
   v
auto-forward
   |
   v
marketing inbox
```

MWEB supports automatic forwarding and optionally retaining a copy.

Recommended:

```text
Forward target: marketing inbox
Keep copy: enabled initially
```

Why keep a copy initially:

- troubleshooting;
- proving response delivery;
- confirming forwarding reliability;
- later IMAP automation.

After the system is proven, retention can be revisited.

---

# 20. Optional Reply Ingestor

Even if no human monitors MWEB, preserving suppression state is valuable.

Optional Render job:

```text
MWEB IMAP :993
   |
   v
read new replies
   |
   +---- negative / stop --> SUPPRESSED
   |
   +---- affirmative --> REPLIED / CONSENT_GRANTED candidate
   |
   +---- ambiguous --> REPLIED + manual/compliance review
```

Do not auto-label every reply `CONSENT_GRANTED`.

A response such as:

```text
"Who are you?"
```

is a reply, but not necessarily consent.

Forward all replies to marketing regardless.

---

# 21. Consent-Request Email Template

The exact wording requires compliance approval.

Recommended neutral baseline:

```text
Subject: Permission to contact you

Hi {{first_name_or_neutral_greeting}},

We obtained your contact details from {{source_description}}.

{{responsible_party_name}} would like your permission to contact you about short-term insurance options.

If you would like us to contact you, please reply:

I CONSENT

If you do not wish to be contacted, you do not need to take any action. You may also reply STOP and we will record your preference.

Responsible party: {{responsible_party_name}}
Contact: {{responsible_party_contact}}
Privacy information: {{privacy_url_or_notice}}
```

Do not put claims such as these into the default consent request unless compliance expressly approves them:

```text
"We will beat your premium."
"We can save you money."
"Get the cheapest insurance."
"Special offer."
"Guaranteed better price."
```

Those statements change the character of the message and create avoidable compliance risk.

## HTML template

Keep HTML simple:

```html
<!doctype html>
<html>
<body>
  <p>Hi {{first_name_or_neutral_greeting}},</p>

  <p>
    We obtained your contact details from {{source_description}}.
  </p>

  <p>
    {{responsible_party_name}} would like your permission to contact you
    about short-term insurance options.
  </p>

  <p>
    If you would like us to contact you, please reply:
    <strong>I CONSENT</strong>
  </p>

  <p>
    If you do not wish to be contacted, you do not need to take any action.
    You may also reply <strong>STOP</strong> and we will record your preference.
  </p>

  <hr>

  <p>
    Responsible party: {{responsible_party_name}}<br>
    Contact: {{responsible_party_contact}}<br>
    {{privacy_notice}}
  </p>
</body>
</html>
```

Generate a plain-text MIME alternative as well.

---

# 22. Google Sheets / Apps Script

Sheets is the operational dashboard, not the database.

Suggested tabs:

```text
SUMMARY
SOURCE_STATS
VALIDATED_SAMPLE
QUEUE_SAMPLE
SEND_METRICS
FAILURES
CONSENT_METRICS
```

Do not mirror all 200,000 raw records into Sheets.

If stakeholders insist on more detailed exports, create static batched tabs/files with values only.

Avoid formula-heavy sheets.

Apps Script functions:

```text
refreshDashboard()
pausePipeline()
resumePipeline()
pauseSending()
resumeSending()
syncValidatedSample()
syncFailureSample()
```

Apps Script communicates with the backend through HTTP/HTTPS only.

---

# 23. Repository Structure

```text
insurance-lead-engine/
|
|-- README.md
|-- ARCHITECTURE.md
|-- AGENTS.md
|
|-- config/
|   |-- sources.yaml
|   |-- keywords.yaml
|   |-- scoring.yaml
|   |-- runtime.yaml
|   |-- retention.yaml
|   `-- email-template.yaml
|
|-- collectors/
|   |-- common/
|   |-- bluesky/
|   |-- youtube/
|   `-- public_web/
|
|-- pipeline/
|   |-- normalize/
|   |-- dedupe/
|   |-- rules/
|   |-- classifier/
|   |-- grok/
|   |-- email_discovery/
|   `-- email_validation/
|
|-- services/
|   `-- render_api/
|       |-- app/
|       |-- tests/
|       |-- requirements.txt
|       `-- render.yaml
|
|-- cloudflare/
|   |-- orchestrator/
|   |-- sender/
|   `-- migrations/
|
|-- apps-script/
|   |-- Code.gs
|   |-- Dashboard.gs
|   |-- Controls.gs
|   `-- appsscript.json
|
|-- templates/
|   |-- consent-request.html
|   `-- consent-request.txt
|
|-- ml/
|   |-- training/
|   |-- models/
|   `-- evaluation/
|
|-- tests/
|   |-- fixtures/
|   |-- integration/
|   `-- compliance/
|
|-- scripts/
|   |-- bootstrap.sh
|   |-- seed-dev-data.py
|   `-- export-report.py
|
`-- .github/
    `-- workflows/
        |-- tests.yml
        |-- deploy-cloudflare.yml
        `-- deploy-render.yml
```

---

# 24. AGENTS.md Instructions

The repository should contain an `AGENTS.md` telling Codex/agents:

```text
1. Never enable production sending by default.
2. Never log passwords, SMTP AUTH payloads, or plaintext credentials.
3. Never remove suppression/consent-history checks.
4. Never send a second consent request to an existing email_hash.
5. Never mark a generic reply as consent automatically.
6. Every collector must support checkpoints and idempotent retries.
7. Every external API must have explicit rate-limit handling.
8. Raw records belong in R2, not D1 or Sheets.
9. D1 is canonical for queue, suppression, dedupe and consent state.
10. All keyword and scoring changes must be config-driven.
11. All production template changes require compliance approval.
12. Tests must run before deployment.
```

---

# 25. Secrets and Environments

Environments:

```text
dev
staging
production
```

Never use production MWEB credentials in dev.

Required secrets may include:

```text
CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_API_TOKEN
R2_ACCESS_KEY
R2_SECRET_KEY
XAI_API_KEY
MWEB_SMTP_USERNAME
MWEB_SMTP_PASSWORD
MWEB_IMAP_USERNAME
MWEB_IMAP_PASSWORD
BACKEND_SHARED_SECRET
```

Google Apps Script stores only the backend API credential it needs.

---

# 26. Logging

Every job should emit structured logs:

```json
{
  "timestamp": "...",
  "service": "render-normalizer",
  "batch_id": "...",
  "source": "youtube",
  "processed": 1000,
  "accepted": 231,
  "rejected": 769,
  "duration_ms": 22341,
  "error_count": 2
}
```

Never log:

```text
MWEB password
full API keys
SMTP AUTH strings
unnecessary personal data
```

---

# 27. Metrics

Required dashboard metrics:

```text
Raw records collected
Records per source
Duplicates removed
Relevant records
ML accepted/rejected
Grok-reviewed records
Grok cost estimate
Emails discovered
Emails domain-valid
Suppressed matches
Previously approached matches
Validated queue size
Consent requests sent
SMTP failures
Bounce count
Replies
Affirmative consents
Negative responses
Source-to-valid-email conversion
Source-to-reply conversion
Source-to-consent conversion
```

These metrics will tell us which source is actually worth continuing.

---

# 28. Acceptance Criteria

## Acquisition MVP

- [ ] At least one source collector implemented end-to-end.
- [ ] Cursor/checkpoint resume works.
- [ ] Failed batches can be replayed.
- [ ] Raw batches stored in R2.
- [ ] Deterministic filtering works.
- [ ] Dedupe survives repeated runs.
- [ ] Local classifier interface exists.
- [ ] Grok structured-output pass exists.
- [ ] Email validation pipeline exists.
- [ ] Valid leads enter D1 queue.

## Sender MVP

- [ ] Sender disabled by default.
- [ ] MWEB STARTTLS authentication tested manually.
- [ ] One-recipient test send succeeds.
- [ ] SMTP errors classified.
- [ ] Circuit breaker pauses automatically.
- [ ] Consent history prevents repeat sends.
- [ ] Suppression prevents sending.
- [ ] HTML + text MIME parts render correctly.
- [ ] MWEB forwarding to marketing verified.
- [ ] Compliance approves final template.

## Production readiness

- [ ] Compliance sign-off documented.
- [ ] Information Officer confirms source-disclosure wording.
- [ ] MWEB usage approved for intended workflow.
- [ ] Privacy notice approved.
- [ ] Retention schedule approved.
- [ ] Secrets rotated after testing.
- [ ] Staging dry run completed.
- [ ] First production batch manually limited.
- [ ] Deliverability monitored.

---

# 29. Build Order for Codex / AI Agent

## Phase 1 - Repository foundation

Create:

```text
README.md
ARCHITECTURE.md
AGENTS.md
config/
tests/
```

Add:

- Python formatting/linting;
- TypeScript linting;
- unit-test workflow;
- `.env.example`;
- no production secrets.

## Phase 2 - Cloudflare state layer

Build:

- D1 migrations;
- R2 bindings;
- `/health`;
- `/batches`;
- `/queue/stats`;
- `/pipeline/pause`;
- `/pipeline/resume`.

## Phase 3 - First collector

Implement one source only.

Prove:

```text
source -> R2 -> normalize -> D1 manifest
```

## Phase 4 - Qualification

Implement:

```text
rules
dedupe
classifier interface
Grok interface
```

## Phase 5 - Email validation

Implement:

```text
syntax
DNS
MX
suppression
previous-consent-request
```

## Phase 6 - Sender

Implement Cloudflare SMTP client for MWEB with:

```text
STARTTLS
AUTH
MIME
circuit breaker
send history
```

Keep:

```text
MWEB_SENDING_APPROVED=false
```

## Phase 7 - Forwarding / reply workflow

Configure MWEB:

```text
auto-forward to marketing
keep copy = true
```

Then optionally add IMAP reply sync.

## Phase 8 - Apps Script dashboard

Build the lightweight human control surface.

## Phase 9 - Scale sources

Only after metrics prove the first pipeline works:

```text
add YouTube
add Bluesky
add public-web adapters
```

Do not add five broken scrapers simultaneously.

---

# 30. Current Capacity Notes - September 2026

These are implementation assumptions and must be rechecked before deployment because service quotas change.

## Cloudflare D1 free

Current documentation states approximately:

```text
5 million rows read/day
100,000 rows written/day
5 GB stored
```

Use a 75,000-write soft cap.

## Cloudflare R2 free

Current documentation states approximately:

```text
10 GB-month storage
1 million Class A operations/month
10 million Class B operations/month
free egress
```

Batch objects aggressively.

## Cloudflare Workers free

Current Worker allowance includes approximately 100,000 requests/day, but CPU per invocation on free is small.

Use Workers for orchestration, not heavy text processing.

## Render free

Current free web service:

```text
0.1 CPU
512 MB RAM
spins down after idle
ephemeral filesystem
```

Render free also blocks outbound SMTP ports 25, 465 and 587.

Therefore Render must not be the MWEB sender.

## Apps Script

Current published limits include:

```text
6 minutes/execution
20,000 URL Fetch/day consumer
100,000 URL Fetch/day Workspace
90 minutes/day trigger runtime consumer
6 hours/day trigger runtime Workspace
```

Use Apps Script only for control/reporting.

## Grok 4.3

Current xAI documentation lists:

```text
$1.25 / 1M input tokens
$2.50 / 1M output tokens
Batch API supported
```

Use deterministic filtering and local classification before Grok.

---

# 31. Compliance Engineering Notes

Current South African POPIA requirements relevant to this architecture include:

1. Electronic direct marketing generally requires consent or the customer exception.
2. A person whose consent is required and who has not previously withheld it may be approached once to request consent.
3. The 2025 amended Regulations allow the consent request to be made through channels including email.
4. Opt-out does not constitute consent.
5. The responsible party bears the burden of proving consent.
6. Direct-marketing communications must identify the sender/responsible party and provide contact details through which communications can be stopped.
7. When personal information was not collected directly from the person, POPIA section 18 contains notification obligations including informing the person of the source, subject to applicable exceptions.
8. Public availability does not mean the information is free from POPIA requirements. Lead generation and subsequent processing remain regulated.

Engineering consequences:

```text
source attribution is mandatory data
consent history is mandatory state
suppression is mandatory state
repeat consent requests are blocked
template identity/contact fields are mandatory
all outbound copy is versioned
```

---

# 32. Source References for the Implementing Agent

Recheck all limits before production.

## Information Regulator / POPIA

Information Regulator direct-marketing guidance:
https://inforegulator.org.za/guidance-notes/

POPIA page and forms:
https://inforegulator.org.za/popia/

2025 amended POPIA Regulations:
https://inforegulator.org.za/wp-content/uploads/2025/04/POPIA-2021-Regulations-FINAL-21-Jan-2025.pdf

POPIA Act:
https://www.gov.za/documents/protection-personal-information-act

## MWEB

Mail sending rules:
https://help.mweb.co.za/articles/mail/about-mail/mail-sending-rules-and-guidelines

Mail application settings:
https://help.mweb.co.za/articles/mail/mail-application-setup/email-settings-for-mail-applications

Mail forwarding:
https://help.mweb.co.za/articles/mail/managing-mail-settings/how-to-setup-mail-forwarding

Content filters:
https://help.mweb.co.za/articles-preview/mail/webmail/content-filtering

## Cloudflare

D1 pricing:
https://developers.cloudflare.com/d1/platform/pricing/

R2 pricing:
https://developers.cloudflare.com/r2/pricing/

Workers TCP sockets:
https://developers.cloudflare.com/workers/runtime-apis/tcp-sockets/

Workers Cron Triggers:
https://developers.cloudflare.com/workers/configuration/cron-triggers/

## Render

Free services:
https://render.com/docs/free

Compute plans:
https://render.com/docs/compute-plans

## xAI

Grok models:
https://docs.x.ai/developers/models

Grok rate limits:
https://docs.x.ai/developers/rate-limits

Pricing:
https://docs.x.ai/developers/pricing

## Google Apps Script

Quotas:
https://developers.google.com/apps-script/guides/services/quotas

URL Fetch:
https://developers.google.com/apps-script/reference/url-fetch

---

# 33. Final Architecture Decision

Use:

```text
Cloudflare R2     = temporary raw/intermediate data
Cloudflare D1     = durable operational state
Cloudflare Worker = orchestration + MWEB sender
Render            = heavier Python processing
Grok              = semantic ambiguity / scoring
Apps Script       = dashboard + human controls
Google Sheets     = reporting, not canonical storage
MWEB              = sender identity + reply mailbox
MWEB forwarding   = replies -> marketing inbox
GitHub            = monorepo + CI/CD + Codex working context
```

The project should optimise for:

```text
maximum validated queue size
minimum paid inference
zero duplicate consent requests
checkpointed processing
replayable failures
controlled MWEB sending
automatic reply forwarding
clear source attribution
minimal long-term personal-data retention
```

That is the MVP target.
