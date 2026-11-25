from typing import Any
import structlog
from app.integrations.didit.client import DiditAPIError, DiditClient
from app.integrations.didit.repository import DiditRepository
from app.integrations.didit.schemas import Counterparty, PaymentMethod, ServerTransactionRequest, Subject, TransactionDetails

logger = structlog.get_logger(__name__)

class DiditConflict(RuntimeError):
    pass

class DiditMonitoringService:
    def __init__(self, client: DiditClient | None = None, repository: DiditRepository | None = None):
        self.client = client or DiditClient()
        self.repository = repository or DiditRepository()

    async def screen_server_transaction(self, request: ServerTransactionRequest) -> dict[str, Any]:
        """Screen transaction data loaded and validated by trusted Everpay services."""
        payload = request.didit_payload()
        existing = await self.repository.reserve_server_transaction(payload)
        if existing is not None:
            raise DiditConflict(f"Transaction already reserved or submitted; reconcile before retrying (status={existing.get('status')}).")
        try:
            decision = await self.client.create_transaction(payload)
        except DiditAPIError as exc:
            await self.repository.mark_submission_uncertain(request.transaction_id, f"Didit HTTP status {exc.status_code}; reconcile before retrying")
            logger.error("didit_screening_submission_uncertain", transaction_id=request.transaction_id, http_status=exc.status_code)
            raise
        if not decision.get("uuid") or decision.get("txn_id") != request.transaction_id or decision.get("status") not in ("APPROVED", "IN_REVIEW", "DECLINED", "AWAITING_USER"):
            await self.repository.mark_submission_uncertain(request.transaction_id, "Didit response missing expected uuid, txn_id or status")
            raise DiditAPIError(502, "Didit response did not include required transaction decision fields")
        await self.repository.save_provider_decision(request.transaction_id, decision)
        logger.info("didit_transaction_screened", transaction_id=request.transaction_id, didit_uuid=decision["uuid"], status=decision["status"], score=decision.get("score"))
        return decision

    async def register_device_transaction(self, transaction_id: str, vendor_data: str,
            transaction_details: TransactionDetails, full_name: str, entity_type: str = "individual",
            counterparty: Counterparty | None = None, subject_payment_method: PaymentMethod | None = None) -> None:
        subject = Subject(entity_type=entity_type, vendor_data=vendor_data, full_name=full_name,
                          payment_method=subject_payment_method)
        # Reuse the server-lane validator so crypto wallet placement and transaction fields
        # are checked before the device lane is registered.
        ServerTransactionRequest(transaction_id=transaction_id, transaction_details=transaction_details,
                                 subject=subject, counterparty=counterparty)
        await self.repository.register_device_transaction(
            transaction_id=transaction_id,
            vendor_data=vendor_data,
            transaction_details=transaction_details.model_dump(mode="json", exclude_none=True),
            subject_snapshot=subject.model_dump(mode="json", exclude_none=True),
            counterparty_snapshot=counterparty.model_dump(mode="json", exclude_none=True) if counterparty else None,
        )
