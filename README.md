# DPI Sentinel

An independent, public-interest status page for India's digital public
infrastructure (DPI) — UPI and DigiLocker today. Built for SIPS 2026 (CSITM,
IIM Bangalore) under the "Digital Trust & Infrastructure" theme.

## Why this exists

UPI alone clears over 22 billion transactions a month. When it degrades — as
it did for roughly five hours on 12 April 2025 — people find out from
social media, not a dashboard. NPCI's own uptime reporting updates monthly.
There's no independent, real-time monitor today for the rails hundreds of
millions of people depend on every day, and no way for someone hit by an
outage to get anything more than "trust us" after the fact.

DPI Sentinel is a working prototype of what that monitor could look like —
and, further, what it could give back to someone who got burned by an
outage: not just a status dot, but a signed, independently-verifiable
document they can hand to their bank or a regulator.

![The DPI Sentinel status page: a masthead reading "all rails operational", a
table of monitored rails, and the incident log below
it](docs/screenshots/status-page.png)

The status page itself. Three rails are monitored in this build: UPI and
DigiLocker probe real public endpoints, and **Demo Rail** is a self-hosted
target that exists so an outage can be triggered on demand during a demo
without anyone pretending NPCI just went down. Statuses are written in plain
language ("Working normally", not "200 OK"), because the audience for this
page is the person whose payment just failed, not an SRE. Every screenshot
below is from a real run of the stack, not a mockup.

## What's measured, and what isn't

This is the most important thing to understand about the page, not a
footnote:

- **Availability and latency are measured live.** Multiple independent
  "witness" services each run their own real HTTP/TLS probes against each
  rail's public-facing surface, sign what they saw with their own private
  key, and report it in. A rail's status — operational, degraded, or
  insufficient data — is a live consensus across whichever witnesses
  reported recently, not one server's opinion of itself.
- **Transaction-level success rate is not published at all.** No outside
  party — including this project — has bank or PSP-side visibility into real
  transaction settlement. Rather than show an estimate dressed as a
  measurement, the page shows nothing there and says why. What it does show
  per rail is availability, latency, and how many signed observations that
  figure rests on.
- **The historical incident in the log is real**, reconstructed from public
  reporting (NPCI statements, press coverage), with a source note attached.
  We couldn't independently verify the exact figures against a primary NPCI
  dataset, and we say so rather than round the corner.

An accountability tool that hides its own limitations isn't one worth
trusting — so the line between what is measured and what cannot be is drawn
explicitly, everywhere, on purpose.

![Expanded UPI row showing a methodology box that states the probe measures
NPCI's public web surface, not live transaction
settlement](docs/screenshots/rail-methodology.png)

Expanding any rail shows exactly what is being measured and what isn't. This
methodology box is where the project's central caveat lives: the probe
measures the availability of NPCI's *public-facing infrastructure*, and it
says so, rather than letting a green dot imply that money is moving. The
probe target is printed too, so the claim is checkable rather than asserted.

## Walkthrough: an outage, end to end

The screenshots below follow one real incident from detection to a verified
certificate. It was produced by stopping the Demo Rail's target container,
letting the witnesses notice on their own, and then using the page exactly as
a member of the public would.

### 1. Witnesses agree something is wrong, and the page says what to do

![Status page with Demo Rail marked "Confirmed problem right now" and a red
Outage Copilot panel giving three pieces of
advice](docs/screenshots/degraded-page.png)

Nothing was manually switched to "down". The witnesses independently failed to
reach the target, the aggregator's quorum logic crossed its agreement
threshold, and the masthead flipped to **active disruption, confirmed by
witness quorum**.

The **Outage Copilot** appears only while a rail is actually degraded, and it
addresses the three things that actually go wrong for people in that window:
retrying a payment during an outage can leave you with duplicate pending
debits; the UPI app's spinner is not the source of truth for whether money
left your account; and outage windows are prime time for fake "UPI helpline"
callers. That last one is a fraud-prevention message, not a status message,
and it is the reason the panel exists at all.

### 2. The incident log shows its working, not just its verdict

![An incident entry expanded to technical details, showing which named
witnesses marked the rail unhealthy and the exact agreement percentages at
each step](docs/screenshots/incident-receipt.png)

This is the receipt for the call. Expanding "technical details" on any
incident shows *which named witnesses* reported it unhealthy, the exact
agreement percentage at each step, and the threshold that was crossed, at
detection, at every change while it was open, and at resolution. Note the
third line: agreement *dropped* to 67% and the incident stayed open. It
resolved only when the numbers said it had. A status page that just says
"resolved" is asking to be trusted; this one is showing you why.

### 3. An affected user requests an Evidence Certificate

![An issued certificate showing rail, incident window, witness quorum, the
user's self-reported transaction reference marked unverified, and a
disclaimer](docs/screenshots/certificate-issued.png)

A certificate is **only** issued for a window where the consensus process
actually declared an incident. There is deliberately no admin override and no
test-mode issuance path, so a certificate cannot be minted for an outage that
was never detected.

Two lines carry the honesty of the whole feature. The transaction reference
the user typed is stamped **self-reported, unverified**, because DPI Sentinel
has no bank or PSP-side visibility and will not imply otherwise. And the
disclaimer, which is part of the *signed* document rather than page
decoration, states that the certificate confirms an infrastructure incident
and cannot confirm the outcome of any individual transaction.

