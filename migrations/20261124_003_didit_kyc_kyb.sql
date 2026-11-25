-- Primary Didit identity and business verification sessions.
-- Apply after 20261124_001_didit_transaction_monitoring.sql.
CREATE TABLE IF NOT EXISTS didit_verification_cases (
    case_id UUID PRIMARY KEY,
    subject_id STRING(128) NOT NULL,
    entity_type STRING(16) NOT NULL CHECK (entity_type IN ('individual', 'business')),
    vendor_data STRING(160) NOT NULL,
    workflow_id STRING(128) NOT NULL,
    didit_session_id STRING(128) NULL UNIQUE,
    verification_url STRING(2048) NULL,
    callback_url STRING(2048) NULL,
    status STRING(32) NOT NULL,
    decision_summary JSONB NULL,
    last_error STRING(500) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_didit_verification_subject
    ON didit_verification_cases (subject_id, entity_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_didit_verification_status
    ON didit_verification_cases (entity_type, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_didit_verification_vendor
    ON didit_verification_cases (vendor_data, created_at DESC);

CREATE TABLE IF NOT EXISTS didit_verification_webhook_events (
    event_id STRING(128) PRIMARY KEY,
    event_type STRING(100) NOT NULL,
    didit_session_id STRING(128) NOT NULL,
    payload JSONB NOT NULL,
    processing_status STRING(16) NOT NULL CHECK (processing_status IN ('PENDING', 'PROCESSED')),
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ NULL
);
CREATE INDEX IF NOT EXISTS idx_didit_verification_events_session
    ON didit_verification_webhook_events (didit_session_id, received_at DESC);
