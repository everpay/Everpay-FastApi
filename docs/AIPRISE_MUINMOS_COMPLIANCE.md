# AiPrise + Muinmos compliance integration

## Scope

This integration adds AiPrise KYC/KYB API workflows and a common Everpay compliance-agent catalog. It preserves Didit transaction monitoring/KYT as a separate transaction-time control. All decisions remain subject to Everpay policy and authorized compliance review.

### AiPrise capabilities

- KYC: `POST /run_user_verification` using a dashboard-configured template.
- Hosted React flow: `POST /get_user_verification_url`; Everpay requests the URL server-side and returns it to the authenticated frontend. The current frontend can render it in an iframe using `redirect_uri=iframe`.
- Email intelligence: run a user-verification template configured for email checks, with `user_data.email_address`.
- Phone intelligence: run a user-verification template configured for phone checks, with `user_data.phone_number`.
- One-click KYC: configured inside the KYC template. AiPrise currently documents coverage for Bangladesh NID, Brazil CPF, Kenya passport, Nigeria BVN/NIN, South Africa national ID, and United States SSN (last four or full nine) with the associated data requirements. Coverage is provider-described and must be confirmed for the customer's actual template and plan.
- KYB: `POST /run_business_verification` using a KYB template.
- Persistent business profile: `POST /create_business_profile`; include the website and set `events_callback_url` so website monitoring and profile lifecycle events can be received.
- Website review: configure the AI Website Review Agent in the AiPrise dashboard/template and submit the business website through the documented business `website` field. Website-monitoring events such as `WEBSITE_MONITORING_UPDATED` are routed to manual review by default; they do not auto-decline an existing merchant.

### API and webhook behavior

- API base URL default is sandbox: `https://api-sandbox.aiprise.com/api/v1/verify`.
- Production base URL: `https://api.aiprise.com/api/v1/verify`.
- API authentication: `X-API-KEY` over HTTPS.
- Webhooks: verify `X-HMAC-SIGNATURE` against the exact raw request bytes using HMAC-SHA256 and the AiPrise API private key. Do not parse/re-serialize JSON before signature verification.
- Callback event IDs are deduplicated. When the callback type has no `callback_event_id`, exact raw-body SHA-256 is used for duplicate suppression.
- User and business data is not persisted as a raw provider payload. The integration stores correlation IDs, decision summaries, risk score, and bounded reason text only.
- A provider timeout or ambiguous POST outcome is marked `RECONCILIATION_REQUIRED`; do not blindly repeat a non-idempotent create request.
- `APPROVED` is a provider result, not an Everpay payment authorization. `MANUAL_REVIEW`, unresolved AML matches, failed checks, or website changes must not be treated as approval.

### Routes

Authenticated:
- `POST /v1/compliance/aiprise/users/verification`
- `POST /v1/compliance/aiprise/users/verification-url`
- `POST /v1/compliance/aiprise/users/email-check`
- `POST /v1/compliance/aiprise/users/phone-check`
- `POST /v1/compliance/aiprise/businesses/{business_id}/profile`
- `POST /v1/compliance/aiprise/businesses/{business_id}/verification`
- `GET /v1/compliance/aiprise/cases/{case_id}`
- `GET /v1/compliance/muinmos/agents`
- `GET /v1/compliance/providers/status`

Public provider callbacks (HMAC required):
- `POST /v1/compliance/aiprise/callback`
- `POST /v1/compliance/aiprise/events`

Business endpoints require a matching business ID in Supabase `app_metadata.business_id` or `app_metadata.business_ids`, or an admin/compliance role. If Everpay's production RBAC stores business ownership elsewhere, replace this guard with that authoritative lookup before enabling the route.

## Muinmos agents

The agent catalog and dependency map are registered in `app/integrations/muinmos/agents.py`:

1. Orchestration agent
2. Screening agent
3. Identity verification agent
4. Proof of residence agent
5. KYB agent
6. Client classification agent
7. Suitability & appropriateness agent
8. Document collection agent
9. Client risk assessment agent
10. Perpetual KYC monitoring agent

The AMLR guide is used as a policy-design reference for customer due diligence, beneficial ownership, simplified/enhanced due diligence, ongoing customer and transaction monitoring, PEP/high-risk-country controls, sanctions, suspicious-transaction reporting, record keeping and governance.

**Live Muinmos API execution is intentionally disabled.** The public solution pages and AMLR guide describe capabilities, but do not publish the API base URL, authentication mechanism, per-agent request/response schemas, or webhook signature contract needed for a secure production connector. The `MuinmosClient` fails closed rather than guessing endpoints. The database includes workflow and agent-run state tables so the validated provider contract can be connected without changing the compliance evidence model.

## Environment

Set these server-side in the FastAPI secret manager; never expose them as Vite variables:

```dotenv
AIPRISE_API_KEY=
AIPRISE_WEBHOOK_SECRET=
AIPRISE_BASE_URL=https://api-sandbox.aiprise.com/api/v1/verify
AIPRISE_ENVIRONMENT=sandbox
AIPRISE_KYC_TEMPLATE_ID=
AIPRISE_EMAIL_CHECK_TEMPLATE_ID=
AIPRISE_PHONE_CHECK_TEMPLATE_ID=
AIPRISE_KYB_TEMPLATE_ID=
AIPRISE_CALLBACK_BASE_URL=https://api.everpayinc.com/v1
AIPRISE_TIMEOUT_SECONDS=20
AIPRISE_WEBHOOK_MAX_BODY_BYTES=1048576
```

Set `AIPRISE_WEBHOOK_SECRET` to the AiPrise API private key used by its documented callback HMAC procedure. Confirm with AiPrise whether that is identical to the `X-API-KEY` credential in your account; do not assume they are interchangeable.

## Dashboard setup

1. Create separate KYC, email-check, phone-check and KYB templates in AiPrise.
2. Enable One-Click KYC only for countries and ID types enabled for Everpay's account.
3. Configure the AI Website Review Agent in the KYB/business profile flow and enable website monitoring if included in the plan.
4. Allowlist the exact Everpay frontend origin under AiPrise Dashboard → Settings → IFrame Origin Allowlist.
5. Configure both callback URLs to the two Everpay endpoints above.
6. Subscribe/enable business profile website monitoring events and verify callback HMAC delivery in staging.
7. Apply `migrations/20261124_002_aiprise_compliance_orchestration.sql` after the Didit migration.
8. Test sandbox flows and webhook retries before staging activation.

## Muinmos handoff required

Request from Muinmos:
- API base URL(s) and environment separation.
- API authentication and credential rotation process.
- Per-agent endpoints and exact request/response schemas.
- Webhook event list, signing headers/algorithm, retry policy and event IDs.
- Agent availability by product, jurisdiction and plan.
- Data residency, retention, subprocessors, and audit/export APIs.
- Whether orchestration is called once per onboarding or agents are invoked individually.

## Known integration boundary

The FastAPI starter repository does not expose the authoritative merchant/customer tables or the payment/payout transaction creation handlers. These routes create and track compliance cases, but payment and payout release gates must be wired into the trusted transaction orchestration layer after its real data model is available. No ledger schema is modified.
