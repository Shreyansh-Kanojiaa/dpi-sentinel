"""
DPI Sentinel aggregator — Evidence Certificates (Milestone 4).

A certificate is a signed, self-contained document a citizen can hand to
their bank or the RBI ombudsman: "a quorum of independent witnesses
confirmed an infrastructure incident on this rail during this window, and
here is the cryptographic trail." It is only ever issued for a time window
where Milestone 2's quorum logic actually declared an incident — there is
deliberately NO manual-override or admin path to mint one for a quiet
window, even for testing. A demo incident goes through the real pipeline.

What a certificate does and does not claim (this language ships in the
document itself, not just here): it confirms an INFRASTRUCTURE incident
occurred in the window. It does NOT and CANNOT confirm the outcome of the
citizen's specific transaction — the claimed transaction reference is
stored and displayed as self-reported and unverified. That distinction is
what keeps the certificate from being usable to manufacture false refund
claims for transactions that simply never happened.

Chain of trust embedded in each certificate:
  witness Ed25519 signatures -> verified observations -> quorum decision
  (the stored quorum_snapshot receipt) -> incident_event LogEntry rows in
  the hash chain -> Merkle inclusion proofs -> a checkpoint root signed by
  the aggregator and anchored in git. The certificate itself is then signed
  with the SAME aggregator identity that signs checkpoints (identity.py) —
  one key for all of the aggregator's own claims, never a third identity.
"""

import hashlib
import json
import logging
import os
import threading
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey
from sqlalchemy.orm import Session

import checkpoints
import merkle
from identity import get_signing_key, public_key_hex
from log_chain import compute_entry_hash
from models import EvidenceCertificate, Incident, LogEntry, Rail
from signing import canonical_json_bytes
from verify_log import load_git_checkpoint

logger = logging.getLogger("aggregator.certificates")

DISCLAIMER = (
    "This certificate confirms that a quorum of independent witnesses "
    "observed an infrastructure incident on the named rail during the "
    "stated window. It does NOT and CANNOT confirm the outcome of any "
    "individual transaction. The claimed transaction reference, if present, "
    "is self-reported by the requester and has not been verified by DPI "
    "Sentinel, which has no visibility into bank- or PSP-side settlement."
)

# How far in the future a claimed_timestamp may sit before we reject it
# outright (clock skew allowance; anything beyond this can't be a real,
# already-experienced failure).
CLAIMED_TIMESTAMP_FUTURE_TOLERANCE_SECONDS = 300

# --- Rate limiting -----------------------------------------------------------
#
# Simple in-memory sliding window per client IP, chosen over slowapi on
# purpose: the aggregator is a single process (one uvicorn worker), so a
# dict + lock is fully correct here, adds no dependency, and keeps the
# logic readable. The tradeoffs accepted: limits reset on process restart,
# and this wouldn't be shared across multiple workers/replicas — the point
# at which slowapi + a redis backend earns its keep. Neither matters at
# this milestone's scale.
#
# Why rate limiting is a security measure and not just load protection:
# certificates are signed documents minted anonymously. Unlimited minting
# would let one party stockpile thousands of certificates for the same
# incident (spam material for mass fake dispute filings) and lets an
# attacker use the endpoint as a free signing oracle over chosen-ish input
# (the claimed_transaction_ref free-text field ends up inside a document
# signed by the aggregator's key).

CERT_RATE_LIMIT_MAX = int(os.environ.get("CERT_RATE_LIMIT_MAX", "5"))
CERT_RATE_LIMIT_WINDOW_SECONDS = float(os.environ.get("CERT_RATE_LIMIT_WINDOW_SECONDS", "600"))

# Looking a certificate up again is not minting one, so it gets its own,
# much larger budget and its own store. Sharing the issuance deque would
# mean a citizen who re-opens their own printable certificate a few times
# silently loses the ability to request a new one — the two actions have
# nothing to do with each other and must not compete for the same budget.
CERT_LOOKUP_RATE_LIMIT_MAX = int(os.environ.get("CERT_LOOKUP_RATE_LIMIT_MAX", "60"))
CERT_LOOKUP_RATE_LIMIT_WINDOW_SECONDS = float(
    os.environ.get("CERT_LOOKUP_RATE_LIMIT_WINDOW_SECONDS", "600")
)

