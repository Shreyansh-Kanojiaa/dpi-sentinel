"""
DPI Sentinel — main application.

Run with:
    uvicorn main:app --reload --port 8420

On startup:
  - creates SQLite schema
  - seeds rail config + historical incidents
  - starts an APScheduler job that probes every rail on a fixed interval
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from models import Base, Rail, Incident, IncidentEvent, ProbeResult
from rails_config import seed_rails, backfill_probe_history
from historical_seed import seed_historical_incidents
from probe_engine import engine as probe_engine, run_probe_cycle, PROBE_INTERVAL_SECONDS
from sla import rolling_uptime, detect_and_update_incidents

DB_URL = "sqlite:///./dpi_sentinel.db"
db_engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)

scheduler = AsyncIOScheduler()


async def probe_job():
    db = SessionLocal()
    try:
        rails = db.query(Rail).all()
    finally:
        db.close()

    results = await run_probe_cycle(SessionLocal, rails)

    # Run incident detection per rail against the just-recorded result
    db = SessionLocal()
    try:
        for rail, result in zip(rails, results):
            detect_and_update_incidents(db, rail, result)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=db_engine)
    db = SessionLocal()
    try:
        seed_rails(db)
        seed_historical_incidents(db)
        backfill_probe_history(db)
    finally:
        db.close()

    scheduler.add_job(probe_job, "interval", seconds=PROBE_INTERVAL_SECONDS, id="probe_job", next_run_time=datetime.utcnow())
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
        "status": open_incident.severity if open_incident else "operational",
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


@app.get("/health")
def health():
    return {"ok": True}
