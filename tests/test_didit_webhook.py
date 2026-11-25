import hashlib
import hmac
from app.api.v1.didit import canonicalize_webhook, verify_didit_signature

def test_canonicalization_sorts_nested_keys_and_shortens_integral_floats():
    event = {"z": 1.0, "nested": {"b": 2, "a": 3.0}, "items": [{"y": 1.0, "x": 2}]}
    assert canonicalize_webhook(event) == b'{"items":[{"x":2,"y":1}],"nested":{"a":3,"b":2},"z":1}'

def test_signature_verification():
    secret = "test-secret"
    event = {"timestamp": 1795579200, "status": "APPROVED", "event_id": "evt_1"}
    signature = hmac.new(secret.encode(), canonicalize_webhook(event), hashlib.sha256).hexdigest()
    assert verify_didit_signature(event, signature, secret)
    assert not verify_didit_signature(event, signature, "wrong-secret")
    assert not verify_didit_signature(event, signature + "00", secret)
