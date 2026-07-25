#!/usr/bin/env python3
"""
Prove that fetching a certificate by id returns the SAME DOCUMENT that was
issued, and that the printed fingerprint is the digest the signature covers.

Why this needs a check at all: the QR code on a printed certificate carries
only an id and a fingerprint prefix. Everything downstream assumes that
resolving that id reproduces the exact signed bytes. If it ever returned
something merely equivalent-looking, printed certificates would fail
verification for reasons nobody could diagnose from the paper.

NOT compared: the raw HTTP response bytes. FastAPI serialises with its own
encoder, so the wire format legitimately differs in key order and spacing
from the stored canonical JSON. The claim under test is that the DOCUMENT
round-trips, which is why every assert below re-canonicalises first. Do not
"fix" this into a raw byte comparison; it would fail for a correct system.

Usage (needs a running aggregator and an issued certificate):
    python scripts/check_cert_roundtrip.py <certificate_id> [api_base]
"""

import hashlib
import json
import pathlib
import sys
import urllib.request

# signing.py imports PyNaCl, so this only runs somewhere those deps exist.
# In CI that means inside the aggregator container, piped in over stdin --
# where __file__ does not exist and the module is already importable from the
# working directory. Both cases are handled rather than assuming either.
try:
    from signing import canonical_json_bytes
except ImportError:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "backend"))
    from signing import canonical_json_bytes


def get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cert_id = sys.argv[1]
    api = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8420"

    b = get(f"{api}/api/certificates/{cert_id}")
    canonical = canonical_json_bytes(b["certificate"])

    checks = []

    # 1. The fingerprint is the digest the Ed25519 signature is over, not a
    #    parallel hash that could silently drift from it.
    checks.append((
        "fingerprint is the signed digest",
        bytes.fromhex(b["fingerprint"]) == hashlib.sha256(canonical).digest(),
    ))

    # 2. A JSON.parse/JSON.stringify round trip (what every browser does to
    #    this document) must not change the canonical form. This is exactly
    #    the hazard signing._normalize_numbers exists to prevent.
    js_shaped = json.loads(json.dumps(b["certificate"]))
    checks.append((
        "survives a JS-style parse/stringify round trip",
        canonical_json_bytes(js_shaped) == canonical,
    ))

    # 3. The fetched document actually verifies, including after that round
    #    trip — the real end-to-end claim a scanned QR depends on.
    body = json.dumps({"certificate": js_shaped, "signature": b["signature"]}).encode()
    req = urllib.request.Request(
        f"{api}/api/verify", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        v = json.loads(r.read())
    checks.append(("fetched document verifies", v.get("valid") is True))
    for name, res in (v.get("checks") or {}).items():
        passed = res.get("passed")
        # Some checks are intentionally tri-state: passed=None means "not
        # enough anchored data yet to evaluate", not failure.
        checks.append((f"  check: {name}", passed is not False))

    failed = 0
    for name, passed in checks:
        print(f"  {'ok  ' if passed else 'FAIL'} {name}")
        failed += not passed

    print()
    if failed:
        print(f"FAILED: {failed} check(s)")
        return 1
    print("certificate round-trip verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
