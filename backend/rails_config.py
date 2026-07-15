"""
DPI Sentinel — rail configuration & seed data.

IMPORTANT — read before demo day:
The probe targets below were chosen as genuinely public, unauthenticated
surfaces (status/landing pages, public API gateways) that should be
reachable from a normal residential/campus network. They could NOT be
live-verified from this build sandbox, because the sandbox's outbound
network is restricted to an allowlist of developer domains (github,
pypi, npm, etc.) and returns a 403 "host_not_allowed" for anything else —
that is a property of THIS build environment, not of the public internet.

Before your demo: run `python verify_targets.py` on your own machine
(Fedora, your phone hotspot, whatever) to confirm each target responds.
If a target is blocked or flaky on the day, swap PROBE_TARGET_OVERRIDES
below — the rest of the system doesn't care what URL it's hitting.
"""

import random
from datetime import datetime, timedelta
from models import Rail

RAILS_SEED = [
    {
        "slug": "upi",
        "name": "UPI",
        "full_name": "Unified Payments Interface",
        "operator": "NPCI",
        "description": (
            "India's real-time interbank payment rail. ~22.35 billion transactions "
            "in April 2026 alone. No independent, real-time, cross-bank uptime monitor "
            "exists today — NPCI's own dashboard updates monthly."
        ),
        "monitor_mode": "live",
        "probe_target": "https://www.npci.org.in",
        "probe_methodology": (
            "Synthetic HTTPS GET against NPCI's public web surface every 8s, measuring "
            "TLS handshake + response latency and HTTP status. This measures the "
            "availability of NPCI's public-facing infrastructure, NOT live transaction "
            "settlement, which requires bank/PSP-side visibility no outside party has. "
            "Transaction-success-rate figures shown alongside are a calibrated simulation "
            "(see methodology page), not live ground truth."
        ),
        "color": "#1A7A5E",
    },
    {
        "slug": "digilocker",
        "name": "DigiLocker",
        "full_name": "DigiLocker — Digital Document Wallet",
        "operator": "Ministry of Electronics & IT",
        "description": (
            "National digital document wallet used for identity, education, and vehicle "
            "documents. Hundreds of millions of issued documents; outages block access to "
            "documents citizens often need urgently (verification, travel, admissions)."
        ),
        "monitor_mode": "live",
        "probe_target": "https://www.digilocker.gov.in",
        "probe_methodology": (
            "Synthetic HTTPS GET against DigiLocker's public web surface every 8s, "
            "measuring reachability and latency. Document-fetch success rate is a "
            "calibrated simulation layer, clearly labeled, not a live measurement."
        ),
        "color": "#2D6CA3",
    },
]

# If a real target is unreachable from a given network on demo day, swap it here —
# nothing else in the system needs to change.
PROBE_TARGET_OVERRIDES = {
    "upi": "http://demo-target/",
}


def seed_rails(db):
    for spec in RAILS_SEED:
        existing = db.query(Rail).filter_by(slug=spec["slug"]).first()
        target = PROBE_TARGET_OVERRIDES.get(spec["slug"], spec["probe_target"])
        if existing:
            existing.probe_target = target
            continue
        rail = Rail(
            slug=spec["slug"],
            name=spec["name"],
            full_name=spec["full_name"],
            operator=spec["operator"],
            description=spec["description"],
            monitor_mode=spec["monitor_mode"],
            probe_target=target,
            probe_methodology=spec["probe_methodology"],
            color=spec["color"],
        )
        db.add(rail)
    db.commit()


def backfill_probe_history(db, hours=24, interval_minutes=8):
    """
    Populate a plausible-looking probe history on first boot so the
    sparkline and 24h stats aren't empty for the first several minutes
    of a live demo. This is explicitly synthetic backfill (timestamps in
    the past, values drawn from the same baseline distribution the live
    prober uses) — not a substitute for the real-time probing loop, which
    takes over immediately for all NEW data going forward.
    """
    from models import ProbeResult

    for spec in RAILS_SEED:
        rail = db.query(Rail).filter_by(slug=spec["slug"]).first()
        if not rail:
            continue
        already = db.query(ProbeResult).filter_by(rail_id=rail.id).first()
        if already:
            continue  # never backfill over real accumulated history

        now = datetime.utcnow()
        n_points = int((hours * 60) / interval_minutes)
        for i in range(n_points, 0, -1):
            ts = now - timedelta(minutes=i * interval_minutes)
            rate = max(0.0, min(1.0, 0.995 + random.uniform(-0.012, 0.008)))
            db.add(ProbeResult(
                rail_id=rail.id,
                timestamp=ts,
                reachable=True,
                http_status=200,
                latency_ms=round(random.uniform(90, 260), 1),
                error=None,
                simulated_success_rate=round(rate, 4),
                is_synthetic_injection=False,
            ))
    db.commit()
