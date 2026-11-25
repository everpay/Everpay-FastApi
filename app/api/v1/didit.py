import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from app.core.auth import get_current_user
from app.core.config import settings
from app.integrations.didit.config import didit_settings
from app.integrations.didit.client import DiditAPIError, DiditClient
from app.integrations.didit.repository import DiditRepository
from app.integrations.didit.schemas import DeviceTokenRequest

router = APIRouter(prefix="/compliance/didit", tags=["compliance - Didit"])

async def _rate_limit_sdk_token(user_id: str) -> None:
    redis_url = didit_settings.redis_url or settings.redis_url
    if not redis_url:
        if settings.app_env in ("staging", "production"):
            raise HTTPException(status_code=503, detail="Token issuance rate limiter is unavailable")
        return
    client = Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2, decode_responses=True)
    key = f"everpay:didit:sdk-token:{hashlib.sha256(user_id.encode()).hexdigest()}"
    try:
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, 60)
        if count > didit_settings.didit_sdk_token_max_per_minute:
            raise HTTPException(status_code=429, detail="Too many transaction token requests")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Token issuance rate limiter unavailable") from exc
    finally:
        await client.aclose()

@router.post("/sdk-token", response_model=None)
async def create_sdk_token(body: DeviceTokenRequest, user: dict[str, Any] = Depends(get_current_user)):
    if not didit_settings.didit_api_key:
        raise HTTPException(status_code=503, detail="Didit integration is not configured")
    user_id = str(user["id"])
    repository = DiditRepository()
    try:
        allowed = await repository.device_transaction_owned(body.transaction_id, user_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Transaction screening store unavailable") from exc
    if not allowed:
        raise HTTPException(status_code=404, detail="Registered device transaction not found")
    await _rate_limit_sdk_token(user_id)
    try:
        result = await DiditClient().create_sdk_token(user_id)
    except DiditAPIError as exc:
        raise HTTPException(status_code=502, detail="Could not mint Didit transaction token") from exc
    token = result.get("sdk_token")
    if not token:
        raise HTTPException(status_code=502, detail="Didit did not return an SDK token")
    expires_at_raw = result.get("expires_at")
    try:
        expires_at = datetime.fromisoformat(str(expires_at_raw).replace("Z", "+00:00")) if expires_at_raw else datetime.now(timezone.utc) + timedelta(seconds=didit_settings.didit_sdk_token_ttl_seconds)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=didit_settings.didit_sdk_token_ttl_seconds)
    try:
        recorded = await repository.mark_device_token_issued(body.transaction_id, user_id, expires_at)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not persist Didit token issuance") from exc
    if not recorded:
        raise HTTPException(status_code=409, detail="A valid Didit token has already been issued for this transaction")
    return JSONResponse({"sdkToken": token, "expiresAt": expires_at.isoformat()}, headers={"Cache-Control": "no-store, private", "Pragma": "no-cache"})

def _shorten_floats(value: Any) -> Any:
    if isinstance(value, list):
        return [_shorten_floats(item) for item in value]
    if isinstance(value, dict):
        return {key: _shorten_floats(item) for key, item in value.items()}
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value

def canonicalize_webhook(event: dict[str, Any]) -> bytes:
    normalized = _shorten_floats(event)
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return canonical.encode("utf-8")

def verify_didit_signature(event: dict[str, Any], signature: str, secret: str) -> bool:
    if not signature or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), canonicalize_webhook(event), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())

@router.post("/webhook", include_in_schema=False)
async def didit_webhook(request: Request, x_signature_v2: str = Header(default="", alias="X-Signature-V2")):
    if not didit_settings.didit_webhook_secret:
        raise HTTPException(status_code=503, detail="Didit webhook verification is not configured")
    raw = await request.body()
    if len(raw) > didit_settings.didit_webhook_max_body_bytes:
        raise HTTPException(status_code=413, detail="Webhook payload too large")
    try:
        event = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"Invalid JSON constant: {value}")))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid webhook JSON")
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="Invalid webhook payload")
    if not verify_didit_signature(event, x_signature_v2, didit_settings.didit_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid Didit webhook signature")
    timestamp = event.get("timestamp")
    if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
        raise HTTPException(status_code=401, detail="Missing signed webhook timestamp")
    if abs(time.time() - float(timestamp)) > didit_settings.didit_webhook_tolerance_seconds:
        raise HTTPException(status_code=401, detail="Stale Didit webhook event")
    try:
        outcome = await DiditRepository().process_webhook(event)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not persist Didit webhook event") from exc
    return {"ok": True, "outcome": outcome}
