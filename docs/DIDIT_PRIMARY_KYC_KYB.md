# Didit primary KYC/KYB integration

## Provider policy

Everpay's provider routing is:
- **Primary KYC:** Didit User Verification workflow.
- **Primary KYB:** Didit Business Verification workflow.
- **Primary transaction monitoring/KYT:** Didit Transaction Monitoring.
- **Secondary KYC/KYB:** AiPrise, available only when an authorized operator enables `AIPRISE_SECONDARY_FALLBACK_ENABLED=true`. It is not an automatic failover because a timed-out primary session may already exist and a second provider submission can duplicate checks and costs.

The Didit session API uses `POST /v3/session/` for both KYC and KYB. The configured workflow determines the subject type and checks. A KYC workflow verifies a person; a KYB workflow verifies a business and can collect registry data, UBOs/officers, company AML results, documents and linked KYC sessions for people behind the business.

## API routes

Authenticated:
- `POST /v1/compliance/didit/kyc/sessions` — creates an individual KYC hosted session.
- `POST /v1/compliance/didit/kyb/businesses/{business_id}/sessions` — creates a KYB hosted session after business ownership authorization.
- `GET /v1/compliance/didit/verification-cases/{case_id}` — retrieves the caller's case.
- `POST /v1/compliance/didit/verification-cases/{case_id}/refresh` — fetches the current Didit decision.
- `GET /v1/compliance/didit/provider-status` — reports provider priority and configuration.

Webhook:
- `POST /v1/compliance/didit/verification-webhook` — KYC/KYB session events.
- Existing `POST /v1/compliance/didit/webhook` remains dedicated to transaction monitoring and Travel Rule events.

Use two separate Didit webhook destinations. The identity-session payload shape and transaction-monitoring payload shape differ, and each destination has its own signing secret:
1. Identity: subscribe to `status.updated`, `data.updated`, `business.status.updated`, `business.data.updated`.
2. Transaction monitoring: subscribe to `transaction.created`, `transaction.status.updated`, and `travel_rule.status.updated` when applicable.

Both handlers verify `X-Signature-V2` and signed event freshness. Identity webhook events are deduplicated and held pending if they arrive before the API session response has been mapped to the Everpay case.

## CockroachDB migration

Apply `migrations/20261124_003_didit_kyc_kyb.sql` after the existing Didit transaction monitoring migration. It creates identity-verification case and webhook-event tables only; it does not alter the financial ledger.

## Server environment

Set these values in the FastAPI secret manager, never in Vite:
```dotenv
DIDIT_API_KEY=
DIDIT_WEBHOOK_SECRET=
DIDIT_VERIFICATION_WEBHOOK_SECRET=
DIDIT_KYC_WORKFLOW_ID=
DIDIT_KYB_WORKFLOW_ID=
DIDIT_CALLBACK_BASE_URL=https://api.everpayinc.com/v1
DIDIT_BASE_URL=https://verification.didit.me
```

Use the **stable `workflow_id`**, not a version UUID. Configure the values for the correct Didit application/environment. Sandbox workflow IDs must not be used in production.

## Current Everpay Didit account inspection

The connected Didit organization has a Sandbox application and a Live application. Both currently show existing KYC and KYB workflows as drafts with no live version published:
- Live KYC + AML workflow: `923e0df0-507e-4393-9d13-8a6505e08eed` (draft).
- Live Business Verification (KYB) workflow: `ce0338bf-8d3f-4b3f-9e1f-8958b43e4191` (draft).

Do not place these IDs into production configuration until the corresponding workflows are published in the Live application. Publishing a live workflow may initiate billable verifications when sessions are subsequently created. No Didit workflow was published or any live session initiated as part of this code change.

The Sandbox application also has draft KYC + AML and Business Verification workflows. Publish and test those in Sandbox first, then publish the reviewed workflow versions in Live after sign-off.

## Decision handling

- `APPROVED`: provider screening passed; Everpay's own policy and other required controls must still pass.
- `IN_REVIEW`, `AWAITING_USER`, `RESUBMITTED`, `IN_PROGRESS`: hold onboarding or business activation.
- `DECLINED`: block onboarding unless an authorized compliance reviewer records an approved exception.
- `EXPIRED`, `ABANDONED`, `KYC_EXPIRED`: require re-verification or compliance review.
- A KYB parent is not complete until required UBO/officer linked KYC sessions resolve.

Provider decisions are not payment authorization. Payment and payout release controls must consume the persisted compliance case state inside the trusted transaction orchestration layer. The current FastAPI starter does not yet expose the production merchant/customer onboarding or payment/payout handlers, so this integration creates sessions and tracks decisions but does not claim automatic release blocking in every payment flow.

## Data handling

The integration stores Everpay case correlation, Didit session ID, hosted URL, status and a minimized decision summary (warnings and selected feature statuses). It does not persist raw identity documents, biometric media or full Didit decision payloads. Vendor data is a stable pseudonymous hash of the internal subject/business identifier.

## Frontend

The Platform OS client exposes:
- `fastapi.compliance.didit.createKycSession()`
- `fastapi.compliance.didit.createKybSession(businessId)`
- `fastapi.compliance.didit.getVerificationCase(caseId)`
- `fastapi.compliance.didit.refreshVerificationCase(caseId)`
- `fastapi.compliance.didit.getPrimaryProviderStatus()`

Open the returned `verification_url` in the onboarding flow. Poll case status or refresh after the user returns; signed Didit webhooks remain the primary status synchronization path.
