# Didit Transaction Monitoring — AML / KYT

## Integration scope

This integration supports the Didit transaction server lane, device SDK token lane, and signed webhook status updates. It is a compliance screening integration, not a payment processor and not a replacement for Everpay authorization, ledger, sanctions policy, or settlement controls.

## Server-side secrets

Set these values only in the FastAPI service environment or its secret manager:

- DIDIT_API_KEY — full Didit API key.
- DIDIT_WEBHOOK_SECRET — webhook signing secret.
- DIDIT_BASE_URL — defaults to https://verification.didit.me.
- DIDIT_TIMEOUT_SECONDS — defaults to 12.
- DIDIT_SDK_TOKEN_TTL_SECONDS — defaults to 900, allowed range 60–86400.
- DIDIT_SDK_TOKEN_MAX_PER_MINUTE — defaults to 5 per authenticated user.
- DIDIT_WEBHOOK_TOLERANCE_SECONDS — defaults to 300.
- DIDIT_WEBHOOK_MAX_BODY_BYTES — defaults to 262144.

The Didit settings loader reads .env.local for local development. Keep common application settings (including DATABASE_URL, SUPABASE_URL, SUPABASE_ANON_KEY and REDIS_URL) in .env or process environment; put DIDIT_API_KEY and DIDIT_WEBHOOK_SECRET in .env.local. Both files are ignored by Git. Never prefix Didit secrets with VITE_ and never return the API key to a browser or mobile client.

## CockroachDB migration

Apply migrations/20261124_001_didit_transaction_monitoring.sql before enabling the integration. It creates two compliance-specific tables and does not alter the financial ledger:

- didit_transaction_screenings stores local transaction mapping, Didit UUID, lane, subject and counterparty snapshots, screening decision, score, severity, action-required payload and Travel Rule status.
- didit_webhook_events stores signed event payloads and a durable unique event_id for idempotent webhook handling.

The API does not create these tables at startup. Apply and verify the migration in staging before production.

## Server lane — POST /v3/transactions/

Use DiditMonitoringService.screen_server_transaction() from the backend payment, ledger or payout orchestration after loading the source transaction and customer/beneficiary from trusted Everpay records. The browser must not supply the authoritative amount, currency, direction, counterparty or subject identity. The server service reserves the local transaction ID before the provider call and does not blindly retry a non-idempotent create.

Example call (all transaction/customer/beneficiary values below must originate from trusted backend records):

~~~python
from app.integrations.didit.schemas import ServerTransactionRequest
from app.integrations.didit.service import DiditMonitoringService

screening_request = ServerTransactionRequest(
    transaction_id=transaction.id,
    transaction_category="finance",
    transaction_details={
        "direction": "outbound",
        "amount": transaction.amount,
        "currency": transaction.currency,
        "currency_kind": "fiat",
    },
    subject={
        "entity_type": "individual",
        "vendor_data": customer.id,
        "full_name": customer.full_name,
    },
    counterparty={
        "full_name": beneficiary.name,
        "payment_method": {
            "method_type": "bank_account",
            "account_id": beneficiary.account_reference,
        },
    },
)
decision = await DiditMonitoringService().screen_server_transaction(screening_request)
~~~

The service inserts a local reservation before making the non-idempotent Didit POST. It expects the response to contain uuid, txn_id and status. It does not blindly retry. A local duplicate, Didit 400/409, timeout, 5xx or other ambiguous result requires reconciliation before any retry. The current service marks an uncertain submission RECONCILIATION_REQUIRED.

For crypto, set currency_kind to crypto, use the ticker only in currency, and supply crypto_params.crypto_chain. When a payment_reference_id hash is supplied, the crypto wallet method is required on the counterparty for outbound or subject for inbound. Supported chains are validated against the values in the supplied Didit contract.

## Device lane — SDK token

Before a user can request a device token, a trusted backend flow must register the actual Everpay transaction using:

~~~python
from app.integrations.didit.schemas import TransactionDetails
from app.integrations.didit.service import DiditMonitoringService

await DiditMonitoringService().register_device_transaction(
    transaction_id=transaction.id,
    vendor_data=str(authenticated_user.id),
    transaction_details=TransactionDetails(
        direction="outbound",
        amount=transaction.amount,
        currency=transaction.currency,
        currency_kind="fiat",
    ),
    full_name=authenticated_user.full_name,
)

# Import TransactionDetails from app.integrations.didit.schemas.
~~~