_rate_lock = threading.Lock()
_requests_by_ip: dict[str, deque] = defaultdict(deque)
_lookups_by_ip: dict[str, deque] = defaultdict(deque)


def _allow(store: dict[str, deque], client_ip: str, max_calls: int, window_seconds: float) -> bool:
    now = datetime.utcnow().timestamp()
    cutoff = now - window_seconds
    with _rate_lock:
        window = store[client_ip]
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= max_calls:
            return False
        window.append(now)
        return True


def check_rate_limit(client_ip: str) -> bool:
    """True if this ISSUANCE request is allowed; False if over budget.
    Rejected calls are logged by the endpoint (with the IP) so abuse is
    visible in the aggregator logs, not silently dropped."""
    return _allow(_requests_by_ip, client_ip, CERT_RATE_LIMIT_MAX, CERT_RATE_LIMIT_WINDOW_SECONDS)


def check_lookup_rate_limit(client_ip: str) -> bool:
    """True if this LOOKUP request is allowed. Separate budget from issuance
    (see the constants above)."""
    return _allow(
        _lookups_by_ip, client_ip, CERT_LOOKUP_RATE_LIMIT_MAX, CERT_LOOKUP_RATE_LIMIT_WINDOW_SECONDS
    )


# --- Issuance ----------------------------------------------------------------


def find_covering_incident(db: Session, rail: Rail, claimed_ts: datetime) -> Incident | None:
    """
    The ONLY path to a certificate: an Incident that Milestone 2's quorum
    logic actually opened (severity == "degraded"), whose window contains
    the claimed timestamp. Historical seeded incidents and legacy demo
    simulations are excluded — they were never quorum-confirmed, so the
    chain of trust the certificate advertises wouldn't exist for them.
    An open (unresolved) incident covers everything from started_at onward.
    """
    return (
        db.query(Incident)
        .filter(
            Incident.rail_id == rail.id,
            Incident.is_historical.is_(False),
            Incident.is_live_simulation.is_(False),
            Incident.severity == "degraded",
            Incident.started_at <= claimed_ts,
            (Incident.resolved_at.is_(None)) | (Incident.resolved_at >= claimed_ts),
        )
        .order_by(Incident.started_at.desc())
        .first()
    )


def _incident_log_entries(db: Session, incident: Incident) -> list[LogEntry]:
    """The hash-chain entries that record this incident's timeline. Matched
    by parsing each incident_event payload (they're small and few) rather
    than substring-matching JSON, which would be brittle.

    incident_id ALONE is not a safe key here, and this bit me in practice.
    The log is permanent and keyed by the incident_id that was current when
    the entry was appended, but Incident rows are not permanent —
    reset_demo_state.py deletes live incidents, which frees their primary
    keys, and SQLite then hands the same id to the next incident. So a fresh
    incident can inherit an older, unrelated incident's chain entries.

    Observed 28 July 2026: a certificate for an incident that opened at
    04:16:45 cited entries from 03:52, including a "Resolved" event dated
    before its own incident began. Every hash and proof checked out — the
    entries are genuine, they just belong to a different outage. On a printed
    evidence document that is incoherent in exactly the way this project
    exists to avoid.

    Bounding by the incident's own start closes it: an incident's first
    entry IS its "Detected" event at started_at, so no legitimate entry can
    predate it and nothing real is dropped. Entries without a parseable
    timestamp are excluded rather than included — for an evidentiary
    document, omitting an unplaceable record is safer than attaching one
    that may belong to someone else's incident.
    """
    rows = (
        db.query(LogEntry)
        .filter(LogEntry.entry_type == "incident_event")
        .order_by(LogEntry.sequence_number.asc())
        .all()
    )

    matched = []
    for r in rows:
        payload = json.loads(r.payload)
        if payload.get("incident_id") != incident.id:
            continue
        raw_ts = payload.get("timestamp")
        if not raw_ts:
            continue
        try:
            entry_ts = datetime.fromisoformat(raw_ts)
        except ValueError:
            continue
        if entry_ts >= incident.started_at:
            matched.append(r)
    return matched


