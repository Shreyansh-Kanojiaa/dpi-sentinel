"""
DPI Sentinel aggregator — quorum consensus.

Replaces sla.py's threshold-on-simulated-rate incident detection (Tier 0)
with a model where the aggregator never probes anything itself: it only
believes what a quorum of independently registered witnesses agree on,
within a recent time window.

Two separate fractions, checked in order, on purpose:

  1. Participation: did enough registered witnesses report recently
     enough to say anything at all? If not: "insufficient_data" — we
     explicitly refuse to default to "operational" just because nobody
     complained. Silence from witnesses is not evidence of health.
  2. Agreement: of the witnesses that DID report, do enough of them
     agree the rail is unhealthy? This is only meaningful to ask once
     participation has already cleared the first bar.

Collapsing these into one check would produce wrong answers in both
directions — see the design notes in this milestone's writeup for the
concrete failure scenarios (a lone surviving witness reporting "healthy"
looking like consensus, or one dissenting witness among many looking
like an outage).

Every decision this module makes is derived fresh from ProbeResult /
Witness rows at call time — nothing here is cached, so GET /api/rails
and the periodic tick always see the same picture.
"""

import os
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from models import Rail, Witness, ProbeResult, Incident, IncidentEvent
import log_chain

MIN_PARTICIPATION_FRACTION = float(os.environ.get("MIN_PARTICIPATION_FRACTION", "0.66"))
AGREEMENT_SUPERMAJORITY_FRACTION = float(os.environ.get("AGREEMENT_SUPERMAJORITY_FRACTION", "0.6"))
WINDOW_SECONDS = int(os.environ.get("WINDOW_SECONDS", "60"))


def _is_unhealthy(row: ProbeResult) -> bool:
    return (not row.reachable) or row.http_status is None or row.http_status >= 500


def compute_quorum_snapshot(db: Session, rail: Rail, now: datetime | None = None) -> dict:
    """
    Pure read-only computation of a rail's current quorum status. Used both
    to answer GET /api/rails live (status is never persisted on Rail — see
    CLAUDE.md's "no persisted current status field" invariant) and as the
    input to apply_quorum_incident_logic below.
    """
    now = now or datetime.utcnow()
    since = now - timedelta(seconds=WINDOW_SECONDS)

    total_registered = db.query(Witness).count()

    rows = (
        db.query(ProbeResult)
        .filter(
            ProbeResult.rail_id == rail.id,
            ProbeResult.timestamp >= since,
            ProbeResult.witness_id.isnot(None),
        )
        .order_by(ProbeResult.timestamp.asc())
        .all()
    )

    # Most recent observation per witness within the window — that's each
    # witness's current "vote," not a blend of everything they've said recently.
    latest_by_witness_id: dict[int, ProbeResult] = {}
    for row in rows:
        latest_by_witness_id[row.witness_id] = row

    reporting_count = len(latest_by_witness_id)
    participation_fraction = (reporting_count / total_registered) if total_registered else 0.0

    # Resolved regardless of which branch we take below — the receipt should
    # always show WHO reported, even when that's not enough for quorum.
    witness_slugs_by_id = {w.id: w.slug for w in db.query(Witness).all()}
    reporting_witness_ids = sorted(witness_slugs_by_id[wid] for wid in latest_by_witness_id)

    snapshot = {
        "computed_at": now.isoformat(),
        "window_seconds": WINDOW_SECONDS,
        "total_registered": total_registered,
        "min_participation_fraction": MIN_PARTICIPATION_FRACTION,
        "agreement_supermajority_fraction": AGREEMENT_SUPERMAJORITY_FRACTION,
        "reporting_count": reporting_count,
        "participation_fraction": round(participation_fraction, 4),
        "reporting_witness_ids": reporting_witness_ids,
    }

    if total_registered == 0 or participation_fraction < MIN_PARTICIPATION_FRACTION:
        snapshot["status"] = "insufficient_data"
        snapshot["unhealthy_witness_ids"] = []
        snapshot["agreement_fraction"] = None
        return snapshot

    unhealthy_witness_ids = sorted(
        witness_slugs_by_id[wid] for wid, row in latest_by_witness_id.items() if _is_unhealthy(row)
    )
    agreement_fraction = len(unhealthy_witness_ids) / reporting_count

    snapshot["unhealthy_witness_ids"] = unhealthy_witness_ids
    snapshot["agreement_fraction"] = round(agreement_fraction, 4)
    snapshot["status"] = "degraded" if agreement_fraction >= AGREEMENT_SUPERMAJORITY_FRACTION else "operational"
    return snapshot