Then the signed-in client calls POST /api/v1/compliance/didit/sdk-token with JSON body containing only transaction_id. Supabase Bearer authentication identifies the user; the endpoint ignores any client-supplied user ID. It verifies that the transaction was registered to that same user and that no unexpired SDK token has already been issued, applies a Redis rate limit, then requests a 900-second Didit SDK token with max_uses=1. The token expiry is persisted before the token is returned. A second token cannot be minted while the previous token remains unexpired. The response has Cache-Control: no-store.

Install @didit-protocol/sdk-web in the frontend and submit with DiditSdk.shared.submitTransaction. Set txnId to the exact transaction ID registered by the backend. Device-lane subject identity is supplied by Didit's token; do not send subject.externalUserId. The API key must never be included in frontend build variables.

~~~typescript
import { DiditSdk } from "@didit-protocol/sdk-web";

const result = await DiditSdk.shared.submitTransaction({
  transactionToken: sdkToken,
  transaction: {
    txnId: transaction.id,
    category: "finance",
    details: {
      direction: "out",
      amount: transaction.amount,
      currency: transaction.currency,
      currencyType: "fiat",
    },
    subject: { type: "individual", fullName: currentUser.fullName },
  },
});

if (result.actionRequired?.type === "verification_session") {
  DiditSdk.shared.startVerification({ url: result.actionRequired.url });
}
~~~

Important: the SDK token binds vendor_data to the user; it does not cryptographically bind the transaction ID or transaction amount. Therefore, the client SDK result is not sufficient authorization to move funds. Everpay must match the exact local transaction ID and authoritative transaction details, and the backend must use the stored Didit decision/webhook status before allowing the underlying payment, ledger or payout action.

## Webhook receiver

Configure Didit to POST to /api/v1/compliance/didit/webhook. Subscribe to transaction.created and transaction.status.updated. Also subscribe to travel_rule.status.updated when using Travel Rule transfers.

The receiver:

1. Reads the request body and enforces a maximum size.
2. Parses the JSON and verifies X-Signature-V2 with HMAC-SHA256 over canonicalized JSON. Canonicalization recursively sorts object keys, shortens integral floats, preserves array order, and emits compact UTF-8 JSON.
3. Checks freshness against the signed body timestamp. It deliberately ignores the unsigned X-Timestamp header.
4. Persists event_id under a CockroachDB primary key and updates the screening record in the same database transaction.
5. Acknowledges already-processed duplicate events without applying them twice. Database errors return 503 so Didit can retry.

A correctly signed event with no local transaction mapping is stored as PENDING for reconciliation and returns a successful acknowledgement. Do not discard it. A status event is applied only when its signed timestamp is not older than the last event applied to that screening record.

## Decision policy

- APPROVED: Didit approved the screening at that moment. Everpay's own fraud, sanctions, merchant and payment policies still apply.
- IN_REVIEW: hold the transaction flow and surface it for authorized compliance review.
- AWAITING_USER: hold the transaction flow and surface the action_required step to the user.
- DECLINED: block the transaction under Everpay policy. Do not capture, release or pay out.
- action_required: pass the supplied action URL/type to the appropriate app flow. Verification-session actions need the app's Didit SDK or browser handoff; wallet-ownership action is handled by the SDK as documented.

Webhook status changes can supersede the synchronous decision. The backend decision, not a client-only state, governs the transaction. Do not interpret a missing webhook as approval.

## Operational cases from the supplied Didit contract

- Sandbox applications return APPROVED with score 0 and do not execute rules or send webhooks. This is not proof of live screening.
- A 403 insufficient_credits response means screening cannot be completed; alert operations and hold screening-dependent activity.
- A blocklisted vendor_data can return 400. Investigate; do not substitute a different user identifier.
- The webhook does not include vendor_data. Map txn_id or Didit transaction UUID to the local screening record.
- Store only screening metadata and necessary provider response fields. Never send or store raw PAN/CVV.

## Deployment checklist

- Add DIDIT_API_KEY and DIDIT_WEBHOOK_SECRET to the FastAPI secret manager, not frontend variables.
- Apply the CockroachDB migration in staging.
- Configure the Didit webhook URL and event subscriptions.
- Set the exact frontend origin in CORS_ORIGINS and ensure Redis is available for SDK token rate limiting.
- Verify signature success, tampered signature rejection, stale timestamp rejection, duplicate event handling, status update ordering, and missing transaction mapping.
- Run a live non-sandbox test transaction with Didit and confirm synchronous response plus webhook update before enabling a payment hold/release policy.
