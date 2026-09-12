# Insurance Lead Engine — Platform Setup & Access Checklist

This file defines what must be created on each external platform, what values the repository needs, and what must **never** be committed to Git.

> Do not put account passwords, API keys, SMTP passwords, `.env` files, `.dev.vars`, `.clasprc.json`, or other secrets into the repository.

---

# 1. Minimum Access Needed for MVP

The MVP needs these external systems:

1. GitHub repository
2. Cloudflare account
   - Workers
   - D1
   - R2
3. Render account
4. xAI / Grok API account
5. Google Sheet + Apps Script project
6. MWEB dedicated mailbox
7. YouTube Data API key if the YouTube collector is enabled
8. Bluesky Jetstream requires no account/API key for the public stream

Recommended setup order:

```text
GitHub
  ↓
Cloudflare R2 + D1
  ↓
Cloudflare Orchestrator Worker
  ↓
Render processing service
  ↓
xAI key
  ↓
Google Sheet + Apps Script
  ↓
MWEB SMTP mailbox + forwarding
  ↓
Source APIs / collectors
```

---

# 2. GitHub

## Create

Create one private repository, for example:

```text
insurance-lead-engine
```

Recommended default branch:

```text
main
```

## Record

```text
GITHUB_REPOSITORY_URL=
GITHUB_DEFAULT_BRANCH=main
```

## GitHub Secrets eventually needed for CI/CD

Cloudflare deployment:

```text
CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_API_TOKEN
```

Optional Apps Script automated deployment:

```text
CLASPRC_JSON
CLASP_JSON
```

Do **not** configure clasp CI until the Apps Script project exists.

## Render

Render can connect directly to GitHub and automatically deploy changes from the selected branch.

Therefore the repository normally does **not** need a Render username/password or Render API token.

---

# 3. Cloudflare

## 3.1 Create an account/project

Enable access to:

```text
Workers
R2
D1
```

Record the Cloudflare Account ID:

```text
CLOUDFLARE_ACCOUNT_ID=
```

This is an identifier, not a password.

---

## 3.2 Create R2 Bucket

Suggested name:

```text
insurance-leads-raw-prod
```

Create in Cloudflare Dashboard or:

```bash
npx wrangler r2 bucket create insurance-leads-raw-prod
```

Record:

```text
R2_BUCKET_NAME=insurance-leads-raw-prod
```

For Worker runtime access, use an R2 **binding**.

Suggested binding:

```text
RAW_BUCKET
```

Do not generate R2 S3 Access Key / Secret Key unless an external non-Worker service specifically requires S3 access.

The proposed architecture does **not** require Render to have an R2 access key.

---

## 3.3 Create D1 Database

Suggested name:

```text
insurance-leads-prod
```

Create:

```bash
npx wrangler d1 create insurance-leads-prod
```

The command returns a database ID.

Record:

```text
D1_DATABASE_NAME=insurance-leads-prod
D1_DATABASE_ID=
```

Suggested Worker binding:

```text
DB
```

### Data location note

Cloudflare D1 currently does not offer an Africa location hint. Do not make an unreviewed data-residency decision purely for latency.

Select the database location/jurisdiction only after the business/compliance owner confirms the acceptable region.

---

## 3.4 Create Cloudflare Workers

Create two Worker services.

### Worker A

```text
lead-orchestrator
```

Responsibilities:

```text
R2 access
D1 access
source/batch state
Render job handoff
dashboard API
pipeline controls
queue controls
```

### Worker B

```text
mweb-sender
```

Responsibilities:

```text
scheduled queue drain
MWEB SMTP connection
SMTP circuit breaker
send-event logging
```

Separating the sender from the orchestrator prevents processing code from automatically obtaining SMTP credentials.

Record final URLs after deployment:

```text
ORCHESTRATOR_BASE_URL=https://lead-orchestrator.<account-subdomain>.workers.dev
SENDER_BASE_URL=https://mweb-sender.<account-subdomain>.workers.dev
```

