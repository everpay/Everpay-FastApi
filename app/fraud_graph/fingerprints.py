"""Fraud graph fingerprints, spec v1.

Every payin and payout records the accounts and identifiers it touched as keyed
fingerprints: HMAC-SHA256(FRAUD_GRAPH_HMAC_KEY, "v1|<kind>|<canonical>"). Raw card
numbers, account numbers, wallet addresses, emails and IPs never leave this module;
only fingerprints, masked hints and a few non-sensitive attributes do.

This is the same spec as Enki's supabase/functions/_shared/fraud-graph.ts. Both are
checked against vectors.json; a change to one must be made to the other. The rules
below mirror JavaScript string semantics (trim, \\s, lengths) so the two agree.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal
from collections.abc import Mapping

SPEC_VERSION = "v1"
MIN_KEY_BYTES = 32

EntityKind = Literal["email", "phone", "ip", "device", "card", "card_token", "bank_account", "crypto_wallet"]
EntityRole = Literal["payer", "payee", "customer", "device", "ip"]
Rail = Literal["card", "ach", "eft", "sepa", "wire", "interac", "crypto", "open_banking", "wallet", "other"]

ENTITY_KINDS: frozenset[str] = frozenset(
    {"email", "phone", "ip", "device", "card", "card_token", "bank_account", "crypto_wallet"}
)

# JavaScript's String.prototype.trim() and \s character set.
_JS_WS = (
    "\t\n\x0b\x0c\r             "
    "     　﻿"
)
_JS_WS_RE = re.compile(f"[{re.escape(_JS_WS)}]")
_JS_WS_RUN_RE = re.compile(f"[{re.escape(_JS_WS)}]+")


@dataclass(frozen=True)
class Canonical:
    canonical: str
    hint: str
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphEntity:
    kind: str
    role: str
    fingerprint: str
    hint: str
    attributes: dict[str, str]

    def as_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "role": self.role,
            "fingerprint": self.fingerprint,
            "hint": self.hint,
            "attributes": dict(self.attributes),
        }


def _clean(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return unicodedata.normalize("NFKC", value).strip(_JS_WS)


def _js_len(value: str) -> int:
    """Length in UTF-16 code units, as JavaScript counts it."""
    return len(value.encode("utf-16-le")) // 2


def _digits(value: str) -> str:
    return re.sub(r"[^0-9]", "", value)


def _last4(value: str) -> str:
    return value[-4:]


def _full(pattern: str, value: str, flags: int = 0) -> bool:
    return re.fullmatch(pattern, value, flags) is not None


# ── Canonicalisation ────────────────────────────────────────────────────────


def _email(raw: Any) -> Canonical | None:
    v = _clean(raw).lower()
    if _js_len(v) > 254 or _JS_WS_RE.search(v):
        return None
    at = v.find("@")
    if at < 1 or at != v.rfind("@") or at == len(v) - 1:
        return None
    domain = v[at + 1 :]
    if "." not in domain or domain.startswith(".") or domain.endswith("."):
        return None
    return Canonical(v, f"{v[0]}***@{domain}")


def _phone(raw: Any) -> Canonical | None:
    d = _digits(_clean(raw))
    if len(d) < 7 or len(d) > 15:
        return None
    return Canonical(d, f"••{_last4(d)}")


def _ipv4_octets(v: str) -> list[int] | None:
    parts = v.split(".")
    if len(parts) != 4:
        return None
    out: list[int] = []
    for p in parts:
        if not _full(r"0|[1-9][0-9]{0,2}", p):
            return None
        n = int(p)
        if n > 255:
            return None
        out.append(n)
    return out


def _ipv4_routable(o: list[int]) -> bool:
    a, b = o[0], o[1]
    if a in (0, 10, 127) or a >= 224:
        return False
    if a == 100 and 64 <= b <= 127:  # carrier-grade NAT
        return False
    if a == 169 and b == 254:
        return False
    if a == 172 and 16 <= b <= 31:
        return False
    if a == 192 and b == 168:
        return False
    return True


def _ipv6_hextets(v: str) -> list[int] | None:
    """Expand an IPv6 address to 8 hextets; "::" must stand for at least one group."""
    s = v.lower()
    zone = s.find("%")
    if zone >= 0:
        s = s[:zone]
    if not _full(r"[0-9a-f:.]+", s):
        return None
    last_colon = s.rfind(":")
    if last_colon < 0:
        return None
    tail = s[last_colon + 1 :]
    if "." in tail:
        v4 = _ipv4_octets(tail)
        if v4 is None:
            return None
        s = f"{s[: last_colon + 1]}{(v4[0] << 8 | v4[1]):x}:{(v4[2] << 8 | v4[3]):x}"
    halves = s.split("::")
    if len(halves) > 2:
        return None

    def split(part: str) -> list[str]:
        return [] if part == "" else part.split(":")

    groups = split(halves[0])
    if len(halves) == 2:
        rest = split(halves[1])
        fill = 8 - len(groups) - len(rest)
        if fill < 1:
            return None
        groups = groups + ["0"] * fill + rest
    if len(groups) != 8:
        return None
    out: list[int] = []
    for g in groups:
        if not _full(r"[0-9a-f]{1,4}", g):
            return None
        out.append(int(g, 16))
    return out


def _ip(raw: Any) -> Canonical | None:
    v = _clean(raw)
    if not v:
        return None
    v4 = _ipv4_octets(v)
    if v4 is not None:
        if not _ipv4_routable(v4):
            return None
        return Canonical(".".join(map(str, v4)), f"{v4[0]}.{v4[1]}.{v4[2]}.x", {"family": "ipv4"})
    h = _ipv6_hextets(v)
    if h is None:
        return None
    if all(x == 0 for x in h[:5]) and h[5] == 0xFFFF:  # IPv4-mapped
        return _ip(f"{h[6] >> 8}.{h[6] & 255}.{h[7] >> 8}.{h[7] & 255}")
    if all(x == 0 for x in h):
        return None
    if all(x == 0 for x in h[:7]) and h[7] == 1:
        return None
    if (h[0] & 0xFE00) == 0xFC00 or (h[0] & 0xFFC0) == 0xFE80 or (h[0] & 0xFF00) == 0xFF00:
        return None
    # ISPs hand each customer a /64, so the /64 is the stable identifier.
    prefix = ":".join(f"{x:04x}" for x in h[:4])
    return Canonical(f"{prefix}::/64", f"{prefix[:9]}…/64", {"family": "ipv6"})


def _device(raw: Any) -> Canonical | None:
    v = _clean(raw)
    if _js_len(v) < 8 or _js_len(v) > 256:
        return None
    return Canonical(v, f"{v[:6]}…")


def _luhn(d: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = ord(ch) - 48
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _card(raw: Any) -> Canonical | None:
    v = _clean(raw)
    if not _full(r"[0-9 -]+", v):
        return None
    d = _digits(v)
    if len(d) < 12 or len(d) > 19 or not _luhn(d):
        return None
    return Canonical(d, f"{d[:6]}••{_last4(d)}", {"bin": d[:6], "last4": _last4(d)})


def _card_token(raw: Any) -> Canonical | None:
    raw = raw if isinstance(raw, Mapping) else {}
    provider = _clean(raw.get("provider")).lower()
    token = _clean(raw.get("token"))
    if not _full(r"[a-z0-9_-]{2,32}", provider) or _js_len(token) < 6 or _js_len(token) > 256:
        return None
    l4 = _digits(_clean(raw.get("last4") or ""))
    attrs = {"provider": provider}
    if len(l4) == 4:
        attrs["last4"] = l4
    return Canonical(f"{provider}:{token}", f"••{l4}" if len(l4) == 4 else f"{provider} token", attrs)


def _iban_valid(iban: str) -> bool:
    if not _full(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", iban):
        return False
    moved = iban[4:] + iban[:4]
    rem = 0
    for ch in moved:
        for c in (str(ord(ch) - 55) if ch.isalpha() else ch):
            rem = (rem * 10 + int(c)) % 97
    return rem == 1


def _clabe_valid(c: str) -> bool:
    if not _full(r"[0-9]{18}", c):
        return False
    weights = (3, 7, 1)
    total = sum((int(c[i]) * weights[i % 3]) % 10 for i in range(17))
    return (10 - total % 10) % 10 == int(c[17])


def _aba_valid(r: str) -> bool:
    if not _full(r"[0-9]{9}", r):
        return False
    d = [int(x) for x in r]
    return (3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])) % 10 == 0


def _pad(value: str, n: int) -> str | None:
    d = _digits(value)
    return d.zfill(n) if 0 < len(d) <= n else None


def _bank_account(raw: Any) -> Canonical | None:
    raw = raw if isinstance(raw, Mapping) else {}
    get = lambda k: _clean(raw.get(k))  # noqa: E731

    iban = _JS_WS_RUN_RE.sub("", get("iban")).upper()
    if iban:
        if not _iban_valid(iban):
            return None
        return Canonical(f"iban:{iban}", f"{iban[:2]} ••{_last4(iban)}", {"country": iban[:2]})
    clabe = _digits(get("clabe"))
    if clabe:
        if not _clabe_valid(clabe):
            return None
        return Canonical(f"clabe:{clabe}", f"MX ••{_last4(clabe)}", {"country": "MX"})
    acct = re.sub(f"[{re.escape(_JS_WS)}.-]", "", get("account_number")).upper()
    if not _full(r"[A-Z0-9]{4,34}", acct):
        return None
    hint = lambda cc: f"{cc} ••{_last4(acct)}"  # noqa: E731
    country = get("country").upper()

    if get("institution_number") or get("transit_number"):
        inst = _pad(get("institution_number"), 3)
        transit = _pad(get("transit_number"), 5)
        if not inst or not transit:
            return None
        return Canonical(f"ca:{inst}:{transit}:{acct}", hint("CA"), {"country": "CA", "institution": inst})
    routing = _digits(get("routing_number"))
    if routing:
        if len(routing) != 9:
            return None
        if country != "CA" and _aba_valid(routing):
            return Canonical(f"us:{routing}:{acct}", hint("US"), {"country": "US", "routing": routing})
        # Canadian EFT routing number: 0 + institution (3) + transit (5).
        if country in ("CA", "") and routing.startswith("0"):
            inst = routing[1:4]
            return Canonical(f"ca:{inst}:{routing[4:]}:{acct}", hint("CA"), {"country": "CA", "institution": inst})
        return None
    sort = _digits(get("sort_code"))
    if sort:
        return Canonical(f"gb:{sort}:{acct}", hint("GB"), {"country": "GB"}) if len(sort) == 6 else None
    bsb = _digits(get("bsb"))
    if bsb:
        return Canonical(f"au:{bsb}:{acct}", hint("AU"), {"country": "AU"}) if len(bsb) == 6 else None
    ifsc = get("ifsc").upper()
    if ifsc:
        if not _full(r"[A-Z]{4}0[A-Z0-9]{6}", ifsc):
            return None
        return Canonical(f"in:{ifsc}:{acct}", hint("IN"), {"country": "IN"})
    bic = _JS_WS_RUN_RE.sub("", get("swift_bic")).upper()
    if bic:
        if not _full(r"[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?", bic):
            return None
        cc = bic[4:6]
        return Canonical(f"swift:{bic[:8]}:{acct}", hint(cc), {"country": cc, "bic": bic[:8]})
    return None


def _crypto_wallet(raw: Any) -> Canonical | None:
    raw = raw if isinstance(raw, Mapping) else {}
    address = _clean(raw.get("address"))
    short = lambda a: f"{a[:6]}…{a[-4:]}"  # noqa: E731
    # One EVM key controls the same address on every EVM chain, so they share a node.
    if _full(r"0x[0-9a-fA-F]{40}", address):
        a = address.lower()
        return Canonical(f"evm:{a}", short(a), {"network": "evm"})
    network = _clean(raw.get("network")).lower()
    if not _full(r"[a-z0-9_-]{2,32}", network) or not _full(r"[A-Za-z0-9:_-]{20,128}", address):
        return None
    # Bech32 addresses are case-insensitive; base58 and others are not.
    a = address.lower() if re.match(r"(bc1|tb1|ltc1)", address, re.IGNORECASE) else address
    return Canonical(f"{network}:{a}", short(a), {"network": network})


_CANONICALIZERS = {
    "email": _email,
    "phone": _phone,
    "ip": _ip,
    "device": _device,
    "card": _card,
    "card_token": _card_token,
    "bank_account": _bank_account,
    "crypto_wallet": _crypto_wallet,
}


def canonicalize(kind: str, value: Any) -> Canonical | None:
    """Canonical form, masked hint and attributes for an entity; None when the input is unusable."""
    fn = _CANONICALIZERS.get(kind)
    return fn(value) if fn else None


# ── Fingerprints ────────────────────────────────────────────────────────────


def validate_key(secret: str) -> bytes:
    key = secret.encode("utf-8")
    if len(key) < MIN_KEY_BYTES:
        raise ValueError(f"FRAUD_GRAPH_HMAC_KEY must be at least {MIN_KEY_BYTES} bytes")
    return key


def fingerprint(secret: str, kind: str, canonical: str) -> str:
    message = f"{SPEC_VERSION}|{kind}|{canonical}".encode()
    return hmac.new(validate_key(secret), message, hashlib.sha256).hexdigest()


def build_entity(secret: str, role: str, kind: str, value: Any) -> GraphEntity | None:
    c = canonicalize(kind, value)
    if c is None:
        return None
    return GraphEntity(kind, role, fingerprint(secret, kind, c.canonical), c.hint, dict(c.attributes))


# ── Rails and stored destinations ───────────────────────────────────────────

_RAILS: dict[str, tuple[str, ...]] = {
    "card": ("card", "apple_pay", "google_pay", "debit", "credit"),
    "ach": ("ach", "ach_bank", "plaid", "echeck"),
    "eft": ("eft", "dcbank", "bank_eft"),
    "sepa": ("sepa", "sepa_credit", "sepa_instant"),
    "wire": ("wire", "swift", "bank_wire", "remittance", "bank", "bank_transfer"),
    "interac": ("interac", "e_transfer", "etransfer"),
    "crypto": ("crypto", "usdt", "usdc", "btc", "eth", "stablecoin"),
    "open_banking": ("open_banking", "openbanking", "a2a", "pix", "pay_by_bank"),
    "wallet": ("wallet", "paypal", "mobile_money"),
}


def rail_for(method: str | None) -> str:
    """Map payment-method and payout-method names to a rail."""
    m = re.sub(f"[{re.escape(_JS_WS)}-]", "_", str(method or "").lower())
    for rail, names in _RAILS.items():
        if m in names:
            return rail
    return "other"


def _pick(details: Mapping[str, Any], *keys: str) -> str | None:
    for k in keys:
        v = details.get(k)
        if isinstance(v, str) and v.strip():
            return v
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(v)
    return None


def entity_from_details(
    destination_type: str | None,
    details: Mapping[str, Any] | None,
    network: str | None = None,
) -> tuple[str, Any] | None:
    """(kind, value) for a stored payout destination or recipient row."""
    if not isinstance(details, Mapping):
        return None
    t = str(destination_type or "").lower()
    if t in ("crypto", "crypto_wallet", "stablecoin"):
        address = _pick(details, "address", "wallet_address")
        if not address:
            return None
        return "crypto_wallet", {"address": address, "network": network or _pick(details, "network", "wallet_network", "chain")}
    if t == "card":
        number = _pick(details, "card_number", "number", "pan")
        return ("card", number) if number else None
    return "bank_account", {
        "iban": _pick(details, "iban"),
        "clabe": _pick(details, "clabe"),
        "account_number": _pick(details, "account_number", "accountNumber"),
        "routing_number": _pick(details, "routing_number", "routingNumber", "aba"),
        "institution_number": _pick(details, "institution_number", "institutionNumber"),
        "transit_number": _pick(details, "transit_number", "transitNumber"),
        "sort_code": _pick(details, "sort_code", "sortCode"),
        "bsb": _pick(details, "bsb", "bsb_code"),
        "ifsc": _pick(details, "ifsc", "ifsc_code"),
        "swift_bic": _pick(details, "swift_bic", "swift", "bic"),
        "country": _pick(details, "country", "country_code"),
    }


def rail_for_entity(kind: str | None, value: Any = None) -> str:
    """Rail for a payout to the given beneficiary."""
    if kind == "crypto_wallet":
        return "crypto"
    if kind in ("card", "card_token"):
        return "card"
    if kind in ("email", "phone"):
        return "interac"
    if kind == "bank_account" and isinstance(value, Mapping):
        if value.get("institution_number") or value.get("transit_number") or str(value.get("country") or "").upper() == "CA":
            return "eft"
        if value.get("routing_number"):
            return "ach"
        return "wire"
    return "other"
