import json
from decimal import Decimal, InvalidOperation
from typing import Any
from sqlalchemy import text
from app.core.db import get_engine

class DiditRepositoryError(RuntimeError):
    pass

class DiditRepository:
    def _engine(self):
        engine = get_engine()
        if engine is None:
            raise DiditRepositoryError("CockroachDB is not configured")
        return engine

    async def reserve_server_transaction(self, request: dict[str, Any]) -> dict[str, Any] | None:
        query = text("""INSERT INTO didit_transaction_screenings
            (transaction_id, vendor_data, lane, transaction_category, transaction_details,
             subject_snapshot, counterparty_snapshot, status, created_at, updated_at)
            VALUES (:transaction_id, :vendor_data, 'server', :category, CAST(:details AS JSONB),
                    CAST(:subject AS JSONB), CAST(:counterparty AS JSONB), 'SUBMITTING', now(), now())
            ON CONFLICT (transaction_id) DO NOTHING RETURNING transaction_id""")
        async with self._engine().begin() as conn:
            inserted = (await conn.execute(query, {
                "transaction_id": request["transaction_id"], "vendor_data": request["subject"]["vendor_data"],
                "category": request["transaction_category"], "details": json.dumps(request["transaction_details"]),
                "subject": json.dumps(request["subject"]),
                "counterparty": json.dumps(request.get("counterparty")) if request.get("counterparty") else "null",
            })).first()
            if inserted:
                return None
            row = (await conn.execute(text("""SELECT transaction_id, didit_transaction_uuid, status, score,
                severity, action_required, lane, vendor_data FROM didit_transaction_screenings
                WHERE transaction_id=:transaction_id"""), {"transaction_id": request["transaction_id"]})).mappings().first()
            return dict(row) if row else None

    async def save_provider_decision(self, transaction_id: str, decision: dict[str, Any]) -> None:
        query = text("""UPDATE didit_transaction_screenings SET didit_transaction_uuid=:uuid,
            status=:status, score=:score, severity=:severity,
            action_required=CAST(:action_required AS JSONB), provider_response=CAST(:provider_response AS JSONB),
            updated_at=now() WHERE transaction_id=:transaction_id""")
        async with self._engine().begin() as conn:
            result = await conn.execute(query, {
                "uuid": decision.get("uuid"), "status": decision.get("status", "IN_REVIEW"),
                "score": decision.get("score"), "severity": decision.get("severity"),
                "action_required": json.dumps(decision.get("action_required")) if decision.get("action_required") is not None else "null",
                "provider_response": json.dumps(decision), "transaction_id": transaction_id,
            })
            if result.rowcount != 1:
                raise DiditRepositoryError("Reserved Didit screening record was not found")
        await self.replay_pending_webhooks(transaction_id, decision.get("uuid"))

    async def mark_submission_uncertain(self, transaction_id: str, detail: str) -> None:
        async with self._engine().begin() as conn:
            await conn.execute(text("""UPDATE didit_transaction_screenings SET status='RECONCILIATION_REQUIRED',
                last_error=:detail, updated_at=now() WHERE transaction_id=:transaction_id"""),
                {"transaction_id": transaction_id, "detail": detail[:1000]})

    async def register_device_transaction(self, transaction_id: str, vendor_data: str,
            transaction_details: dict[str, Any], subject_snapshot: dict[str, Any],
            counterparty_snapshot: dict[str, Any] | None = None) -> None:
        async with self._engine().begin() as conn:
            result = await conn.execute(text("""INSERT INTO didit_transaction_screenings
                (transaction_id, vendor_data, lane, transaction_category, transaction_details,
                 subject_snapshot, counterparty_snapshot, status, created_at, updated_at)
                VALUES (:transaction_id, :vendor_data, 'device', 'finance', CAST(:details AS JSONB),
                 CAST(:subject AS JSONB), CAST(:counterparty AS JSONB), 'AWAITING_DEVICE_SUBMISSION', now(), now())
                ON CONFLICT (transaction_id) DO NOTHING RETURNING transaction_id"""), {
                    "transaction_id": transaction_id, "vendor_data": vendor_data,
                    "details": json.dumps(transaction_details), "subject": json.dumps(subject_snapshot),
                    "counterparty": json.dumps(counterparty_snapshot) if counterparty_snapshot else "null",
                })
            if not result.first():
                row = (await conn.execute(text("""SELECT vendor_data, lane, transaction_details
                    FROM didit_transaction_screenings WHERE transaction_id=:transaction_id"""),
                    {"transaction_id": transaction_id})).mappings().first()
                if not row or row["lane"] != "device" or row["vendor_data"] != vendor_data:
                    raise PermissionError("Transaction is not registered to this user for device screening")
                if row["transaction_details"] != transaction_details:
                    raise PermissionError("Registered transaction details cannot be changed")
        await self.replay_pending_webhooks(transaction_id)

    async def device_transaction_owned(self, transaction_id: str, vendor_data: str) -> bool:
        async with self._engine().connect() as conn:
            row = (await conn.execute(text("""SELECT 1 FROM didit_transaction_screenings
                WHERE transaction_id=:transaction_id AND vendor_data=:vendor_data AND lane='device'
                  AND status='AWAITING_DEVICE_SUBMISSION' AND (sdk_token_expires_at IS NULL OR sdk_token_expires_at <= now())"""),
                {"transaction_id": transaction_id, "vendor_data": vendor_data})).first()
            return row is not None


    async def mark_device_token_issued(self, transaction_id: str, vendor_data: str, expires_at) -> bool:
        async with self._engine().begin() as conn:
            result = await conn.execute(text("""UPDATE didit_transaction_screenings SET
                sdk_token_issued_at=now(), sdk_token_expires_at=:expires_at, updated_at=now()
                WHERE transaction_id=:transaction_id AND vendor_data=:vendor_data AND lane='device'
                  AND status='AWAITING_DEVICE_SUBMISSION'
                  AND (sdk_token_expires_at IS NULL OR sdk_token_expires_at <= now())"""),
                {"transaction_id": transaction_id, "vendor_data": vendor_data, "expires_at": expires_at})
            return result.rowcount == 1

    async def replay_pending_webhooks(self, transaction_id: str, didit_uuid: str | None = None) -> int:
        async with self._engine().connect() as conn:
            rows = (await conn.execute(text("""SELECT payload FROM didit_webhook_events
                WHERE processing_status='PENDING'
                  AND (payload->>'txn_id'=:transaction_id OR (:didit_uuid IS NOT NULL AND payload->>'transaction_id'=:didit_uuid))
                ORDER BY received_at ASC LIMIT 100"""),
                {"transaction_id": transaction_id, "didit_uuid": didit_uuid})).all()
        processed = 0
        for row in rows:
            event = row[0]
            if isinstance(event, str):
                event = json.loads(event)
            if await self.process_webhook(event) == "processed":
                processed += 1
        return processed

    async def process_webhook(self, event: dict[str, Any]) -> str:
        event_id = str(event.get("event_id") or "")
        if not event_id:
            raise ValueError("Webhook event_id is required")
        webhook_type = str(event.get("webhook_type") or "")
        txn_id = str(event.get("txn_id") or "")
        didit_uuid = str(event.get("transaction_id") or "")
        timestamp = int(event["timestamp"])
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        async with self._engine().begin() as conn:
            inserted = await conn.execute(text("""INSERT INTO didit_webhook_events
                (event_id, webhook_type, payload, processing_status, received_at)
                VALUES (:event_id, :webhook_type, CAST(:payload AS JSONB), 'PENDING', now())
                ON CONFLICT (event_id) DO NOTHING RETURNING event_id"""),
                {"event_id": event_id, "webhook_type": webhook_type, "payload": payload})
            if not inserted.first():
                existing = (await conn.execute(text("SELECT processing_status FROM didit_webhook_events WHERE event_id=:event_id"),
                    {"event_id": event_id})).scalar_one_or_none()
                if existing == "PROCESSED":
                    return "duplicate"

            matched = None
            if txn_id:
                matched = (await conn.execute(text("SELECT transaction_id, transaction_details FROM didit_transaction_screenings WHERE transaction_id=:txn_id"),
                    {"txn_id": txn_id})).first()
            if not matched and didit_uuid:
                matched = (await conn.execute(text("SELECT transaction_id, transaction_details FROM didit_transaction_screenings WHERE didit_transaction_uuid=:uuid"),
                    {"uuid": didit_uuid})).first()
            if not matched:
                await conn.execute(text("""UPDATE didit_webhook_events SET processing_status='PENDING',
                    last_error='Awaiting local transaction mapping' WHERE event_id=:event_id"""), {"event_id": event_id})
                return "pending"

            local_id, details = matched
            if isinstance(details, str):
                details = json.loads(details)
            if webhook_type in ("transaction.created", "transaction.status.updated"):
                try:
                    amount_matches = Decimal(str(event.get("amount"))) == Decimal(str(details["amount"]))
                except (InvalidOperation, KeyError, TypeError):
                    amount_matches = False
                currency_matches = str(event.get("currency", "")).upper() == str(details.get("currency", "")).upper()
                direction_matches = str(event.get("direction", "")).lower() == str(details.get("direction", "")).lower()
                if not (amount_matches and currency_matches and direction_matches):
                    await conn.execute(text("""UPDATE didit_transaction_screenings SET status='DATA_MISMATCH',
                        last_error='Didit webhook amount, currency or direction did not match the registered Everpay transaction',
                        updated_at=now() WHERE transaction_id=:transaction_id"""), {"transaction_id": local_id})
                    await conn.execute(text("""UPDATE didit_webhook_events SET processing_status='PROCESSED',
                        processed_at=now(), last_error='Transaction data mismatch; screening held' WHERE event_id=:event_id"""),
                        {"event_id": event_id})
                    return "mismatch"
                await conn.execute(text("""UPDATE didit_transaction_screenings SET
                    didit_transaction_uuid=COALESCE(:uuid, didit_transaction_uuid), status=:status,
                    score=:score, severity=:severity, action_required=CAST(:action_required AS JSONB),
                    last_transaction_event_at=to_timestamp(:event_ts), updated_at=now()
                    WHERE transaction_id=:transaction_id
                    AND (last_transaction_event_at IS NULL OR last_transaction_event_at <= to_timestamp(:event_ts))"""),
                    {"uuid": didit_uuid or None, "status": event.get("status", "IN_REVIEW"),
                     "score": event.get("score"), "severity": event.get("severity"),
                     "action_required": json.dumps(event.get("action_required")) if event.get("action_required") is not None else "null",
                     "event_ts": timestamp, "transaction_id": local_id})
            elif webhook_type == "travel_rule.status.updated":
                await conn.execute(text("""UPDATE didit_transaction_screenings SET travel_rule_status=:status,
                    last_travel_rule_event_at=to_timestamp(:event_ts), updated_at=now() WHERE transaction_id=:transaction_id
                    AND (last_travel_rule_event_at IS NULL OR last_travel_rule_event_at <= to_timestamp(:event_ts))"""),
                    {"status": event.get("travel_rule_status") or event.get("status"), "event_ts": timestamp, "transaction_id": local_id})
            await conn.execute(text("""UPDATE didit_webhook_events SET processing_status='PROCESSED',
                processed_at=now(), last_error=NULL WHERE event_id=:event_id"""), {"event_id": event_id})
            return "processed"
