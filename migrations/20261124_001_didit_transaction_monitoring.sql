-- Apply to CockroachDB before enabling Didit routes.
-- Compliance integration tables only; this does not modify the financial ledger.
CREATE TABLE IF NOT EXISTS didit_transaction_screenings (
    transaction_id STRING(128) PRIMARY KEY,
    didit_transaction_uuid STRING UNIQUE,
    vendor_data STRING(128) NOT NULL,
    lane STRING(16) NOT NULL CHECK (lane IN ('server', 'device')),
    transaction_category STRING(40) NOT NULL,
    transaction_details JSONB NULL,
    subject_snapshot JSONB NULL,
    counterparty_snapshot JSONB NULL,
    status STRING(40) NOT NULL,
    score INT8 NULL,
    severity STRING(16) NULL,
    action_required JSONB NULL,
    provider_response JSONB NULL,
    travel_rule_status STRING(40) NULL,
    last_transaction_event_at TIMESTAMPTZ NULL,
    last_travel_rule_event_at TIMESTAMPTZ NULL,
    sdk_token_issued_at TIMESTAMPTZ NULL,
    sdk_token_expires_at TIMESTAMPTZ NULL,
    last_error STRING(1000) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_didit_screenings_vendor_data ON didit_transaction_screenings (vendor_data, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_didit_screenings_status ON didit_transaction_screenings (status, updated_at DESC);
CREATE TABLE IF NOT EXISTS didit_webhook_events (
    event_id STRING(128) PRIMARY KEY,
    webhook_type STRING(80) NOT NULL,
    payload JSONB NOT NULL,
    processing_status STRING(16) NOT NULL CHECK (processing_status IN ('PENDING', 'PROCESSED')),
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ NULL,
    last_error STRING(1000) NULL
);
CREATE INDEX IF NOT EXISTS idx_didit_webhook_events_pending ON didit_webhook_events (received_at) WHERE processing_status = 'PENDING';
