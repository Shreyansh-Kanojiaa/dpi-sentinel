"""
DPI Sentinel witness — Ed25519 identity.

On first run, generates a keypair and writes the raw private key seed to
KEY_PATH. On subsequent runs, loads it. This is deliberately the only
place private-key bytes are read or written.
"""

import os
from pathlib import Path

from nacl.signing import SigningKey


def load_or_create_signing_key(key_path: str) -> SigningKey:
    path = Path(key_path)
    if path.exists():
        seed = path.read_bytes()
        return SigningKey(seed)

    signing_key = SigningKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(signing_key.encode())
    os.chmod(path, 0o600)
    return signing_key
