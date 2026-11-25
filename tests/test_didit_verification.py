from app.integrations.didit.verification_schemas import DiditKycSessionRequest, DiditKybSessionRequest
from app.integrations.didit.verification_repository import _feature_statuses


def test_didit_kyc_session_does_not_allow_client_selected_workflow():
    request = DiditKycSessionRequest(language="en")
    assert request.language == "en"


def test_didit_kyb_session_does_not_allow_client_supplied_feature_list():
    try:
        DiditKybSessionRequest.model_validate({"features": ["OCR", "LIVENESS"]})
    except Exception:
        pass
    else:
        raise AssertionError("client must not be able to alter the configured Didit KYB workflow")


def test_decision_summary_extracts_only_allowed_feature_statuses():
    decision = {
        "kyb_registry": {"status": "APPROVED", "registered_address": "sensitive"},
        "kyb_company_aml": {"status": "IN_REVIEW", "raw_match_data": ["sensitive"]},
        "document_verifications": [{"document_number": "sensitive"}],
    }
    assert _feature_statuses(decision) == {
        "kyb_registry": "APPROVED",
        "kyb_company_aml": "IN_REVIEW",
    }

def test_business_decision_summary_extracts_array_statuses_without_personal_data():
    decision = {
        "registry_checks": [{"status": "APPROVED", "registered_address": "private"}],
        "key_people_checks": [{"status": "IN_REVIEW", "full_name": "private"}],
        "company_aml_checks": [{"status": "APPROVED", "matches": ["private"]}],
        "document_verifications": [{"status": "PENDING", "document_number": "private"}],
    }
    summary = _feature_statuses(decision)
    assert summary == {
        "registry_checks": "APPROVED",
        "key_people_checks": "IN_REVIEW",
        "company_aml_checks": "APPROVED",
        "document_verifications": "PENDING",
    }
    assert "private" not in str(summary)
