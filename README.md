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
  prober.py             Async HTTP probe of one target + signed POST to the aggregator —
                        called once per configured PROBE_TARGETS entry each cycle
  main.py              FastAPI app: background probe loop + /pubkey, /health
```

### Multi-witness architecture (Milestones 1 + 2)

The whole point of this design is that no single process's word is trusted.
Independent `witness/` instances each probe one or more configured targets
and sign what they saw with their own private key — one independent
signature per target, never a combined/batched signature over multiple
targets (Milestone 1). The `backend/` aggregator
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

### Multi-target witnesses (fix, post-Milestone-4)

`quorum.py`'s participation math is `reporting_witnesses_for_this_rail /
total_registered_witnesses` — a **global** denominator. That's correct math,
but it has a sharp edge: a rail watched by only a strict subset of the
registered witnesses can never clear `MIN_PARTICIPATION_FRACTION`, no
matter how healthy that subset's reports are. DigiLocker hit exactly this:
zero witnesses ever probed it, so its participation was permanently `0/3`
and it sat at `insufficient_data` forever — not because anything was wrong,
but because nothing was measuring it at all.

The fix is upstream of quorum.py, not inside it: each `witness/` instance
now probes **every** configured target, not just one. `PROBE_TARGETS` (env
var, replacing the old singular `PROBE_TARGET`) takes a comma-separated
`label:url` list, e.g.:
```
PROBE_TARGETS="upi:https://www.npci.org.in,digilocker:https://www.digilocker.gov.in"
```
Each cycle, a witness probes all its configured targets concurrently (same
`PROBE_INTERVAL_SECONDS` tick for the whole batch, not staggered per
target), and reports each as its **own independently signed observation** —
one HTTP probe, one signature, one `POST /observations` per target, using
the exact same single-target probe/sign/report code path as before, just
invoked once per target. Nothing about the signing scheme changed, and
nothing on the aggregator side changed either: rail routing already worked
by matching `payload.target` against `Rail.probe_target` by exact string
equality, so a witness sending the right URL per probe is all that's needed
— there's no separate `rail_slug` field on the wire, and none was added.
`label` in `PROBE_TARGETS` is purely a human-readable tag for that
witness's own logs.

`docker-compose.yml` now gives all three witnesses both targets, which is
what gives DigiLocker a non-zero participation denominator input for the
first time. Startup fails loudly (raises `ValueError`, container won't
come up) if `PROBE_TARGETS` is missing or malformed — a witness silently
probing nothing would only surface later as an unexplained quorum gap.

**This is a deliberate, documented simplification**, not the long-term
design: every witness covering every rail sidesteps the global-denominator
issue without touching `quorum.py`'s math (which is correct and untouched
by this fix). The correct long-term fix is an explicit witness-to-rail
assignment, so participation is computed against the count of witnesses
*assigned to that rail*, not the total registry — deferred post-hackathon;
see the comment above `PROBE_TARGETS` in `witness/main.py`.

**Watch item, not yet root-caused:** this fix's done-check showed all three
witnesses logging a `timeout` on their first probe tick after a restart,
then clean from the second tick on — plausibly container-network/DNS
cold-start noise, but "multiple witnesses failing in lockstep" was also
exactly the signature seen during the earlier UPI flapping incident (see
"Investigation Log" below — that investigation ruled out a sandbox-network
cause and a flaky-WAF cause, but never reached a confirmed root cause, so
this pattern is still an open diagnostic question, not a closed one). Don't
file this away after one occurrence. If it recurs on future restarts, or
ever happens mid-run rather than only at t=0, it stops being explainable as
startup noise and needs real investigation — see the matching comment in
`witness/prober.py`.

### Routing-mismatch visibility (fix, hardening)

`POST /observations` matches `payload.target` against `Rail.probe_target`
by **exact string equality** — a trailing slash, `http` vs `https`, or a
`PROBE_TARGETS` entry edited slightly differently from `rails_config.py`
all produce a target that matches nothing. This was investigated after the
DigiLocker bug, specifically to rule out a silent-drop failure mode before
trusting exact-string routing long-term.

**Finding:** it was never silent. An unmatched target already gets a `400`
(not a 200-and-drop) and a server-side `WARNING` log with the offending
`witness_id` and `target` — see the routing-match block in `main.py`. What
was missing was durable *visibility*: a log line only surfaces if someone
is already tailing container logs at the right moment. `GET
/api/diagnostics/unmatched-targets` (`backend/unmatched_targets.py`) now
exposes a running count since process start plus the most recent
rejections, so a mismatch shows up somewhere you'd actually look — this
doesn't change what gets rejected or why, only makes an existing rejection
impossible to miss. The `400` response body was also changed to include
the actual offending target string (previously only the generic "does not
match any monitored rail"). The matching logic itself is unchanged —
still exact string equality, a reasonable design given the small number of
rails; this is purely about surfacing a match failure, not making matching
fuzzier.

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

### Outage Copilot + Evidence Certificates (Milestone 4)

The first citizen-facing payoff of all the infrastructure above. While a
rail is `degraded` (quorum-confirmed), the status page shows an **Outage
Copilot** panel: don't retry immediately, check your own bank app/SMS for
the actual debit status, and a warning that outage windows are prime time
for fake "UPI helpline" scam calls. It also lets anyone affected request an
**Evidence Certificate** — a signed JSON document usable as supporting
evidence in a bank dispute or RBI ombudsman complaint.

- **Only for confirmed windows.** `POST /api/certificates` issues a
  certificate only if the claimed timestamp falls inside an incident that
  quorum consensus actually declared. No incident → 404. There is
  deliberately no manual-override or admin path — a demo incident goes
  through the real pipeline (stop witness containers or point their targets
  at something failing).
- **What it does and doesn't claim.** The certificate document itself says,
  in a `disclaimer` field: it confirms an **infrastructure incident**
  occurred in the window; it does **not and cannot** confirm any individual
  transaction's outcome. The requester's transaction reference is stored and
  displayed as self-reported and unverified. This is what keeps the
  certificate from being usable to manufacture false refund claims.
- **One aggregator identity.** Certificates are signed by the same Ed25519
  key that signs Merkle checkpoints (`backend/identity.py`) — one key for
  all of the aggregator's own claims.
- **Self-contained evidence.** Each certificate embeds the incident's quorum
  snapshot (which witnesses reported/agreed), the incident's hash-chain log
  entries with their Merkle inclusion proofs, and the signed checkpoint
  roots they prove into.
- **Independent verification.** `POST /api/verify` (and the `#/verify` page
  in the frontend) re-derives trust from the math with three separately
  reported checks: (1) aggregator signature over the exact document,
  (2) each log entry rebuilt from content and proven up its Merkle path,
  (3) the cited checkpoint roots cross-checked against the **git-anchored**
  copies — not just the live DB, which a dishonest operator could rewrite
  wholesale. Different failure modes are never collapsed into one boolean.