---

## 3.5 Cloudflare bindings

These are declared in `wrangler.jsonc` / `wrangler.toml`, not `.env`.

Example:

```json
{
  "d1_databases": [
    {
      "binding": "DB",
      "database_name": "insurance-leads-prod",
      "database_id": "<D1_DATABASE_ID>"
    }
  ],
  "r2_buckets": [
    {
      "binding": "RAW_BUCKET",
      "bucket_name": "insurance-leads-raw-prod"
    }
  ]
}
```

Worker bindings mean the runtime does not need D1/R2 passwords.

---

## 3.6 Cloudflare runtime secrets

Create encrypted Worker secrets for sensitive values.

Orchestrator:

```text
RENDER_SHARED_SECRET
APPS_SCRIPT_API_TOKEN
ADMIN_API_TOKEN
```

Sender:

```text
MWEB_SMTP_USERNAME
MWEB_SMTP_PASSWORD
MWEB_FROM_EMAIL
MWEB_FROM_NAME
```

Example command:

```bash
npx wrangler secret put MWEB_SMTP_PASSWORD
```

Never put the real values in `wrangler.jsonc`.

---

## 3.7 Cloudflare deployment API token

Only needed for CI/CD or agent-driven deployment.

Create a narrowly scoped Cloudflare API token.

Typical required capabilities:

```text
Workers Scripts: Write
D1: Write        # only if CI runs migrations/manages D1
Workers R2 Storage: Write  # only if CI manages R2 resources
```

Limit the token to the relevant Cloudflare account.

Record in GitHub Secrets, not the repository:

```text
CLOUDFLARE_API_TOKEN=
CLOUDFLARE_ACCOUNT_ID=
```

Do not use the Cloudflare Global API Key.

---

# 4. Render

## 4.1 Account connection

Create/sign into Render.

Connect the GitHub repository from:

```text
Render → Account Settings → Git Deployment Credentials
```

Do not provide the repo or AI agent with:

```text
Render login email/password
```

---

## 4.2 Create service

Create one Web Service:

```text
Name: insurance-lead-processor
Root directory: services/render_api
Runtime: Python
Branch: main
```

The repository should contain a `render.yaml` where practical.

Render should auto-deploy from GitHub.

Record the resulting public URL:

```text
RENDER_BASE_URL=https://insurance-lead-processor.onrender.com
```

Render automatically exposes values such as its own external URL and service ID at runtime, so the application does not need you to manually duplicate those values unless another service needs them.

---

## 4.3 Render environment variables

Required:

```text
ENVIRONMENT=production

ORCHESTRATOR_BASE_URL=https://lead-orchestrator.<subdomain>.workers.dev
ORCHESTRATOR_SHARED_SECRET=<same value stored in Cloudflare as RENDER_SHARED_SECRET>

XAI_API_KEY=<secret>
XAI_MODEL=<chosen current model>

LOG_LEVEL=INFO
PROCESSING_BATCH_SIZE=1000
```

If YouTube is enabled:

```text
YOUTUBE_API_KEY=<secret>
```

Bluesky:

```text
BLUESKY_JETSTREAM_HOST=jetstream.us-east.bsky.network
BLUESKY_COLLECTION=app.bsky.feed.post
```

No Bluesky API key is required for the public Jetstream feed.

### Important

Render does **not** need:

```text
D1_DATABASE_PASSWORD
R2_SECRET_KEY
MWEB_SMTP_PASSWORD
MWEB_SMTP_USERNAME
```

Render talks to the Cloudflare orchestrator using authenticated HTTP.

---

## 4.4 Render API key

Not required for the MVP if Render deploys automatically from GitHub.

Only create:

```text
RENDER_API_KEY
```

if later automation must use the Render management API to trigger deploys, inspect services, etc.

Never provide the Render account password.

---

