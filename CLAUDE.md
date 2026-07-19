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
- **Milestone 4 (current)**: the citizen-facing payoff. `backend/certificates.py` issues signed Evidence Certificates via `POST /api/certificates` — but ONLY for time windows where quorum consensus actually declared an incident (no manual-override/admin path exists, on purpose; demo incidents go through the real pipeline). Certificates embed the incident's `quorum_snapshot` receipt, the incident's hash-chain entries with Merkle inclusion proofs, and an explicit disclaimer that they confirm an *infrastructure* incident, never an individual transaction outcome (the claimed transaction ref is stored/displayed as self-reported and unverified — this distinction ships in the document itself). Signed with the SAME aggregator key that signs checkpoints — one identity for the aggregator's claims, no third key. `POST /api/verify` re-checks (1) signature against the aggregator's OWN key (never the submitted one), (2) inclusion proofs with leaves rebuilt from content, (3) checkpoint roots against the git-anchored copies (via `verify_log.load_git_checkpoint`) — three separately-reported failure modes, never one boolean. Issuance is rate-limited per IP (in-memory, fine for a single-process aggregator). Frontend: `OutageCopilot.jsx` (guidance panel shown only while a rail is `degraded`, with the certificate request form) and `VerifyPage.jsx` (hash-routed `#/verify`, paste/upload a certificate bundle). Not built, deliberately: certificate revocation, citizen accounts/auth, the B2B/paid API tier.

- **Milestone 5 (current)**: explicit witness-to-rail assignment, replacing the Milestone-4-era stopgap where every witness probed every rail just to keep quorum's participation math meaningful. `backend/models.py` adds `WitnessRailAssignment` (witness_id, rail_id, assigned_at) — the source of truth for which witnesses are supposed to cover a rail, independent of whether they're currently healthy or reporting. `witness/main.py`'s `GET /pubkey` now also declares its configured targets; `backend/registry.py`'s `_sync_rail_assignments` (called from `build_registry`, at every aggregator startup, for every witness it just registered) matches those declared target URLs against `Rail.probe_target` — same exact-string-match rule `POST /observations` uses to route incoming observations — and makes `WitnessRailAssignment` match exactly what was just declared (adds new assignments, removes stale ones). `quorum.compute_quorum_snapshot`'s participation denominator changed from `total_registered` (every registered witness, globally) to `assigned_count` (witnesses assigned to *this* rail); a rail with zero assigned witnesses is `insufficient_data` with an explicit `reason: "no witnesses assigned"` in the snapshot, never a divide-by-zero and never a silent "operational". `GET /api/rails` now also returns a `witness_coverage` string (e.g. `"2/3 assigned witnesses reporting"`) and a new `GET /api/diagnostics/witness-assignments` lists the raw assignment table. `docker-compose.yml` adds `witness-d`, assigned to DigiLocker only, to prove partial coverage works without reducing UPI's or DigiLocker's existing 3-witness coverage. This is additive/corrective to quorum.py's denominator only — it did **not** touch signature verification, the hash chain, checkpoints, or certificate logic.

Old guidance still holds in spirit: don't casually blur layers or merge systems beyond what a given milestone explicitly asks for. But "decoupled" no longer means "wire `witness/` into `backend/` is forbidden" — Milestone 2 already did that, correctly, on request.

## Running it

