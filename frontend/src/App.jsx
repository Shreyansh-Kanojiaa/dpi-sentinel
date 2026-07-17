import React, { useEffect, useState, useCallback } from "react";
import { api } from "./api";
import PulseStrip from "./PulseStrip";
import OutageCopilot from "./OutageCopilot";
import VerifyPage from "./VerifyPage";

const SEVERITY_LABEL = {
  operational: "Operational",
  // Quorum three-state (Milestone 2) — what the backend actually reports now.
  degraded: "Degraded — witness quorum",
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

function StatusDot({ status }) {
  const color = SEVERITY_COLOR[status] || "var(--stone)";
  return (
    <span
      className={`status-dot${status !== "operational" ? " status-dot--alert" : ""}`}
      style={{ "--dot": color }}
    />
  );
}

function RailRow({ rail, onTrigger, onResolve, expanded, onToggle }) {
  const u = rail.uptime_24h || {};
  return (
    <div className="rail-row">
      <div className="rail-main ledger-grid" onClick={onToggle}>
        <div className="rail-chevron">{expanded ? "▾" : "▸"}</div>

        <div className="rail-cell-name">
          <div className="rail-name-display">{rail.name}</div>
          <div className="rail-operator">{rail.operator}</div>
        </div>

        <div className="rail-status" style={{ color: SEVERITY_COLOR[rail.status] || "var(--ink)" }}>
          <StatusDot status={rail.status} />
          {SEVERITY_LABEL[rail.status] || rail.status}
        </div>

        <div className="rail-stat">
          <span className="rail-stat-value">{u.availability_pct != null ? `${u.availability_pct}%` : "—"}</span>
          <span className="rail-stat-label">availability · 24h</span>
        </div>

        <div className="rail-stat">
          <span className="rail-stat-value">{u.avg_latency_ms != null ? `${u.avg_latency_ms}ms` : "—"}</span>
          <span className="rail-stat-label">avg latency</span>
        </div>

        <div className="rail-stat">
          <span className="rail-stat-value">
            {u.avg_simulated_success_rate != null ? `${(u.avg_simulated_success_rate * 100).toFixed(1)}%` : "—"}
          </span>
          <span className="rail-stat-label">
            success rate <em className="sim-flag" title="Calibrated simulation — not live settlement data">simulated</em>
          </span>
        </div>

        <div className="rail-pulse">
          <PulseStrip points={u.sparkline || []} color={rail.color} />
        </div>
      </div>

      {expanded && (
        <div className="rail-expanded">
          <div className="rail-expanded-desc">
            {rail.description}
            <div className="methodology-box">
              <strong>Methodology —</strong> {rail.probe_methodology}
              <br />
              <span className="target">target: {rail.probe_target}</span>
            </div>
          </div>

          <div className="rail-expanded-controls">
            <div className="eyebrow" style={{ marginBottom: 8 }}>Demo controls</div>
            <button
              onClick={(e) => { e.stopPropagation(); onTrigger(rail.slug); }}
              className="btn-danger"
            >
              Inject simulated outage
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); onResolve(rail.slug); }}
              className="btn-ghost"
            >
              Resolve
            </button>
          </div>
        </div>
      )}

      {/* Milestone 4: citizen guidance + Evidence Certificate request, shown
          only while quorum consensus says this rail is degraded. */}
      {rail.status === "degraded" && <OutageCopilot rail={rail} />}
    </div>
  );
}

function IncidentEntry({ incident }) {
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
              <strong>{e.label}.</strong> {e.narrative}
            </div>
          ))}
        </div>

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

  const handleTrigger = async (slug) => {
    await api.triggerOutage(slug, 0.4);
    setTimeout(refresh, 500);
  };
  const handleResolve = async (slug) => {
    await api.resolveOutage(slug);
    setTimeout(refresh, 500);
  };

  // After all hooks (rules of hooks), branch to the verify page if routed there.
  if (route.startsWith("#/verify")) {
    return <VerifyPage />;
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
      note: "insufficient data — not a confirmed disruption",
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
              22 billion transactions a month — no independent, real-time monitor exists for it.
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

        <div className="ledger-grid ledger-cols">
          <div />
          <div>Rail</div>
          <div>Quorum status</div>
          <div>Avail · 24h</div>
          <div>Latency</div>
          <div>Success (sim.)</div>
          <div className="col-signal">Signal · live probes</div>
        </div>

        <div>
          {rails.map((rail) => (
            <RailRow
              key={rail.slug}
              rail={rail}
              expanded={expandedSlug === rail.slug}
              onToggle={() => setExpandedSlug(expandedSlug === rail.slug ? null : rail.slug)}
              onTrigger={handleTrigger}
              onResolve={handleResolve}
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
            incidents.map((inc) => <IncidentEntry key={inc.id} incident={inc} />)
          )}
        </div>
      </section>

      {/* Why this exists */}
      <section className="why-panel">
        <h2 className="section-title" style={{ fontSize: 18, marginBottom: 12 }}>
          Why an independent register
        </h2>
        <div className="why-grid">
          <p>
            India's digital public infrastructure now clears more transactions in a month than most
            countries see in a year. When it degrades — as UPI did for roughly five hours on 12 April
            2025 — citizens find out from social media, not a dashboard. NPCI's own uptime reporting
            updates monthly; no cross-rail, real-time, independently operated monitor exists today.
          </p>
          <p>
            DPI Sentinel measures what's honestly measurable from outside — public-surface availability
            and latency, in real time — and is explicit about what it can't see: real transaction
            settlement, which lives inside banks and PSPs. We'd rather show a transparent simulation
            than a confident-looking number we can't stand behind.
          </p>
        </div>
      </section>

      <footer className="site-footer">
        <div className="verify-link">
          Holding an Evidence Certificate? <a href="#/verify">Verify it independently →</a>
        </div>
        DPI Sentinel is an independent, non-commercial research project, built for the SIPS 2026 summit.
        Availability and latency figures are measured live via synthetic probes against each rail's
        public-facing surface. Transaction-level success-rate figures are a calibrated simulation layer,
        not live settlement data — no outside party has bank or PSP-side visibility into real transaction
        outcomes. This distinction is stated plainly because an accountability tool that hides its own
        limitations isn't one worth trusting.
      </footer>
    </div>
  );
}
