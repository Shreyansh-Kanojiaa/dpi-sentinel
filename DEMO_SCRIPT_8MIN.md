# DPI Sentinel — 8-minute demo script

Local working document (gitignored). Derived from `DEMO_SCRIPT_5MIN.md` for the
longer slot. Timings assume the stack has been running for at least two
minutes before you start. Spoken content totals ~7:00 — the remaining ~1:00 is
slack for waits (witness detection, checkpoint sealing), not padding to fill.
If you're short on time, this is built to compress back toward the 5-minute
cut cleanly: drop the DigiLocker-coverage beat and the extra Q&A lead-in first.

---

## Before you walk up

```bash
docker compose up -d                 # start ≥2 min early: the chain needs entries
cd frontend && npm run dev
cd backend && python verify_targets.py   # on the VENUE network, not at home
```

Checklist:

- [ ] `demo-target` running, Demo Rail reading **Working normally**
- [ ] UPI + DigiLocker not stuck on "Can't confirm" (if they are, see Contingencies)
- [ ] "How this page knows" shows a nonzero **sealed checkpoints** count and a git anchor
- [ ] Browser: tab 1 = status page, tab 2 = `#/verify` already open
- [ ] Terminal visible on screen, sitting in the repo, font large
- [ ] Zoom the page so the rail rows and the masthead stamp are readable from the back

One rule for the whole eight minutes: **never say a number the page isn't
showing.** The entire pitch is that this thing doesn't overclaim.

---

## 0:00 – 0:55 · The problem

> UPI clears over 22 billion transactions a month. When it broke for about five
> hours on 12 April 2025, how did people find out? Twitter. NPCI's own uptime
> reporting updates monthly.
>
> So there are two gaps. Nobody outside the operator can tell you if a rail is
> down right now — and if your payment failed during an outage, you have
> nothing to show your bank except your word. You just look like someone
> making an excuse.

*(Don't touch the laptop yet. Let them sit with the second gap — that's the one
nobody else in the room is solving.)*

## 0:55 – 1:45 · What this is

> DPI Sentinel is an independent monitor for those rails. Four separate witness
> services each run their own probes, sign what they saw with their own key, and
> report in. A rail's status is consensus across them — never one server's
> opinion, including ours.

Point at the masthead: **All rails operational**, and the coverage line under a
rail — read whatever it actually says, e.g. *"3/3 assigned witnesses reporting."*

> That line matters more than the green dot. If witnesses go quiet, this page
> does not say "fine" — it says it can't tell. Silence is never treated as
> health.

Now point at DigiLocker's coverage line specifically, next to UPI's:

> Notice these two rails don't show the same count. A fourth witness is
> assigned to DigiLocker only — participation is measured per rail, against
> exactly the witnesses assigned to *that* rail. So a witness dedicated to one
> rail never dilutes another's math, and losing it wouldn't touch UPI's number
> at all.

## 1:45 – 2:30 · The page, and the honesty

Click a rail row open. Read the methodology box aloud — the actual line on the
screen:

> This measures the availability of NPCI's public-facing infrastructure, *not*
> live transaction settlement.

> We publish no transaction success-rate number at all. Nobody outside a bank or
> PSP can see settlement, so any figure we invented would just be an estimate
> wearing the clothes of a measurement. Availability, latency, and how many
> signed observations that rests on — that's what we can honestly show, so
> that's all we show.

Point out the **Demo Rail** and its "test target" tag:

> This one is ours — an nginx container in this deployment. It exists so I can
> break something in front of you without pretending NPCI just went down.

## 2:30 – 3:35 · Break it, for real

```bash
docker compose stop demo-target
```

> No test hook, no admin switch. I just killed an HTTP server. The witnesses
> don't know I did it — they'll find out the same way they'd find out about a
> real outage.

