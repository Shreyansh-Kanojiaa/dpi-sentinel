#!/usr/bin/env python3
"""
backend/signing.py and witness/signing.py must produce byte-identical
canonical JSON. They are deliberately separate files (the witness is an
independently deployable service and does not import from the aggregator),
which means nothing stops them drifting apart except a check like this.

If they diverge, every honest observation starts failing signature
verification at POST /observations, and the aggregator looks like it is
rejecting witnesses for tampering when the real fault is a serializer edit.
That failure is confusing enough to be worth one assert.
"""

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Observations use exactly these fields (see witness/prober.probe_and_report).
# Values chosen to exercise the parts serializers usually disagree on:
# unicode, None, floats, bools, and non-alphabetical key order.
SAMPLE = {
    "witness_id": "witness-a",
    "timestamp": "2026-01-01T00:00:00+00:00",
    "target": "http://demo-target/",
    "reachable": False,
    "http_status": None,
    "latency_ms": 1.5,
    "error": "connect_error: naïve — dash",
}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    aggregator = load("agg_signing", ROOT / "backend" / "signing.py")
    witness = load("wit_signing", ROOT / "witness" / "signing.py")

    a = aggregator.canonical_json_bytes(SAMPLE)
    b = witness.canonical_json_bytes(SAMPLE)

    if a != b:
        print("canonical_json_bytes DIVERGED", file=sys.stderr)
        print(f"  backend/signing.py: {a!r}", file=sys.stderr)
        print(f"  witness/signing.py: {b!r}", file=sys.stderr)
        return 1

    print(f"canonical_json_bytes agree ({len(a)} bytes)")
    print(f"  {a.decode()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
