"""
DPI Sentinel witness — canonical serialization + signing.

Canonical JSON here means: sorted keys, no extraneous whitespace, and a
fixed separator style. This is required because Ed25519 signs bytes, not
Python dicts — two dicts that are semantically identical can serialize to
different byte strings (key order, spacing) if left to json.dumps()
defaults, which would make the same observation fail signature
verification depending on incidental serialization differences.
"""

import hashlib
import json

from nacl.signing import SigningKey


def _normalize_numbers(obj):
    """Must stay byte-for-byte identical to backend/signing.py's copy of
    this function (see that file's docstring) — collapses whole-number
    floats to ints before hashing so canonicalization doesn't depend on a
    Python-only int/float distinction that other JSON implementations
    (e.g. JavaScript's Number type) don't preserve."""
    if isinstance(obj, dict):
        return {k: _normalize_numbers(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_numbers(v) for v in obj]
    if isinstance(obj, bool):
        return obj  # bool is a subclass of int in Python — must check first
    if isinstance(obj, float) and obj.is_integer():
        return int(obj)
    return obj


def canonical_json_bytes(obj: dict) -> bytes:
    return json.dumps(_normalize_numbers(obj), sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_observation(observation: dict, signing_key: SigningKey) -> dict:
    """Hash the canonical serialization, sign the hash, attach hex-encoded
    hash + signature to a copy of the observation."""
    payload = canonical_json_bytes(observation)
    digest = hashlib.sha256(payload).digest()
    signature = signing_key.sign(digest).signature

    signed = dict(observation)
    signed["hash"] = digest.hex()
    signed["signature"] = signature.hex()
    return signed
