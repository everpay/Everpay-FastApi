# Everpay FastAPI

FastAPI backend for Everpay Platform OS. It runs alongside the existing React/Vite frontend and Supabase functions; it does not replace either application.

## Architecture

- Supabase Auth validates user access tokens; Supabase continues to own authentication and user/profile/application data.
- CockroachDB is the financial system of record. The API does not auto-create tables or run migrations at startup.
- Celery + Redis provide background task execution.
- VGS remains the tokenization boundary. Do not send or persist raw PAN/CVV through this API.
- DigitalOcean is staging; AWS is production.

## Endpoints

| Endpoint | Purpose |
|---|---|
| GET / | Service identity |
| GET /health/live | Process liveness |
| GET /health/ready | CockroachDB/Redis readiness |
| GET /api/v1/me | Validated Supabase identity |
| POST /api/v1/compliance/didit/sdk-token | Authenticated, transaction-bound Didit device token |
| POST /api/v1/compliance/didit/webhook | Didit signed webhook receiver (hidden from public OpenAPI) |

Interactive docs are available at /docs outside production and disabled in production.

## Didit transaction monitoring

See docs/DIDIT_TRANSACTION_MONITORING.md for the server lane, device lane, webhook verification, CockroachDB migration, and decision-handling requirements. Apply migrations/20261124_001_didit_transaction_monitoring.sql before enabling the integration. Set DIDIT_API_KEY and DIDIT_WEBHOOK_SECRET only in the backend environment or secret manager. Local development keeps common application settings in ignored .env and Didit credentials in ignored .env.local.

The server lane is a backend service method, not a public client endpoint. Call it only after the payment/ledger/payout service has loaded and validated the source transaction. Device SDK tokens are minted only for a pre-registered transaction belonging to the authenticated user.

## Run locally

1. Use Python 3.12 and install dependencies with pip install -r requirements.txt.
2. Copy .env.example to .env for common application settings. Keep DIDIT_API_KEY and DIDIT_WEBHOOK_SECRET in .env.local (the Didit settings loader reads both files). Never commit either file.
3. Apply the approved CockroachDB migrations in order.
4. Start the API with uvicorn app.main:app --reload --port 8000.
5. Start a worker in a separate process with celery -A app.worker.celery_app worker --loglevel=INFO.

## Tests

Run pytest -q.

## Financial route boundary

Existing Platform OS contracts include charge, refund, payout, merchant summary, treasury liquidity, rate limits and adaptive rate limits. This repository does not fake success for those operations. Durable financial endpoints require the current approved CockroachDB schema, Supabase merchant authorization model, provider contracts, idempotency, reconciliation and webhook semantics. See docs/IMPLEMENTATION_BOUNDARIES.md.
