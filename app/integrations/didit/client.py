from typing import Any
import httpx
from app.integrations.didit.config import didit_settings

class DiditAPIError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail[:2000]
        super().__init__(f"Didit API returned HTTP {status_code}")

class DiditClient:
    def __init__(self) -> None:
        if not didit_settings.didit_api_key:
            raise RuntimeError("DIDIT_API_KEY is not configured")
        self.base_url = didit_settings.didit_base_url.rstrip("/")
        self.timeout = didit_settings.didit_timeout_seconds
        self.api_key = didit_settings.didit_api_key

    async def _request(self, method: str, path: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"x-api-key": self.api_key, "Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(method, f"{self.base_url}{path}", headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise DiditAPIError(504, "Didit request timed out; outcome may be unknown. Reconcile before retrying.") from exc
        except httpx.HTTPError as exc:
            raise DiditAPIError(502, "Could not reach Didit; outcome may be unknown. Reconcile before retrying.") from exc
        if not response.is_success:
            raise DiditAPIError(response.status_code, response.text)
        try:
            result = response.json()
        except ValueError as exc:
            raise DiditAPIError(502, "Didit returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise DiditAPIError(502, "Didit returned an unexpected response")
        return result

    async def create_transaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/v3/transactions/", payload=payload)

    async def get_transaction(self, didit_uuid: str) -> dict[str, Any]:
        return await self._request("GET", f"/v3/transactions/{didit_uuid}/")

    async def create_sdk_token(self, vendor_data: str) -> dict[str, Any]:
        return await self._request("POST", "/v3/transactions/sdk-token/", payload={
            "vendor_data": vendor_data,
            "ttl_seconds": didit_settings.didit_sdk_token_ttl_seconds,
            "max_uses": 1,
        })
