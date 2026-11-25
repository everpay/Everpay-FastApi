import hashlib
import json
from typing import Any
from sqlalchemy import text

from app.core.db import get_engine


class AiPriseRepositoryError(RuntimeError):
    pass


class AiPriseRepository:
    def _engine(self):
        engine = get_engine()
        if engine is None:
            raise AiPriseRepositoryError("CockroachDB is not configured")
        return engine

    async def create_case(
        self,
        *,
        case_id: str,
        subject_id: str,
        entity_type: str,
        workflow_type: str,
        template_id: str,
        request_fingerprint: str,
    ) -> None:
        query = text("""
            INSERT INTO compliance_verification_cases
                (case_id, subject_id, entity_type, provider, workflow_type, template_id,
                 status, request_fingerprint, created_at, updated_at)
            VALUES
                (:case_id, :subject_id, :entity_type, 'aiprise', :workflow_type, :template_id,
                 'SUBMITTING', :request_fingerprint, now(), now())
        """)
        async with self._engine().begin() as conn:
            await conn.execute(query, {
                "case_id": case_id,
                "subject_id": subject_id,
                "entity_type": entity_type,
                "workflow_type": workflow_type,
                "template_id": template_id,
                "request_fingerprint": request_fingerprint,
            })

    async def attach_provider_session(
        self, case_id: str, session_id: str | None, profile_id: str | None, result: dict[str, Any]
    ) -> None:
        safe_result = {
            key: result.get(key)
            for key in ("status", "verification_session_id", "business_profile_id", "message")
            if result.get(key) is not None
        }
        async with self._engine().begin() as conn:
            await conn.execute(text("""
                UPDATE compliance_verification_cases
                SET provider_session_id = COALESCE(:session_id, provider_session_id),
                    provider_profile_id = COALESCE(:profile_id, provider_profile_id),
                    status = 'IN_PROGRESS',
                    provider_summary = CAST(:summary AS JSONB),
                    updated_at = now()
                WHERE case_id = :case_id
            """), {
                "case_id": case_id,
                "session_id": session_id,
                "profile_id": profile_id,
                "summary": json.dumps(safe_result),
            })

    async def mark_submission_uncertain(self, case_id: str, error_code: str) -> None:
        async with self._engine().begin() as conn:
            await conn.execute(text("""
                UPDATE compliance_verification_cases
                SET status = 'RECONCILIATION_REQUIRED',
                    last_error_code = :error_code,
                    updated_at = now()
                WHERE case_id = :case_id
            """), {"case_id": case_id, "error_code": error_code[:80]})

    async def get_case(self, case_id: str, subject_id: str) -> dict[str, Any] | None:
        async with self._engine().connect() as conn:
            row = (await conn.execute(text("""
                SELECT case_id, subject_id, entity_type, provider, workflow_type,
                       provider_session_id, provider_profile_id, status, decision,
                       risk_score, decision_reasons, created_at, updated_at
                FROM compliance_verification_cases
                WHERE case_id = :case_id AND subject_id = :subject_id
            """), {"case_id": case_id, "subject_id": subject_id})).mappings().first()
            return dict(row) if row else None

    async def find_case_for_callback(
        self, session_id: str | None, client_reference_id: str | None
    ) -> dict[str, Any] | None:
        async with self._engine().connect() as conn:
            if client_reference_id:
                row = (await conn.execute(text("""
                    SELECT case_id, subject_id, entity_type, workflow_type
                    FROM compliance_verification_cases
                    WHERE case_id = :case_id AND provider = 'aiprise'
                """), {"case_id": client_reference_id})).mappings().first()
                if row:
                    return dict(row)
            if session_id:
                row = (await conn.execute(text("""
                    SELECT case_id, subject_id, entity_type, workflow_type
                    FROM compliance_verification_cases
                    WHERE provider_session_id = :session_id AND provider = 'aiprise'
                """), {"session_id": session_id})).mappings().first()
                return dict(row) if row else None
            return None

    async def process_callback(
        self,
        *,
        event_id: str,
        event_type: str,
        session_id: str | None,
        client_reference_id: str | None,
        payload: dict[str, Any],
    ) -> str:
        case = await self.find_case_for_callback(session_id, client_reference_id)
        if not case:
            # Persist only correlation and event metadata. Never retain raw KYC/KYB documents or PII.
            async with self._engine().begin() as conn:
                await conn.execute(text("""
                    INSERT INTO compliance_provider_events
                        (provider, event_id, event_type, processing_status, correlation_id, received_at)
                    VALUES ('aiprise', :event_id, :event_type, 'UNMATCHED', :correlation_id, now())
                    ON CONFLICT (provider, event_id) DO NOTHING
                """), {
                    "event_id": event_id,
                    "event_type": event_type[:100],
                    "correlation_id": client_reference_id or session_id or "unknown",
                })
            return "unmatched"

        summary = payload.get("aiprise_summary") or {}
        decision = summary.get("decision") or summary.get("verification_result")
        if event_type == "CASE_STATUS_UPDATE":
            decision = payload.get("verification_result") or decision
        if event_type == "AML_MONITORING_UPDATE":
            aml_update = payload.get("aml_monitoring_update") or {}
            if aml_update.get("status") in ("MATCH_FOUND", "POTENTIAL_MATCH"):
                decision = "MANUAL_REVIEW"
        if event_type == "WEBSITE_MONITORING_UPDATED":
            # A changed website is evidence for review, not an automatic decline.
            decision = "MANUAL_REVIEW"

        allowed = {"APPROVED", "DECLINED", "MANUAL_REVIEW", "FAILED"}
        if decision not in allowed:
            decision = None

        reasons = summary.get("reasons") or summary.get("notes") or []
        if isinstance(reasons, str):
            reasons = [reasons]
        reasons = [str(item)[:500] for item in reasons[:20]] if isinstance(reasons, list) else []

        risk_info = payload.get("risk_info") or {}
        fraud = payload.get("fraud_insights") or {}
        risk_score = risk_info.get("risk_score", fraud.get("overall_risk_score"))
        try:
            risk_score = int(risk_score) if risk_score is not None else None
        except (TypeError, ValueError):
            risk_score = None

        async with self._engine().begin() as conn:
            inserted = await conn.execute(text("""
                INSERT INTO compliance_provider_events
                    (provider, event_id, event_type, processing_status, correlation_id, received_at)
                VALUES ('aiprise', :event_id, :event_type, 'RECEIVED', :correlation_id, now())
                ON CONFLICT (provider, event_id) DO NOTHING RETURNING event_id
            """), {
                "event_id": event_id,
                "event_type": event_type[:100],
                "correlation_id": case["case_id"],
            })
            if not inserted.first():
                return "duplicate"

            await conn.execute(text("""
                UPDATE compliance_verification_cases
                SET provider_session_id = COALESCE(:session_id, provider_session_id),
                    status = CASE
                        WHEN :decision = 'APPROVED' THEN 'COMPLETED'
                        WHEN :decision = 'DECLINED' THEN 'DECLINED'
                        WHEN :decision = 'MANUAL_REVIEW' THEN 'MANUAL_REVIEW'
                        WHEN :decision = 'FAILED' THEN 'FAILED'
                        WHEN :event_type IN ('VERIFICATION_SESSION_STARTED', 'VERIFICATION_REQUEST_SUBMITTED')
                            THEN 'IN_PROGRESS'
                        ELSE status
                    END,
                    decision = COALESCE(:decision, decision),
                    risk_score = COALESCE(:risk_score, risk_score),
                    decision_reasons = CAST(:reasons AS JSONB),
                    last_event_type = :event_type,
                    last_event_at = now(),
                    updated_at = now()
                WHERE case_id = :case_id
            """), {
                "case_id": case["case_id"],
                "session_id": session_id,
                "decision": decision,
                "risk_score": risk_score,
                "reasons": json.dumps(reasons),
                "event_type": event_type[:100],
            })
            await conn.execute(text("""
                UPDATE compliance_provider_events
                SET processing_status = 'PROCESSED', processed_at = now()
                WHERE provider = 'aiprise' AND event_id = :event_id
            """), {"event_id": event_id})
        return "processed"
