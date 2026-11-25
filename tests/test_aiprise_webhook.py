import hashlib
import hmac

from app.api.v1.aiprise import verify_aiprise_signature
from app.integrations.muinmos.agents import AGENTS, agent_catalog


def test_aiprise_signature_uses_exact_raw_body():
    body = b'{ "event_type": "VERIFICATION_SESSION_STARTED", "verification_session_id": "vs_1" }'
    secret = "test-private-key"
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_aiprise_signature(body, signature, secret)
    assert not verify_aiprise_signature(body.replace(b" ", b""), signature, secret)


def test_muinmos_catalog_contains_all_ten_named_agents():
    keys = {agent.key for agent in AGENTS}
    assert len(AGENTS) == 10
    assert {
        "orchestration", "screening", "identity_verification", "proof_of_residence",
        "kyb", "client_classification", "suitability_appropriateness",
        "document_collection", "client_risk_assessment", "perpetual_kyc_monitoring",
    } == keys
    assert len(agent_catalog()) == 10
