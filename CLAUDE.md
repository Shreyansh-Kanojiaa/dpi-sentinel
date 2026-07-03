# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

DPI Sentinel is a prototype public-interest status page for India's digital public infrastructure (UPI, DigiLocker), built for a hackathon (SIPS 2026). Full pitch/context/demo script is in `README.md` — read it before making product decisions, since it explains the real/simulated data split that is the entire premise of the project.

**The core design principle, load-bearing everywhere in the code:** availability/latency come from real live HTTP/TLS probes; transaction-level "success rate" is an explicitly labeled simulation (no outside party has bank/PSP settlement visibility). Never blur this line — e.g. don't make simulated data look like a live measurement in the API or UI, and don't add real-looking precision to the simulation.

The project is being built in deliberate milestones so each part is understood before the next is added. `backend/` + `frontend/` (the status page) is the milestone-0 monolith. `witness/` (Milestone 1, see below) is a separate, standalone service being built toward a future multi-witness architecture — a central aggregator that cross-checks signed observations from independent witnesses is a later, not-yet-built milestone. Don't build the aggregator, wire `witness/` into `backend/`, or merge these systems unless explicitly asked — they are intentionally decoupled right now.

## Running it

Backend (Python 3.10+, SQLite, no venv assumed):
```bash
cd backend
pip install fastapi uvicorn httpx sqlalchemy aiosqlite pydantic apscheduler python-multipart
python -m uvicorn main:app --reload --port 8420
```

Frontend:
```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, expects backend at http://127.0.0.1:8420 (override with VITE_API_BASE)
npm run build
npm run lint        # oxlint
```

Before any real demo, verify the real probe targets are reachable from the network you'll demo on (the dev sandbox restricts outbound traffic to an allowlist and cannot verify them):
```bash
cd backend
python verify_targets.py
```
If a target fails, edit `PROBE_TARGET_OVERRIDES` in `rails_config.py` — nothing else needs to change, the prober just hits whatever URL is configured.

