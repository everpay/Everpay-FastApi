import hashlib
import hmac
import json
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.core.auth import get_current_user
from app.integrations.aiprise.client import AiPriseAPIError
from app.integrations.aiprise.config import aiprise_settings
from app.integrations.aiprise.repository import AiPriseRepository
from app.integrations.aiprise.schemas import BusinessVerificationInput, EmailCheckInput, PhoneCheckInput, UserVerificationInput
from app.integrations.aiprise.service import AiPriseService
from app.integrations.muinmos.agents import agent_catalog

router = APIRouter(prefix="/compliance", tags=["compliance"])


def _service() -> AiPriseService:
    if not aiprise_settings.aiprise_secondary_fallback_enabled:
        raise HTTPException(status_code=409, detail="AiPrise is configured as Everpay's secondary provider and is not enabled for fallback. Didit is the primary KYC/KYB provider.")
    try:
        return AiPriseService()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _api_error(exc: AiPriseAPIError) -> HTTPException:
    return HTTPException(status_code=502, detail={
        "code": "aiprise_provider_error", "provider_status": exc.status_code,
        "message": "AiPrise request failed. Reconcile the case before submitting again.",
    })


def _authorize_business(user: dict[str, Any], business_id: str) -> None:
    metadata = user.get("app_metadata") or {}
    role = str(metadata.get("role") or "").lower()
    allowed_business_ids = metadata.get("business_ids") or []
    own_business_id = metadata.get("business_id")
    if role in {"admin", "super_admin", "compliance_admin"}:
        return
    if business_id == str(own_business_id or "") or business_id in {str(x) for x in allowed_business_ids}:
        return
    raise HTTPException(status_code=403, detail="You are not authorized to manage this business")


@router.post("/aiprise/users/verification")
async def start_user_verification(body: UserVerificationInput, user: dict[str, Any] = Depends(get_current_user)):
    payload = body.model_dump(mode="json", exclude_none=True)
    additional = payload.pop("additional_user_data", {})
    if set(additional).intersection(payload):
        raise HTTPException(status_code=422, detail="additional_user_data cannot override standard identity fields")
    payload.update(additional)
    try:
        return await _service().start_user_verification(str(user["id"]), payload)
    except AiPriseAPIError as exc:
        raise _api_error(exc)


@router.post("/aiprise/users/verification-url")
async def create_user_verification_url(body: UserVerificationInput, user: dict[str, Any] = Depends(get_current_user)):
    payload = body.model_dump(mode="json", exclude_none=True)
    additional = payload.pop("additional_user_data", {})
    if set(additional).intersection(payload):
        raise HTTPException(status_code=422, detail="additional_user_data cannot override standard identity fields")
    payload.update(additional)
    try:
        return await _service().create_user_verification_url(str(user["id"]), payload)
    except AiPriseAPIError as exc:
        raise _api_error(exc)


@router.post("/aiprise/users/email-check")
async def start_email_check(body: EmailCheckInput, user: dict[str, Any] = Depends(get_current_user)):
    try:
        return await _service().start_user_verification(str(user["id"]), {"email_address": str(body.email_address)}, "email_check")
    except AiPriseAPIError as exc:
        raise _api_error(exc)


@router.post("/aiprise/users/phone-check")
async def start_phone_check(body: PhoneCheckInput, user: dict[str, Any] = Depends(get_current_user)):
    try:
        return await _service().start_user_verification(str(user["id"]), {"phone_number": body.phone_number}, "phone_check")
    except AiPriseAPIError as exc:
        raise _api_error(exc)


@router.post("/aiprise/businesses/{business_id}/profile")
async def create_business_profile(business_id: str, body: BusinessVerificationInput, user: dict[str, Any] = Depends(get_current_user)):
    _authorize_business(user, business_id)
    try:
        return await _service().create_business_profile(business_id, body.model_dump(mode="json", exclude_none=True))
    except AiPriseAPIError as exc:
        raise _api_error(exc)


