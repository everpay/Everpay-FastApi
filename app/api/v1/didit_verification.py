import hashlib
import hmac
import json
from datetime import datetime, timezone
import httpx
from typing import Any
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from app.core.auth import get_current_user
from app.core.config import settings
from app.integrations.didit.client import DiditAPIError, DiditClient
from app.integrations.didit.config import didit_settings
from app.integrations.didit.verification_repository import DiditVerificationRepository
from app.integrations.didit.verification_schemas import DiditKycSessionRequest, DiditKybSessionRequest
from app.integrations.aiprise.config import aiprise_settings
from app.integrations.didit.verification_service import DiditVerificationService
from app.api.v1.didit import canonicalize_webhook, verify_didit_signature

router = APIRouter(prefix="/compliance/didit", tags=["compliance - Didit KYC/KYB"])


def _subject_id(user: dict[str, Any]) -> str:
    return str(user["id"])


async def _authorize_business(user: dict[str, Any], business_id: str) -> None:
    metadata = user.get("app_metadata") or {}
    role = str(metadata.get("role") or "").lower()
    allowed = metadata.get("business_ids") or []
    if role in {"admin", "super_admin", "compliance_admin"}:
        return
    if business_id == str(metadata.get("business_id") or "") or business_id in {str(x) for x in allowed}:
        return
    # Verify the actual merchant -> authenticated user relationship server-side.
    # The Platform OS merchant onboarding table uses merchants.id and merchants.user_id.
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise HTTPException(status_code=503, detail="Merchant ownership verification is not configured")
    try:
        async with httpx.AsyncClient(timeout=settings.auth_timeout_seconds) as client:
            response = await client.get(
                f"{settings.supabase_url.rstrip('/')}/rest/v1/merchants",
                params={"select": "id", "id": f"eq.{business_id}", "user_id": f"eq.{user['id']}", "limit": "1"},
                headers={
                    "apikey": settings.supabase_service_role_key,
                    "Authorization": f"Bearer {settings.supabase_service_role_key}",
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Merchant ownership service unavailable") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=503, detail="Merchant ownership service unavailable")
    try:
        rows = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Invalid merchant ownership response") from exc
    if not isinstance(rows, list) or not rows:
        raise HTTPException(status_code=403, detail="Not authorized for this business")


@router.post("/kyc/sessions")
async def create_kyc_session(body: DiditKycSessionRequest, user: dict[str, Any] = Depends(get_current_user)):
    if not didit_settings.didit_kyc_workflow_id:
        raise HTTPException(status_code=503, detail="Primary Didit KYC workflow is not configured")
    try:
        return await DiditVerificationService().create_session(
            subject_id=_subject_id(user), entity_type="individual",
            workflow_id=didit_settings.didit_kyc_workflow_id, language=body.language,
        )
    except DiditAPIError as exc:
        raise HTTPException(status_code=502, detail="Didit KYC session could not be created; reconcile before retrying") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/kyb/businesses/{business_id}/sessions")
async def create_kyb_session(business_id: str, body: DiditKybSessionRequest,
                              user: dict[str, Any] = Depends(get_current_user)):
    await _authorize_business(user, business_id)
    if not didit_settings.didit_kyb_workflow_id:
        raise HTTPException(status_code=503, detail="Primary Didit KYB workflow is not configured")
    try:
        return await DiditVerificationService().create_session(
            subject_id=business_id, entity_type="business",
            workflow_id=didit_settings.didit_kyb_workflow_id, language=body.language,
        )
    except DiditAPIError as exc:
        raise HTTPException(status_code=502, detail="Didit KYB session could not be created; reconcile before retrying") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/verification-cases/{case_id}")
async def get_verification_case(case_id: str, user: dict[str, Any] = Depends(get_current_user)):
    repository = DiditVerificationRepository()
    record = await repository.get_case(case_id, _subject_id(user))
    if record is None:
        metadata = user.get("app_metadata") or {}
        business_ids = {str(x) for x in (metadata.get("business_ids") or [])}
        if metadata.get("business_id"):
            business_ids.add(str(metadata["business_id"]))
        for business_id in business_ids:
            record = await repository.get_case(case_id, business_id)
            if record is not None:
                break
    if record is None:
        raise HTTPException(status_code=404, detail="Verification case not found")
    return record


@router.get("/kyb/businesses/{business_id}/cases/{case_id}")
async def get_business_verification_case(business_id: str, case_id: str,
                                         user: dict[str, Any] = Depends(get_current_user)):
    await _authorize_business(user, business_id)
    record = await DiditVerificationRepository().get_case(case_id, business_id)
    if record is None or record.get("entity_type") != "business":
        raise HTTPException(status_code=404, detail="Business verification case not found")
    return record


@router.post("/kyb/businesses/{business_id}/cases/{case_id}/refresh")
async def refresh_business_verification_case(business_id: str, case_id: str,
                                              user: dict[str, Any] = Depends(get_current_user)):
    await _authorize_business(user, business_id)
    record = await DiditVerificationRepository().get_case(case_id, business_id)
    if record is None or record.get("entity_type") != "business" or not record.get("didit_session_id"):
        raise HTTPException(status_code=404, detail="Business verification session not found")
    try:
        decision = await DiditVerificationService().reconcile_session(str(record["didit_session_id"]))
    except DiditAPIError as exc:
        raise HTTPException(status_code=502, detail="Could not refresh Didit business verification decision") from exc
    return {"case_id": case_id, "status": decision.get("status"), "session_kind": decision.get("session_kind")}


@router.post("/verification-cases/{case_id}/refresh")
async def refresh_verification_case(case_id: str, user: dict[str, Any] = Depends(get_current_user)):
    repository = DiditVerificationRepository()
    record = await repository.get_case(case_id, _subject_id(user))
    if record is None:
        metadata = user.get("app_metadata") or {}
        business_ids = {str(x) for x in (metadata.get("business_ids") or [])}
        if metadata.get("business_id"):
            business_ids.add(str(metadata["business_id"]))
        for business_id in business_ids:
            record = await repository.get_case(case_id, business_id)
            if record is not None:
                break
    if record is None or not record.get("didit_session_id"):
        raise HTTPException(status_code=404, detail="Verification session not found")
    try:
        decision = await DiditVerificationService().reconcile_session(str(record["didit_session_id"]))
    except DiditAPIError as exc:
        raise HTTPException(status_code=502, detail="Could not refresh Didit verification decision") from exc
    return {"case_id": case_id, "status": decision.get("status"), "session_kind": decision.get("session_kind")}


@router.post("/verification-webhook", include_in_schema=False)
async def didit_verification_webhook(
    request: Request,
    x_signature_v2: str = Header(default="", alias="X-Signature-V2"),
):
    if not didit_settings.didit_verification_webhook_secret:
        raise HTTPException(status_code=503, detail="Didit KYC/KYB webhook verification is not configured")
    raw = await request.body()
    if len(raw) > didit_settings.didit_webhook_max_body_bytes:
        raise HTTPException(status_code=413, detail="Webhook payload too large")
    try:
        event = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid webhook JSON")
    if not isinstance(event, dict) or not verify_didit_signature(event, x_signature_v2, didit_settings.didit_verification_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid Didit webhook signature")
    timestamp = event.get("timestamp")
    if isinstance(timestamp, str):
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("Webhook timestamp must include a timezone")
            timestamp_value = parsed.timestamp()
        except ValueError:
            raise HTTPException(status_code=401, detail="Invalid signed webhook timestamp")
    elif isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        timestamp_value = float(timestamp)
    else:
        raise HTTPException(status_code=401, detail="Missing signed webhook timestamp")
    if abs(datetime.now(timezone.utc).timestamp() - timestamp_value) > didit_settings.didit_webhook_tolerance_seconds:
        raise HTTPException(status_code=401, detail="Stale Didit webhook event")

    event_type = str(event.get("event") or event.get("webhook_type") or "")
    data = event.get("data") if isinstance(event.get("data"), dict) else event
    session_id = str(data.get("session_id") or data.get("business_session_id") or "")
    vendor_data = str(data.get("vendor_data") or "")
    if not session_id:
        raise HTTPException(status_code=400, detail="Didit verification webhook is missing session_id")
    stable_event = {key: value for key, value in event.items() if key not in {"timestamp", "event_id"}}
    event_id = str(event.get("event_id") or data.get("event_id") or hashlib.sha256(canonicalize_webhook(stable_event)).hexdigest())
    repo = DiditVerificationRepository()
    try:
        event_at = datetime.fromtimestamp(timestamp_value, tz=timezone.utc)
        result = await repo.record_webhook_event(event_id, event_type, session_id, event)
        if result == "duplicate":
            return {"ok": True, "outcome": "duplicate"}
        if result == "pending":
            return {"ok": True, "outcome": "pending"}
        if event_type in {"status.updated", "business.status.updated"}:
            decision = {"status": data.get("status"), "session_kind": data.get("session_kind")}
            applied = await repo.apply_decision(session_id, decision, event_at)
            if not applied:
                return {"ok": True, "outcome": "pending"}
        await repo.mark_webhook_processed(event_id)
        return {"ok": True, "outcome": "processed"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not persist Didit verification webhook") from exc


@router.get("/provider-status")
async def didit_provider_status(_: dict[str, Any] = Depends(get_current_user)):
    return {
        "primary_provider": {"kyc": "didit", "kyb": "didit", "transaction_monitoring": "didit"},
        "secondary_provider": {"kyc": "aiprise", "kyb": "aiprise", "mode": "manual_fallback_only"},
        "didit": {
            "api_configured": bool(didit_settings.didit_api_key),
            "transaction_webhook_configured": bool(didit_settings.didit_webhook_secret),
            "verification_webhook_configured": bool(didit_settings.didit_verification_webhook_secret),
            "kyc_workflow_configured": bool(didit_settings.didit_kyc_workflow_id),
            "kyb_workflow_configured": bool(didit_settings.didit_kyb_workflow_id),
            "kyc_workflow_id": didit_settings.didit_kyc_workflow_id,
            "kyb_workflow_id": didit_settings.didit_kyb_workflow_id,
        },
        "aiprise_secondary_fallback": {
            "configured": bool(aiprise_settings.aiprise_api_key),
            "automatic_failover": False,
        },
    }