def _log_evidence_item(db: Session, entry: LogEntry) -> dict:
    """
    One entry of the certificate's log_evidence list. Self-contained on
    purpose: it carries prev_hash + payload so an auditor (or /api/verify)
    can recompute entry_hash from CONTENT rather than trusting the stored
    hash — the same rebuild-from-content rule verify_log.py follows.
    A proof is only available once a checkpoint covers the entry; until
    then the item is marked "awaiting_checkpoint" rather than omitted, so
    the certificate is honest about what is and isn't proven yet.
    """
    item = {
        "sequence_number": entry.sequence_number,
        "entry_type": entry.entry_type,
        "payload": entry.payload,
        "prev_hash": entry.prev_hash,
        "entry_hash": entry.entry_hash,
    }
    proof = checkpoints.get_inclusion_proof(db, entry.id)
    if proof is None:
        item["status"] = "awaiting_checkpoint"
        item["proof"] = None
        item["checkpoint"] = None
    else:
        item["status"] = "proven"
        item["proof"] = proof["proof"]
        item["checkpoint"] = proof["checkpoint"]
    return item


def _sign_certificate(cert: dict) -> str:
    """Same signing discipline as the witnesses: canonical-JSON the whole
    document, SHA-256 it, Ed25519-sign the digest with the aggregator's one
    identity (the checkpoint-signing key — no third key)."""
    digest = hashlib.sha256(canonical_json_bytes(cert)).digest()
    return get_signing_key().sign(digest).signature.hex()


def issue_certificate(
    db: Session,
    rail: Rail,
    incident: Incident,
    claimed_ts: datetime,
    claimed_transaction_ref: str | None,
    requester_ip: str | None,
) -> dict:
    """Assemble, sign, and persist one Evidence Certificate. Returns the
    full response bundle: { certificate, signature, aggregator_public_key_hex }."""
    now = datetime.utcnow()
    cert = {
        "schema": "dpi-sentinel/evidence-certificate/v1",
        "certificate_id": uuid.uuid4().hex,
        "rail": {"slug": rail.slug, "name": rail.name, "operator": rail.operator},
        "incident_id": incident.id,
        "incident_window": {
            "started_at": incident.started_at.isoformat(),
            "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
            "ongoing": incident.resolved_at is None,
        },
        "severity": incident.severity,
        # The Milestone 2 receipt: which witnesses reported, which agreed
        # it was unhealthy, and the fractions that crossed the thresholds.
        "witness_quorum_snapshot": incident.quorum_snapshot,
        "claimed_timestamp": claimed_ts.isoformat(),
        "claimed_transaction_ref": {
            "value": claimed_transaction_ref,
            "verified": False,
            "note": (
                "Self-reported by the requester. DPI Sentinel has no "
                "visibility into individual transactions and has not "
                "verified this reference."
            ),
        },
        "issued_at": now.isoformat(),
        "log_evidence": [
            _log_evidence_item(db, e) for e in _incident_log_entries(db, incident)
        ],
        "disclaimer": DISCLAIMER,
    }

    signature = _sign_certificate(cert)

    payload_json = canonical_json_bytes(cert).decode("utf-8")
    db.add(EvidenceCertificate(
        certificate_id=cert["certificate_id"],
        rail_id=rail.id,
        incident_id=incident.id,
        claimed_timestamp=claimed_ts,
        claimed_transaction_ref=claimed_transaction_ref,
        issued_at=now,
        requester_ip=requester_ip,
        payload_json=payload_json,
        signature=signature,
    ))
    db.commit()

    logger.info(
        "issued certificate %s for rail=%s incident=%d to %s",
        cert["certificate_id"], rail.slug, incident.id, requester_ip or "unknown",
    )
    # `fingerprint` is a SIBLING of `certificate`, never a key inside it —
    # inside, it would be a hash of a document containing itself, and every
    # signature would break. Returned here as well as from the lookup so the
    # printable view can build its QR straight off the issue response, and so
    # both paths derive the string from the same code.
    return {
        "certificate": cert,
        "signature": signature,
        "aggregator_public_key_hex": public_key_hex(),
        "fingerprint": document_fingerprint(payload_json),
    }


