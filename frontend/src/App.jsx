import React, { useEffect, useState, useCallback } from "react";
import { api } from "./api";
import PulseStrip from "./PulseStrip";
import OutageCopilot from "./OutageCopilot";
import VerifyPage from "./VerifyPage";
import VerificationLedger from "./VerificationLedger";
import CertificatePrint from "./CertificatePrint";

const SEVERITY_LABEL = {
  operational: "Operational",
  // Quorum three-state (Milestone 2) — what the backend actually reports now.
  degraded: "Degraded (witness quorum)",
  insufficient_data: "Insufficient data",
  // Legacy severity grades, still used by historical incident entries.
  minor: "Degraded",
  major: "Major outage",
  critical: "Critical outage",
};

const SEVERITY_COLOR = {
  operational: "var(--green)",
  degraded: "var(--rust)",
  insufficient_data: "var(--amber)",
  minor: "var(--amber)",
  major: "var(--rust)",
  critical: "var(--rust)",
};

const SEVERITY_STAMP = {
  operational: "stamp--green",
  degraded: "stamp--rust",
  insufficient_data: "stamp--amber",
  minor: "stamp--amber",
  major: "stamp--rust",
  critical: "stamp--rust",
};

// Plain-language default read for the three quorum states, shown collapsed to
// the citizen who needs an instant answer rather than a dashboard to interpret.
// HONESTY RULE (mirrors quorum.py's three-state design): these THREE must stay
// clearly distinct. "insufficient_data" must never read as "everything's fine"
// — only as genuine uncertainty ("can't confirm"), which is a different message
// from "operational". The full metrics behind each read stay one click away in
// the expanded row; nothing is removed, only relocated.
const RAIL_PLAIN = {
  operational: {
    headline: "Working normally",
    sub: "Independent witnesses are checking this rail continuously and agree it's up.",
  },
  degraded: {
    headline: "Confirmed problem right now",
    sub: "Multiple independent witnesses agree this rail is failing right now.",
  },
  insufficient_data: {
    headline: "Can't confirm status right now",
    sub: "Too few independent witnesses are reporting to say either way. This is not an all-clear.",
  },
};

// Plain-language default for each incident-timeline event label. The precise
// technical narrative (witness counts, agreement %, thresholds) is UNCHANGED —
// it just moves behind the per-incident "Technical details" toggle instead of
// being the default. Any label without a mapping falls back to its own
// narrative, so a line is never blank.
const EVENT_PLAIN = {
  Detected: "A problem was detected.",
  Updated: "The situation changed while the problem was ongoing.",
  Resolved: "Service is back to normal.",
  Identified: "The cause was identified.",
  Monitoring: "Service was partially recovering, and being watched.",
};

function StatusDot({ status }) {
  const color = SEVERITY_COLOR[status] || "var(--stone)";
  return (
    <span
      className={`status-dot${status !== "operational" ? " status-dot--alert" : ""}`}
      style={{ "--dot": color }}
    />
  );
}

