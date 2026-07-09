"""
DPI Sentinel aggregator — Ed25519 identity (Milestone 3).

This is deliberately the SAME load-or-generate pattern as
witness/identity.py (PyNaCl, raw seed persisted to KEY_PATH), just for a
different party. A witness's key answers "I, this witness, observed X." The
aggregator's key answers a different question: "I, the aggregator, attest
that the append-only log looked exactly like this — Merkle root R — up to
sequence N, as of this timestamp." No witness can make that statement (a
witness never sees the whole log or the tree over it), so the aggregator
needs its own identity rather than borrowing one witness's.

As with the witness, this is the only module that reads/writes the
aggregator's private-key bytes.
"""

import logging
import os
from pathlib import Path

from nacl.signing import SigningKey

logger = logging.getLogger("aggregator.identity")

AGGREGATOR_KEY_PATH = os.environ.get("AGGREGATOR_KEY_PATH", "./aggregator.key")

_signing_key: SigningKey | None = None


def load_or_create_signing_key(key_path: str = AGGREGATOR_KEY_PATH) -> SigningKey:
    path = Path(key_path)
    if path.exists():
        return SigningKey(path.read_bytes())

    signing_key = SigningKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(signing_key.encode())
    os.chmod(path, 0o600)
    logger.info("generated new aggregator identity at %s", key_path)
    return signing_key


def get_signing_key() -> SigningKey:
    """Process-wide singleton so the checkpoint job and any endpoint that
    needs the aggregator public key share one identity."""
    global _signing_key
    if _signing_key is None:
        _signing_key = load_or_create_signing_key()
    return _signing_key


def public_key_hex() -> str:
    return get_signing_key().verify_key.encode().hex()
