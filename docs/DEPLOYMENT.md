# Everpay Celery deployment foundation

## Scope

This branch adds a first deployable foundation: FastAPI, Celery, Redis broker, Docker Compose, non-root application container and health checks.

This is not the complete Everpay payment backend. It does not yet implement the financial ledger, CockroachDB repositories, transactional outbox, processor adapters, payment authorization, webhook verification, KYC/KYB, VGS integration, Supabase integration, TLS ingress, backups or PCI validation. Do not connect real cardholder data or production payment credentials to this foundation.

CockroachDB is intentionally external to Compose. Provision separate staging and production clusters/databases with TLS and backups. Never run a single-node insecure CockroachDB container on a public server.

## Server baseline

- Ubuntu 24.04 LTS with current security updates.
- Docker Engine and Docker Compose plugin.
- SSH key authentication; disable password SSH only after verifying key access.
- Cloud firewall: allow TCP 22 only from administrator IPs, and TCP 80/443 as needed. Do not expose Redis 6379, CockroachDB 26257, API 8000 or Celery management interfaces.
- Cloudflare DNS/WAF in front of the origin. API container binds to localhost; use a host reverse proxy with TLS.
- Staging data only; never use live PAN/CVV, production credentials or production customer data.

## Configure and start

1. Copy .env.example to .env.
2. Generate a unique long Redis password and set it in REDIS_PASSWORD and in the password portion of REDIS_URL.
3. Set APP_ENV=staging.
4. Keep .env out of Git and restrict it to the deployment account.
5. Start: docker compose up -d --build
6. Inspect: docker compose ps
7. Logs: docker compose logs --tail=100 api worker redis

API binds to 127.0.0.1:8000. Put Nginx or Caddy in front for HTTPS; do not expose port 8000 publicly.

## Verify

- curl -fsS http://127.0.0.1:8000/health/live
- curl -fsS http://127.0.0.1:8000/health/ready
- docker compose exec worker celery -A app.celery_app:celery_app inspect ping
- docker compose exec worker celery -A app.celery_app:celery_app call app.tasks.health.worker_health

The Celery task is a smoke test only and has no financial side effects.

## Scheduler

Start Celery Beat only after scheduled jobs exist, and run one active scheduler per schedule domain:
docker compose --profile scheduler up -d beat

## Staging vs production

Use separate Redis, CockroachDB databases, secrets, DNS names, service accounts and backups. Do not promote staging data to production. Use immutable image tags tied to Git commits for production releases.

## DigitalOcean staging

Low-cost integration-test topology: one 2-vCPU / 4-GiB Ubuntu Droplet for API, one Celery worker, Redis and reverse proxy; use a separately managed CockroachDB staging cluster. This is for low-volume staging only, not highly available payment production. Increase RAM if worker/API memory pressure appears. Apply a DigitalOcean Cloud Firewall as above.

Steps:
1. Create a Droplet in a region close to the engineering team or test partners; Ubuntu 24.04 LTS, Basic shared CPU, 2 vCPU / 4 GiB RAM, SSH key, backups optional for disposable staging (database backups must still exist).
2. Create a Cloud Firewall: inbound SSH from your admin IP only; HTTP/HTTPS as required; no inbound Redis, database or API port.
3. SSH into the Droplet and install Docker Engine plus the Compose plugin using Docker's official Ubuntu instructions.
4. Clone this branch, copy .env.example to .env, set unique staging secrets and start the stack.
5. Install Nginx or Caddy on the host, proxy HTTPS to 127.0.0.1:8000, and issue a TLS certificate. Point a staging DNS hostname through Cloudflare.
6. Verify health endpoints, Celery worker ping and logs. Keep staging isolated from production.

## AWS Ireland least-cost pilot

Region: eu-west-1 (Ireland). Cheapest practical always-on pilot: one Graviton EC2 instance (t4g.small for a light test, t4g.medium if the API and worker need more memory), Ubuntu 24.04 ARM64, Docker Compose and private Redis. Use a separate TLS-enabled CockroachDB cluster. This is a cost-minimized pilot, not high availability.

Steps:
1. Set AWS region to Europe (Ireland), eu-west-1.
2. Launch Ubuntu 24.04 ARM64 EC2 Graviton instance. Use t4g.small only for a light pilot; prefer t4g.medium for a more comfortable single-node API + worker test.
3. Use one small gp3 EBS volume sized for OS, Docker images and logs; do not store durable financial data only on instance disk.
4. Create a security group allowing SSH only from administrator IPs and 80/443 only as needed. Do not open 6379, 26257 or 8000.
5. Attach an IAM instance role with least privilege for required AWS services; do not store AWS access keys on the server.
6. Install Docker and Compose, clone the deployment branch, configure environment secrets, and start the Compose stack.
7. Put Nginx/Caddy on the host for TLS behind Cloudflare, or use an AWS load balancer only when its added fixed cost is justified.
8. Configure AWS Budgets alerts, CloudWatch basic monitoring, encrypted EBS snapshots and tested recovery.
9. Keep CockroachDB, Redis and backups private; separate API and worker nodes as traffic grows.

## Production caveat

One EC2 instance is a single point of failure. For real payment production, separate API and worker capacity, use multiple availability zones for critical dependencies, managed secrets, tested backups and a regional CockroachDB topology. Do not use Spot for always-on payment services.

## Cost controls

- Set AWS and DigitalOcean budget alerts before launch.
- Avoid NAT Gateway in a single-instance pilot because of its fixed hourly and data-processing charges; do not expose private services publicly as a workaround.
- Avoid a public Application Load Balancer for a single small pilot unless required; it adds fixed cost.
- Monitor IPv4, snapshots, backups, transfer, EBS, CloudWatch logs and database charges.
- Test all images and dependencies for linux/arm64 before choosing Graviton.

## PCI boundary

Cloud-provider infrastructure compliance does not certify Everpay's application. Validate provider service scope, responsibility matrix, acquiring requirements and PCI scope with Everpay's QSA/acquirer. Keep raw PAN/CVV outside Everpay services where the VGS/processor integration permits; tokenization does not automatically remove every system from PCI scope.

## Next engineering increments

1. CockroachDB connectivity and Alembic migrations after confirming the supported driver strategy.
2. Durable payment state and transactional outbox.
3. Outbox dispatcher and idempotent Celery task base.
4. Processor adapters and signed webhook ingestion.
5. Duplicate-delivery, timeout, retry, reconciliation and recovery tests.
6. Secrets management, TLS, monitoring, alerting, backup/restore and security hardening.
