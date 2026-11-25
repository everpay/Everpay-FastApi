from typing import Any
from uuid import uuid4
from app.integrations.didit.client import DiditAPIError, DiditClient
from app.integrations.didit.config import didit_settings
from app.integrations.didit.verification_repository import DiditVerificationRepository


class DiditVerificationService:
    def __init__(self, client: DiditClient | None = None,
                 repository: DiditVerificationRepository | None = None):
        self.client = client or DiditClient()
        self.repository = repository or DiditVerificationRepository()

    def _callback_url(self) -> str | None:
        base = didit_settings.didit_callback_base_url
        return f"{base.rstrip('/')}/compliance/didit/verification-webhook" if base else None

    async def create_session(self, *, subject_id: str, entity_type: str,
                             workflow_id: str, callback: str | None,
                             callback_method: str, language: str | None) -> dict[str, Any]:
        if not workflow_id:
            raise RuntimeError(f"Didit {entity_type} verification workflow is not configured")
        case_id = str(uuid4())
        vendor_data = f"ep_{'usr' if entity_type == 'individual' else 'biz'}_{subject_id}"
        callback_url = self._callback_url()
        await self.repository.reserve_case(
            case_id=case_id, subject_id=subject_id, entity_type=entity_type,
            vendor_data=vendor_data, workflow_id=workflow_id, callback_url=callback_url,
        )
        payload: dict[str, Any] = {"workflow_id": workflow_id, "vendor_data": vendor_data}
        if callback:
            payload["callback"] = callback
            payload["callback_method"] = callback_method
        if language:
            payload["language"] = language
        # The provider's workflow owns the checks; do not accept client-supplied feature lists.
        try:
            result = await self.client.create_verification_session(payload)
        except DiditAPIError as exc:
            await self.repository.mark_uncertain(case_id, f"HTTP_{exc.status_code}: ambiguous session create outcome")
            raise
        try:
            await self.repository.attach_session(case_id, result)
        except Exception:
            await self.repository.mark_uncertain(case_id, "Didit returned an incomplete/unpersisted session response")
            raise
        return {"case_id": case_id, "session_id": result["session_id"],
                "verification_url": result["url"], "status": "IN_PROGRESS",
                "provider": "didit", "entity_type": entity_type}

    async def reconcile_session(self, session_id: str) -> dict[str, Any]:
        decision = await self.client.get_verification_decision(session_id)
        await self.repository.apply_decision(session_id, decision)
        return decision
