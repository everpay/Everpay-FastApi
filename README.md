# Everpay FastAPI

FastAPI backend foundation for Everpay Platform OS. This service is designed to run alongside the existing React/Vite frontend and Supabase functions; it does not replace either application.

## Architecture boundary

- **Supabase Auth** validates user access tokens; Supabase continues to own authentication and user/profile/application data.
- **CockroachDB** is the financial system of record. This branch adds connectivity and readiness checks only; it does not invent ledger tables or run schema migrations.
- **Celery + Redis** provide background task execution. Celery Beat is configured but financial/outbox schedules are intentionally absent until the approved schema and operational contract are available.
- **VGS** remains the tokenization boundary. Do not send or persist raw PAN/CVV through this API.
- **DigitalOcean** is staging; **AWS** is production. Deployments and secrets are managed outside this repository.

## Implemented endpoints

| Endpoint | Purpose |
|---|---|
| GET / | Service identity |
| GET /health/live | Process liveness |
| GET /health/ready | CockroachDB/Redis readiness when configured |
| GET /api/v1/me | Validated Supabase identity (Bearer token required) |

Interactive docs are available at /docs outside production. They are disabled when APP_ENV=production.

## Run locally

1. Create a Python 3.12 environment.
2. Install dependencies with pip install -r requirements.txt.
3. Copy .env.example to .env and set the Supabase URL/key, CockroachDB URL, Redis URL, and exact CORS origins.
4. Start the API with uvicorn app.main:app --reload --port 8000.
5. Start a worker in a separate process with celery -A app.worker.celery_app worker --loglevel=INFO.
6. Start the scheduler only when approved periodic tasks have been added with celery -A app.worker.celery_app beat --loglevel=INFO.

The legacy main:app import remains supported for deployments that still use the original module path.

## Container

Build with docker build -t everpay-api . and run with required environment variables supplied by your secret manager/orchestrator. The image runs as a non-root user and has a liveness health check.

## Tests

Run pytest -q.

## Important: payment routes are not faked

The existing Platform OS frontend has client contracts for charge, refund, payout, merchant summary, treasury liquidity, rate limits, and adaptive rate limits. This branch intentionally does not return simulated success for those operations. Before implementing them, map the current CockroachDB schema, Supabase merchant authorization model, existing frontend request/response contracts, provider contracts, idempotency, reconciliation, and webhook behavior. See docs/IMPLEMENTATION_BOUNDARIES.md.

No database tables are created automatically. No raw card data is stored. No provider credentials belong in source control.