### 4. It prints as a single page you can hand across a counter

![A one-page printable Evidence Certificate with letterhead, ruled field
rows, disclaimer, and a large centred QR
code](docs/screenshots/certificate-sheet.png)

A JSON file is useless at a bank branch, so the same certificate renders as a
formal one-page document. It is always exactly one page: the technical
appendix is screen-only, so a long, messy incident with twenty log entries
prints the same single sheet as a quiet one.

The QR code carries a pointer to the verifier plus a fingerprint of the exact
signed bytes, not the document itself, so a printout that was altered after
issue will not match the record the code resolves to. The stamp reads
"Certificate issued", never "valid", because this sheet was fetched, not
verified. Claiming a verdict on paper that nobody checked is exactly the
unearned reassurance this project argues against.

### 5. Anyone can re-check it, and each check is reported separately

![The verify page showing a VALID stamp and three separate PASS checks:
aggregator signature, Merkle inclusion proofs, and external checkpoint
anchor](docs/screenshots/verify-result.png)

Paste the JSON, upload the file, or scan the printed QR with a phone or
webcam. The verifier re-derives everything from scratch and reports **three
separate checks**, never one green tick:

1. **Aggregator signature** — was this exact document, byte for byte, signed
   by the aggregator's key? (Checked against the aggregator's *own* key, never
   the one the submitted bundle carries, or anyone could re-sign a forgery.)
2. **Merkle inclusion proofs** — does each cited log entry, rebuilt from its
   own content, hash up through its proof path to the cited checkpoint root?
3. **External checkpoint anchor** — do those roots match the copies committed
   to a separate git repository, not just the aggregator's own database?

A check that cannot be evaluated reports as such, distinct from both pass and
fail. The third check is the one that matters most: it is what stops the
operator of this system from quietly rewriting its own history.

## Under the hood, briefly

- Independent **witness services** probe the rails and sign what they see —
  no single process's word is trusted.
- The **aggregator** verifies those signatures and only calls a rail's
  status once a real quorum of witnesses agrees, never from one report.
- Every verified observation and incident is written into a **tamper-evident,
  hash-chained log**, periodically sealed into a git repository kept separate
  from the application database (and, in production, pushed to an external
  remote) — so no one, including the operator, can quietly rewrite past
  history without it being mathematically detectable.
- **Evidence Certificates** are built directly from that log and consensus
  record, then signed — making them self-contained and independently
  checkable rather than something you have to take on faith.

None of that is worth much if you have to read the source to believe it, so
the page surfaces the machinery itself:

![The "How this page knows" panel: a table of four witnesses with their
public keys and assigned rails, and chain statistics showing entry count,
sealed checkpoints, latest Merkle root and git
anchor](docs/screenshots/verification-ledger.png)

The witness roster lists every key the aggregator will accept, where each key
came from, and which rails each witness covers. Coverage is per-rail and
declared by the witness itself: `witness-d` here covers only DigiLocker, and
the quorum math counts it against DigiLocker's participation alone. A witness
that goes quiet stays listed and stays counted, so silence lowers confidence
rather than being mistaken for an all-clear.

Below it are the figures on record: chain length, sealed checkpoints, the
latest Merkle root, and the external git anchor. This panel deliberately
never says "verified" — it reports what is recorded. Verifying means walking
every entry and recomputing every hash, which is `verify_log.py`'s job and the
certificate verifier's, not something a page render can claim to have done.

If you're digging into how any of this is implemented, `CLAUDE.md` has the
full engineering detail, module-by-module.

## Try it yourself

The backend half (aggregator + 4 witnesses + the Demo Rail target) runs via
Docker Compose:

```bash
docker compose up
```

The frontend is a Vite dev server, started separately:

```bash
cd frontend && npm install && npm run dev
```

Then visit `http://localhost:5173` for the status page (backend API on
`http://localhost:8420`). Rail statuses populate within moments as the
witnesses start reporting in.

### Reproducing the walkthrough above

Every screenshot in this README came from these two commands. Stopping the
Demo Rail's target is a real network failure, so the witnesses discover it the
same way they would a real outage, with no test hooks involved:

```bash
docker compose stop demo-target    # Demo Rail goes degraded within ~15s
docker compose start demo-target   # and resolves on its own once it recovers
```

While it is degraded, the Outage Copilot appears on the Demo Rail row and a
certificate can be requested for that window. The form defaults to the moment
it opened, which is inside the window; entering a time from before the
incident started is correctly refused, since a certificate only ever covers a
window quorum actually declared.

Running pieces separately, or want to point it at your own probe targets?
See the "Running it" section in `CLAUDE.md` for backend-only setup,
environment variables, and pre-demo network checks.

## What's next

- Real settlement-adjacent signals via partnership with a PSP sandbox or
  bank API program — the only honest way to say anything about transaction
  outcomes, which is why the page says nothing about them today.
- More rails: ONDC, ABDM/ABHA, Aadhaar/AEPS.
- Certificate revocation, for cases where an incident is later reclassified.
- Wider witness coverage so more rails clear quorum with room to spare.
- A public "DPI Uptime Leaderboard" — transparent, methodology-first,
  applying equal scrutiny across all monitored rails.
- Alerting (webhook/SMS) for civic tech orgs, journalists, and researchers.
- Open data export of historical incident timelines for public-interest
  research.
