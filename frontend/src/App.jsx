import React, { useEffect, useState, useCallback } from "react";
import { api } from "./api";
import PulseStrip from "./PulseStrip";

const SEVERITY_LABEL = {
  operational: "Operational",
  minor: "Degraded",
  major: "Major outage",
  critical: "Critical outage",
};

const SEVERITY_COLOR = {
  operational: "var(--green)",
  minor: "var(--amber)",
  major: "var(--rust)",
  critical: "var(--rust)",
};

function StatusDot({ status }) {
  const color = SEVERITY_COLOR[status] || "var(--stone)";
  return (
    <span
      style={{
        display: "inline-block",
        width: 8,
        height: 8,
        borderRadius: "50%",
        background: color,
        marginRight: 8,
        boxShadow: status !== "operational" ? `0 0 0 3px ${color}22` : "none",
      }}
    />
  );
}

function RailRow({ rail, onTrigger, onResolve, expanded, onToggle }) {
  const u = rail.uptime_24h || {};
  return (
    <div className="rail-row" style={{ borderBottom: "1px solid var(--stone-line)", padding: "20px 0" }}>
      <div className="rail-row-main" onClick={onToggle}>
        <div className="rail-chevron">{expanded ? "▾" : "▸"}</div>

        <div className="rail-name">
          <div style={{ fontFamily: "var(--font-display)", fontSize: 19, fontWeight: 600 }}>
            {rail.name}
          </div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--stone)", marginTop: 2 }}>
            {rail.operator}
          </div>
        </div>

        <div className="rail-status">
          <StatusDot status={rail.status} />
          <span style={{ fontSize: 13.5, fontWeight: 500, color: SEVERITY_COLOR[rail.status] || "var(--ink)" }}>
            {SEVERITY_LABEL[rail.status] || rail.status}
          </span>
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
          <span className="rail-stat-label">success rate <em style={{ fontStyle: "normal", opacity: 0.7 }}>(sim.)</em></span>
        </div>

        <div className="rail-pulse">
          <PulseStrip points={u.sparkline || []} color={rail.color} />
        </div>
      </div>

      {expanded && (
        <div className="rail-expanded">
          <div className="rail-expanded-desc">
            <div style={{ fontSize: 13, lineHeight: 1.6, color: "var(--ink-soft)", marginBottom: 12 }}>
              {rail.description}
            </div>
            <div className="methodology-box">
              <strong style={{ color: "var(--ink-soft)" }}>Methodology —</strong> {rail.probe_methodology}
              <br />
              <span style={{ opacity: 0.8 }}>target: {rail.probe_target}</span>
            </div>
          </div>

          <div className="rail-expanded-controls">
            <div style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--stone)", marginBottom: 8 }}>
              Demo controls
            </div>
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
        {incident.is_historical && (
          <div style={{ marginTop: 4, color: "var(--blue)", fontWeight: 600 }}>HISTORICAL</div>
        )}
        {incident.is_live_simulation && (
          <div style={{ marginTop: 4, color: "var(--rust)", fontWeight: 600 }}>LIVE DEMO</div>
        )}
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 6, flexWrap: "wrap" }}>
          <span
            style={{
              fontSize: 10.5,
              fontWeight: 700,
              letterSpacing: "0.04em",
              textTransform: "uppercase",
              color: SEVERITY_COLOR[incident.severity] || "var(--ink)",
              border: `1px solid ${SEVERITY_COLOR[incident.severity] || "var(--stone)"}`,
              padding: "1px 6px",
            }}
          >
            {incident.severity}
          </span>
          <span
            style={{
              fontSize: 10.5,
              color: "var(--stone)",
              fontFamily: "var(--font-mono)",
            }}
          >
            {incident.status}
          </span>
          <span style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 600 }}>
            {incident.title}
          </span>
        </div>

        <div style={{ marginLeft: 2, borderLeft: "2px solid var(--stone-line)", paddingLeft: 14 }}>
          {incident.events.map((e, i) => (
            <div key={i} style={{ marginBottom: 8, fontSize: 12.5, lineHeight: 1.55 }}>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 10.5, color: "var(--stone)", marginRight: 8 }}>
                {new Date(e.timestamp).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}
              </span>
              <strong style={{ fontWeight: 600 }}>{e.label}.</strong> {e.narrative}
            </div>
          ))}
        </div>

        {incident.source_note && (
          <div style={{ marginTop: 8, fontSize: 11, color: "var(--stone)", fontStyle: "italic", lineHeight: 1.5 }}>
            Source note: {incident.source_note}
          </div>
        )}
      </div>
    </div>
  );
}