@router.post("/aiprise/businesses/{business_id}/verification")
async def start_business_verification(business_id: str, body: BusinessVerificationInput, user: dict[str, Any] = Depends(get_current_user)):
    _authorize_business(user, business_id)
    try:
        return await _service().start_business_verification(business_id, body.model_dump(mode="json", exclude_none=True))
    except AiPriseAPIError as exc:
        raise _api_error(exc)


@router.get("/aiprise/businesses/{business_id}/cases/{case_id}")
async def get_aiprise_business_case(business_id: str, case_id: str, user: dict[str, Any] = Depends(get_current_user)):
    _authorize_business(user, business_id)
    try:
        record = await AiPriseRepository().get_case(case_id, business_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Compliance case store unavailable") from exc
    if record is None:
        raise HTTPException(status_code=404, detail="Compliance case not found")
    return record


@router.get("/aiprise/cases/{case_id}")
async def get_aiprise_case(case_id: str, user: dict[str, Any] = Depends(get_current_user)):
    try:
        record = await AiPriseRepository().get_case(case_id, str(user["id"]))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Compliance case store unavailable") from exc
    if record is None:
        raise HTTPException(status_code=404, detail="Compliance case not found")
    return record


def verify_aiprise_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest().lower()
    return hmac.compare_digest(expected, signature.strip().lower())


async def _handle_aiprise_callback(request: Request, x_hmac_signature: str):
    if not aiprise_settings.aiprise_webhook_secret:
        raise HTTPException(status_code=503, detail="AiPrise webhook verification is not configured")
    raw_body = await request.body()
    if len(raw_body) > aiprise_settings.aiprise_webhook_max_body_bytes:
        raise HTTPException(status_code=413, detail="Webhook payload too large")
    if not verify_aiprise_signature(raw_body, x_hmac_signature, aiprise_settings.aiprise_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid AiPrise webhook signature")
    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid AiPrise webhook JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid AiPrise webhook payload")

    event_type = str(payload.get("event_type") or payload.get("business_profile_event_type") or "DECISION_CALLBACK")
    session_id = payload.get("verification_session_id")
    client_reference_id = payload.get("client_reference_id")
    event_id = payload.get("callback_event_id") or hashlib.sha256(raw_body).hexdigest()
    try:
        outcome = await AiPriseRepository().process_callback(
            event_id=str(event_id), event_type=event_type,
            session_id=str(session_id) if session_id else None,
            client_reference_id=str(client_reference_id) if client_reference_id else None,
            payload=payload,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not persist AiPrise callback") from exc
    return {"ok": True, "outcome": outcome}


@router.post("/aiprise/callback", include_in_schema=False)
async def aiprise_decision_callback(request: Request, x_hmac_signature: str = Header(default="", alias="X-HMAC-SIGNATURE")):
    return await _handle_aiprise_callback(request, x_hmac_signature)


@router.post("/aiprise/events", include_in_schema=False)
async def aiprise_event_callback(request: Request, x_hmac_signature: str = Header(default="", alias="X-HMAC-SIGNATURE")):
    return await _handle_aiprise_callback(request, x_hmac_signature)


@router.get("/muinmos/agents")
async def get_muinmos_agent_catalog(_: dict[str, Any] = Depends(get_current_user)):
    return {"provider": "muinmos", "live_connector": "contract_pending", "agents": agent_catalog()}


@router.get("/providers/status")
async def compliance_provider_status(_: dict[str, Any] = Depends(get_current_user)):
    return {
        "aiprise": {
            "configured": bool(aiprise_settings.aiprise_api_key),
            "secondary_fallback_enabled": aiprise_settings.aiprise_secondary_fallback_enabled,
            "environment": aiprise_settings.aiprise_environment,
            "kyc_template_configured": bool(aiprise_settings.aiprise_kyc_template_id),
            "email_template_configured": bool(aiprise_settings.aiprise_email_check_template_id),
            "phone_template_configured": bool(aiprise_settings.aiprise_phone_check_template_id),
            "kyb_template_configured": bool(aiprise_settings.aiprise_kyb_template_id),
            "webhook_configured": bool(aiprise_settings.aiprise_webhook_secret),
        },
        "muinmos": {"configured": False, "status": "contract_pending"},
    }