Witness service (standalone, Docker-based; see Architecture below):
```bash
docker compose up witness-a          # or witness-b / witness-c, or all three
curl http://localhost:8500/pubkey
curl http://localhost:8500/health
docker compose logs witness-a
```
Each witness generates/persists its own Ed25519 key on first boot into its own named volume (`witness-a-keys`, etc. — see `docker-compose.yml`), so instances never share identity. Without an aggregator running, expect (and don't treat as a bug) repeated log warnings that the POST to `AGGREGATOR_URL` failed to connect.

There is no test suite in this repo currently.

## Architecture

```
backend/   FastAPI + SQLite (SQLAlchemy) + APScheduler, single process
  models.py            Rail, ProbeResult, Incident, IncidentEvent (SQLAlchemy schema)
  probe_engine.py       Async httpx prober (real) + in-memory per-rail RailState with
                        injectable-outage demo control (probe_engine.trigger_outage/resolve_outage)
  sla.py               Rolling uptime/latency aggregation + threshold-based incident
                        open/update/auto-resolve state machine
  rails_config.py      Rail definitions/probe targets + on-first-boot synthetic history backfill
  historical_seed.py   The one real, sourced historical incident (12 April 2025 UPI)
  main.py              FastAPI app, lifespan startup (schema+seed+scheduler), REST endpoints
  verify_targets.py    Standalone script to confirm probe targets are reachable pre-demo

frontend/  React 19 + Vite, no router/state library
  src/App.jsx          Entire UI: rail rows, incident log, methodology panel, demo controls
  src/PulseStrip.jsx   Sparkline visualization of recent probe history
  src/api.js           Thin fetch wrapper for the backend REST API

witness/   Standalone FastAPI service, independent of backend/ and frontend/
  identity.py          Loads/generates an Ed25519 keypair (PyNaCl) at KEY_PATH; the only
                        module that reads/writes private-key bytes
  signing.py           canonical_json_bytes() (sorted keys, fixed separators) + SHA-256 +
                        Ed25519 sign — the single source of truth for reproducible signing
  prober.py             Real async httpx probe of PROBE_TARGET + signed POST to
                        f"{AGGREGATOR_URL}/observations"; connection failures are logged
                        and swallowed, never crash the loop
  main.py              FastAPI app; lifespan starts an asyncio background probe_loop task
                        (not APScheduler); GET /pubkey, GET /health
```

### Data flow / request lifecycle

1. On startup (`main.py` lifespan), schema is created, rails + historical incidents are seeded, and 24h of plausible synthetic probe history is backfilled (only if no real rows exist yet — never overwrites accumulated data).
2. `AsyncIOScheduler` runs `probe_job` every `PROBE_INTERVAL_SECONDS` (8s, in `probe_engine.py`): concurrently probes every rail via real HTTP requests, persists a `ProbeResult` row per rail, then runs `sla.detect_and_update_incidents` per rail against that fresh result.
3. Incident detection is threshold-based on the *simulated* success rate (`sla.py`: minor <98.5%, major <90%, critical <70%) — opens an `Incident` with an initial `IncidentEvent`, updates severity/min-rate while still degraded, and auto-resolves when the rate recovers above threshold.
4. The demo control endpoints (`POST /api/demo/trigger-outage/{slug}`, `POST /api/demo/resolve-outage/{slug}`) mutate `ProbeEngine`'s in-memory `RailState` for a slug — the next probe cycle picks up the injected severity and the normal detection pipeline reacts as if it were real, which is the point (isolation is per-rail, propagates through the same code path as a genuine detection).
5. `GET /api/rails` and `/api/rails/{slug}` compute derived state on read (`main.serialize_rail` calls `sla.rolling_uptime` and looks up any open non-historical incident) rather than storing status — there is no persisted "current status" field on `Rail`.
6. Frontend polls the backend via `src/api.js` and renders directly from these REST responses; no local caching/state management beyond React state in `App.jsx`.

### Key invariants to preserve when editing

- `ProbeResult` always carries both layers on every row: real (`reachable`, `http_status`, `latency_ms`, `error`) and simulated (`simulated_success_rate`, `is_synthetic_injection`). Don't let one layer silently drive the other.
- `backfill_probe_history` must never run if any real `ProbeResult` rows already exist for a rail (checked via `already = db.query(ProbeResult).filter_by(rail_id=rail.id).first()`).
- Incident open/resolve logic keys off `Incident.status != "resolved"` and `Incident.is_historical.is_(False)` — historical incidents (the seeded 12 April 2025 entry) must never be picked up by the live detection loop.
- `rails_config.PROBE_TARGET_OVERRIDES` is the one sanctioned way to change what URL is probed; the rest of the system is target-agnostic.

### witness/ — design notes

- Each witness has its own Ed25519 identity, generated once and persisted to `KEY_PATH` (a Docker volume per instance in `docker-compose.yml` — `witness-a-keys`, `witness-b-keys`, `witness-c-keys` — so keys are never shared across instances or images).
- The signed unit is always the *canonical JSON* of the observation dict (`witness_id, timestamp, target, reachable, http_status, latency_ms, error`), hashed with SHA-256, then that hash is what gets Ed25519-signed — not the observation dict directly. Any code that needs to verify a signature must reproduce the observation dict, canonicalize it the same way (`signing.canonical_json_bytes`), hash it, and verify against that hash — never re-derive canonical form ad hoc elsewhere.
- The signature proves authorship + integrity of a witness's report, not truthfulness of the underlying probe, and not Sybil-resistance across witnesses — that reasoning lives with the (future) aggregator, not here.
- The aggregator endpoint (`POST /observations`) does not exist yet. `prober.report_observation` must keep treating `httpx.ConnectError`/timeouts as expected-and-logged, not exceptional — don't add retries, queuing, or circuit breakers here; that's explicitly deferred to a later milestone.
