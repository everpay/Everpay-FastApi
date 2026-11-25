from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class DiditKycSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str | None = Field(default="en", min_length=2, max_length=8)
    callback: str | None = Field(default=None, max_length=2048)
    callback_method: Literal["initiator", "completer", "both"] = "completer"


class DiditKybSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str | None = Field(default="en", min_length=2, max_length=8)
    callback: str | None = Field(default=None, max_length=2048)
    callback_method: Literal["initiator", "completer", "both"] = "completer"


def normalize_didit_status(value: object) -> str:
    normalized = str(value or "").strip().upper().replace(" ", "_")
    allowed = {
        "NOT_STARTED", "IN_PROGRESS", "IN_REVIEW", "APPROVED",
        "DECLINED", "EXPIRED", "ABANDONED", "KYC_EXPIRED",
        "RESUBMITTED", "AWAITING_USER",
    }
    return normalized if normalized in allowed else "IN_REVIEW"