- **Rate limited.** Certificate issuance is limited per IP (default 5 per
  10 minutes, `CERT_RATE_LIMIT_MAX` / `CERT_RATE_LIMIT_WINDOW_SECONDS`),
  because anonymous minting of signed documents is an abuse surface, not
  just a load concern; rejected requests are logged.

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
match a `target` string a witness reports, or the aggregator will reject
those observations as belonging to no known rail — update the matching
entry in each witness's `PROBE_TARGETS` env var to match (see "Multi-target
witnesses" below).

**Frontend:**

```bash
cd frontend
npm install
npm run dev
```

Visit `http://localhost:5173`. The frontend expects the backend at
`http://127.0.0.1:8420` by default (override with `VITE_API_BASE`).
**Known gap (partially closed by Milestone 4):** the rail rows now render
the quorum three-state status (`operational` / `insufficient_data` /
`degraded`) and show the Outage Copilot + certificate flow while degraded,
but the sparkline/simulated-rate display and the "Inject simulated outage"
demo button still reflect the old self-probing model — the button no longer
does anything observable (it flips in-memory state in `probe_engine.py`,
which nothing reads anymore). Reconnecting the demo control to the new
pipeline is a follow-up, not done yet.

## Investigation Log

Engineering history worth keeping, not just a demo note: the chase across
several sessions for why the UPI incident log was rapidly cycling
detect → update → resolve instead of holding a stable incident. Written as
a timeline, with "confirmed," "ruled out," and "still open" kept explicitly
separate — this is a record of what was actually established, not a
polished postmortem. See also the lockstep-timeout watch item in "Multi-
target witnesses" above, which is the same "correlated failure across
witnesses" diagnostic principle surfacing again in a different fix; that
note and this log cross-reference each other rather than repeating the
same content.

