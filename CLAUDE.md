# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

DPI Sentinel is a prototype public-interest status page for India's digital public infrastructure (UPI, DigiLocker), built for a hackathon (SIPS 2026). Full pitch/context/demo script is in `README.md` — read it before making product decisions, since it explains the real/simulated data split that is the entire premise of the project.

**The core design principle, load-bearing everywhere in the code:** availability/latency come from real live HTTP/TLS probes; transaction-level "success rate" is an explicitly labeled simulation (no outside party has bank/PSP settlement visibility). Never blur this line — e.g. don't make simulated data look like a live measurement in the API or UI, and don't add real-looking precision to the simulation.

The project is being built in deliberate milestones so each part is understood before the next is added.

- **Milestone 0**: `backend/` + `frontend/` as a self-contained monolith — the backend probed its own configured targets and ran threshold-based incident detection on a simulated success-rate number (`sla.py`'s `detect_and_update_incidents`, now unused — see below).
- **Milestone 1**: `witness/` — a standalone, independently-deployed service with its own Ed25519 identity that probes a target and signs what it saw. Decoupled from `backend/` on purpose; at the end of Milestone 1 there was no aggregator to receive its reports yet.
- **Milestone 2 (current)**: `backend/` was turned into that aggregator. It no longer probes anything itself — it verifies signed observations from registered witnesses and runs quorum consensus (`quorum.py`) to decide a rail's status. This *was* explicitly asked for, which is why `witness/` is now wired to `backend/` — that's a deliberate exception to the "keep them decoupled" default, not a reversal of it. `witness/` itself was not touched to make this happen and remains an independently-deployable service.
- **Milestone 3 (current)**: the tamper-evident log. `backend/` now also appends every verified observation and every incident event to a hash-chained, append-only `LogEntry` table (`log_chain.py`), seals batches into Merkle checkpoints signed by the aggregator's *own* Ed25519 identity (`identity.py`, `merkle.py`, `checkpoints.py`), and anchors each signed root into an external git repo. `verify_log.py` re-derives the whole chain and cross-checks each checkpoint root against both the DB and the git-committed copy. This is additive: it did **not** change quorum consensus logic — the only edits to `quorum.py` are `log_chain.append_log_entry(...)` calls placed *after* the existing commits that create `IncidentEvent` rows.
- **Milestone 4 (not yet built)**: Evidence Certificate generation and a citizen-facing verify page, consuming Milestone 3's log/proofs. Don't build this unless explicitly asked.

Old guidance still holds in spirit: don't casually blur layers or merge systems beyond what a given milestone explicitly asks for. But "decoupled" no longer means "wire `witness/` into `backend/` is forbidden" — Milestone 2 already did that, correctly, on request.

## Running it

Full stack via Docker Compose (aggregator + 3 witnesses — the normal way to run this now):
```bash
docker compose up
```
`aggregator` (backend/, port 8420) builds its witness registry at startup from `WITNESS_URLS`, retrying each with backoff since compose gives no startup-order guarantee. All three witnesses currently point at the same UPI target (`docker-compose.yml`) so a 3-witness quorum is actually demonstrable — see quorum.py's design notes below for why a rail with only one witness could never clear participation quorum, which is correct behavior, not a bug.

Backend only (Python 3.10+, SQLite, no venv assumed), against locally-running or remote witnesses:
```bash
cd backend
pip install -r requirements.txt
WITNESS_URLS="http://127.0.0.1:8500,http://127.0.0.1:8501,http://127.0.0.1:8502" python -m uvicorn main:app --reload --port 8420
```
Other env vars, all with defaults baked into `main.py`/`quorum.py`/`registry.py`: `MIN_PARTICIPATION_FRACTION` (0.66), `AGREEMENT_SUPERMAJORITY_FRACTION` (0.6), `WINDOW_SECONDS` (60), `QUORUM_TICK_SECONDS` (5), `OBSERVATION_FRESHNESS_SECONDS` (30), `FUTURE_TOLERANCE_SECONDS` (5), `DB_URL`.

Frontend:
```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, expects backend at http://127.0.0.1:8420 (override with VITE_API_BASE)
npm run build
npm run lint        # oxlint
```
**Not yet updated for Milestone 2**: the frontend still expects the old severity-graded status/simulated-rate shape and its "Inject simulated outage" button calls the now-inert `probe_engine` demo endpoints. Expect it to look wrong until a later milestone reconnects it to `quorum`'s three-state status. This is expected, not a regression to fix reflexively.

Before any real demo, verify the real probe targets are reachable from the network you'll demo on (the dev sandbox restricts outbound traffic to an allowlist and cannot verify them):
```bash
cd backend
python verify_targets.py
```
If a target fails, edit `PROBE_TARGET_OVERRIDES` in `rails_config.py` — but note that whatever URL ends up there must exactly match the `target` string a witness reports (its own `PROBE_TARGET`), or the aggregator's rail-matching lookup in `POST /observations` will reject its observations as belonging to no known rail. Keep both in sync when swapping a target.

There is no test suite in this repo currently.

## Architecture

```
backend/   FastAPI + SQLite (SQLAlchemy) + APScheduler, single process — the aggregator
  models.py            Rail, Witness, ProbeResult, Incident, IncidentEvent,
                        LogEntry, Checkpoint (SQLAlchemy schema)
  identity.py          Aggregator's OWN Ed25519 keypair (Milestone 3), same load-or-generate
                        pattern as witness/identity.py — signs checkpoints, not observations
  log_chain.py         Append-only hash chain: append_log_entry() (serialized under a lock),
                        GENESIS_PREV_HASH, compute_entry_hash(), payload-field builders
  merkle.py            Pure-Python SHA-256 Merkle tree: build_levels/compute_root/
                        inclusion_proof/verify_proof (no external Merkle lib)
  checkpoints.py       maybe_create_checkpoint() (50-entries-or-1-hour trigger), signs the
                        root, get_inclusion_proof(), and git anchoring via subprocess
  verify_log.py        Standalone verifier: recompute-and-compare the whole chain + every
                        checkpoint root against DB AND the git-committed copy
  registry.py          Builds the trusted witness registry from WITNESS_URLS at startup,
                        concurrently, each with its own retry/backoff loop
  signing.py           Aggregator-side canonical_json_bytes() + verify_observation_signature() —
                        must stay byte-for-byte in sync with witness/signing.py's method
  quorum.py            compute_quorum_snapshot() (pure, read-derived participation + agreement
                        fractions -> operational/insufficient_data/degraded) and
                        apply_quorum_incident_logic() (opens/updates/resolves Incidents)
  main.py              FastAPI app; POST /observations (verify + freshness + rail-match),
                        lifespan startup (schema+seed+registry+quorum-tick scheduler), REST endpoints
  rails_config.py      Rail definitions/probe targets + on-first-boot synthetic history backfill
                        (backfill is no longer called from lifespan — see invariants below)
  historical_seed.py   The one real, sourced historical incident (12 April 2025 UPI)
  verify_targets.py    Standalone script to confirm probe targets are reachable pre-demo
  probe_engine.py, sla.py   Milestone-0 self-probing + threshold-detection code. Left in place,
                        unused — see "What's dead" below. Don't delete without being asked;
                        don't resurrect either without checking why they were retired.

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

### Data flow / request lifecycle (Milestone 2 — current)

1. On aggregator startup (`main.py` lifespan), schema is created, rails + historical incidents are seeded, and `registry.build_registry` fetches `/pubkey` from every `WITNESS_URLS` entry concurrently, each with its own retry/backoff loop. **`rails_config.backfill_probe_history` is deliberately no longer called** — it manufactured synthetic `ProbeResult` rows with no `witness_id`, which doesn't fit a model where every row must trace back to a registered witness's verified signature. The function is left in place, just uncalled.
2. Each `witness/` instance probes its own target on its own schedule (independent of the aggregator) and `POST`s a signed observation to `{AGGREGATOR_URL}/observations`.
3. `POST /observations` (`main.py`) does, in order: (a) look up the claimed `witness_id` in the `Witness` registry — reject unknown witnesses outright, never auto-register from the payload; (b) recompute the canonical-JSON SHA-256 hash from the raw observation fields and verify the Ed25519 signature against the *registry's* stored `public_key_hex` — never anything the payload itself claims about its hash or key; (c) reject if the timestamp is more than `OBSERVATION_FRESHNESS_SECONDS` old or more than `FUTURE_TOLERANCE_SECONDS` in the future (replay defense); (d) match `payload.target` against `Rail.probe_target` to find which rail this observation is about, rejecting if nothing matches. Only then is a `ProbeResult` row stored (with `witness_id`, `signature_hex`, `observation_hash_hex`), `Witness.last_seen_at` updated, and `quorum.compute_quorum_snapshot` + `apply_quorum_incident_logic` run for just that rail.
4. `quorum.py` replaces `sla.py`'s old threshold-on-simulated-rate detection entirely. `compute_quorum_snapshot` is pure/read-derived: it counts distinct witnesses whose most recent observation for a rail falls within `WINDOW_SECONDS`, divides by total registered witnesses for **participation**, and — only if participation clears `MIN_PARTICIPATION_FRACTION` — computes what fraction of those reporting witnesses are "unhealthy" (`not reachable or http_status is None or http_status >= 500`) for **agreement** against `AGREEMENT_SUPERMAJORITY_FRACTION`. Status is `insufficient_data` (participation not met), `degraded` (agreement met), or `operational` (neither) — see quorum.py's design notes below for why these are two separate checks, not one.
5. A periodic `quorum_tick_job` (every `QUORUM_TICK_SECONDS`, via `AsyncIOScheduler`) re-runs the same evaluation for every rail even absent new observations — this is what catches a witness going silent, which the event-driven path in step 3 would never notice on its own (nothing arrives to trigger a re-check).
6. `GET /api/rails` and `/api/rails/{slug}` still compute everything on read (`main.serialize_rail` calls both `sla.rolling_uptime`, still valid for latency/availability display, and `quorum.compute_quorum_snapshot` fresh) — there is still no persisted "current status" field on `Rail`.
7. The old demo control endpoints (`POST /api/demo/trigger-outage/{slug}`, `resolve-outage/{slug}`) still exist and still mutate `ProbeEngine`'s in-memory `RailState`, but nothing reads that state anymore — they're inert until a later milestone reconnects them, deliberately left as dead code rather than removed (see "What's dead" below).
8. Frontend polls the backend via `src/api.js` and renders directly from these REST responses; it has **not** been updated for the new three-state status or `quorum_snapshot` field yet.

### What's dead (left in place on purpose, not silently deleted)

- `probe_engine.py` — the `ProbeEngine`/`RailState`/`run_probe_cycle` self-probing code. Still imported by `main.py` only for the inert demo endpoints; `run_probe_cycle` itself is never called.
- `sla.py`'s `detect_and_update_incidents` and `severity_for_rate` — fully superseded by `quorum.py`. `sla.rolling_uptime` is the one thing from this file still in active use.
- `rails_config.backfill_probe_history` — still a valid function, just not called from `lifespan` anymore.

If you're asked to clean up "unused code" in `backend/`, check this list and this file before assuming any of it is safe to delete — it's unused *by current milestone scope*, not orphaned by accident.

### Key invariants to preserve when editing

- The aggregator's trust in a witness's public key comes **only** from `registry.py`'s out-of-band fetch at startup, keyed by `WITNESS_URLS` — never from anything a `POST /observations` payload claims about its own key or hash. `signing.verify_observation_signature` always recomputes the hash from the raw fields itself.
- `POST /observations` must check signature validity **and** timestamp freshness **and** rail-target match, in that order, before writing anything. Skipping the freshness check reopens the replay attack it exists to prevent (see quorum.py design notes).
- `quorum.compute_quorum_snapshot` must never let a low-participation rail fall back to `"operational"` — that's the entire point of the `insufficient_data` state. Silence from witnesses is not evidence of health.
- Participation and agreement are checked as two independent fractions, in that order (participation gates whether agreement is even evaluated) — collapsing them into one check produces wrong answers in both directions (see quorum.py's module docstring for concrete scenarios).
- `Incident.quorum_snapshot` must be included in `serialize_incident`'s API output, not just stored — it's the "receipt" for why an incident was declared, and a hidden receipt defeats the point. (This was missed once during Milestone 2 development and had to be fixed — don't reintroduce the gap.)
- `ProbeResult` rows written via `POST /observations` always carry `witness_id`, `signature_hex`, `observation_hash_hex` — rows from before Milestone 2 (or any future non-witness path) may have `witness_id IS NULL`; `quorum.py` filters those out explicitly (`ProbeResult.witness_id.isnot(None)`).
- Incident open/resolve logic still keys off `Incident.status != "resolved"` and `Incident.is_historical.is_(False)` — historical incidents (the seeded 12 April 2025 entry) must never be picked up by quorum logic either.
- `rails_config.PROBE_TARGET_OVERRIDES` is still the one sanctioned way to change what URL is probed — but now it must be kept in sync with whatever `PROBE_TARGET` the corresponding witness instances are configured with, since `POST /observations` matches on exact string equality against `Rail.probe_target`.
- (Milestone 3) `entry_hash = sha256(prev_hash + payload)` and the `payload` must be built with `signing.canonical_json_bytes` — the *same* serializer the witnesses/observation-verifier use. `verify_log.py` and `log_chain.compute_entry_hash` must stay byte-for-byte identical, or honest entries "fail" verification. Don't introduce a second serializer.
- (Milestone 3) `verify_log.py` rebuilds each Merkle leaf from CONTENT (`compute_entry_hash(prev_hash, payload)`), **not** from the stored `entry_hash` column — otherwise a payload edit that leaves `entry_hash` untouched slips past the checkpoint check (it was caught this way during development). Checkpoint *creation* uses the freshly-correct stored `entry_hash`, which is fine; only the verifier must recompute from payload.
- (Milestone 3) `log_chain.append_log_entry` holds `_APPEND_LOCK` across read-max-seq → insert → commit. Don't move the commit outside the lock or make the sequence rely on autoincrement — concurrent `POST /observations` would fork the chain.
- (Milestone 3) The genesis `prev_hash` is the fixed `"0"*64`, never NULL/empty — it makes "this is entry #1" a checkable claim. Git anchoring failures (no remote/network/auth) must stay non-fatal (warn + continue), same tolerance as the witness→aggregator POST.

### witness/ — design notes

- Each witness has its own Ed25519 identity, generated once and persisted to `KEY_PATH` (a Docker volume per instance in `docker-compose.yml` — `witness-a-keys`, `witness-b-keys`, `witness-c-keys` — so keys are never shared across instances or images).
- The signed unit is always the *canonical JSON* of the observation dict (`witness_id, timestamp, target, reachable, http_status, latency_ms, error`), hashed with SHA-256, then that hash is what gets Ed25519-signed — not the observation dict directly. Any code that needs to verify a signature must reproduce the observation dict, canonicalize it the same way (`signing.canonical_json_bytes`), hash it, and verify against that hash — never re-derive canonical form ad hoc elsewhere.
- The signature proves authorship + integrity of a witness's report, not truthfulness of the underlying probe, and not Sybil-resistance across witnesses — that reasoning lives with the aggregator (`backend/`, see below), not here.
- The aggregator endpoint (`POST /observations`) exists now (Milestone 2, `backend/main.py`), but `witness/prober.report_observation` is unchanged — it still treats `httpx.ConnectError`/timeouts as expected-and-logged, not exceptional. Don't add retries, queuing, or circuit breakers here; that's still explicitly deferred, and adding it here vs. in the aggregator are different decisions with different tradeoffs (see below).

### backend/ (aggregator) — design notes

- The registry (`registry.py`) is the *only* source of truth for which public key belongs to which `witness_id`. It's built once, at aggregator startup, from `WITNESS_URLS` — concurrently, each URL with its own retry/backoff loop, so one slow-to-start witness container doesn't block the others from registering. **Known limitation:** if a witness is still down after the retry budget is exhausted (~10 attempts, capped exponential backoff, well under 2 minutes total by default), it will never be registered until the aggregator process itself restarts — there's no background retry for witnesses that missed the startup window. This was deliberately not built out further to keep Milestone 2 scoped; flag it rather than silently add continuous re-registration polling without being asked.
- `POST /observations` never trusts anything the payload claims about its own key or hash — see `signing.verify_observation_signature`'s docstring. This is the load-bearing security property of the whole milestone: without it, anyone could submit a `witness_id` they don't own alongside a keypair they do, and the signature would "verify" against a key they control while claiming to speak for someone else.
- The freshness check (`OBSERVATION_FRESHNESS_SECONDS`, default 30s) exists specifically to bound replay: a captured, validly-signed "all healthy" observation has a ~30s shelf life before it becomes unusable to mask a real, ongoing outage. This is separate from `WINDOW_SECONDS` (default 60s), which is quorum.py's *aggregation* window for deciding current status — don't conflate the two constants when tuning either.
- Quorum consensus deliberately runs from two independent checks, not one — `MIN_PARTICIPATION_FRACTION` (are enough witnesses even talking?) gates whether `AGREEMENT_SUPERMAJORITY_FRACTION` (do the ones talking agree it's broken?) is even evaluated. A single surviving witness reporting "healthy" must never look like consensus (that's the participation check), and a single dissenting witness among many must never look like an outage (that's the agreement check) — see `quorum.py`'s module docstring for the exact failure scenarios this prevents.
- Rail-routing in `POST /observations` matches `payload.target` against `Rail.probe_target` by exact string equality. This is fragile by construction (see the invariant above about keeping `PROBE_TARGET_OVERRIDES` and witness `PROBE_TARGET` env vars in sync) but not a trust/security issue — a witness can't forge *authorship* this way, it can at most misroute its own genuine observations if misconfigured. A future milestone could replace this with an explicit witness-to-rail assignment rather than inferring it from a freeform URL string.
- A rail with fewer registered witnesses covering it than `MIN_PARTICIPATION_FRACTION` requires (e.g., a rail only one witness ever targets, against a 3-witness global registry) will sit at `insufficient_data` permanently, no matter how healthy that one witness's reports are. This is correct, not a bug — don't "fix" it by special-casing low-coverage rails to fall back to a looser check.
