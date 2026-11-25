from pydantic import BaseModel, ConfigDict, Field


class DiditKycSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str | None = Field(default="en", min_length=2, max_length=8)


class DiditKybSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str | None = Field(default="en", min_length=2, max_length=8)
