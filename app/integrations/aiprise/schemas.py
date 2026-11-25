from datetime import date
from typing import Any
from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator


class UserVerificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_name: str = Field(min_length=1, max_length=120)
    last_name: str = Field(min_length=1, max_length=120)
    date_of_birth: date | None = None
    email_address: EmailStr | None = None
    phone_number: str | None = Field(default=None, min_length=7, max_length=32)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    address: dict[str, Any] | None = None

    @field_validator("phone_number")
    @classmethod
    def normalize_phone(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class EmailCheckInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email_address: EmailStr


class PhoneCheckInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone_number: str = Field(min_length=7, max_length=32)


class BusinessVerificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=256)
    alternate_name: str | None = Field(default=None, max_length=256)
    tax_identification_number: str | None = Field(default=None, max_length=128)
    website: HttpUrl | None = None
    formation_date: date | None = None
    country_code: str = Field(min_length=2, max_length=2)
    state_code: str | None = Field(default=None, max_length=32)
    business_entity_id: str | None = Field(default=None, max_length=128)
    addresses: list[dict[str, Any]] = Field(default_factory=list)
    phone_numbers: list[str] = Field(default_factory=list)
    email_addresses: list[str] = Field(default_factory=list)
    additional_information: list[dict[str, Any]] = Field(default_factory=list)
