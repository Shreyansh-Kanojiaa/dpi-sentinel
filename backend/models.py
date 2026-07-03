"""
DPI Sentinel — data models.

Core entities:
  Rail        — a monitored piece of digital public infrastructure (UPI, DigiLocker, ...)
  ProbeResult — a single synthetic probe outcome against a rail's monitored target
  Incident    — a detected or historical disruption, with a timeline of events
  IncidentEvent — a single timestamped entry in an incident's timeline (detection, update, resolution)
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Text
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


class ProbeResult(Base):
    __tablename__ = "probe_results"

    id = Column(Integer, primary_key=True)
    rail_id = Column(Integer, ForeignKey("rails.id"), nullable=False)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    # Availability layer (real, from actual HTTP/TLS probing)
    reachable = Column(Boolean, nullable=False)
    http_status = Column(Integer, nullable=True)
    latency_ms = Column(Float, nullable=True)
    error = Column(String, nullable=True)

    # Simulated transaction-success layer (modeled, clearly labeled in API + UI)
    simulated_success_rate = Column(Float, nullable=True)       # 0.0 - 1.0
    is_synthetic_injection = Column(Boolean, default=False)     # true during demo-triggered outage

    rail = relationship("Rail", back_populates="probes")


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
    min_success_rate = Column(Float, nullable=True)              # worst point during incident

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