function RailRow({ rail, expanded, onToggle }) {
  const u = rail.uptime_24h || {};
  const plain = RAIL_PLAIN[rail.status];
  return (
    <div className="rail-row">
      <div className="rail-main" onClick={onToggle}>
        <div className="rail-chevron">{expanded ? "▾" : "▸"}</div>

        <div className="rail-cell-name">
          <div className="rail-name-display">
            {rail.name}
            {/* A rail that isn't probing a real public service must say so
                wherever it appears — the status dot alone reads identical to
                a real rail's. */}
            {rail.monitor_mode !== "live" && (
              <span className="stamp stamp--amber rail-demo-tag">test target</span>
            )}
          </div>
          <div className="rail-operator">{rail.operator}</div>
        </div>

        {/* Plain-language default read. Raw metrics live in the expanded view. */}
        <div className="rail-plain">
          <div className="rail-status" style={{ color: SEVERITY_COLOR[rail.status] || "var(--ink)" }}>
            <StatusDot status={rail.status} />
            {plain ? plain.headline : SEVERITY_LABEL[rail.status] || rail.status}
          </div>
          {plain && <div className="rail-plain-sub">{plain.sub}</div>}
          {/* Milestone 5's per-rail coverage, on the row itself. The backend
              already computed it (main.serialize_rail); leaving it only in the
              ledger panel far below meant the thing that changes when a witness
              goes quiet was invisible next to the status it changes. */}
          {rail.witness_coverage && (
            <div className="rail-coverage">{rail.witness_coverage}</div>
          )}
        </div>
      </div>

      {expanded && (
        <div className="rail-expanded">
          <div className="rail-expanded-desc">
            {rail.description}
            <div className="methodology-box">
              <strong>Methodology:</strong> {rail.probe_methodology}
              <br />
              <span className="target">target: {rail.probe_target}</span>
            </div>
          </div>

          {/* All raw metrics, relocated here from the collapsed row — nothing
              removed, just no longer the default read. */}
          <div className="rail-metrics">
            <div className="rail-metrics-grid">
              <div className="rail-stat">
                <span className="rail-stat-value">{u.availability_pct != null ? `${u.availability_pct}%` : "n/a"}</span>
                <span className="rail-stat-label">availability · 24h</span>
              </div>
              <div className="rail-stat">
                <span className="rail-stat-value">{u.avg_latency_ms != null ? `${u.avg_latency_ms}ms` : "n/a"}</span>
                <span className="rail-stat-label">avg latency</span>
              </div>
              <div className="rail-stat">
                <span className="rail-stat-value">{u.sample_count ?? 0}</span>
                <span className="rail-stat-label">signed observations · 24h</span>
              </div>
            </div>
            <div className="rail-metrics-pulse">
              <div className="rail-stat-label" style={{ marginBottom: 6 }}>reachability · last 60 probes</div>
              <PulseStrip points={u.sparkline || []} color={rail.color} />
            </div>
          </div>
        </div>
      )}

      {/* Milestone 4: citizen guidance + Evidence Certificate request, shown
          only while quorum consensus says this rail is degraded. */}
      {rail.status === "degraded" && <OutageCopilot rail={rail} />}
    </div>
  );
}

// Incident log grows without bound over time (every quorum-confirmed
// incident, including short-lived ones from demo/stress testing) — cap the
// default view so the page doesn't require scrolling past a long history to
// reach "why this exists" / the footer. Incidents are already sorted
// most-recent-first by the backend; this only slices that array for display.
const DEFAULT_VISIBLE_INCIDENTS = 5;

function IncidentEntry({ incident }) {
  // Per-incident (not per-event) toggle: an incident reads as one story, and a
  // control on every timeline row would clutter it. One switch flips all of
  // this incident's events between the plain-language default and the exact
  // technical narrative that renders today.
  const [showTech, setShowTech] = useState(false);
  const start = new Date(incident.started_at);
  const dateStr = start.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });

  return (
    <div className="incident-entry">
      <div className="incident-date">
        {dateStr}
        {incident.is_historical && <span className="stamp stamp--blue stamp--tilt">Historical</span>}
        {incident.is_live_simulation && <span className="stamp stamp--rust stamp--tilt">Live demo</span>}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="incident-head">
          <span className={`stamp ${SEVERITY_STAMP[incident.severity] || ""}`}>
            {incident.severity}
          </span>
          <span className="incident-status">{incident.status}</span>
          <span className="incident-title">{incident.title}</span>
        </div>

        <div className="incident-timeline">
          {incident.events.map((e, i) => (
            <div key={i} className="incident-event">
              <time>
                {new Date(e.timestamp).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}
              </time>
              {showTech ? (
                <><strong>{e.label}.</strong> {e.narrative}</>
              ) : (
                EVENT_PLAIN[e.label] || e.narrative
              )}
            </div>
          ))}
        </div>

        <button
          type="button"
          className="incident-tech-toggle"
          onClick={() => setShowTech((v) => !v)}
        >
          {showTech ? "Hide technical details" : "Technical details"}
        </button>

        {incident.source_note && (
          <div className="incident-source">Source note: {incident.source_note}</div>
        )}
      </div>
    </div>
  );
}

