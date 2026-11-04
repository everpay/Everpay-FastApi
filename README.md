# Everpay FastAPI + Celery foundation

Initial deployable foundation for Everpay:
- FastAPI liveness and readiness endpoints
- Celery task processing with Redis as broker
- Dockerfile and Docker Compose
- Non-root application container
- Redis health check and isolated internal network
- Deployment guidance for DigitalOcean staging and AWS Ireland

This repository began as a minimal FastAPI starter. This branch adds the deployment foundation and a worker smoke-test task. It does not yet implement Everpay's financial ledger, CockroachDB repositories, transactional outbox, payment orchestration, processor adapters, webhook signature verification, KYC/KYB, VGS or Supabase integration. Do not use it for live payment traffic or cardholder data.

See docs/DEPLOYMENT.md for the deployment and security runbook.

## Local run

1. Copy .env.example to .env and set a unique Redis password.
2. Start with docker compose up -d --build.
3. Check http://127.0.0.1:8000/health/live and /health/ready.
4. Review worker logs with docker compose logs -f worker.