export default function App() {
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

  const overallStatus = rails.some((r) => r.status !== "operational") ? "issue" : "ok";

  return (
    <div style={{ maxWidth: 980, margin: "0 auto", padding: "48px 28px 96px" }}>
      {/* Masthead */}
      <header style={{ marginBottom: 40 }}>
        <div className="masthead">
          <div>
            <div style={{ fontSize: 11, letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--stone)", marginBottom: 6 }}>
              An independent register · not affiliated with NPCI, MeitY, or UIDAI
            </div>
            <h1 style={{ fontFamily: "var(--font-display)", fontSize: 38, margin: 0, lineHeight: 1.1, fontWeight: 700 }}>
              DPI Sentinel
            </h1>
            <div style={{ fontSize: 14, color: "var(--ink-soft)", marginTop: 6, maxWidth: 520 }}>
              A public ledger of uptime for the digital rails India depends on. UPI alone moves over
              22 billion transactions a month — no independent, real-time monitor exists for it.
            </div>
          </div>
          <div className="masthead-status">
            <div
              style={{
                display: "inline-flex",
                alignItems: "center",
                fontSize: 12.5,
                fontWeight: 600,
                padding: "5px 12px",
                border: `1px solid ${overallStatus === "ok" ? "var(--green)" : "var(--rust)"}`,
                color: overallStatus === "ok" ? "var(--green)" : "var(--rust)",
              }}
            >
              <StatusDot status={overallStatus === "ok" ? "operational" : "critical"} />
              {overallStatus === "ok" ? "All monitored rails operational" : "Active disruption detected"}
            </div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 10.5, color: "var(--stone)", marginTop: 8 }}>
              {lastUpdated ? `updated ${lastUpdated.toLocaleTimeString("en-IN")}` : "connecting…"}
            </div>
          </div>
        </div>
      </header>

      {error && (
        <div style={{ background: "var(--rust-dim)", border: "1px solid var(--rust)", padding: 12, fontSize: 13, marginBottom: 24 }}>
          Could not reach the monitoring backend ({error}). Is the FastAPI server running on port 8420?
        </div>
      )}

      {/* Rail ledger */}
      <section style={{ marginBottom: 56 }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "baseline",
            marginBottom: 4,
          }}
        >
          <h2 style={{ fontFamily: "var(--font-display)", fontSize: 20, fontWeight: 600, margin: 0 }}>
            Monitored rails
          </h2>
          <span style={{ fontSize: 11.5, color: "var(--stone)", fontFamily: "var(--font-mono)" }}>
            click a row to expand · {rails.length} monitored
          </span>
        </div>

        <div style={{ marginTop: 12 }}>
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
      <section style={{ marginBottom: 48 }}>
        <h2 style={{ fontFamily: "var(--font-display)", fontSize: 20, fontWeight: 600, marginBottom: 4 }}>
          Incident log
        </h2>
        <div style={{ fontSize: 12.5, color: "var(--stone)", marginBottom: 16 }}>
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
      <section
        style={{
          background: "var(--paper-raised)",
          border: "1px solid var(--stone-line)",
          padding: "24px 28px",
          marginBottom: 40,
        }}
      >
        <h2 style={{ fontFamily: "var(--font-display)", fontSize: 18, fontWeight: 600, marginTop: 0, marginBottom: 10 }}>
          Why an independent register
        </h2>
        <div style={{ fontSize: 13, lineHeight: 1.65, color: "var(--ink-soft)" }} className="why-grid">
          <p style={{ margin: 0 }}>
            India's digital public infrastructure now clears more transactions in a month than most
            countries see in a year. When it degrades — as UPI did for roughly five hours on 12 April
            2025 — citizens find out from social media, not a dashboard. NPCI's own uptime reporting
            updates monthly; no cross-rail, real-time, independently operated monitor exists today.
          </p>
          <p style={{ margin: 0 }}>
            DPI Sentinel measures what's honestly measurable from outside — public-surface availability
            and latency, in real time — and is explicit about what it can't see: real transaction
            settlement, which lives inside banks and PSPs. We'd rather show a transparent simulation
            than a confident-looking number we can't stand behind.
          </p>
        </div>
      </section>

      <footer style={{ paddingTop: 18, borderTop: "1px solid var(--stone-line)", fontSize: 11.5, color: "var(--stone)", lineHeight: 1.6 }}>
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
