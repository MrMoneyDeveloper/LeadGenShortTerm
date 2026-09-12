# LeadGenShortTerm — MVP Data Architecture

## Purpose

Build a low-cost, checkpointed lead-processing engine for short-term-insurance prospect research.

The first phase is intentionally **data-first**:

1. collect public records;
2. process them quickly;
3. discard low-value material;
4. retain only qualified candidates, validated contact data, hashes, and pipeline state;
5. postpone outbound email until the lead engine has proven useful.

---

# 1. High-level flow

```text
SOURCE ADAPTERS
Bluesky / YouTube / approved public web
        |
        v
RENDER PYTHON SERVICE
        |
        +--> normalize
        +--> deterministic filter
        +--> dedupe
        +--> local score / classifier
        +--> Grok for ambiguous candidates
        +--> email discovery / validation
        |
        v
RENDER POSTGRESQL
        |
        +--> source cursors
        +--> processing jobs
        +--> candidate state
        +--> dedupe hashes
        +--> validated leads
        +--> metrics
        |
        v
LATER: APPS SCRIPT / SHEETS DASHBOARD
```

Cloudflare R2/D1 is not required for this MVP. It can be introduced if/when the free Render database or processing model becomes inadequate.

---

# 2. Core design decision: process fast, retain selectively

The system should not act as a raw-data archive.

For each batch:

```text
pull
  ↓
normalize
  ↓
filter
  ↓
dedupe
  ↓
score
  ↓
classify uncertain records
  ↓
email discovery / validation
  ↓
persist accepted candidate state
  ↓
delete/reduce rejected raw content
```

Recommended retention:

| State | Retention |
|---|---|
| Raw record | 1–7 days |
| Rejected record | Delete after processing |
| Candidate | Until qualification finishes |
| Validated lead | Through campaign/export lifecycle |
| Dedupe hash | Long-term |
| Suppression hash | Long-term once outbound phase exists |

The database must store enough source evidence to explain why a prospect qualified without retaining every unnecessary field from the original scrape.

---

# 3. Data-volume target

Target acquisition throughput:

```text
~200,000 raw records / 7 days
~28,600 records/day
```

This should be achieved with small, checkpointed batches rather than long jobs.

Initial batch size:

```text
500–1,000 records
```

Every batch must have:

```text
batch_id
source
cursor_in
cursor_out
record_count
status
attempt_count
created_at
completed_at
```

Retries must be idempotent.

---

# 4. Source adapters

Use a common source interface.

```python
class SourceAdapter:
    def collect(self, cursor: str | None, limit: int):
        ...
```

Return:

```python
class BatchResult:
    records: list
    next_cursor: str | None
    exhausted: bool
    metrics: dict
```

Initial adapters:

```text
collectors/
  bluesky/
  youtube/
  public_web/
```

Each adapter owns its own cursor and enabled/disabled state.

One failed source must not reset another.

---

# 5. Configuration-driven intent rules

Do not hard-code search terms or scoring rules inside scrapers.

Suggested files:

```text
config/
  sources.yaml
  keywords.yaml
  scoring.yaml
  runtime.yaml
  retention.yaml
```

Example `keywords.yaml`:

```yaml
motor:
  explicit:
    - car insurance
    - vehicle insurance
    - motor insurance
    - insurance quote

  intent:
    - looking for insurance
    - need insurance
    - recommend insurance
    - switch insurance
    - premium increased
    - insurance too expensive
    - cheaper insurance

  context:
    - bought a car
    - new car
    - first car
    - financed car

negative:
  - insurance broker
  - insurance agent
  - insurance vacancy
  - insurance job
  - insurance news
  - advertisement
  - sponsored
```

---

# 6. Deterministic scoring

Starting example only:

```text
+35 explicit quote request
+30 asks for recommendation
+25 wants to switch insurer
+20 premium complaint
+15 recent vehicle purchase
+15 South Africa signal
+10 named insurer

-60 broker/agent sales content
-50 job/recruitment
-50 advertisement
-40 news/press
```

Suggested stages:

```text
score < 25
  -> reject

score 25–84
  -> local classifier / Grok depending on confidence

score >= 85
  -> accept without Grok if all other rules pass
```

Thresholds must be calibrated against real labelled samples.

---

# 7. Local classifier

After enough labelled data exists, train a lightweight local model before using Grok heavily.

Recommended first implementation:

```text
TF-IDF
+
Logistic Regression
```

Suggested labels:

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

Store classifier/model version on each scored candidate.

---

# 8. Grok / xAI responsibilities

Grok should only perform semantic classification where rules/local models are uncertain.

Expected structured output:

