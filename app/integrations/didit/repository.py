import json
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

    async def mark_submission_uncertain(self, transaction_id: str, detail: str) -> None:
        async with self._engine().begin() as conn:
            await conn.execute(text("""UPDATE didit_transaction_screenings SET status='RECONCILIATION_REQUIRED',
                last_error=:detail, updated_at=now() WHERE transaction_id=:transaction_id"""),
                {"transaction_id": transaction_id, "detail": detail[:1000]})

    async def register_device_transaction(self, transaction_id: str, vendor_data: str) -> None:
        async with self._engine().begin() as conn:
            result = await conn.execute(text("""INSERT INTO didit_transaction_screenings
                (transaction_id, vendor_data, lane, transaction_category, status, created_at, updated_at)
                VALUES (:transaction_id, :vendor_data, 'device', 'finance', 'AWAITING_DEVICE_SUBMISSION', now(), now())
                ON CONFLICT (transaction_id) DO NOTHING RETURNING transaction_id"""),
                {"transaction_id": transaction_id, "vendor_data": vendor_data})
            if result.first():
                return
            row = (await conn.execute(text("""SELECT vendor_data, lane FROM didit_transaction_screenings
                WHERE transaction_id=:transaction_id"""), {"transaction_id": transaction_id})).mappings().first()
            if not row or row["lane"] != "device" or row["vendor_data"] != vendor_data:
                raise PermissionError("Transaction is not registered to this user for device screening")

    async def device_transaction_owned(self, transaction_id: str, vendor_data: str) -> bool:
        async with self._engine().connect() as conn:
            row = (await conn.execute(text("""SELECT 1 FROM didit_transaction_screenings
                WHERE transaction_id=:transaction_id AND vendor_data=:vendor_data AND lane='device'
                  AND status IN ('AWAITING_DEVICE_SUBMISSION','AWAITING_USER','IN_REVIEW')"""),
                {"transaction_id": transaction_id, "vendor_data": vendor_data})).first()
            return row is not None

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
                existing = (await conn.execute(text("""SELECT processing_status FROM didit_webhook_events
                    WHERE event_id=:event_id"""), {"event_id": event_id})).scalar_one_or_none()
                if existing == "PROCESSED":
                    return "duplicate"

            matched = None
            if txn_id:
                matched = (await conn.execute(text("SELECT transaction_id FROM didit_transaction_screenings WHERE transaction_id=:txn_id"), {"txn_id": txn_id})).first()
            if not matched and didit_uuid:
                matched = (await conn.execute(text("SELECT transaction_id FROM didit_transaction_screenings WHERE didit_transaction_uuid=:uuid"), {"uuid": didit_uuid})).first()
            if not matched:
                await conn.execute(text("""UPDATE didit_webhook_events SET processing_status='PENDING',
                    last_error='Awaiting local transaction mapping' WHERE event_id=:event_id"""), {"event_id": event_id})
                return "pending"

            transaction_id = matched[0]
            if webhook_type == "travel_rule.status.updated":
                await conn.execute(text("""UPDATE didit_transaction_screenings SET travel_rule_status=:status,
                    last_event_at=to_timestamp(:event_ts), updated_at=now() WHERE transaction_id=:transaction_id
                    AND (last_event_at IS NULL OR last_event_at <= to_timestamp(:event_ts))"""),
                    {"status": event.get("travel_rule_status") or event.get("status"), "event_ts": timestamp, "transaction_id": transaction_id})
            elif webhook_type in ("transaction.created", "transaction.status.updated"):
                await conn.execute(text("""UPDATE didit_transaction_screenings SET
                    didit_transaction_uuid=COALESCE(:uuid, didit_transaction_uuid), status=:status,
                    score=:score, severity=:severity, action_required=CAST(:action_required AS JSONB),
                    last_event_at=to_timestamp(:event_ts), updated_at=now() WHERE transaction_id=:transaction_id
                    AND (last_event_at IS NULL OR last_event_at <= to_timestamp(:event_ts))"""),
                    {"uuid": didit_uuid or None, "status": event.get("status", "IN_REVIEW"),
                     "score": event.get("score"), "severity": event.get("severity"),
                     "action_required": json.dumps(event.get("action_required")) if event.get("action_required") is not None else "null",
                     "event_ts": timestamp, "transaction_id": transaction_id})
            await conn.execute(text("""UPDATE didit_webhook_events SET processing_status='PROCESSED',
                processed_at=now(), last_error=NULL WHERE event_id=:event_id"""), {"event_id": event_id})
            return "processed"
