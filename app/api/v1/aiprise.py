import hashlib
import hmac
import json
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.auth import get_current_user
from app.integrations.aiprise.client import AiPriseAPIError
from app.integrations.aiprise.config import aiprise_settings
from app.integrations.aiprise.repository import AiPriseRepository
from app.integrations.aiprise.schemas import (
    BusinessVerificationInput,
    EmailCheckInput,
    PhoneCheckInput,
    UserVerificationInput,
)
from app.integrations.aiprise.service import AiPriseService
from app.integrations.muinmos.agents import agent_catalog

router = APIRouter(prefix="/compliance", tags=["compliance"])


def _service() -> AiPriseService:
    try:
        return AiPriseService()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _api_error(exc: AiPriseAPIError) -> HTTPException:
    return HTTPException(status_code=502, detail={
        "code": "aiprise_provider_error",
        "provider_status": exc.status_code,
        "message": "AiPrise request failed. Reconcile the case before submitting again.",
    })


@router.post("/aiprise/users/verification")
async def start_user_verification(
    body: UserVerificationInput,
    user: dict[str, Any] = Depends(get_current_user),
):
    user_data = body.model_dump(mode="json", exclude_none=True)
    try:
        return await _service().start_user_verification(str(user["id"]), user_data)
    except AiPriseAPIError as exc:
        raise _api_error(exc)


@router.post("/aiprise/users/verification-url")
async def create_user_verification_url(
    body: UserVerificationInput,
    user: dict[str, Any] = Depends(get_current_user),
):
    user_data = body.model_dump(mode="json", exclude_none=True)
    try:
        return await _service().create_user_verification_url(str(user["id"]), user_data)
    except AiPriseAPIError as exc:
        raise _api_error(exc)


@router.post("/aiprise/users/email-check")
async def start_email_check(
    body: EmailCheckInput,
    user: dict[str, Any] = Depends(get_current_user),
):
    try:
        return await _service().start_user_verification(
            str(user["id"]), {"email_address": str(body.email_address)}, "email_check"
        )
    except AiPriseAPIError as exc:
        raise _api_error(exc)


@router.post("/aiprise/users/phone-check")
async def start_phone_check(
    body: PhoneCheckInput,
    user: dict[str, Any] = Depends(get_current_user),
):
    try:
        return await _service().start_user_verification(
            str(user["id"]), {"phone_number": body.phone_number}, "phone_check"
        )
    except AiPriseAPIError as exc:
        raise _api_error(exc)


@router.post("/aiprise/businesses/{business_id}/verification")
async def start_business_verification(
    business_id: str,
    body: BusinessVerificationInput,
    user: dict[str, Any] = Depends(get_current_user),
):
    # The authenticated actor is recorded in the request context. The caller must
    # be authorized for this business through the application's merchant RBAC layer.
    # This starter repository does not contain the merchant ownership schema.
    try:
        return await _service().start_business_verification(
            business_id, body.model_dump(mode="json", exclude_none=True)
        )
    except AiPriseAPIError as exc:
        raise _api_error(exc)


@router.get("/aiprise/cases/{case_id}")
async def get_aiprise_case(
    case_id: str,
    user: dict[str, Any] = Depends(get_current_user),
):
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


async def _handle_aiprise_callback(
    request: Request,
    x_hmac_signature: str,
):
    if not aiprise_settings.aiprise_webhook_secret:
        raise HTTPException(status_code=503, detail="AiPrise webhook verification is not configured")
    raw_body = await request.body()
    if len(raw_body) > aiprise_settings.aiprise_webhook_max_body_bytes:
        raise HTTPException(status_code=413, detail="Webhook payload too large")
    if not verify_aiprise_signature(
        raw_body, x_hmac_signature, aiprise_settings.aiprise_webhook_secret
    ):
        raise HTTPException(status_code=401, detail="Invalid AiPrise webhook signature")
    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid AiPrise webhook JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid AiPrise webhook payload")

    event_type = str(
        payload.get("event_type")
        or payload.get("business_profile_event_type")
        or "DECISION_CALLBACK"
    )
    session_id = payload.get("verification_session_id")
    client_reference_id = payload.get("client_reference_id")
    event_id = payload.get("callback_event_id")
    if not event_id:
        # Some documented callback types do not include callback_event_id.
        # Hash the exact signed bytes for deterministic duplicate suppression.
        event_id = hashlib.sha256(raw_body).hexdigest()
    try:
        outcome = await AiPriseRepository().process_callback(
            event_id=str(event_id),
            event_type=event_type,
            session_id=str(session_id) if session_id else None,
            client_reference_id=str(client_reference_id) if client_reference_id else None,
            payload=payload,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not persist AiPrise callback") from exc
    return {"ok": True, "outcome": outcome}


@router.post("/aiprise/callback", include_in_schema=False)
async def aiprise_decision_callback(
    request: Request,
    x_hmac_signature: str = Header(default="", alias="X-HMAC-SIGNATURE"),
):
    return await _handle_aiprise_callback(request, x_hmac_signature)


@router.post("/aiprise/events", include_in_schema=False)
async def aiprise_event_callback(
    request: Request,
    x_hmac_signature: str = Header(default="", alias="X-HMAC-SIGNATURE"),
):
    return await _handle_aiprise_callback(request, x_hmac_signature)


@router.get("/muinmos/agents")
async def get_muinmos_agent_catalog(_: dict[str, Any] = Depends(get_current_user)):
    return {
        "provider": "muinmos",
        "live_connector": "contract_pending",
        "agents": agent_catalog(),
    }


@router.get("/providers/status")
async def compliance_provider_status(_: dict[str, Any] = Depends(get_current_user)):
    return {
        "aiprise": {
            "configured": bool(aiprise_settings.aiprise_api_key),
            "environment": aiprise_settings.aiprise_environment,
            "kyc_template_configured": bool(aiprise_settings.aiprise_kyc_template_id),
            "email_template_configured": bool(aiprise_settings.aiprise_email_check_template_id),
            "phone_template_configured": bool(aiprise_settings.aiprise_phone_check_template_id),
            "kyb_template_configured": bool(aiprise_settings.aiprise_kyb_template_id),
            "webhook_configured": bool(aiprise_settings.aiprise_webhook_secret),
        },
        "muinmos": {"configured": False, "status": "contract_pending"},
    }
