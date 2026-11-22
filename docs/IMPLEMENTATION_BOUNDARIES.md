# Everpay FastAPI — implementation boundaries

## Implemented in this branch

- FastAPI application lifecycle and versioned /api/v1 routing.
- Request IDs and structured JSON logs.
- Explicit CORS allow-list; configure exact trusted browser origins per environment.
- Supabase access-token validation through the Supabase Auth /auth/v1/user endpoint. Authorization must use server-managed app_metadata and explicit policy checks, never user_metadata.
- CockroachDB async connectivity/readiness only. The app runs SELECT 1 and does not create tables, run migrations, or assume ledger/application schema.
- Redis readiness and Celery worker configuration using Redis. Celery Beat has no invented financial schedules.
- Container image, CI test, and environment example.

## Deliberately not implemented

The existing Platform OS client calls /payments/charge, /payments/refund, /payouts/create, /merchants/{id}/summary, /treasury/liquidity, /config/rate-limit/..., and /adaptive-rate-limit/.... They are not implemented as fake success endpoints. Durable implementations require the approved CockroachDB schema, Supabase merchant/profile authorization rules, provider credentials/contracts, and the existing payment orchestration code to be mapped first.

No payment, refund, payout, balance, settlement, pricing, or ledger route may return success until it persists and enforces the approved data model and idempotency/reconciliation rules.

## Required before enabling financial writes

1. Current CockroachDB schema and migration history for the financial system of record.
2. Current Supabase schema for profiles, merchants, memberships, and roles. Supabase remains identity/profile/application data, not the ledger.
3. Approved provider integration contracts, credentials in the deployment secret manager, webhook signing rules, and sandbox evidence.
4. Existing Platform OS payment and merchant request/response contracts mapped endpoint by endpoint.
5. Operational requirements for outbox retention, replay, dead-letter handling, reconciliation, and audit retention.

## Deployment

- DigitalOcean is staging; AWS is production. This repository does not deploy itself.
- Store secrets in the environment/deployment secret manager, never in Git.
- Set CORS_ORIGINS to exact trusted browser origins. Do not use wildcard origins with credentials.
- Set APP_ENV=production in production; interactive API docs are disabled in that mode.
- Use TLS to CockroachDB and Redis. Restrict network access to API and worker services.
- Run schema migrations as a separately reviewed release job only after the schema is approved; this API currently performs no migrations.
