"""
DPI Sentinel — synthetic probing engine.

This is the real part of the system: a scheduled async prober that performs
genuine HTTP/TLS reachability + latency checks against real public surfaces
of each rail, on a fixed interval, and persists results.

Design principle (and the thing we say out loud in the pitch, not hide):
  - Availability/latency numbers come from REAL probes against real public
    endpoints (status pages, public API gateways, auth surfaces).
  - Transaction-level "success rate" numbers — which require bank/PSP-side
    settlement visibility no outside party has — are SIMULATED. The
    simulation is calibrated against real documented incidents (e.g. the
    12 April 2025 UPI degradation) and is always labeled as such, never
    presented as live ground truth.
  - A live demo "inject outage" control flips a rail into a synthetic
    incident in real time, observable end-to-end: probe -> detection ->
    incident card -> resolution.
"""

import asyncio
import random
import time
from datetime import datetime
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from models import Rail, ProbeResult

PROBE_INTERVAL_SECONDS = 8
PROBE_TIMEOUT_SECONDS = 5


@dataclass
class RailState:
    """In-memory live state per rail, used for fast reads + injected-outage control."""
    baseline_success_rate: float = 0.995
    injected_outage: bool = False
    injection_severity: float = 0.50   # success rate floor during injected outage


class ProbeEngine:
    def __init__(self):
        self.states: dict[str, RailState] = {}
        self._client = httpx.AsyncClient(
            timeout=PROBE_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": "DPI-Sentinel/0.1 (+research project; respectful synthetic monitor)"},
        )

    def state_for(self, slug: str) -> RailState:
        if slug not in self.states:
            self.states[slug] = RailState()
        return self.states[slug]

    async def probe_once(self, rail: Rail) -> dict:
        """Perform one real network probe against the rail's public target."""
        start = time.perf_counter()
        reachable = False
        status_code = None
        error = None

        try:
            resp = await self._client.get(rail.probe_target)
            status_code = resp.status_code
            reachable = resp.status_code < 500
        except httpx.TimeoutException:
            error = "timeout"
        except httpx.ConnectError as e:
            error = f"connect_error: {str(e)[:120]}"
        except Exception as e:
            error = f"error: {str(e)[:120]}"

        latency_ms = round((time.perf_counter() - start) * 1000, 1)

        # --- Simulated transaction-success layer ---
        state = self.state_for(rail.slug)
        if state.injected_outage:
            # Smooth dip rather than a hard cliff, to look like a real degradation curve
            jitter = random.uniform(-0.04, 0.04)
            sim_rate = max(0.05, min(1.0, state.injection_severity + jitter))
            is_injection = True
        else:
            jitter = random.uniform(-0.01, 0.01)
            sim_rate = max(0.0, min(1.0, state.baseline_success_rate + jitter))
            is_injection = False

        return {
            "reachable": reachable,
            "http_status": status_code,
            "latency_ms": latency_ms if reachable else None,
            "error": error,
            "simulated_success_rate": round(sim_rate, 4),
            "is_synthetic_injection": is_injection,
        }

    def trigger_outage(self, slug: str, severity: float = 0.45):
        state = self.state_for(slug)
        state.injected_outage = True
        state.injection_severity = severity

    def resolve_outage(self, slug: str):
        state = self.state_for(slug)
        state.injected_outage = False

    async def aclose(self):
        await self._client.aclose()


engine = ProbeEngine()


async def run_probe_cycle(db_session_factory, rails: list[Rail]):
    """One full cycle: probe every rail concurrently, persist results."""
    results = await asyncio.gather(*[engine.probe_once(rail) for rail in rails])

    db: Session = db_session_factory()
    try:
        for rail, result in zip(rails, results):
            pr = ProbeResult(
                rail_id=rail.id,
                timestamp=datetime.utcnow(),
                reachable=result["reachable"],
                http_status=result["http_status"],
                latency_ms=result["latency_ms"],
                error=result["error"],
                simulated_success_rate=result["simulated_success_rate"],
                is_synthetic_injection=result["is_synthetic_injection"],
            )
            db.add(pr)
        db.commit()
    finally:
        db.close()

    return results