Full stack via Docker Compose (aggregator + 3 witnesses — the normal way to run this now):
```bash
docker compose up
```
`aggregator` (backend/, port 8420) builds its witness registry at startup from `WITNESS_URLS`, retrying each with backoff since compose gives no startup-order guarantee. All three witnesses currently probe BOTH the UPI and DigiLocker targets (`docker-compose.yml`'s `PROBE_TARGETS`, see "Multi-target witnesses" below) so a 3-witness quorum is actually demonstrable on *each* rail — see quorum.py's design notes below for why a rail covered by only a subset of registered witnesses could never clear participation quorum, which is correct behavior given that math, not a bug.

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
**Frontend catch-up status**: Milestone 4 taught the rail rows the quorum three-state status (`degraded` / `insufficient_data` labels + colors) and added the Outage Copilot + `#/verify` page, but the sparkline/simulated-rate display and the "Inject simulated outage" button still reflect the old self-probing model — the button calls the now-inert `probe_engine` demo endpoints. That remainder is expected, not a regression to fix reflexively.

Before any real demo, verify the real probe targets are reachable from the network you'll demo on (the dev sandbox restricts outbound traffic to an allowlist and cannot verify them):
```bash
cd backend
python verify_targets.py
```
If a target fails, edit `PROBE_TARGET_OVERRIDES` in `rails_config.py` — but note that whatever URL ends up there must exactly match a `target` string a witness reports (one entry in its `PROBE_TARGETS`), or the aggregator's rail-matching lookup in `POST /observations` will reject those observations as belonging to no known rail. Keep both in sync when swapping a target.

**Multi-target witnesses:** each `witness/` instance probes every target in its `PROBE_TARGETS` env var (`label:url` pairs, comma-separated), which may be a subset of monitored rails or all of them — a witness's choice of coverage is independent of the aggregator's math now (see Milestone 5 above). Each target still gets its own independent HTTP probe, its own signature, its own `POST /observations` — never a batched/combined signature over multiple targets. Historical note: between Milestone 4 and Milestone 5, every witness had to cover every rail as a stopgap, because `quorum.py`'s participation math divided by the *total registered witness count* rather than a per-rail-assigned count — a rail covered by only some witnesses could never clear `MIN_PARTICIPATION_FRACTION`, which is exactly what left DigiLocker permanently at `insufficient_data`. Milestone 5's `WitnessRailAssignment` table replaced that global denominator with a per-rail one, so partial coverage (like `witness-d`, DigiLocker-only) now works correctly instead of just being avoided.

There is no test suite in this repo currently.

## Architecture

```
backend/   FastAPI + SQLite (SQLAlchemy) + APScheduler, single process — the aggregator
  models.py            Rail, Witness, ProbeResult, Incident, IncidentEvent,
                        LogEntry, Checkpoint, EvidenceCertificate,
                        WitnessRailAssignment (Milestone 5 — witness_id, rail_id,
                        assigned_at; the participation denominator's source of truth)
                        (SQLAlchemy schema)
  identity.py          Aggregator's OWN Ed25519 keypair (Milestone 3), same load-or-generate
                        pattern as witness/identity.py — signs checkpoints, not observations
  log_chain.py         Append-only hash chain: append_log_entry() (serialized under a lock),
                        GENESIS_PREV_HASH, compute_entry_hash(), payload-field builders
  merkle.py            Pure-Python SHA-256 Merkle tree: build_levels/compute_root/
                        inclusion_proof/verify_proof (no external Merkle lib)
  checkpoints.py       maybe_create_checkpoint() (50-entries-or-1-hour trigger), signs the
                        root, get_inclusion_proof(), and git anchoring via subprocess
  verify_log.py        Standalone verifier: recompute-and-compare the whole chain + every
                        checkpoint root against DB AND the git-committed copy; also exports
                        load_git_checkpoint() reused by certificates.py's /api/verify path
  certificates.py      Milestone 4: per-IP rate limiter, find_covering_incident() (the ONLY
                        path to a certificate — a real quorum-declared window), certificate
                        assembly/signing (same aggregator key as checkpoints), and
                        verify_certificate() (3 separately-reported checks)
  unmatched_targets.py Fix (post-Milestone-4): in-memory count + last-50 log of observations
                        POST /observations rejected for matching no Rail.probe_target — the
                        rejection itself already existed (400 + WARNING log); this only makes
                        it visible via GET /api/diagnostics/unmatched-targets instead of
                        requiring someone to already be tailing container logs
  registry.py          Builds the trusted witness registry from WITNESS_URLS at startup,
                        concurrently, each with its own retry/backoff loop. Also (Milestone 5)
                        fetches each witness's declared targets alongside its pubkey and syncs
                        WitnessRailAssignment rows via _sync_rail_assignments — matched against
                        Rail.probe_target, same rule POST /observations uses to route
  signing.py           Aggregator-side canonical_json_bytes() + verify_observation_signature() —
                        must stay byte-for-byte in sync with witness/signing.py's method
  quorum.py            compute_quorum_snapshot() (pure, read-derived participation + agreement
                        fractions -> operational/insufficient_data/degraded) and
                        apply_quorum_incident_logic() (opens/updates/resolves Incidents).
                        Milestone 5: participation's denominator is assigned_count (witnesses
                        assigned to THIS rail via WitnessRailAssignment), not total registered
                        witness count; zero-assigned is insufficient_data with an explicit
                        reason field, never a divide-by-zero or silent "operational"
  main.py              FastAPI app; POST /observations (verify + freshness + rail-match),
                        lifespan startup (schema+seed+registry+quorum-tick scheduler), REST endpoints.
                        Milestone 5: serialize_rail adds a witness_coverage string
                        ("N/M assigned witnesses reporting"); GET /api/diagnostics/witness-assignments
                        lists the raw WitnessRailAssignment table
  rails_config.py      Rail definitions/probe targets + on-first-boot synthetic history backfill
                        (backfill is no longer called from lifespan — see invariants below)
  historical_seed.py   The one real, sourced historical incident (12 April 2025 UPI)
  verify_targets.py    Standalone script to confirm probe targets are reachable pre-demo
  probe_engine.py, sla.py   Milestone-0 self-probing + threshold-detection code. Left in place,
                        unused — see "What's dead" below. Don't delete without being asked;
                        don't resurrect either without checking why they were retired.

frontend/  React 19 + Vite, no router/state library (a two-line hash "router" in App.jsx
           switches to the verify page — don't add react-router for this)
  src/App.jsx          Main UI: rail rows, incident log, methodology panel, demo controls
  src/OutageCopilot.jsx  Milestone 4: guidance panel shown only while a rail is "degraded"
                        (don't-retry / check-your-bank / scam warning) + the Evidence
                        Certificate request form, readable result, and JSON download
  src/VerifyPage.jsx   Milestone 4: #/verify — paste/upload a certificate bundle, calls
                        POST /api/verify, renders the per-check breakdown
  src/PulseStrip.jsx   Sparkline visualization of recent probe history
  src/api.js           Thin fetch wrapper for the backend REST API

witness/   Standalone FastAPI service, independent of backend/ and frontend/
  identity.py          Loads/generates an Ed25519 keypair (PyNaCl) at KEY_PATH; the only
                        module that reads/writes private-key bytes
  signing.py           canonical_json_bytes() (sorted keys, fixed separators) + SHA-256 +
                        Ed25519 sign — the single source of truth for reproducible signing
  prober.py             Real async httpx probe of one target + signed POST to
                        f"{AGGREGATOR_URL}/observations"; connection failures are logged
                        and swallowed, never crash the loop. Single-target — called once
                        per PROBE_TARGETS entry by main.py's probe_loop, unchanged itself.
  main.py              FastAPI app; parses PROBE_TARGETS (fails loudly if empty/malformed)
                        into every target this witness covers; lifespan starts an asyncio
                        background probe_loop task (not APScheduler) that probes+reports all
                        configured targets concurrently each tick, one independent signed
                        observation per target; GET /pubkey (Milestone 5: now also returns
                        "targets", which registry.py uses to sync WitnessRailAssignment),
                        GET /health (health lists targets too)
```

### Data flow / request lifecycle (Milestone 2 — current)

1. On aggregator startup (`main.py` lifespan), schema is created, rails + historical incidents are seeded, and `registry.build_registry` fetches `/pubkey` (now also carrying declared `targets`, Milestone 5) from every `WITNESS_URLS` entry concurrently, each with its own retry/backoff loop; for each witness it registers, it also syncs `WitnessRailAssignment` rows to match those declared targets (`registry._sync_rail_assignments`). **`rails_config.backfill_probe_history` is deliberately no longer called** — it manufactured synthetic `ProbeResult` rows with no `witness_id`, which doesn't fit a model where every row must trace back to a registered witness's verified signature. The function is left in place, just uncalled.
2. Each `witness/` instance probes its own target on its own schedule (independent of the aggregator) and `POST`s a signed observation to `{AGGREGATOR_URL}/observations`.
3. `POST /observations` (`main.py`) does, in order: (a) look up the claimed `witness_id` in the `Witness` registry — reject unknown witnesses outright, never auto-register from the payload; (b) recompute the canonical-JSON SHA-256 hash from the raw observation fields and verify the Ed25519 signature against the *registry's* stored `public_key_hex` — never anything the payload itself claims about its hash or key; (c) reject if the timestamp is more than `OBSERVATION_FRESHNESS_SECONDS` old or more than `FUTURE_TOLERANCE_SECONDS` in the future (replay defense); (d) match `payload.target` against `Rail.probe_target` to find which rail this observation is about, rejecting if nothing matches. Only then is a `ProbeResult` row stored (with `witness_id`, `signature_hex`, `observation_hash_hex`), `Witness.last_seen_at` updated, and `quorum.compute_quorum_snapshot` + `apply_quorum_incident_logic` run for just that rail.
4. `quorum.py` replaces `sla.py`'s old threshold-on-simulated-rate detection entirely. `compute_quorum_snapshot` is pure/read-derived: it counts distinct witnesses whose most recent observation for a rail falls within `WINDOW_SECONDS`, divides by witnesses **assigned to that rail** (`WitnessRailAssignment`, Milestone 5 — not total registered witnesses globally) for **participation**, and — only if participation clears `MIN_PARTICIPATION_FRACTION` — computes what fraction of those reporting witnesses are "unhealthy" (`not reachable or http_status is None or http_status >= 500`) for **agreement** against `AGREEMENT_SUPERMAJORITY_FRACTION`. Status is `insufficient_data` (participation not met — with an explicit `reason`, e.g. `"no witnesses assigned"` when the denominator itself is zero), `degraded` (agreement met), or `operational` (neither) — see quorum.py's design notes below for why these are two separate checks, not one.
5. A periodic `quorum_tick_job` (every `QUORUM_TICK_SECONDS`, via `AsyncIOScheduler`) re-runs the same evaluation for every rail even absent new observations — this is what catches a witness going silent, which the event-driven path in step 3 would never notice on its own (nothing arrives to trigger a re-check).
6. `GET /api/rails` and `/api/rails/{slug}` still compute everything on read (`main.serialize_rail` calls both `sla.rolling_uptime`, still valid for latency/availability display, and `quorum.compute_quorum_snapshot` fresh) — there is still no persisted "current status" field on `Rail`.
7. The old demo control endpoints (`POST /api/demo/trigger-outage/{slug}`, `resolve-outage/{slug}`) still exist and still mutate `ProbeEngine`'s in-memory `RailState`, but nothing reads that state anymore — they're inert until a later milestone reconnects them, deliberately left as dead code rather than removed (see "What's dead" below).
8. Frontend polls the backend via `src/api.js` and renders directly from these REST responses; as of Milestone 4 the rail rows understand the three-state status (and show the Outage Copilot while `degraded`), but the sparkline/simulated-rate display still reflects the old model.
9. (Milestone 4) `POST /api/certificates` rate-limits per IP first, then requires the claimed timestamp to fall inside a quorum-declared incident window (`certificates.find_covering_incident`: `severity == "degraded"`, not historical, not a legacy simulation; an open incident covers `started_at` onward). It assembles a self-contained signed document — quorum snapshot receipt, the incident's log entries with prev_hash+payload+inclusion proofs, the unverified-transaction-ref disclaimer — signs it with the aggregator identity, persists an `EvidenceCertificate` row, and returns `{certificate, signature, aggregator_public_key_hex}`. `POST /api/verify` re-derives validity: signature against the aggregator's OWN key, Merkle leaves rebuilt from content, checkpoint roots against the git-anchored files. Checks that can't be evaluated report `passed: null`, never silently pass or fail.

### What's dead (left in place on purpose, not silently deleted)

- `probe_engine.py` — the `ProbeEngine`/`RailState`/`run_probe_cycle` self-probing code. Still imported by `main.py` only for the inert demo endpoints; `run_probe_cycle` itself is never called.
- `sla.py`'s `detect_and_update_incidents` and `severity_for_rate` — fully superseded by `quorum.py`. `sla.rolling_uptime` is the one thing from this file still in active use.
- `rails_config.backfill_probe_history` — still a valid function, just not called from `lifespan` anymore.

If you're asked to clean up "unused code" in `backend/`, check this list and this file before assuming any of it is safe to delete — it's unused *by current milestone scope*, not orphaned by accident.

### Key invariants to preserve when editing

- The aggregator's trust in a witness's public key comes **only** from `registry.py`'s out-of-band fetch at startup, keyed by `WITNESS_URLS` — never from anything a `POST /observations` payload claims about its own key or hash. `signing.verify_observation_signature` always recomputes the hash from the raw fields itself.
- `POST /observations` must check signature validity **and** timestamp freshness **and** rail-target match, in that order, before writing anything. Skipping the freshness check reopens the replay attack it exists to prevent (see quorum.py design notes).
- `quorum.compute_quorum_snapshot` must never let a low-participation rail fall back to `"operational"` — that's the entire point of the `insufficient_data` state. Silence from witnesses is not evidence of health.
- (Milestone 5) Participation's denominator is `assigned_count` — witnesses assigned to *that specific rail* via `WitnessRailAssignment` — never `total_registered` (every witness globally). A rail with zero assigned witnesses must be `insufficient_data` with `reason: "no witnesses assigned"`, distinct from "witnesses assigned but not currently reporting" (also `insufficient_data`, but with a different `reason` and a nonzero `assigned_count`) — collapsing these loses the ability to tell a config mistake (nobody was ever assigned) apart from an operational one (assigned witnesses went quiet). Don't reintroduce a global-witness-count denominator.
- (Milestone 5) `WitnessRailAssignment` rows are synced declaratively at every aggregator startup from each witness's `/pubkey`-declared `targets`, matched against `Rail.probe_target` by exact string equality — the same rule `POST /observations` uses to route observations. Don't infer assignment from which witnesses happened to report recently; a witness that's down must still count as "assigned."
- Participation and agreement are checked as two independent fractions, in that order (participation gates whether agreement is even evaluated) — collapsing them into one check produces wrong answers in both directions (see quorum.py's module docstring for concrete scenarios).
- `Incident.quorum_snapshot` must be included in `serialize_incident`'s API output, not just stored — it's the "receipt" for why an incident was declared, and a hidden receipt defeats the point. (This was missed once during Milestone 2 development and had to be fixed — don't reintroduce the gap.)
- `ProbeResult` rows written via `POST /observations` always carry `witness_id`, `signature_hex`, `observation_hash_hex` — rows from before Milestone 2 (or any future non-witness path) may have `witness_id IS NULL`; `quorum.py` filters those out explicitly (`ProbeResult.witness_id.isnot(None)`).
- Incident open/resolve logic still keys off `Incident.status != "resolved"` and `Incident.is_historical.is_(False)` — historical incidents (the seeded 12 April 2025 entry) must never be picked up by quorum logic either.
- `rails_config.PROBE_TARGET_OVERRIDES` is still the one sanctioned way to change what URL is probed — but now it must be kept in sync with whatever `PROBE_TARGET` the corresponding witness instances are configured with, since `POST /observations` matches on exact string equality against `Rail.probe_target`.
- (Milestone 3) `entry_hash = sha256(prev_hash + payload)` and the `payload` must be built with `signing.canonical_json_bytes` — the *same* serializer the witnesses/observation-verifier use. `verify_log.py` and `log_chain.compute_entry_hash` must stay byte-for-byte identical, or honest entries "fail" verification. Don't introduce a second serializer.
- (Milestone 3) `verify_log.py` rebuilds each Merkle leaf from CONTENT (`compute_entry_hash(prev_hash, payload)`), **not** from the stored `entry_hash` column — otherwise a payload edit that leaves `entry_hash` untouched slips past the checkpoint check (it was caught this way during development). Checkpoint *creation* uses the freshly-correct stored `entry_hash`, which is fine; only the verifier must recompute from payload.
- (Milestone 3) `log_chain.append_log_entry` holds `_APPEND_LOCK` across read-max-seq → insert → commit. Don't move the commit outside the lock or make the sequence rely on autoincrement — concurrent `POST /observations` would fork the chain.
- (Milestone 3) The genesis `prev_hash` is the fixed `"0"*64`, never NULL/empty — it makes "this is entry #1" a checkable claim. Git anchoring failures (no remote/network/auth) must stay non-fatal (warn + continue), same tolerance as the witness→aggregator POST.
- (Milestone 4) A certificate is ONLY issued for a window where quorum actually declared an incident. Never add a manual-override, admin bypass, or test-mode issuance path — a demo incident goes through the real pipeline. `find_covering_incident` filtering on `severity == "degraded"` + `is_historical == False` + `is_live_simulation == False` is what enforces this; the seeded 12 April 2025 incident must never yield a certificate (it was never quorum-confirmed).
- (Milestone 4) The "confirms infrastructure incident, NOT individual transaction outcome" disclaimer and the `verified: false` marking on the claimed transaction ref live in the signed certificate document itself, not just in docs/UI — removing them from the payload changes what the aggregator is attesting to.
- (Milestone 4) `verify_certificate` checks signatures against the aggregator's OWN key (`identity.public_key_hex()`), never the `aggregator_public_key_hex` the submitted bundle carries — otherwise anyone re-signs a forged document with their own keypair. Its three checks (signature / inclusion proofs / git checkpoint anchor) are reported separately and never collapsed into one boolean; "couldn't evaluate" is `passed: null`, distinct from both pass and fail.
- (Milestone 4) Certificates are signed with the same aggregator identity that signs checkpoints — never introduce a separate certificate-signing key.

### witness/ — design notes

- Each witness has its own Ed25519 identity, generated once and persisted to `KEY_PATH` (a Docker volume per instance in `docker-compose.yml` — `witness-a-keys`, `witness-b-keys`, `witness-c-keys` — so keys are never shared across instances or images).
- A witness may cover multiple targets (`PROBE_TARGETS`, see "Multi-target witnesses" above), but **each target still gets its own independent observation, hash, and signature** — never a batched payload covering more than one target. There is no `rail_slug` field on the wire; the aggregator routes purely by matching `payload.target` against `Rail.probe_target`, so sending the correct URL per probe is sufficient.
- The signed unit is always the *canonical JSON* of one observation dict (`witness_id, timestamp, target, reachable, http_status, latency_ms, error`), hashed with SHA-256, then that hash is what gets Ed25519-signed — not the observation dict directly. Any code that needs to verify a signature must reproduce the observation dict, canonicalize it the same way (`signing.canonical_json_bytes`), hash it, and verify against that hash — never re-derive canonical form ad hoc elsewhere.
- The signature proves authorship + integrity of a witness's report, not truthfulness of the underlying probe, and not Sybil-resistance across witnesses — that reasoning lives with the aggregator (`backend/`, see below), not here.
- The aggregator endpoint (`POST /observations`) exists now (Milestone 2, `backend/main.py`), but `witness/prober.report_observation` is unchanged — it still treats `httpx.ConnectError`/timeouts as expected-and-logged, not exceptional. Don't add retries, queuing, or circuit breakers here; that's still explicitly deferred, and adding it here vs. in the aggregator are different decisions with different tradeoffs (see below).

### backend/ (aggregator) — design notes

- The registry (`registry.py`) is the *only* source of truth for which public key belongs to which `witness_id`. It's built once, at aggregator startup, from `WITNESS_URLS` — concurrently, each URL with its own retry/backoff loop, so one slow-to-start witness container doesn't block the others from registering. **Known limitation:** if a witness is still down after the retry budget is exhausted (~10 attempts, capped exponential backoff, well under 2 minutes total by default), it will never be registered until the aggregator process itself restarts — there's no background retry for witnesses that missed the startup window. This was deliberately not built out further to keep Milestone 2 scoped; flag it rather than silently add continuous re-registration polling without being asked.
- `POST /observations` never trusts anything the payload claims about its own key or hash — see `signing.verify_observation_signature`'s docstring. This is the load-bearing security property of the whole milestone: without it, anyone could submit a `witness_id` they don't own alongside a keypair they do, and the signature would "verify" against a key they control while claiming to speak for someone else.
- The freshness check (`OBSERVATION_FRESHNESS_SECONDS`, default 30s) exists specifically to bound replay: a captured, validly-signed "all healthy" observation has a ~30s shelf life before it becomes unusable to mask a real, ongoing outage. This is separate from `WINDOW_SECONDS` (default 60s), which is quorum.py's *aggregation* window for deciding current status — don't conflate the two constants when tuning either.
- Quorum consensus deliberately runs from two independent checks, not one — `MIN_PARTICIPATION_FRACTION` (are enough witnesses even talking?) gates whether `AGREEMENT_SUPERMAJORITY_FRACTION` (do the ones talking agree it's broken?) is even evaluated. A single surviving witness reporting "healthy" must never look like consensus (that's the participation check), and a single dissenting witness among many must never look like an outage (that's the agreement check) — see `quorum.py`'s module docstring for the exact failure scenarios this prevents.
- Rail-routing in `POST /observations` matches `payload.target` against `Rail.probe_target` by exact string equality. This is fragile by construction (see the invariant above about keeping `PROBE_TARGET_OVERRIDES` and witness `PROBE_TARGETS` env vars in sync) but not a trust/security issue — a witness can't forge *authorship* this way, it can at most misroute its own genuine observations if misconfigured. A future milestone could replace this with an explicit witness-to-rail assignment rather than inferring it from a freeform URL string. A mismatch was investigated (post-Milestone-4) and confirmed to already fail loud — 400 + WARNING log, never a silent 200-and-drop — and is now also durably visible via `GET /api/diagnostics/unmatched-targets` (`unmatched_targets.py`). Don't reintroduce a silent-drop path here, and don't remove the visibility call when touching this block.
- (Superseded by Milestone 5 — kept for history) Before per-rail assignment, a rail covered by fewer registered witnesses than `MIN_PARTICIPATION_FRACTION` required against the *global* registry (e.g., a rail only one witness targeted, against a 3-witness global registry) would sit at `insufficient_data` permanently, no matter how healthy that one witness's reports were. That's what `WitnessRailAssignment` fixes: participation is now measured against witnesses *assigned to that rail specifically*, so a rail with one assigned witness reaches quorum on that witness's report alone. The one case that's still permanently `insufficient_data` by design is a rail with **zero** assigned witnesses (`reason: "no witnesses assigned"`) — that's a config gap to fix by assigning a witness, not something to special-case into a looser check or a silent "operational".
