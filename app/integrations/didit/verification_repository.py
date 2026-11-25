import json
from typing import Any
from sqlalchemy import text
from app.core.db import get_engine


class DiditVerificationRepositoryError(RuntimeError):
    pass


class DiditVerificationRepository:
    def _engine(self):
        engine = get_engine()
        if engine is None:
            raise DiditVerificationRepositoryError("CockroachDB is not configured")
        return engine

    async def reserve_case(self, *, case_id: str, subject_id: str, entity_type: str,
                           vendor_data: str, workflow_id: str) -> None:
        async with self._engine().begin() as conn:
            await conn.execute(text("""
                INSERT INTO didit_verification_cases
                    (case_id, subject_id, entity_type, vendor_data, workflow_id,
                     status, created_at, updated_at)
                VALUES (:case_id, :subject_id, :entity_type, :vendor_data, :workflow_id,
                        'SUBMITTING', now(), now())
            """), {"case_id": case_id, "subject_id": subject_id, "entity_type": entity_type,
                    "vendor_data": vendor_data, "workflow_id": workflow_id})

    async def attach_session(self, case_id: str, result: dict[str, Any]) -> None:
        session_id = result.get("session_id")
        url = result.get("url")
        if not session_id or not url:
            raise ValueError("Didit session response must include session_id and url")
        async with self._engine().begin() as conn:
            updated = await conn.execute(text("""
                UPDATE didit_verification_cases
                SET didit_session_id=COALESCE(didit_session_id, :session_id),
                    verification_url=:url,
                    status=CASE WHEN last_event_at IS NULL AND status='SUBMITTING' THEN 'IN_PROGRESS' ELSE status END,
                    updated_at=now()
                WHERE case_id=:case_id
                  AND (didit_session_id IS NULL OR didit_session_id=:session_id)
            """), {"case_id": case_id, "session_id": str(session_id), "url": str(url)})
            if updated.rowcount != 1:
                raise DiditVerificationRepositoryError("Didit case reservation was not found")

    async def mark_uncertain(self, case_id: str, reason: str) -> None:
        async with self._engine().begin() as conn:
            await conn.execute(text("""
                UPDATE didit_verification_cases
                SET status=CASE
                        WHEN didit_session_id IS NULL AND last_event_at IS NULL THEN 'RECONCILIATION_REQUIRED'
                        ELSE status
                    END,
                    last_error=:reason, updated_at=now()
                WHERE case_id=:case_id
            """), {"case_id": case_id, "reason": reason[:500]})

    async def apply_decision(self, session_id: str, decision: dict[str, Any], event_at=None) -> bool:
        status = str(decision.get("status") or "IN_REVIEW").upper().replace(" ", "_")
        if status not in {"NOT_STARTED","IN_PROGRESS","IN_REVIEW","APPROVED","DECLINED",
                          "EXPIRED","ABANDONED","KYC_EXPIRED","RESUBMITTED","AWAITING_USER"}:
            status = "IN_REVIEW"
        warnings = decision.get("warnings") or decision.get("warning_codes") or []
        if not isinstance(warnings, list):
            warnings = [str(warnings)]
        warnings = [
            str(x.get("code") or x.get("warning_code") or x.get("tag") or "UNSPECIFIED")[:120]
            if isinstance(x, dict) else str(x)[:120]
            for x in warnings[:50]
        ]
        summary = {
            "session_kind": decision.get("session_kind"),
            "status": status,
            "warning_codes": warnings,
            "feature_statuses": _feature_statuses(decision),
            "decision_at": decision.get("decision_at") or decision.get("updated_at"),
        }
        async with self._engine().begin() as conn:
            result = await conn.execute(text("""
                UPDATE didit_verification_cases
                SET status=:status, decision_summary=CAST(:summary AS JSONB),
                    last_event_at=CASE WHEN :event_at IS NULL THEN last_event_at ELSE :event_at END,
                    last_error=NULL, updated_at=now()
                WHERE didit_session_id=:session_id
                  AND (:event_at IS NULL OR last_event_at IS NULL OR last_event_at <= :event_at)
            """), {"session_id": session_id, "status": status, "summary": json.dumps(summary), "event_at": event_at})
            if result.rowcount == 1:
                return True
            # A zero-row update can mean an out-of-order stale event, not an unknown session.
            exists = (await conn.execute(text("""
                SELECT 1 FROM didit_verification_cases WHERE didit_session_id=:session_id
            """), {"session_id": session_id})).first()
            return exists is not None

    async def get_case(self, case_id: str, subject_id: str) -> dict[str, Any] | None:
        async with self._engine().connect() as conn:
            row = (await conn.execute(text("""
                SELECT case_id, entity_type, status, workflow_id, didit_session_id,
                       verification_url, decision_summary, created_at, updated_at
                FROM didit_verification_cases
                WHERE case_id=:case_id AND subject_id=:subject_id
            """), {"case_id": case_id, "subject_id": subject_id})).mappings().first()
            return dict(row) if row else None

    async def record_webhook_event(self, event_id: str, event_type: str, session_id: str,
                                   payload: dict[str, Any]) -> str:
        async with self._engine().begin() as conn:
            inserted = await conn.execute(text("""
                INSERT INTO didit_verification_webhook_events
                    (event_id, event_type, didit_session_id, payload, processing_status, received_at)
                VALUES (:event_id, :event_type, :session_id, CAST(:payload AS JSONB), 'PENDING', now())
                ON CONFLICT (event_id) DO NOTHING RETURNING event_id
            """), {"event_id": event_id, "event_type": event_type[:100],
                    "session_id": session_id, "payload": json.dumps(payload, ensure_ascii=False)})
            if not inserted.first():
                processing_status = (await conn.execute(text("""
                    SELECT processing_status FROM didit_verification_webhook_events
                    WHERE event_id=:event_id
                """), {"event_id": event_id})).scalar_one_or_none()
                if processing_status == "PROCESSED":
                    return "duplicate"
            data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            vendor_data = str(data.get("vendor_data") or "")
            if vendor_data:
                await conn.execute(text("""
                    UPDATE didit_verification_cases
                    SET didit_session_id=COALESCE(didit_session_id, :session_id),
                        status=CASE WHEN status='SUBMITTING' THEN 'IN_PROGRESS' ELSE status END,
                        updated_at=now()
                    WHERE vendor_data=:vendor_data AND didit_session_id IS NULL
                """), {"session_id": session_id, "vendor_data": vendor_data})
            exists = (await conn.execute(text("""
                SELECT 1 FROM didit_verification_cases WHERE didit_session_id=:session_id
            """), {"session_id": session_id})).first()
            return "received" if exists else "pending"

    async def mark_webhook_processed(self, event_id: str) -> None:
        async with self._engine().begin() as conn:
            await conn.execute(text("""
                UPDATE didit_verification_webhook_events
                SET processing_status='PROCESSED', processed_at=now(), payload=CAST('{}' AS JSONB)
                WHERE event_id=:event_id
            """), {"event_id": event_id})

    async def replay_pending_events(self, session_id: str) -> int:
        async with self._engine().connect() as conn:
            rows = (await conn.execute(text("""
                SELECT event_id, event_type, payload FROM didit_verification_webhook_events
                WHERE didit_session_id=:session_id AND processing_status='PENDING'
                ORDER BY received_at ASC LIMIT 100
            """), {"session_id": session_id})).all()
        count = 0
        for event_id, event_type, payload in rows:
            if isinstance(payload, str):
                payload = json.loads(payload)
            data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            timestamp = payload.get("timestamp")
            event_at = None
            if isinstance(timestamp, str):
                from datetime import datetime, timezone
                event_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if event_at.tzinfo is None:
                    event_at = event_at.replace(tzinfo=timezone.utc)
            elif isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
                from datetime import datetime, timezone
                event_at = datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
            if event_type in {"status.updated", "business.status.updated"}:
                await self.apply_decision(session_id, data, event_at)
            await self.mark_webhook_processed(str(event_id))
            count += 1
        return count


def _feature_statuses(decision: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    # Current Didit KYB decision objects expose per-check arrays.
    array_fields = ("registry_checks", "key_people_checks", "company_aml_checks", "document_verifications")
    for field in array_fields:
        entries = decision.get(field)
        if isinstance(entries, list):
            statuses = sorted({
                str(item.get("status")).upper()[:40]
                for item in entries
                if isinstance(item, dict) and item.get("status") is not None
            })
            if statuses:
                result[field] = ",".join(statuses)[:160]
    # Retain compatibility with the compact legacy feature summary shape.
    object_fields = ("kyc", "aml", "kyb_registry", "kyb_company_aml", "kyb_documents", "kyb_key_people")
    for field in object_fields:
        value = decision.get(field)
        if isinstance(value, dict) and value.get("status") is not None:
            result[field] = str(value["status"])[:40]
    return result