# --- Lookup ------------------------------------------------------------------


def document_fingerprint(payload_json: str) -> str:
    """SHA-256 over the EXACT signed bytes, as stored.

    This is not a second, parallel hash scheme sitting alongside the
    signature: `payload_json` IS `canonical_json_bytes(cert)`, and
    `_sign_certificate` signs `sha256(canonical_json_bytes(cert)).digest()`.
    So this hex string is precisely the digest the Ed25519 signature covers.
    Printing a prefix of it on paper therefore pins the document against the
    same number the signature is over, and leaks nothing.

    Computed here and never in the browser: reproducing canonical_json_bytes
    in JavaScript would be a second serializer that has to stay byte-identical
    with this one forever, which is precisely what the project forbids. The
    client only ever compares this string to the one printed on the page.
    """
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def load_certificate(db: Session, certificate_id: str) -> dict | None:
    """Rebuild an issued bundle from its id, byte for byte.

    Nothing is re-derived: `payload_json` holds the exact canonical JSON that
    was signed, so this returns what was issued rather than something
    equivalent-looking. That matters because `issued_at`, `certificate_id`
    and the Merkle proofs in `log_evidence` are all point-in-time values that
    would differ if the document were rebuilt today.

    json.loads() here is safe despite the signature covering exact bytes:
    verify_certificate re-canonicalises with canonical_json_bytes before
    hashing, so key order and whitespace cannot survive to affect the digest.

    PRIVACY, decided deliberately: certificate_id is a 122-bit random
    capability token, and the document it returns can contain a transaction
    reference the citizen typed in themselves. Anyone holding the id (or a
    printout carrying it) can fetch the document. That is intended for a
    document whose whole purpose is to be handed to a bank or an ombudsman,
    but it is a real trade-off, not an oversight. There is deliberately no
    listing endpoint, so ids cannot be enumerated.
    """
    row = (
        db.query(EvidenceCertificate)
        .filter(EvidenceCertificate.certificate_id == certificate_id)
        .first()
    )
    if row is None:
        return None
    return {
        "certificate": json.loads(row.payload_json),
        "signature": row.signature,
        "aggregator_public_key_hex": public_key_hex(),
        "fingerprint": document_fingerprint(row.payload_json),
    }


# --- Verification ------------------------------------------------------------