export default function App() {
  // Minimal hash "router" — the only second page is the certificate
  // verifier, which deliberately works standalone (no shared state).
  const [route, setRoute] = useState(window.location.hash);
  useEffect(() => {
    const onHash = () => setRoute(window.location.hash);
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const [rails, setRails] = useState([]);
  const [incidents, setIncidents] = useState([]);
  const [expandedSlug, setExpandedSlug] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [error, setError] = useState(null);
  const [showAllIncidents, setShowAllIncidents] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [railsData, incidentsData] = await Promise.all([
        api.getRails(),
        api.getIncidents(),
      ]);
      setRails(railsData);
      setIncidents(incidentsData);
      setLastUpdated(new Date());
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 4000);
    return () => clearInterval(id);
  }, [refresh]);

  // After all hooks (rules of hooks), branch to a sub-page if routed there.
  // Routes: #/verify | #/verify/<id>?f=<fingerprint> | #/certificate/<id>
  // The query string lives AFTER the "#", so window.location.search is empty
  // and it has to be split off the hash itself. URLSearchParams is native;
  // this stays a few lines rather than becoming a router (see CLAUDE.md).
  const [hashPath, hashQuery] = route.replace(/^#\/?/, "").split("?");
  const [page, pageArg] = hashPath.split("/");
  const fingerprint = new URLSearchParams(hashQuery || "").get("f");

  if (page === "certificate" && pageArg) {
    return <CertificatePrint id={pageArg} />;
  }
  if (page === "verify") {
    // Bare "#/verify" gives pageArg === undefined, so the paste/upload page
    // behaves exactly as before.
    return <VerifyPage certificateId={pageArg || null} fingerprint={fingerprint} />;
  }

  // "insufficient_data" means quorum can't confirm health OR a problem — it
  // is not itself a disruption (see quorum.py: silence isn't evidence of
  // health, but it also isn't evidence of an outage). Only "degraded" (or a
  // legacy severity grade, for historical entries) should read as an active
  // disruption; a rail with no witness coverage shouldn't paint the whole
  // masthead red.
  const overallStatus = rails.some((r) => ["degraded", "minor", "major", "critical"].includes(r.status))
    ? "issue"
    : rails.some((r) => r.status === "insufficient_data")
    ? "partial"
    : "ok";

  const OVERALL = {
    ok: { cls: "stamp--green", dot: "operational", label: "All rails operational", note: null },
    partial: {
      cls: "stamp--amber",
      dot: "insufficient_data",
      label: "Limited witness coverage",
      note: "insufficient data, not a confirmed disruption",
    },
    issue: { cls: "stamp--rust", dot: "degraded", label: "Active disruption", note: "confirmed by witness quorum" },
  }[overallStatus];

  return (
    <div className="page">
      {/* Masthead */}
      <header style={{ marginBottom: 44 }}>
        <div className="masthead">
          <div>
            <div className="eyebrow">
              An independent register · not affiliated with NPCI, MeitY, or UIDAI
            </div>
            <h1 className="masthead-title">DPI Sentinel</h1>
            <div className="masthead-sub">
              A public ledger of uptime for the digital rails India depends on. UPI alone moves over
              22 billion transactions a month, yet no independent, real-time monitor exists for it.
            </div>
          </div>
          <div className="masthead-status">
            <span className={`stamp ${OVERALL.cls}`}>
              <StatusDot status={OVERALL.dot} />
              {OVERALL.label}
            </span>
            {OVERALL.note && (
              <div style={{ fontSize: 11, color: "var(--stone)", marginTop: 7 }}>{OVERALL.note}</div>
            )}
            <div className="masthead-updated">
              {lastUpdated ? (
                <>
                  <span className="live-tick" />
                  updated {lastUpdated.toLocaleTimeString("en-IN")}
                </>
              ) : (
                "connecting…"
              )}
            </div>
            <a className="btn-primary masthead-nav" href="#/verify">
              Verify a Certificate →
            </a>
          </div>
        </div>
      </header>

      {error && (
        <div className="error-banner">
          Could not reach the monitoring backend ({error}). Is the FastAPI server running on port 8420?
        </div>
      )}

      {/* Rail ledger */}
      <section style={{ marginBottom: 56 }}>
        <div className="ledger-header">
          <h2 className="section-title">Monitored rails</h2>
          <span className="ledger-note">click a row to expand · {rails.length} monitored</span>
        </div>

        <div className="rail-ledger-head">
          <div className="rail-chevron" />
          <div className="rail-cell-name">Rail</div>
          <div className="rail-plain">Current status</div>
        </div>

        <div>
          {rails.map((rail) => (
            <RailRow
              key={rail.slug}
              rail={rail}
              expanded={expandedSlug === rail.slug}
              onToggle={() => setExpandedSlug(expandedSlug === rail.slug ? null : rail.slug)}
            />
          ))}
        </div>
      </section>

      {/* Incident log */}
      <section style={{ marginBottom: 52 }}>
        <h2 className="section-title" style={{ marginBottom: 4 }}>Incident log</h2>
        <div style={{ fontSize: 12.5, color: "var(--stone)", marginBottom: 14 }}>
          Live-detected incidents, alongside historical incidents reconstructed from public reporting.
        </div>
        <div>
          {incidents.length === 0 ? (
            <div style={{ fontSize: 13, color: "var(--stone)", padding: "20px 0" }}>
              No incidents recorded yet.
            </div>
          ) : (
            (showAllIncidents ? incidents : incidents.slice(0, DEFAULT_VISIBLE_INCIDENTS)).map((inc) => (
              <IncidentEntry key={inc.id} incident={inc} />
            ))
          )}
        </div>

        {!showAllIncidents && incidents.length > DEFAULT_VISIBLE_INCIDENTS && (
          <button
            type="button"
            className="btn-ghost"
            style={{ marginTop: 12 }}
            onClick={() => setShowAllIncidents(true)}
          >
            Show all {incidents.length} incidents
          </button>
        )}
      </section>

      {/* The trust machinery, surfaced. Sits directly under the incident log
          because "why should I believe any of the above" is the next question
          a reader has, and the answer was previously only visible in code. */}
      <VerificationLedger />

      {/* Why this exists */}
      <section className="why-panel">
        <h2 className="section-title" style={{ fontSize: 18, marginBottom: 12 }}>
          Why an independent register
        </h2>
        <div className="why-grid">
          <p>
            India's digital public infrastructure now clears more transactions in a month than most
            countries see in a year. When it degrades, as UPI did for roughly five hours on 12 April
            2025, citizens find out from social media, not a dashboard. NPCI's own uptime reporting
            updates monthly; no cross-rail, real-time, independently operated monitor exists today.
          </p>
          <p>
            DPI Sentinel measures what's honestly measurable from outside: public-surface availability
            and latency, in real time. It is equally explicit about what it cannot see, namely real transaction
            settlement, which lives inside banks and PSPs. Where we can't measure, we publish nothing
            rather than a confident-looking number we can't stand behind.
          </p>
        </div>
      </section>

      {/* Terms. Native <details> so it's on the page without pushing the
          register itself below the fold; no accordion component needed.
          The clauses describe what this system actually does (per-IP rate
          limiting, self-reported transaction refs, what is and isn't measured) —
          boilerplate terms that contradicted the methodology would be worse
          than none. */}
      <section className="terms">
        <details>
          <summary>
            <span className="terms-summary-title">Terms of Use and Disclaimers</span>
            <span className="terms-summary-hint">read before relying on anything here</span>
          </summary>

          <div className="terms-body">
            <p className="terms-updated">Last updated 25 July 2026. Version 1.0.</p>

            <h3>1. What this service is</h3>
            <p>
              DPI Sentinel is an independent, non-commercial research prototype that monitors the
              public-facing availability of selected Indian digital public infrastructure. It is
              provided for public-interest, informational and research purposes only. It is not a
              commercial product, not a certification authority, and not a regulated financial
              service.
            </p>

            <h3>2. No affiliation or endorsement</h3>
            <p>
              DPI Sentinel is not affiliated with, endorsed by, authorised by, or operated on behalf
              of the National Payments Corporation of India (NPCI), the Ministry of Electronics and
              Information Technology (MeitY), the Unique Identification Authority of India (UIDAI),
              the Reserve Bank of India, or any bank or payment service provider. All product names,
              logos and trademarks referenced remain the property of their respective owners and are
              used for identification only.
            </p>

            <h3>3. What is measured, and what is not</h3>
            <p>
              Availability and latency shown on this page are derived from synthetic HTTP and TLS
              probes run by independent witness services against each rail's public-facing surface.
              They describe the reachability of that public surface only. They do not measure, and
              must not be read as measuring, transaction settlement, funds movement, bank-side
              processing, or the outcome of any individual payment or document request.
            </p>
            <p>
              This page deliberately publishes <strong>no</strong> transaction success-rate figure.
              No party outside a bank or payment service provider has settlement-level visibility,
              so any such number produced here would be an estimate wearing the clothes of a
              measurement. Availability, latency and observation counts are what is measured, and
              they are all that is shown.
            </p>

            <h3>4. Accuracy and availability</h3>
            <p>
              This service is provided on an "as is" and "as available" basis, without warranties of
              any kind, whether express or implied, including any warranty of accuracy,
              completeness, fitness for a particular purpose, or uninterrupted availability. Probe
              results can be affected by network conditions, geography, rate limiting, and
              maintenance on either side. A rail shown as operational may still be failing for some
              users, and a rail shown as degraded may be reachable for others.
            </p>
            <p>
              A status of <strong>insufficient data</strong> means the system cannot confirm the
              state of a rail either way. It is never an all-clear.
            </p>

            <h3>5. Evidence Certificates</h3>
            <p>
              An Evidence Certificate attests to one thing only: that this system's witness quorum
              recorded an infrastructure incident on the named rail during the stated time window.
              It does not confirm, and cannot confirm, that any particular transaction failed,
              succeeded, or was affected. Any transaction reference included in a certificate is
              self-reported by the requester, is stored and displayed as unverified, and is not
              checked against any bank or payment network.
            </p>
            <p>
              Certificates are issued only for windows where the consensus process actually declared
              an incident. There is no manual override or administrative issuance path. A
              certificate carries no legal status of its own, and whether any bank, ombudsman,
              regulator or court gives it any weight is entirely at their discretion.
            </p>

            <h3>6. Not professional advice</h3>
            <p>
              Nothing on this page constitutes legal, financial, or regulatory advice. The outage
              guidance shown during an incident is general information, not instructions for your
              specific situation. Always follow your bank's official channels, and treat unsolicited
              calls about an outage as potential fraud regardless of what this page shows.
            </p>

            <h3>7. Data collected</h3>
            <p>
              This prototype does not use accounts, logins, advertising, or third-party analytics. If
              you request an Evidence Certificate, the time you claim and any transaction reference
              you enter are stored as part of the issued document. Requester IP addresses are held
              transiently in memory for rate limiting only, and are not written to the permanent
              record. Do not submit information you are not willing to have stored in a document you
              may then share onward.
            </p>

            <h3>8. Acceptable use</h3>
            <p>
              You may read, share, and independently verify anything published here. You may not
              present certificates or figures from this service as originating from NPCI, MeitY,
              UIDAI, any bank, or any regulator; alter a certificate and present it as genuine; or
              use this service to harass any operator or to imply wrongdoing that the data does not
              support.
            </p>

            <h3>9. Limitation of liability</h3>
            <p>
              To the maximum extent permitted by applicable law, the operators of this project accept
              no liability for any loss or damage arising from reliance on the information presented
              here, including any financial loss, missed transaction, or dispute outcome.
            </p>

            <h3>10. Changes and contact</h3>
            <p>
              These terms may change as the project develops; the version and date above indicate the
              current revision. Questions, corrections, and takedown or accuracy disputes from any
              named operator are welcome and will be acted on: contact{" "}
              <a className="terms-contact" href="mailto:www.shreyanshkanojia@gmail.com">
                www.shreyanshkanojia@gmail.com
              </a>.
              These terms are governed by the laws of India.
            </p>
          </div>
        </details>
      </section>

      <footer className="site-footer">
        <div className="verify-link">
          Holding an Evidence Certificate? <a href="#/verify">Verify it independently →</a>
        </div>
        DPI Sentinel is an independent, non-commercial research project, built for the SIPS 2026 summit.
        Availability and latency figures are measured live via synthetic probes against each rail's
        public-facing surface. No transaction success-rate figure is published here, because no outside
        party has bank or PSP-side visibility into real transaction outcomes and a number we cannot
        measure would only look like one we can. This limit is stated plainly because an accountability
        tool that hides its own limitations isn't one worth trusting.
      </footer>
    </div>
  );
}