# 5. xAI / Grok

## Create

1. Create/sign into xAI Console.
2. Add API credits/billing if required.
3. Open API Keys.
4. Create a dedicated key for this project.

Record only as a secret:

```text
XAI_API_KEY=
```

Configuration:

```text
XAI_MODEL=
```

Do not hard-code the model deeply into application code. Keep it configurable because xAI models/pricing change.

The inference API base URL is:

```text
https://api.x.ai
```

The application should use Bearer-token authentication.

The xAI key belongs on Render, because Render performs semantic classification.

It does not need to exist inside Apps Script or the MWEB Sender Worker.

---

# 6. Google Sheets

## Create spreadsheet

Create one spreadsheet for the operational dashboard.

Suggested name:

```text
Insurance Lead Engine Dashboard
```

Give the AI agent/repository:

```text
GOOGLE_SHEET_URL=
GOOGLE_SPREADSHEET_ID=
```

The spreadsheet ID is the part between `/d/` and `/edit`:

```text
https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit
```

The URL/ID are not secret by themselves, but the spreadsheet must not be publicly shared.

---

# 7. Google Apps Script

## Recommended setup

Use a bound Apps Script project:

```text
Google Sheet
→ Extensions
→ Apps Script
```

This avoids needing a service account for the MVP.

The backend does not directly authenticate to Google Sheets.

Apps Script reads the backend API and writes data into its bound Sheet.

## Record

From Apps Script Project Settings:

```text
APPS_SCRIPT_SCRIPT_ID=
```

Only needed if using `clasp` or automated deployment.

---

## Apps Script Script Properties

In Apps Script, store:

```text
ORCHESTRATOR_BASE_URL
APPS_SCRIPT_API_TOKEN
```

Do not put the API token in a spreadsheet cell.

The value of:

```text
APPS_SCRIPT_API_TOKEN
```

must match the encrypted Cloudflare secret used to authenticate Apps Script requests.

Optional:

```text
DASHBOARD_SHEET_ID=<spreadsheet ID>
```

A bound script can generally use the active spreadsheet instead.

---

## Apps Script Web App URL

Not required for the basic MVP.

Only deploy Apps Script as a Web App if another service needs to call Apps Script.

If later required, record:

```text
APPS_SCRIPT_WEB_APP_URL=
APPS_SCRIPT_DEPLOYMENT_ID=
```

---

# 8. MWEB Mailbox

## Required from the business

Create/provide the dedicated sending mailbox.

Required:

```text
MWEB_SMTP_USERNAME=<full mailbox address>
MWEB_SMTP_PASSWORD=<mailbox password>
MWEB_FROM_EMAIL=<usually same mailbox>
MWEB_FROM_NAME=<approved business display name>
```

MWEB documented SMTP settings:

```text
Host: smtp.mweb.co.za
Port: 587
Authentication: required
Encryption: STARTTLS/TLS
```

Optional later IMAP ingestion:

```text
Host: imap.mweb.co.za
Port: 993
Encryption: SSL/TLS
```

The mailbox password must be stored as a Cloudflare Worker secret.

Do not place it in:

```text
.env.example
GitHub
Google Sheets
Apps Script source
Render
```

unless the architecture changes and the relevant service genuinely requires it.

---

# 9. MWEB Forwarding

Configure this manually in MWEB/Webmail.

Required business value:

```text
MARKETING_FORWARD_EMAIL=
```

Recommended initial behaviour:

```text
Forward inbound replies → marketing inbox
Keep a copy in MWEB → yes
```

No application credential is required for native forwarding.

Optional later IMAP processing can classify responses and maintain automated suppression/consent status.

---

# 10. Campaign / Compliance Configuration

These values are configuration, not platform credentials.

Provide:

