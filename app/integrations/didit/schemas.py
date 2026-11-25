from decimal import Decimal
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

Direction = Literal["inbound", "outbound"]
CurrencyKind = Literal["fiat", "crypto"]
TransactionCategory = Literal["finance", "kyc", "travel_rule", "user_event", "audit_trail_event", "gambling_bet", "gambling_limit_change", "gambling_bonus_change"]

class PaymentMethod(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method_type: Literal["bank_card", "bank_account", "e_wallet", "crypto_wallet", "unhosted_wallet", "other"]
    account_id: str = Field(min_length=1, max_length=512)

class CryptoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    crypto_chain: Literal["BTC", "LIGHTNING", "ETH", "LTC", "XRP", "BCH", "DOGE", "TRX", "SOL", "MATIC", "BNB", "BASE", "OP", "ARB"]

class TransactionDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: Direction
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=1, max_length=10)
    currency_kind: CurrencyKind = "fiat"
    payment_reference_id: str | None = Field(default=None, max_length=256)
    crypto_params: CryptoParams | None = None
    include_crypto_screening: bool | None = None

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("currency is required")
        return normalized

class Subject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_type: Literal["individual", "company"] = "individual"
    vendor_data: str = Field(min_length=1, max_length=128)
    full_name: str = Field(min_length=1, max_length=256)

class Counterparty(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str | None = Field(default=None, max_length=256)
    payment_method: PaymentMethod | None = None

class ServerTransactionRequest(BaseModel):
    """Only construct this from trusted, persisted Everpay transaction data."""
    model_config = ConfigDict(extra="forbid")
    transaction_id: str = Field(min_length=1, max_length=128)
    transaction_category: TransactionCategory = "finance"
    transaction_details: TransactionDetails
    subject: Subject
    counterparty: Counterparty | None = None

    def didit_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude_none=True)
        payload["transaction_details"]["amount"] = str(self.transaction_details.amount)
        return payload

class DeviceTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction_id: str = Field(min_length=1, max_length=128)
