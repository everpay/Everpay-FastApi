from decimal import Decimal
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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

    @model_validator(mode="after")
    def validate_crypto_fields(self):
        if self.currency_kind == "crypto" and not self.crypto_params:
            raise ValueError("crypto_params.crypto_chain is required for crypto transactions")
        if self.currency_kind == "fiat" and self.crypto_params:
            raise ValueError("crypto_params is only valid for crypto transactions")
        return self

class Subject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_type: Literal["individual", "company"] = "individual"
    vendor_data: str = Field(min_length=1, max_length=128)
    full_name: str = Field(min_length=1, max_length=256)
    payment_method: PaymentMethod | None = None

class Counterparty(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str | None = Field(default=None, max_length=256)
    payment_method: PaymentMethod | None = None

class ServerTransactionRequest(BaseModel):
    """Construct only from trusted, persisted Everpay transaction data."""
    model_config = ConfigDict(extra="forbid")
    transaction_id: str = Field(min_length=1, max_length=128)
    transaction_category: TransactionCategory = "finance"
    transaction_details: TransactionDetails
    subject: Subject
    counterparty: Counterparty | None = None

    @model_validator(mode="after")
    def validate_crypto_wallet_location(self):
        details = self.transaction_details
        if details.currency_kind == "crypto" and details.payment_reference_id:
            wallet = self.counterparty.payment_method if details.direction == "outbound" and self.counterparty else self.subject.payment_method if details.direction == "inbound" else None
            if wallet is None or wallet.method_type not in ("crypto_wallet", "unhosted_wallet"):
                raise ValueError("A crypto wallet payment_method is required on the counterparty for outbound or subject for inbound transfers with a transaction hash")
        return self

    def didit_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude_none=True)
        payload["transaction_details"]["amount"] = str(self.transaction_details.amount)
        return payload

class DeviceTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction_id: str = Field(min_length=1, max_length=128)
