-- Apply to CockroachDB after 20261124_001_didit_transaction_monitoring.sql.
-- Provider-specific compliance evidence only; never alter the financial ledger here.
CREATE TABLE IF NOT EXISTS compliance_verification_cases (
    case_id UUID PRIMARY KEY,
    subject_id STRING(128) NOT NULL,
    entity_type STRING(16) NOT NULL CHECK (entity_type IN ('individual', 'business')),
    provider STRING(32) NOT NULL,
    workflow_type STRING(48) NOT NULL,
    template_id STRING(128) NOT NULL,
    provider_session_id STRING(128) NULL UNIQUE,
    provider_profile_id STRING(128) NULL,
    status STRING(32) NOT NULL CHECK (status IN (
        'SUBMITTING', 'IN_PROGRESS', 'COMPLETED', 'DECLINED',
        'MANUAL_REVIEW', 'FAILED', 'RECONCILIATION_REQUIRED'
    )),
    decision STRING(32) NULL,
    risk_score INT8 NULL,
    decision_reasons JSONB NULL,
    provider_summary JSONB NULL,
    request_fingerprint STRING(64) NOT NULL,
    last_event_type STRING(100) NULL,
    last_event_at TIMESTAMPTZ NULL,
    last_error_code STRING(80) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_compliance_cases_subject
    ON compliance_verification_cases (subject_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_compliance_cases_status
    ON compliance_verification_cases (provider, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_compliance_cases_profile
    ON compliance_verification_cases (provider_profile_id) WHERE provider_profile_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS compliance_provider_events (
    provider STRING(32) NOT NULL,
    event_id STRING(128) NOT NULL,
    event_type STRING(100) NOT NULL,
    processing_status STRING(16) NOT NULL CHECK (processing_status IN ('RECEIVED', 'PROCESSED', 'UNMATCHED')),
    correlation_id STRING(128) NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ NULL,
    PRIMARY KEY (provider, event_id)
);
CREATE INDEX IF NOT EXISTS idx_compliance_events_status
    ON compliance_provider_events (provider, processing_status, received_at DESC);

-- Agent execution ledger for Muinmos/Everpay orchestration.
-- Agent runs are auditable state records; execution is only enabled for providers
-- whose endpoint/authentication/webhook contracts have been validated.
CREATE TABLE IF NOT EXISTS compliance_workflows (
    workflow_id UUID PRIMARY KEY,
    subject_id STRING(128) NOT NULL,
    entity_type STRING(16) NOT NULL CHECK (entity_type IN ('individual', 'business')),
    jurisdiction STRING(2) NULL,
    product_code STRING(64) NULL,
    status STRING(24) NOT NULL CHECK (status IN ('PLANNED', 'IN_PROGRESS', 'ON_HOLD', 'COMPLETED', 'CANCELLED')),
    policy_version STRING(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_compliance_workflows_subject
    ON compliance_workflows (subject_id, created_at DESC);

CREATE TABLE IF NOT EXISTS compliance_agent_runs (
    run_id UUID PRIMARY KEY,
    workflow_id UUID NOT NULL REFERENCES compliance_workflows(workflow_id),
    agent_key STRING(64) NOT NULL,
    provider STRING(32) NOT NULL,
    status STRING(24) NOT NULL CHECK (status IN (
        'NOT_REQUIRED', 'BLOCKED', 'PENDING', 'RUNNING', 'SUCCEEDED',
        'FAILED', 'MANUAL_REVIEW', 'CONTRACT_PENDING'
    )),
    dependencies JSONB NOT NULL DEFAULT '[]',
    result_summary JSONB NULL,
    started_at TIMESTAMPTZ NULL,
    completed_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workflow_id, agent_key)
);
CREATE INDEX IF NOT EXISTS idx_compliance_agent_runs_workflow
    ON compliance_agent_runs (workflow_id, status);