def apply_quorum_incident_logic(db: Session, rail: Rail, snapshot: dict) -> Incident | None:
    """
    Opens/updates/resolves Incident rows based on a snapshot from
    compute_quorum_snapshot. Only "degraded" and "operational" cause a
    write — "insufficient_data" deliberately leaves any existing open
    incident untouched, since a lack of quorum lets us confidently assert
    neither "still broken" nor "recovered."
    """
    now = datetime.utcnow()
    open_incident = (
        db.query(Incident)
        .filter(Incident.rail_id == rail.id, Incident.status != "resolved", Incident.is_historical.is_(False))
        .order_by(Incident.started_at.desc())
        .first()
    )

    status = snapshot["status"]

    if status == "insufficient_data":
        return open_incident

    if status == "degraded":
        if not open_incident:
            incident = Incident(
                rail_id=rail.id,
                title=f"Quorum-detected degradation on {rail.name}",
                severity="degraded",
                status="investigating",
                started_at=now,
                is_historical=False,
                is_live_simulation=False,
                quorum_snapshot=snapshot,
            )
            db.add(incident)
            db.flush()
            event = IncidentEvent(
                incident_id=incident.id,
                timestamp=now,
                label="Detected",
                narrative=(
                    f"{len(snapshot['unhealthy_witness_ids'])} of {snapshot['reporting_count']} reporting "
                    f"witnesses ({', '.join(snapshot['unhealthy_witness_ids'])}) marked {rail.name} unhealthy, "
                    f"crossing the {AGREEMENT_SUPERMAJORITY_FRACTION:.0%} agreement threshold."
                ),
            )
            db.add(event)
            db.commit()
            # Milestone 3: mirror this incident event into the hash chain.
            log_chain.append_log_entry(
                db, "incident_event",
                log_chain.incident_event_payload_fields(rail.slug, incident.id, event),
            )
            return incident

        # Still degraded — refresh the receipt; only log an event if which
        # witnesses disagree actually changed, to avoid an event per tick.
        previous_unhealthy = set((open_incident.quorum_snapshot or {}).get("unhealthy_witness_ids", []))
        update_event = None
        if set(snapshot["unhealthy_witness_ids"]) != previous_unhealthy:
            update_event = IncidentEvent(
                incident_id=open_incident.id,
                timestamp=now,
                label="Updated",
                narrative=(
                    f"Unhealthy witness set changed: {', '.join(snapshot['unhealthy_witness_ids']) or 'none'} "
                    f"now report {rail.name} unhealthy ({snapshot['agreement_fraction']:.0%} agreement)."
                ),
            )
            db.add(update_event)
        open_incident.quorum_snapshot = snapshot
        db.commit()
        # Milestone 3: mirror the incident event into the hash chain (only if one was created).
        if update_event is not None:
            log_chain.append_log_entry(
                db, "incident_event",
                log_chain.incident_event_payload_fields(rail.slug, open_incident.id, update_event),
            )
        return open_incident

    # status == "operational"
    if open_incident:
        open_incident.status = "resolved"
        open_incident.resolved_at = now
        open_incident.quorum_snapshot = snapshot
        resolve_event = IncidentEvent(
            incident_id=open_incident.id,
            timestamp=now,
            label="Resolved",
            narrative=(
                f"Quorum recovered: {snapshot['reporting_count']} witnesses reporting, "
                f"{snapshot['agreement_fraction']:.0%} now unhealthy — below the "
                f"{AGREEMENT_SUPERMAJORITY_FRACTION:.0%} threshold."
            ),
        )
        db.add(resolve_event)
        db.commit()
        # Milestone 3: mirror the resolution event into the hash chain.
        log_chain.append_log_entry(
            db, "incident_event",
            log_chain.incident_event_payload_fields(rail.slug, open_incident.id, resolve_event),
        )
    return open_incident
