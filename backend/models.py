"""
DPI Sentinel — data models.

Core entities:
  Rail        — a monitored piece of digital public infrastructure (UPI, DigiLocker, ...)
  Witness     — an independent witness the aggregator has registered (out-of-band, from
                WITNESS_URLS) and trusts a public key for; see registry.py
  ProbeResult — a single signed probe observation reported by a witness for a rail
  Incident    — a detected or historical disruption, with a timeline of events
  IncidentEvent — a single timestamped entry in an incident's timeline (detection, update, resolution)
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Text, JSON,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Rail(Base):
    __tablename__ = "rails"

    id = Column(Integer, primary_key=True)
    slug = Column(String, unique=True, nullable=False)        # "upi", "digilocker"
    name = Column(String, nullable=False)                      # "UPI"
    full_name = Column(String, nullable=False)                 # "Unified Payments Interface"
    operator = Column(String, nullable=False)                  # "NPCI"
    description = Column(Text, nullable=False)
    monitor_mode = Column(String, nullable=False)               # "live" | "synthetic"
    probe_target = Column(String, nullable=False)               # what we actually hit
    probe_methodology = Column(Text, nullable=False)            # human-readable, shown in UI
    color = Column(String, nullable=False)                      # brand accent for UI

    probes = relationship("ProbeResult", back_populates="rail", cascade="all, delete-orphan")
    incidents = relationship("Incident", back_populates="rail", cascade="all, delete-orphan")


class Witness(Base):
    __tablename__ = "witnesses"

    id = Column(Integer, primary_key=True)
    slug = Column(String, unique=True, nullable=False)          # "witness-a" — matches witness_id in observations
    base_url = Column(String, nullable=False)                   # where we fetched /pubkey from (WITNESS_URLS entry)
    public_key_hex = Column(String, nullable=False)              # the ONLY public key we trust for this witness_id
    registered_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = Column(DateTime, nullable=True)               # updated on each accepted observation

    probes = relationship("ProbeResult", back_populates="witness")


class WitnessRailAssignment(Base):
    """
    Milestone 5 — explicit witness-to-rail assignment.

    The source of truth for "which witnesses are supposed to cover this
    rail," independent of whether any particular witness is currently
    healthy or reporting. This is what quorum.py now divides by for a
    rail's participation denominator, replacing the old global
    "every registered witness" count — see quorum.py's module docstring.

    Rows here are (re)synced by registry.py at every aggregator startup
    from each witness's declared targets (fetched alongside its pubkey),
    matched against Rail.probe_target the same way POST /observations
    routes incoming observations. This is registration-time, declarative
    assignment — never inferred from which witnesses happened to report
    recently, so a witness that's merely down still counts as "assigned."
    """
    __tablename__ = "witness_rail_assignments"

    id = Column(Integer, primary_key=True)
    witness_id = Column(Integer, ForeignKey("witnesses.id"), nullable=False)
    rail_id = Column(Integer, ForeignKey("rails.id"), nullable=False)
    assigned_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("witness_id", "rail_id", name="uq_witness_rail_assignment"),)


class ProbeResult(Base):
    __tablename__ = "probe_results"

    id = Column(Integer, primary_key=True)
    rail_id = Column(Integer, ForeignKey("rails.id"), nullable=False)
    witness_id = Column(Integer, ForeignKey("witnesses.id"), nullable=True)  # null only for pre-Milestone-2 rows
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    # Availability layer (real, reported by a witness's own HTTP/TLS probe)
    reachable = Column(Boolean, nullable=False)
    http_status = Column(Integer, nullable=True)
    latency_ms = Column(Float, nullable=True)
    error = Column(String, nullable=True)

    # The signature this observation arrived with, kept as the receipt that it was
    # actually verified against the witness's registered key at ingest time.
    signature_hex = Column(String, nullable=True)
    observation_hash_hex = Column(String, nullable=True)

    # Simulated transaction-success layer (legacy Tier-0 self-probe field, unused by
    # anything that writes rows via POST /observations — kept for old rows' sake)
    simulated_success_rate = Column(Float, nullable=True)       # 0.0 - 1.0
    is_synthetic_injection = Column(Boolean, default=False)     # true during demo-triggered outage

    rail = relationship("Rail", back_populates="probes")
    witness = relationship("Witness", back_populates="probes")


class Incident(Base):
    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True)
    rail_id = Column(Integer, ForeignKey("rails.id"), nullable=False)
    title = Column(String, nullable=False)
    severity = Column(String, nullable=False)                   # "minor" | "major" | "critical"
    status = Column(String, nullable=False, default="investigating")  # investigating|identified|monitoring|resolved
    started_at = Column(DateTime, nullable=False)
    resolved_at = Column(DateTime, nullable=True)
    is_historical = Column(Boolean, default=False)              # real documented past incident
    is_live_simulation = Column(Boolean, default=False)         # triggered live in a demo
    source_note = Column(Text, nullable=True)                   # citation for historical incidents
    min_success_rate = Column(Float, nullable=True)              # worst point during incident (legacy Tier-0 field)
    quorum_snapshot = Column(JSON, nullable=True)                # the "receipt": witnesses reporting/agreeing + fractions
                                                                  # at the moment quorum.py made this decision

    rail = relationship("Rail", back_populates="incidents")
    events = relationship("IncidentEvent", back_populates="incident", cascade="all, delete-orphan")


class IncidentEvent(Base):
    __tablename__ = "incident_events"

    id = Column(Integer, primary_key=True)
    incident_id = Column(Integer, ForeignKey("incidents.id"), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    label = Column(String, nullable=False)                      # "Detected", "Identified", "Resolved"...
    narrative = Column(Text, nullable=False)

    incident = relationship("Incident", back_populates="events")


class LogEntry(Base):
    """
    Milestone 3 — the tamper-evident append-only log.

    Every verified observation and every incident-timeline event is also
    appended here as one hash-chained entry. Unlike ProbeResult /
    IncidentEvent (which are just editable rows), each LogEntry commits to
    the one before it: entry_hash = sha256(prev_hash + payload). Edit any
    old row's payload and every entry_hash from that point on stops
    matching — that's what verify_log.py detects.

    A single unified log (not one per record type) is deliberate: the
    Merkle checkpoints and inclusion proofs need one linear, gapless
    sequence to build a tree over. Interleaving observations and incident
    events in one chain also means the *order* in which the aggregator saw
    things is itself committed to and can't be silently reshuffled later.
    """
    __tablename__ = "log_entries"

    id = Column(Integer, primary_key=True)
    # Monotonic + gapless. Enforced by the serialized append in log_chain.py
    # (a global lock around "read max seq -> insert"), not by autoincrement,
    # so the chain order is well-defined even under concurrent /observations.
    sequence_number = Column(Integer, unique=True, nullable=False, index=True)
    entry_type = Column(String, nullable=False)                 # "observation" | "incident_event"
    payload = Column(Text, nullable=False)                      # canonical JSON string of the record's key fields
    prev_hash = Column(String, nullable=False)                  # entry_hash of seq-1, or GENESIS for the first
    entry_hash = Column(String, nullable=False)                 # sha256(prev_hash + payload)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class EvidenceCertificate(Base):
    """
    Milestone 4 — one issued Evidence Certificate.

    The certificate the citizen holds is the signed JSON bundle returned by
    POST /api/certificates; this row is the aggregator's own record of
    having issued it (payload_json is the exact canonical string that was
    signed). claimed_transaction_ref is stored verbatim as SELF-REPORTED,
    UNVERIFIED text — the certificate document itself says so, because the
    aggregator has no visibility into individual transaction outcomes and
    must not appear to confirm them.
    """
    __tablename__ = "evidence_certificates"

    id = Column(Integer, primary_key=True)
    certificate_id = Column(String, unique=True, nullable=False, index=True)
    rail_id = Column(Integer, ForeignKey("rails.id"), nullable=False)
    incident_id = Column(Integer, ForeignKey("incidents.id"), nullable=False)
    claimed_timestamp = Column(DateTime, nullable=False)
    claimed_transaction_ref = Column(Text, nullable=True)      # self-reported, never verified
    issued_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    requester_ip = Column(String, nullable=True)
    payload_json = Column(Text, nullable=False)                 # the exact canonical JSON that was signed
    signature = Column(String, nullable=False)                  # aggregator Ed25519 sig (hex)


class Checkpoint(Base):
    """
    Milestone 3 — a periodic Merkle checkpoint over a contiguous batch of
    LogEntry rows.

    merkle_root is the root of a SHA-256 Merkle tree built over the
    entry_hashes in [seq_start, seq_end]. aggregator_signature is the
    aggregator's Ed25519 signature over that root — the aggregator's own
    identity (backend/identity.py), NOT any witness key, because the claim
    being signed ("the log looked exactly like this, up to seq N, at this
    time") is the aggregator's to make, not a witness's.

    The same record is also written to a git repo (see checkpoints.py) so a
    copy of merkle_root lives somewhere the operator can't silently rewrite.
    """
    __tablename__ = "checkpoints"

    id = Column(Integer, primary_key=True)
    seq_start = Column(Integer, nullable=False)                 # inclusive
    seq_end = Column(Integer, nullable=False)                   # inclusive
    entry_count = Column(Integer, nullable=False)
    merkle_root = Column(String, nullable=False)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    aggregator_public_key_hex = Column(String, nullable=False)
    aggregator_signature = Column(String, nullable=False)       # Ed25519 sig over bytes.fromhex(merkle_root)
    git_committed = Column(Boolean, default=False)              # did the git anchor write+commit succeed
    git_commit_sha = Column(String, nullable=True)
