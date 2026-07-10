import React, { useState } from "react";
import { api } from "./api";

// Rendered only while a rail's quorum status is "degraded" (App.jsx gates
// this). Two jobs: tell an affected citizen what to actually do right now,
// and let them walk away with a signed, independently verifiable Evidence
// Certificate for the confirmed incident window.

function toLocalInputValue(d) {
  // datetime-local wants "YYYY-MM-DDTHH:MM" in local time.
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function downloadBundle(bundle) {
  // This file — raw certificate JSON + signature + aggregator public key —
  // is the actual artifact a citizen forwards to their bank or the RBI
  // ombudsman. Anyone can later validate it at /verify without trusting
  // how it travelled.
  const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `dpi-sentinel-certificate-${bundle.certificate.certificate_id}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function CertificateResult({ bundle }) {
  const cert = bundle.certificate;
  const q = cert.witness_quorum_snapshot || {};
  const proven = (cert.log_evidence || []).filter((e) => e.status === "proven").length;
  const total = (cert.log_evidence || []).length;
  const fmt = (iso) => (iso ? new Date(iso + (iso.endsWith("Z") ? "" : "Z")).toLocaleString("en-IN") : null);

  const row = (label, value) => (
    <div style={{ display: "flex", gap: 12, padding: "5px 0", borderBottom: "1px solid var(--stone-line)", fontSize: 12.5 }}>
      <div style={{ width: 170, flexShrink: 0, color: "var(--stone)", fontFamily: "var(--font-mono)", fontSize: 11 }}>{label}</div>
      <div style={{ minWidth: 0, overflowWrap: "anywhere" }}>{value}</div>
    </div>
  );

  return (
    <div style={{ marginTop: 14, border: "1px solid var(--green)", background: "var(--green-dim)", padding: "16px 18px" }}>
      <div style={{ fontFamily: "var(--font-display)", fontSize: 15, fontWeight: 600, marginBottom: 4 }}>
        Evidence Certificate issued
      </div>
      <div style={{ fontSize: 12, color: "var(--ink-soft)", marginBottom: 10 }}>
        Signed by the aggregator's Ed25519 identity. Download the bundle below — that file is what you
        forward to your bank or the ombudsman, and anyone can independently verify it on the{" "}
        <a href="#/verify">verify page</a>.
      </div>

      {row("certificate id", <span style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{cert.certificate_id}</span>)}
      {row("rail", `${cert.rail.name} (${cert.rail.operator})`)}
      {row("incident window", `${fmt(cert.incident_window.started_at)} → ${cert.incident_window.ongoing ? "ongoing" : fmt(cert.incident_window.resolved_at)}`)}
      {row("witness quorum", q.reporting_count != null
        ? `${(q.unhealthy_witness_ids || []).length} of ${q.reporting_count} reporting witnesses (${(q.unhealthy_witness_ids || []).join(", ")}) marked the rail unhealthy`
        : "—")}
      {row("your claimed time", fmt(cert.claimed_timestamp))}
      {row("your transaction ref", cert.claimed_transaction_ref.value
        ? <span>{cert.claimed_transaction_ref.value}{" "}
            <em style={{ color: "var(--rust)", fontStyle: "normal", fontSize: 11 }}>· self-reported, unverified</em></span>
        : <em style={{ color: "var(--stone)" }}>none provided</em>)}
      {row("cryptographic evidence", `${proven} of ${total} incident log entries carry Merkle inclusion proofs to a signed, git-anchored checkpoint${proven < total ? " (the rest await the next checkpoint)" : ""}`)}
      {row("signature", <span style={{ fontFamily: "var(--font-mono)", fontSize: 10.5 }}>{bundle.signature.slice(0, 32)}…</span>)}

      <div style={{ marginTop: 10, fontSize: 11.5, lineHeight: 1.55, color: "var(--ink-soft)", borderLeft: "3px solid var(--amber)", paddingLeft: 10 }}>
        {cert.disclaimer}
      </div>

      <button className="btn-danger" style={{ marginTop: 12 }} onClick={() => downloadBundle(bundle)}>
        Download certificate (JSON)
      </button>
    </div>
  );
}

export default function OutageCopilot({ rail }) {
  const [showForm, setShowForm] = useState(false);
  const [claimedTime, setClaimedTime] = useState(() => toLocalInputValue(new Date()));
  const [txnRef, setTxnRef] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [bundle, setBundle] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.requestCertificate({
        rail_slug: rail.slug,
        claimed_timestamp: new Date(claimedTime).toISOString(),
        claimed_transaction_ref: txnRef.trim() || null,
      });
      setBundle(result);
      setShowForm(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      onClick={(e) => e.stopPropagation()}
      style={{ margin: "4px 0 8px 26px", border: "1px solid var(--rust)", background: "var(--rust-dim)", padding: "16px 18px" }}
    >
      <div style={{ fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--rust)", fontWeight: 700, marginBottom: 8 }}>
        Outage copilot — {rail.name} is degraded right now
      </div>

      <div style={{ fontSize: 12.5, lineHeight: 1.6, color: "var(--ink)", display: "grid", gap: 8 }}>
        <div>
          <strong>Don't retry the payment immediately.</strong> During an infrastructure incident,
          retries pile onto an already struggling system and can leave you with multiple pending
          debits for the same purchase. Wait a few minutes and check this page before trying again.
        </div>
        <div>
          <strong>Check whether the money actually left your account</strong> — open your bank's own
          app or look for the debit SMS, not the UPI app's spinner. A "failed" screen with a real
          debit usually auto-reverses; note the time and transaction reference if it doesn't.
        </div>
        <div style={{ borderLeft: "3px solid var(--rust)", paddingLeft: 10 }}>
          <strong>Scam warning:</strong> outage windows are prime time for fake "UPI helpline" calls.
          NPCI and your bank will not call you to "reverse a stuck transaction" or ask for your UPI
          PIN, OTP, or a screen-share. If someone calls you about this outage unprompted, hang up.
        </div>
      </div>

      {!bundle && !showForm && (
        <button className="btn-danger" style={{ marginTop: 14 }} onClick={() => setShowForm(true)}>
          Request Evidence Certificate
        </button>
      )}

      {showForm && !bundle && (
        <form onSubmit={submit} style={{ marginTop: 14, display: "grid", gap: 10, maxWidth: 440 }}>
          <div style={{ fontSize: 12, color: "var(--ink-soft)" }}>
            A signed, verifiable record that this incident was confirmed by independent witness
            quorum — usable as supporting evidence in a bank dispute or RBI ombudsman complaint.
          </div>
          <label style={{ fontSize: 12 }}>
            Rail
            <input value={`${rail.name} (${rail.slug})`} disabled style={{ display: "block", width: "100%", marginTop: 4 }} />
          </label>
          <label style={{ fontSize: 12 }}>
            Approximate time your transaction failed
            <input
              type="datetime-local"
              required
              value={claimedTime}
              onChange={(e) => setClaimedTime(e.target.value)}
              style={{ display: "block", width: "100%", marginTop: 4 }}
            />
          </label>
          <label style={{ fontSize: 12 }}>
            Transaction reference (optional — recorded as self-reported, not verified)
            <input
              value={txnRef}
              onChange={(e) => setTxnRef(e.target.value)}
              placeholder="e.g. UPI ref no. from your app"
              style={{ display: "block", width: "100%", marginTop: 4 }}
            />
          </label>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="submit" className="btn-danger" disabled={busy}>
              {busy ? "Requesting…" : "Issue certificate"}
            </button>
            <button type="button" className="btn-ghost" onClick={() => setShowForm(false)}>Cancel</button>
          </div>
        </form>
      )}

      {error && (
        <div style={{ marginTop: 10, fontSize: 12, color: "var(--rust)", border: "1px solid var(--rust)", padding: "8px 10px" }}>
          {error}
        </div>
      )}

      {bundle && <CertificateResult bundle={bundle} />}
    </div>
  );
}
