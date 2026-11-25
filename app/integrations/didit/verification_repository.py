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
                           vendor_data: str, workflow_id: str, callback_url: str | None) -> None:
        async with self._engine().begin() as conn:
            await conn.execute(text("""
                INSERT INTO didit_verification_cases
                    (case_id, subject_id, entity_type, vendor_data, workflow_id, callback_url,
                     status, created_at, updated_at)
                VALUES (:case_id, :subject_id, :entity_type, :vendor_data, :workflow_id,
                        :callback_url, 'SUBMITTING', now(), now())
            """), {"case_id": case_id, "subject_id": subject_id, "entity_type": entity_type,
                    "vendor_data": vendor_data, "workflow_id": workflow_id, "callback_url": callback_url})

    async def attach_session(self, case_id: str, result: dict[str, Any]) -> None:
        session_id = result.get("session_id")
        url = result.get("url")
        if not session_id or not url:
            raise ValueError("Didit session response must include session_id and url")
        async with self._engine().begin() as conn:
            updated = await conn.execute(text("""
                UPDATE didit_verification_cases
                SET didit_session_id=:session_id, verification_url=:url,
                    status='IN_PROGRESS', updated_at=now()
                WHERE case_id=:case_id AND status='SUBMITTING'
            """), {"case_id": case_id, "session_id": str(session_id), "url": str(url)})
            if updated.rowcount != 1:
                raise DiditVerificationRepositoryError("Didit case reservation was not found")

    async def mark_uncertain(self, case_id: str, reason: str) -> None:
        async with self._engine().begin() as conn:
            await conn.execute(text("""
                UPDATE didit_verification_cases SET status='RECONCILIATION_REQUIRED',
                    last_error=:reason, updated_at=now() WHERE case_id=:case_id
            """), {"case_id": case_id, "reason": reason[:500]})

    async def apply_decision(self, session_id: str, decision: dict[str, Any]) -> bool:
        status = str(decision.get("status") or "IN_REVIEW").upper().replace(" ", "_")
        if status not in {"NOT_STARTED","IN_PROGRESS","IN_REVIEW","APPROVED","DECLINED",
                          "EXPIRED","ABANDONED","KYC_EXPIRED","RESUBMITTED","AWAITING_USER"}:
            status = "IN_REVIEW"
        warnings = decision.get("warnings") or decision.get("warning_codes") or []
        if not isinstance(warnings, list):
            warnings = [str(warnings)]
        warnings = [str(x)[:120] for x in warnings[:50]]
        # Store only a minimized decision summary; no identity document images or raw PII.
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
                    last_error=NULL, updated_at=now()
                WHERE didit_session_id=:session_id
            """), {"session_id": session_id, "status": status, "summary": json.dumps(summary)})
            return result.rowcount == 1

    async def get_case(self, case_id: str, subject_id: str) -> dict[str, Any] | None:
        async with self._engine().connect() as conn:
            row = (await conn.execute(text("""
                SELECT case_id, entity_type, status, workflow_id, didit_session_id,
                       verification_url, decision_summary, created_at, updated_at
                FROM didit_verification_cases
                WHERE case_id=:case_id AND subject_id=:subject_id
            """), {"case_id": case_id, "subject_id": subject_id})).mappings().first()
            return dict(row) if row else None

    async def get_case_by_vendor_data(self, vendor_data: str, entity_type: str) -> dict[str, Any] | None:
        async with self._engine().connect() as conn:
            row = (await conn.execute(text("""
                SELECT case_id, subject_id, entity_type, didit_session_id
                FROM didit_verification_cases
                WHERE vendor_data=:vendor_data AND entity_type=:entity_type
                ORDER BY created_at DESC LIMIT 1
            """), {"vendor_data": vendor_data, "entity_type": entity_type})).mappings().first()
            return dict(row) if row else None

    async def record_webhook_event(self, event_id: str, event_type: str, session_id: str,
                                   payload: dict[str, Any]) -> str:
        async with self._engine().begin() as conn:
            inserted = await conn.execute(text("""
                INSERT INTO didit_verification_webhook_events
                    (event_id, event_type, didit_session_id, received_at)
                VALUES (:event_id, :event_type, :session_id, now())
                ON CONFLICT (event_id) DO NOTHING RETURNING event_id
            """), {"event_id": event_id, "event_type": event_type[:100], "session_id": session_id})
            if not inserted.first():
                return "duplicate"
            return "received"


def _feature_statuses(decision: dict[str, Any]) -> dict[str, str]:
    allowed = {
        "kyc": ("kyc", "status"), "aml": ("aml", "status"),
        "kyb_registry": ("kyb_registry", "status"),
        "kyb_company_aml": ("kyb_company_aml", "status"),
        "kyb_documents": ("kyb_documents", "status"),
        "kyb_key_people": ("kyb_key_people", "status"),
    }
    result = {}
    for key, (field, status_field) in allowed.items():
        value = decision.get(field)
        if isinstance(value, dict) and value.get(status_field) is not None:
            result[key] = str(value[status_field])[:40]
    return result
