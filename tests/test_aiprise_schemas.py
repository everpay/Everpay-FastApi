from pydantic import ValidationError
import pytest

from app.integrations.aiprise.schemas import BusinessVerificationInput, EmailCheckInput, PhoneCheckInput


def test_email_check_rejects_invalid_email():
    with pytest.raises(ValidationError):
        EmailCheckInput(email_address="not-an-email")


def test_phone_check_requires_minimum_length():
    with pytest.raises(ValidationError):
        PhoneCheckInput(phone_number="123")


def test_business_website_must_be_valid_url():
    with pytest.raises(ValidationError):
        BusinessVerificationInput(name="Example Ltd", country_code="US", website="not a url")
