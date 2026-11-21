"""Spec v1 fingerprints. vectors.json is shared with Enki's TypeScript implementation."""

import json
from pathlib import Path

import pytest

from app.fraud_graph.fingerprints import (
    build_entity,
    canonicalize,
    entity_from_details,
    fingerprint,
    rail_for,
    rail_for_entity,
)

VECTORS = json.loads((Path(__file__).resolve().parents[1] / "app" / "fraud_graph" / "vectors.json").read_text("utf-8"))
KEY = VECTORS["key"]


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda c: f"{c['kind']}:{json.dumps(c['input'], ensure_ascii=False)[:40]}")
def test_spec_vectors(case):
    got = canonicalize(case["kind"], case["input"])
    if case["canonical"] is None:
        assert got is None, case.get("note")
        return
    assert got is not None
    assert got.canonical == case["canonical"]
    assert got.hint == case["hint"]
    assert got.attributes == case["attributes"]
    assert fingerprint(KEY, case["kind"], case["canonical"]) == case["fingerprint"]


@pytest.mark.parametrize("method,rail", sorted(VECTORS["rails"].items()))
def test_rails(method, rail):
    assert rail_for(method) == rail


def test_short_keys_are_refused():
    with pytest.raises(ValueError, match="32 bytes"):
        fingerprint("too-short", "email", "a@b.co")


def test_hints_never_contain_the_full_value():
    for case in VECTORS["cases"]:
        if case["canonical"] and case["kind"] != "ip":
            assert case["canonical"] not in canonicalize(case["kind"], case["input"]).hint


def test_surrogate_pairs_are_not_split_in_hints():
    assert canonicalize("device", "😀😀😀😀😀😀😀😀").hint == "😀😀😀😀😀😀…"
    assert canonicalize("email", "😀x@mail.com").hint == "😀***@mail.com"


def test_stored_destinations_map_to_the_same_node():
    kind, value = entity_from_details("bank", {"iban": "GB82 WEST 1234 5698 7654 32", "full_name": "x"})
    direct = build_entity(KEY, "payee", "bank_account", {"iban": "GB82WEST12345698765432"})
    assert build_entity(KEY, "payee", kind, value).fingerprint == direct.fingerprint
    assert entity_from_details("crypto", {"address": "0xAbC0000000000000000000000000000000000001"}, "base")[0] == "crypto_wallet"
    assert entity_from_details("card", {"card_number": "4111111111111111"}) == ("card", "4111111111111111")
    assert entity_from_details("crypto", {"memo": "x"}) is None
    assert entity_from_details("bank", None) is None


def test_payout_rails():
    canadian = {"institution_number": "3", "transit_number": "1", "account_number": "1234567"}
    assert rail_for_entity(*entity_from_details("bank", canadian)) == "eft"
    assert rail_for_entity(*entity_from_details("bank", {"routing_number": "021000021", "account_number": "1234567"})) == "ach"
    assert rail_for_entity(*entity_from_details("bank", {"iban": "GB82WEST12345698765432"})) == "wire"
    assert rail_for_entity("crypto_wallet") == "crypto"
    assert rail_for_entity(None) == "other"