```text
RESPONSIBLE_PARTY_NAME=
RESPONSIBLE_PARTY_CONTACT_EMAIL=
RESPONSIBLE_PARTY_CONTACT_PHONE=
PRIVACY_NOTICE_URL=

CAMPAIGN_NAME=
CAMPAIGN_CODE=

COMPLIANCE_APPROVED_TEMPLATE_VERSION=
```

Also provide an approved description for each source.

Example:

```yaml
source_descriptions:
  youtube: "a public YouTube comment/profile"
  bluesky: "a public Bluesky post/profile"
  public_web: "publicly available information at {{source_domain}}"
```

The final language must be approved by compliance.

---

# 11. YouTube Collector

If using YouTube:

1. Create/select Google Cloud project.
2. Enable `YouTube Data API v3`.
3. Create API key.
4. Restrict that API key to the YouTube Data API.
5. Store key only in Render environment variables.

Required:

```text
YOUTUBE_API_KEY=
```

Default quota is finite, so the collector must checkpoint pagination and stop cleanly when quota is exhausted.

Do not place this key in Apps Script or client-side code.

---

# 12. Bluesky Collector

No account/API key is required for public Jetstream.

Configuration:

```text
BLUESKY_JETSTREAM_HOST=jetstream.us-east.bsky.network
BLUESKY_COLLECTION=app.bsky.feed.post
```

Current public Jetstream exposes a persistent WebSocket stream.

The collector must persist its cursor/checkpoint to avoid restarting from the beginning.

---

# 13. Internal Application Endpoints

These endpoints are part of **our** solution.

They do not exist until the repository implements them.

## Cloudflare Orchestrator

### Public-ish operational endpoint

```http
GET /health
```

Returns service health only.

---

### Render service endpoints

Protected using `RENDER_SHARED_SECRET`.

```http
POST /internal/batches/claim
GET  /internal/batches/{batch_id}/input
POST /internal/batches/{batch_id}/complete
POST /internal/batches/{batch_id}/fail

POST /internal/candidates/upsert
POST /internal/metrics/processing
```

Suggested behaviour:

### `POST /internal/batches/claim`

Render asks for the next processable batch.

Response:

```json
{
  "batch_id": "youtube-2026W37-0001",
  "source": "youtube",
  "input_url": "/internal/batches/youtube-2026W37-0001/input",
  "pipeline_stage": "normalize"
}
```

### `GET /internal/batches/{batch_id}/input`

Cloudflare streams the R2 object to Render.

This means Render does not need an R2 credential.

### `POST /internal/batches/{batch_id}/complete`

Render returns:

```json
{
  "batch_id": "...",
  "processed": 1000,
  "accepted": 216,
  "rejected": 784,
  "output": [...]
}
```

Cloudflare persists final operational state and/or writes the returned result to R2/D1.

---

## Apps Script / dashboard endpoints

Protected using a separate `APPS_SCRIPT_API_TOKEN`.

```http
GET  /dashboard/summary
GET  /dashboard/source-stats
GET  /dashboard/queue
GET  /dashboard/failures

POST /admin/pipeline/pause
POST /admin/pipeline/resume

POST /admin/sender/pause
POST /admin/sender/resume
```

Do not use the same token for Render and Apps Script.

---

## Sender

The MWEB Sender Worker mainly runs from a Cloudflare Cron Trigger.

Internal/admin endpoints:

```http
GET  /health
GET  /admin/sender/status
POST /admin/sender/pause
POST /admin/sender/resume
POST /admin/sender/test
```

`/admin/sender/test` must require explicit authentication and should only send to a configured test address.

No anonymous endpoint may enqueue arbitrary email.

---

## Render processing service

Cloudflare calls Render.

```http
GET  /health
POST /jobs/process-batch
```

Example request:

```json
{
  "batch_id": "youtube-2026W37-0001"
}
```

Render then uses `ORCHESTRATOR_BASE_URL` to claim/read/return job data.

This endpoint must authenticate Cloudflare.

Recommended shared-secret header:

```http
Authorization: Bearer <ORCHESTRATOR_SHARED_SECRET>
```

