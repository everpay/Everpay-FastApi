from typing import Any
from urllib.parse import quote

import httpx

from app.integrations.aiprise.config import aiprise_settings


class AiPriseAPIError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail[:1500]
        super().__init__(f"AiPrise API returned HTTP {status_code}")


class AiPriseClient:
    """Server-side client. POST requests are never blindly retried."""

    def __init__(self) -> None:
        if not aiprise_settings.aiprise_api_key:
            raise RuntimeError("AIPRISE_API_KEY is not configured")
        self.base_url = aiprise_settings.aiprise_base_url.rstrip("/")
        self.timeout = aiprise_settings.aiprise_timeout_seconds
        self.api_key = aiprise_settings.aiprise_api_key

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "X-API-KEY": self.api_key,
            "Accept": "application/json",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method,
                    f"{self.base_url}/{path.lstrip('/')}",
                    headers=headers,
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise AiPriseAPIError(
                504, "AiPrise request timed out; reconcile before resubmitting."
            ) from exc
        except httpx.HTTPError as exc:
            raise AiPriseAPIError(
                502, "Could not reach AiPrise; reconcile before resubmitting."
            ) from exc

        if not response.is_success:
            raise AiPriseAPIError(response.status_code, response.text)
        try:
            result = response.json()
        except ValueError as exc:
            raise AiPriseAPIError(502, "AiPrise returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise AiPriseAPIError(502, "AiPrise returned an unexpected response")
        return result

    async def run_user_verification(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/run_user_verification", payload=payload)

    async def get_user_verification_url(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/get_user_verification_url", payload=payload)

    async def get_user_verification_result(self, session_id: str) -> dict[str, Any]:
        return await self._request(
            "GET", f"/get_user_verification_result/{quote(session_id, safe='')}"
        )

    async def run_business_verification(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/run_business_verification", payload=payload)

    async def create_business_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/create_business_profile", payload=payload)

    async def get_business_verification_result(self, session_id: str) -> dict[str, Any]:
        return await self._request(
            "GET", f"/get_business_verification_result/{quote(session_id, safe='')}"
        )
