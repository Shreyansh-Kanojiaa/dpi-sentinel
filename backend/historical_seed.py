"""
DPI Sentinel — historical incident seed data.

These are REAL, documented past incidents, included to demonstrate the
incident-timeline feature against ground truth rather than only synthetic
demo data. Each entry carries a source_note for the claim — cite it if
asked. Figures are as publicly reported; treat them as best-available
estimates, not audited statistics, and say so if pressed.
"""

from datetime import datetime
from models import Rail, Incident, IncidentEvent


HISTORICAL_INCIDENTS = [
    {
        "rail_slug": "upi",
        "title": "UPI transaction success rate degradation — 12 April 2025",
        "severity": "critical",
        "status": "resolved",
        "started_at": datetime(2025, 4, 12, 11, 40),
        "resolved_at": datetime(2025, 4, 12, 16, 40),
        "min_success_rate": 0.50,
        "source_note": (
            "Publicly reported via NPCI statements and subsequent coverage (e.g. "
            "Outlook Money, ORF) that the UPI success rate fell to approximately "
            "50% for nearly two hours starting ~11:40, recovering to approximately "
            "80% for the following three hours before full resolution. NPCI "
            "attributed the disruption to a surge in 'Check Transaction Status' "
            "API calls. We could not independently verify these figures against a "
            "primary NPCI dataset — this entry represents best-available public "
            "reporting, presented as historical context, not a live measurement."
        ),
        "events": [
            (datetime(2025, 4, 12, 11, 40), "Detected", "Publicly reported onset of degraded UPI transaction success rates, beginning around 11:40 IST."),
            (datetime(2025, 4, 12, 12, 30), "Identified", "Cause later attributed by NPCI to a surge in 'Check Transaction Status' API calls overloading backend systems."),
            (datetime(2025, 4, 12, 13, 40), "Monitoring", "Success rate reported to have partially recovered to roughly 80% as mitigation took effect."),
            (datetime(2025, 4, 12, 16, 40), "Resolved", "Full service restoration reported, after roughly five hours of degraded service overall."),
        ],
    }
]


def seed_historical_incidents(db):
    for spec in HISTORICAL_INCIDENTS:
        rail = db.query(Rail).filter_by(slug=spec["rail_slug"]).first()
        if not rail:
            continue
        existing = db.query(Incident).filter_by(rail_id=rail.id, title=spec["title"]).first()
        if existing:
            continue
        incident = Incident(
            rail_id=rail.id,
            title=spec["title"],
            severity=spec["severity"],
            status=spec["status"],
            started_at=spec["started_at"],
            resolved_at=spec["resolved_at"],
            is_historical=True,
            is_live_simulation=False,
            source_note=spec["source_note"],
            min_success_rate=spec["min_success_rate"],
        )
        db.add(incident)
        db.flush()
        for ts, label, narrative in spec["events"]:
            db.add(IncidentEvent(incident_id=incident.id, timestamp=ts, label=label, narrative=narrative))
        db.commit()