A future upgrade can replace this with signed service tokens.

---

# 14. Shared Secret Generation

Generate independent secrets.

Example:

```bash
openssl rand -hex 32
```

Create at least:

```text
RENDER_SHARED_SECRET
APPS_SCRIPT_API_TOKEN
ADMIN_API_TOKEN
```

Do not reuse one secret everywhere.

---

# 15. Information You Should Collect Into a Private Setup File

The following is what the implementation owner should ultimately have.

## Non-secret configuration

```text
GITHUB_REPOSITORY_URL=
GITHUB_DEFAULT_BRANCH=main

CLOUDFLARE_ACCOUNT_ID=
R2_BUCKET_NAME=
D1_DATABASE_NAME=
D1_DATABASE_ID=
ORCHESTRATOR_BASE_URL=
SENDER_BASE_URL=

RENDER_BASE_URL=

GOOGLE_SHEET_URL=
GOOGLE_SPREADSHEET_ID=
APPS_SCRIPT_SCRIPT_ID=

MWEB_FROM_EMAIL=
MWEB_FROM_NAME=
MARKETING_FORWARD_EMAIL=

RESPONSIBLE_PARTY_NAME=
RESPONSIBLE_PARTY_CONTACT_EMAIL=
RESPONSIBLE_PARTY_CONTACT_PHONE=
PRIVACY_NOTICE_URL=
CAMPAIGN_NAME=
CAMPAIGN_CODE=
```

## Secrets

These should **not** be written into a normal Markdown file committed to Git:

```text
CLOUDFLARE_API_TOKEN
RENDER_SHARED_SECRET
APPS_SCRIPT_API_TOKEN
ADMIN_API_TOKEN

XAI_API_KEY
YOUTUBE_API_KEY

MWEB_SMTP_USERNAME
MWEB_SMTP_PASSWORD
```

Use each platform's encrypted secret store.

---

# 16. Values You Do NOT Need to Give the AI Agent

Do not provide:

```text
Cloudflare account password
Render account password
Google account password
GitHub account password
xAI account password
MWEB master/customer-zone password if different from mailbox password
credit-card details
personal OAuth browser cookies
```

The agent needs service credentials, not ownership credentials.

---

# 17. Suggested Bootstrap Sequence for the AI Agent

Once the values above are available, instruct the implementation agent:

```text
1. Read ARCHITECTURE.md and PLATFORM_SETUP.md.

2. Create `.env.example` containing only variable names and safe defaults.

3. Create Cloudflare Wrangler configs using:
   - D1 binding `DB`
   - R2 binding `RAW_BUCKET`

4. Implement the Cloudflare orchestrator endpoints.

5. Implement Render `/health` and `/jobs/process-batch`.

6. Wire Render to Cloudflare using `RENDER_SHARED_SECRET`.

7. Add xAI integration behind a feature flag.

8. Add the first collector.

9. Add Apps Script dashboard sync.

10. Implement the MWEB sender last and keep production sending disabled.

11. Never commit real secret values.
```

---

# 18. MVP Access Checklist

Before coding the first end-to-end run, obtain:

- [ ] GitHub repository URL
- [ ] Cloudflare Account ID
- [ ] R2 bucket created
- [ ] D1 database created
- [ ] D1 database ID
- [ ] Cloudflare deployment token if CI deploy is desired
- [ ] Render service created/connected to repo
- [ ] Render base URL
- [ ] xAI API key
- [ ] Google Sheet URL
- [ ] Apps Script project created
- [ ] MWEB dedicated mailbox address
- [ ] MWEB mailbox password stored securely
- [ ] Marketing forwarding address
- [ ] Responsible-party/company details
- [ ] Approved privacy notice URL
- [ ] YouTube API key if YouTube is enabled
- [ ] Compliance-approved consent-request template before production sending

That is enough to build the first production-shaped MVP.