**1. The symptom (first observed).** The incident log for UPI showed rapid
detect → update → resolve cycling — multiple full cycles within a ~2
minute window, with `AGREEMENT_SUPERMAJORITY_FRACTION`-relevant agreement
percentages swinging between values like 33% / 67% / 100% and back. This
looked like instability, not a real, held incident.

**2. Hypothesis 1 — ruled out.** Initial guess: `PROBE_TARGET` had been
changed on witnesses one at a time during manual testing, so witnesses
were briefly watching different targets and genuinely disagreeing.
**Ruled out** — confirmed all three witnesses' targets were changed
together, not staggered.

**3. Hypothesis 2 — investigated, then reframed.** Next theory: a shared,
restricted outbound network specific to whatever environment was running
the witness containers was causing correlated failures across all three
"independent" witnesses at once. This was supported by direct evidence: a
raw `ProbeResult` query across the flapping window showed all three
witnesses failing with `connect_error` at the exact same moments, in
perfect lockstep, with no partial disagreement anywhere — the signature of
one shared cause, not three independently-arrived-at observations.

This theory was **partially retired**: direct investigation (`hostname`,
`uname -a`, `whoami`/`id`, absence of `/.dockerenv`, `docker info` against
a local daemon) confirmed the containers were running via Docker on the
user's own Fedora machine the whole time, on the user's normal home
network — not inside any separate restricted sandbox. So "restricted
sandbox network" specifically **is ruled out**. The broader diagnostic
pattern it pointed to — correlated, lockstep failure across witnesses
means one shared cause, not real disagreement — is still the right
principle; it just wasn't sandbox-specific. That's the same principle
invoked by the lockstep-timeout watch item under "Multi-target witnesses"
above.

**4. Hypothesis 3 — tested, NPCI's response ruled out as flaky.** Direct,
repeated `curl -v` against `https://www.npci.org.in` from the same real
machine/network, with no app code involved, returned a consistent HTTP
403 every time, from Akamai's edge (`server-timing: cdn-cache`, `ak_`
headers present), with a clean TLS/HTTP2 handshake on every attempt. This
is a deterministic, stable WAF block — not intermittent, not flaky. So
"NPCI's bot-protection behaves inconsistently" **is ruled out** as the
cause of the flapping, even though NPCI blocking real requests at all is
itself a known, permanent, and separately important fact (see the
operational decision below).

**5. Still open — not yet root-caused.** With both the sandbox-network
theory and the flaky-WAF theory ruled out, and NPCI's block confirmed
consistent rather than intermittent, the original flapping pattern
(correlated, in-lockstep, rapidly oscillating) does **not** yet have a
confirmed root cause. Leading candidates, **unconfirmed**:
- differences between witnesses in outgoing request headers/User-Agent
  causing the WAF to treat them inconsistently from each other;
- a timeout threshold close enough to real response latency that some
  requests cross it and others don't;
- a timing/race pattern in how close together consecutive probes fire.

This needs its own direct diagnostic — comparing raw outgoing request
details across witnesses during an actual flapping window — before any
fix is attempted. Nothing here should be read as more resolved than this.

**6. Operational decision (settled, independent of the open root cause
above).** Because NPCI's production endpoint consistently 403s real,
unrecognized-client requests, it cannot be used as the target for the
live Round 2 demo recording — a monitor whose primary target is always
blocked isn't a compelling demo, independent of the flapping question.
**Decision:** use the project's own injected-outage demo control for any
recorded/live demonstration, not the real NPCI endpoint. This is a
demo-recording decision, not a change to what the system actually does
against real targets in normal operation, and doesn't affect the
legitimacy of the monitoring architecture itself.

## What's next (roadmap, for the deck)

- Evidence Certificates + Outage Copilot + citizen verify page are done
  (Milestone 4) — see above; they consume the tamper-evident log (Milestone
  3) and quorum consensus (Milestone 2).
- Reconnect the rest of the frontend to the new pipeline: the rail-status
  row now understands the three-state status (`operational` /
  `insufficient_data` / `degraded`), but the demo controls, sparkline, and
  simulated-rate display still reflect the old self-probing model.
- Certificate revocation (e.g. if an incident is later reclassified).
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
