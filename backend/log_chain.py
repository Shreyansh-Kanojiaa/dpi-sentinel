"""
DPI Sentinel aggregator — the append-only hash chain (Milestone 3).

Every verified observation and every incident-timeline event becomes one
LogEntry here. Each entry commits to its predecessor:

    entry_hash = sha256(prev_hash + payload)

where `payload` is the canonical JSON string of the underlying record's key
fields, produced by the SAME signing.canonical_json_bytes() helper the
witnesses and the /observations verifier use. Using one serializer
everywhere is the whole point: if the log used even a slightly different
JSON encoding than verify_log.py recomputes with, honest entries would
"fail" verification and a real tamper could hide in the discrepancy.

Genesis: the first entry's prev_hash is a fixed, well-known constant
(64 zeros), never NULL/empty. See GENESIS_PREV_HASH below for why that
matters.

Concurrency: POST /observations runs in a threadpool, so several appends
can be in flight at once, and the periodic quorum tick can append from the
event loop at the same time. Computing "next sequence number + prev_hash"
and inserting the row must therefore be one indivisible critical section —
otherwise two appends could read the same max sequence number and fork the
chain. _APPEND_LOCK below makes that explicit; every append holds it across
read-max-seq -> compute-hash -> insert -> commit.
"""

import hashlib
import logging
import threading

from sqlalchemy.orm import Session

from models import LogEntry
from signing import canonical_json_bytes

logger = logging.getLogger("aggregator.log_chain")

# A defined genesis anchors the whole chain to a value that is NOT itself a
# real entry_hash. If the first entry's prev_hash were NULL/empty instead:
#   - "what does entry #1 chain to?" would be undefined, so verify_log.py
#     couldn't recompute its entry_hash deterministically;
#   - an attacker who wanted to delete the true first entry and promote the
#     second could just blank out the new front entry's prev_hash and it
#     would look like a legitimate start — there'd be nothing pinning down
#     where the log actually begins. A fixed genesis makes "this is entry #1"
#     a checkable claim, not a matter of an empty field.
GENESIS_PREV_HASH = "0" * 64

# Serializes the read-max-seq -> insert -> commit critical section across
# every thread and the event loop. Held only for the (fast) append itself.
_APPEND_LOCK = threading.Lock()


def compute_entry_hash(prev_hash: str, payload: str) -> str:
    """The one definition of an entry's hash. verify_log.py MUST recompute
    it exactly this way, byte for byte, or verification is meaningless."""
    return hashlib.sha256((prev_hash + payload).encode("utf-8")).hexdigest()


def append_log_entry(db: Session, entry_type: str, payload_fields: dict) -> LogEntry:
    """
    Append one entry to the chain and commit it, atomically w.r.t. other
    appends. `payload_fields` is a plain dict of the record's key fields; it
    is canonicalized here so the stored payload string and the recomputed
    one in verify_log.py are identical.

    The commit happens inside the lock on purpose: releasing the lock before
    committing would let another append read a stale max sequence number and
    assign a duplicate, forking the chain.
    """
    payload = canonical_json_bytes(payload_fields).decode("utf-8")

    with _APPEND_LOCK:
        last = (
            db.query(LogEntry)
            .order_by(LogEntry.sequence_number.desc())
            .first()
        )
        if last is None:
            seq = 1
            prev_hash = GENESIS_PREV_HASH
        else:
            seq = last.sequence_number + 1
            prev_hash = last.entry_hash

        entry_hash = compute_entry_hash(prev_hash, payload)
        entry = LogEntry(
            sequence_number=seq,
            entry_type=entry_type,
            payload=payload,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)

    logger.debug("appended log entry seq=%d type=%s hash=%s", seq, entry_type, entry_hash[:12])
    return entry


def observation_payload_fields(rail_slug: str, witness_slug: str, probe_result) -> dict:
    """Canonical key fields for an observation LogEntry. Includes the
    witness's signature + observation hash as the receipt that this row
    traces back to a verified, signed observation — not just an arbitrary
    aggregator-authored record."""
    ts = probe_result.timestamp
    return {
        "record": "observation",
        "rail": rail_slug,
        "witness_id": witness_slug,
        "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
        "reachable": probe_result.reachable,
        "http_status": probe_result.http_status,
        "latency_ms": probe_result.latency_ms,
        "error": probe_result.error,
        "observation_hash": probe_result.observation_hash_hex,
        "signature": probe_result.signature_hex,
    }


def incident_event_payload_fields(rail_slug: str, incident_id: int, event) -> dict:
    """Canonical key fields for an incident-event LogEntry."""
    ts = event.timestamp
    return {
        "record": "incident_event",
        "rail": rail_slug,
        "incident_id": incident_id,
        "incident_event_id": event.id,
        "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
        "label": event.label,
        "narrative": event.narrative,
    }
