"""
DPI Sentinel — aggregator application (Milestone 2).

Run with:
    uvicorn main:app --reload --port 8420

This process no longer probes anything itself (that was the Tier 0
prototype). It only:
  - builds a witness registry at startup from WITNESS_URLS (registry.py)
  - accepts signed observations from those witnesses at POST /observations,
    verifying each against the REGISTERED public key (never a key the
    payload itself supplies — see registry.py / signing.py)
  - runs quorum consensus over recent observations per rail (quorum.py)
    to decide operational / insufficient_data / degraded, replacing
    sla.py's old threshold-on-simulated-rate detection

probe_engine.py is intentionally left in place but unused except by the
now-inert demo trigger-outage/resolve-outage endpoints below — nothing
calls run_probe_cycle anymore. sla.py's detect_and_update_incidents is
likewise unused; rolling_uptime is still used for uptime/latency display,
which stays meaningful now that ProbeResult rows come from real witnesses.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from models import Base, Rail, Witness, Incident, ProbeResult, LogEntry, Checkpoint, WitnessRailAssignment
from rails_config import seed_rails
from historical_seed import seed_historical_incidents
from probe_engine import engine as probe_engine
from sla import rolling_uptime
from signing import verify_observation_signature
from registry import build_registry
import quorum
import log_chain
import checkpoints
import identity
import certificates
import unmatched_targets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("aggregator.main")

DB_URL = os.environ.get("DB_URL", "sqlite:///./dpi_sentinel.db")
db_engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)

WITNESS_URLS = [u.strip() for u in os.environ.get("WITNESS_URLS", "").split(",") if u.strip()]
QUORUM_TICK_SECONDS = float(os.environ.get("QUORUM_TICK_SECONDS", "5"))
OBSERVATION_FRESHNESS_SECONDS = float(os.environ.get("OBSERVATION_FRESHNESS_SECONDS", "30"))
FUTURE_TOLERANCE_SECONDS = float(os.environ.get("FUTURE_TOLERANCE_SECONDS", "5"))
CHECKPOINT_TICK_SECONDS = float(os.environ.get("CHECKPOINT_TICK_SECONDS", "60"))

scheduler = AsyncIOScheduler()


async def checkpoint_tick_job():
    """
    Milestone 3 — periodically seal the log into a signed Merkle checkpoint
    when the size (50 entries) or age (1 hour) trigger fires. The actual
    work touches SQLite and shells out to git, so it runs in a worker thread
    to avoid blocking the event loop.
    """
    def _run():
        db = SessionLocal()
        try:
            checkpoints.maybe_create_checkpoint(db)
        finally:
            db.close()

    await asyncio.to_thread(_run)


async def quorum_tick_job():
    """
    Periodic safety net alongside the event-driven check in POST
    /observations. Event-driven alone would never notice a witness that
    just stopped sending observations — nothing arrives to trigger a
    re-evaluation, so a rail's last-known status would sit stale forever
    even as participation quietly decays below quorum. This tick re-derives
    every rail's status from scratch on a fixed interval so that silence
    is itself detected, at the cost of up to QUORUM_TICK_SECONDS of lag
    versus a purely event-driven design.
    """
    db = SessionLocal()
    try:
        for rail in db.query(Rail).all():
            snapshot = quorum.compute_quorum_snapshot(db, rail)
            quorum.apply_quorum_incident_logic(db, rail, snapshot)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=db_engine)
    db = SessionLocal()
    try:
        seed_rails(db)
        seed_historical_incidents(db)
        # Tier 0's backfill_probe_history() is deliberately no longer called:
        # it manufactured synthetic ProbeResult rows with no witness_id,
        # which doesn't fit a model where every row must trace back to a
        # registered witness's signature. rails_config.backfill_probe_history
        # is left in place, just uncalled.
    finally:
        db.close()

    db = SessionLocal()
    try:
        await build_registry(db, WITNESS_URLS)
    finally:
        db.close()

    # Milestone 3: load (or generate) the aggregator's own signing identity
    # eagerly at startup so its public key is stable and available before the
    # first checkpoint is signed. Separate from any witness key — see
    # identity.py for why the aggregator signs with its own key.
    logger.info("aggregator identity loaded, pubkey=%s", identity.public_key_hex())

    scheduler.add_job(quorum_tick_job, "interval", seconds=QUORUM_TICK_SECONDS, id="quorum_tick", next_run_time=datetime.utcnow())
    scheduler.add_job(checkpoint_tick_job, "interval", seconds=CHECKPOINT_TICK_SECONDS, id="checkpoint_tick")
    scheduler.start()

    yield

    scheduler.shutdown(wait=False)
    await probe_engine.aclose()


app = FastAPI(title="DPI Sentinel", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def serialize_rail(db, rail: Rail) -> dict:
    uptime = rolling_uptime(db, rail.id, hours=24)
    snapshot = quorum.compute_quorum_snapshot(db, rail)
    open_incident = (
        db.query(Incident)
        .filter(Incident.rail_id == rail.id, Incident.status != "resolved", Incident.is_historical.is_(False))
        .order_by(Incident.started_at.desc())
        .first()
    )
    return {
        "slug": rail.slug,
        "name": rail.name,
        "full_name": rail.full_name,
        "operator": rail.operator,
        "description": rail.description,
        "monitor_mode": rail.monitor_mode,
        "probe_target": rail.probe_target,
        "probe_methodology": rail.probe_methodology,
        "color": rail.color,
        "status": snapshot["status"],
        "quorum": snapshot,
        # Milestone 5: makes "a witness is down" visually distinct from "no
        # witness was ever assigned" without digging into the quorum object —
        # e.g. "2/3 assigned witnesses reporting".
        "witness_coverage": f"{snapshot['reporting_count']}/{snapshot['assigned_count']} assigned witnesses reporting",
        "active_incident_id": open_incident.id if open_incident else None,
        "uptime_24h": uptime,
    }


def serialize_incident(incident: Incident) -> dict:
    return {
        "id": incident.id,
        "rail_id": incident.rail_id,
        "title": incident.title,
        "severity": incident.severity,
        "status": incident.status,
        "started_at": incident.started_at.isoformat(),
        "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
        "is_historical": incident.is_historical,
        "is_live_simulation": incident.is_live_simulation,
        "source_note": incident.source_note,
        "min_success_rate": incident.min_success_rate,
        "quorum_snapshot": incident.quorum_snapshot,
        "events": [
            {
                "timestamp": e.timestamp.isoformat(),
                "label": e.label,
                "narrative": e.narrative,
            }
            for e in sorted(incident.events, key=lambda e: e.timestamp)
        ],
    }


@app.get("/api/rails")
def get_rails():
    db = SessionLocal()
    try:
        rails = db.query(Rail).all()
        return [serialize_rail(db, r) for r in rails]
    finally:
        db.close()


@app.get("/api/rails/{slug}")
def get_rail(slug: str):
    db = SessionLocal()
    try:
        rail = db.query(Rail).filter_by(slug=slug).first()
        if not rail:
            raise HTTPException(404, "rail not found")
        return serialize_rail(db, rail)
    finally:
        db.close()


@app.get("/api/incidents")
def get_incidents(rail: str | None = None):
    db = SessionLocal()
    try:
        q = db.query(Incident)
        if rail:
            r = db.query(Rail).filter_by(slug=rail).first()
            if not r:
                raise HTTPException(404, "rail not found")
            q = q.filter(Incident.rail_id == r.id)
        incidents = q.order_by(Incident.started_at.desc()).all()
        return [serialize_incident(i) for i in incidents]
    finally:
        db.close()


@app.post("/api/demo/trigger-outage/{slug}")
def trigger_outage(slug: str, severity: float = 0.45):
    db = SessionLocal()
    try:
        rail = db.query(Rail).filter_by(slug=slug).first()
        if not rail:
            raise HTTPException(404, "rail not found")
        probe_engine.trigger_outage(slug, severity=severity)
        return {"ok": True, "slug": slug, "injected_severity": severity}
    finally:
        db.close()


@app.post("/api/demo/resolve-outage/{slug}")
def resolve_outage(slug: str):
    db = SessionLocal()
    try:
        rail = db.query(Rail).filter_by(slug=slug).first()
        if not rail:
            raise HTTPException(404, "rail not found")
        probe_engine.resolve_outage(slug)
        return {"ok": True, "slug": slug}
    finally:
        db.close()


class SignedObservationIn(BaseModel):
    witness_id: str
    timestamp: str
    target: str
    reachable: bool
    http_status: int | None = None
    latency_ms: float | None = None
    error: str | None = None
    hash: str
    signature: str


@app.post("/observations")
def post_observation(payload: SignedObservationIn):
    db = SessionLocal()
    try:
        witness = db.query(Witness).filter_by(slug=payload.witness_id).first()
        if not witness:
            logger.warning("rejected observation: unknown witness_id=%r (not in registry)", payload.witness_id)
            raise HTTPException(status_code=403, detail="unknown witness_id")

        # Recompute the hash from the raw fields ourselves — never trust the
        # payload's own "hash" claim. This is what catches tampering: editing
        # any field after signing changes this recomputed hash, so the
        # signature (made over the ORIGINAL hash) will no longer verify.
        observation = {
            "witness_id": payload.witness_id,
            "timestamp": payload.timestamp,
            "target": payload.target,
            "reachable": payload.reachable,
            "http_status": payload.http_status,
            "latency_ms": payload.latency_ms,
            "error": payload.error,
        }
        if not verify_observation_signature(observation, payload.signature, witness.public_key_hex):
            logger.warning("rejected observation from witness_id=%s: signature verification failed", payload.witness_id)
            raise HTTPException(status_code=400, detail="signature verification failed")

        try:
            ts = datetime.fromisoformat(payload.timestamp)
        except ValueError:
            logger.warning("rejected observation from witness_id=%s: unparseable timestamp %r", payload.witness_id, payload.timestamp)
            raise HTTPException(status_code=400, detail="invalid timestamp")
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        age_seconds = (datetime.now(timezone.utc) - ts).total_seconds()
        if age_seconds > OBSERVATION_FRESHNESS_SECONDS:
            logger.warning("rejected observation from witness_id=%s: timestamp %.1fs old (max %.0fs)", payload.witness_id, age_seconds, OBSERVATION_FRESHNESS_SECONDS)
            raise HTTPException(status_code=400, detail="observation too old")
        if age_seconds < -FUTURE_TOLERANCE_SECONDS:
            logger.warning("rejected observation from witness_id=%s: timestamp %.1fs in the future", payload.witness_id, -age_seconds)
            raise HTTPException(status_code=400, detail="observation timestamp is in the future")

        rail = db.query(Rail).filter_by(probe_target=payload.target).first()
        if not rail:
            # This was already a loud 400 + WARNING log, not a silent drop —
            # what was missing was durable, queryable visibility beyond the
            # process log. record_unmatched_target() doesn't change whether
            # or how this observation is rejected, only whether a mismatch
            # shows up somewhere you'd actually look (GET
            # /api/diagnostics/unmatched-targets) instead of requiring you
            # to already be tailing container logs to notice it.
            logger.warning("rejected observation from witness_id=%s: target %r matches no monitored rail", payload.witness_id, payload.target)
            unmatched_targets.record_unmatched_target(payload.witness_id, payload.target)
            raise HTTPException(status_code=400, detail=f"target {payload.target!r} does not match any monitored rail")

        pr = ProbeResult(
            rail_id=rail.id,
            witness_id=witness.id,
            timestamp=ts.astimezone(timezone.utc).replace(tzinfo=None),
            reachable=payload.reachable,
            http_status=payload.http_status,
            latency_ms=payload.latency_ms,
            error=payload.error,
            signature_hex=payload.signature,
            observation_hash_hex=payload.hash,
        )
        db.add(pr)
        witness.last_seen_at = datetime.utcnow()
        db.commit()

        # Milestone 3: append this verified observation to the tamper-evident
        # hash chain. Done after the ProbeResult is committed and before any
        # incident events it triggers, so the log's order mirrors causality.
        log_chain.append_log_entry(
            db, "observation",
            log_chain.observation_payload_fields(rail.slug, witness.slug, pr),
        )

        snapshot = quorum.compute_quorum_snapshot(db, rail)
        quorum.apply_quorum_incident_logic(db, rail, snapshot)

        return {"ok": True}
    finally:
        db.close()


@app.get("/api/methodology")
def get_methodology():
    return {
        "summary": (
            "DPI Sentinel separates two layers of signal on every rail. "
            "Availability and latency are measured live, via real synthetic "
            "HTTPS probes against each rail's public-facing surface, on a "
            "fixed interval. Transaction-level success-rate is a calibrated "
            "simulation layer — no outside party has bank/PSP-side settlement "
            "visibility — calibrated against publicly documented incidents, "
            "and always labeled as such. We believe stating this plainly is "
            "part of the product, not a caveat to hide."
        ),
        "thresholds": {
            "minor_below": 0.985,
            "major_below": 0.90,
            "critical_below": 0.70,
        },
    }


@app.get("/api/aggregator/pubkey")
def aggregator_pubkey():
    """The aggregator's Ed25519 public key — the counterpart to each
    witness's /pubkey. Anyone verifying a checkpoint signature needs this."""
    return {"public_key_hex": identity.public_key_hex()}


