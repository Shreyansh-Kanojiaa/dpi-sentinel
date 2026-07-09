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
  team would collect. As of Milestone 2, this probing is no longer done by
  the status-page backend itself — it's done by independent, Ed25519-signed
  `witness/` instances, and a rail's status (`operational` /
  `insufficient_data` / `degraded`) is a live quorum consensus across
  whichever witnesses reported recently, not one process's opinion of
  itself. See "Multi-witness architecture" below.
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
backend/   FastAPI + SQLite + APScheduler — the aggregator (Milestone 2)
  models.py            SQLAlchemy schema: Rail, Witness, ProbeResult, Incident, IncidentEvent
  registry.py          Builds the trusted witness registry from WITNESS_URLS at startup
                        (retries each with backoff — never trusts a key an observation claims)
  signing.py           Recomputes canonical-JSON + SHA-256 hash, verifies Ed25519 signature
                        against the REGISTERED key — mirrors witness/signing.py's method exactly
  quorum.py            Quorum consensus: participation + agreement fractions -> operational /
                        insufficient_data / degraded, replacing sla.py's old self-probe detection
  main.py              REST API + POST /observations (signature + freshness + rail-match checks)
  rails_config.py      Rail definitions, real probe targets, history backfill
  historical_seed.py   Real, sourced historical incident (12 April 2025 UPI)
  verify_targets.py    Run this on your own network before demoing
  probe_engine.py, sla.py   Tier-0 self-probing code, now unused (kept, not deleted — see
                        CLAUDE.md for what's dead and why)

frontend/  React (Vite) — ledger-style status page
  src/App.jsx          Main layout: rail rows, incident log, methodology
  src/PulseStrip.jsx   The signature "pulse" sparkline visual
  src/api.js           Backend client

witness/   Standalone signed-observation service (Milestone 1)
  identity.py          Ed25519 keypair (PyNaCl), generated on first boot, persisted to KEY_PATH
  signing.py           Canonical JSON + SHA-256 + Ed25519 signing of observations
  prober.py             Async HTTP probe of PROBE_TARGET + signed POST to the aggregator
  main.py              FastAPI app: background probe loop + /pubkey, /health
```

### Multi-witness architecture (Milestones 1 + 2)

The whole point of this design is that no single process's word is trusted.
Independent `witness/` instances each probe the same target and sign what
they saw with their own private key (Milestone 1). The `backend/` aggregator
no longer probes anything itself — it verifies each witness's signature
against a public key it fetched itself, out-of-band, at startup (never a
key an observation claims to have), rejects anything stale (a basic
replay defense), and only believes a rail is healthy or unhealthy when a
quorum of recently-reporting witnesses actually agree (Milestone 2).

That last point is deliberately strict: if too few registered witnesses
have reported recently, the status is `insufficient_data` — not
`operational`. A monitor that quietly assumes "healthy" when it's actually
just gone deaf isn't independent, it's just slow to notice. Every
quorum-triggered incident carries a `quorum_snapshot` — which witnesses
reported, which disagreed, and the exact fractions — as the receipt for
why the call was made; it's returned by `GET /api/incidents`.

### Tamper-evident log (Milestone 3)

Verified observations and incident events used to sit in ordinary database
rows — which means the operator (me) could quietly edit an old row and
nothing would notice. Milestone 3 makes that **mathematically detectable**
instead of a matter of trust:

- **Hash chain.** Every accepted observation and every incident event is
  also appended to a single append-only `log_entries` table, where each
  entry commits to the one before it: `entry_hash = sha256(prev_hash +
  payload)`. Edit any old payload and that entry's hash — and every hash
  after it — stops matching. The first entry chains to a fixed genesis
  value (64 zeros) so "where the log begins" is itself a checkable claim.
  Appends are serialized under a lock so concurrent `POST /observations`
  requests can't fork the chain.
- **Merkle checkpoints.** A scheduled job seals batches of the log into a
  signed checkpoint whenever 50 new entries accumulate or an hour passes
  (whichever first): it builds a SHA-256 Merkle tree over the batch and
  stores the root, signed with the **aggregator's own Ed25519 key**
  (`backend/identity.py` — separate from any witness key, because "the log
  looked exactly like this as of seq N" is the aggregator's claim to make,
  not a witness's). `GET /api/log/{entry_id}/proof` returns a Merkle
  inclusion proof so anyone can confirm one entry belongs to a published
  root without downloading the whole log.
- **External anchoring.** After each checkpoint, its signed root is written
  as JSON into a git repo (`CHECKPOINT_REPO_PATH`), committed, and pushed.
  This is the load-bearing part: the DB alone can't prove it wasn't
  rewritten, because the operator controls all of it. A copy of the root in
  a git remote the operator can't silently rewrite is what lets
  `verify_log.py` catch a full after-the-fact rewrite of the database.
- **Verification.** `python verify_log.py` walks the whole chain, recomputes
  every hash, and cross-checks each checkpoint's Merkle root against the DB
  **and** the git-committed copy, reporting the exact sequence number where
  anything breaks.

**Git remote / auth setup you must provide** for anchoring to actually push
(it degrades gracefully to commit-only, then to DB-only, if these are
missing — a failed push logs a warning and never crashes the aggregator):

- Point `CHECKPOINT_REPO_PATH` at a directory (a Docker volume in compose:
  `aggregator-checkpoints`). On first checkpoint the aggregator runs
  `git init` there and sets a local commit identity automatically, so
  **commit-only** anchoring works with zero setup.
- To actually **push** to a real GitHub repo, that directory needs a remote
  named `origin` and working auth. Simplest options:
  - **Deploy key / PAT over HTTPS:** `git -C <repo> remote add origin
    https://<token>@github.com/<you>/<checkpoints-repo>.git` — the token is
    a fine-grained PAT with `contents:write` on that one repo. Mount it via
    an env var / secret rather than baking it into the image.
  - **SSH deploy key:** add `origin` as `git@github.com:<you>/<repo>.git`,
    mount a read-write deploy key into the container's `~/.ssh`, and make
    sure `known_hosts` trusts `github.com`.
  - Push targets whatever branch `HEAD` is on; the checkpoints repo should
    be a dedicated, ideally public repo (public commit history is exactly
    the point — an outside timestamped witness to your roots).

## Running it

**Full stack (aggregator + 3 witnesses), via Docker Compose — the easiest way
to see quorum consensus actually working:**

```bash
docker compose up
```

This brings up the `aggregator` (backend/, port `8420`) and three witness
instances — `witness-a`/`witness-b`/`witness-c` on host ports
`8500`/`8501`/`8502` — all currently pointed at the same UPI target so a
3-witness quorum is demonstrable (stop one, 2/3 still participates; stop
two, participation drops below quorum). Each witness persists its own
Ed25519 identity in its own named volume; the aggregator persists its
SQLite db in the `aggregator-db` volume. Check it's working:

```bash
curl http://localhost:8420/api/rails | python3 -m json.tool
curl http://localhost:8500/pubkey
docker compose logs aggregator
```

You should see the aggregator log each witness registering at startup
(with retries if a witness container isn't up yet), then accepted
`POST /observations` requests, then `GET /api/rails` reporting `status`
as `operational` once enough witnesses agree. Quorum thresholds
(`MIN_PARTICIPATION_FRACTION`, `AGREEMENT_SUPERMAJORITY_FRACTION`,
`WINDOW_SECONDS`) are configurable via the `aggregator` service's
environment in `docker-compose.yml`.

**Backend only** (Python 3.10+, no virtualenv friction — uses
`--break-system-packages` if needed), if you want to run it without Docker
against locally-running witnesses:

```bash
cd backend
pip install -r requirements.txt
WITNESS_URLS="http://127.0.0.1:8500,http://127.0.0.1:8501,http://127.0.0.1:8502" \
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
real public surface. Note that whatever URL you set here must exactly
match the `target` string a witness reports, or the aggregator will reject
its observations as belonging to no known rail — update the witness's
`PROBE_TARGET` env var to match.

**Frontend:**

```bash
cd frontend
npm install
npm run dev
```

Visit `http://localhost:5173`. The frontend expects the backend at
`http://127.0.0.1:8420` by default (override with `VITE_API_BASE`).
**Known gap as of Milestone 2:** the frontend hasn't been updated for the
new three-state status or the `quorum` field yet, and the "Inject
simulated outage" demo button no longer does anything observable (it
still flips in-memory state in `probe_engine.py`, but nothing reads that
anymore — status now comes purely from witness quorum). Reconnecting the
demo control to the new pipeline is a follow-up, not done yet.

## Demo script (for judges)

**Note:** steps 3–4 below describe the Milestone-0/1 demo flow via the
frontend's "Inject simulated outage" button. As of Milestone 2 that button
is inert (see "Running it" above) — the equivalent live demo today is
stopping/restarting witness containers or POSTing synthetic signed
observations (see the aggregator's design notes in CLAUDE.md) to show
quorum consensus reacting in real time. Update this script once the
frontend is wired back up to the new pipeline.

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

- Evidence Certificate generation + a citizen-facing verify page
  (Milestone 4), consuming the tamper-evident log below. The hash-chained
  transparency log, Merkle checkpoints, and git anchoring are done
  (Milestone 3) — see "Tamper-evident log" above; multi-witness quorum
  consensus is done (Milestone 2).
- Reconnect the frontend's demo controls and rail-status display to the
  new quorum-based three-state status (`operational` / `insufficient_data`
  / `degraded`) and `quorum_snapshot` receipt, replacing the old
  threshold-on-simulated-rate severity display.
- Witness coverage for more than one rail per witness (or more witnesses),
  so DigiLocker isn't permanently `insufficient_data`.
- Real settlement-adjacent signals via partnership with a PSP sandbox or
  bank API program, replacing the simulation layer where possible.
- More rails: ONDC, ABDM/ABHA, Aadhaar/AEPS.
- A public "DPI Uptime Leaderboard" — transparent, methodology-first,
  applying equal scrutiny across all monitored rails.
- Alerting (webhook/SMS) for civic tech orgs, journalists, and researchers.
- Open data export of historical incident timelines for public-interest
  research.