def verify_certificate(certificate: dict, signature_hex: str) -> dict:
    """
    Re-derive trust from the math alone. Three INDEPENDENT checks, reported
    separately because they fail for different reasons and the difference
    matters to whoever is holding the document:

      signature        — was this exact document signed by the aggregator?
                         Verified against the aggregator's OWN key, never a
                         key the submitted document carries (an attacker can
                         swap in a keypair they control and "re-sign").
      inclusion_proofs — does each cited log entry, rebuilt from its own
                         content, actually hash up through its Merkle proof
                         to the cited checkpoint root?
      checkpoint_anchor— does that checkpoint root match the copy committed
                         to the external git repo (not just the live DB,
                         which the operator could rewrite along with
                         everything else)? Reuses verify_log.py's loader.

    A check that cannot be evaluated (no proofs yet, git file not present)
    is reported as passed=None with a reason — "unknown" is not collapsed
    into either pass or fail.
    """
    checks: dict[str, dict] = {}

    # (1) Signature over the exact submitted document.
    digest = hashlib.sha256(canonical_json_bytes(certificate)).digest()
    try:
        VerifyKey(bytes.fromhex(public_key_hex())).verify(digest, bytes.fromhex(signature_hex))
        checks["signature"] = {
            "passed": True,
            "detail": "Ed25519 signature verifies against the aggregator's identity key. This exact document, byte for byte, is what the aggregator signed.",
        }
    except (BadSignatureError, ValueError):
        checks["signature"] = {
            "passed": False,
            "detail": (
                "Signature does NOT verify against the aggregator's identity key. "
                "Either the document was modified after issuance, or it was never "
                "signed by this aggregator."
            ),
        }

    # (2) Merkle inclusion proofs, leaves rebuilt from content.
    evidence = certificate.get("log_evidence") or []
    proven = [e for e in evidence if e.get("status") == "proven" and e.get("proof") is not None]
    failures: list[str] = []
    for item in proven:
        leaf = compute_entry_hash(item["prev_hash"], item["payload"])
        if leaf != item["entry_hash"]:
            failures.append(
                f"seq {item['sequence_number']}: entry content does not hash to the "
                f"claimed entry_hash (payload or prev_hash was altered)"
            )
            continue
        root = (item.get("checkpoint") or {}).get("merkle_root")
        if not root or not merkle.verify_proof(leaf, item["proof"], root):
            failures.append(
                f"seq {item['sequence_number']}: inclusion proof does not recompute "
                f"to the cited checkpoint root"
            )
    if not evidence:
        checks["inclusion_proofs"] = {
            "passed": None,
            "detail": "Certificate cites no log entries, so there is nothing to check.",
        }
    elif not proven:
        checks["inclusion_proofs"] = {
            "passed": None,
            "detail": (
                f"All {len(evidence)} cited log entries were still awaiting a "
                "checkpoint when this certificate was issued, so there are no proofs to check yet."
            ),
        }
    elif failures:
        checks["inclusion_proofs"] = {"passed": False, "detail": "; ".join(failures)}
    else:
        checks["inclusion_proofs"] = {
            "passed": True,
            "detail": (
                f"{len(proven)} of {len(evidence)} cited log entries rebuilt from "
                "content and proven up their Merkle paths to the cited checkpoint roots."
            ),
        }

    # (3) Checkpoint roots against the git-anchored copies.
    cited_ckpts: dict[tuple[int, int], dict] = {}
    for item in proven:
        c = item.get("checkpoint") or {}
        if "seq_start" in c and "seq_end" in c:
            cited_ckpts[(c["seq_start"], c["seq_end"])] = c
    if not cited_ckpts:
        checks["checkpoint_anchor"] = {
            "passed": None,
            "detail": "No checkpoint cited (no proven log entries), so there is nothing to anchor-check.",
        }
    else:
        anchor_failures: list[str] = []
        anchor_unknown: list[str] = []
        anchor_ok = 0
        for (s, e), c in sorted(cited_ckpts.items()):
            label = f"checkpoint seq {s}-{e}"
            # The checkpoint's own signature: the aggregator attested to this root.
            try:
                VerifyKey(bytes.fromhex(public_key_hex())).verify(
                    bytes.fromhex(c["merkle_root"]), bytes.fromhex(c["aggregator_signature"])
                )
            except (BadSignatureError, ValueError, KeyError):
                anchor_failures.append(f"{label}: aggregator signature over the cited root does not verify")
                continue
            git_ckpt = load_git_checkpoint(s, e)
            if git_ckpt is None:
                anchor_unknown.append(f"{label}: no git-anchored copy found to cross-check")
            elif git_ckpt.get("merkle_root") != c["merkle_root"]:
                anchor_failures.append(
                    f"{label}: cited root does not match the git-anchored copy. "
                    f"the certificate's checkpoint disagrees with the externally published one"
                )
            else:
                anchor_ok += 1
        if anchor_failures:
            checks["checkpoint_anchor"] = {"passed": False, "detail": "; ".join(anchor_failures + anchor_unknown)}
        elif anchor_ok == 0:
            checks["checkpoint_anchor"] = {"passed": None, "detail": "; ".join(anchor_unknown)}
        else:
            detail = f"{anchor_ok} cited checkpoint root(s) match the git-anchored copies and carry valid aggregator signatures."
            if anchor_unknown:
                detail += " " + "; ".join(anchor_unknown)
            checks["checkpoint_anchor"] = {"passed": True, "detail": detail}

    failed = [name for name, c in checks.items() if c["passed"] is False]
    return {
        "valid": not failed,
        "failed_checks": failed,
        "checks": checks,
        "aggregator_public_key_hex": public_key_hex(),
    }


def parse_claimed_timestamp(raw: str) -> datetime | None:
    """ISO-8601 in, naive-UTC out (matching how the DB stores datetimes).
    None if unparseable or implausibly far in the future."""
    try:
        ts = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    if ts > datetime.utcnow() + timedelta(seconds=CLAIMED_TIMESTAMP_FUTURE_TOLERANCE_SECONDS):
        return None
    return ts
