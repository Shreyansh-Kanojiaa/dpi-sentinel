"""
DPI Sentinel — reset the operational DISPLAY layer for a clean demo start.

Standalone, manual-only. Not imported by main.py, not wired into lifespan,
not on any scheduler — the only way this runs is `python reset_demo_state.py`,
by hand.

WHAT THIS TOUCHES (the read-model the UI renders from):
  - ProbeResult   — deleted entirely
  - Incident      — deleted where is_historical is False (every live-detected
                     test incident from this week's stress testing)
  - IncidentEvent — deleted for whichever incidents above get deleted
                     (cascade would also do this via the ORM relationship,
                     but we delete explicitly so the counts we print are
                     exact, not inferred)
  - Then re-runs historical_seed.seed_historical_incidents(), which is
    idempotent per-title: it recreates the 12 April 2025 incident if
    missing, or does nothing if it's already there (it always is, since
    is_historical rows are never touched above).

WHAT THIS NEVER TOUCHES (the permanent record):
  - LogEntry, Checkpoint, and the git-anchored checkpoint files — see the
    module docstring in log_chain.py: a LogEntry's `payload` column is a
    self-contained canonical-JSON snapshot of the record's field VALUES at
    append time (e.g. incident_id is stored as a plain int inside that
    JSON string), not a live foreign key to the Incident/ProbeResult row.
    verify_log.py and checkpoints.py never query Incident/ProbeResult/
    IncidentEvent at all (grep confirms it) — they only ever read
    LogEntry/Checkpoint. Deleting rows from the operational tables this
    script touches cannot change a single byte of the permanent log; this
    script doesn't even import log_chain.py, merkle.py, or checkpoints.py.
  - Witness, WitnessRailAssignment — not imported, not queried, not
    written. Registration and rail assignment are untouched by construction.
  - EvidenceCertificate — never deleted or modified. Reported (count +
    timestamps) for visibility only. One side effect worth knowing about:
    SQLite FK enforcement is off in this codebase (no PRAGMA anywhere), so
    if a certificate was issued against an incident this script deletes,
    that certificate row's incident_id will point at a row that no longer
    exists. Nothing in the running app re-reads incident_id off an issued
    certificate (grep confirms — it's write-once at issuance), so this is
    a cosmetic dangling reference, not a functional break. Reported below,
    not silently left for you to discover later.

Run manually:
    python reset_demo_state.py           # asks for a typed confirmation
    python reset_demo_state.py --yes     # skips the prompt (e.g. scripted use)
"""

import argparse
import os
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from historical_seed import seed_historical_incidents
from models import Incident, IncidentEvent, ProbeResult, EvidenceCertificate, LogEntry, Checkpoint, Witness, WitnessRailAssignment

DB_URL = os.environ.get("DB_URL", "sqlite:///./dpi_sentinel.db")

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def ok(msg):
    print(f"{GREEN}  OK {RESET} {msg}")


def fail(msg):
    print(f"{RED} FAIL{RESET} {msg}")


def warn(msg):
    print(f"{YELLOW} WARN{RESET} {msg}")


def snapshot(db) -> dict:
    """Read-only counts across every table this script cares about —
    including the ones it must NOT change, so before/after can prove it
    didn't touch them, not just claim it."""
    return {
        "probe_results": db.query(ProbeResult).count(),
        "incidents_live": db.query(Incident).filter(Incident.is_historical.is_(False)).count(),
        "incidents_historical": db.query(Incident).filter(Incident.is_historical.is_(True)).count(),
        "incident_events": db.query(IncidentEvent).count(),
        "evidence_certificates": db.query(EvidenceCertificate).count(),
        "log_entries": db.query(LogEntry).count(),
        "checkpoints": db.query(Checkpoint).count(),
        "witnesses": db.query(Witness).count(),
        "witness_rail_assignments": db.query(WitnessRailAssignment).count(),
    }


def print_snapshot(label, snap):
    print(f"\n{label}")
    for k, v in snap.items():
        print(f"  {k}: {v}")


