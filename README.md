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

## What's real vs. simulated

This is the most important thing to understand about the page, not a
footnote:

- **Availability and latency are measured live.** Multiple independent
  "witness" services each run their own real HTTP/TLS probes against each
  rail's public-facing surface, sign what they saw with their own private
  key, and report it in. A rail's status — operational, degraded, or
  insufficient data — is a live consensus across whichever witnesses
  reported recently, not one server's opinion of itself.
- **Transaction-level success rate is a calibrated simulation, and it's
  labeled as one everywhere it appears.** No outside party — including this
  project — has bank or PSP-side visibility into real transaction
  settlement. That number is simulated, calibrated against publicly
  documented incidents, and never presented as a live measurement.
- **The historical incident in the log is real**, reconstructed from public
  reporting (NPCI statements, press coverage), with a source note attached.
  We couldn't independently verify the exact figures against a primary NPCI
  dataset, and we say so rather than round the corner.

An accountability tool that hides its own limitations isn't one worth
trusting — so the line between "measured" and "simulated" is drawn
explicitly, everywhere, on purpose.

## What you can do on the page

- **Watch rail status in real time.** Each rail (UPI, DigiLocker) shows a
  live status derived from consensus across independent witnesses, plus a
  recent-activity sparkline.
- **See the incident timeline**, including how an incident was actually
  declared — which witnesses reported, which disagreed, and the exact
  numbers behind the call, not just a status change with no explanation.
- **Get guided during an outage.** While a rail shows as degraded, an
  **Outage Copilot** panel appears: don't retry the payment immediately,
  check your own bank app/SMS for the real debit status first, and a
  heads-up that outage windows are prime time for fake "helpline" scam
  calls.
- **Request an Evidence Certificate.** If you were affected during a
  confirmed incident window, you can request a signed document to use as
  supporting evidence in a bank dispute or RBI ombudsman complaint. It's
  only ever issued for windows where the system's own consensus process
  actually declared an incident — there's no manual override, so a
  certificate can't be minted for something that wasn't really detected.
  The document is explicit that it confirms an *infrastructure* incident
  occurred, never that any individual transaction failed — your
  transaction reference is included as self-reported, and clearly marked
  as unverified.
- **Independently verify any certificate**, on the `#/verify` page: paste
  or upload one, and the page re-derives its validity from scratch —
  checking the signature, the underlying log evidence, and its anchor in a
  public git history — reporting each check separately rather than a
  single pass/fail.

## Under the hood, briefly

- Independent **witness services** probe the rails and sign what they see —
  no single process's word is trusted.
- The **aggregator** verifies those signatures and only calls a rail's
  status once a real quorum of witnesses agrees, never from one report.
- Every verified observation and incident is written into a **tamper-evident,
  hash-chained log**, periodically sealed and published to an external git
  history — so no one, including the operator, can quietly rewrite past
  history without it being mathematically detectable.
- **Evidence Certificates** are built directly from that log and consensus
  record, then signed — making them self-contained and independently
  checkable rather than something you have to take on faith.

If you're digging into how any of this is implemented, `CLAUDE.md` has the
full engineering detail, module-by-module.

## Try it yourself

Full stack (aggregator + 3 witnesses), via Docker Compose:

```bash
docker compose up
```

Then visit `http://localhost:5173` for the status page frontend (backend
API on `http://localhost:8420`). You should see rail statuses populate
within moments as the witnesses start reporting in.

Running pieces separately, or want to point it at your own probe targets?
See the "Running it" section in `CLAUDE.md` for backend-only setup,
environment variables, and pre-demo network checks.

## What's next

- Real settlement-adjacent signals via partnership with a PSP sandbox or
  bank API program, to shrink what the simulation layer has to cover.
- More rails: ONDC, ABDM/ABHA, Aadhaar/AEPS.
- Certificate revocation, for cases where an incident is later reclassified.
- Wider witness coverage so more rails clear quorum with room to spare.
- A public "DPI Uptime Leaderboard" — transparent, methodology-first,
  applying equal scrutiny across all monitored rails.
- Alerting (webhook/SMS) for civic tech orgs, journalists, and researchers.
- Open data export of historical incident timelines for public-interest
  research.
