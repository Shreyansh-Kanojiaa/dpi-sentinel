"""
DPI Sentinel aggregator — signature verification.

This mirrors witness/signing.py's canonical_json_bytes() exactly (sorted
keys, fixed separators) because the two services are independently
deployed and don't share a package — but the byte-for-byte serialization
must match, or a genuine, untampered observation would fail verification
for no reason other than incidental formatting differences. If you change
one, change the other.

Verification never trusts anything the payload claims about its own hash:
the hash is always recomputed here from the raw observation fields, and
THAT recomputed hash is what gets checked against the signature. This is
what makes tampering detectable — a payload that had one field edited
after signing will produce a different hash than the one that was
actually signed, so the signature (computed over the original hash) will
no longer verify.
"""

import hashlib
import json

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey


def canonical_json_bytes(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_observation_signature(observation: dict, signature_hex: str, public_key_hex: str) -> bool:
    """
    observation must be exactly the raw fields the witness signed
    (witness_id, timestamp, target, reachable, http_status, latency_ms,
    error) — never the payload's own "hash"/"signature" claims.
    """
    payload = canonical_json_bytes(observation)
    digest = hashlib.sha256(payload).digest()

    try:
        verify_key = VerifyKey(bytes.fromhex(public_key_hex))
        verify_key.verify(digest, bytes.fromhex(signature_hex))
        return True
    except (BadSignatureError, ValueError):
        return False
