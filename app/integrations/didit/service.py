from typing import Any
import structlog
from app.integrations.didit.client import DiditAPIError, DiditClient
from app.integrations.didit.repository import DiditRepository
from app.integrations.didit.schemas import ServerTransactionRequest

logger = structlog.get_logger(__name__)

class DiditConflict(RuntimeError):
    pass

class DiditMonitoringService:
    def __init__(self, client: DiditClient | None = None, repository: DiditRepository | None = None):
        self.client = client or DiditClient()
        self.repository = repository or DiditRepository()

    async def screen_server_transaction(self, request: ServerTransactionRequest) -> dict[str, Any]:
        """Screen a transaction whose data was loaded from trusted Everpay records."""
        payload = request.didit_payload()
        existing = await self.repository.reserve_server_transaction(payload)
        if existing is not None:
            raise DiditConflict(f"Transaction already reserved or submitted; reconcile before retrying (status={existing.get('status')}).")
        try:
            decision = await self.client.create_transaction(payload)
        except DiditAPIError as exc:
            await self.repository.mark_submission_uncertain(request.transaction_id, f"HTTP {exc.status_code}: {exc.detail}")
            raise
        if not decision.get("uuid") or decision.get("txn_id") != request.transaction_id or decision.get("status") not in ("APPROVED", "IN_REVIEW", "DECLINED", "AWAITING_USER"):
            await self.repository.mark_submission_uncertain(request.transaction_id, "Didit response missing expected uuid, txn_id or status")
            raise DiditAPIError(502, "Didit response did not include required transaction decision fields")
        await self.repository.save_provider_decision(request.transaction_id, decision)
        logger.info("didit_transaction_screened", transaction_id=request.transaction_id, didit_uuid=decision["uuid"], status=decision["status"], score=decision.get("score"))
        return decision

    async def register_device_transaction(self, transaction_id: str, vendor_data: str) -> None:
        await self.repository.register_device_transaction(transaction_id, vendor_data)
