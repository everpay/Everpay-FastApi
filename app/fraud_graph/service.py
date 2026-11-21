"""Record payins and payouts in the Supabase fraud graph.

Usage from payment or payout code (after the money movement's outcome is known):

    from app.fraud_graph import FraudGraphEventIn, FraudGraphEntityIn, enqueue_fraud_graph_event

    enqueue_fraud_graph_event(FraudGraphEventIn(
        merchant_id=merchant_id,
        direction="payout",
        source_ref=str(payout_id),
        amount=amount,
        currency="CAD",
        entities=[FraudGraphEntityIn(kind="bank_account", role="payee", value={
            "institution_number": "003", "transit_number": "12345", "account_number": "1234567"})],
        attributes={"status": "processing", "method": "eft"},
    ))

Entities are fingerprinted in-process before anything is queued, so raw card numbers,
account numbers, addresses, emails and IPs never reach Redis or the database. Recording
never raises into the caller and never blocks a payment: failures are logged and dropped,
and database outages are retried by the Celery task.
"""

from __future__ import annotations

import logging
from datetime import datetime, UTC
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Jsonb
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.fraud_graph.fingerprints import ENTITY_KINDS, GraphEntity, build_entity, rail_for_entity

logger = logging.getLogger(__name__)

Direction = Literal["payin", "payout", "refund"]
RailName = Literal["card", "ach", "eft", "sepa", "wire", "interac", "crypto", "open_banking", "wallet", "other"]
RoleName = Literal["payer", "payee", "customer", "device", "ip"]
KindName = Literal["email", "phone", "ip", "device", "card", "card_token", "bank_account", "crypto_wallet"]
AttributeValue = str | int | float | bool | None

RECORD_SQL = (
    "SELECT public.record_fraud_graph_event("
    "%(merchant_id)s, %(direction)s, %(rail)s, %(source_ref)s, %(amount)s, %(currency)s, "
    "%(occurred_at)s, %(entities)s, %(attributes)s)"
)


class FraudGraphEntityIn(BaseModel):
    """One account or identifier touched by a payment. `value` holds raw data and is never logged."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: KindName
    role: RoleName
    value: str | dict[str, Any] = Field(repr=False)


class FraudGraphEventIn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    merchant_id: UUID
    direction: Direction
    rail: RailName | None = None
    source_ref: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(ge=0, max_digits=20, decimal_places=8)
    currency: str = Field(pattern=r"^[A-Za-z0-9]{3,10}$")
    occurred_at: AwareDatetime | None = None
    entities: list[FraudGraphEntityIn] = Field(min_length=1, max_length=32)
    attributes: dict[str, AttributeValue] = Field(default_factory=dict, max_length=16)

    @field_validator("source_ref")
    @classmethod
    def _strip_ref(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("source_ref is required")
        return v

    @field_validator("attributes")
    @classmethod
    def _small_attributes(cls, v: dict[str, AttributeValue]) -> dict[str, AttributeValue]:
        for key, value in v.items():
            if len(key) > 40 or (isinstance(value, str) and len(value) > 120):
                raise ValueError("attribute keys must be ≤ 40 characters and values ≤ 120")
        return v


class PreparedFraudGraphEvent(BaseModel):
    """What is queued and sent to the database: fingerprints and masked hints only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    merchant_id: UUID
    direction: Direction
    rail: RailName
    source_ref: str
    amount: Decimal
    currency: str
    occurred_at: datetime
    entities: list[dict[str, Any]] = Field(min_length=1, max_length=32)
    attributes: dict[str, AttributeValue] = Field(default_factory=dict)

    @field_validator("entities")
    @classmethod
    def _fingerprints_only(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        allowed = {"kind", "role", "fingerprint", "hint", "attributes"}
        for e in v:
            if set(e) - allowed or e.get("kind") not in ENTITY_KINDS:
                raise ValueError("prepared entities may only carry kind, role, fingerprint, hint and attributes")
            fp = e.get("fingerprint")
            if not isinstance(fp, str) or len(fp) != 64 or any(c not in "0123456789abcdef" for c in fp):
                raise ValueError("prepared entities must carry a 64-character hex fingerprint")
        return v


def _default_rail(event: FraudGraphEventIn) -> str:
    role = "payee" if event.direction == "payout" else "payer"
    for e in event.entities:
        if e.role == role:
            return rail_for_entity(e.kind, e.value)
    return "other"


def prepare_event(event: FraudGraphEventIn, secret: str) -> PreparedFraudGraphEvent | None:
    """Fingerprint the event's entities. Unusable inputs are dropped; None if nothing is left."""
    unique: dict[tuple[str, str, str], GraphEntity] = {}
    for e in event.entities:
        built = build_entity(secret, e.role, e.kind, e.value)
        if built is not None:
            unique[(built.kind, built.fingerprint, built.role)] = built
    if not unique:
        return None
    return PreparedFraudGraphEvent(
        merchant_id=event.merchant_id,
        direction=event.direction,
        rail=event.rail or _default_rail(event),
        source_ref=event.source_ref,
        amount=event.amount,
        currency=event.currency.upper(),
        occurred_at=event.occurred_at or datetime.now(UTC),
        entities=[g.as_payload() for g in unique.values()],
        attributes=dict(event.attributes),
    )


def record_prepared(connection: Connection, prepared: PreparedFraudGraphEvent) -> str:
    """Write one prepared event through record_fraud_graph_event(); returns the event node id."""
    cursor = connection.execute(
        RECORD_SQL,
        {
            "merchant_id": prepared.merchant_id,
            "direction": prepared.direction,
            "rail": prepared.rail,
            "source_ref": prepared.source_ref,
            "amount": prepared.amount,
            "currency": prepared.currency,
            "occurred_at": prepared.occurred_at,
            "entities": Jsonb(prepared.entities),
            "attributes": Jsonb(prepared.attributes),
        },
    )
    row = cursor.fetchone()
    connection.commit()
    return str(row[0])


def enqueue_fraud_graph_event(event: FraudGraphEventIn) -> bool:
    """Fingerprint and queue an event for recording. Never raises; returns whether it was queued."""
    from app.config import get_settings
    from app.tasks.fraud_graph import record_event

    try:
        settings = get_settings()
        if settings.fraud_graph_hmac_key is None or settings.supabase_database_url is None:
            logger.info("fraud graph recording is not configured; event %s skipped", event.source_ref)
            return False
        prepared = prepare_event(event, settings.fraud_graph_hmac_key.get_secret_value())
        if prepared is None:
            return False
        record_event.apply_async(args=[prepared.model_dump(mode="json")])
        return True
    except Exception as exc:  # never break the payment path
        logger.warning("fraud graph event %s not queued: %s", event.source_ref, type(exc).__name__)
        return False
