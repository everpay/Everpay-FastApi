import hashlib
import json
from typing import Any
from uuid import uuid4

from app.integrations.aiprise.client import AiPriseClient, AiPriseAPIError
from app.integrations.aiprise.config import aiprise_settings
from app.integrations.aiprise.repository import AiPriseRepository


class AiPriseService:
    def __init__(
        self,
        client: AiPriseClient | None = None,
        repository: AiPriseRepository | None = None,
    ):
        self.client = client or AiPriseClient()
        self.repository = repository or AiPriseRepository()

    @staticmethod
    def _fingerprint(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _callback_urls(self) -> dict[str, str]:
        base = aiprise_settings.aiprise_callback_base_url
        if not base:
            return {}
        base = base.rstrip("/")
        return {
            "callback_url": f"{base}/compliance/aiprise/callback",
            "events_callback_url": f"{base}/compliance/aiprise/events",
        }

    async def start_user_verification(
        self,
        subject_id: str,
        user_data: dict[str, Any],
        workflow_type: str = "kyc",
    ) -> dict[str, Any]:
        template_by_type = {
            "kyc": aiprise_settings.aiprise_kyc_template_id,
            "email_check": aiprise_settings.aiprise_email_check_template_id,
            "phone_check": aiprise_settings.aiprise_phone_check_template_id,
        }
        template_id = template_by_type.get(workflow_type)
        if not template_id:
            raise RuntimeError(f"AiPrise template for {workflow_type} is not configured")
        case_id = str(uuid4())
        payload = {
            "template_id": template_id,
            "client_reference_id": case_id,
            "client_reference_data": {
                "everpay_subject_id": subject_id,
                "workflow_type": workflow_type,
            },
            "user_data": user_data,
            **self._callback_urls(),
        }
        await self.repository.create_case(
            case_id=case_id,
            subject_id=subject_id,
            entity_type="individual",
            workflow_type=workflow_type,
            template_id=template_id,
            request_fingerprint=self._fingerprint(payload),
        )
        try:
            result = await self.client.run_user_verification(payload)
        except AiPriseAPIError as exc:
            await self.repository.mark_submission_uncertain(case_id, f"HTTP_{exc.status_code}")
            raise
        session_id = result.get("verification_session_id") or result.get("session_id")
        if not session_id:
            await self.repository.mark_submission_uncertain(case_id, "MISSING_SESSION_ID")
            raise AiPriseAPIError(502, "AiPrise response did not contain a verification session ID")
        await self.repository.attach_provider_session(case_id, str(session_id), None, result)
        return {"case_id": case_id, "verification_session_id": str(session_id), "status": "IN_PROGRESS"}

    async def create_user_verification_url(
        self, subject_id: str, user_data: dict[str, Any], redirect_uri: str = "iframe"
    ) -> dict[str, Any]:
        template_id = aiprise_settings.aiprise_kyc_template_id
        if not template_id:
            raise RuntimeError("AIPRISE_KYC_TEMPLATE_ID is not configured")
        case_id = str(uuid4())
        payload = {
            "redirect_uri": redirect_uri,
            "template_id": template_id,
            "client_reference_id": case_id,
            "client_reference_data": {"everpay_subject_id": subject_id, "workflow_type": "kyc"},
            "user_data": user_data,
            **self._callback_urls(),
        }
        await self.repository.create_case(
            case_id=case_id,
            subject_id=subject_id,
            entity_type="individual",
            workflow_type="kyc_hosted",
            template_id=template_id,
            request_fingerprint=self._fingerprint(payload),
        )
        try:
            result = await self.client.get_user_verification_url(payload)
        except AiPriseAPIError as exc:
            await self.repository.mark_submission_uncertain(case_id, f"HTTP_{exc.status_code}")
            raise
        session_id = result.get("verification_session_id") or result.get("session_id")
        await self.repository.attach_provider_session(case_id, str(session_id) if session_id else None, None, result)
        return {"case_id": case_id, **result}

    async def start_business_verification(
        self,
        business_id: str,
        business_data: dict[str, Any],
    ) -> dict[str, Any]:
        template_id = aiprise_settings.aiprise_kyb_template_id
        if not template_id:
            raise RuntimeError("AIPRISE_KYB_TEMPLATE_ID is not configured")
        case_id = str(uuid4())
        payload = {
            "template_id": template_id,
            "client_reference_id": case_id,
            "client_reference_data": {
                "everpay_business_id": business_id,
                "workflow_type": "kyb",
            },
            "business_data": business_data,
            **self._callback_urls(),
        }
        await self.repository.create_case(
            case_id=case_id,
            subject_id=business_id,
            entity_type="business",
            workflow_type="kyb",
            template_id=template_id,
            request_fingerprint=self._fingerprint(payload),
        )
        try:
            result = await self.client.run_business_verification(payload)
        except AiPriseAPIError as exc:
            await self.repository.mark_submission_uncertain(case_id, f"HTTP_{exc.status_code}")
            raise
        session_id = result.get("verification_session_id") or result.get("session_id")
        if not session_id:
            await self.repository.mark_submission_uncertain(case_id, "MISSING_SESSION_ID")
            raise AiPriseAPIError(502, "AiPrise response did not contain a business verification session ID")
        profile_id = result.get("business_profile_id")
        await self.repository.attach_provider_session(
            case_id, str(session_id), str(profile_id) if profile_id else None, result
        )
        return {
            "case_id": case_id,
            "verification_session_id": str(session_id),
            "business_profile_id": profile_id,
            "status": "IN_PROGRESS",
        }

    async def get_case(self, case_id: str, subject_id: str) -> dict[str, Any] | None:
        return await self.repository.get_case(case_id, subject_id)