@app.get("/api/log")
def get_log(limit: int = 50):
    """Recent tamper-evident log entries, newest first — mostly so you can
    grab an entry_id to feed the inclusion-proof endpoint below."""
    db = SessionLocal()
    try:
        rows = (
            db.query(LogEntry)
            .order_by(LogEntry.sequence_number.desc())
            .limit(min(limit, 500))
            .all()
        )
        return [
            {
                "id": r.id,
                "sequence_number": r.sequence_number,
                "entry_type": r.entry_type,
                "payload": r.payload,
                "prev_hash": r.prev_hash,
                "entry_hash": r.entry_hash,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    finally:
        db.close()


@app.get("/api/log/{entry_id}/proof")
def get_log_proof(entry_id: int):
    """Merkle inclusion proof: the sibling hashes that recompute the
    checkpoint's published root from this one entry, without the whole log."""
    db = SessionLocal()
    try:
        result = checkpoints.get_inclusion_proof(db, entry_id)
        if result is None:
            raise HTTPException(
                404,
                "no proof available: entry not found, or not yet inside a "
                "published checkpoint (wait for the next checkpoint to cover it)",
            )
        return result
    finally:
        db.close()


@app.get("/api/checkpoints")
def get_checkpoints():
    """Published Merkle checkpoints, newest first."""
    db = SessionLocal()
    try:
        rows = db.query(Checkpoint).order_by(Checkpoint.seq_end.desc()).all()
        return [
            {
                "id": c.id,
                "seq_start": c.seq_start,
                "seq_end": c.seq_end,
                "entry_count": c.entry_count,
                "merkle_root": c.merkle_root,
                "timestamp": c.timestamp.isoformat(),
                "aggregator_public_key_hex": c.aggregator_public_key_hex,
                "aggregator_signature": c.aggregator_signature,
                "git_committed": c.git_committed,
                "git_commit_sha": c.git_commit_sha,
            }
            for c in rows
        ]
    finally:
        db.close()


@app.get("/api/diagnostics/unmatched-targets")
def get_unmatched_targets():
    """
    Observations POST /observations rejected because payload.target matched
    no Rail.probe_target — already a loud 400 + WARNING log at rejection
    time (see the routing-match block above), but that log line only
    surfaces if someone is already tailing container logs. This is the
    same information made durably visible: a running count since process
    start, plus the most recent rejections (witness_id, target, when).

    A non-empty response here means some witness's configured target
    string doesn't exactly match any Rail.probe_target — a trailing slash,
    http vs https, or a stray edit to PROBE_TARGETS vs rails_config.py are
    the likely causes (routing is exact-string-match by design; see
    CLAUDE.md). That witness's observations for the mismatched target are
    being silently lost from quorum's point of view — silently to quorum,
    not to this endpoint.
    """
    return unmatched_targets.snapshot()


@app.get("/api/diagnostics/witness-assignments")
def get_witness_assignments():
    """
    Milestone 5 — the WitnessRailAssignment table made visible: which
    witnesses are recorded as assigned to which rails, and when that
    assignment was (re)synced by registry.py at aggregator startup. This is
    declarative coverage, independent of whether a witness is currently up
    or reporting — cross-reference against a rail's "witness_coverage"
    string in GET /api/rails to see assigned-vs-currently-reporting.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(WitnessRailAssignment, Witness, Rail)
            .join(Witness, WitnessRailAssignment.witness_id == Witness.id)
            .join(Rail, WitnessRailAssignment.rail_id == Rail.id)
            .order_by(Rail.slug, Witness.slug)
            .all()
        )
        return [
            {
                "witness_slug": w.slug,
                "rail_slug": r.slug,
                "assigned_at": a.assigned_at.isoformat(),
            }
            for a, w, r in rows
        ]
    finally:
        db.close()


class CertificateRequest(BaseModel):
    rail_slug: str
    claimed_timestamp: str
    claimed_transaction_ref: str | None = None


@app.post("/api/certificates")
def post_certificate(body: CertificateRequest, request: Request):
    """
    Milestone 4 — issue an Evidence Certificate. Only succeeds if the
    claimed timestamp falls inside a window where quorum consensus actually
    declared an incident (see certificates.find_covering_incident). There is
    no override path: no incident, no certificate.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not certificates.check_rate_limit(client_ip):
        logger.warning("rate-limited certificate request from %s (max %d per %.0fs)",
                       client_ip, certificates.CERT_RATE_LIMIT_MAX,
                       certificates.CERT_RATE_LIMIT_WINDOW_SECONDS)
        raise HTTPException(
            status_code=429,
            detail=(
                f"Too many certificate requests from this address — limited to "
                f"{certificates.CERT_RATE_LIMIT_MAX} per "
                f"{int(certificates.CERT_RATE_LIMIT_WINDOW_SECONDS // 60)} minutes. "
                f"Please try again later."
            ),
        )

    claimed_ts = certificates.parse_claimed_timestamp(body.claimed_timestamp)
    if claimed_ts is None:
        raise HTTPException(400, "claimed_timestamp must be a valid ISO-8601 timestamp, not in the future")

    db = SessionLocal()
    try:
        rail = db.query(Rail).filter_by(slug=body.rail_slug).first()
        if not rail:
            raise HTTPException(404, "rail not found")

        incident = certificates.find_covering_incident(db, rail, claimed_ts)
        if incident is None:
            logger.info("certificate refused for rail=%s ts=%s from %s: no quorum-confirmed incident covers that time",
                        rail.slug, claimed_ts.isoformat(), client_ip)
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No quorum-confirmed incident on {rail.name} covers "
                    f"{claimed_ts.isoformat()}. Certificates are only issued for time "
                    f"windows where independent witness consensus actually declared a "
                    f"degradation — there is no way to generate one for a quiet window."
                ),
            )

        return certificates.issue_certificate(
            db, rail, incident, claimed_ts, body.claimed_transaction_ref, client_ip,
        )
    finally:
        db.close()


class VerifyRequest(BaseModel):
    certificate: dict
    signature: str
    # Present in the issued bundle; accepted here so a citizen can paste the
    # whole downloaded file as-is, but verification NEVER uses it — the
    # signature is always checked against the aggregator's own key.
    aggregator_public_key_hex: str | None = None


@app.post("/api/verify")
def post_verify(body: VerifyRequest):
    """
    Milestone 4 — verify a certificate bundle. POST rather than GET on
    purpose: a certificate is a multi-kilobyte JSON document, which doesn't
    fit in a query string (URL length limits) and would smear the full
    contents across access logs and browser history. The cost is that a
    verification isn't a linkable/cacheable URL — acceptable, since
    verifying is an action performed on a document you hold, not a resource.
    """
    return certificates.verify_certificate(body.certificate, body.signature)


@app.get("/health")
def health():
    return {"ok": True}