Talk while you wait (this can run 15–30s depending on witness timing — don't rush it):

> Each witness is probing on its own schedule. When enough of them independently
> fail to reach it, and enough of those agree, the aggregator opens an incident.
> Two separate checks: are enough witnesses talking, and do the ones talking
> agree. One witness having a bad network moment can't declare an outage — it
> takes independent agreement, not one report.

Row flips to **Confirmed problem right now**, masthead to **Active disruption ·
confirmed by witness quorum**.

## 3:35 – 4:25 · The receipt, and the copilot

Scroll to the incident log, hit **Technical details**:

> Which named witnesses said it was down, the exact agreement percentage, the
> threshold it crossed. A status page that just says "resolved" is asking to be
> trusted. This one shows its working.

Point at the red panel that appeared on the rail:

> And this is the part I care about most. Don't retry — retries during an outage
> leave you with duplicate pending debits. Check your bank's own app, not the
> UPI spinner. And: outage windows are prime time for fake "UPI helpline" calls.
> Nobody legitimate will ring you to reverse a stuck transaction. That's a
> fraud warning, not a status update, and it's why this panel exists.

## 4:25 – 5:35 · The certificate

Click **Request Evidence Certificate** → the time is pre-filled → optionally type
a transaction ref → **Issue certificate**.

> A certificate is only ever issued for a window where the consensus process
> actually declared an incident. There's no admin override and no test mode —
> I couldn't mint one for an outage that didn't happen if I wanted to.

Point at the two honest lines on the issued document:

> Your transaction reference is stamped self-reported and unverified, because we
> have no bank-side visibility and won't imply otherwise. And the disclaimer —
> which is inside the signed document, not page decoration — says this confirms
> an infrastructure incident, and cannot confirm what happened to any one
> payment.

Open **printable copy**:

> A JSON file is useless at a bank counter. Same certificate, one page, QR code.
> The stamp says "issued", never "valid" — this sheet was fetched, not verified.

## 5:35 – 6:20 · Anyone can check it

Switch to the `#/verify` tab, paste or upload the bundle.

> Three separate checks, never one green tick. Was this exact document signed by
> the aggregator's key — checked against *our* key, not the one the file carries,
> or anyone could re-sign a forgery. Does each cited log entry hash up through
> its proof to a sealed checkpoint. And do those roots match the copies
> committed to a git repository outside the application database.
>
> That third one is the important one. It's what stops us — the operators of
> this system — from quietly rewriting our own history.

## 6:20 – 6:55 · Close

```bash
docker compose start demo-target
```

> It'll resolve on its own, because the witnesses will see it come back.
>
> Real probes, real signatures, real consensus, a record that can't be edited
> after the fact, and a document a citizen can actually hand to someone. Built by
> an outsider, with no access to NPCI. That's the point: accountability that
> doesn't require permission.

## 6:55 – 8:00 · Buffer / Q&A lead-in

This slack exists on purpose — the witness-detection and checkpoint-sealing
waits above don't run on a fixed clock. If everything landed early, don't
rush to fill it: pause and open the floor.

> That's the demo. Happy to go deeper on any piece — the consensus math, the
> hash chain, or what's still missing before this could run for real.

---

## Contingencies

**A real rail sits on "Can't confirm status right now."** Don't hide it — use it:
> That's the venue network, and notice what the page does about it: it says it
> can't tell, rather than guessing. That's the whole design.

**Nothing goes degraded after ~30s.** Check `docker compose ps` — demo-target
actually stopped? If witnesses died too, quorum will read `insufficient_data`
instead of `degraded`; say so and restart them (`docker compose up -d`).

**Certificate refused as outside the window.** The claimed time is before the
incident opened. Re-open the form (it re-fills with the current time) and retry.

**Certificate shows "0 of 1 entries carry proofs."** The entry isn't sealed yet
(≤30s). Say it out loud — *"the proof isn't sealed yet, and it says so rather
than pretending"* — wait, then re-issue. This is a feature, not a stumble.

**Frontend can't reach the backend.** Red banner names the port. `docker compose
logs aggregator | tail -20`. The aggregator refuses to serve until every witness
registers, which takes ~20–30s if one is down.

**Projector/network dies entirely.** The README walkthrough has all eight
screenshots from a real run, in order. Narrate from those.

**Running long.** Cut the DigiLocker-coverage beat (1:45's second half) and
compress "Break it, for real"'s wait-time talking to one sentence — that
recovers ~45s without losing any of the certificate/verify story, which is
the actual payoff.

---

## Q&A, one line each

**"How is this different from Downdetector?"** They aggregate user complaints —
sentiment. We run signed probes and publish a verifiable record. And they can't
give you a document to take to your bank.

**"Your witnesses all run on one machine."** Correct, today. The consensus code
doesn't care where they run — distributing them is a deployment change, not a
redesign. In production they'd sit in different networks and cities.

**"So you can't actually see whether payments fail."** Right, and we say so on
the page. Nobody outside a bank or PSP can. We measure what's honestly
measurable from outside and publish nothing where we'd have to guess.

**"What stops *you* from faking an incident?"** Every observation is signed by a
witness key I don't hold, and every record is hash-chained and sealed under a
Merkle root committed to a repo outside the application database. If I edited
history, the verifier would catch it — that's check three.

**"Is the git anchor actually external?"** In this deployment it's a separate
repository in its own volume, not a remote. Pushing to a public remote is a
config line and the push path is already implemented. I'd rather tell you that
than let a slide imply more.

**"Does the certificate have legal standing?"** None on its own, and it says so
in the Terms. It's supporting evidence: an independently verifiable record that
the infrastructure was down when someone says it was.

**"What if a witness's key is stolen?"** No revocation path yet — a known gap.
Today the mitigation is quorum: one compromised key can't declare an outage on
its own.

**"Why is one witness only covering DigiLocker?"** You may already have covered
this live in the "What this is" beat — if so, skip straight to: each rail's
participation is measured against the witnesses assigned to *that* rail, so
adding or losing `witness-d` doesn't change UPI's math at all.
