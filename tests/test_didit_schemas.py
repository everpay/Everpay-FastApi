from decimal import Decimal
import pytest
from pydantic import ValidationError
from app.integrations.didit.schemas import ServerTransactionRequest, TransactionDetails

def test_fiat_server_transaction_payload_uses_decimal_string():
    request = ServerTransactionRequest(
        transaction_id="ep_txn_123",
        transaction_details={"direction": "outbound", "amount": Decimal("10.25"), "currency": "usd"},
        subject={"vendor_data": "user-1", "full_name": "Ada Lovelace"},
    )
    payload = request.didit_payload()
    assert payload["transaction_details"]["currency"] == "USD"
    assert payload["transaction_details"]["amount"] == "10.25"

def test_crypto_requires_chain():
    with pytest.raises(ValidationError):
        TransactionDetails(direction="inbound", amount=Decimal("1"), currency="BTC", currency_kind="crypto")

def test_outbound_crypto_hash_requires_counterparty_wallet():
    with pytest.raises(ValidationError):
        ServerTransactionRequest(
            transaction_id="ep_crypto_1",
            transaction_details={"direction": "outbound", "amount": "0.5", "currency": "ETH",
                "currency_kind": "crypto", "payment_reference_id": "0xabc", "crypto_params": {"crypto_chain": "ETH"}},
            subject={"vendor_data": "user-1", "full_name": "Ada Lovelace"},
        )