def report_certificates(db, live_incident_ids: set[int]):
    """Read-only. Lists every EvidenceCertificate and flags which ones
    reference an incident that's about to be deleted (informational only —
    the certificate row itself is never touched)."""
    certs = db.query(EvidenceCertificate).order_by(EvidenceCertificate.issued_at.asc()).all()
    print("\n=== EVIDENCE CERTIFICATES (read-only, never modified by this script) ===")
    if not certs:
        print("  none issued")
        return
    print(f"  {len(certs)} certificate(s) on record:")
    dangling = 0
    for c in certs:
        will_dangle = c.incident_id in live_incident_ids
        marker = " -- incident being wiped, incident_id will become stale" if will_dangle else ""
        if will_dangle:
            dangling += 1
        print(f"    {c.certificate_id}  issued {c.issued_at}  incident_id={c.incident_id}{marker}")
    if dangling:
        warn(
            f"{dangling} certificate(s) reference an incident this reset deletes. The certificate "
            f"rows are untouched, but their incident_id will no longer resolve to a live Incident "
            f"row (SQLite FK enforcement is off in this project, so this is silent unless you look "
            f"for it — nothing in the running app re-reads incident_id after issuance, so this has "
            f"no functional effect, only a stale reference if you inspect the table directly)."
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="skip the interactive confirmation prompt")
    args = parser.parse_args()

    engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
    db = sessionmaker(bind=engine)()
    try:
        print(f"Resetting demo display state in {DB_URL}")
        before = snapshot(db)
        print_snapshot("Before reset:", before)

        live_incident_ids = {
            row[0] for row in db.query(Incident.id).filter(Incident.is_historical.is_(False)).all()
        }
        report_certificates(db, live_incident_ids)

        if not args.yes:
            print(
                f"\nAbout to delete {before['probe_results']} ProbeResult rows and "
                f"{before['incidents_live']} live Incident row(s) ({before['incident_events']} "
                f"IncidentEvent rows total across all incidents). LogEntry/Checkpoint/Witness/"
                f"WitnessRailAssignment/EvidenceCertificate are never touched."
            )
            reply = input("Type 'yes' to proceed: ").strip().lower()
            if reply != "yes":
                print("Aborted — nothing was changed.")
                sys.exit(1)

        deleted_events = 0
        if live_incident_ids:
            deleted_events = (
                db.query(IncidentEvent)
                .filter(IncidentEvent.incident_id.in_(live_incident_ids))
                .delete(synchronize_session=False)
            )
        deleted_incidents = (
            db.query(Incident).filter(Incident.is_historical.is_(False)).delete(synchronize_session=False)
        )
        deleted_probes = db.query(ProbeResult).delete(synchronize_session=False)
        db.commit()

        print(f"\nDeleted {deleted_probes} ProbeResult row(s)")
        print(f"Deleted {deleted_incidents} live Incident row(s) and {deleted_events} IncidentEvent row(s)")

        seed_historical_incidents(db)
        hist = db.query(Incident).filter(Incident.is_historical.is_(True)).all()
        if len(hist) == 1:
            ok(f"historical incident confirmed present: \"{hist[0].title}\" (started {hist[0].started_at})")
        elif len(hist) == 0:
            fail("no historical incident present after seeding — historical_seed.py did not run correctly")
        else:
            warn(f"expected exactly 1 historical incident, found {len(hist)} — check historical_seed.py for duplicate titles")

        after = snapshot(db)
        print_snapshot("After reset:", after)

        clean = True
        if after["log_entries"] != before["log_entries"]:
            fail(f"log_entries count changed ({before['log_entries']} -> {after['log_entries']}) — this must NEVER happen")
            clean = False
        else:
            ok(f"log_entries unchanged: {after['log_entries']}")

        if after["checkpoints"] != before["checkpoints"]:
            fail(f"checkpoints count changed ({before['checkpoints']} -> {after['checkpoints']}) — this must NEVER happen")
            clean = False
        else:
            ok(f"checkpoints unchanged: {after['checkpoints']}")

        if after["witnesses"] != before["witnesses"] or after["witness_rail_assignments"] != before["witness_rail_assignments"]:
            fail("witness registry or rail assignments changed — this script must never touch them")
            clean = False
        else:
            ok(f"witnesses ({after['witnesses']}) and witness_rail_assignments ({after['witness_rail_assignments']}) unchanged")

        if after["evidence_certificates"] != before["evidence_certificates"]:
            fail("evidence_certificates row count changed — this script must never delete certificates")
            clean = False
        else:
            ok(f"evidence_certificates unchanged: {after['evidence_certificates']} (contents also untouched)")

        if after["incidents_live"] != 0:
            fail(f"expected 0 live incidents after reset, found {after['incidents_live']}")
            clean = False

        print()
        if clean:
            print(f"{GREEN}RESULT: demo display state reset. Permanent log/checkpoint/witness records untouched.{RESET}")
            sys.exit(0)
        else:
            print(f"{RED}RESULT: reset completed but one or more invariants failed — see FAIL lines above.{RESET}")
            sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
