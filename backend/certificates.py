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

_rate_lock = threading.Lock()
_requests_by_ip: dict[str, deque] = defaultdict(deque)


def check_rate_limit(client_ip: str) -> bool:
    """True if this request is allowed; False if the caller is over budget.
    Rejected calls are logged by the endpoint (with the IP) so abuse is
    visible in the aggregator logs, not silently dropped."""
    now = datetime.utcnow().timestamp()
    cutoff = now - CERT_RATE_LIMIT_WINDOW_SECONDS
    with _rate_lock:
        window = _requests_by_ip[client_ip]
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= CERT_RATE_LIMIT_MAX:
            return False
        window.append(now)
        return True


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


def _incident_log_entries(db: Session, incident_id: int) -> list[LogEntry]:
    """The hash-chain entries that record this incident's timeline. Matched
    by parsing each incident_event payload (they're small and few) rather
    than substring-matching JSON, which would be brittle."""
    rows = (
        db.query(LogEntry)
        .filter(LogEntry.entry_type == "incident_event")
        .order_by(LogEntry.sequence_number.asc())
        .all()
    )
    return [r for r in rows if json.loads(r.payload).get("incident_id") == incident_id]


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
            _log_evidence_item(db, e) for e in _incident_log_entries(db, incident.id)
        ],
        "disclaimer": DISCLAIMER,
    }

    signature = _sign_certificate(cert)

    db.add(EvidenceCertificate(
        certificate_id=cert["certificate_id"],
        rail_id=rail.id,
        incident_id=incident.id,
        claimed_timestamp=claimed_ts,
        claimed_transaction_ref=claimed_transaction_ref,
        issued_at=now,
        requester_ip=requester_ip,
        payload_json=canonical_json_bytes(cert).decode("utf-8"),
        signature=signature,
    ))
    db.commit()

    logger.info(
        "issued certificate %s for rail=%s incident=%d to %s",
        cert["certificate_id"], rail.slug, incident.id, requester_ip or "unknown",
    )
    return {
        "certificate": cert,
        "signature": signature,
        "aggregator_public_key_hex": public_key_hex(),
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
            "detail": "Ed25519 signature verifies against the aggregator's identity key — this exact document, byte for byte, is what the aggregator signed.",
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
            "detail": "Certificate cites no log entries — nothing to check.",
        }
    elif not proven:
        checks["inclusion_proofs"] = {
            "passed": None,
            "detail": (
                f"All {len(evidence)} cited log entries were still awaiting a "
                "checkpoint when this certificate was issued — no proofs to check yet."
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
            "detail": "No checkpoint cited (no proven log entries) — nothing to anchor-check.",
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
                    f"{label}: cited root does not match the git-anchored copy — "
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
