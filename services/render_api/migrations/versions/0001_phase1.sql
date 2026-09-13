CREATE TABLE source_cursors (
 source VARCHAR(32) PRIMARY KEY, cursor JSONB NOT NULL DEFAULT '{}', enabled BOOLEAN NOT NULL DEFAULT FALSE,
 failures INTEGER NOT NULL DEFAULT 0, retry_at TIMESTAMPTZ, last_success_at TIMESTAMPTZ,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE pipeline_state (
 id INTEGER PRIMARY KEY CHECK (id=1), paused BOOLEAN NOT NULL DEFAULT TRUE, updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE processing_jobs (
 id VARCHAR(36) PRIMARY KEY, idempotency_key VARCHAR(180) NOT NULL UNIQUE,
 kind VARCHAR(24) NOT NULL, source VARCHAR(32), status VARCHAR(24) NOT NULL DEFAULT 'QUEUED',
 attempts INTEGER NOT NULL DEFAULT 0, batch_size INTEGER NOT NULL CHECK (batch_size BETWEEN 1 AND 2000),
 cursor_in JSONB, cursor_out JSONB, result JSONB NOT NULL DEFAULT '{}',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), available_at TIMESTAMPTZ NOT NULL DEFAULT now(), completed_at TIMESTAMPTZ
);
CREATE INDEX ix_processing_jobs_status ON processing_jobs(status);
CREATE INDEX ix_processing_jobs_created_at ON processing_jobs(created_at);
CREATE INDEX ix_processing_jobs_available_at ON processing_jobs(available_at);
CREATE INDEX ix_jobs_claim ON processing_jobs(status,available_at);
CREATE TABLE source_records (
 id VARCHAR(36) PRIMARY KEY, source VARCHAR(32) NOT NULL, source_record_id TEXT NOT NULL, source_url TEXT NOT NULL,
 identity_hash VARCHAR(64) NOT NULL, content_hash VARCHAR(64) NOT NULL, text TEXT NOT NULL,
 published_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), status VARCHAR(24) NOT NULL DEFAULT 'PENDING',
 UNIQUE(source,source_record_id)
);
CREATE INDEX ix_source_records_source ON source_records(source);
CREATE INDEX ix_source_records_identity_hash ON source_records(identity_hash);
CREATE INDEX ix_source_records_content_hash ON source_records(content_hash);
CREATE INDEX ix_source_records_created_at ON source_records(created_at);
CREATE INDEX ix_source_records_status ON source_records(status);
CREATE TABLE dedupe_index (key VARCHAR(80) PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE identity_index (
 identity_hash VARCHAR(64) PRIMARY KEY, email_hash VARCHAR(64),
 first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_identity_index_email_hash ON identity_index(email_hash);
CREATE TABLE candidates (
 id VARCHAR(36) PRIMARY KEY, record_id VARCHAR(36) UNIQUE REFERENCES source_records(id) ON DELETE SET NULL,
 source VARCHAR(32) NOT NULL, source_url TEXT NOT NULL, identity_hash VARCHAR(64) NOT NULL,
 excerpt VARCHAR(2000) NOT NULL, contacts JSONB NOT NULL DEFAULT '[]', product_type VARCHAR(24) NOT NULL,
 score INTEGER NOT NULL, status VARCHAR(32) NOT NULL DEFAULT 'CLASSIFY', attempts INTEGER NOT NULL DEFAULT 0,
 available_at TIMESTAMPTZ NOT NULL DEFAULT now(), created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_candidates_source ON candidates(source);
CREATE INDEX ix_candidates_identity_hash ON candidates(identity_hash);
CREATE INDEX ix_candidates_score ON candidates(score);
CREATE INDEX ix_candidates_status ON candidates(status);
CREATE INDEX ix_candidates_available_at ON candidates(available_at);
CREATE INDEX ix_candidates_created_at ON candidates(created_at);
CREATE INDEX ix_candidates_stage_due ON candidates(status,available_at);
CREATE TABLE classification_results (
 id VARCHAR(36) PRIMARY KEY, candidate_id VARCHAR(36) NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
 provider VARCHAR(32) NOT NULL, version VARCHAR(100) NOT NULL, result JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(candidate_id,provider)
);
CREATE INDEX ix_classification_results_candidate_id ON classification_results(candidate_id);
CREATE TABLE email_validation (
 email_hash VARCHAR(64) PRIMARY KEY, status VARCHAR(32) NOT NULL, reason VARCHAR(100) NOT NULL,
 checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_email_validation_status ON email_validation(status);
CREATE INDEX ix_email_validation_checked_at ON email_validation(checked_at);
CREATE TABLE validated_leads (
 id BIGSERIAL PRIMARY KEY, email VARCHAR(320) NOT NULL, email_hash VARCHAR(64) NOT NULL UNIQUE,
 identity_hash VARCHAR(64) NOT NULL UNIQUE, source VARCHAR(32) NOT NULL, source_url TEXT NOT NULL,
 email_source_url TEXT NOT NULL, excerpt VARCHAR(2000) NOT NULL, product_type VARCHAR(24) NOT NULL,
 score INTEGER NOT NULL, status VARCHAR(32) NOT NULL DEFAULT 'VALIDATED',
 validation_status VARCHAR(32) NOT NULL DEFAULT 'DELIVERABLE_DOMAIN', created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_validated_leads_source ON validated_leads(source);
CREATE INDEX ix_validated_leads_score ON validated_leads(score);
CREATE INDEX ix_validated_leads_created_at ON validated_leads(created_at);
CREATE TABLE pipeline_metrics (
 day DATE NOT NULL, source VARCHAR(32) NOT NULL, name VARCHAR(64) NOT NULL, value DOUBLE PRECISION NOT NULL DEFAULT 0,
 PRIMARY KEY(day,source,name)
);
CREATE TABLE api_usage (
 day DATE NOT NULL, provider VARCHAR(32) NOT NULL, units INTEGER NOT NULL DEFAULT 0,
 input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
 estimated_cost DOUBLE PRECISION NOT NULL DEFAULT 0, PRIMARY KEY(day,provider)
);
CREATE TABLE failed_jobs (
 id VARCHAR(36) PRIMARY KEY, job_id VARCHAR(36), source VARCHAR(32), code VARCHAR(100) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_failed_jobs_job_id ON failed_jobs(job_id);
CREATE INDEX ix_failed_jobs_created_at ON failed_jobs(created_at);
INSERT INTO pipeline_state (id,paused) VALUES (1,TRUE);
INSERT INTO source_cursors (source,enabled) VALUES ('bluesky',FALSE),('youtube',FALSE);
