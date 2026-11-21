# Fraud graph writer

This service records its payins and payouts in the same Supabase fraud graph as the Enki
edge functions, so an account seen by either system is one node. The graph schema,
access rules and fingerprint spec live in Enki (`docs/FRAUD_GRAPH.md`, migration
`20261121200000_fraud_graph_event_model.sql`).

## How it works

1. Payment or payout code calls `enqueue_fraud_graph_event(FraudGraphEventIn(...))` once
   the outcome is known.
2. Entities are fingerprinted in-process (`app/fraud_graph/fingerprints.py`, spec v1).
   Raw card numbers, account numbers, wallet addresses, emails and IPs never reach Redis
   or Supabase; only fingerprints, masked hints and non-sensitive attributes are queued.
3. The Celery task `app.tasks.fraud_graph.record_event` (queue `fraud_graph`) calls
   `public.record_fraud_graph_event()` over a verify-full TLS connection.

Recording never raises into the caller. If the key or connection string is missing,
events are skipped with an info log. Connection failures are retried with backoff (up
to 8 times); payloads the database rejects as invalid are logged and dropped. The
database function is idempotent, so retries cannot double count.

```python
from app.fraud_graph import FraudGraphEntityIn, FraudGraphEventIn, enqueue_fraud_graph_event

enqueue_fraud_graph_event(FraudGraphEventIn(
    merchant_id=merchant_id,
    direction="payout",
    source_ref=str(payout_id),
    amount=amount,
    currency="CAD",
    entities=[FraudGraphEntityIn(kind="bank_account", role="payee", value={
        "institution_number": "003", "transit_number": "12345", "account_number": "1234567"})],
    attributes={"status": "processing", "method": "eft"},
))
```

Kinds: `email`, `phone`, `ip`, `device`, `card`, `card_token`, `bank_account`,
`crypto_wallet`. Roles: `payer`, `payee`, `customer`, `device`, `ip`. When `rail` is
omitted it is derived from the payee (payouts) or payer (payins). For stored payout
destinations, `entity_from_details(type, details, network)` returns `(kind, value)`.

## Configuration

| Setting | Value |
| --- | --- |
| `FRAUD_GRAPH_HMAC_KEY` | Same value as the Enki edge-function secret of that name (32+ bytes) |
| `SUPABASE_DATABASE_URL` | Login in `fraud_graph_writer`, `sslmode=verify-full`, `sslrootcert=<Supabase root certificate>` |

1. In Supabase, after the Enki migration:
   `CREATE ROLE everpay_fastapi LOGIN PASSWORD '<long random>' IN ROLE fraud_graph_writer;`
   The role can call the writer and nothing else: it cannot read any table.
2. Download the Supabase root certificate from the project's database SSL settings and mount it
   read-only, e.g. `/run/secrets/supabase-root.crt`. Public CA roots are not used as a fallback.
3. Use the transaction pooler (port 6543) or a direct connection; prepared statements are
   disabled so either works.
4. Keep both values in the secrets manager with the other credentials, not in Git.
5. Run workers with the `fraud_graph` queue (`docker-compose.yml` already does).
6. Verify without writing: `docker compose run --rm --no-deps api python scripts/check_fraud_graph.py`

## Tests

`python -m pytest` runs the spec vectors (shared with Enki), payload privacy, validation,
retry and drop behaviour, and TLS rules. With `FRAUD_GRAPH_TEST_DSN` pointing at a
database that has the Enki migration and the test merchant
`aaaaaaaa-0000-0000-0000-000000000001`, it also records into the real function.