```json
{
  "is_short_term_insurance_relevant": true,
  "intent_level": "HIGH",
  "product": "MOTOR",
  "is_consumer": true,
  "is_advertisement": false,
  "is_broker_or_agent": false,
  "south_africa_signal": true,
  "score": 92,
  "reason": "User is explicitly asking for cheaper motor insurance after a premium increase."
}
```

Do not use Grok for:

```text
email syntax
DNS lookup
MX lookup
duplicate lookup
simple keyword presence
```

Those are deterministic tasks.

---

# 9. Email discovery and validation

Email handling should follow deterministic gates:

```text
email found
  ↓
normalize
  ↓
syntax check
  ↓
domain exists
  ↓
MX exists
  ↓
disposable-domain check
  ↓
dedupe
  ↓
validated candidate
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

MX existence does not prove a specific mailbox exists.

Avoid aggressive SMTP mailbox probing.

---

# 10. Suggested PostgreSQL schema

Minimal MVP tables:

```sql
CREATE TABLE source_cursors (
    source TEXT PRIMARY KEY,
    cursor TEXT,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE processing_jobs (
    batch_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    cursor_in TEXT,
    cursor_out TEXT,
    record_count INTEGER DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE identity_index (
    identity_hash TEXT PRIMARY KEY,
    email_hash TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE candidates (
    lead_id UUID PRIMARY KEY,
    source TEXT NOT NULL,
    source_record_id TEXT,
    source_url TEXT,
    display_name TEXT,
    public_handle TEXT,
    intent_excerpt TEXT,
    product_type TEXT,
    deterministic_score INTEGER,
    ml_score DOUBLE PRECISION,
    llm_score INTEGER,
    final_score INTEGER,
    email TEXT,
    email_hash TEXT,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_candidates_email_hash
ON candidates(email_hash)
WHERE email_hash IS NOT NULL;
```

Do not store giant raw payloads in `candidates`.

---

# 11. Render API shape

First useful endpoints:

```http
GET  /health
POST /jobs/run-batch
GET  /jobs/{batch_id}
GET  /metrics/summary
GET  /leads/validated
GET  /export/validated.csv
```

Possible source-control endpoints later:

```http
POST /sources/{source}/enable
POST /sources/{source}/disable
GET  /sources/status
```

Do not expose destructive/admin endpoints without authentication.

---

# 12. Environment variables for data phase

Minimum planned values:

```text
ENVIRONMENT=development
LOG_LEVEL=INFO
DATABASE_URL=

XAI_API_KEY=
XAI_MODEL=

YOUTUBE_API_KEY=

BLUESKY_JETSTREAM_HOST=jetstream.us-east.bsky.network
BLUESKY_COLLECTION=app.bsky.feed.post

PROCESSING_BATCH_SIZE=500
RAW_RETENTION_DAYS=3
DELETE_REJECTED_RAW=true
```

`DATABASE_URL` should come from Render when PostgreSQL is linked.

No MWEB variables are required in Phase 1.

---

# 13. Apps Script later

Apps Script is introduced only after the backend produces useful validated data.

Responsibilities:

```text
scheduled trigger calls
dashboard refresh
source metrics
validated-lead export
daily CSV/Sheet backup
manual review
```

Sheets should contain values/exports rather than formula-heavy processing logic.

---

# 14. Safety / failure controls

The application must have:

```text
per-source enable/disable flags
batch retry limits
exponential backoff
API quota handling
maximum batch size
maximum Grok candidates per run/day
Postgres-size monitoring
cleanup jobs
export before destructive cleanup
```

No collector should loop indefinitely without moving its cursor.

---

# 15. MVP acceptance criteria

The data engine is considered proven when one source can reliably perform:

```text
collect 500–1,000 records
  ↓
checkpoint source cursor
  ↓
normalize
  ↓
filter
  ↓
dedupe
  ↓
score
  ↓
Grok uncertain cases only
  ↓
validate discovered email
  ↓
store useful candidate
  ↓
export validated records
```

Then scale volume gradually.

Do not add email delivery until this milestone is reliable and the lead quality has been manually reviewed.

---

# 16. Why not Cloudflare first?

Cloudflare R2/D1 remains a valid future architecture, but the MVP intentionally avoids it because:

- the immediate question is lead quality, not distributed storage;
- R2 requires enabling billable usage beyond the included free allowance;
- Render already hosts the Python processing service;
- one Postgres database is simpler for an AI coding agent to reason about;
- fewer moving parts means faster proof of concept.

If the MVP proves value, migration can happen later with explicit retention and cost controls.

---

# 17. Next implementation order

```text
1. repository skeleton
2. Render FastAPI service
3. Render PostgreSQL connection + migrations
4. source cursor/job tables
5. Bluesky collector
6. deterministic filter/scoring
7. dedupe
8. Grok structured classifier
9. email validation
10. validated export
11. YouTube adapter
12. Apps Script dashboard/backup
13. only then outbound-email architecture
```
