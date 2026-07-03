# DPI Sentinel

An independent, public-interest observability layer for India's digital public
infrastructure (DPI). Built for SIPS 2026 (CSITM, IIM Bangalore) under the
"Digital Trust & Infrastructure" theme.

## The problem

UPI alone clears over 22 billion transactions a month. When it degrades — as
it did for roughly five hours on 12 April 2025 — citizens find out from
social media, not a dashboard. NPCI's own uptime reporting updates monthly.
No independent, real-time, cross-rail monitor exists today for the rails
hundreds of millions of people depend on daily.

DPI Sentinel is a working prototype of what that monitor could look like:
real synthetic probing of public infrastructure surfaces, automatic incident
detection and timeline generation, and a public status page that's honest
about exactly what it can and cannot measure from outside.

## What's real vs. simulated (read this before any demo or Q&A)

This is the most important thing about the project, not a caveat to bury:

- **Availability and latency are measured live**, via real HTTP/TLS synthetic
  probes against each rail's public-facing surface, on a fixed interval.
  This is genuine uptime monitoring, the same category of signal a real SRE
  team would collect.
- **Transaction-level success rate is a calibrated simulation.** No outside
  party — including this project — has bank or PSP-side visibility into real
  UPI/DigiLocker transaction settlement. We simulate this layer, calibrated
  against publicly documented incidents (see the 12 April 2025 entry in the
  incident log), and label it as simulated everywhere it appears in the UI
  and API.
- **The historical incident is real**, reconstructed from public reporting
  (NPCI statements, subsequent press coverage), with a source note attached.
  We could not independently verify the exact figures against a primary
  NPCI dataset, and say so.

This honesty is itself part of the pitch: an accountability tool that hides
its own limitations isn't one worth trusting.

## Architecture

```
backend/   FastAPI + SQLite + APScheduler
  models.py            SQLAlchemy schema: Rail, ProbeResult, Incident, IncidentEvent
  probe_engine.py       Async HTTP prober + injectable-outage demo control
  sla.py               Rolling uptime computation + threshold-based incident detection
  rails_config.py      Rail definitions, real probe targets, history backfill
  historical_seed.py   Real, sourced historical incident (12 April 2025 UPI)
  main.py              REST API + scheduler wiring
  verify_targets.py    Run this on your own network before demoing

frontend/  React (Vite) — ledger-style status page
  src/App.jsx          Main layout: rail rows, incident log, methodology
  src/PulseStrip.jsx   The signature "pulse" sparkline visual
  src/api.js           Backend client

witness/   Standalone signed-observation service (Milestone 1 of a
           planned multi-witness architecture — not yet wired into the
           backend/frontend above)
  identity.py          Ed25519 keypair (PyNaCl), generated on first boot, persisted to KEY_PATH
  signing.py           Canonical JSON + SHA-256 + Ed25519 signing of observations
  prober.py             Async HTTP probe of PROBE_TARGET + signed POST to an aggregator
  main.py              FastAPI app: background probe loop + /pubkey, /health
```

The `witness/` service is the first step toward removing single-party trust
from the monitor itself: instead of one process probing and reporting on
its own word, multiple independent witness instances each sign what they
observed with their own private key, so no witness can fake or alter
another's report. The aggregator that collects and cross-checks witness
reports is a later milestone — right now each witness runs standalone,
logs its probe results, and logs (without crashing) when it can't reach an
aggregator, since none exists yet.

## Running it

**Backend** (Python 3.10+, no virtualenv friction — uses `--break-system-packages`
if needed):

```bash
cd backend
pip install fastapi uvicorn httpx sqlalchemy aiosqlite pydantic apscheduler python-multipart
python -m uvicorn main:app --reload --port 8420
```

**IMPORTANT — before any real demo**, verify the real probe targets are
reachable from your network (they could not be verified from the build
sandbox used to write this code, which restricts outbound traffic to an
allowlist of developer domains):

```bash
cd backend
python verify_targets.py
```

If a target fails, edit `PROBE_TARGET_OVERRIDES` in `rails_config.py` — the
rest of the system doesn't care what URL it's hitting, as long as it's a
real public surface.

**Frontend:**

```bash
cd frontend
npm install
npm run dev
```

Visit `http://localhost:5173`. The frontend expects the backend at
`http://127.0.0.1:8420` by default (override with `VITE_API_BASE`).

**Witness service** (standalone — see Architecture above; no aggregator
exists yet, so this runs and reports independently of the backend/frontend):

```bash
docker compose up witness-a
```

This starts three witness instances defined in `docker-compose.yml`
(`witness-a`, `witness-b`, `witness-c`), each with its own Ed25519 identity
(persisted in its own Docker volume) and probe target, on host ports
`8500`/`8501`/`8502`. Check one with:

```bash
curl http://localhost:8500/pubkey
curl http://localhost:8500/health
docker compose logs witness-a
```

You should see periodic probe log lines and a warning that it can't reach
the aggregator yet — that's expected until the aggregator milestone lands.

## Demo script (for judges)

1. Show the status page at rest — both rails operational, real availability
   and latency numbers ticking on an 8-second interval, simulated success
   rate visible and clearly labeled.
2. Expand a rail row — show the methodology box. This is where you explain
   the real/simulated split out loud, before anyone can ask "wait, is this
   real?"
3. Click **Inject simulated outage** on UPI. Within ~10 seconds: the header
   flips to "Active disruption detected," the row turns critical, the pulse
   strip visibly cliffs off, and a new incident card appears in the log with
   an auto-generated detection narrative — while DigiLocker, right below,
   stays untouched. That isolation is the proof the system actually works
   per-rail, not just globally.
4. Click **Resolve**. Watch the recovery propagate: pulse strip recovers,
   incident auto-closes with a "Resolved" event and the actual recovered
   rate.
5. Scroll to the historical incident — the real 12 April 2025 UPI
   degradation, with full sourcing — to show the same system modeling
   ground truth, not just synthetic demo data.
6. Close on "Why an independent register" — the accountability framing.

## What's next (roadmap, for the deck)

- Multi-witness aggregator: collect signed observations from independent
  `witness/` instances, cross-check them, and surface disagreement between
  witnesses as its own signal (in progress — witness service built,
  aggregator not yet).
- Real settlement-adjacent signals via partnership with a PSP sandbox or
  bank API program, replacing the simulation layer where possible.
- More rails: ONDC, ABDM/ABHA, Aadhaar/AEPS.
- A public "DPI Uptime Leaderboard" — transparent, methodology-first,
  applying equal scrutiny across all monitored rails.
- Alerting (webhook/SMS) for civic tech orgs, journalists, and researchers.
- Open data export of historical incident timelines for public-interest
  research.
