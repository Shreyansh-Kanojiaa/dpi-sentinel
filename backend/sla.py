"""
DPI Sentinel — SLA computation & incident detection.

This is the analytical layer that turns raw probe rows into the things a
status page actually needs: rolling uptime %, latency percentiles, and
automatic incident detection/resolution based on the simulated
success-rate signal crossing thresholds.
"""

from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import select

from models import Rail, ProbeResult, Incident, IncidentEvent

# Thresholds — deliberately simple and stated plainly (defensible in Q&A:
# "why these numbers" -> they mirror common SRE/SLA conventions: <99.5% is
# a real degradation worth flagging for a rail processing billions of
# transactions a day, where even fractions of a percent are large absolute
# numbers of affected transactions).
DEGRADED_THRESHOLD = 0.985
MAJOR_THRESHOLD = 0.90
CRITICAL_THRESHOLD = 0.70


def severity_for_rate(rate: float) -> str | None:
    if rate is None:
        return None
    if rate < CRITICAL_THRESHOLD:
        return "critical"
    if rate < MAJOR_THRESHOLD:
        return "major"
    if rate < DEGRADED_THRESHOLD:
        return "minor"
    return None


def rolling_uptime(db: Session, rail_id: int, hours: int = 24) -> dict:
    """Compute rolling availability + simulated success-rate stats for a rail."""
    since = datetime.utcnow() - timedelta(hours=hours)
    rows = (
        db.execute(
            select(ProbeResult)
            .where(ProbeResult.rail_id == rail_id, ProbeResult.timestamp >= since)
            .order_by(ProbeResult.timestamp.asc())
        )
        .scalars()
        .all()
    )

    if not rows:
        return {
            "availability_pct": None,
            "avg_latency_ms": None,
            "avg_simulated_success_rate": None,
            "sample_count": 0,
            "sparkline": [],
        }

    reachable_count = sum(1 for r in rows if r.reachable)
    latencies = [r.latency_ms for r in rows if r.latency_ms is not None]
    sim_rates = [r.simulated_success_rate for r in rows if r.simulated_success_rate is not None]

    return {
        "availability_pct": round(100 * reachable_count / len(rows), 3),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "avg_simulated_success_rate": round(sum(sim_rates) / len(sim_rates), 4) if sim_rates else None,
        "sample_count": len(rows),
        # last 60 points for the sparkline strip
        "sparkline": [
            {
                "t": r.timestamp.isoformat(),
                "rate": r.simulated_success_rate,
                "reachable": r.reachable,
                "injected": r.is_synthetic_injection,
            }
            for r in rows[-60:]
        ],
    }


def detect_and_update_incidents(db: Session, rail: Rail, latest_result: dict):
    """
    Called after each probe cycle. Opens a new incident if the success rate
    crosses a threshold and none is currently open; updates / auto-resolves
    an open incident as the rate recovers.
    """
    rate = latest_result.get("simulated_success_rate")
    severity = severity_for_rate(rate)
    now = datetime.utcnow()

    open_incident = (
        db.query(Incident)
        .filter(Incident.rail_id == rail.id, Incident.status != "resolved", Incident.is_historical.is_(False))
        .order_by(Incident.started_at.desc())
        .first()
    )

    if severity and not open_incident:
        # New incident detected
        incident = Incident(
            rail_id=rail.id,
            title=f"Elevated failure rate detected on {rail.name}",
            severity=severity,
            status="investigating",
            started_at=now,
            is_historical=False,
            is_live_simulation=latest_result.get("is_synthetic_injection", False),
            min_success_rate=rate,
        )
        db.add(incident)
        db.flush()
        db.add(IncidentEvent(
            incident_id=incident.id,
            timestamp=now,
            label="Detected",
            narrative=(
                f"Synthetic monitoring detected simulated success rate of "
                f"{rate * 100:.1f}% on {rail.name}, crossing the {severity} threshold."
            ),
        ))
        db.commit()
        return incident

    if open_incident:
        if severity:
            # Still degraded — update min rate and maybe severity
            if rate is not None and (open_incident.min_success_rate is None or rate < open_incident.min_success_rate):
                open_incident.min_success_rate = rate
            if severity != open_incident.severity:
                open_incident.severity = severity
                db.add(IncidentEvent(
                    incident_id=open_incident.id,
                    timestamp=now,
                    label="Updated",
                    narrative=f"Severity reassessed to {severity} ({rate * 100:.1f}% success rate).",
                ))
            db.commit()
        else:
            # Recovered — resolve
            open_incident.status = "resolved"
            open_incident.resolved_at = now
            db.add(IncidentEvent(
                incident_id=open_incident.id,
                timestamp=now,
                label="Resolved",
                narrative=f"Success rate recovered to {rate * 100:.1f}%, above degradation threshold.",
            ))
            db.commit()

    return open_incident
